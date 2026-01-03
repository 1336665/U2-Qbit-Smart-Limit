#!/bin/bash
#
# qBit Smart Limit 管理脚本 v4.0.0
# 模块化重构版 - 支持订阅/删种功能
# https://github.com/1336665/U2-Qbit-Smart-Limit
#

SCRIPT_PATH="/usr/local/bin/qsl"
INSTALL_DIR="/opt/qbit-smart-limit"
CONFIG_FILE="${INSTALL_DIR}/config.json"
MAIN_PY="${INSTALL_DIR}/main.py"
QSL_DIR="${INSTALL_DIR}/qsl"
SERVICE_FILE="/etc/systemd/system/qbit-smart-limit.service"
GITHUB_RAW="https://raw.githubusercontent.com/1336665/U2-Qbit-Smart-Limit/main"
SCRIPT_VER="4.0.0"

# 颜色
R='\033[0;31m'
G='\033[0;32m'
Y='\033[1;33m'
B='\033[0;34m'
C='\033[0;36m'
W='\033[1;37m'
D='\033[0;90m'
N='\033[0m'

# ════════════════════════════════════════════════════════════
# 管道安装
# ════════════════════════════════════════════════════════════
if [[ ! -t 0 ]]; then
    echo ""
    echo -e "  ${C}安装管理脚本...${N}"
    cat > "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"
    echo -e "  ${G}✓${N} 已安装到 $SCRIPT_PATH"
    echo -e "  ${W}运行 qsl 打开菜单${N}"
    echo ""
    exit 0
fi

# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════
ok()   { echo -e "  ${G}✓${N} $1"; }
err()  { echo -e "  ${R}✗${N} $1"; }
warn() { echo -e "  ${Y}!${N} $1"; }
info() { echo -e "  ${C}i${N} $1"; }

is_installed() { [[ -f "$MAIN_PY" && -f "$CONFIG_FILE" ]]; }
is_running() { systemctl is-active --quiet qbit-smart-limit 2>/dev/null; }

get_local_ver() {
    # 模块化版本：版本号在 qsl/utils.py
    if [[ -f "$QSL_DIR/utils.py" ]]; then
        local v
        v=$(grep -oP 'VERSION = "\K[^"]+' "$QSL_DIR/utils.py" 2>/dev/null | head -1)
        [[ -n "$v" ]] && echo "$v" && return
    fi
    # 兼容旧版：版本号在 main.py
    [[ -f "$MAIN_PY" ]] && grep -oP 'VERSION = "\K[^"]+' "$MAIN_PY" 2>/dev/null | head -1 || echo "-"
}

