#!/usr/bin/env python3
"""
qBit Smart Limit - Telegram 双向交互模块
"""

import re
import html
import time
import queue
import threading
from typing import Dict, Optional, List
from datetime import datetime

import requests

from .utils import C, fmt_size, fmt_speed, fmt_duration, escape_html, safe_div, wall_time, log_buffer, get_logger


class TelegramBot:
    """支持命令交互的 Telegram Bot"""
    
    def __init__(self, token: str, chat_id: str, controller: 'Controller' = None):
        self.enabled = bool(token and chat_id)
        self.token = token
        self.chat_id = str(chat_id).strip()
        self.controller = controller
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""
        
        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._last_update_id = 0
        self._last_send: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        
        # 运行时状态
        self.paused = False
        self.temp_target_kib: Optional[int] = None
        
        # 下载完成通知追踪
        self._finish_notified = set()
        
        if self.enabled:
            self._session = requests.Session()
            threading.Thread(target=self._send_worker, daemon=True, name="TG-Sender").start()
            threading.Thread(target=self._poll_worker, daemon=True, name="TG-Poller").start()
    
    def _html_sanitize(self, msg: str) -> str:
        """
        Sanitize message for Telegram HTML parse_mode.
        """
        if not msg:
            return msg

        msg = re.sub(r'&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', str(msg))

        if '<' not in msg:
            return msg

        allowed = {
            'b','strong','i','em','u','ins','s','strike','del',
            'code','pre','a','span','tg-spoiler','blockquote'
        }

        def repl(m: re.Match) -> str:
            full = m.group(0)
            inner = (m.group(1) or '').strip()
            if not inner:
                return html.escape(full)

            name = inner.lstrip('/').split()[0].lower()
            if name not in allowed:
                return html.escape(full)

            if name == 'a' and not inner.startswith('/'):
                if re.search(r'\bhref\s*=', inner, flags=re.IGNORECASE):
                    return full
                return html.escape(full)

            if name == 'span' and not inner.startswith('/'):
                if re.search(r'tg-spoiler', inner, flags=re.IGNORECASE):
                    return full
                return html.escape(full)

            return full

        return re.sub(r'<([^>]*)>', repl, msg)
    
    def close(self):
        self._stop.set()
    
    def send(self, msg: str, tag: str = "", cooldown: int = 10):
        if not self.enabled: return
        now = wall_time()
        with self._lock:
            if tag and tag in self._last_send and now - self._last_send[tag] < cooldown:
                return
            if tag:
                self._last_send[tag] = now
        try:
            self._queue.put_nowait((msg, tag))
        except queue.Full:
            pass
    
    def send_immediate(self, msg: str):
        """立即发送消息（用于命令响应）"""
        if not self.enabled: return
        try:
            safe_msg = self._html_sanitize(msg)
            self._session.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": safe_msg, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            get_logger().debug(f"TG发送失败: {e}")
    
    def _send_worker(self):
        while not self._stop.is_set():
            try:
                msg, tag = self._queue.get(timeout=2)
                safe_msg = self._html_sanitize(msg)
                self._session.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": safe_msg, "parse_mode": "HTML"},
                    timeout=15
                )
            except queue.Empty:
                pass
            except Exception as e:
                get_logger().debug(f"TG发送失败: {e}")
    
    def _poll_worker(self):
        logger = get_logger()
        try:
            resp = self._session.get(f"{self.base_url}/getMe", timeout=10)
            if resp.status_code != 200:
                logger.warning(f"⚠️ TG getMe失败 HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"TG getMe异常: {e}")

        while not self._stop.is_set():
            try:
                resp = self._session.get(
                    f"{self.base_url}/getUpdates",
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                        "allowed_updates": ["message"]
                    },
                    timeout=35
                )
                if resp.status_code != 200:
                    logger.warning(f"⚠️ TG getUpdates失败 HTTP {resp.status_code}: {resp.text[:200]}")
                    time.sleep(5)
                    continue

                data = resp.json()
                for update in data.get('result', []):
                    self._last_update_id = update.get('update_id', self._last_update_id)
                    msg = update.get('message', {}) or {}
                    text = (msg.get('text') or '').strip()
                    chat_id = str((msg.get('chat') or {}).get('id', ''))

                    if not text:
                        continue

                    if text.startswith('/'):
                        logger.info(f"📩 TG命令: chat_id={chat_id} text={text}")

                    if chat_id == self.chat_id and text.startswith('/'):
                        self._handle_command(text)
                    elif text.startswith('/'):
                        logger.warning(f"🚫 TG未授权chat_id: {chat_id} (期望 {self.chat_id})")
            except Exception as e:
                logger.debug(f"TG轮询异常: {e}")
                time.sleep(5)

            time.sleep(C.TG_POLL_INTERVAL)
    
    def _handle_command(self, text: str):
        """处理用户命令"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        handlers = {
            '/start': self._cmd_start,
            '/help': self._cmd_help,
            '/status': self._cmd_status,
            '/pause': self._cmd_pause,
            '/resume': self._cmd_resume,
            '/limit': self._cmd_limit,
            '/log': self._cmd_log,
            '/cookie': self._cmd_cookie,
            '/config': self._cmd_config,
            '/stats': self._cmd_stats,
            # 新增订阅和删种命令
            '/sub': self._cmd_subscription,
            '/cleanup': self._cmd_cleanup,
        }
        
        handler = handlers.get(cmd, self._cmd_unknown)
        try:
            handler(args)
        except Exception as e:
            self.send_immediate(f"❌ 命令执行出错: {e}")
    
    # ═══════════════════════════════════════════
    # 命令处理器
    # ═══════════════════════════════════════════
    def _cmd_start(self, args: str):
        self._cmd_help(args)
    
    def _cmd_help(self, args: str):
        msg = """🤖 <b>qBit Smart Limit 命令帮助</b>
