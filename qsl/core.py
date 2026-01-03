#!/usr/bin/env python3
"""
qBit Smart Limit - 核心限速控制模块
PID控制、Kalman滤波、种子状态管理
"""

import threading
from typing import Dict, Optional, Tuple, List, Any, Deque
from dataclasses import dataclass, field
from collections import deque

from .utils import (
    C, safe_div, clamp, get_phase, estimate_announce_interval, 
    wall_time, get_logger
)


# ════════════════════════════════════════════════════════════════════════════════
# PID 控制器
# ════════════════════════════════════════════════════════════════════════════════
class PIDController:
    def __init__(self):
        self.kp = 0.6; self.ki = 0.15; self.kd = 0.08
        self._integral = 0.0; self._last_error = 0.0; self._last_time = 0.0
        self._last_output = 1.0; self._initialized = False
        self._integral_limit = 0.3; self._derivative_filter = 0.0
    
    def set_phase(self, phase: str):
        params = C.PID_PARAMS.get(phase, C.PID_PARAMS['steady'])
        self.kp, self.ki, self.kd = params['kp'], params['ki'], params['kd']
    
    def update(self, setpoint: float, measured: float, now: float) -> float:
        error = safe_div(setpoint - measured, max(setpoint, 1), 0)
        if not self._initialized:
            self._last_error = error; self._last_time = now; self._initialized = True
            return 1.0
        dt = now - self._last_time
        if dt <= 0.01: return self._last_output
        self._last_time = now
        
        p_term = self.kp * error
        self._integral = clamp(self._integral + error * dt, -self._integral_limit, self._integral_limit)
        i_term = self.ki * self._integral
        
        raw_derivative = (error - self._last_error) / dt
        self._derivative_filter = 0.3 * raw_derivative + 0.7 * self._derivative_filter
        d_term = self.kd * self._derivative_filter
        self._last_error = error
        
        output = clamp(1.0 + p_term + i_term + d_term, 0.5, 2.0)
        self._last_output = output
        return output
    
    def reset(self):
        self._integral = 0.0; self._last_error = 0.0; self._last_time = 0.0
        self._last_output = 1.0; self._derivative_filter = 0.0; self._initialized = False


# ════════════════════════════════════════════════════════════════════════════════
# Kalman 滤波器
# ════════════════════════════════════════════════════════════════════════════════
class ExtendedKalman:
    def __init__(self):
        self.speed = 0.0; self.accel = 0.0
        self.p00 = 1000.0; self.p01 = 0.0; self.p10 = 0.0; self.p11 = 1000.0
        self._last_time = 0.0; self._initialized = False
    
    def update(self, measurement: float, now: float) -> Tuple[float, float]:
        if not self._initialized:
            self.speed = measurement; self._last_time = now; self._initialized = True
            return measurement, 0.0
        dt = now - self._last_time
        if dt <= 0.01: return self.speed, self.accel
        self._last_time = now
        
        pred_speed = self.speed + self.accel * dt
        p00_pred = self.p00 + dt * (self.p10 + self.p01) + dt * dt * self.p11 + C.KALMAN_Q_SPEED
        p01_pred = self.p01 + dt * self.p11
        p10_pred = self.p10 + dt * self.p11
        p11_pred = self.p11 + C.KALMAN_Q_ACCEL
        
        s = p00_pred + C.KALMAN_R
        if abs(s) < 1e-10: return self.speed, self.accel
        k0, k1 = p00_pred / s, p10_pred / s
        innovation = measurement - pred_speed
        
        self.speed = pred_speed + k0 * innovation
        self.accel = self.accel + k1 * innovation
        self.p00 = (1 - k0) * p00_pred
        self.p01 = (1 - k0) * p01_pred
        self.p10 = -k1 * p00_pred + p10_pred
        self.p11 = -k1 * p01_pred + p11_pred
        return self.speed, self.accel
    
    def predict_upload(self, seconds: float) -> float:
        return max(0, self.speed * seconds + 0.5 * self.accel * seconds * seconds)
    
    def reset(self):
        self.speed = 0.0; self.accel = 0.0; self.p00 = 1000.0
        self.p01 = 0.0; self.p10 = 0.0; self.p11 = 1000.0; self._initialized = False