get_remote_ver() {
    # 尝试多种方式获取远程版本
    local ver=""
    local proxy_cfg=""
    
    # 尝试从配置文件读取代理设置
    if [[ -f "$CONFIG_FILE" ]]; then
        proxy_cfg=$(jq -r '.proxy // ""' "$CONFIG_FILE" 2>/dev/null)
    fi
    
    # 方法1: 直接连接（优先取模块化版本 qsl/utils.py，其次兼容 main.py）
    ver=$(curl -sL --connect-timeout 10 --max-time 30 "${GITHUB_RAW}/qsl/utils.py" 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
    [[ -z "$ver" ]] && ver=$(curl -sL --connect-timeout 10 --max-time 30 "${GITHUB_RAW}/main.py" 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
    
    # 方法2: 如果配置了代理，尝试使用代理
    if [[ -z "$ver" && -n "$proxy_cfg" ]]; then
        ver=$(curl -sL --connect-timeout 10 --max-time 30 -x "$proxy_cfg" "${GITHUB_RAW}/qsl/utils.py" 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
        [[ -z "$ver" ]] && ver=$(curl -sL --connect-timeout 10 --max-time 30 -x "$proxy_cfg" "${GITHUB_RAW}/main.py" 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
    fi
    
    # 方法3: 使用 GitHub API (更可靠)
    if [[ -z "$ver" ]]; then
        ver=$(curl -sL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/1336665/U2-Qbit-Smart-Limit/contents/qsl/utils.py" 2>/dev/null | \
              jq -r '.content' 2>/dev/null | base64 -d 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
    fi
    if [[ -z "$ver" ]]; then
        ver=$(curl -sL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/1336665/U2-Qbit-Smart-Limit/contents/main.py" 2>/dev/null | \
              jq -r '.content' 2>/dev/null | base64 -d 2>/dev/null | grep -oP 'VERSION = "\K[^"]+' | head -1)
    fi
    
    echo "$ver"
}

# JSON 安全转义
json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    echo "$s"
}

# 清理 Cookie 前缀
clean_cookie() {
    local c="$1"
    c="${c#nexusphp_u2=}"
    c="${c#\"}"
    c="${c%\"}"
    c="${c#\'}"
    c="${c%\'}"
    c="${c## }"
    c="${c%% }"
    echo "$c"
}

# 获取配置值
get_config_val() {
    local key="$1" default="$2"
    if [[ -f "$CONFIG_FILE" ]]; then
        local val
        val=$(jq -r ".$key // \"$default\"" "$CONFIG_FILE" 2>/dev/null)
        [[ "$val" == "null" ]] && echo "$default" || echo "$val"
    else
        echo "$default"
    fi
}

# 获取布尔配置
get_config_bool() {
    local key="$1" default="$2"
    if [[ -f "$CONFIG_FILE" ]]; then
        local val
        val=$(jq -r ".$key" "$CONFIG_FILE" 2>/dev/null)
        if [[ "$val" == "true" ]]; then
            echo "true"
        elif [[ "$val" == "false" ]]; then
            echo "false"
        else
            echo "$default"
        fi
    else
        echo "$default"
    fi
}

# 获取嵌套配置值
get_nested_config() {
    local path="$1" default="$2"
    if [[ -f "$CONFIG_FILE" ]]; then
        local val
        val=$(jq -r "$path" "$CONFIG_FILE" 2>/dev/null)
        [[ "$val" == "null" || -z "$val" ]] && echo "$default" || echo "$val"
    else
        echo "$default"
    fi
}

# 设置布尔配置
set_config_bool() {
    local key="$1" value="$2"
    local tmp_cfg="/tmp/cfg_set_$$.json"
    
    if [[ "$value" == "true" ]]; then
        jq ".$key = true" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
    else
        jq ".$key = false" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
    fi
    
    if [[ -s "$tmp_cfg" ]] && jq empty "$tmp_cfg" 2>/dev/null; then
        mv "$tmp_cfg" "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
        return 0
    else
        rm -f "$tmp_cfg" 2>/dev/null
        return 1
    fi
}

# 设置嵌套配置
set_nested_config() {
    local path="$1" value="$2" is_number="${3:-false}"
    local tmp_cfg="/tmp/cfg_set_$$.json"
    
    if [[ "$is_number" == "true" ]]; then
        jq "$path = $value" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
    else
        jq "$path = \"$value\"" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
    fi
    
    if [[ -s "$tmp_cfg" ]] && jq empty "$tmp_cfg" 2>/dev/null; then
        mv "$tmp_cfg" "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
        return 0
    else
        rm -f "$tmp_cfg" 2>/dev/null
        return 1
    fi
}

# 初始化嵌套配置对象
init_nested_config() {
    local key="$1"
    local tmp_cfg="/tmp/cfg_init_$$.json"
    
    local exists
    exists=$(jq -r ".$key" "$CONFIG_FILE" 2>/dev/null)
    
    if [[ "$exists" == "null" ]]; then
        jq ".$key = {}" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
        if [[ -s "$tmp_cfg" ]] && jq empty "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"
            chmod 600 "$CONFIG_FILE"
        else
            rm -f "$tmp_cfg" 2>/dev/null
        fi
    fi
}

# 清理临时文件
cleanup() {
    rm -f /tmp/qsl_*.tmp /tmp/cfg_*.json 2>/dev/null
}
trap cleanup EXIT

# ════════════════════════════════════════════════════════════
# 界面
# ════════════════════════════════════════════════════════════
show_banner() {
    clear
    echo ""
    echo -e "${C}  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${N}"
    echo -e "${C}  ┃${N}  ${G} ██████  ${W}██████  ${C}██${N} ████████   ${G}███████${N} ${W}██${N}          ${C}┃${N}"
    echo -e "${C}  ┃${N}  ${G}██    ██ ${W}██   ██ ${C}██${N}    ██      ${G}██     ${N} ${W}██${N}          ${C}┃${N}"
    echo -e "${C}  ┃${N}  ${G}██    ██ ${W}██████  ${C}██${N}    ██      ${G}███████${N} ${W}██${N}          ${C}┃${N}"
    echo -e "${C}  ┃${N}  ${G}██ ▄▄ ██ ${W}██   ██ ${C}██${N}    ██           ${G}██${N} ${W}██${N}          ${C}┃${N}"
    echo -e "${C}  ┃${N}  ${G} ██████  ${W}██████  ${C}██${N}    ██      ${G}███████${N} ${W}███████${N}     ${C}┃${N}"
    echo -e "${C}  ┃${N}  ${G}    ▀▀${N}                                              ${C}┃${N}"
    echo -e "${C}  ┃${N}              ${Y}PT 上传速度精准控制器${N}                   ${C}┃${N}"
    echo -e "${C}  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${N}"
    echo ""
}

show_status() {
    local inst_st serv_st local_v u2_st dl_st ra_st sub_st clean_st
    
    # 安装状态
    if is_installed; then
        inst_st="${G}● 已安装${N}"
        local_v=$(get_local_ver)
    else
        inst_st="${Y}○ 未安装${N}"
        local_v="-"
    fi
    
    # 服务状态
    if is_running; then
        serv_st="${G}● 运行中${N}"
    else
        serv_st="${R}○ 已停止${N}"
    fi
    
    # U2 状态
    u2_st="${D}○ 未配置${N}"
    if [[ -f "$CONFIG_FILE" ]]; then
        local u2_cookie
        u2_cookie=$(jq -r '.u2_cookie // ""' "$CONFIG_FILE" 2>/dev/null)
        if [[ -n "$u2_cookie" && "$u2_cookie" != "null" ]]; then
            if python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
                u2_st="${G}● 已启用${N}"
            else
                u2_st="${Y}● 缺bs4${N}"
            fi
        fi
    fi
    
    # 下载限速状态
    dl_st="${D}○ 未配置${N}"
    if [[ -f "$CONFIG_FILE" ]]; then
        local dl_enabled
        dl_enabled=$(get_config_bool "enable_dl_limit" "true")
        if [[ "$dl_enabled" == "true" ]]; then
            dl_st="${G}● 已启用${N}"
        else
            dl_st="${R}○ 未启用${N}"
        fi
    fi
    
    # 汇报优化状态
    ra_st="${D}○ 未配置${N}"
    if [[ -f "$CONFIG_FILE" ]]; then
        local ra_enabled
        ra_enabled=$(get_config_bool "enable_reannounce_opt" "true")
        if [[ "$ra_enabled" == "true" ]]; then
            ra_st="${G}● 已启用${N}"
        else
            ra_st="${R}○ 未启用${N}"
        fi
    fi
    
    # 订阅模块状态
    sub_st="${D}○ 未配置${N}"
    if [[ -f "$CONFIG_FILE" ]]; then
        local sub_enabled
        sub_enabled=$(get_nested_config ".subscription.enabled" "false")
        if [[ "$sub_enabled" == "true" ]]; then
            sub_st="${G}● 已启用${N}"
        fi
    fi
    
    # 删种模块状态
    clean_st="${D}○ 未配置${N}"
    if [[ -f "$CONFIG_FILE" ]]; then
        local clean_enabled
        clean_enabled=$(get_nested_config ".cleanup.enabled" "false")
        if [[ "$clean_enabled" == "true" ]]; then
            clean_st="${G}● 已启用${N}"
        fi
    fi
    
    echo -e "  ${D}┌────────────────────────────────────────────────────────────────┐${N}"
    echo -e "  ${D}│${N}  ${W}安装状态${N}  $inst_st        ${W}服务状态${N}  $serv_st            ${D}│${N}"
    echo -e "  ${D}│${N}  ${W}程序版本${N}  ${C}${local_v}${N}              ${W}脚本版本${N}  ${D}v${SCRIPT_VER}${N}               ${D}│${N}"
    echo -e "  ${D}├────────────────────────────────────────────────────────────────┤${N}"
    echo -e "  ${D}│${N}  ${W}U2 辅助${N}   $u2_st        ${W}下载限速${N}  $dl_st            ${D}│${N}"
    echo -e "  ${D}│${N}  ${W}汇报优化${N}  $ra_st        ${W}Telegram${N}  ${D}见配置${N}               ${D}│${N}"
    echo -e "  ${D}├────────────────────────────────────────────────────────────────┤${N}"
    echo -e "  ${D}│${N}  ${W}RSS订阅${N}   $sub_st        ${W}自动删种${N}  $clean_st            ${D}│${N}"
    echo -e "  ${D}└────────────────────────────────────────────────────────────────┘${N}"
    echo ""
}

show_menu() {
    echo -e "  ${C}━━━━━━━━━━━━━━━━━━━━ 主菜单 ━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${G}1${N}. 全新安装              ${G}2${N}. 修改配置"
    echo -e "     ${G}3${N}. 查看状态              ${G}4${N}. 查看日志"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━━━ 服务管理 ━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${Y}5${N}. 启动服务              ${Y}6${N}. 停止服务"
    echo -e "     ${Y}7${N}. 重启服务"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━ 扩展模块 (新) ━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${B}a${N}. RSS 订阅管理          ${B}b${N}. 自动删种管理"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━━━━ 其他 ━━━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${G}8${N}. 检查更新              ${R}9${N}. 卸载程序"
    echo -e "     ${D}0${N}. 退出"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
}

# ════════════════════════════════════════════════════════════
# 依赖安装
# ════════════════════════════════════════════════════════════
install_deps() {
    echo ""
    info "安装系统依赖..."
    
    if command -v apt-get &>/dev/null; then
        apt-get update -qq &>/dev/null || true
        apt-get install -y python3 python3-pip jq curl -qq &>/dev/null || true
        apt-get install -y python3-requests python3-bs4 python3-lxml -qq &>/dev/null || true
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip jq curl -q &>/dev/null || true
    elif command -v dnf &>/dev/null; then
        dnf install -y python3 python3-pip jq curl -q &>/dev/null || true
    fi
    
    ok "系统依赖"
    
    info "安装 Python 依赖..."
    
    if ! python3 -c "import qbittorrentapi" &>/dev/null; then
        pip3 install --break-system-packages -q qbittorrent-api 2>/dev/null || \
        pip3 install -q qbittorrent-api 2>/dev/null || true
    fi
    
    if ! python3 -c "import requests" &>/dev/null; then
        pip3 install --break-system-packages -q requests 2>/dev/null || \
        pip3 install -q requests 2>/dev/null || true
    fi
    
    if ! python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
        pip3 install --break-system-packages -q beautifulsoup4 lxml 2>/dev/null || \
        pip3 install -q beautifulsoup4 lxml 2>/dev/null || true
    fi
    
    # feedparser 用于 RSS 订阅
    if ! python3 -c "import feedparser" &>/dev/null; then
        pip3 install --break-system-packages -q feedparser 2>/dev/null || \
        pip3 install -q feedparser 2>/dev/null || true
    fi
    
    if ! python3 -c "import qbittorrentapi" &>/dev/null; then
        err "qbittorrent-api 安装失败"
        echo -e "     ${D}手动: pip3 install --break-system-packages qbittorrent-api${N}"
        return 1
    fi
    ok "qbittorrent-api"
    
    if ! python3 -c "import requests" &>/dev/null; then
        err "requests 安装失败"
        return 1
    fi
    ok "requests"
    
    if python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
        ok "BeautifulSoup (U2辅助可用)"
    else
        warn "BeautifulSoup 未安装，U2辅助不可用"
        echo -e "     ${D}安装: apt install python3-bs4 python3-lxml${N}"
    fi
    
    if python3 -c "import feedparser" &>/dev/null; then
        ok "feedparser (RSS订阅可用)"
    else
        warn "feedparser 未安装，RSS订阅不可用"
        echo -e "     ${D}安装: pip3 install feedparser${N}"
    fi
    
    return 0
}

# ════════════════════════════════════════════════════════════
# 下载
# ════════════════════════════════════════════════════════════
download() {
    local url="$1" dest="$2" name="$3"
    local tmp="/tmp/qsl_dl_$$.tmp"
    local proxy_cfg=""
    
    # 尝试从配置文件读取代理设置
    if [[ -f "$CONFIG_FILE" ]]; then
        proxy_cfg=$(jq -r '.proxy // ""' "$CONFIG_FILE" 2>/dev/null)
    fi
    
    echo -ne "  ${C}↓${N} 下载 ${name}..."
    
    local http_code=""
    
    # 方法1: 直接连接
    http_code=$(curl -sL --connect-timeout 15 --max-time 60 -w "%{http_code}" "$url" -o "$tmp" 2>/dev/null)
    
    # 方法2: 如果配置了代理且直连失败，尝试使用代理
    if [[ "$http_code" != "200" && -n "$proxy_cfg" ]]; then
        http_code=$(curl -sL --connect-timeout 15 --max-time 60 -x "$proxy_cfg" -w "%{http_code}" "$url" -o "$tmp" 2>/dev/null)
    fi
    
    if [[ "$http_code" == "200" && -s "$tmp" ]]; then
        mv "$tmp" "$dest"
        chmod +x "$dest"
        echo -e "\r  ${G}✓${N} 下载 ${name}              "
        return 0
    fi
    
    rm -f "$tmp" 2>/dev/null
    echo -e "\r  ${R}✗${N} 下载 ${name} (HTTP ${http_code:-timeout})   "
    return 1
}

# ════════════════════════════════════════════════════════════
# 对 main.py 应用补丁
# ════════════════════════════════════════════════════════════
patch_main_py() {
    local file="$1"
    [[ -f "$file" ]] || { err "找不到 $file"; return 1; }

    python3 - "$file" <<'PY'
import re, sys

path = sys.argv[1]
s = open(path, 'r', encoding='utf-8', errors='replace').read()
orig = s
changed = False
warnings = []

# 1) Fix indentation: avoid IndentationError
if "\n        def save_torrent_state" in s:
    s = s.replace("\n        def save_torrent_state", "\n    def save_torrent_state")
    changed = True

# 2) TelegramBot: force chat_id to string (handles JSON int)
# 更宽松的匹配
s2 = re.sub(r'(\n\s*self\.chat_id\s*=\s*)chat_id(\s*\n)', r'\1str(chat_id).strip()\2', s)
if s2 != s:
    s = s2
    changed = True

# 检查是否有 TelegramBot 类
has_telegram = bool(re.search(r'class\s+TelegramBot', s))

# 3) Inject _html_safe helper if missing AND TelegramBot exists
if has_telegram and re.search(r'def\s+_html_safe\s*\(', s) is None:
    helper_lines = [
        "",
        "    def _html_safe(self, msg: str) -> str:",
        "        # Escape '<' that would be parsed as an unsupported Telegram HTML tag",
        "        if not msg or '<' not in msg:",
        "            return msg",
        "        import re as _re",
        "        return _re.sub(",
        r"            r'<(?!/?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a)(?:\s|>|$))',",
        "            '&lt;',",
        "            msg",
        "        )",
        "",
    ]
    helper = "\n".join(helper_lines)

    # 尝试多种插入位置
    insert_pos = None
    
    # 方法1: 在 close 方法后
    m = re.search(r'(\n\s*def\s+close\s*\(self\):\s*\n(?:\s+.*\n)*?\s*self\._stop\.set\(\)[^\n]*\n)', s)
    if m:
        insert_pos = m.end(1)
    
    # 方法2: 在 TelegramBot 类的任意方法前
    if insert_pos is None:
        m = re.search(r'(class\s+TelegramBot[^\n]*:\s*\n(?:\s*"""[^"]*"""\s*\n)?(?:\s*\n)*)', s)
        if m:
            insert_pos = m.end(1)
    
    # 方法3: 在 __init__ 方法后
    if insert_pos is None:
        m = re.search(r'(class\s+TelegramBot[^\n]*:\s*\n.*?def\s+__init__\s*\([^)]*\):[^\n]*\n(?:\s+[^\n]+\n)*)', s, re.DOTALL)
        if m:
            insert_pos = m.end(1)
    
    if insert_pos is not None:
        s = s[:insert_pos] + helper + s[insert_pos:]
        changed = True
    else:
        warnings.append("无法找到 TelegramBot 类的合适插入位置")

# 4) Ensure send payload uses _html_safe(msg) - only if _html_safe exists
if 'def _html_safe' in s:
    # 替换 "text": msg 为 "text": self._html_safe(msg)
    s2 = re.sub(r'("text"\s*:\s*)msg(\s*[,}])', r'\1self._html_safe(msg)\2', s)
    if s2 != s:
        s = s2
        changed = True

if s != orig:
    open(path, 'w', encoding='utf-8').write(s)

# 最终验证 - 更宽松的检查
s_check = open(path, 'r', encoding='utf-8', errors='replace').read()

# 缩进问题必须修复
if "\n        def save_torrent_state" in s_check:
    print("WARNING: 缩进问题未修复")
    sys.exit(1)

# TelegramBot 相关的检查 - 仅当存在该类时
if has_telegram:
    if 'def _html_safe' not in s_check:
        # 不强制退出，只是警告
        print("WARNING: _html_safe 未注入，TG 消息可能出现解析错误")
    if 'self._html_safe(msg)' not in s_check and '"text": msg' in s_check:
        print("WARNING: sendMessage 未使用 _html_safe")

# 打印警告
for w in warnings:
    print(f"WARNING: {w}")

# 成功退出
sys.exit(0)

PY

    local rc=$?
    if [[ $rc -ne 0 ]]; then
        err "补丁应用失败"
        return 1
    fi
    
    # 检查是否有警告输出
    local patch_output
    patch_output=$(python3 - "$file" <<'PY' 2>&1
import re, sys
s = open(sys.argv[1], 'r', encoding='utf-8', errors='replace').read()
has_tg = bool(re.search(r'class\s+TelegramBot', s))
has_safe = 'def _html_safe' in s
if has_tg:
    if has_safe:
        print("TG_OK")
    else:
        print("TG_WARN")
else:
    print("NO_TG")
PY
)
    
    case "$patch_output" in
        *TG_OK*)
            ok "main.py 已具备 TG HTML 安全发送能力"
            ;;
        *TG_WARN*)
            warn "TG 补丁部分应用，消息发送可能有问题"
            ;;
        *NO_TG*)
            ok "main.py 补丁完成 (无 TelegramBot 类)"
            ;;
        *)
            ok "main.py 补丁完成"
            ;;
    esac
    
    return 0
}

# ════════════════════════════════════════════════════════════
# 配置输入
# ════════════════════════════════════════════════════════════
get_input() {
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━ qBittorrent 设置 ━━━━━━━━━━━━━━━${N}"
    echo ""
    
    read -rp "  WebUI 地址 [http://127.0.0.1:8080]: " QB_HOST
    QB_HOST=${QB_HOST:-"http://127.0.0.1:8080"}
    
    read -rp "  用户名 [admin]: " QB_USER
    QB_USER=${QB_USER:-"admin"}
    
    read -rsp "  密码: " QB_PASS
    echo ""
    
    if [[ -z "$QB_PASS" ]]; then
        err "密码不能为空"
        return 1
    fi
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━ 速度设置 ━━━━━━━━━━━━━━━━━━━${N}"
    echo -e "  ${D}提示: U2 限制 50 MiB/s = 51200 KiB/s${N}"
    echo ""
    
    read -rp "  目标速度 KiB/s [51200]: " TARGET
    TARGET=${TARGET:-51200}
    
    read -rp "  安全系数 [0.98]: " SAFETY
    SAFETY=${SAFETY:-0.98}
    
    read -rp "  Tracker 关键词 [daydream]: " TRACKER
    TRACKER=${TRACKER:-"daydream"}
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━ 高级功能 (v10.8.0+) ━━━━━━━━━━━━${N}"
    echo -e "  ${D}下载限速: 防止快速完成导致的超速${N}"
    echo -e "  ${D}汇报优化: 优化汇报时间点，最大化上传${N}"
    echo ""
    
    read -rp "  启用下载限速? [Y/n]: " DL_LIMIT
    [[ "$DL_LIMIT" =~ ^[Nn] ]] && ENABLE_DL_LIMIT="false" || ENABLE_DL_LIMIT="true"
    
    read -rp "  启用汇报优化? [Y/n]: " RA_OPT
    [[ "$RA_OPT" =~ ^[Nn] ]] && ENABLE_RA_OPT="false" || ENABLE_RA_OPT="true"
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━ Telegram 通知 (可选) ━━━━━━━━━━━━${N}"
    echo -e "  ${D}直接回车跳过${N}"
    echo ""
    
    read -rp "  Bot Token: " TG_TOKEN
    TG_TOKEN=${TG_TOKEN:-""}
    
    TG_CHAT=""
    if [[ -n "$TG_TOKEN" ]]; then
        read -rp "  Chat ID: " TG_CHAT
    fi
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━ U2 Cookie (可选) ━━━━━━━━━━━━━━━${N}"
    echo -e "  ${D}获取: 登录U2 → F12 → Application → Cookies → nexusphp_u2${N}"
    echo -e "  ${Y}注意: 只需填写 Value 值，不需要带 nexusphp_u2= 前缀${N}"
    echo ""
    
    read -rp "  Cookie: " U2_COOKIE
    U2_COOKIE=$(clean_cookie "${U2_COOKIE:-""}")
    
    read -rp "  HTTP 代理 (可选): " PROXY
    PROXY=${PROXY:-""}
    
    return 0
}

# ════════════════════════════════════════════════════════════
# 保存配置
# ════════════════════════════════════════════════════════════
save_config() {
    mkdir -p "$INSTALL_DIR"
    
    local peer="false"
    [[ -n "$U2_COOKIE" ]] && peer="true"
    
    local esc_pass esc_token esc_chat esc_cookie esc_proxy
    esc_pass=$(json_escape "$QB_PASS")
    esc_token=$(json_escape "$TG_TOKEN")
    esc_chat=$(json_escape "$TG_CHAT")
    esc_cookie=$(json_escape "$U2_COOKIE")
    esc_proxy=$(json_escape "$PROXY")
    
    cat > "$CONFIG_FILE" << EOFCFG
{
  "host": "$QB_HOST",
  "username": "$QB_USER",
  "password": "$esc_pass",
  "target_speed_kib": $TARGET,
  "safety_margin": $SAFETY,
  "log_level": "INFO",
  "target_tracker_keyword": "$TRACKER",
  "exclude_tracker_keyword": "",
  "telegram_bot_token": "$esc_token",
  "telegram_chat_id": "$esc_chat",
  "max_physical_speed_kib": 0,
  "api_rate_limit": 20,
  "u2_cookie": "$esc_cookie",
  "proxy": "$esc_proxy",
  "peer_list_enabled": $peer,
  "enable_dl_limit": $ENABLE_DL_LIMIT,
  "enable_reannounce_opt": $ENABLE_RA_OPT,
  "subscription_enabled": false,
  "subscription_interval": 300,
  "subscription_rss_url": "",
  "subscription_download_path": "",
  "subscription_category": "",
  "subscription_paused": false,
  "subscription_first_last_piece": false,
  "cleanup_enabled": false,
  "cleanup_interval": 600,
  "cleanup_delete_files": false,
  "cleanup_tracker_keyword": "",
  "cleanup_reannounce_before_delete": true,
  "cleanup_reannounce_wait": 5,
  "cleanup_space_rule1_gb": 10,
  "cleanup_space_rule1_upload_kib": 1024,
  "cleanup_space_rule1_download_kib": 1024,
  "cleanup_space_rule2_gb": 20,
  "cleanup_space_rule2_upload_kib": 512,
  "cleanup_space_rule2_download_kib": 512,
  "cleanup_space_rule3_gb": 5,
  "cleanup_space_rule3_upload_kib": 5120,
  "cleanup_space_rule3_download_kib": 5120,
  "subscription": {
    "enabled": false,
    "interval_seconds": 300,
    "feeds": [],
    "save_path": "",
    "category": "",
    "paused": false,
    "first_last_piece": false
  },
  "cleanup": {
    "enabled": false,
    "interval_seconds": 600,
    "delete_files": false,
    "protected_categories": [],
    "protected_tags": ["keep", "protected"]
  }
}
EOFCFG
    chmod 600 "$CONFIG_FILE"
    
    if ! jq empty "$CONFIG_FILE" &>/dev/null; then
        err "配置文件格式错误"
        return 1
    fi
    
    return 0
}

# ════════════════════════════════════════════════════════════
# 创建服务
# ════════════════════════════════════════════════════════════
create_service() {
    cat > "$SERVICE_FILE" << EOFSVC
[Unit]
Description=qBit Smart Limit
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $MAIN_PY
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOFSVC
    systemctl daemon-reload
}

# ════════════════════════════════════════════════════════════
# 下载 qsl 模块
# ════════════════════════════════════════════════════════════
download_qsl_modules() {
    local qsl_dir="${INSTALL_DIR}/qsl"
    mkdir -p "$qsl_dir"
    
    # 模块文件列表
    local modules=("__init__.py" "utils.py" "config.py" "database.py" "telegram.py" "u2_helper.py" "core.py" "subscription.py" "cleanup.py")
    
    local success=0
    local failed=0
    
    for mod in "${modules[@]}"; do
        local url="${GITHUB_RAW}/qsl/${mod}"
        local dest="${qsl_dir}/${mod}"
        local tmp="/tmp/qsl_mod_$$.tmp"
        
        echo -ne "  ${C}↓${N} 下载 qsl/${mod}..."
        
        local http_code
        http_code=$(curl -sL --connect-timeout 10 -w "%{http_code}" "$url" -o "$tmp" 2>/dev/null)
        
        if [[ "$http_code" == "200" && -s "$tmp" ]]; then
            mv "$tmp" "$dest"
            echo -e "\r  ${G}✓${N} 下载 qsl/${mod}              "
            ((success++))
        else
            rm -f "$tmp" 2>/dev/null
            echo -e "\r  ${R}✗${N} 下载 qsl/${mod} (HTTP $http_code)   "
            ((failed++))
        fi
    done
    
    echo ""
    if [[ $success -eq ${#modules[@]} ]]; then
        ok "qsl 模块下载完成 ($success/${#modules[@]})"
        return 0
    elif [[ $success -gt 0 ]]; then
        warn "qsl 模块部分下载 ($success/${#modules[@]})"
        return 0
    else
        err "qsl 模块下载失败"
        echo ""
        echo -e "  ${Y}GitHub 仓库可能未包含 qsl/ 目录${N}"
        echo -e "  ${D}请检查: ${GITHUB_RAW}/qsl/${N}"
        echo ""
        echo -e "  ${W}解决方案:${N}"
        echo -e "  1. 将 qsl/ 目录上传到 GitHub 仓库"
        echo -e "  2. 或使用单文件版本的 main.py"
        echo ""
        return 1
    fi
}

# 检查 main.py 是否需要 qsl 模块
check_needs_qsl() {
    local file="$1"
    [[ -f "$file" ]] && grep -q "from qsl import\|import qsl" "$file"
}

# ════════════════════════════════════════════════════════════
# 一键交互安装（快速安装）
# ════════════════════════════════════════════════════════════
do_quick_install() {
    show_banner
    echo -e "  ${W}>>> 一键交互安装 qBit Smart Limit <<<${N}"
    echo ""
    echo -e "  ${C}这个向导将引导你完成所有必要的配置${N}"
    echo -e "  ${D}直接回车使用 [默认值]${N}"
    echo ""
    
    # 检查网络
    echo -ne "  ${C}○${N} 检查网络连接..."
    if ! curl -sL --connect-timeout 10 "${GITHUB_RAW}/main.py" &>/dev/null; then
        echo -e "\r  ${R}✗${N} 无法连接 GitHub         "
        echo ""
        echo -e "  ${D}可能原因: 网络问题 / 需要代理 / GitHub 被墙${N}"
        echo -e "  ${Y}尝试: export https_proxy=http://127.0.0.1:7890${N}"
        return 1
    fi
    echo -e "\r  ${G}✓${N} 网络连接正常            "
    
    # 安装依赖
    install_deps || return 1
    
    # 创建目录
    mkdir -p "$INSTALL_DIR"
    
    # 下载文件
    download "${GITHUB_RAW}/main.py" "$MAIN_PY" "main.py" || return 1
    
    # 检查是否需要下载 qsl 模块
    if check_needs_qsl "$MAIN_PY"; then
        echo ""
        info "检测到模块化版本，下载 qsl 模块..."
        echo ""
        download_qsl_modules || {
            echo ""
            read -rp "  是否继续安装? (模块缺失可能导致运行失败) [y/N]: " cont
            [[ ! "$cont" =~ ^[Yy] ]] && return 1
        }
    fi
    
    patch_main_py "$MAIN_PY" || return 1
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━ 开始配置 ━━━━━━━━━━━━━━━━${N}"
    
    # qBittorrent 配置
    echo ""
    echo -e "  ${W}【1/5】 qBittorrent 连接配置${N}"
    echo ""
    read -rp "  WebUI 地址 [http://127.0.0.1:8080]: " QB_HOST
    QB_HOST=${QB_HOST:-"http://127.0.0.1:8080"}
    
    read -rp "  用户名 [admin]: " QB_USER
    QB_USER=${QB_USER:-"admin"}
    
    read -rsp "  密码 (必填): " QB_PASS
    echo ""
    
    if [[ -z "$QB_PASS" ]]; then
        err "密码不能为空"
        return 1
    fi
    
    # 速度配置
    echo ""
    echo -e "  ${W}【2/5】 速度控制配置${N}"
    echo -e "  ${D}提示: U2 限制 50 MiB/s = 51200 KiB/s${N}"
    echo ""
    
    read -rp "  目标速度 KiB/s [51200]: " TARGET
    TARGET=${TARGET:-51200}
    
    read -rp "  安全系数 [0.98]: " SAFETY
    SAFETY=${SAFETY:-0.98}
    
    read -rp "  Tracker 关键词 [daydream]: " TRACKER
    TRACKER=${TRACKER:-"daydream"}
    
    # 高级功能
    echo ""
    echo -e "  ${W}【3/5】 高级功能配置${N}"
    echo ""
    
    read -rp "  启用下载限速? [Y/n]: " DL_LIMIT
    [[ "$DL_LIMIT" =~ ^[Nn] ]] && ENABLE_DL_LIMIT="false" || ENABLE_DL_LIMIT="true"
    
    read -rp "  启用汇报优化? [Y/n]: " RA_OPT
    [[ "$RA_OPT" =~ ^[Nn] ]] && ENABLE_RA_OPT="false" || ENABLE_RA_OPT="true"
    
    # Telegram 配置
    echo ""
    echo -e "  ${W}【4/5】 Telegram 通知配置${N}"
    echo -e "  ${D}直接回车跳过${N}"
    echo ""
    
    read -rp "  Bot Token: " TG_TOKEN
    TG_TOKEN=${TG_TOKEN:-""}
    
    TG_CHAT=""
    if [[ -n "$TG_TOKEN" ]]; then
        read -rp "  Chat ID: " TG_CHAT
    fi
    
    # U2 Cookie 配置
    echo ""
    echo -e "  ${W}【5/5】 U2 Cookie 配置${N}"
    echo -e "  ${D}获取: 登录U2 → F12 → Application → Cookies → nexusphp_u2${N}"
    echo -e "  ${Y}注意: 只需填写 Value 值，不需要带前缀${N}"
    echo ""
    
    read -rp "  Cookie (可选): " U2_COOKIE
    U2_COOKIE=$(clean_cookie "${U2_COOKIE:-""}")
    
    read -rp "  HTTP 代理 (可选): " PROXY
    PROXY=${PROXY:-""}
    
    # 确认配置
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━ 配置确认 ━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "  qBittorrent: ${G}$QB_HOST${N}"
    echo -e "  目标速度:    ${G}$TARGET KiB/s × $SAFETY${N}"
    echo -e "  Tracker:     ${G}$TRACKER${N}"
    [[ "$ENABLE_DL_LIMIT" == "true" ]] && echo -e "  下载限速:    ${G}已启用${N}" || echo -e "  下载限速:    ${Y}已禁用${N}"
    [[ "$ENABLE_RA_OPT" == "true" ]] && echo -e "  汇报优化:    ${G}已启用${N}" || echo -e "  汇报优化:    ${Y}已禁用${N}"
    [[ -n "$TG_TOKEN" ]] && echo -e "  Telegram:    ${G}已配置${N}" || echo -e "  Telegram:    ${D}未配置${N}"
    [[ -n "$U2_COOKIE" ]] && echo -e "  U2 Cookie:   ${G}已配置 (${#U2_COOKIE}字符)${N}" || echo -e "  U2 Cookie:   ${D}未配置${N}"
    echo ""
    
    read -rp "  确认安装? [Y/n]: " confirm
    [[ "$confirm" =~ ^[Nn] ]] && return 0
    
    echo ""
    if ! save_config; then
        err "保存配置失败"
        return 1
    fi
    ok "配置文件已保存"
    
    create_service && ok "系统服务已创建"
    download "${GITHUB_RAW}/install.sh" "$SCRIPT_PATH" "管理脚本" || true
    
    systemctl enable qbit-smart-limit &>/dev/null || true
    systemctl start qbit-smart-limit && ok "服务已启动"
    
    echo ""
    echo -e "  ${G}╔════════════════════════════════════════════════════╗${N}"
    echo -e "  ${G}║              ✓ 一键安装完成!                       ║${N}"
    echo -e "  ${G}║         运行 ${W}qsl${G} 随时打开管理菜单                  ║${N}"
    echo -e "  ${G}╚════════════════════════════════════════════════════╝${N}"
    echo ""
    
    # 验证安装
    sleep 2
    if journalctl -u qbit-smart-limit -n 30 --no-pager 2>/dev/null | grep -q "已连接"; then
        ok "已成功连接到 qBittorrent"
    else
        warn "请检查日志确认连接状态: journalctl -u qbit-smart-limit -f"
    fi
    
    if [[ -n "$U2_COOKIE" ]]; then
        if journalctl -u qbit-smart-limit -n 30 --no-pager 2>/dev/null | grep -q "U2.*已启用"; then
            ok "U2 辅助功能已启用"
        elif ! python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
            warn "U2 Cookie 已配置，但 BeautifulSoup 未安装"
        fi
    fi
}

# ════════════════════════════════════════════════════════════
# 安装
# ════════════════════════════════════════════════════════════
do_install() {
    show_banner
    echo -e "  ${W}>>> 安装 qBit Smart Limit <<<${N}"
    echo ""
    
    echo -ne "  ${C}○${N} 检查网络连接..."
    if ! curl -sL --connect-timeout 10 "${GITHUB_RAW}/main.py" &>/dev/null; then
        echo -e "\r  ${R}✗${N} 无法连接 GitHub         "
        echo ""
        echo -e "  ${D}可能原因: 网络问题 / 需要代理 / GitHub 被墙${N}"
        echo -e "  ${Y}尝试: export https_proxy=http://127.0.0.1:7890${N}"
        return 1
    fi
    echo -e "\r  ${G}✓${N} 网络连接正常            "
    
    install_deps || return 1
    
    mkdir -p "$INSTALL_DIR"
    download "${GITHUB_RAW}/main.py" "$MAIN_PY" "main.py" || return 1
    
    # 检查是否需要下载 qsl 模块
    if check_needs_qsl "$MAIN_PY"; then
        echo ""
        info "检测到模块化版本，下载 qsl 模块..."
        echo ""
        if ! download_qsl_modules; then
            echo ""
            read -rp "  是否继续安装? (模块缺失可能导致运行失败) [y/N]: " cont
            [[ ! "$cont" =~ ^[Yy] ]] && return 1
        fi
    fi
    
    patch_main_py "$MAIN_PY" || return 1
    
    get_input || return 1
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━━ 配置确认 ━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "  qBittorrent: ${G}$QB_HOST${N}"
    echo -e "  目标速度:    ${G}$TARGET KiB/s × $SAFETY${N}"
    [[ "$ENABLE_DL_LIMIT" == "true" ]] && echo -e "  下载限速:    ${G}已启用${N}" || echo -e "  下载限速:    ${Y}已禁用${N}"
    [[ "$ENABLE_RA_OPT" == "true" ]] && echo -e "  汇报优化:    ${G}已启用${N}" || echo -e "  汇报优化:    ${Y}已禁用${N}"
    [[ -n "$TG_TOKEN" ]] && echo -e "  Telegram:    ${G}已配置${N}" || echo -e "  Telegram:    ${D}未配置${N}"
    [[ -n "$U2_COOKIE" ]] && echo -e "  U2 Cookie:   ${G}已配置 (${#U2_COOKIE}字符)${N}" || echo -e "  U2 Cookie:   ${D}未配置${N}"
    echo ""
    
    read -rp "  确认安装? [Y/n]: " confirm
    [[ "$confirm" =~ ^[Nn] ]] && return 0
    
    echo ""
    if ! save_config; then
        err "保存配置失败"
        return 1
    fi
    ok "配置文件已保存"
    
    create_service && ok "系统服务已创建"
    download "${GITHUB_RAW}/install.sh" "$SCRIPT_PATH" "管理脚本" || true
    
    systemctl enable qbit-smart-limit &>/dev/null || true
    systemctl start qbit-smart-limit && ok "服务已启动"
    
    echo ""
    echo -e "  ${G}╔════════════════════════════════════════════════════╗${N}"
    echo -e "  ${G}║              ✓ 安装完成!                           ║${N}"
    echo -e "  ${G}║         运行 ${W}qsl${G} 随时打开管理菜单                  ║${N}"
    echo -e "  ${G}╚════════════════════════════════════════════════════╝${N}"
    echo ""
    
    sleep 2
    if journalctl -u qbit-smart-limit -n 30 --no-pager 2>/dev/null | grep -q "U2.*已启用"; then
        ok "U2 辅助功能已启用"
    elif [[ -n "$U2_COOKIE" ]]; then
        if ! python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
            warn "U2 Cookie 已配置，但 BeautifulSoup 未安装"
        fi
    fi
}

# ════════════════════════════════════════════════════════════
# 更新管理脚本
# ════════════════════════════════════════════════════════════
update_script() {
    local tmp="/tmp/qsl_update_$$.tmp"
    local proxy_cfg=""
    
    echo -ne "  ${C}↓${N} 更新 install.sh..."
    
    # 尝试从配置文件读取代理设置
    if [[ -f "$CONFIG_FILE" ]]; then
        proxy_cfg=$(jq -r '.proxy // ""' "$CONFIG_FILE" 2>/dev/null)
    fi
    
    local http_code=""
    
    # 方法1: 直接连接
    http_code=$(curl -sL --connect-timeout 10 --max-time 60 -w "%{http_code}" "${GITHUB_RAW}/install.sh" -o "$tmp" 2>/dev/null)
    
    # 方法2: 如果配置了代理且直连失败，尝试使用代理
    if [[ "$http_code" != "200" && -n "$proxy_cfg" ]]; then
        http_code=$(curl -sL --connect-timeout 10 --max-time 60 -x "$proxy_cfg" -w "%{http_code}" "${GITHUB_RAW}/install.sh" -o "$tmp" 2>/dev/null)
    fi
    
    if [[ "$http_code" == "200" && -s "$tmp" ]]; then
        mv "$tmp" "$SCRIPT_PATH"
        chmod +x "$SCRIPT_PATH"
        echo -e "\r  ${G}✓${N} 更新 install.sh         "
        return 0
    fi
    
    rm -f "$tmp" 2>/dev/null
    echo -e "\r  ${R}✗${N} 更新 install.sh (HTTP: ${http_code:-timeout})    "
    return 1
}

# ════════════════════════════════════════════════════════════
# 更新
# ════════════════════════════════════════════════════════════
do_update() {
    echo ""
    echo -e "  ${W}>>> 检查更新 <<<${N}"
    echo ""
    
    echo -ne "  ${C}○${N} 获取版本信息..."
    
    local local_v remote_v
    local_v=$(get_local_ver)
    remote_v=$(get_remote_ver)
    
    if [[ -z "$remote_v" ]]; then
        echo -e "\r  ${R}✗${N} 无法连接 GitHub         "
        echo ""
        echo -e "  ${D}可能的原因:${N}"
        echo -e "  ${D}1. 网络连接问题${N}"
        echo -e "  ${D}2. GitHub 被防火墙阻止${N}"
        echo -e "  ${D}3. DNS 解析失败${N}"
        echo ""
        echo -e "  ${D}尝试诊断...${N}"
        
        # 测试网络连接
        if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
            echo -e "  ${G}✓${N} 网络连接正常"
        else
            echo -e "  ${R}✗${N} 网络连接失败"
        fi
        
        if ping -c 1 -W 3 github.com &>/dev/null; then
            echo -e "  ${G}✓${N} GitHub DNS解析正常"
        else
            echo -e "  ${R}✗${N} 无法解析 github.com"
        fi
        
        # 检查代理配置
        local proxy_cfg=""
        if [[ -f "$CONFIG_FILE" ]]; then
            proxy_cfg=$(jq -r '.proxy // ""' "$CONFIG_FILE" 2>/dev/null)
        fi
        
        if [[ -n "$proxy_cfg" ]]; then
            echo -e "  ${C}i${N} 已配置代理: ${proxy_cfg}"
            echo -e "  ${D}  已尝试使用代理但仍失败${N}"
        else
            echo -e "  ${Y}!${N} 未配置代理"
            echo -e "  ${D}  如需使用代理，请在主配置中设置 proxy${N}"
        fi
        
        echo ""
        return 1
    fi
    
    echo -e "\r  ${G}✓${N} 获取版本信息            "
    echo ""
    echo -e "  ${D}┌─────────────────────────────────────────────┐${N}"
    echo -e "  ${D}│${N}  ${W}main.py${N}    本地: ${W}${local_v:-无}${N}  远程: ${C}${remote_v}${N}"
    
    if [[ "$local_v" != "$remote_v" && "$local_v" != "-" ]]; then
        echo -e "  ${D}│${N}             ${Y}→ 有新版本可用${N}"
    else
        echo -e "  ${D}│${N}             ${G}✓ 已是最新${N}"
    fi
    
    echo -e "  ${D}│${N}  ${W}install.sh${N} 将同步到最新"
    echo -e "  ${D}└─────────────────────────────────────────────┘${N}"
    echo ""
    
    echo -e "  ${C}━━━━━━━━━━━━━━━ 更新选项 ━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${G}1${N}. 更新全部 (main.py + install.sh)"
    echo -e "     ${G}2${N}. 仅更新 install.sh"
    echo -e "     ${G}3${N}. 强制重新下载 main.py"
    echo -e "     ${D}0${N}. 返回"
    echo ""
    
    read -rp "  选择 [0-3]: " choice
    
    case "$choice" in
        1)
            echo ""
            [[ -f "$MAIN_PY" ]] && cp "$MAIN_PY" "${MAIN_PY}.bak"
            
            if download "${GITHUB_RAW}/main.py" "$MAIN_PY" "main.py"; then
                # 检查是否需要下载 qsl 模块
                if check_needs_qsl "$MAIN_PY"; then
                    info "检测到模块化版本，更新 qsl 模块..."
                    download_qsl_modules || warn "部分模块下载失败"
                fi
                patch_main_py "$MAIN_PY" || true
                migrate_config
                systemctl restart qbit-smart-limit 2>/dev/null && ok "服务已重启"
            else
                [[ -f "${MAIN_PY}.bak" ]] && mv "${MAIN_PY}.bak" "$MAIN_PY"
                err "main.py 更新失败"
            fi
            
            if update_script; then
                echo ""
                info "请重新运行 qsl"
                exit 0
            fi
            ;;
        2)
            echo ""
            if update_script; then
                echo ""
                info "请重新运行 qsl"
                exit 0
            fi
            ;;
        3)
            echo ""
            [[ -f "$MAIN_PY" ]] && cp "$MAIN_PY" "${MAIN_PY}.bak"
            
            if download "${GITHUB_RAW}/main.py" "$MAIN_PY" "main.py (强制)"; then
                local new_v
                new_v=$(get_local_ver)
                ok "已更新到 v${new_v}"
                # 检查是否需要下载 qsl 模块
                if check_needs_qsl "$MAIN_PY"; then
                    info "检测到模块化版本，更新 qsl 模块..."
                    download_qsl_modules || warn "部分模块下载失败"
                fi
                patch_main_py "$MAIN_PY" || true
                migrate_config
                systemctl restart qbit-smart-limit 2>/dev/null && ok "服务已重启"
            else
                [[ -f "${MAIN_PY}.bak" ]] && mv "${MAIN_PY}.bak" "$MAIN_PY"
                err "下载失败"
            fi
            ;;
        *)
            return
            ;;
    esac
}

# ════════════════════════════════════════════════════════════
# 配置迁移
# ════════════════════════════════════════════════════════════
migrate_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        return
    fi
    
    local changed=0
    local tmp_cfg="/tmp/cfg_migrate_$$.json"
    
    # 检查是否缺少 enable_dl_limit
    local dl_val
    dl_val=$(jq -r '.enable_dl_limit' "$CONFIG_FILE" 2>/dev/null)
    if [[ "$dl_val" == "null" ]]; then
        info "添加新配置项: enable_dl_limit"
        if set_config_bool "enable_dl_limit" "true"; then
            changed=1
        fi
    fi
    
    # 检查是否缺少 enable_reannounce_opt
    local ra_val
    ra_val=$(jq -r '.enable_reannounce_opt' "$CONFIG_FILE" 2>/dev/null)
    if [[ "$ra_val" == "null" ]]; then
        info "添加新配置项: enable_reannounce_opt"
        if set_config_bool "enable_reannounce_opt" "true"; then
            changed=1
        fi
    fi
    
    # 检查是否缺少 subscription 配置
    local sub_val
    sub_val=$(jq -r '.subscription' "$CONFIG_FILE" 2>/dev/null)
    if [[ "$sub_val" == "null" ]]; then
        info "添加新配置项: subscription"
        jq '.subscription = {"enabled": false, "interval_seconds": 300, "feeds": [], "save_path": "", "category": "", "paused": false}' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
        if [[ -s "$tmp_cfg" ]] && jq empty "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"
            chmod 600 "$CONFIG_FILE"
            changed=1
        else
            rm -f "$tmp_cfg" 2>/dev/null
        fi
    fi
    
    # 检查是否缺少 cleanup 配置
    local clean_val
    clean_val=$(jq -r '.cleanup' "$CONFIG_FILE" 2>/dev/null)
    if [[ "$clean_val" == "null" ]]; then
        info "添加新配置项: cleanup"
        jq '.cleanup = {"enabled": false, "interval_seconds": 600, "delete_files": false, "protected_categories": [], "protected_tags": ["keep", "protected"]}' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null
        if [[ -s "$tmp_cfg" ]] && jq empty "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"
            chmod 600 "$CONFIG_FILE"
            changed=1
        else
            rm -f "$tmp_cfg" 2>/dev/null
        fi
    fi

    # 删种空间规则：补齐下载阈值（旧版本只有 upload）
    local r1d r2d r3d
    r1d=$(jq -r '.cleanup_space_rule1_download_kib' "$CONFIG_FILE" 2>/dev/null)
    r2d=$(jq -r '.cleanup_space_rule2_download_kib' "$CONFIG_FILE" 2>/dev/null)
    r3d=$(jq -r '.cleanup_space_rule3_download_kib' "$CONFIG_FILE" 2>/dev/null)

    if [[ "$r1d" == "null" ]]; then
        info "添加新配置项: cleanup_space_rule1_download_kib"
        if jq '.cleanup_space_rule1_download_kib = 1024' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"; changed=1
        fi
    fi

    if [[ "$r2d" == "null" ]]; then
        info "添加新配置项: cleanup_space_rule2_download_kib"
        if jq '.cleanup_space_rule2_download_kib = 512' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"; changed=1
        fi
    fi

    if [[ "$r3d" == "null" ]]; then
        info "添加新配置项: cleanup_space_rule3_download_kib"
        if jq '.cleanup_space_rule3_download_kib = 5120' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
            mv "$tmp_cfg" "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"; changed=1
        fi
    fi
    
    if [[ $changed -eq 1 ]]; then
        ok "配置已迁移到新版本"
    fi
}