━━━━━━━━━━━━━━━━━━━━━
📊 <b>状态查询</b>
├ /status - 查看所有种子状态
├ /stats - 查看统计信息
└ /log [n] - 查看最近n条日志

⚙️ <b>控制命令</b>
├ /pause - 暂停限速功能
├ /resume - 恢复限速功能
└ /limit &lt;速度&gt; - 设置目标速度
   例: /limit 100M 或 /limit 51200K

📥 <b>订阅管理</b>
├ /sub status - 查看订阅状态
├ /sub start - 启动订阅
└ /sub stop - 停止订阅

🗑️ <b>删种管理</b>
├ /cleanup status - 查看删种状态
├ /cleanup start - 启动删种
└ /cleanup stop - 停止删种

🔧 <b>配置管理</b>
├ /cookie - 检查U2 Cookie状态
└ /config &lt;参数&gt; &lt;值&gt; - 修改配置
━━━━━━━━━━━━━━━━━━━━━
💡 速度单位支持: K/M/G (KiB)"""
        self.send_immediate(msg)
    
    def _cmd_status(self, args: str):
        if not self.controller:
            self.send_immediate("❌ 控制器未初始化")
            return
        
        states = self.controller.states
        if not states:
            self.send_immediate("📭 当前没有正在监控的种子")
            return
        
        now = wall_time()
        lines = ["📊 <b>种子状态总览</b>", "━━━━━━━━━━━━━━━━━━━━━"]
        
        for h, state in list(states.items())[:10]:
            name = escape_html(state.name[:25])
            phase = state.get_phase(now)
            tl = state.get_tl(now)
            
            speed = state.limit_controller.kalman.speed
            
            phase_emoji = {'warmup': '🔥', 'catch': '🏃', 'steady': '⚖️', 'finish': '🎯'}.get(phase, '❓')
            
            lines.append(f"{phase_emoji} <b>{name}</b>")
            lines.append(f"   ↑{fmt_speed(speed)} | ⏱{tl:.0f}s | 周期#{state.cycle_index}")
        
        if len(states) > 10:
            lines.append(f"\n... 还有 {len(states)-10} 个种子")
        
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
        status = "⏸️ 已暂停" if self.paused else "▶️ 运行中"
        target = self.temp_target_kib or self.controller.config.target_speed_kib
        lines.append(f"状态: {status} | 目标: {fmt_speed(target * 1024)}")
        
        self.send_immediate("\n".join(lines))
    
    def _cmd_pause(self, args: str):
        self.paused = True
        self.send_immediate("""⏸️ <b>限速功能已暂停</b>