# ════════════════════════════════════════════════════════════════════════════════
# 多窗口速度跟踪器
# ════════════════════════════════════════════════════════════════════════════════
class MultiWindowSpeedTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=1200)
    
    def record(self, now: float, speed: float):
        with self._lock: self._samples.append((now, speed))
    
    def get_weighted_avg(self, now: float, phase: str) -> float:
        weights = C.WINDOW_WEIGHTS.get(phase, C.WINDOW_WEIGHTS['steady'])
        with self._lock: samples = list(self._samples)
        
        total_weight = 0.0; weighted_sum = 0.0
        for window in C.SPEED_WINDOWS:
            win_samples = [s for t, s in samples if now - t <= window]
            if win_samples:
                avg = sum(win_samples) / len(win_samples)
                w = weights.get(window, 0.25)
                weighted_sum += avg * w; total_weight += w
        return weighted_sum / total_weight if total_weight > 0 else 0.0
    
    def get_recent_trend(self, now: float, window: int = 10) -> float:
        with self._lock:
            samples = [(t, s) for t, s in self._samples if now - t <= window]
        if len(samples) < 5: return 0.0
        mid = len(samples) // 2
        first = sum(s for _, s in samples[:mid]) / mid
        second = sum(s for _, s in samples[mid:]) / (len(samples) - mid)
        return safe_div(second - first, first, 0)
    
    def clear(self):
        with self._lock: self._samples.clear()