# ════════════════════════════════════════════════════════════
# 配置管理
# ════════════════════════════════════════════════════════════
do_config() {
    if ! is_installed; then
        err "请先安装"
        return
    fi
    
    # 先尝试迁移配置
    migrate_config
    
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━━━ 配置管理 ━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    
    # 显示当前状态
    local dl_status ra_status dl_text ra_text
    dl_status=$(get_config_bool "enable_dl_limit" "true")
    ra_status=$(get_config_bool "enable_reannounce_opt" "true")
    
    if [[ "$dl_status" == "true" ]]; then
        dl_text="${G}已启用${N}"
    else
        dl_text="${R}未启用${N}"
    fi
    
    if [[ "$ra_status" == "true" ]]; then
        ra_text="${G}已启用${N}"
    else
        ra_text="${R}未启用${N}"
    fi
    
    echo -e "  ${D}当前状态: 下载限速 $dl_text | 汇报优化 $ra_text${N}"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━ 基础配置 ━━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${G}1${N}. 编辑配置文件"
    echo -e "     ${G}2${N}. 修改 qB 密码"
    echo -e "     ${G}3${N}. 修改目标速度"
    echo -e "     ${G}4${N}. 配置 Telegram"
    echo -e "     ${G}5${N}. 配置 U2 Cookie"
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━━ 高级功能 ━━━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    echo -e "     ${G}6${N}. 开关下载限速          当前: $dl_text"
    echo -e "     ${G}7${N}. 开关汇报优化          当前: $ra_text"
    echo ""
    echo -e "     ${D}0${N}. 返回"
    echo ""
    
    read -rp "  选择: " choice
    
    local tmp_cfg="/tmp/cfg_$$.json"
    
    case "$choice" in
        1)
            local editor="${EDITOR:-nano}"
            if ! command -v "$editor" &>/dev/null; then
                editor="vi"
            fi
            $editor "$CONFIG_FILE"
            ;;
        2)
            read -rsp "  新密码: " p
            echo ""
            if [[ -n "$p" ]]; then
                local esc_p
                esc_p=$(json_escape "$p")
                if jq --arg v "$esc_p" '.password = $v' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE"
                    ok "密码已更新"
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
            fi
            ;;
        3)
            read -rp "  新速度 (KiB/s): " s
            if [[ -n "$s" && "$s" =~ ^[0-9]+$ ]]; then
                if jq ".target_speed_kib = $s" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    ok "速度已更新: $s KiB/s"
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
            else
                err "请输入有效数字"
            fi
            ;;
        4)
            read -rp "  Bot Token: " t
            read -rp "  Chat ID: " i
            local esc_t esc_i
            esc_t=$(json_escape "$t")
            esc_i=$(json_escape "$i")
            if jq --arg t "$esc_t" --arg i "$esc_i" '.telegram_bot_token = $t | .telegram_chat_id = $i' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                mv "$tmp_cfg" "$CONFIG_FILE"
                ok "Telegram 已更新"
            else
                rm -f "$tmp_cfg"
                err "更新失败"
            fi
            ;;
        5)
            echo ""
            echo -e "  ${D}获取: F12 → Application → Cookies → nexusphp_u2${N}"
            echo -e "  ${Y}注意: 只需填写 Value 值，不需要带 nexusphp_u2= 前缀${N}"
            echo ""
            read -rp "  Cookie (留空禁用): " u
            
            u=$(clean_cookie "$u")
            
            local esc_u
            esc_u=$(json_escape "$u")
            if [[ -n "$u" ]]; then
                if jq --arg v "$esc_u" '.u2_cookie = $v | .peer_list_enabled = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    ok "U2 Cookie 已配置 (${#u}字符)"
                    if python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
                        info "U2 辅助功能将启用"
                    else
                        warn "BeautifulSoup 未安装，U2辅助不可用"
                        echo -e "     ${D}安装: apt install python3-bs4 python3-lxml${N}"
                    fi
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
            else
                if jq '.u2_cookie = "" | .peer_list_enabled = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    warn "U2 Cookie 已禁用"
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
            fi
            ;;
        6)
            # 开关下载限速
            echo ""
            echo -e "  ${C}━━━━━━━━━━━━━ 下载限速功能 ━━━━━━━━━━━━━━━━━${N}"
            echo ""
            echo -e "  ${W}功能说明:${N}"
            echo -e "  当周期内上传均值超过 50 MiB/s 且种子即将完成时，"
            echo -e "  自动限制下载速度以延长完成时间，避免完成汇报时超速。"
            echo ""
            echo -e "  ${D}来源: U2脚本的核心防超速逻辑${N}"
            echo ""
            
            local current_dl
            current_dl=$(get_config_bool "enable_dl_limit" "true")
            
            if [[ "$current_dl" == "true" ]]; then
                echo -e "  当前状态: ${G}已启用${N}"
                echo ""
                read -rp "  确认禁用下载限速? [y/N]: " confirm
                if [[ "$confirm" =~ ^[Yy] ]]; then
                    if set_config_bool "enable_dl_limit" "false"; then
                        warn "下载限速已禁用"
                    else
                        err "更新失败"
                    fi
                else
                    info "已取消"
                fi
            else
                echo -e "  当前状态: ${R}未启用${N}"
                echo ""
                read -rp "  确认启用下载限速? [Y/n]: " confirm
                if [[ ! "$confirm" =~ ^[Nn] ]]; then
                    if set_config_bool "enable_dl_limit" "true"; then
                        ok "下载限速已启用"
                    else
                        err "更新失败"
                    fi
                else
                    info "已取消"
                fi
            fi
            ;;
        7)
            # 开关汇报优化
            echo ""
            echo -e "  ${C}━━━━━━━━━━━━━ 汇报优化功能 ━━━━━━━━━━━━━━━━━${N}"
            echo ""
            echo -e "  ${W}功能说明:${N}"
            echo -e "  优化完成前最后一次汇报的时间点，在合适时机强制重新汇报，"
            echo -e "  最大化上传量的同时避免超速。"
            echo ""
            echo -e "  ${W}触发条件:${N}"
            echo -e "  • 5分钟平均上传速度 > 50 MiB/s"
            echo -e "  • 5分钟平均下载速度 > 0"
            echo -e "  • 距离上次强制汇报 > 15分钟"
            echo ""
            echo -e "  ${D}来源: U2脚本的汇报时间优化逻辑${N}"
            echo ""
            
            local current_ra
            current_ra=$(get_config_bool "enable_reannounce_opt" "true")
            
            if [[ "$current_ra" == "true" ]]; then
                echo -e "  当前状态: ${G}已启用${N}"
                echo ""
                read -rp "  确认禁用汇报优化? [y/N]: " confirm
                if [[ "$confirm" =~ ^[Yy] ]]; then
                    if set_config_bool "enable_reannounce_opt" "false"; then
                        warn "汇报优化已禁用"
                    else
                        err "更新失败"
                    fi
                else
                    info "已取消"
                fi
            else
                echo -e "  当前状态: ${R}未启用${N}"
                echo ""
                read -rp "  确认启用汇报优化? [Y/n]: " confirm
                if [[ ! "$confirm" =~ ^[Nn] ]]; then
                    if set_config_bool "enable_reannounce_opt" "true"; then
                        ok "汇报优化已启用"
                    else
                        err "更新失败"
                    fi
                else
                    info "已取消"
                fi
            fi
            ;;
        *)
            return
            ;;
    esac
    
    if [[ "$choice" =~ ^[1-7]$ ]]; then
        echo ""
        read -rp "  重启服务使配置生效? [Y/n]: " r
        if [[ ! "$r" =~ ^[Nn] ]]; then
            systemctl restart qbit-smart-limit && ok "服务已重启"
        fi
    fi
}

