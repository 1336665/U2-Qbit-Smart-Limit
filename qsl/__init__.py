#!/usr/bin/env python3
"""
qBit Smart Limit - 模块化版本
PT上传速度精准控制器

模块:
- utils: 工具函数和常量
- config: 配置管理
- database: SQLite数据持久化
- telegram: Telegram双向交互
- u2_helper: U2网页辅助
- core: 核心限速控制
- subscription: 订阅模块
- cleanup: 删种模块
"""

from .utils import C, get_logger, reinit_logger, fmt_size, fmt_speed, fmt_duration
from .config import Config
from .database import Database
from .telegram import TelegramBot
from .u2_helper import U2WebHelper, is_bs4_available
from .core import (
    TorrentState, Stats, 
    PrecisionLimitController, DownloadLimiter, ReannounceOptimizer,
    precision_tracker
)
from .subscription import SubscriptionModule
from .cleanup import CleanupModule

__version__ = C.VERSION
__all__ = [
    'C', 'Config', 'Database', 'TelegramBot', 'U2WebHelper',
    'TorrentState', 'Stats', 'PrecisionLimitController',
    'DownloadLimiter', 'ReannounceOptimizer',
    'SubscriptionModule', 'CleanupModule',
    'get_logger', 'reinit_logger', 'is_bs4_available',
    'fmt_size', 'fmt_speed', 'fmt_duration', 'precision_tracker'
]