━━━━━━━━━━━━━━━━━━━━━
所有种子将以最大速度运行
发送 /resume 恢复限速""")
        get_logger().warning("⏸️ 用户暂停了限速功能")
    
    def _cmd_resume(self, args: str):
        self.paused = False
        self.send_immediate("""▶️ <b>限速功能已恢复</b>
━━━━━━━━━━━━━━━━━━━━━
种子将按目标速度限制""")
        get_logger().info("▶️ 用户恢复了限速功能")
    
    def _cmd_limit(self, args: str):
        from .utils import parse_speed_str
        
        if not args:
            current = self.temp_target_kib or (self.controller.config.target_speed_kib if self.controller else 0)
            self.send_immediate(f"🎯 当前目标速度: <code>{fmt_speed(current * 1024)}</code>\n用法: /limit <速度> (如 100M)")
            return
        
        new_limit = parse_speed_str(args)
        if not new_limit or new_limit <= 0:
            self.send_immediate("❌ 无效的速度值\n例: /limit 100M 或 /limit 51200K")
            return
        
        old_limit = self.temp_target_kib or (self.controller.config.target_speed_kib if self.controller else 0)
        self.temp_target_kib = new_limit
        
        self.send_immediate(f"""🎯 <b>目标速度已修改</b>
━━━━━━━━━━━━━━━━━━━━━
原速度: <code>{fmt_speed(old_limit * 1024)}</code>
新速度: <code>{fmt_speed(new_limit * 1024)}</code>
━━━━━━━━━━━━━━━━━━━━━
⚠️ 此为临时设置，重启后恢复""")
        get_logger().info(f"🎯 用户修改目标速度: {fmt_speed(old_limit*1024)} → {fmt_speed(new_limit*1024)}")
    
    def _cmd_log(self, args: str):
        try:
            n = int(args) if args else 10
            n = min(max(1, n), 30)
        except:
            n = 10
        
        logs = log_buffer.get_recent(n)
        if not logs:
            self.send_immediate("📜 暂无日志记录")
            return
        
        msg = f"📜 <b>最近 {len(logs)} 条日志</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        msg += "\n".join(f"<code>{escape_html(l)}</code>" for l in logs)
        self.send_immediate(msg)
    
    def _cmd_cookie(self, args: str):
        if not self.controller or not self.controller.u2_helper:
            self.send_immediate("❌ U2辅助功能未启用")
            return
        
        self.send_immediate("🔍 正在检查 Cookie 状态...")
        valid, msg = self.controller.u2_helper.check_cookie_valid()
        
        if valid:
            self.send_immediate(f"""✅ <b>Cookie 状态正常</b>
━━━━━━━━━━━━━━━━━━━━━
状态: {msg}
检查时间: {datetime.now().strftime('%H:%M:%S')}""")
        else:
            self.send_immediate(f"""❌ <b>Cookie 状态异常</b>
━━━━━━━━━━━━━━━━━━━━━
问题: {msg}
━━━━━━━━━━━━━━━━━━━━━
⚠️ 请尽快更新 Cookie！""")
    
    def _cmd_config(self, args: str):
        if not args:
            self.send_immediate("""⚙️ <b>配置修改帮助</b>
━━━━━━━━━━━━━━━━━━━━━
用法: /config &lt;参数&gt; &lt;值&gt;

可用参数:
├ qb_host - qBittorrent 地址
├ qb_user - qBittorrent 用户名
└ qb_pass - qBittorrent 密码