# ════════════════════════════════════════════════════════════
# RSS 订阅管理
# ════════════════════════════════════════════════════════════
do_subscription() {
    if ! is_installed; then
        err "请先安装"
        return
    fi
    
    # 确保配置存在
    migrate_config
    
    while true; do
        echo ""
        echo -e "  ${C}━━━━━━━━━━━━━━ RSS 订阅管理 ━━━━━━━━━━━━━━━━${N}"
        echo ""
        
        # 当前状态
        local sub_enabled sub_interval feed_count save_path category
        sub_enabled=$(get_nested_config ".subscription.enabled" "false")
        sub_interval=$(get_nested_config ".subscription.interval_seconds" "300")
        feed_count=$(jq -r '.subscription.feeds | length' "$CONFIG_FILE" 2>/dev/null || echo "0")
        save_path=$(get_nested_config ".subscription.save_path" "")
        category=$(get_nested_config ".subscription.category" "")
        
        if [[ "$sub_enabled" == "true" ]]; then
            echo -e "  状态: ${G}● 已启用${N}    拉取间隔: ${C}${sub_interval}秒${N}"
        else
            echo -e "  状态: ${R}○ 未启用${N}    拉取间隔: ${C}${sub_interval}秒${N}"
        fi
        echo -e "  订阅源: ${C}${feed_count}${N} 个    保存路径: ${D}${save_path:-未设置}${N}"
        echo -e "  分类: ${D}${category:-未设置}${N}"
        echo ""
        
        echo -e "  ${C}━━━━━━━━━━━━━━━ 操作菜单 ━━━━━━━━━━━━━━━━━━━${N}"
        echo ""
        echo -e "     ${Y}q${N}. 一键配置订阅"
        echo -e "     ${Y}t${N}. 手动测试抓取"
        echo ""
        echo -e "     ${G}1${N}. 开关订阅功能"
        echo -e "     ${G}2${N}. 设置拉取间隔"
        echo -e "     ${G}3${N}. 添加 RSS 订阅源"
        echo -e "     ${G}4${N}. 查看/删除订阅源"
        echo -e "     ${G}5${N}. 设置保存路径"
        echo -e "     ${G}6${N}. 设置种子分类"
        echo -e "     ${G}7${N}. 首尾文件块优先"
        echo -e "     ${G}8${N}. 查看订阅日志"
        echo ""
        echo -e "     ${D}0${N}. 返回主菜单"
        echo ""
        
        read -rp "  选择: " choice
        
        local tmp_cfg="/tmp/cfg_sub_$$.json"
        
        case "$choice" in
            t|T)
                # 手动测试抓取
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 手动测试 RSS 抓取 ━━━━━━━━━━━━${N}"
                echo ""
                
                # 检查RSS URL是否配置
                local rss_url
                rss_url=$(jq -r '.subscription.feeds[0].url // ""' "$CONFIG_FILE" 2>/dev/null)
                if [[ -z "$rss_url" ]]; then
                    err "请先配置 RSS 订阅源"
                    continue
                fi
                
                echo -e "  ${D}RSS URL: ${rss_url:0:60}...${N}"
                echo ""
                info "正在测试抓取..."
                echo ""
                
                # 使用Python脚本测试RSS抓取
                python3 << 'PYTEST' 2>&1
