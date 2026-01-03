#!/usr/bin/env python3
"""
qBit Smart Limit - 订阅模块
从RSS/任务列表订阅新种子到qBittorrent
"""

import os
import json
import time
import hashlib
import threading
from typing import Optional, List, Dict
from datetime import datetime
import xml.etree.ElementTree as ET

import requests

from .utils import C, wall_time, get_logger, fmt_size


class SubscriptionModule:
    """
    订阅模块 - 支持RSS订阅和任务文件
    
    功能:
    1. 定时拉取RSS源，自动添加新种子
    2. 监控任务文件，处理手动添加的种子
    3. 去重处理，避免重复添加
    4. TG通知新添加的种子
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
        self.task_file = os.path.join(os.path.dirname(config._mtime and "" or C.SUBSCRIPTION_TASK_FILE), C.SUBSCRIPTION_TASK_FILE)
        
        # 内存中的已处理hash集合
        self._processed_hashes = set()
        self._load_processed_hashes()
    
    def _load_processed_hashes(self):
        """从数据库加载已处理的hash"""
        try:
            history = self.db.get_subscription_history(1000)
            self._processed_hashes = {h['hash'] for h in history}
        except Exception as e:
            get_logger().debug(f"加载订阅历史失败: {e}")
    
    def start(self):
        """启动订阅模块"""
        if self.running:
            return
        
        self.running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="Subscription")
        self._thread.start()
        get_logger().info("📥 订阅模块已启动")
    
    def stop(self):
        """停止订阅模块"""
        self.running = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        get_logger().info("📥 订阅模块已停止")
    
    def _worker(self):
        """后台工作线程"""
        logger = get_logger()
        interval = self.config.subscription_interval
        
        while not self._stop.is_set():
            try:
                # 1. 处理RSS订阅
                if self.config.subscription_rss_url:
                    self._process_rss()
                
                # 2. 处理任务文件
                self._process_task_file()
                
            except Exception as e:
                logger.error(f"订阅模块异常: {e}")
            
            # 等待下一次循环
            self._stop.wait(interval)
    
    def _process_rss(self):
        """处理RSS订阅"""
        logger = get_logger()
        rss_url = self.config.subscription_rss_url
        
        if not rss_url:
            return
        
        try:
            resp = None
            
            # 方法1: 先尝试直连（不使用代理）
            try:
                resp = requests.get(rss_url, timeout=30, proxies=None)
            except Exception as e1:
                logger.debug(f"RSS直连失败: {e1}")
                resp = None
            
            # 方法2: 如果直连失败且配置了代理，尝试代理
            if (resp is None or resp.status_code != 200) and self.config.proxy:
                try:
                    proxies = {'http': self.config.proxy, 'https': self.config.proxy}
                    resp = requests.get(rss_url, timeout=30, proxies=proxies)
                except Exception as e2:
                    logger.debug(f"RSS代理连接失败: {e2}")
                    resp = None
            
            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp else "无响应"
                logger.warning(f"RSS获取失败: {status}")
                return
            
            # 解析RSS
            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            
            added_count = 0
            for item in items:
                try:
                    title = item.find('title')
                    link = item.find('link')
                    enclosure = item.find('enclosure')
                    
                    if title is None:
                        continue
                    
                    name = title.text or "Unknown"
                    
                    # 获取种子链接
                    torrent_url = None
                    if enclosure is not None and enclosure.get('url'):
                        torrent_url = enclosure.get('url')
                    elif link is not None and link.text:
                        torrent_url = link.text
                    
                    if not torrent_url:
                        continue
                    
                    # 生成hash用于去重
                    url_hash = hashlib.md5(torrent_url.encode()).hexdigest()
                    
                    with self._lock:
                        if url_hash in self._processed_hashes:
                            continue
                        
                        # 添加到qBittorrent
                        if self._add_torrent(torrent_url, name, "RSS"):
                            self._processed_hashes.add(url_hash)
                            added_count += 1
                
                except Exception as e:
                    logger.debug(f"处理RSS item失败: {e}")
            
            if added_count > 0:
                logger.info(f"📥 RSS订阅添加了 {added_count} 个种子")
        
        except Exception as e:
            logger.error(f"RSS处理失败: {e}")
    
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
            added_count = 0
            
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                
                url = task.get('url', '').strip()
                name = task.get('name', 'Unknown')
                category = task.get('category', self.config.subscription_category)
                download_path = task.get('download_path', self.config.subscription_download_path)
                paused = task.get('paused', self.config.subscription_paused)
                
                if not url:
                    continue
                
                url_hash = hashlib.md5(url.encode()).hexdigest()
                
                with self._lock:
                    if url_hash in self._processed_hashes:
                        continue
                    
                    if self._add_torrent(url, name, "任务文件", category=category, 
                                         download_path=download_path, paused=paused):
                        self._processed_hashes.add(url_hash)
                        added_count += 1
                    else:
                        # 添加失败的任务保留
                        remaining_tasks.append(task)
            
            # 更新任务文件
            if remaining_tasks:
                with open(self.task_file, 'w', encoding='utf-8') as f:
                    json.dump(remaining_tasks, f, ensure_ascii=False, indent=2)
            else:
                os.remove(self.task_file)
            
            if added_count > 0:
                logger.info(f"📥 任务文件添加了 {added_count} 个种子")
        
        except json.JSONDecodeError:
            logger.warning(f"任务文件格式错误")
        except Exception as e:
            logger.error(f"任务文件处理失败: {e}")
    
    def _add_torrent(self, url: str, name: str, source: str, 
                     category: str = None, download_path: str = None, 
                     paused: bool = None) -> bool:
        """添加种子到qBittorrent"""
        logger = get_logger()
        
        try:
            # 准备参数
            add_params = {}
            
            if category or self.config.subscription_category:
                add_params['category'] = category or self.config.subscription_category
            
            if download_path or self.config.subscription_download_path:
                add_params['savepath'] = download_path or self.config.subscription_download_path
            
            if paused is not None:
                add_params['is_paused'] = paused
            elif self.config.subscription_paused:
                add_params['is_paused'] = True
            
            # 先下载首尾文件块
            if self.config.subscription_first_last_piece:
                add_params['firstLastPiecePrio'] = True
            
            # 判断是URL还是磁力链接
            if url.startswith('magnet:'):
                self.client.torrents_add(urls=url, **add_params)
            else:
                self.client.torrents_add(urls=url, **add_params)
            
            # 获取种子详细信息（多次尝试以确保获取到大小）
            size = 0
            torrent_hash = ""
            torrent_name = name
            
            for attempt in range(5):  # 最多尝试5次
                time.sleep(1)  # 每次等待1秒
                try:
                    torrents = self.client.torrents_info()
                    for t in torrents:
                        # 模糊匹配种子名称
                        if name.lower() in t.name.lower() or t.name.lower() in name.lower():
                            current_size = getattr(t, 'total_size', 0) or getattr(t, 'size', 0) or 0
                            if current_size > 0:
                                size = current_size
                                torrent_hash = t.hash
                                torrent_name = t.name
                                break
                    if size > 0:
                        break  # 成功获取到大小，退出重试
                except:
                    pass
            
            # 记录到数据库
            url_hash = hashlib.md5(url.encode()).hexdigest()
            self.db.add_subscription_history(url_hash, torrent_name, source)
            
            # TG通知 - 详细信息
            if self.notifier:
                self.notifier.subscription_notify_detailed(
                    name=torrent_name, 
                    size=size, 
                    source=source,
                    category=category or self.config.subscription_category,
                    download_path=download_path or self.config.subscription_download_path,
                    first_last_piece=self.config.subscription_first_last_piece,
                    paused=paused if paused is not None else self.config.subscription_paused
                )
            
            logger.info(f"📥 添加种子: {torrent_name[:40]} ({source}) 大小: {fmt_size(size)}")
            return True
        
        except Exception as e:
            logger.error(f"添加种子失败 {name[:30]}: {e}")
            return False
    
    def add_torrent_manual(self, url: str, name: str = "Unknown", **kwargs) -> bool:
        """手动添加种子（供外部调用）"""
        with self._lock:
            url_hash = hashlib.md5(url.encode()).hexdigest()
            if url_hash in self._processed_hashes:
                return False
            
            if self._add_torrent(url, name, "手动", **kwargs):
                self._processed_hashes.add(url_hash)
                return True
            return False
    
    def run_once(self) -> dict:
        """手动执行一次RSS抓取（用于测试）"""
        logger = get_logger()
        result = {
            'success': False,
            'rss_url': self.config.subscription_rss_url,
            'items_found': 0,
            'items_added': 0,
            'errors': []
        }
        
        rss_url = self.config.subscription_rss_url
        if not rss_url:
            result['errors'].append("RSS URL未配置")
            return result
        
        try:
            import requests
            resp = None
            
            # 方法1: 先尝试直连
            logger.info(f"📥 正在获取RSS (直连): {rss_url[:60]}...")
            try:
                resp = requests.get(rss_url, timeout=30, proxies=None)
            except Exception as e1:
                logger.debug(f"直连失败: {e1}")
                result['errors'].append(f"直连失败: {str(e1)[:50]}")
                resp = None
            
            # 方法2: 如果直连失败且配置了代理，尝试代理
            if (resp is None or resp.status_code != 200) and self.config.proxy:
                logger.info(f"📥 尝试代理连接...")
                try:
                    proxies = {'http': self.config.proxy, 'https': self.config.proxy}
                    resp = requests.get(rss_url, timeout=30, proxies=proxies)
                except Exception as e2:
                    logger.debug(f"代理连接失败: {e2}")
                    result['errors'].append(f"代理连接失败: {str(e2)[:50]}")
                    resp = None
            
            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp else "无响应"
                result['errors'].append(f"HTTP {status}")
                return result
            
            # 解析RSS
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            items = root.findall('.//item')
            result['items_found'] = len(items)
            
            added_count = 0
            for item in items:
                try:
                    title = item.find('title')
                    link = item.find('link')
                    enclosure = item.find('enclosure')
                    
                    if title is None:
                        continue
                    
                    name = title.text or "Unknown"
                    
                    # 获取种子链接
                    torrent_url = None
                    if enclosure is not None and enclosure.get('url'):
                        torrent_url = enclosure.get('url')
                    elif link is not None and link.text:
                        torrent_url = link.text
                    
                    if not torrent_url:
                        continue
                    
                    # 生成hash用于去重
                    import hashlib
                    url_hash = hashlib.md5(torrent_url.encode()).hexdigest()
                    
                    with self._lock:
                        if url_hash in self._processed_hashes:
                            continue
                        
                        # 添加到qBittorrent
                        if self._add_torrent(torrent_url, name, "RSS手动测试"):
                            self._processed_hashes.add(url_hash)
                            added_count += 1
                
                except Exception as e:
                    result['errors'].append(f"处理item失败: {e}")
            
            result['items_added'] = added_count
            result['success'] = True
            logger.info(f"📥 手动RSS测试完成: 发现{result['items_found']}个, 新增{added_count}个")
        
        except Exception as e:
            result['errors'].append(str(e))
            logger.error(f"手动RSS测试失败: {e}")
        
        return result
    
    def get_status(self) -> dict:
        """获取模块状态"""
        return {
            'running': self.running,
            'interval': self.config.subscription_interval,
            'rss_url': self.config.subscription_rss_url[:50] + '...' if len(self.config.subscription_rss_url) > 50 else self.config.subscription_rss_url,
            'processed_count': len(self._processed_hashes),
            'history_count': len(self.db.get_subscription_history(1000))
        }