示例: /config qb_host http://127.0.0.1:8080""")
            return
        
        parts = args.split(maxsplit=1)
        if len(parts) != 2:
            self.send_immediate("❌ 用法: /config <参数> <值>")
            return
        
        param, value = parts
        param = param.lower()
        
        valid_params = {'qb_host': 'host', 'qb_user': 'username', 'qb_pass': 'password'}
        if param not in valid_params:
            self.send_immediate(f"❌ 未知参数: {param}\n可用: qb_host, qb_user, qb_pass")
            return
        
        if self.controller and self.controller.db:
            self.controller.db.save_runtime_config(f"override_{valid_params[param]}", value)
            self.send_immediate(f"""✅ <b>配置已保存</b>
━━━━━━━━━━━━━━━━━━━━━
参数: {param}
新值: <code>{escape_html(value[:30])}</code>
━━━━━━━━━━━━━━━━━━━━━
⚠️ 需要重启脚本生效""")
        else:
            self.send_immediate("❌ 数据库未初始化")
    
    def _cmd_stats(self, args: str):
        if not self.controller:
            self.send_immediate("❌ 控制器未初始化")
            return
        
        stats = self.controller.stats
        runtime = wall_time() - stats.start
        
        success_rate = safe_div(stats.success, stats.total, 0) * 100
        precision_rate = safe_div(stats.precision, stats.total, 0) * 100
        
        self.send_immediate(f"""📈 <b>运行统计</b>
━━━━━━━━━━━━━━━━━━━━━
⏱️ 运行时长: <code>{fmt_duration(runtime)}</code>

📊 <b>周期统计</b>
├ 总周期数: <code>{stats.total}</code>
├ 达标率: <code>{success_rate:.1f}%</code> ({stats.success}/{stats.total})
└ 精准率: <code>{precision_rate:.1f}%</code> ({stats.precision}/{stats.total})

📤 <b>流量统计</b>
└ 总上传: <code>{fmt_size(stats.uploaded)}</code>""")
    
    def _cmd_subscription(self, args: str):
        """订阅模块命令"""
        if not self.controller:
            self.send_immediate("❌ 控制器未初始化")
            return
        
        sub_args = args.strip().lower()
        sub = getattr(self.controller, 'subscription_module', None)
        
        if sub_args == "status":
            if sub:
                status = "▶️ 运行中" if sub.running else "⏹️ 已停止"
                interval = self.controller.config.subscription_interval
                rss_url = self.controller.config.subscription_rss_url
                rss_status = "✅ 已配置" if rss_url else "❌ 未配置"
                count = len(self.controller.db.get_subscription_history(100))
                auto_start = "✅" if self.controller.config.subscription_enabled else "❌"
                self.send_immediate(f"""📥 <b>订阅模块状态</b>
━━━━━━━━━━━━━━━━━━━━━
运行状态: {status}
自动启动: {auto_start}
RSS源: {rss_status}
拉取间隔: {interval}秒
已添加种子: {count}个

💡 使用 /sub start 启动模块""")
            else:
                self.send_immediate("""❌ <b>订阅模块未初始化</b>

可能原因:
1. 脚本刚启动，模块正在初始化
2. 模块初始化时发生错误

💡 请检查日志或重启服务""")
        elif sub_args == "start":
            if sub:
                if sub.running:
                    self.send_immediate("⚠️ 订阅模块已在运行中")
                else:
                    sub.start()
                    self.send_immediate("✅ 订阅模块已启动")
            else:
                self.send_immediate("❌ 订阅模块未初始化，无法启动")
        elif sub_args == "stop":
            if sub:
                if not sub.running:
                    self.send_immediate("⚠️ 订阅模块未在运行")
                else:
                    sub.stop()
                    self.send_immediate("⏹️ 订阅模块已停止")
            else:
                self.send_immediate("❌ 订阅模块未初始化")
        else:
            self.send_immediate("""📥 <b>订阅模块命令</b>
━━━━━━━━━━━━━━━━━━━━━
/sub status - 查看状态
/sub start - 启动订阅
/sub stop - 停止订阅""")
    
    def _cmd_cleanup(self, args: str):
        """删种模块命令"""
        if not self.controller:
            self.send_immediate("❌ 控制器未初始化")
            return
        
        cleanup_args = args.strip().lower()
        cleanup = getattr(self.controller, 'cleanup_module', None)
        
        if cleanup_args == "status":
            if cleanup:
                status = "▶️ 运行中" if cleanup.running else "⏹️ 已停止"
                interval = self.controller.config.cleanup_interval
                min_ratio = self.controller.config.cleanup_min_ratio
                min_time = self.controller.config.cleanup_min_seeding_time
                delete_files = "✅" if self.controller.config.cleanup_delete_files else "❌"
                auto_start = "✅" if self.controller.config.cleanup_enabled else "❌"
                count = len(self.controller.db.get_cleanup_history(100))
                
                # 格式化做种时间
                hours = min_time // 3600
                time_str = f"{hours}小时" if hours > 0 else f"{min_time}秒"
                
                self.send_immediate(f"""🗑️ <b>删种模块状态</b>
━━━━━━━━━━━━━━━━━━━━━
运行状态: {status}
自动启动: {auto_start}
检查间隔: {interval}秒
最小分享率: {min_ratio}
最小做种: {time_str}
删除文件: {delete_files}
已删除种子: {count}个

💡 使用 /cleanup start 启动模块""")
            else:
                self.send_immediate("""❌ <b>删种模块未初始化</b>

可能原因:
1. 脚本刚启动，模块正在初始化
2. 模块初始化时发生错误

💡 请检查日志或重启服务""")
        elif cleanup_args == "start":
            if cleanup:
                if cleanup.running:
                    self.send_immediate("⚠️ 删种模块已在运行中")
                else:
                    cleanup.start()
                    self.send_immediate("✅ 删种模块已启动")
            else:
                self.send_immediate("❌ 删种模块未初始化，无法启动")
        elif cleanup_args == "stop":
            if cleanup:
                if not cleanup.running:
                    self.send_immediate("⚠️ 删种模块未在运行")
                else:
                    cleanup.stop()
                    self.send_immediate("⏹️ 删种模块已停止")
            else:
                self.send_immediate("❌ 删种模块未初始化")
        else:
            self.send_immediate("""🗑️ <b>删种模块命令</b>
━━━━━━━━━━━━━━━━━━━━━
/cleanup status - 查看状态
/cleanup start - 启动删种
/cleanup stop - 停止删种""")
    
    def _cmd_unknown(self, args: str):
        self.send_immediate("❓ 未知命令，发送 /help 查看帮助")
    
    # ═══════════════════════════════════════════
    # 通知方法（美化版）
    # ═══════════════════════════════════════════
    def startup(self, config: 'Config', qb_version: str = "", u2_enabled: bool = False):
        if not self.enabled: return
        msg = f"""🚀 <b>qBit Smart Limit 已启动</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>版本</b>: v{C.VERSION}

⚙️ <b>配置信息</b>
├ 🎯 目标速度: <code>{fmt_speed(config.target_bytes)}</code>
├ 🛡️ 安全边际: <code>{config.safety_margin:.0%}</code>
├ 🔄 汇报优化: {'✅' if config.enable_reannounce_opt else '❌'}
└ 📥 下载限速: {'✅' if config.enable_dl_limit else '❌'}

💻 <b>系统状态</b>
├ 🤖 qBittorrent: <code>{qb_version}</code>
├ 🌐 U2辅助: {'✅' if u2_enabled else '❌'}
├ 📥 订阅模块: {'✅' if config.subscription_enabled else '❌'}
├ 🗑️ 删种模块: {'✅' if config.cleanup_enabled else '❌'}
└ 🕒 启动时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>

━━━━━━━━━━━━━━━━━━━━━
💡 发送 /help 查看可用命令"""
        self.send(msg, "startup", 0)
    
    def monitor_start(self, info: dict):
        if not self.enabled: return
        h = info.get('hash', '')
        name = escape_html(info.get('name', 'Unknown'))
        total_size = info.get('total_size', 0)
        target = info.get('target', 0)
        promotion = info.get('promotion', '无优惠')
        tid = info.get('tid')
        
        if tid and tid > 0:
            linked_name = f'<a href="https://u2.dmhy.org/details.php?id={tid}&hit=1">{name}</a>'
        else:
            linked_name = f"<b>{name}</b>"
        
        msg = f"""🎬 <b>开始监控新任务</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {linked_name}

📦 种子大小: <code>{fmt_size(total_size)}</code>
🎯 目标均速: <code>{fmt_speed(target)}</code>
🍪 优惠状态: <code>{promotion}</code>
📅 开始时间: <code>{datetime.now().strftime('%H:%M:%S')}</code>"""
        self.send(msg, f"start_{h}", 0)
    
    def check_finish(self, info: dict):
        """检查并通知下载完成"""
        if not self.enabled: return
        h = info.get('hash', '')
        progress = info.get('progress', 0)
        
        if progress >= 0.999 and h not in self._finish_notified:
            self._finish_notified.add(h)
            name = escape_html(info.get('name', 'Unknown'))
            total_up = info.get('total_uploaded', 0)
            total_dl = info.get('total_downloaded', 0)
            
            msg = f"""🎉 <b>种子下载完成!</b>
━━━━━━━━━━━━━━━━━━━━━
📛 <b>{name}</b>

⏱️ 完成时间: <code>{datetime.now().strftime('%H:%M:%S')}</code>

📊 <b>流量统计</b>
├ 📤 已上传: <code>{fmt_size(total_up)}</code>
└ 📥 已下载: <code>{fmt_size(total_dl)}</code>"""
            self.send(msg, f"finish_{h}", 0)
    
    def cycle_report(self, info: dict):
        if not self.enabled: return
        
        name = escape_html(info.get('name', 'Unknown')[:35])
        cycle_idx = info.get('idx', 0)
        uploaded = info.get('uploaded', 0)
        duration = info.get('duration', 0)
        ratio = info.get('ratio', 0)
        real_speed = info.get('real_speed', 0)
        progress_pct = info.get('progress_pct', 0)
        total_size = info.get('total_size', 0)
        total_up_life = info.get('total_uploaded_life', 0)
        total_dl_life = info.get('total_downloaded_life', 0)
        
        if ratio >= 0.99:
            status = "🎯 完美"
        elif ratio >= 0.95:
            status = "✅ 达标"
        elif ratio >= 0.90:
            status = "👍 良好"
        else:
            status = "⚠️ 欠速"
        
        left_size = total_size * (1 - progress_pct / 100)
        
        msg = f"""📊 <b>周期汇报 #{cycle_idx}</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {name}

⚡ <b>本周期 ({fmt_duration(duration)})</b>
├ 📤 上传: <code>{fmt_size(uploaded)}</code>
├ 📈 均速: <code>{fmt_speed(real_speed)}</code>
└ 🎯 达标: {status} (<code>{ratio*100:.1f}%</code>)

📉 <b>整体进度</b>
├ ⏳ 进度: <code>{progress_pct:.1f}%</code>
├ 📦 剩余: <code>{fmt_size(left_size)}</code>
├ 📤 总上传: <code>{fmt_size(total_up_life)}</code>
└ 📥 总下载: <code>{fmt_size(total_dl_life)}</code>"""
        self.send(msg, f"cycle_{info.get('hash', '')}", 5)
    
    def overspeed_warning(self, name: str, real_speed: float, target: float, tid: int = None):
        msg = f"""🚨 <b>超速警告</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {escape_html(name[:30])}

⚠️ 实际速度: <code>{fmt_speed(real_speed)}</code>
🎯 目标速度: <code>{fmt_speed(target)}</code>
📊 超速比例: <code>{real_speed/target*100:.0f}%</code>"""
        self.send(msg, f"overspeed_{name[:10]}", 120)
    
    def dl_limit_notify(self, name: str, dl_limit: float, reason: str, tid: int = None):
        msg = f"""📥 <b>下载限速启动</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {escape_html(name[:30])}
🔒 限制速度: <code>{fmt_speed(dl_limit * 1024)}</code>
📝 原因: {reason}"""
        self.send(msg, f"dl_limit_{name[:10]}", 60)
    
    def reannounce_notify(self, name: str, reason: str, tid: int = None):
        msg = f"""🔄 <b>强制汇报</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {escape_html(name[:30])}
📝 原因: {reason}"""
        self.send(msg, f"reannounce_{name[:10]}", 60)
    
    def cookie_invalid_notify(self):
        msg = """🔴 <b>Cookie 失效警告</b>
━━━━━━━━━━━━━━━━━━━━━
⚠️ U2 Cookie 已失效!

请尽快登录 U2 获取新的 Cookie
并更新配置文件中的 u2_cookie

━━━━━━━━━━━━━━━━━━━━━
🔧 更新后重启脚本生效"""
        self.send(msg, "cookie_invalid", 3600)
    
    def subscription_notify(self, name: str, size: int, source: str = ""):
        """订阅添加种子通知（简单版）"""
        msg = f"""📥 <b>订阅添加新种子</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {escape_html(name[:40])}
📦 大小: <code>{fmt_size(size)}</code>
🔗 来源: {source or 'RSS'}
⏱️ 时间: <code>{datetime.now().strftime('%H:%M:%S')}</code>"""
        self.send(msg, f"sub_{name[:10]}", 5)
    
    def subscription_notify_detailed(self, name: str, size: int, source: str = "",
                                      category: str = "", download_path: str = "",
                                      first_last_piece: bool = False, paused: bool = False):
        """订阅添加种子通知（详细版）"""
        if not self.enabled: return
        
        cat_info = f"📁 分类: <code>{escape_html(category)}</code>\n" if category else ""
        path_info = f"💾 路径: <code>{escape_html(download_path[:30])}</code>\n" if download_path else ""
        flp_info = "🎯 首尾块优先: ✅\n" if first_last_piece else ""
        pause_info = "⏸️ 暂停状态: 是\n" if paused else ""
        
        msg = f"""📥 <b>订阅添加新种子</b>
━━━━━━━━━━━━━━━━━━━━━
📛 <b>{escape_html(name[:45])}</b>

📊 <b>种子信息</b>
├ 📦 大小: <code>{fmt_size(size)}</code>
├ 🔗 来源: {source or 'RSS'}
{cat_info}{path_info}{flp_info}{pause_info}└ ⏱️ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"""
        self.send(msg, f"sub_{name[:10]}", 5)
    
    def cleanup_notify(self, name: str, reason: str, ratio: float, seeding_time: float):
        """删种通知（简单版）"""
        msg = f"""🗑️ <b>已删除种子</b>
━━━━━━━━━━━━━━━━━━━━━
📛 {escape_html(name[:40])}
📝 原因: {reason}
📊 分享率: <code>{ratio:.2f}</code>
⏱️ 做种时间: <code>{fmt_duration(seeding_time)}</code>"""
        self.send(msg, f"cleanup_{name[:10]}", 5)
    
    def cleanup_notify_detailed(self, name: str, reason: str, ratio: float, seeding_time: float,
                                 size: int = 0, uploaded: int = 0, downloaded: int = 0,
                                 delete_files: bool = False):
        """删种通知（详细版）"""
        if not self.enabled: return
        
        delete_mode = "🗃️ 删除文件: ✅ 已删除" if delete_files else "🗃️ 删除文件: ❌ 仅移除"
        
        msg = f"""🗑️ <b>已删除种子</b>
━━━━━━━━━━━━━━━━━━━━━
📛 <b>{escape_html(name[:45])}</b>

📊 <b>种子统计</b>
├ 📦 大小: <code>{fmt_size(size)}</code>
├ 📤 已上传: <code>{fmt_size(uploaded)}</code>
├ 📥 已下载: <code>{fmt_size(downloaded)}</code>
├ 📈 分享率: <code>{ratio:.2f}</code>
└ ⏱️ 做种: <code>{fmt_duration(seeding_time)}</code>

📝 <b>删除原因</b>
{reason}

{delete_mode}
⏱️ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"""
        self.send(msg, f"cleanup_{name[:10]}", 5)
    
    def shutdown_report(self):
        if not self.enabled: return
        msg = f"""🛑 <b>脚本已停止</b>
━━━━━━━━━━━━━━━━━━━━━
⏱️ 停止时间: <code>{datetime.now().strftime('%H:%M:%S')}</code>"""
        self.send(msg, "shutdown", 0)
        time.sleep(1)