import sys
sys.path.insert(0, '/opt/qbit-smart-limit')
try:
    import json
    import requests
    import xml.etree.ElementTree as ET
    
    with open('/opt/qbit-smart-limit/config.json', 'r') as f:
        cfg = json.load(f)
    
    sub = cfg.get('subscription', {})
    feeds = sub.get('feeds', [])
    rss_url = feeds[0].get('url', '') if feeds else ''
    proxy = cfg.get('proxy', '')
    
    if not rss_url:
        print("  ✗ RSS URL未配置")
        sys.exit(1)
    
    resp = None
    use_proxy = False
    
    # 方法1: 先尝试直连（不使用代理）
    print(f"  → 尝试直连...")
    try:
        resp = requests.get(rss_url, timeout=30, proxies=None)
        if resp.status_code == 200:
            print(f"  ✓ 直连成功")
    except Exception as e1:
        print(f"  ! 直连失败: {str(e1)[:50]}")
        resp = None
    
    # 方法2: 如果直连失败且配置了代理，尝试代理
    if (resp is None or resp.status_code != 200) and proxy:
        print(f"  → 尝试代理连接 ({proxy[:30]}...)...")
        try:
            proxies = {'http': proxy, 'https': proxy}
            resp = requests.get(rss_url, timeout=30, proxies=proxies)
            if resp.status_code == 200:
                print(f"  ✓ 代理连接成功")
                use_proxy = True
        except Exception as e2:
            print(f"  ! 代理连接失败: {str(e2)[:50]}")
            resp = None
    
    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp else "无响应"
        print(f"  ✗ 连接失败 (状态: {status})")
        print("")
        print("  可能的原因:")
        print("  1. RSS URL不正确或已失效")
        print("  2. 网站需要特定的访问方式")
        print("  3. passkey已失效，请重新获取RSS链接")
        sys.exit(1)
    
    print(f"  ✓ 连接成功 (状态码: {resp.status_code})")
    
    # 解析RSS
    root = ET.fromstring(resp.content)
    items = root.findall('.//item')
    
    print(f"  ✓ 发现 {len(items)} 个种子条目")
    print("")
    
    if items:
        print("  最新5个种子:")
        for i, item in enumerate(items[:5]):
            title = item.find('title')
            if title is not None and title.text:
                print(f"    {i+1}. {title.text[:60]}")
        print("")
        print("  ✓ RSS抓取测试成功")
        if use_proxy:
            print("  ℹ 注意: 使用代理连接成功")
        else:
            print("  ℹ 建议: 清除配置中的无效代理设置")
    else:
        print("  ! RSS源中暂无种子")
