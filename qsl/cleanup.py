#!/usr/bin/env python3
"""qBit Smart Limit - 删种模块

自动清理达到条件的种子。

当前版本默认只按“剩余空间 + 速度阈值”触发删除：
- 上传规则：对做种/上传状态生效
- 下载规则：对下载中状态生效

安全策略：
- 等待/排队中的任务（queued/paused）不会被自动删除
- 每次删除 1 个任务后等待 30 秒重新检测空间，不够则继续循环删除
"""

import os
import json
import time
import shutil
import threading
from typing import Optional, Dict, Tuple, Any

from .utils import C, wall_time, get_logger, fmt_size, fmt_speed, fmt_duration


class CleanupModule:
    """
    删种模块 - 自动清理种子
    
    功能:
    1. 基于剩余空间的智能删种规则（默认启用）
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

        # 自动删种：每删 1 个后等待一会儿，再重新检测空间
        self._recheck_wait_seconds = 30

        # qBittorrent 状态集合（统一为小写）
        self._upload_states = {'seeding', 'stalledup', 'uploading', 'forcedup'}
        self._download_states = {'downloading', 'stalleddl', 'forceddl', 'metadl'}
    
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
        """获取 qBittorrent 默认保存路径所在磁盘的剩余空间（GB）。

        重要：如果脚本运行在另一台机器上（qB 在远端），不能用本机磁盘空间判断，
        否则会出现“空间误判 → 乱删种”。

        因此这里**优先**通过 qBittorrent WebAPI 读取 `free_space_on_disk`（由 qB 服务器端计算），
        只有在无法读取时才回退到本机 `disk_usage`（仅适用于脚本与 qB 同机/同挂载点）。
        """
        # 1) 优先使用 qBittorrent 服务器端上报的剩余空间（远端部署也准确）
        try:
            md = None
            fn = getattr(self.client, 'sync_maindata', None)
            if callable(fn):
                # qbittorrentapi 通常提供该方法
                md = fn(rid=0)
            else:
                sync = getattr(self.client, 'sync', None)
                if sync is not None:
                    fn2 = getattr(sync, 'maindata', None)
                    if callable(fn2):
                        md = fn2(rid=0)

            free_bytes = self._extract_free_space_on_disk(md) if md is not None else None
            if isinstance(free_bytes, (int, float)) and free_bytes >= 0:
                return float(free_bytes) / (1024 ** 3)
        except Exception as e:
            get_logger().debug(f"从qBittorrent获取剩余空间失败，将回退本机检测: {e}")

        # 2) 回退：本机磁盘检测（仅当脚本与 qB 同机/同挂载点时才准确）
        try:
            prefs = self.client.app_preferences()
            save_path = prefs.get('save_path', '/downloads')
            check_path = save_path if os.path.exists(save_path) else '/'
            stat = shutil.disk_usage(check_path)
            return stat.free / (1024 ** 3)
        except Exception as e:
            get_logger().debug(f"获取剩余空间失败: {e}")
            return float('inf')  # 返回无限大，避免误删

    @staticmethod
    def _extract_free_space_on_disk(maindata: Any) -> Optional[int]:
        """从 qBittorrent 的 maindata 结构中提取 free_space_on_disk（bytes）。

        兼容：
        - dict
        - qbittorrentapi 的对象/命名空间
        """
        if maindata is None:
            return None

        # dict 结构
        if isinstance(maindata, dict):
            ss = maindata.get('server_state') or maindata.get('serverState')
            if isinstance(ss, dict):
                v = ss.get('free_space_on_disk')
                if v is None:
                    v = ss.get('freeSpaceOnDisk')
                
                try:
                    return int(v)
                except Exception:
                    return None

            return None

        # qbittorrentapi 对象
        ss = getattr(maindata, 'server_state', None) or getattr(maindata, 'serverState', None)
        if ss is None:
            return None
        if isinstance(ss, dict):
            v = ss.get('free_space_on_disk') or ss.get('freeSpaceOnDisk')
            return int(v) if isinstance(v, (int, float)) else None
        v = getattr(ss, 'free_space_on_disk', None)
        if v is None:
            v = getattr(ss, 'freeSpaceOnDisk', None)
        return int(v) if isinstance(v, (int, float)) else None

    @staticmethod
    def _is_waiting_state(state: str) -> bool:
        """等待/排队/暂停中的任务不自动删除。"""
        s = (state or "").lower()
        return ('queued' in s) or ('paused' in s)

    def _space_target_gb(self) -> float:
        """空间恢复目标：高于所有规则的空间阈值即可避免继续触发。"""
        return float(max(
            self.config.cleanup_space_rule1_gb,
            self.config.cleanup_space_rule2_gb,
            self.config.cleanup_space_rule3_gb,
        ))

    def _check_upload_space_rules(self, torrent: Any, free_space_gb: float) -> Tuple[bool, str, int, float]:
        """检查上传(做种)规则。返回 (命中, 原因, 优先级, 当前速度KiB/s)。"""
        state = (getattr(torrent, 'state', '') or '').lower()
        if self._is_waiting_state(state):
            return False, "", 99, 0.0

        # 仅对做种/上传状态生效
        is_seeding = state in self._upload_states
        if not is_seeding:
            return False, "", 99, 0.0

        up_speed = getattr(torrent, 'upspeed', 0) or 0
        up_kib = up_speed / 1024

        progress = getattr(torrent, 'progress', 0) or 0
        is_completed = progress >= 1.0

        # 规则3 (紧急) > 规则1 > 规则2
        r3_gb = self.config.cleanup_space_rule3_gb
        r3_up = self.config.cleanup_space_rule3_upload_kib
        if free_space_gb < r3_gb and up_kib < r3_up:
            return True, f"[上传-紧急] 剩余{free_space_gb:.1f}G<{r3_gb}G, 上传{up_kib:.0f}KiB/s<{r3_up}", 0, up_kib

        r1_gb = self.config.cleanup_space_rule1_gb
        r1_up = self.config.cleanup_space_rule1_upload_kib
        if free_space_gb < r1_gb and up_kib < r1_up:
            return True, f"[上传] 剩余{free_space_gb:.1f}G<{r1_gb}G, 上传{up_kib:.0f}KiB/s<{r1_up}", 1, up_kib

        r2_gb = self.config.cleanup_space_rule2_gb
        r2_up = self.config.cleanup_space_rule2_upload_kib
        if free_space_gb < r2_gb and is_completed and up_kib < r2_up:
            return True, f"[上传] 剩余{free_space_gb:.1f}G<{r2_gb}G, 已完成, 上传{up_kib:.0f}KiB/s<{r2_up}", 2, up_kib

        return False, "", 99, up_kib

    def _check_download_space_rules(self, torrent: Any, free_space_gb: float) -> Tuple[bool, str, int, float]:
        """检查下载规则。返回 (命中, 原因, 优先级, 当前速度KiB/s)。"""
        state = (getattr(torrent, 'state', '') or '').lower()
        if self._is_waiting_state(state):
            return False, "", 99, 0.0

        # 仅对下载中状态生效
        is_downloading = state in self._download_states
        if not is_downloading:
            return False, "", 99, 0.0

        progress = getattr(torrent, 'progress', 0) or 0
        if progress >= 1.0:
            return False, "", 99, 0.0

        dl_speed = getattr(torrent, 'dlspeed', 0) or 0
        dl_kib = dl_speed / 1024

        r3_gb = self.config.cleanup_space_rule3_gb
        r3_dl = self.config.cleanup_space_rule3_download_kib
        if free_space_gb < r3_gb and dl_kib < r3_dl:
            return True, f"[下载-紧急] 剩余{free_space_gb:.1f}G<{r3_gb}G, 下载{dl_kib:.0f}KiB/s<{r3_dl}", 0, dl_kib

        r1_gb = self.config.cleanup_space_rule1_gb
        r1_dl = self.config.cleanup_space_rule1_download_kib
        if free_space_gb < r1_gb and dl_kib < r1_dl:
            return True, f"[下载] 剩余{free_space_gb:.1f}G<{r1_gb}G, 下载{dl_kib:.0f}KiB/s<{r1_dl}", 1, dl_kib

        r2_gb = self.config.cleanup_space_rule2_gb
        r2_dl = self.config.cleanup_space_rule2_download_kib
        if free_space_gb < r2_gb and dl_kib < r2_dl:
            return True, f"[下载] 剩余{free_space_gb:.1f}G<{r2_gb}G, 下载{dl_kib:.0f}KiB/s<{r2_dl}", 2, dl_kib

        return False, "", 99, dl_kib
    
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
        """自动清理符合条件的种子（空间规则：上传 + 下载）。

        - queued/paused 的等待任务不会删除
        - 每次只删除 1 个任务；删除后等待 30 秒再重新检测空间
        """
        logger = get_logger()

        if not self.config.cleanup_enabled:
            logger.debug("删种功能未启用")
            return

        delete_files = self.config.cleanup_delete_files
        tracker_keyword = self.config.cleanup_tracker_keyword
        target_gb = self._space_target_gb()

        try:
            loop_guard = 0
            while not self._stop.is_set():
                free_gb = self._get_free_space_gb()
                if free_gb >= target_gb:
                    break

                torrents = self.client.torrents_info()
                best = None  # (priority, speed_kib, -release_bytes)

                for t in torrents:
                    try:
                        # 保护/待删队列跳过
                        if t.hash in self._protected_hashes:
                            continue
                        with self._lock:
                            if t.hash in self._pending_delete:
                                continue

                        state = getattr(t, 'state', '') or ''
                        if self._is_waiting_state(state):
                            continue

                        # tracker 关键词过滤（可选）
                        if tracker_keyword:
                            tracker = getattr(t, 'tracker', '') or ''
                            if tracker_keyword.lower() not in tracker.lower():
                                continue

                        # 命中规则（上传 / 下载）
                        hit_u, reason_u, pri_u, speed_u = self._check_upload_space_rules(t, free_gb)
                        hit_d, reason_d, pri_d, speed_d = self._check_download_space_rules(t, free_gb)

                        if not hit_u and not hit_d:
                            continue

                        # 选择更高优先级（数字越小越紧急）
                        if hit_u and (not hit_d or pri_u <= pri_d):
                            reason, pri, speed_kib = reason_u, pri_u, speed_u
                        else:
                            reason, pri, speed_kib = reason_d, pri_d, speed_d

                        name = getattr(t, 'name', 'Unknown')
                        ratio = getattr(t, 'ratio', 0) or 0
                        seeding_time = getattr(t, 'seeding_time', 0) or 0
                        size = getattr(t, 'total_size', 0) or 0
                        uploaded = getattr(t, 'uploaded', 0) or 0
                        downloaded = getattr(t, 'completed', 0) or getattr(t, 'downloaded', 0) or 0

                        release_bytes = (
                            getattr(t, 'size_on_disk', 0) or
                            getattr(t, 'total_size', 0) or
                            getattr(t, 'downloaded', 0) or 0
                        )

                        key = (pri, speed_kib, -release_bytes)
                        if best is None or key < best['key']:
                            best = {
                                'key': key,
                                'hash': t.hash,
                                'name': name,
                                'reason': reason,
                                'ratio': ratio,
                                'seeding_time': seeding_time,
                                'size': size,
                                'uploaded': uploaded,
                                'downloaded': downloaded,
                            }

                    except Exception as e:
                        logger.debug(f"检查种子失败: {e}")

                if not best:
                    logger.warning(f"🗑️ 空间不足: {free_gb:.1f}G<{target_gb}G，但没有符合规则的可删任务（queued/paused 会被跳过）")
                    break

                logger.info(
                    f"🗑️ 空间不足: {free_gb:.1f}G<{target_gb}G，删除1个 → {best['name'][:40]} ({best['reason']})"
                )

                # 自动删种：同步执行（便于删除后等待 30 秒再次检测）
                if self.config.cleanup_reannounce_before_delete:
                    self._do_reannounce(best['hash'], best['name'])
                    if self._stop.wait(self.config.cleanup_reannounce_wait):
                        break

                ok = self._execute_delete(
                    best['hash'], best['name'], delete_files,
                    best['reason'], best['ratio'], best['seeding_time'],
                    best.get('size', 0), best.get('uploaded', 0), best.get('downloaded', 0)
                )
                if not ok:
                    break

                logger.info(f"⏳ 等待{self._recheck_wait_seconds}秒后重新检测空间...")
                if self._stop.wait(self._recheck_wait_seconds):
                    break

                loop_guard += 1
                if loop_guard >= 50:
                    logger.warning("🗑️ 连续删除次数过多，停止本轮自动清理（防止异常循环）")
                    break

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
    
    def run_once(self) -> dict:
        """手动执行一次删种检查（用于测试）"""
        logger = get_logger()
        result = {
            'success': False,
            'checked': 0,
            'matched': 0,
            'pending': 0,
            'errors': []
        }

        if not self.config.cleanup_enabled:
            result['errors'].append("删种功能未启用")
            return result

        delete_files = self.config.cleanup_delete_files
        tracker_keyword = self.config.cleanup_tracker_keyword

        try:
            free_gb = self._get_free_space_gb()
            torrents = self.client.torrents_info()
            result['checked'] = len(torrents)
            matched_count = 0

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
                    size = getattr(t, 'total_size', 0) or 0
                    uploaded = getattr(t, 'uploaded', 0) or 0
                    downloaded = getattr(t, 'completed', 0) or getattr(t, 'downloaded', 0) or 0

                    # tracker 关键词过滤（可选）
                    if tracker_keyword:
                        tracker = getattr(t, 'tracker', '') or ''
                        if tracker_keyword.lower() not in tracker.lower():
                            continue

                    state = getattr(t, 'state', '') or ''
                    if self._is_waiting_state(state):
                        continue

                    hit_u, reason_u, pri_u, _ = self._check_upload_space_rules(t, free_gb)
                    hit_d, reason_d, pri_d, _ = self._check_download_space_rules(t, free_gb)

                    if hit_u or hit_d:
                        if hit_u and (not hit_d or pri_u <= pri_d):
                            space_reason = reason_u
                        else:
                            space_reason = reason_d
                        self._schedule_delete(
                            t.hash, name, delete_files, space_reason,
                            ratio, seeding_time, size, uploaded, downloaded
                        )
                        matched_count += 1

                except Exception as e:
                    result['errors'].append(f"检查种子失败: {e}")

            result['matched'] = matched_count
            result['pending'] = len(self._pending_delete)
            result['success'] = True
            logger.info(f"🗑️ 手动删种测试完成: 检查{result['checked']}个, 匹配{matched_count}个")

        except Exception as e:
            result['errors'].append(str(e))
            logger.error(f"手动删种测试失败: {e}")

        return result


    def get_status(self) -> dict:
        """获取模块状态"""
        free_space = self._get_free_space_gb()
        return {
            'running': self.running,
            'interval': self.config.cleanup_interval,
            'delete_files': self.config.cleanup_delete_files,
            'tracker_keyword': self.config.cleanup_tracker_keyword,
            'protected_count': len(self._protected_hashes),
            'pending_count': len(self._pending_delete),
            'history_count': len(self.db.get_cleanup_history(1000)),
            'free_space_gb': free_space,
            'space_rules': {
                'rule1_gb': self.config.cleanup_space_rule1_gb,
                'rule1_upload_kib': self.config.cleanup_space_rule1_upload_kib,
                'rule1_download_kib': self.config.cleanup_space_rule1_download_kib,
                'rule2_gb': self.config.cleanup_space_rule2_gb,
                'rule2_upload_kib': self.config.cleanup_space_rule2_upload_kib,
                'rule2_download_kib': self.config.cleanup_space_rule2_download_kib,
                'rule3_gb': self.config.cleanup_space_rule3_gb,
                'rule3_upload_kib': self.config.cleanup_space_rule3_upload_kib,
                'rule3_download_kib': self.config.cleanup_space_rule3_download_kib,
                'target_gb': self._space_target_gb(),
            },
            'reannounce_before_delete': self.config.cleanup_reannounce_before_delete
        }
