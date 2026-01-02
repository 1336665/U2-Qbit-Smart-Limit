#!/usr/bin/env python3
"""
qBit Smart Limit - 删种模块
自动清理达到条件的种子

功能:
1. 根据分享率、做种时间等条件自动删除种子
2. 基于剩余空间的智能删种规则
3. 删除前强制汇报并等待
4. 支持任务文件手动指定删除
5. 可选是否同时删除文件
6. TG通知删除的种子详细信息
"""

import os
import json
import time
import shutil
import threading
from typing import Optional, List, Dict, Tuple
from datetime import datetime

from .utils import C, wall_time, get_logger, fmt_size, fmt_speed, fmt_duration


class CleanupModule:
    """
    删种模块 - 自动清理种子
    
    功能:
    1. 根据分享率、做种时间等条件自动删除种子
    2. 基于剩余空间的智能删种规则
    3. 删除前强制汇报
    4. 支持任务文件手动指定删除
    5. 可选是否同时删除文件
    6. TG通知删除的种子
    """
    
    def __init__(self, qb_client, config: 'Config', db: 'Database', notifier: 'TelegramBot' = None):
        self.client = qb_client
        self.config = config
        self.db = db
        self.notifier = notifier
        self.running = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # 任务文件路径
        self.task_file = os.path.join(os.path.dirname(config._mtime and "" or C.CLEANUP_TASK_FILE), C.CLEANUP_TASK_FILE)
        
        # 保护列表 - 不会被自动删除的种子hash
        self._protected_hashes = set()
        
        # 待删除队列（等待汇报后删除）
        self._pending_delete: Dict[str, dict] = {}
    
    def start(self):
        """启动删种模块"""
        if self.running:
            return
        
        self.running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="Cleanup")
        self._thread.start()
        get_logger().info("🗑️ 删种模块已启动")
    
    def stop(self):
        """停止删种模块"""
        self.running = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        get_logger().info("🗑️ 删种模块已停止")
    
    def _get_free_space_gb(self) -> float:
        """获取默认保存路径的剩余空间（GB）"""
        try:
            # 尝试从qBittorrent获取默认保存路径
            prefs = self.client.app_preferences()
            save_path = prefs.get('save_path', '/downloads')
            
            if os.path.exists(save_path):
                stat = shutil.disk_usage(save_path)
                return stat.free / (1024 ** 3)  # 转换为GB
            else:
                # 如果路径不存在，尝试根目录
                stat = shutil.disk_usage('/')
                return stat.free / (1024 ** 3)
        except Exception as e:
            get_logger().debug(f"获取剩余空间失败: {e}")
            return float('inf')  # 返回无限大，避免误删
    
    def _check_space_rules(self, torrent) -> Tuple[bool, str]:
        """
        检查基于剩余空间的删种规则
        
        规则:
        1. 剩余空间 < 10G，上传 < 1MiB/s
        2. 剩余空间 < 20G，下载完成，上传 < 512KiB/s  
        3. 剩余空间 < 5G，上传 < 5MiB/s
        
        返回: (是否应删除, 原因)
        """
        if not self.config.cleanup_space_rules_enabled:
            return False, ""
        
        free_space_gb = self._get_free_space_gb()
        up_speed = getattr(torrent, 'upspeed', 0) or 0
        up_speed_kib = up_speed / 1024
        
        progress = getattr(torrent, 'progress', 0) or 0
        is_completed = progress >= 1.0
        
        state = getattr(torrent, 'state', '')
        is_seeding = 'seeding' in state.lower() or 'stalledUP' in state
        
        # 规则3优先级最高（空间最紧张）: 剩余空间 < 5G，上传 < 5MiB/s
        rule3_gb = self.config.cleanup_space_rule3_gb
        rule3_upload = self.config.cleanup_space_rule3_upload_kib
        if free_space_gb < rule3_gb and up_speed_kib < rule3_upload:
            return True, f"剩余空间{free_space_gb:.1f}G<{rule3_gb}G, 上传{fmt_speed(up_speed)}<{fmt_speed(rule3_upload*1024)}"
        
        # 规则1: 剩余空间 < 10G，上传 < 1MiB/s
        rule1_gb = self.config.cleanup_space_rule1_gb
        rule1_upload = self.config.cleanup_space_rule1_upload_kib
        if free_space_gb < rule1_gb and up_speed_kib < rule1_upload:
            return True, f"剩余空间{free_space_gb:.1f}G<{rule1_gb}G, 上传{fmt_speed(up_speed)}<{fmt_speed(rule1_upload*1024)}"
        
        # 规则2: 剩余空间 < 20G，下载完成，上传 < 512KiB/s
        rule2_gb = self.config.cleanup_space_rule2_gb
        rule2_upload = self.config.cleanup_space_rule2_upload_kib
        if free_space_gb < rule2_gb and is_completed and up_speed_kib < rule2_upload:
            return True, f"剩余空间{free_space_gb:.1f}G<{rule2_gb}G, 已完成, 上传{fmt_speed(up_speed)}<{fmt_speed(rule2_upload*1024)}"
        
        return False, ""
    
    def _do_reannounce(self, torrent_hash: str, name: str) -> bool:
        """执行强制汇报"""
        try:
            self.client.torrents_reannounce(torrent_hashes=torrent_hash)
            get_logger().info(f"[{name[:20]}] 🔄 删前强制汇报")
            return True
        except Exception as e:
            get_logger().debug(f"强制汇报失败: {e}")
            return False
    
    def _worker(self):
        """后台工作线程"""
        logger = get_logger()
        interval = self.config.cleanup_interval
        
        while not self._stop.is_set():
            try:
                # 1. 处理待删除队列（等待汇报后删除）
                self._process_pending_delete()
                
                # 2. 处理任务文件（手动删除指令）
                self._process_task_file()
                
                # 3. 自动检查和删除
                self._auto_cleanup()
                
            except Exception as e:
                logger.error(f"删种模块异常: {e}")
            
            # 等待下一次循环
            self._stop.wait(interval)
    
    def _process_pending_delete(self):
        """处理待删除队列"""
        logger = get_logger()
        now = wall_time()
        
        with self._lock:
            to_delete = []
            for h, info in list(self._pending_delete.items()):
                if now >= info['delete_time']:
                    to_delete.append((h, info))
                    del self._pending_delete[h]
            
        for h, info in to_delete:
            try:
                self._execute_delete(
                    h, info['name'], info['delete_files'], 
                    info['reason'], info['ratio'], info['seeding_time'],
                    info.get('size', 0), info.get('uploaded', 0), info.get('downloaded', 0)
                )
            except Exception as e:
                logger.error(f"执行删除失败: {e}")
    
    def _schedule_delete(self, torrent_hash: str, name: str, delete_files: bool, 
                         reason: str, ratio: float, seeding_time: float,
                         size: int = 0, uploaded: int = 0, downloaded: int = 0):
        """安排删除（先汇报，等待后删除）"""
        logger = get_logger()
        
        # 执行强制汇报
        if self.config.cleanup_reannounce_before_delete:
            self._do_reannounce(torrent_hash, name)
            wait_time = self.config.cleanup_reannounce_wait
            delete_time = wall_time() + wait_time
            logger.info(f"[{name[:20]}] ⏳ 等待{wait_time}秒后删除")
            
            with self._lock:
                self._pending_delete[torrent_hash] = {
                    'name': name,
                    'delete_files': delete_files,
                    'reason': reason,
                    'ratio': ratio,
                    'seeding_time': seeding_time,
                    'size': size,
                    'uploaded': uploaded,
                    'downloaded': downloaded,
                    'delete_time': delete_time
                }
        else:
            # 不需要汇报，直接删除
            self._execute_delete(torrent_hash, name, delete_files, reason, ratio, seeding_time,
                                size, uploaded, downloaded)
    
    def _execute_delete(self, torrent_hash: str, name: str, delete_files: bool,
                        reason: str, ratio: float, seeding_time: float,
                        size: int = 0, uploaded: int = 0, downloaded: int = 0):
        """执行实际删除操作"""
        logger = get_logger()
        
        try:
            self.client.torrents_delete(delete_files=delete_files, torrent_hashes=torrent_hash)
            
            # 记录到数据库
            self.db.add_cleanup_history(torrent_hash, name, reason, ratio, seeding_time)
            
            # TG通知 - 详细信息
            if self.notifier:
                self.notifier.cleanup_notify_detailed(
                    name=name, 
                    reason=reason, 
                    ratio=ratio, 
                    seeding_time=seeding_time,
                    size=size,
                    uploaded=uploaded,
                    downloaded=downloaded,
                    delete_files=delete_files
                )
            
            logger.info(f"🗑️ 删除种子: {name[:40]} ({reason})")
            return True
        
        except Exception as e:
            logger.error(f"删除种子失败 {name[:30]}: {e}")
            return False
    
    def _process_task_file(self):
        """处理任务文件"""
        logger = get_logger()
        
        if not os.path.exists(self.task_file):
            return
        
        try:
            with open(self.task_file, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
            
            if not isinstance(tasks, list) or not tasks:
                return
            
            remaining_tasks = []
            deleted_count = 0
            
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                
                action = task.get('action', 'delete').lower()
                torrent_hash = task.get('hash', '').strip()
                name_pattern = task.get('name', '').strip()
                delete_files = task.get('delete_files', self.config.cleanup_delete_files)
                reason = task.get('reason', '手动任务')
                
                if action == 'protect':
                    # 保护种子不被自动删除
                    if torrent_hash:
                        self._protected_hashes.add(torrent_hash)
                        logger.info(f"🛡️ 保护种子: {torrent_hash[:16]}")
                    continue
                
                if action == 'unprotect':
                    # 取消保护
                    if torrent_hash:
                        self._protected_hashes.discard(torrent_hash)
                        logger.info(f"🔓 取消保护: {torrent_hash[:16]}")
                    continue
                
                if action == 'delete':
                    if torrent_hash:
                        # 按hash删除
                        if self._delete_torrent_by_hash(torrent_hash, delete_files, reason):
                            deleted_count += 1
                        else:
                            remaining_tasks.append(task)
                    elif name_pattern:
                        # 按名称模式删除
                        count = self._delete_torrent_by_name(name_pattern, delete_files, reason)
                        deleted_count += count
            
            # 更新任务文件
            if remaining_tasks:
                with open(self.task_file, 'w', encoding='utf-8') as f:
                    json.dump(remaining_tasks, f, ensure_ascii=False, indent=2)
            else:
                os.remove(self.task_file)
            
            if deleted_count > 0:
                logger.info(f"🗑️ 任务文件删除了 {deleted_count} 个种子")
        
        except json.JSONDecodeError:
            logger.warning(f"任务文件格式错误")
        except Exception as e:
            logger.error(f"任务文件处理失败: {e}")
    
    def _auto_cleanup(self):
        """自动清理符合条件的种子"""
        logger = get_logger()
        
        if not self.config.cleanup_enabled:
            return
        
        min_ratio = self.config.cleanup_min_ratio
        min_seeding_time = self.config.cleanup_min_seeding_time
        delete_files = self.config.cleanup_delete_files
        tracker_keyword = self.config.cleanup_tracker_keyword
        
        try:
            torrents = self.client.torrents_info()
            deleted_count = 0
            
            for t in torrents:
                try:
                    # 检查保护列表
                    if t.hash in self._protected_hashes:
                        continue
                    
                    # 检查是否已在待删除队列
                    with self._lock:
                        if t.hash in self._pending_delete:
                            continue
                    
                    # 获取种子信息
                    name = getattr(t, 'name', 'Unknown')
                    ratio = getattr(t, 'ratio', 0) or 0
                    seeding_time = getattr(t, 'seeding_time', 0) or 0
                    state = getattr(t, 'state', '')
                    size = getattr(t, 'total_size', 0) or 0
                    uploaded = getattr(t, 'uploaded', 0) or 0
                    downloaded = getattr(t, 'completed', 0) or getattr(t, 'downloaded', 0) or 0
                    
                    # 检查tracker关键词
                    if tracker_keyword:
                        tracker = getattr(t, 'tracker', '') or ''
                        if tracker_keyword.lower() not in tracker.lower():
                            continue
                    
                    # 检查基于剩余空间的规则（优先级最高）
                    should_delete_space, space_reason = self._check_space_rules(t)
                    if should_delete_space:
                        self._schedule_delete(t.hash, name, delete_files, space_reason, 
                                             ratio, seeding_time, size, uploaded, downloaded)
                        deleted_count += 1
                        continue
                    
                    # 只对做种中的种子检查分享率规则
                    if 'seeding' not in state.lower() and 'stalledUP' not in state:
                        continue
                    
                    # 检查是否满足删除条件（分享率 + 做种时间）
                    if ratio >= min_ratio and seeding_time >= min_seeding_time:
                        reason = f"R={ratio:.2f}≥{min_ratio}, T={fmt_duration(seeding_time)}≥{fmt_duration(min_seeding_time)}"
                        self._schedule_delete(t.hash, name, delete_files, reason, 
                                             ratio, seeding_time, size, uploaded, downloaded)
                        deleted_count += 1
                
                except Exception as e:
                    logger.debug(f"检查种子失败: {e}")
            
            if deleted_count > 0:
                logger.info(f"🗑️ 自动安排删除 {deleted_count} 个种子")
        
        except Exception as e:
            logger.error(f"自动清理失败: {e}")
    
    def _delete_torrent_by_hash(self, torrent_hash: str, delete_files: bool, reason: str) -> bool:
        """按hash删除种子"""
        logger = get_logger()
        
        try:
            # 获取种子信息
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
            if not torrents:
                logger.warning(f"找不到种子: {torrent_hash[:16]}")
                return False
            
            t = torrents[0]
            name = getattr(t, 'name', 'Unknown')
            ratio = getattr(t, 'ratio', 0) or 0
            seeding_time = getattr(t, 'seeding_time', 0) or 0
            size = getattr(t, 'total_size', 0) or 0
            uploaded = getattr(t, 'uploaded', 0) or 0
            downloaded = getattr(t, 'completed', 0) or getattr(t, 'downloaded', 0) or 0
            
            self._schedule_delete(torrent_hash, name, delete_files, reason, 
                                 ratio, seeding_time, size, uploaded, downloaded)
            return True
        
        except Exception as e:
            logger.error(f"按hash删除失败 {torrent_hash[:16]}: {e}")
            return False
    
    def _delete_torrent_by_name(self, name_pattern: str, delete_files: bool, reason: str) -> int:
        """按名称模式删除种子"""
        logger = get_logger()
        deleted_count = 0
        
        try:
            torrents = self.client.torrents_info()
            for t in torrents:
                name = getattr(t, 'name', '')
                if name_pattern.lower() in name.lower():
                    ratio = getattr(t, 'ratio', 0) or 0
                    seeding_time = getattr(t, 'seeding_time', 0) or 0
                    size = getattr(t, 'total_size', 0) or 0
                    uploaded = getattr(t, 'uploaded', 0) or 0
                    downloaded = getattr(t, 'completed', 0) or getattr(t, 'downloaded', 0) or 0
                    
                    self._schedule_delete(t.hash, name, delete_files, reason, 
                                         ratio, seeding_time, size, uploaded, downloaded)
                    deleted_count += 1
        
        except Exception as e:
            logger.error(f"按名称删除失败: {e}")
        
        return deleted_count
    
    def _delete_torrent(self, torrent_hash: str, name: str, delete_files: bool, 
                        reason: str, ratio: float, seeding_time: float) -> bool:
        """删除种子（兼容旧接口）"""
        return self._delete_torrent_by_hash(torrent_hash, delete_files, reason)
    
    def delete_torrent_manual(self, torrent_hash: str, delete_files: bool = None, 
                              reason: str = "手动删除") -> bool:
        """手动删除种子（供外部调用）"""
        if delete_files is None:
            delete_files = self.config.cleanup_delete_files
        return self._delete_torrent_by_hash(torrent_hash, delete_files, reason)
    
    def protect_torrent(self, torrent_hash: str):
        """保护种子不被自动删除"""
        with self._lock:
            self._protected_hashes.add(torrent_hash)
    
    def unprotect_torrent(self, torrent_hash: str):
        """取消保护"""
        with self._lock:
            self._protected_hashes.discard(torrent_hash)
    
    def get_status(self) -> dict:
        """获取模块状态"""
        free_space = self._get_free_space_gb()
        return {
            'running': self.running,
            'interval': self.config.cleanup_interval,
            'min_ratio': self.config.cleanup_min_ratio,
            'min_seeding_time': self.config.cleanup_min_seeding_time,
            'delete_files': self.config.cleanup_delete_files,
            'protected_count': len(self._protected_hashes),
            'pending_count': len(self._pending_delete),
            'history_count': len(self.db.get_cleanup_history(1000)),
            'free_space_gb': free_space,
            'space_rules_enabled': self.config.cleanup_space_rules_enabled,
            'reannounce_before_delete': self.config.cleanup_reannounce_before_delete
        }