except Exception as e:
    print(f"  ✗ 测试失败: {e}")
PYTEST
                echo ""
                ;;
            q|Q)
                # 一键配置订阅
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 一键配置 RSS 订阅 ━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}本向导将引导您完成 RSS 订阅的配置${N}"
                echo -e "  ${D}RSS 订阅使用主配置中的 qBittorrent 连接${N}"
                echo ""
                
                # 添加RSS订阅源
                read -rp "  RSS URL: " rss_url
                if [[ -z "$rss_url" ]]; then
                    warn "未输入 RSS URL，已取消"
                    continue
                fi
                
                read -rp "  订阅名称 (可选，回车跳过): " rss_name
                rss_name=${rss_name:-"Feed-1"}
                
                # 拉取间隔
                read -rp "  拉取间隔 (秒) [300]: " interval
                interval=${interval:-300}
                
                # 保存路径
                echo -e "  ${D}保存路径留空使用 qB 默认${N}"
                read -rp "  保存路径 (回车跳过): " new_path
                
                # 种子分类
                read -rp "  种子分类 (回车跳过): " new_cat
                
                # 首尾优先
                read -rp "  首尾文件块优先? [y/N]: " flp
                local flp_val="false"
                [[ "$flp" =~ ^[Yy] ]] && flp_val="true"
                
                # 应用配置
                echo ""
                info "正在应用配置..."
                
                local esc_url esc_name esc_path esc_cat
                esc_url=$(json_escape "$rss_url")
                esc_name=$(json_escape "$rss_name")
                esc_path=$(json_escape "$new_path")
                esc_cat=$(json_escape "$new_cat")
                
                if jq ".subscription.feeds = [{\"name\": \"$esc_name\", \"url\": \"$esc_url\"}] | \
                        .subscription.interval_seconds = $interval | \
                        .subscription.save_path = \"$esc_path\" | \
                        .subscription.category = \"$esc_cat\" | \
                        .subscription.first_last_piece = $flp_val | \
                        .subscription.enabled = true" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE"
                    ok "RSS 订阅配置完成"
                    echo ""
                    read -rp "  立即重启服务? [Y/n]: " r
                    if [[ ! "$r" =~ ^[Nn] ]]; then
                        systemctl restart qbit-smart-limit && ok "服务已重启"
                    fi
                else
                    rm -f "$tmp_cfg"
                    err "配置失败"
                fi
                ;;
            1)
                # 开关订阅
                if [[ "$sub_enabled" == "true" ]]; then
                    echo ""
                    read -rp "  确认禁用订阅功能? [y/N]: " confirm
                    if [[ "$confirm" =~ ^[Yy] ]]; then
                        if jq '.subscription.enabled = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            warn "订阅功能已禁用"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                else
                    # 检查是否有订阅源
                    if [[ "$feed_count" -eq 0 ]]; then
                        warn "请先添加至少一个订阅源"
                    else
                        echo ""
                        read -rp "  确认启用订阅功能? [Y/n]: " confirm
                        if [[ ! "$confirm" =~ ^[Nn] ]]; then
                            if jq '.subscription.enabled = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                                mv "$tmp_cfg" "$CONFIG_FILE"
                                chmod 600 "$CONFIG_FILE"
                                ok "订阅功能已启用"
                            else
                                rm -f "$tmp_cfg"
                                err "更新失败"
                            fi
                        fi
                    fi
                fi
                ;;
            2)
                # 设置拉取间隔
                echo ""
                echo -e "  ${D}建议: 300-600秒，避免过于频繁${N}"
                echo ""
                read -rp "  拉取间隔 (秒) [$sub_interval]: " interval
                interval=${interval:-$sub_interval}
                
                if [[ "$interval" =~ ^[0-9]+$ && "$interval" -ge 60 ]]; then
                    if jq ".subscription.interval_seconds = $interval" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                        mv "$tmp_cfg" "$CONFIG_FILE"
                        chmod 600 "$CONFIG_FILE"
                        ok "拉取间隔已设置: ${interval}秒"
                    else
                        rm -f "$tmp_cfg"
                        err "更新失败"
                    fi
                else
                    err "请输入至少60秒的有效数字"
                fi
                ;;
            3)
                # 添加订阅源
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 添加 RSS 订阅源 ━━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}支持标准 RSS/Atom 格式${N}"
                echo -e "  ${D}示例: https://u2.dmhy.org/torrentrss.php?...${N}"
                echo ""
                read -rp "  RSS URL: " rss_url
                
                if [[ -n "$rss_url" ]]; then
                    read -rp "  订阅名称 (可选): " rss_name
                    rss_name=${rss_name:-"Feed-$((feed_count + 1))"}
                    
                    # 添加到 feeds 数组
                    local esc_url esc_name
                    esc_url=$(json_escape "$rss_url")
                    esc_name=$(json_escape "$rss_name")
                    
                    if jq ".subscription.feeds += [{\"name\": \"$esc_name\", \"url\": \"$esc_url\"}]" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                        mv "$tmp_cfg" "$CONFIG_FILE"
                        chmod 600 "$CONFIG_FILE"
                        ok "已添加订阅源: $rss_name"
                    else
                        rm -f "$tmp_cfg"
                        err "添加失败"
                    fi
                fi
                ;;
            4)
                # 查看/删除订阅源
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━━ 当前订阅源 ━━━━━━━━━━━━━━━${N}"
                echo ""
                
                if [[ "$feed_count" -eq 0 ]]; then
                    echo -e "  ${D}暂无订阅源${N}"
                else
                    local i=0
                    while [[ $i -lt $feed_count ]]; do
                        local fname furl
                        fname=$(jq -r ".subscription.feeds[$i].name" "$CONFIG_FILE" 2>/dev/null)
                        furl=$(jq -r ".subscription.feeds[$i].url" "$CONFIG_FILE" 2>/dev/null)
                        echo -e "  ${G}$((i+1))${N}. ${W}$fname${N}"
                        echo -e "     ${D}$furl${N}"
                        echo ""
                        ((i++))
                    done
                    
                    read -rp "  删除哪个? (输入序号, 0返回): " del_idx
                    if [[ "$del_idx" =~ ^[1-9][0-9]*$ && "$del_idx" -le "$feed_count" ]]; then
                        local real_idx=$((del_idx - 1))
                        if jq "del(.subscription.feeds[$real_idx])" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "已删除订阅源 #$del_idx"
                        else
                            rm -f "$tmp_cfg"
                            err "删除失败"
                        fi
                    fi
                fi
                ;;
            5)
                # 设置保存路径
                echo ""
                echo -e "  ${D}留空使用 qB 默认路径${N}"
                echo ""
                read -rp "  保存路径: " new_path
                
                local esc_path
                esc_path=$(json_escape "$new_path")
                if jq ".subscription.save_path = \"$esc_path\"" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE"
                    if [[ -n "$new_path" ]]; then
                        ok "保存路径已设置: $new_path"
                    else
                        ok "将使用 qB 默认路径"
                    fi
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
                ;;
            6)
                # 设置种子分类
                echo ""
                echo -e "  ${D}新种子添加时会自动设置此分类${N}"
                echo -e "  ${D}留空则不设置分类${N}"
                echo ""
                echo -e "  当前: ${category:-未设置}"
                echo ""
                read -rp "  种子分类: " new_cat
                
                local esc_cat
                esc_cat=$(json_escape "$new_cat")
                if jq ".subscription.category = \"$esc_cat\"" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE"
                    if [[ -n "$new_cat" ]]; then
                        ok "种子分类已设置: $new_cat"
                    else
                        ok "已清除种子分类设置"
                    fi
                else
                    rm -f "$tmp_cfg"
                    err "更新失败"
                fi
                ;;
            7)
                # 首尾文件块优先
                echo ""
                local flp_enabled
                flp_enabled=$(get_nested_config ".subscription.first_last_piece" "false")
                
                if [[ "$flp_enabled" == "true" ]]; then
                    echo -e "  首尾优先: ${G}已启用${N}"
                    echo ""
                    read -rp "  禁用首尾优先? [y/N]: " confirm
                    if [[ "$confirm" =~ ^[Yy] ]]; then
                        if jq '.subscription.first_last_piece = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            warn "首尾优先已禁用"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                else
                    echo -e "  首尾优先: ${R}未启用${N}"
                    echo ""
                    echo -e "  ${D}启用后新种子会优先下载首尾文件块${N}"
                    echo -e "  ${D}有助于尽快开始播放视频文件${N}"
                    echo ""
                    read -rp "  启用首尾优先? [Y/n]: " confirm
                    if [[ ! "$confirm" =~ ^[Nn] ]]; then
                        if jq '.subscription.first_last_piece = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "首尾优先已启用"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                fi
                ;;
            8)
                # 查看订阅日志
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━━ RSS 订阅日志 ━━━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}最近 20 条日志 (包含 订阅/RSS 关键字):${N}"
                echo ""
                journalctl -u qbit-smart-limit -n 100 --no-pager -o cat 2>/dev/null | \
                    grep -iE "订阅|rss|subscription|📥|feed" | tail -20 || \
                    echo -e "  ${D}暂无订阅相关日志${N}"
                echo ""
                ;;
            0)
                echo ""
                read -rp "  重启服务使配置生效? [Y/n]: " r
                if [[ ! "$r" =~ ^[Nn] ]]; then
                    systemctl restart qbit-smart-limit && ok "服务已重启"
                fi
                return
                ;;
            *)
                err "无效选择"
                ;;
        esac
        
        echo ""
        read -rp "  按回车继续..."
    done
}

# ════════════════════════════════════════════════════════════
# 自动删种管理
# ════════════════════════════════════════════════════════════
do_cleanup() {
    if ! is_installed; then
        err "请先安装"
        return
    fi
    
    # 确保配置存在
    migrate_config
    
    while true; do
        echo ""
        echo -e "  ${C}━━━━━━━━━━━━━━ 自动删种管理 ━━━━━━━━━━━━━━━━${N}"
        echo ""
        
        # 当前状态
        local clean_enabled clean_interval delete_files
        clean_enabled=$(get_nested_config ".cleanup.enabled" "false")
        clean_interval=$(get_nested_config ".cleanup.interval_seconds" "600")
        delete_files=$(get_nested_config ".cleanup.delete_files" "false")
        
        if [[ "$clean_enabled" == "true" ]]; then
            echo -e "  状态: ${G}● 已启用${N}    检查间隔: ${C}${clean_interval}秒${N}"
        else
            echo -e "  状态: ${R}○ 未启用${N}    检查间隔: ${C}${clean_interval}秒${N}"
        fi
        if [[ "$delete_files" == "true" ]]; then
            echo -e "  删除文件: ${R}是${N} (同时删除本地文件)"
        else
            echo -e "  删除文件: ${G}否${N} (仅从 qB 移除)"
        fi
        echo ""
        
        echo -e "  ${C}━━━━━━━━━━━━━━━ 操作菜单 ━━━━━━━━━━━━━━━━━━━${N}"
        echo ""
        echo -e "     ${Y}q${N}. 一键配置删种"
        echo -e "     ${Y}t${N}. 手动测试删种"
        echo ""
        echo -e "     ${G}1${N}. 开关删种功能"
        echo -e "     ${G}2${N}. 设置检查间隔"
        echo -e "     ${G}5${N}. 开关删除文件"
        echo -e "     ${Y}8${N}. 空间规则配置"
        echo -e "     ${Y}9${N}. 删前汇报设置"
        echo -e "     ${G}l${N}. 查看删种日志"
        echo ""
        echo -e "     ${D}0${N}. 返回主菜单"
        echo ""
        
        read -rp "  选择: " choice
        
        local tmp_cfg="/tmp/cfg_clean_$$.json"
        
        case "$choice" in
            t|T)
                # 手动测试删种（空间规则）
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 手动测试删种检查 ━━━━━━━━━━━━${N}"
                echo ""

                info "正在按“剩余空间 + 速度(上传/下载)”规则检查可删任务 (queued/paused 会被跳过)..."
                echo ""

                python3 << 'PYTEST' 2>&1
