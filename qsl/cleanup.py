#!/usr/bin/env python3
"""
qBit Smart Limit - 删种模块
自动清理达到条件的种子
"""

import os
import json
import time
import threading
from typing import Optional, List, Dict
from datetime import datetime

from .utils import C, wall_time, get_logger, fmt_size, fmt_duration


class CleanupModule:
    """
    删种模块 - 自动清理种子
    
    功能:
    1. 根据分享率、做种时间等条件自动删除种子
    2. 支持任务文件手动指定删除
    3. 可选是否同时删除文件
    4. TG通知删除的种子
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
    
    def _worker(self):
        """后台工作线程"""
        logger = get_logger()
        interval = self.config.cleanup_interval
        
        while not self._stop.is_set():
            try:
                # 1. 处理任务文件（手动删除指令）
                self._process_task_file()
                
                # 2. 自动检查和删除
                self._auto_cleanup()
                
            except Exception as e:
                logger.error(f"删种模块异常: {e}")
            
            # 等待下一次循环
            self._stop.wait(interval)
    
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
                    
                    # 只处理做种中的种子
                    state = getattr(t, 'state', '')
                    if 'seeding' not in state.lower() and 'stalledUP' not in state:
                        continue
                    
                    # 检查tracker关键词
                    if tracker_keyword:
                        tracker = getattr(t, 'tracker', '') or ''
                        if tracker_keyword.lower() not in tracker.lower():
                            continue
                    
                    # 获取种子信息
                    ratio = getattr(t, 'ratio', 0) or 0
                    seeding_time = getattr(t, 'seeding_time', 0) or 0
                    name = getattr(t, 'name', 'Unknown')
                    
                    # 检查是否满足删除条件
                    if ratio >= min_ratio and seeding_time >= min_seeding_time:
                        reason = f"分享率{ratio:.2f} >= {min_ratio}, 做种时间{fmt_duration(seeding_time)} >= {fmt_duration(min_seeding_time)}"
                        
                        if self._delete_torrent(t.hash, name, delete_files, reason, ratio, seeding_time):
                            deleted_count += 1
                
                except Exception as e:
                    logger.debug(f"检查种子失败: {e}")
            
            if deleted_count > 0:
                logger.info(f"🗑️ 自动删除了 {deleted_count} 个种子")
        
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
            
            return self._delete_torrent(torrent_hash, name, delete_files, reason, ratio, seeding_time)
        
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
                    
                    if self._delete_torrent(t.hash, name, delete_files, reason, ratio, seeding_time):
                        deleted_count += 1
        
        except Exception as e:
            logger.error(f"按名称删除失败: {e}")
        
        return deleted_count
    
    def _delete_torrent(self, torrent_hash: str, name: str, delete_files: bool, 
                        reason: str, ratio: float, seeding_time: float) -> bool:
        """删除种子"""
        logger = get_logger()
        
        try:
            self.client.torrents_delete(delete_files=delete_files, torrent_hashes=torrent_hash)
            
            # 记录到数据库
            self.db.add_cleanup_history(torrent_hash, name, reason, ratio, seeding_time)
            
            # TG通知
            if self.notifier:
                self.notifier.cleanup_notify(name, reason, ratio, seeding_time)
            
            logger.info(f"🗑️ 删除种子: {name[:40]} ({reason})")
            return True
        
        except Exception as e:
            logger.error(f"删除种子失败 {name[:30]}: {e}")
            return False
    
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
        return {
            'running': self.running,
            'interval': self.config.cleanup_interval,
            'min_ratio': self.config.cleanup_min_ratio,
            'min_seeding_time': self.config.cleanup_min_seeding_time,
            'delete_files': self.config.cleanup_delete_files,
            'protected_count': len(self._protected_hashes),
            'history_count': len(self.db.get_cleanup_history(1000))
        }
