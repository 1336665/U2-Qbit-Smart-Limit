#!/usr/bin/env python3
"""
qBit Smart Limit - 工具函数模块
"""

import os
import re
import time
import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional, List, Deque
from datetime import datetime
from collections import deque


# ════════════════════════════════════════════════════════════════════════════════
# 常量配置
# ════════════════════════════════════════════════════════════════════════════════
class C:
    VERSION = "12.0.0 PRO"
    
    PHASE_WARMUP = "warmup"
    PHASE_CATCH = "catch"
    PHASE_STEADY = "steady"
    PHASE_FINISH = "finish"
    
    FINISH_TIME = 30
    STEADY_TIME = 120
    
    PRECISION_PERFECT = 0.001
    PRECISION_GOOD = 0.005
    
    SPEED_PROTECT_RATIO = 2.5
    SPEED_PROTECT_LIMIT = 1.3
    PROGRESS_PROTECT = 0.90
    
    MIN_LIMIT = 4096
    
    PID_PARAMS = {
        'warmup': {'kp': 0.3, 'ki': 0.05, 'kd': 0.02, 'headroom': 1.03},
        'catch':  {'kp': 0.5, 'ki': 0.10, 'kd': 0.05, 'headroom': 1.02},
        'steady': {'kp': 0.6, 'ki': 0.15, 'kd': 0.08, 'headroom': 1.005},
        'finish': {'kp': 0.8, 'ki': 0.20, 'kd': 0.12, 'headroom': 1.001},
    }
    
    QUANT_STEPS = {'finish': 256, 'steady': 512, 'catch': 2048, 'warmup': 4096}
    
    KALMAN_Q_SPEED = 0.1
    KALMAN_Q_ACCEL = 0.05
    KALMAN_R = 0.5
    
    SPEED_WINDOWS = [5, 15, 30, 60]
    WINDOW_WEIGHTS = {
        'warmup': {5: 0.1, 15: 0.2, 30: 0.3, 60: 0.4},
        'catch':  {5: 0.2, 15: 0.3, 30: 0.3, 60: 0.2},
        'steady': {5: 0.3, 15: 0.3, 30: 0.2, 60: 0.2},
        'finish': {5: 0.5, 15: 0.3, 30: 0.15, 60: 0.05},
    }
    
    MAX_REANNOUNCE = 86400
    PROPS_CACHE = {"finish": 0.2, "steady": 0.5, "catch": 1.0, "warmup": 2.0}
    LOG_INTERVAL = 20
    CONFIG_CHECK = 30
    
    ANNOUNCE_INTERVAL_NEW = 1800
    ANNOUNCE_INTERVAL_WEEK = 2700
    ANNOUNCE_INTERVAL_OLD = 3600
    
    SPEED_LIMIT = 50 * 1024 * 1024
    
    DL_LIMIT_MIN_TIME = 20
    DL_LIMIT_BUFFER = 30
    DL_LIMIT_MIN = 512
    DL_LIMIT_ADJUST_BUFFER = 60
    
    REANNOUNCE_WAIT_LIMIT = 5120
    REANNOUNCE_MIN_INTERVAL = 900
    REANNOUNCE_SPEED_SAMPLES = 300
    
    PEER_LIST_CHECK_INTERVAL = 300
    TID_SEARCH_INTERVAL = 60
    
    # 数据库相关
    DB_PATH = "qbit_smart_limit.db"
    DB_SAVE_INTERVAL = 180
    
    # TG Bot 轮询
    TG_POLL_INTERVAL = 2
    COOKIE_CHECK_INTERVAL = 3600
    
    # 订阅模块
    SUBSCRIPTION_INTERVAL = 60  # 默认60秒拉取一次
    SUBSCRIPTION_TASK_FILE = "subscription_tasks.json"
    
    # 删种模块
    CLEANUP_INTERVAL = 300  # 默认300秒检查一次
    CLEANUP_TASK_FILE = "cleanup_tasks.json"