import sys, json, shutil, os
import qbittorrentapi

sys.path.insert(0, '/opt/qbit-smart-limit')

with open('/opt/qbit-smart-limit/config.json', 'r') as f:
    cfg = json.load(f)

clean = cfg.get('cleanup', {}) if isinstance(cfg.get('cleanup', {}), dict) else {}
enabled = bool(clean.get('enabled', cfg.get('cleanup_enabled', False)))
interval = int(clean.get('interval_seconds', cfg.get('cleanup_interval', 600)) or 600)
delete_files = bool(clean.get('delete_files', cfg.get('cleanup_delete_files', False)))

r1_gb = int(cfg.get('cleanup_space_rule1_gb', 10) or 10)
r1_up = int(cfg.get('cleanup_space_rule1_upload_kib', 1024) or 1024)
r1_dl = int(cfg.get('cleanup_space_rule1_download_kib', 1024) or 1024)
r2_gb = int(cfg.get('cleanup_space_rule2_gb', 20) or 20)
r2_up = int(cfg.get('cleanup_space_rule2_upload_kib', 512) or 512)
r2_dl = int(cfg.get('cleanup_space_rule2_download_kib', 512) or 512)
r3_gb = int(cfg.get('cleanup_space_rule3_gb', 5) or 5)
r3_up = int(cfg.get('cleanup_space_rule3_upload_kib', 5120) or 5120)
r3_dl = int(cfg.get('cleanup_space_rule3_download_kib', 5120) or 5120)

print(f"  删种模块: {'已启用' if enabled else '未启用'}  (间隔 {interval}s, 删除文件={delete_files})")
print("  空间规则(默认启用，优先级 3>1>2):")
print(f"    1) 剩余<{r1_gb}G, 上传<{r1_up}KiB/s 或 下载<{r1_dl}KiB/s → 删除")
print(f"    2) 剩余<{r2_gb}G, 已完成&上传<{r2_up}KiB/s 或 下载<{r2_dl}KiB/s → 删除")
print(f"    3) 剩余<{r3_gb}G, 上传<{r3_up}KiB/s 或 下载<{r3_dl}KiB/s → 删除 (紧急)")
print("")

host = cfg.get('host', 'http://127.0.0.1:8080')
username = cfg.get('username', 'admin')
password = cfg.get('password', '')

client = qbittorrentapi.Client(host=host, username=username, password=password)
client.auth_log_in()

# 获取默认保存路径剩余空间
try:
    prefs = client.app_preferences()
    save_path = prefs.get('save_path', '/downloads')
    if not os.path.exists(save_path):
        save_path = '/'
    free_gb = shutil.disk_usage(save_path).free / (1024**3)
except Exception:
    free_gb = float('inf')

print(f"  默认下载路径剩余空间: {free_gb:.1f}G")
print("")

torrents = client.torrents_info()
print(f"  → 检查 {len(torrents)} 个种子 (做种/上传 + 下载中状态会被空间规则命中)")
print("")

matched = []
upload_states = {'seeding', 'stalledup', 'forcedup', 'uploading'}
download_states = {'downloading', 'stalleddl', 'forceddl', 'metadl'}

def is_waiting(st: str) -> bool:
    st = (st or '').lower()
    return ('queued' in st) or ('paused' in st)

for t in torrents:
    name = getattr(t, 'name', 'Unknown')
    state = getattr(t, 'state', '') or ''
    s = state.lower()

    if is_waiting(s):
        continue

    progress = getattr(t, 'progress', 0) or 0
    completed = progress >= 1.0

    reason = None

    if s in upload_states:
        up_kib = (getattr(t, 'upspeed', 0) or 0) / 1024
        if free_gb < r3_gb and up_kib < r3_up:
            reason = f"[上传-紧急] 规则3: 剩余{free_gb:.1f}G<{r3_gb}G, 上传{up_kib:.0f}KiB/s<{r3_up}"
        elif free_gb < r1_gb and up_kib < r1_up:
            reason = f"[上传] 规则1: 剩余{free_gb:.1f}G<{r1_gb}G, 上传{up_kib:.0f}KiB/s<{r1_up}"
        elif free_gb < r2_gb and completed and up_kib < r2_up:
            reason = f"[上传] 规则2: 剩余{free_gb:.1f}G<{r2_gb}G, 已完成, 上传{up_kib:.0f}KiB/s<{r2_up}"

    elif s in download_states and not completed:
        dl_kib = (getattr(t, 'dlspeed', 0) or 0) / 1024
        if free_gb < r3_gb and dl_kib < r3_dl:
            reason = f"[下载-紧急] 规则3: 剩余{free_gb:.1f}G<{r3_gb}G, 下载{dl_kib:.0f}KiB/s<{r3_dl}"
        elif free_gb < r1_gb and dl_kib < r1_dl:
            reason = f"[下载] 规则1: 剩余{free_gb:.1f}G<{r1_gb}G, 下载{dl_kib:.0f}KiB/s<{r1_dl}"
        elif free_gb < r2_gb and dl_kib < r2_dl:
            reason = f"[下载] 规则2: 剩余{free_gb:.1f}G<{r2_gb}G, 下载{dl_kib:.0f}KiB/s<{r2_dl}"

    if reason:
        matched.append((name, reason, state))

if matched:
    print(f"  ✓ 发现 {len(matched)} 个符合“空间规则”删除条件的任务:")
    print("")
    for i, (name, reason, state) in enumerate(matched[:10], 1):
        print(f"    {i}. {name[:55]}")
        print(f"       {reason}  ({state})")
    if len(matched) > 10:
        print(f"    ... 还有 {len(matched) - 10} 个")
else:
    print("  ✓ 没有符合“空间规则”删除条件的任务")

PYTEST
                echo ""
                ;;
q|Q)
    # 一键配置删种（仅空间规则）
    echo ""
    echo -e "  ${C}━━━━━━━━━━━━ 一键配置自动删种 ━━━━━━━━━━━━${N}"
    echo ""
    echo -e "  ${D}当前版本删种按“剩余空间 + 速度(上传/下载)”规则运行${N}"
    echo -e "  ${D}空间规则默认启用，无需开关（可在“空间规则配置”里调整阈值）${N}"
    echo -e "  ${Y}警告: 配置完成后将自动启用删种模块!${N}"
    echo ""

    # 检查间隔
    read -rp "  检查间隔 (秒) [600]: " interval
    interval=${interval:-600}

    # 删除文件
    read -rp "  同时删除本地文件? [y/N]: " del_files
    local del_val="false"
    [[ "$del_files" =~ ^[Yy] ]] && del_val="true"

    # 应用配置
    echo ""
    info "正在应用配置..."

    if jq ".cleanup.interval_seconds = $interval | \
            .cleanup.delete_files = $del_val | \
            .cleanup.enabled = true" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
        mv "$tmp_cfg" "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
        ok "自动删种配置完成"
        echo ""
        read -rp "  立即重启服务? [Y/n]: " r
        if [[ ! "$r" =~ ^[Nn] ]]; then
            systemctl restart qbit-smart-limit && ok "服务已重启"
        fi
    else
        rm -f "$tmp_cfg"
        err "配置失败"
    fi
    ;;
            l|L)
                # 查看删种日志
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━━ 自动删种日志 ━━━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}最近 20 条日志 (包含 删种/cleanup 关键字):${N}"
                echo ""
                journalctl -u qbit-smart-limit -n 100 --no-pager -o cat 2>/dev/null | \
                    grep -iE "删种|cleanup|删除|🗑|移除|清理" | tail -20 || \
                    echo -e "  ${D}暂无删种相关日志${N}"
                echo ""
                ;;
            1)
                # 开关删种
                if [[ "$clean_enabled" == "true" ]]; then
                    echo ""
                    read -rp "  确认禁用自动删种? [y/N]: " confirm
                    if [[ "$confirm" =~ ^[Yy] ]]; then
                        if jq '.cleanup.enabled = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            warn "自动删种已禁用"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                else
                    echo ""
                    echo -e "  ${Y}警告: 启用后将根据条件自动删除种子!${N}"
                    echo ""
                    read -rp "  确认启用自动删种? [y/N]: " confirm
                    if [[ "$confirm" =~ ^[Yy] ]]; then
                        if jq '.cleanup.enabled = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "自动删种已启用"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                fi
                ;;
            2)
                # 设置检查间隔
                echo ""
                echo -e "  ${D}建议: 600-3600秒${N}"
                echo ""
                read -rp "  检查间隔 (秒) [$clean_interval]: " interval
                interval=${interval:-$clean_interval}
                
                if [[ "$interval" =~ ^[0-9]+$ && "$interval" -ge 60 ]]; then
                    if jq ".cleanup.interval_seconds = $interval" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                        mv "$tmp_cfg" "$CONFIG_FILE"
                        chmod 600 "$CONFIG_FILE"
                        ok "检查间隔已设置: ${interval}秒"
                    else
                        rm -f "$tmp_cfg"
                        err "更新失败"
                    fi
                else
                    err "请输入至少60秒的有效数字"
                fi
                ;;
            5)
                # 开关删除文件
                if [[ "$delete_files" == "true" ]]; then
                    echo ""
                    read -rp "  改为仅从 qB 移除 (保留文件)? [Y/n]: " confirm
                    if [[ ! "$confirm" =~ ^[Nn] ]]; then
                        if jq '.cleanup.delete_files = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "将仅从 qB 移除，保留本地文件"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                else
                    echo ""
                    echo -e "  ${R}警告: 这将同时删除本地文件，无法恢复!${N}"
                    echo ""
                    read -rp "  确认同时删除本地文件? [y/N]: " confirm
                    if [[ "$confirm" =~ ^[Yy] ]]; then
                        if jq '.cleanup.delete_files = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            warn "将同时删除本地文件"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                    fi
                fi
                ;;
            8)
                # 空间规则配置
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 空间规则配置 ━━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}根据剩余空间和速度(上传/下载)自动删种${N}"
                echo ""

                echo -e "  当前状态: ${G}默认启用${N} (无需开关)"
                echo ""
                
                # 显示当前规则
                local r1_gb r1_up r1_dl r2_gb r2_up r2_dl r3_gb r3_up r3_dl
                r1_gb=$(jq -r '.cleanup_space_rule1_gb // 10' "$CONFIG_FILE" 2>/dev/null)
                r1_up=$(jq -r '.cleanup_space_rule1_upload_kib // 1024' "$CONFIG_FILE" 2>/dev/null)
                r1_dl=$(jq -r '.cleanup_space_rule1_download_kib // 1024' "$CONFIG_FILE" 2>/dev/null)
                r2_gb=$(jq -r '.cleanup_space_rule2_gb // 20' "$CONFIG_FILE" 2>/dev/null)
                r2_up=$(jq -r '.cleanup_space_rule2_upload_kib // 512' "$CONFIG_FILE" 2>/dev/null)
                r2_dl=$(jq -r '.cleanup_space_rule2_download_kib // 512' "$CONFIG_FILE" 2>/dev/null)
                r3_gb=$(jq -r '.cleanup_space_rule3_gb // 5' "$CONFIG_FILE" 2>/dev/null)
                r3_up=$(jq -r '.cleanup_space_rule3_upload_kib // 5120' "$CONFIG_FILE" 2>/dev/null)
                r3_dl=$(jq -r '.cleanup_space_rule3_download_kib // 5120' "$CONFIG_FILE" 2>/dev/null)
                
                echo -e "  ${Y}当前规则:${N}"
                echo -e "  1. 剩余<${r1_gb}G, 上传<${r1_up}KiB/s 或 下载<${r1_dl}KiB/s → 删除"
                echo -e "  2. 剩余<${r2_gb}G, 已完成&上传<${r2_up}KiB/s 或 下载<${r2_dl}KiB/s → 删除"
                echo -e "  3. 剩余<${r3_gb}G, 上传<${r3_up}KiB/s 或 下载<${r3_dl}KiB/s → 删除 (紧急)"
                echo ""
                
                echo -e "  ${W}选项:${N}"
                echo -e "     ${G}1${N}. 修改规则1 (空间<10G)"
                echo -e "     ${G}2${N}. 修改规则2 (空间<20G+已完成)"
                echo -e "     ${G}3${N}. 修改规则3 (空间<5G,紧急)"
                echo -e "     ${D}0${N}. 返回"
                echo ""
                
                read -rp "  选择: " sub_choice
                case "$sub_choice" in
                    1)
                        echo ""
                        read -rp "  规则1 - 剩余空间阈值(G) [$r1_gb]: " new_gb
                        read -rp "  规则1 - 上传速度阈值(KiB/s) [$r1_up]: " new_up
                        read -rp "  规则1 - 下载速度阈值(KiB/s) [$r1_dl]: " new_dl
                        new_gb=${new_gb:-$r1_gb}
                        new_up=${new_up:-$r1_up}
                        new_dl=${new_dl:-$r1_dl}
                        if jq ".cleanup_space_rule1_gb = $new_gb | .cleanup_space_rule1_upload_kib = $new_up | .cleanup_space_rule1_download_kib = $new_dl" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "规则1已更新: 空间<${new_gb}G, 上传<${new_up}KiB/s, 下载<${new_dl}KiB/s"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                        ;;
                    2)
                        echo ""
                        read -rp "  规则2 - 剩余空间阈值(G) [$r2_gb]: " new_gb
                        read -rp "  规则2 - 上传速度阈值(KiB/s) [$r2_up]: " new_up
                        read -rp "  规则2 - 下载速度阈值(KiB/s) [$r2_dl]: " new_dl
                        new_gb=${new_gb:-$r2_gb}
                        new_up=${new_up:-$r2_up}
                        new_dl=${new_dl:-$r2_dl}
                        if jq ".cleanup_space_rule2_gb = $new_gb | .cleanup_space_rule2_upload_kib = $new_up | .cleanup_space_rule2_download_kib = $new_dl" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "规则2已更新: 空间<${new_gb}G, 已完成&上传<${new_up}KiB/s, 下载<${new_dl}KiB/s"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                        ;;
                    3)
                        echo ""
                        read -rp "  规则3 - 剩余空间阈值(G) [$r3_gb]: " new_gb
                        read -rp "  规则3 - 上传速度阈值(KiB/s) [$r3_up]: " new_up
                        read -rp "  规则3 - 下载速度阈值(KiB/s) [$r3_dl]: " new_dl
                        new_gb=${new_gb:-$r3_gb}
                        new_up=${new_up:-$r3_up}
                        new_dl=${new_dl:-$r3_dl}
                        if jq ".cleanup_space_rule3_gb = $new_gb | .cleanup_space_rule3_upload_kib = $new_up | .cleanup_space_rule3_download_kib = $new_dl" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                            mv "$tmp_cfg" "$CONFIG_FILE"
                            chmod 600 "$CONFIG_FILE"
                            ok "规则3已更新: 空间<${new_gb}G, 上传<${new_up}KiB/s, 下载<${new_dl}KiB/s"
                        else
                            rm -f "$tmp_cfg"
                            err "更新失败"
                        fi
                        ;;
                esac
                ;;
            9)
                # 删前汇报设置
                echo ""
                echo -e "  ${C}━━━━━━━━━━━━ 删前汇报设置 ━━━━━━━━━━━━━${N}"
                echo ""
                echo -e "  ${D}删除种子前强制汇报一次，等待后再删除${N}"
                echo -e "  ${D}这样可以确保上传量被正确记录${N}"
                echo ""
                
                local reann_enabled reann_wait
                reann_enabled=$(get_config_bool "cleanup_reannounce_before_delete" "true")
                reann_wait=$(jq -r '.cleanup_reannounce_wait // 5' "$CONFIG_FILE" 2>/dev/null)
                
                if [[ "$reann_enabled" == "true" ]]; then
                    echo -e "  删前汇报: ${G}已启用${N}"
                else
                    echo -e "  删前汇报: ${R}未启用${N}"
                fi
                echo -e "  等待时间: ${C}${reann_wait}秒${N}"
                echo ""
                
                echo -e "  ${W}选项:${N}"
                echo -e "     ${G}1${N}. 开关删前汇报"
                echo -e "     ${G}2${N}. 设置等待时间"
                echo -e "     ${D}0${N}. 返回"
                echo ""
                
                read -rp "  选择: " sub_choice
                
                case "$sub_choice" in
                    1)
                        if [[ "$reann_enabled" == "true" ]]; then
                            if jq '.cleanup_reannounce_before_delete = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                                mv "$tmp_cfg" "$CONFIG_FILE"
                                chmod 600 "$CONFIG_FILE"
                                warn "删前汇报已禁用"
                            else
                                rm -f "$tmp_cfg"
                                err "更新失败"
                            fi
                        else
                            if jq '.cleanup_reannounce_before_delete = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                                mv "$tmp_cfg" "$CONFIG_FILE"
                                chmod 600 "$CONFIG_FILE"
                                ok "删前汇报已启用"
                            else
                                rm -f "$tmp_cfg"
                                err "更新失败"
                            fi
                        fi
                        ;;
                    2)
                        echo ""
                        read -rp "  等待时间(秒) [$reann_wait]: " new_wait
                        new_wait=${new_wait:-$reann_wait}
                        if [[ "$new_wait" =~ ^[0-9]+$ && "$new_wait" -ge 1 && "$new_wait" -le 60 ]]; then
                            if jq ".cleanup_reannounce_wait = $new_wait" "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                                mv "$tmp_cfg" "$CONFIG_FILE"
                                chmod 600 "$CONFIG_FILE"
                                ok "等待时间已设置: ${new_wait}秒"
                            else
                                rm -f "$tmp_cfg"
                                err "更新失败"
                            fi
                        else
                            err "请输入1-60之间的有效数字"
                        fi
                        ;;
                esac
                ;;
            0)
                echo ""
                read -rp "  重启服务使配置生效? [Y/n]: " r
                if [[ ! "$r" =~ ^[Nn] ]]; then
                    systemctl restart qbit-smart-limit && ok "服务已重启"
                fi
                return
                ;;
            *)
                err "无效选择"
                ;;
        esac
        
        echo ""
        read -rp "  按回车继续..."
    done
}

