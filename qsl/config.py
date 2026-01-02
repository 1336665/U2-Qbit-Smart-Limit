#!/usr/bin/env python3
"""
qBit Smart Limit - 配置模块
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .utils import C


@dataclass
class Config:
    host: str = "http://127.0.0.1:8080"
    username: str = "admin"
    password: str = ""
    target_speed_kib: int = 51200
    safety_margin: float = 0.98
    log_level: str = "INFO"
    target_tracker_keyword: str = ""
    exclude_tracker_keyword: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    max_physical_speed_kib: int = 0
    api_rate_limit: int = 20
    u2_cookie: str = ""
    proxy: str = ""
    peer_list_enabled: bool = True
    enable_dl_limit: bool = True
    enable_reannounce_opt: bool = True
    
    # 订阅模块配置
    subscription_enabled: bool = False
    subscription_interval: int = 60
    subscription_rss_url: str = ""
    subscription_download_path: str = ""
    subscription_category: str = ""
    subscription_paused: bool = False
    subscription_first_last_piece: bool = False  # 新增: 先下载首尾文件块
    
    # 删种模块配置
    cleanup_enabled: bool = False
    cleanup_interval: int = 300
    cleanup_min_ratio: float = 1.0
    cleanup_min_seeding_time: int = 86400
    cleanup_delete_files: bool = False
    cleanup_tracker_keyword: str = ""
    cleanup_reannounce_before_delete: bool = True  # 新增: 删除前强制汇报
    cleanup_reannounce_wait: int = 5  # 新增: 汇报后等待秒数
    
    # 新增: 基于剩余空间的删种规则
    cleanup_space_rules_enabled: bool = False
    # 规则1: 剩余空间小于10G，上传小于1MiB/s
    cleanup_space_rule1_gb: int = 10
    cleanup_space_rule1_upload_kib: int = 1024
    # 规则2: 剩余空间小于20G，下载完成，上传小于512KiB/s
    cleanup_space_rule2_gb: int = 20
    cleanup_space_rule2_upload_kib: int = 512
    # 规则3: 剩余空间小于5G，上传小于5MiB/s
    cleanup_space_rule3_gb: int = 5
    cleanup_space_rule3_upload_kib: int = 5120
    
    _mtime: float = 0
    
    @property
    def target_bytes(self) -> int:
        return int(self.target_speed_kib * 1024 * self.safety_margin)
    
    @property
    def max_physical_bytes(self) -> int:
        return int(self.max_physical_speed_kib * 1024) if self.max_physical_speed_kib > 0 else 0
    
    @staticmethod
    def load(path: str, db: 'Database' = None) -> Tuple[Optional['Config'], Optional[str]]:
        try:
            if not os.path.exists(path):
                return None, f"配置文件不存在: {path}"
            
            mtime = os.path.getmtime(path)
            
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
            
            # 支持嵌套配置结构 (subscription.* 和 cleanup.*)
            sub = d.get('subscription', {}) if isinstance(d.get('subscription'), dict) else {}
            clean = d.get('cleanup', {}) if isinstance(d.get('cleanup'), dict) else {}
            
            # 获取RSS URL - 支持新的feeds数组结构和旧的单URL结构
            rss_url = ''
            if sub.get('feeds') and isinstance(sub['feeds'], list) and len(sub['feeds']) > 0:
                # 新结构: subscription.feeds[0].url
                first_feed = sub['feeds'][0]
                if isinstance(first_feed, dict):
                    rss_url = str(first_feed.get('url', '')).strip()
            elif sub.get('rss_url'):
                # 旧结构: subscription.rss_url
                rss_url = str(sub.get('rss_url', '')).strip()
            else:
                # 扁平结构: subscription_rss_url
                rss_url = str(d.get('subscription_rss_url', '')).strip()
            
            cfg = Config(
                host=str(d.get('host', 'http://127.0.0.1:8080')).strip(),
                username=str(d.get('username', 'admin')).strip(),
                password=str(d.get('password', '')),
                target_speed_kib=int(d.get('target_speed_kib', 51200) or 51200),
                safety_margin=float(d.get('safety_margin', 0.98) or 0.98),
                log_level=str(d.get('log_level', 'INFO')).upper(),
                target_tracker_keyword=str(d.get('target_tracker_keyword', '')).strip(),
                exclude_tracker_keyword=str(d.get('exclude_tracker_keyword', '')).strip(),
                telegram_bot_token=str(d.get('telegram_bot_token', '')).strip(),
                telegram_chat_id=str(d.get('telegram_chat_id', '')).strip(),
                max_physical_speed_kib=int(d.get('max_physical_speed_kib', 0) or 0),
                api_rate_limit=int(d.get('api_rate_limit', 20) or 20),
                u2_cookie=str(d.get('u2_cookie', '')).strip(),
                proxy=str(d.get('proxy', '')).strip(),
                peer_list_enabled=bool(d.get('peer_list_enabled', True)),
                enable_dl_limit=bool(d.get('enable_dl_limit', True)),
                enable_reannounce_opt=bool(d.get('enable_reannounce_opt', True)),
                # 订阅模块 - 支持嵌套和扁平结构
                subscription_enabled=bool(sub.get('enabled', d.get('subscription_enabled', False))),
                subscription_interval=int(sub.get('interval_seconds', d.get('subscription_interval', 300)) or 300),
                subscription_rss_url=rss_url,
                subscription_download_path=str(sub.get('save_path', d.get('subscription_download_path', ''))).strip(),
                subscription_category=str(sub.get('category', d.get('subscription_category', ''))).strip(),
                subscription_paused=bool(sub.get('paused', d.get('subscription_paused', False))),
                subscription_first_last_piece=bool(sub.get('first_last_piece', d.get('subscription_first_last_piece', False))),
                # 删种模块 - 支持嵌套和扁平结构
                cleanup_enabled=bool(clean.get('enabled', d.get('cleanup_enabled', False))),
                cleanup_interval=int(clean.get('interval_seconds', d.get('cleanup_interval', 600)) or 600),
                cleanup_min_ratio=float(clean.get('min_ratio', d.get('cleanup_min_ratio', 1.0)) or 1.0),
                # 做种时间：嵌套结构存小时，扁平结构存秒
                cleanup_min_seeding_time=int(clean.get('min_seeding_time_hours', 0) or 0) * 3600 if 'min_seeding_time_hours' in clean else int(d.get('cleanup_min_seeding_time', 86400) or 86400),
                cleanup_delete_files=bool(clean.get('delete_files', d.get('cleanup_delete_files', False))),
                cleanup_tracker_keyword=str(clean.get('tracker_keyword', d.get('cleanup_tracker_keyword', ''))).strip(),
                cleanup_reannounce_before_delete=bool(clean.get('reannounce_before_delete', d.get('cleanup_reannounce_before_delete', True))),
                cleanup_reannounce_wait=int(clean.get('reannounce_wait', d.get('cleanup_reannounce_wait', 5)) or 5),
                # 基于剩余空间的删种规则
                cleanup_space_rules_enabled=bool(d.get('cleanup_space_rules_enabled', False)),
                cleanup_space_rule1_gb=int(d.get('cleanup_space_rule1_gb', 10) or 10),
                cleanup_space_rule1_upload_kib=int(d.get('cleanup_space_rule1_upload_kib', 1024) or 1024),
                cleanup_space_rule2_gb=int(d.get('cleanup_space_rule2_gb', 20) or 20),
                cleanup_space_rule2_upload_kib=int(d.get('cleanup_space_rule2_upload_kib', 512) or 512),
                cleanup_space_rule3_gb=int(d.get('cleanup_space_rule3_gb', 5) or 5),
                cleanup_space_rule3_upload_kib=int(d.get('cleanup_space_rule3_upload_kib', 5120) or 5120),
                _mtime=mtime
            )
            
            # 应用数据库中的运行时覆盖配置
            if db:
                for param, attr in [('host', 'host'), ('username', 'username'), ('password', 'password')]:
                    override = db.get_runtime_config(f"override_{attr}")
                    if override:
                        setattr(cfg, attr, override)
            
            return cfg, None
        except Exception as e:
            return None, str(e)
    
    def save(self, path: str) -> bool:
        """保存配置到文件"""
        try:
            data = {
                'host': self.host,
                'username': self.username,
                'password': self.password,
                'target_speed_kib': self.target_speed_kib,
                'safety_margin': self.safety_margin,
                'log_level': self.log_level,
                'target_tracker_keyword': self.target_tracker_keyword,
                'exclude_tracker_keyword': self.exclude_tracker_keyword,
                'telegram_bot_token': self.telegram_bot_token,
                'telegram_chat_id': self.telegram_chat_id,
                'max_physical_speed_kib': self.max_physical_speed_kib,
                'api_rate_limit': self.api_rate_limit,
                'u2_cookie': self.u2_cookie,
                'proxy': self.proxy,
                'peer_list_enabled': self.peer_list_enabled,
                'enable_dl_limit': self.enable_dl_limit,
                'enable_reannounce_opt': self.enable_reannounce_opt,
                # 订阅模块
                'subscription_enabled': self.subscription_enabled,
                'subscription_interval': self.subscription_interval,
                'subscription_rss_url': self.subscription_rss_url,
                'subscription_download_path': self.subscription_download_path,
                'subscription_category': self.subscription_category,
                'subscription_paused': self.subscription_paused,
                'subscription_first_last_piece': self.subscription_first_last_piece,
                # 删种模块
                'cleanup_enabled': self.cleanup_enabled,
                'cleanup_interval': self.cleanup_interval,
                'cleanup_min_ratio': self.cleanup_min_ratio,
                'cleanup_min_seeding_time': self.cleanup_min_seeding_time,
                'cleanup_delete_files': self.cleanup_delete_files,
                'cleanup_tracker_keyword': self.cleanup_tracker_keyword,
                'cleanup_reannounce_before_delete': self.cleanup_reannounce_before_delete,
                'cleanup_reannounce_wait': self.cleanup_reannounce_wait,
                # 基于剩余空间的删种规则
                'cleanup_space_rules_enabled': self.cleanup_space_rules_enabled,
                'cleanup_space_rule1_gb': self.cleanup_space_rule1_gb,
                'cleanup_space_rule1_upload_kib': self.cleanup_space_rule1_upload_kib,
                'cleanup_space_rule2_gb': self.cleanup_space_rule2_gb,
                'cleanup_space_rule2_upload_kib': self.cleanup_space_rule2_upload_kib,
                'cleanup_space_rule3_gb': self.cleanup_space_rule3_gb,
                'cleanup_space_rule3_upload_kib': self.cleanup_space_rule3_upload_kib,
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False