# ════════════════════════════════════════════════════════════════════════════════
# 自适应量化器
# ════════════════════════════════════════════════════════════════════════════════
class AdaptiveQuantizer:
    @staticmethod
    def quantize(limit: int, phase: str, current_speed: float, target: float, trend: float = 0) -> int:
        if limit <= 0: return limit
        base = C.QUANT_STEPS.get(phase, 1024)
        ratio = safe_div(current_speed, target, 1)
        
        if phase == 'finish': step = 256
        elif ratio > 1.2: step = base * 2
        elif ratio > 1.05: step = base
        elif ratio > 0.8: step = base // 2
        else: step = base
        
        if abs(trend) > 0.1: step = max(256, step // 2)
        step = int(clamp(step, 256, 8192))
        return max(C.MIN_LIMIT, int((limit + step // 2) // step) * step)


# ════════════════════════════════════════════════════════════════════════════════
# 精度跟踪器
# ════════════════════════════════════════════════════════════════════════════════
class PrecisionTracker:
    def __init__(self, window: int = 30):
        self._history: Deque[Tuple[float, str, float]] = deque(maxlen=window)
        self._phase_adj: Dict[str, float] = {'warmup': 1.0, 'catch': 1.0, 'steady': 1.0, 'finish': 1.0}
        self._global_adj = 1.0
        self._lock = threading.Lock()
    
    def record(self, ratio: float, phase: str, now: float):
        with self._lock:
            self._history.append((ratio, phase, now))
            self._update()
    
    def _update(self):
        if len(self._history) < 5: return
        phase_data: Dict[str, List[float]] = {}
        for ratio, phase, _ in self._history:
            phase_data.setdefault(phase, []).append(ratio)
        
        for phase, ratios in phase_data.items():
            if len(ratios) < 3: continue
            avg = sum(ratios) / len(ratios)
            if avg > 1.005: adj = 0.998
            elif avg > 1.001: adj = 0.999
            elif avg < 0.99: adj = 1.002
            elif avg < 0.995: adj = 1.001
            else: adj = 1.0
            self._phase_adj[phase] = clamp(self._phase_adj[phase] * adj, 0.92, 1.08)
        
        all_ratios = [r for r, _, _ in self._history]
        global_avg = sum(all_ratios) / len(all_ratios)
        if global_avg > 1.002: self._global_adj = clamp(self._global_adj * 0.999, 0.95, 1.05)
        elif global_avg < 0.995: self._global_adj = clamp(self._global_adj * 1.001, 0.95, 1.05)
    
    def get_adjustment(self, phase: str) -> float:
        with self._lock:
            return self._phase_adj.get(phase, 1.0) * self._global_adj


# 全局精度跟踪器
precision_tracker = PrecisionTracker()


# ════════════════════════════════════════════════════════════════════════════════
# 精确限速控制器
# ════════════════════════════════════════════════════════════════════════════════
class PrecisionLimitController:
    def __init__(self):
        self.kalman = ExtendedKalman()
        self.speed_tracker = MultiWindowSpeedTracker()
        self.pid = PIDController()
        self._smooth_limit = -1
    
    def record_speed(self, now: float, speed: float):
        self.kalman.update(speed, now)
        self.speed_tracker.record(now, speed)
    
    def calculate(self, target: float, uploaded: int, time_left: float, elapsed: float, 
                  phase: str, now: float, precision_adj: float = 1.0) -> Tuple[int, str, Dict]:
        debug: Dict[str, Any] = {}
        adjusted_target = target * precision_adj
        
        kalman_speed = self.kalman.speed
        weighted_speed = self.speed_tracker.get_weighted_avg(now, phase)
        trend = self.speed_tracker.get_recent_trend(now)
        current_speed = weighted_speed if (phase == 'finish' and weighted_speed > 0) else (kalman_speed if kalman_speed > 0 else weighted_speed)
        
        total_time = elapsed + time_left
        target_total = adjusted_target * total_time
        debug['predicted_ratio'] = safe_div(uploaded + self.kalman.predict_upload(time_left), target_total, 0)
        
        need = max(0, target_total - uploaded)
        ideal = safe_div(need, max(1, time_left), adjusted_target)
        
        self.pid.set_phase(phase)
        pid_output = self.pid.update(ideal, current_speed, now)
        debug['pid_output'] = pid_output
        
        headroom = C.PID_PARAMS.get(phase, C.PID_PARAMS['steady'])['headroom']
        base_limit = int(ideal * pid_output * headroom)
        
        limit = AdaptiveQuantizer.quantize(base_limit, phase, current_speed, adjusted_target, trend)
        
        # 平滑限速变化
        if self._smooth_limit > 0:
            if phase == 'finish':
                smooth_factor = 0.5
            elif phase == 'steady':
                smooth_factor = 0.7
            else:
                smooth_factor = 0.85
            limit = int(limit * (1 - smooth_factor) + self._smooth_limit * smooth_factor)
        
        self._smooth_limit = limit
        
        # 确定reason
        if time_left <= C.FINISH_TIME:
            reason = "冲刺"
        elif time_left <= C.STEADY_TIME:
            reason = "稳定"
        elif uploaded < target_total * 0.5:
            reason = "追赶"
        else:
            reason = "预热"
        
        return limit, reason, debug
    
    def reset(self):
        self.kalman.reset()
        self.speed_tracker.clear()
        self.pid.reset()
        self._smooth_limit = -1


# ════════════════════════════════════════════════════════════════════════════════
# 速度跟踪器（用于上传/下载）
# ════════════════════════════════════════════════════════════════════════════════
class SpeedTracker:
    def __init__(self, maxlen: int = 600):
        self._samples: Deque[Tuple[float, int, int, float, float]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
    
    def record(self, now: float, total_up: int, total_dl: int, up_speed: float, dl_speed: float):
        with self._lock:
            self._samples.append((now, total_up, total_dl, up_speed, dl_speed))
    
    def get_avg_speeds(self, window: int = 300) -> Tuple[float, float]:
        now = wall_time()
        with self._lock:
            samples = [(t, u, d, us, ds) for t, u, d, us, ds in self._samples if now - t <= window]
        if len(samples) < 2: return 0, 0
        first, last = samples[0], samples[-1]
        dt = last[0] - first[0]
        if dt <= 0: return 0, 0
        return safe_div(last[1]-first[1], dt, 0), safe_div(last[2]-first[2], dt, 0)
    
    def clear(self):
        with self._lock: self._samples.clear()


# ════════════════════════════════════════════════════════════════════════════════
# 下载限速器
# ════════════════════════════════════════════════════════════════════════════════
class DownloadLimiter:
    @staticmethod
    def calc_dl_limit(state: 'TorrentState', total_uploaded: int, total_done: int, 
                      total_size: int, eta: int, up_speed: float, dl_speed: float, 
                      now: float) -> Tuple[int, str]:
        this_up = state.this_up(total_uploaded)
        this_time = state.this_time(now)
        if this_time < 2: return -1, ""
        
        avg_speed = this_up / this_time
        if avg_speed <= C.SPEED_LIMIT:
            if state.last_dl_limit > 0: return -1, "均值恢复"
            return -1, ""
        
        remaining = total_size - total_done
        if remaining <= 0: return -1, ""
        
        min_time = C.DL_LIMIT_MIN_TIME * (2 if state.last_up_limit > 0 else 1)
        
        if state.last_dl_limit <= 0:
            if 0 < eta <= min_time:
                denominator = this_up / C.SPEED_LIMIT - this_time + C.DL_LIMIT_BUFFER
                if denominator <= 0: return C.DL_LIMIT_MIN, "超速严重"
                dl_limit = remaining / denominator / 1024
                return max(C.DL_LIMIT_MIN, int(dl_limit)), f"均值超限"
        else:
            if avg_speed >= C.SPEED_LIMIT:
                if dl_speed / 1024 < 2 * state.last_dl_limit:
                    denominator = this_up / C.SPEED_LIMIT - this_time + C.DL_LIMIT_ADJUST_BUFFER
                    if denominator <= 0: return C.DL_LIMIT_MIN, "超速严重"
                    new_limit = remaining / denominator / 1024
                    new_limit = min(new_limit, 512000)
                    if new_limit > 1.5 * state.last_dl_limit:
                        new_limit = 1.5 * state.last_dl_limit
                    elif new_limit < state.last_dl_limit:
                        new_limit = new_limit / 1.5
                    return max(C.DL_LIMIT_MIN, int(new_limit)), "调整中"
                return state.last_dl_limit, "保持"
            else:
                return -1, "均值恢复"
        return -1, ""


# ════════════════════════════════════════════════════════════════════════════════
# 汇报优化器
# ════════════════════════════════════════════════════════════════════════════════
class ReannounceOptimizer:
    @staticmethod
    def should_reannounce(state: 'TorrentState', total_uploaded: int, total_done: int,
                          total_size: int, up_speed: float, dl_speed: float, 
                          now: float) -> Tuple[bool, str]:
        if state.last_reannounce > 0 and now - state.last_reannounce < C.REANNOUNCE_MIN_INTERVAL:
            return False, ""
        
        this_up = state.this_up(total_uploaded)
        this_time = state.this_time(now)
        if this_time < 30: return False, ""
        
        avg_up, avg_dl = state.speed_tracker.get_avg_speeds(C.REANNOUNCE_SPEED_SAMPLES)
        if avg_up <= C.SPEED_LIMIT or avg_dl <= 0: return False, ""
        
        remaining = total_size - total_done
        if remaining <= 0: return False, ""
        
        announce_interval = state.get_announce_interval()
        complete_time = remaining / avg_dl + now
        perfect_time = complete_time - announce_interval * C.SPEED_LIMIT / avg_up
        
        if this_up / this_time > C.SPEED_LIMIT:
            earliest = (this_up - C.SPEED_LIMIT * this_time) / (45 * 1024 * 1024) + now
        else:
            earliest = now
        
        if earliest - (now - this_time) < C.REANNOUNCE_MIN_INTERVAL:
            return False, ""
        
        if earliest > perfect_time:
            if now >= earliest:
                if this_up / this_time > C.SPEED_LIMIT:
                    return True, "优化汇报"
            else:
                if earliest < perfect_time + 60:
                    state.waiting_reannounce = True
                    return False, "等待汇报"
        return False, ""
    
    @staticmethod
    def check_waiting_reannounce(state: 'TorrentState', total_uploaded: int, 
                                  now: float) -> Tuple[bool, str]:
        if not state.waiting_reannounce: return False, ""
        this_up = state.this_up(total_uploaded)
        this_time = state.this_time(now)
        if this_time < C.REANNOUNCE_MIN_INTERVAL: return False, ""
        avg_speed = this_up / this_time
        if avg_speed < C.SPEED_LIMIT:
            return True, "均值恢复"
        return False, ""


# ════════════════════════════════════════════════════════════════════════════════
# 统计数据
# ════════════════════════════════════════════════════════════════════════════════
@dataclass
class Stats:
    start: float = field(default_factory=wall_time)
    total: int = 0
    success: int = 0
    precision: int = 0
    uploaded: int = 0
    
    def record(self, ratio: float, uploaded: int):
        self.total += 1
        self.uploaded += uploaded
        if ratio >= 0.95: self.success += 1
        if abs(ratio - 1) <= C.PRECISION_PERFECT: self.precision += 1
    
    def load_from_db(self, data: dict):
        if not data: return
        self.total = data.get('total', 0)
        self.success = data.get('success', 0)
        self.precision = data.get('precision', 0)
        self.uploaded = data.get('uploaded', 0)
        self.start = data.get('start', wall_time())


# ════════════════════════════════════════════════════════════════════════════════
# 种子状态类
# ════════════════════════════════════════════════════════════════════════════════
class TorrentState:
    def __init__(self, h: str):
        self.hash = h
        self.name = ""
        self._lock = threading.RLock()
        
        # 基础信息
        self._tid: Optional[int] = None
        self.tid_searched = False
        self.tid_search_time = 0.0
        self.tid_not_found = False
        self.promotion = "获取中..."
        self.monitor_notified = False
        
        # 周期信息
        self.cycle_start = 0.0
        self.cycle_start_uploaded = 0
        self.cycle_synced = False
        self.cycle_interval = 0.0
        self.cycle_index = 0
        self.jump_count = 0
        self.last_jump = 0.0
        
        # 时间信息
        self.time_added = 0.0
        self._publish_time: Optional[float] = None
        self._last_announce_time: Optional[float] = None
        
        # 上传信息
        self.initial_uploaded = 0
        self.total_size = 0
        self.total_uploaded_start = 0
        self.session_start_time = 0.0
        
        # 缓存
        self.cached_tl = 0.0
        self.cache_ts = 0.0
        self.prev_tl = 0.0
        
        # 限速状态
        self.last_up_limit = -1
        self.last_up_reason = ""
        self.last_dl_limit = -1
        self.dl_limited_this_cycle = False
        
        # 汇报状态
        self.last_reannounce = 0.0
        self.reannounced_this_cycle = False
        self.waiting_reannounce = False
        
        # 日志控制
        self.last_log = 0.0
        self.last_log_limit = -1
        self.last_props = 0.0
        self.report_sent = False
        
        # Peer list
        self.last_peer_list_check = 0.0
        self.peer_list_uploaded: Optional[int] = None
        
        # 控制器
        self.limit_controller = PrecisionLimitController()
        self.speed_tracker = SpeedTracker()
        self.last_debug: Dict[str, Any] = {}
    
    # 属性访问器
    @property
    def tid(self) -> Optional[int]:
        with self._lock: return self._tid
    
    @tid.setter
    def tid(self, value: Optional[int]):
        with self._lock: self._tid = value
    
    @property
    def publish_time(self) -> Optional[float]:
        with self._lock: return self._publish_time
    
    @publish_time.setter
    def publish_time(self, value: Optional[float]):
        with self._lock: self._publish_time = value
    
    @property
    def last_announce_time(self) -> Optional[float]:
        with self._lock: return self._last_announce_time
    
    @last_announce_time.setter
    def last_announce_time(self, value: Optional[float]):
        with self._lock: self._last_announce_time = value
    
    def get_tl(self, now: float) -> float:
        with self._lock:
            if self._last_announce_time and self._last_announce_time > 0:
                interval = self.get_announce_interval()
                next_announce = self._last_announce_time + interval
                return max(0, next_announce - now)
            if self.cache_ts <= 0: return 9999
            return max(0, self.cached_tl - (now - self.cache_ts))
    
    def get_phase(self, now: float) -> str:
        return get_phase(self.get_tl(now), self.cycle_synced)
    
    def get_announce_interval(self) -> int:
        with self._lock:
            if self._publish_time and self._publish_time > 0:
                return estimate_announce_interval(self._publish_time)
        if self.time_added > 0:
            return estimate_announce_interval(self.time_added)
        return C.ANNOUNCE_INTERVAL_NEW
    
    def elapsed(self, now: float) -> float:
        return max(0, now - self.cycle_start) if self.cycle_start > 0 else 0
    
    def this_time(self, now: float) -> float:
        return self.elapsed(now)
    
    def uploaded_in_cycle(self, current_uploaded: int) -> int:
        return max(0, current_uploaded - self.cycle_start_uploaded)
    
    def this_up(self, current_uploaded: int) -> int:
        return self.uploaded_in_cycle(current_uploaded)
    
    def estimate_total(self, now: float, tl: float) -> float:
        e = self.elapsed(now)
        if 0 < tl < C.MAX_REANNOUNCE: return max(1, e + tl)
        if self.cycle_synced and self.cycle_interval > 0: return max(1, self.cycle_interval)
        return max(1, e)
    
    def get_real_avg_speed(self, current_uploaded: int) -> float:
        if self.session_start_time <= 0: return 0
        elapsed = wall_time() - self.session_start_time
        if elapsed < 10: return 0
        uploaded = current_uploaded - self.total_uploaded_start
        return safe_div(uploaded, elapsed, 0)
    
    def new_cycle(self, now: float, uploaded: int, tl: float, is_jump: bool):
        if is_jump:
            self.jump_count += 1
            if self.jump_count >= 2 and self.last_jump > 0:
                self.cycle_interval = now - self.last_jump
                self.cycle_synced = True
            self.last_jump = now
            self.cycle_index += 1
            self.cycle_start_uploaded = uploaded
            with self._lock:
                self._last_announce_time = now
        elif self.time_added > 0 and (now - self.time_added) < self.get_announce_interval():
            self.cycle_start_uploaded = 0
        else:
            interval = self.get_announce_interval()
            elapsed_in_cycle = interval - tl if 0 < tl < interval else 0
            if elapsed_in_cycle > 60:
                avg_speed = self.limit_controller.kalman.speed
                if avg_speed > 0:
                    estimated_start = uploaded - int(avg_speed * elapsed_in_cycle)
                    self.cycle_start_uploaded = max(0, estimated_start)
                else:
                    self.cycle_start_uploaded = uploaded
            else:
                self.cycle_start_uploaded = uploaded
        
        self.cycle_start = now
        self.report_sent = False
        self.dl_limited_this_cycle = False
        self.reannounced_this_cycle = False
        self.waiting_reannounce = False
        self.last_dl_limit = -1
        self.limit_controller.reset()
        self.speed_tracker.clear()
    
    def to_db_dict(self) -> dict:
        """转换为可存储的字典"""
        return {
            'hash': self.hash, 'name': self.name, 'tid': self.tid,
            'promotion': self.promotion, 'publish_time': self.publish_time,
            'cycle_index': self.cycle_index, 'cycle_start': self.cycle_start,
            'cycle_start_uploaded': self.cycle_start_uploaded,
            'cycle_synced': self.cycle_synced, 'cycle_interval': self.cycle_interval,
            'total_uploaded_start': self.total_uploaded_start,
            'session_start_time': self.session_start_time,
            'last_announce_time': self.last_announce_time
        }
    
    def load_from_db(self, data: dict):
        """从数据库加载状态"""
        if not data: return
        self.name = data.get('name', '')
        self.tid = data.get('tid')
        self.promotion = data.get('promotion', '获取中...')
        self.publish_time = data.get('publish_time')
        self.cycle_index = data.get('cycle_index', 0)
        self.cycle_start = data.get('cycle_start', 0)
        self.cycle_start_uploaded = data.get('cycle_start_uploaded', 0)
        self.cycle_synced = data.get('cycle_synced', False)
        self.cycle_interval = data.get('cycle_interval', 0)
        self.total_uploaded_start = data.get('total_uploaded_start', 0)
        self.session_start_time = data.get('session_start_time', 0)
        self.last_announce_time = data.get('last_announce_time')
        
        if self.tid:
            self.tid_searched = True