# ════════════════════════════════════════════════════════════
# 状态
# ════════════════════════════════════════════════════════════
do_status() {
    show_banner
    
    echo -e "  ${C}━━━━━━━━━━━━━━━━ 服务状态 ━━━━━━━━━━━━━━━━━━━${N}"
    echo ""
    systemctl status qbit-smart-limit --no-pager -l 2>/dev/null || echo -e "  ${R}服务未运行${N}"
    
    if [[ -f "$CONFIG_FILE" ]]; then
        echo ""
        echo -e "  ${C}━━━━━━━━━━━━━━━━ 当前配置 ━━━━━━━━━━━━━━━━━━━${N}"
        echo ""
        echo -e "  qBittorrent: $(jq -r '.host // "未设置"' "$CONFIG_FILE" 2>/dev/null)"
        echo -e "  目标速度:    $(jq -r '.target_speed_kib // 0' "$CONFIG_FILE" 2>/dev/null) KiB/s × $(jq -r '.safety_margin // 0.98' "$CONFIG_FILE" 2>/dev/null)"
        echo -e "  Tracker:     $(jq -r '.target_tracker_keyword // "全部"' "$CONFIG_FILE" 2>/dev/null)"
        
        # 高级功能状态
        local dl_enabled ra_enabled
        dl_enabled=$(get_config_bool "enable_dl_limit" "true")
        ra_enabled=$(get_config_bool "enable_reannounce_opt" "true")
        
        if [[ "$dl_enabled" == "true" ]]; then
            echo -e "  下载限速:    ${G}已启用${N}"
        else
            echo -e "  下载限速:    ${R}未启用${N}"
        fi
        
        if [[ "$ra_enabled" == "true" ]]; then
            echo -e "  汇报优化:    ${G}已启用${N}"
        else
            echo -e "  汇报优化:    ${R}未启用${N}"
        fi
        
        # 扩展模块状态
        local sub_enabled clean_enabled
        sub_enabled=$(get_nested_config ".subscription.enabled" "false")
        clean_enabled=$(get_nested_config ".cleanup.enabled" "false")
        
        if [[ "$sub_enabled" == "true" ]]; then
            local feed_count
            feed_count=$(jq -r '.subscription.feeds | length' "$CONFIG_FILE" 2>/dev/null || echo "0")
            echo -e "  RSS订阅:     ${G}已启用${N} (${feed_count}个源)"
        else
            echo -e "  RSS订阅:     ${D}未启用${N}"
        fi
        
        
if [[ "$clean_enabled" == "true" ]]; then
    local r1_gb r1_up r1_dl r2_gb r2_up r2_dl r3_gb r3_up r3_dl
    r1_gb=$(jq -r '.cleanup_space_rule1_gb // 10' "$CONFIG_FILE" 2>/dev/null)
    r1_up=$(jq -r '.cleanup_space_rule1_upload_kib // 1024' "$CONFIG_FILE" 2>/dev/null)
    r1_dl=$(jq -r '.cleanup_space_rule1_download_kib // 1024' "$CONFIG_FILE" 2>/dev/null)
    r2_gb=$(jq -r '.cleanup_space_rule2_gb // 20' "$CONFIG_FILE" 2>/dev/null)
    r2_up=$(jq -r '.cleanup_space_rule2_upload_kib // 512' "$CONFIG_FILE" 2>/dev/null)
    r2_dl=$(jq -r '.cleanup_space_rule2_download_kib // 512' "$CONFIG_FILE" 2>/dev/null)
    r3_gb=$(jq -r '.cleanup_space_rule3_gb // 5' "$CONFIG_FILE" 2>/dev/null)
    r3_up=$(jq -r '.cleanup_space_rule3_upload_kib // 5120' "$CONFIG_FILE" 2>/dev/null)
    r3_dl=$(jq -r '.cleanup_space_rule3_download_kib // 5120' "$CONFIG_FILE" 2>/dev/null)
    echo -e "  自动删种:    ${G}已启用${N} (空间规则 3>1>2)"
    echo -e "              1) 剩余<${r1_gb}G & 上传<${r1_up}KiB/s | 下载<${r1_dl}KiB/s"
    echo -e "              2) 剩余<${r2_gb}G & 已完成&上传<${r2_up}KiB/s | 下载<${r2_dl}KiB/s"
    echo -e "              3) 剩余<${r3_gb}G & 上传<${r3_up}KiB/s | 下载<${r3_dl}KiB/s (紧急)"
else
    echo -e "  自动删种:    ${D}未启用${N}"
fi
        
        local u2_cookie tg_token
        u2_cookie=$(jq -r '.u2_cookie // ""' "$CONFIG_FILE" 2>/dev/null)
        tg_token=$(jq -r '.telegram_bot_token // ""' "$CONFIG_FILE" 2>/dev/null)
        
        [[ -n "$tg_token" ]] && echo -e "  Telegram:    ${G}已配置${N}" || echo -e "  Telegram:    ${D}未配置${N}"
        
        if [[ -n "$u2_cookie" ]]; then
            if python3 -c "from bs4 import BeautifulSoup" &>/dev/null; then
                echo -e "  U2 Cookie:   ${G}已配置 (${#u2_cookie}字符, bs4 已安装)${N}"
            else
                echo -e "  U2 Cookie:   ${Y}已配置 (${#u2_cookie}字符, bs4 未安装)${N}"
            fi
        else
            echo -e "  U2 Cookie:   ${D}未配置${N}"
        fi
        
        echo ""
        echo -e "  ${C}━━━━━━━━━━━━━━━━ 最近日志 ━━━━━━━━━━━━━━━━━━━${N}"
        echo ""
        journalctl -u qbit-smart-limit -n 15 --no-pager -o cat 2>/dev/null || echo -e "  ${D}无日志${N}"
    fi
}

do_logs() {
    echo ""
    echo -e "  ${C}实时日志 (Ctrl+C 退出)${N}"
    echo ""
    journalctl -u qbit-smart-limit -f --no-hostname -o cat
}

# ════════════════════════════════════════════════════════════
# 卸载
# ════════════════════════════════════════════════════════════
do_uninstall() {
    echo ""
    echo -e "  ${R}>>> 卸载 qBit Smart Limit <<<${N}"
    echo ""
    
    read -rp "  确认卸载? [y/N]: " confirm
    [[ ! "$confirm" =~ ^[Yy] ]] && return
    
    echo ""
    systemctl stop qbit-smart-limit 2>/dev/null || true
    systemctl disable qbit-smart-limit 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    ok "服务已移除"
    
    rm -f "$SCRIPT_PATH"
    ok "管理脚本已移除"
    
    read -rp "  删除配置文件? [y/N]: " d
    [[ "$d" =~ ^[Yy] ]] && rm -rf "$INSTALL_DIR" && ok "配置已删除"
    
    echo ""
    ok "卸载完成"
}

# ════════════════════════════════════════════════════════════
# 主程序
# ════════════════════════════════════════════════════════════
main() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${R}请使用 root 运行${N}"
        exit 1
    fi
    
    while true; do
        show_banner
        show_status
        show_menu
        
        read -rp "  请选择 [0-9/a-b]: " choice
        
        case "$choice" in
            1) do_install ;;
            2) do_config ;;
            3) do_status ;;
            4) do_logs ;;
            5) systemctl start qbit-smart-limit 2>/dev/null && ok "服务已启动" || err "启动失败" ;;
            6) systemctl stop qbit-smart-limit 2>/dev/null && warn "服务已停止" ;;
            7) systemctl restart qbit-smart-limit 2>/dev/null && ok "服务已重启" || err "重启失败" ;;
            8) do_update ;;
            9) do_uninstall ;;
            a|A) do_subscription ;;
            b|B) do_cleanup ;;
            0) echo -e "  ${C}再见!${N}"; exit 0 ;;
            *) err "无效选择" ;;
        esac
        
        echo ""
        read -rp "  按回车继续..."
    done
}

main "$@"