# ════════════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════════════
def fmt_size(b: float, precision: int = 2) -> str:
    if b == 0: return "0 B"
    for u in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(b) < 1024: return f"{b:.{precision}f} {u}"
        b /= 1024
    return f"{b:.{precision}f} PiB"


def fmt_speed(b: float, precision: int = 1) -> str:
    if b == 0: return "0 B/s"
    for u in ['B/s', 'KiB/s', 'MiB/s', 'GiB/s']:
        if abs(b) < 1024: return f"{b:.{precision}f} {u}"
        b /= 1024
    return f"{b:.{precision}f} TiB/s"


def fmt_duration(s: float) -> str:
    s = max(0, int(s))
    if s < 60: return f"{s}s"
    if s < 3600: return f"{s//60}m{s%60}s"
    return f"{s//3600}h{(s%3600)//60}m"


def escape_html(t: str) -> str:
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def safe_div(a: float, b: float, default: float = 0) -> float:
    try:
        if b == 0 or abs(b) < 1e-10: return default
        return a / b
    except: return default


def clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def get_phase(tl: float, synced: bool) -> str:
    if not synced: return C.PHASE_WARMUP
    if tl <= C.FINISH_TIME: return C.PHASE_FINISH
    if tl <= C.STEADY_TIME: return C.PHASE_STEADY
    return C.PHASE_CATCH


def estimate_announce_interval(time_ref: float) -> int:
    age = time.time() - time_ref
    if age < 7 * 86400: return C.ANNOUNCE_INTERVAL_NEW
    elif age < 30 * 86400: return C.ANNOUNCE_INTERVAL_WEEK
    return C.ANNOUNCE_INTERVAL_OLD


def wall_time() -> float:
    return time.time()


def parse_speed_str(s: str) -> Optional[int]:
    """解析速度字符串，如 '100M' -> 102400 (KiB)"""
    s = s.strip().upper()
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(K|M|G|KB|MB|GB|KIB|MIB|GIB)?$', s)
    if not match: return None
    num = float(match.group(1))
    unit = match.group(2) or 'K'
    multipliers = {'K': 1, 'KB': 1, 'KIB': 1, 'M': 1024, 'MB': 1024, 'MIB': 1024, 'G': 1048576, 'GB': 1048576, 'GIB': 1048576}
    return int(num * multipliers.get(unit, 1))


# ════════════════════════════════════════════════════════════════════════════════
# 日志系统
# ════════════════════════════════════════════════════════════════════════════════
class LogBuffer:
    def __init__(self, maxlen: int = 100):
        self._buffer: Deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
    
    def add(self, msg: str):
        with self._lock:
            self._buffer.append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
    
    def get_recent(self, n: int = 10) -> List[str]:
        with self._lock:
            return list(self._buffer)[-n:]


class LoggerWrapper:
    def __init__(self, logger: logging.Logger, buffer: LogBuffer):
        self._logger = logger
        self._buffer = buffer
    
    def info(self, msg): self._logger.info(msg); self._buffer.add(f"[I] {msg}")
    def warning(self, msg): self._logger.warning(msg); self._buffer.add(f"[W] {msg}")
    def error(self, msg): self._logger.error(msg); self._buffer.add(f"[E] {msg}")
    def debug(self, msg): self._logger.debug(msg)


def setup_logging(level: str = "INFO") -> logging.Logger:
    log = logging.getLogger("qsl")
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        try: h.close()
        except: pass
    log.handlers.clear()
    
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    log.addHandler(console)
    
    try:
        os.makedirs("/var/log", exist_ok=True)
        fh = RotatingFileHandler("/var/log/qbit-smart-limit.log", maxBytes=10*1024*1024, backupCount=3)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        log.addHandler(fh)
    except: pass
    return log


# 全局日志实例
log_buffer = LogBuffer()
_raw_logger = setup_logging()
logger = LoggerWrapper(_raw_logger, log_buffer)


def get_logger() -> LoggerWrapper:
    return logger


def reinit_logger(level: str = "INFO") -> LoggerWrapper:
    global logger, _raw_logger
    _raw_logger = setup_logging(level)
    logger = LoggerWrapper(_raw_logger, log_buffer)
    return logger
