#!/bin/bash
#
# qBit Smart Limit 安装器 & 遥控器
# 功能: 安装/更新/卸载/控制服务
#

set -e

# ==================== 颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ==================== 配置变量 ====================
INSTALL_DIR="/opt/qsl"
SERVICE_NAME="qsl"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CONFIG_FILE="${INSTALL_DIR}/config.json"
VENV_DIR="${INSTALL_DIR}/venv"
GITHUB_REPO="your-username/qbit-smart-limit"
REQUIRED_PYTHON="3.8"

# ==================== 辅助函数 ====================
print_header() {
    clear
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}     ${BOLD}qBit Smart Limit${NC} - 智能限速控制器                      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}     安装器 & 遥控器 v2.0                                    ${CYAN}║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local answer
    
    if [[ "$default" == "y" ]]; then
        prompt="$prompt [Y/n]: "
    else
        prompt="$prompt [y/N]: "
    fi
    
    read -r -p "$prompt" answer
    answer=${answer:-$default}
    
    [[ "$answer" =~ ^[Yy]$ ]]
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "请使用 root 权限运行此脚本"
        exit 1
    fi
}

is_installed() {
    [[ -d "$INSTALL_DIR" && -f "$CONFIG_FILE" ]]
}

is_service_active() {
    systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null
}

# ==================== 安装函数 ====================
check_dependencies() {
    log_step "检查系统依赖..."
    
    local missing=()
    
    # 检查 Python
    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    else
        local py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if [[ $(echo "$py_ver < $REQUIRED_PYTHON" | bc -l) -eq 1 ]]; then
            log_error "Python 版本需要 >= $REQUIRED_PYTHON，当前: $py_ver"
            exit 1
        fi
        log_info "Python 版本: $py_ver ✓"
    fi
    
    # 检查 pip
    if ! command -v pip3 &>/dev/null; then
        missing+=("python3-pip")
    fi
    
    # 检查 venv
    if ! python3 -c "import venv" 2>/dev/null; then
        missing+=("python3-venv")
    fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "缺少依赖: ${missing[*]}"
        log_step "正在安装..."
        
        if command -v apt-get &>/dev/null; then
            apt-get update && apt-get install -y "${missing[@]}"
        elif command -v yum &>/dev/null; then
            yum install -y "${missing[@]}"
        elif command -v dnf &>/dev/null; then
            dnf install -y "${missing[@]}"
        else
            log_error "无法自动安装依赖，请手动安装: ${missing[*]}"
            exit 1
        fi
    fi
    
    log_info "系统依赖检查完成 ✓"
}

install_files() {
    log_step "安装程序文件..."
    
    # 创建安装目录
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/qsl"
    mkdir -p "$INSTALL_DIR/logs"
    
    # 复制文件
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    cp "$script_dir/main.py" "$INSTALL_DIR/"
    cp "$script_dir/qsl/"*.py "$INSTALL_DIR/qsl/"
    
    # 设置权限
    chmod +x "$INSTALL_DIR/main.py"
    
    log_info "程序文件已安装到 $INSTALL_DIR ✓"
}

setup_venv() {
    log_step "创建虚拟环境..."
    
    if [[ -d "$VENV_DIR" ]]; then
        log_warn "虚拟环境已存在，跳过创建"
    else
        python3 -m venv "$VENV_DIR"
    fi
    
    # 安装依赖
    log_step "安装 Python 依赖..."
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install qbittorrent-api requests
    
    log_info "虚拟环境配置完成 ✓"
}

create_config() {
    log_step "配置文件设置..."
    
    if [[ -f "$CONFIG_FILE" ]]; then
        if ! confirm "配置文件已存在，是否覆盖？"; then
            log_info "保留现有配置"
            return
        fi
        cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
        log_info "已备份现有配置到 ${CONFIG_FILE}.bak"
    fi
    
    echo ""
    echo -e "${BOLD}=== qBittorrent 连接配置 ===${NC}"
    read -r -p "qBittorrent 地址 [127.0.0.1]: " qb_host
    qb_host=${qb_host:-127.0.0.1}
    
    read -r -p "qBittorrent 端口 [8080]: " qb_port
    qb_port=${qb_port:-8080}
    
    read -r -p "qBittorrent 用户名 [admin]: " qb_user
    qb_user=${qb_user:-admin}
    
    read -r -s -p "qBittorrent 密码: " qb_pass
    echo ""
    
    echo ""
    echo -e "${BOLD}=== 限速配置 ===${NC}"
    read -r -p "最大上传速度 (MB/s) [20]: " max_upload
    max_upload=${max_upload:-20}
    max_upload_bytes=$((max_upload * 1024 * 1024))
    
    read -r -p "最小上传速度 (MB/s) [1]: " min_upload
    min_upload=${min_upload:-1}
    min_upload_bytes=$((min_upload * 1024 * 1024))
    
    read -r -p "目标缓冲区大小 (MB) [200]: " target_buffer
    target_buffer=${target_buffer:-200}
    target_buffer_bytes=$((target_buffer * 1024 * 1024))
    
    echo ""
    echo -e "${BOLD}=== Telegram 通知 (可选) ===${NC}"
    read -r -p "是否启用 Telegram 通知? [y/N]: " enable_tg
    
    tg_token=""
    tg_chat_id=""
    if [[ "$enable_tg" =~ ^[Yy]$ ]]; then
        read -r -p "Bot Token: " tg_token
        read -r -p "Chat ID: " tg_chat_id
    fi
    
    echo ""
    echo -e "${BOLD}=== 订阅模块 (可选) ===${NC}"
    read -r -p "是否启用 RSS 订阅? [y/N]: " enable_sub
    
    sub_rss_url=""
    sub_interval=300
    if [[ "$enable_sub" =~ ^[Yy]$ ]]; then
        read -r -p "RSS 订阅 URL: " sub_rss_url
        read -r -p "检查间隔 (秒) [300]: " sub_interval
        sub_interval=${sub_interval:-300}
    fi
    
    echo ""
    echo -e "${BOLD}=== 删种模块 (可选) ===${NC}"
    read -r -p "是否启用自动删种? [y/N]: " enable_cleanup
    
    cleanup_min_ratio=1.0
    cleanup_min_seeding=86400
    cleanup_delete_files="false"
    if [[ "$enable_cleanup" =~ ^[Yy]$ ]]; then
        read -r -p "最小分享率 [1.0]: " cleanup_min_ratio
        cleanup_min_ratio=${cleanup_min_ratio:-1.0}
        read -r -p "最小做种时间 (秒) [86400]: " cleanup_min_seeding
        cleanup_min_seeding=${cleanup_min_seeding:-86400}
        read -r -p "删除文件? [y/N]: " del_files
        [[ "$del_files" =~ ^[Yy]$ ]] && cleanup_delete_files="true"
    fi
    
    # 生成配置文件
    cat > "$CONFIG_FILE" << EOF
{
    "_comment": "qBit Smart Limit 配置文件",
    
    "qb_host": "$qb_host",
    "qb_port": $qb_port,
    "qb_username": "$qb_user",
    "qb_password": "$qb_pass",
    
    "max_upload_speed": $max_upload_bytes,
    "min_upload_speed": $min_upload_bytes,
    "target_buffer_size": $target_buffer_bytes,
    
    "pid_kp": 0.5,
    "pid_ki": 0.1,
    "pid_kd": 0.05,
    
    "control_interval": 5.0,
    "speed_alpha": 0.3,
    
    "tg_token": "$tg_token",
    "tg_chat_id": "$tg_chat_id",
    "tg_enable_commands": true,
    
    "subscription_rss_url": "$sub_rss_url",
    "subscription_interval": $sub_interval,
    "subscription_category": "",
    "subscription_download_path": "",
    "subscription_paused": false,
    
    "cleanup_min_ratio": $cleanup_min_ratio,
    "cleanup_min_seeding_time": $cleanup_min_seeding,
    "cleanup_delete_files": $cleanup_delete_files,
    "cleanup_tracker_keyword": "",
    
    "u2_cookie": "",
    "u2_uid": "",
    
    "proxy": "",
    "log_level": "INFO",
    "db_path": "$INSTALL_DIR/qsl.db"
}
EOF
    
    chmod 600 "$CONFIG_FILE"
    log_info "配置文件已生成 ✓"
}

create_service() {
    log_step "创建系统服务..."
    
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=qBit Smart Limit - 智能限速控制器
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    
    log_info "系统服务已创建 ✓"
}

do_install() {
    print_header
    echo -e "${BOLD}开始安装 qBit Smart Limit${NC}"
    echo ""
    
    check_root
    check_dependencies
    install_files
    setup_venv
    create_config
    create_service
    
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}安装完成！${NC}"
    echo ""
    echo -e "启动服务: ${CYAN}systemctl start $SERVICE_NAME${NC}"
    echo -e "查看状态: ${CYAN}systemctl status $SERVICE_NAME${NC}"
    echo -e "查看日志: ${CYAN}journalctl -u $SERVICE_NAME -f${NC}"
    echo -e "配置文件: ${CYAN}$CONFIG_FILE${NC}"
    echo ""
    
    if confirm "是否立即启动服务？" "y"; then
        systemctl start "$SERVICE_NAME"
        log_info "服务已启动"
    fi
}

# ==================== 卸载函数 ====================
do_uninstall() {
    print_header
    echo -e "${BOLD}卸载 qBit Smart Limit${NC}"
    echo ""
    
    check_root
    
    if ! is_installed; then
        log_warn "qBit Smart Limit 未安装"
        return
    fi
    
    if ! confirm "确定要卸载 qBit Smart Limit 吗？"; then
        return
    fi
    
    # 停止服务
    if is_service_active; then
        log_step "停止服务..."
        systemctl stop "$SERVICE_NAME"
    fi
    
    # 禁用服务
    log_step "禁用服务..."
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    
    # 删除文件
    if confirm "是否删除配置文件和数据库？"; then
        rm -rf "$INSTALL_DIR"
        log_info "所有文件已删除"
    else
        rm -rf "$INSTALL_DIR/main.py" "$INSTALL_DIR/qsl" "$VENV_DIR"
        log_info "程序文件已删除，配置和数据已保留"
    fi
    
    echo ""
    log_info "卸载完成"
}

# ==================== 服务控制函数 ====================
service_start() {
    if ! is_installed; then
        log_error "qBit Smart Limit 未安装"
        return 1
    fi
    
    if is_service_active; then
        log_warn "服务已在运行"
        return
    fi
    
    systemctl start "$SERVICE_NAME"
    log_info "服务已启动"
}

service_stop() {
    if ! is_service_active; then
        log_warn "服务未运行"
        return
    fi
    
    systemctl stop "$SERVICE_NAME"
    log_info "服务已停止"
}

service_restart() {
    systemctl restart "$SERVICE_NAME"
    log_info "服务已重启"
}

service_status() {
    echo ""
    if is_service_active; then
        echo -e "服务状态: ${GREEN}运行中${NC}"
    else
        echo -e "服务状态: ${RED}已停止${NC}"
    fi
    echo ""
    systemctl status "$SERVICE_NAME" --no-pager 2>/dev/null || true
}

# ==================== 模块控制函数 ====================
module_menu() {
    while true; do
        print_header
        echo -e "${BOLD}模块管理${NC}"
        echo ""
        echo "  1) 订阅模块状态"
        echo "  2) 删种模块状态"
        echo "  3) 配置订阅模块"
        echo "  4) 配置删种模块"
        echo "  0) 返回主菜单"
        echo ""
        read -r -p "请选择: " choice
        
        case $choice in
            1) show_subscription_status ;;
            2) show_cleanup_status ;;
            3) config_subscription ;;
            4) config_cleanup ;;
            0) return ;;
            *) log_warn "无效选项" ;;
        esac
        
        echo ""
        read -r -p "按回车继续..."
    done
}

show_subscription_status() {
    echo ""
    echo -e "${BOLD}=== 订阅模块状态 ===${NC}"
    
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_error "配置文件不存在"
        return
    fi
    
    local rss_url=$(jq -r '.subscription_rss_url // ""' "$CONFIG_FILE")
    local interval=$(jq -r '.subscription_interval // 300' "$CONFIG_FILE")
    local category=$(jq -r '.subscription_category // ""' "$CONFIG_FILE")
    
    echo ""
    if [[ -n "$rss_url" ]]; then
        echo -e "状态: ${GREEN}已启用${NC}"
        echo "RSS URL: ${rss_url:0:50}..."
        echo "检查间隔: ${interval}秒"
        echo "分类: ${category:-未设置}"
    else
        echo -e "状态: ${YELLOW}未启用${NC}"
        echo "RSS URL 未配置"
    fi
}

show_cleanup_status() {
    echo ""
    echo -e "${BOLD}=== 删种模块状态 ===${NC}"
    
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_error "配置文件不存在"
        return
    fi
    
    local min_ratio=$(jq -r '.cleanup_min_ratio // 1.0' "$CONFIG_FILE")
    local min_seeding=$(jq -r '.cleanup_min_seeding_time // 86400' "$CONFIG_FILE")
    local delete_files=$(jq -r '.cleanup_delete_files // false' "$CONFIG_FILE")
    local tracker=$(jq -r '.cleanup_tracker_keyword // ""' "$CONFIG_FILE")
    
    echo ""
    if [[ "$min_ratio" != "0" || "$min_seeding" != "0" ]]; then
        echo -e "状态: ${GREEN}已配置${NC}"
        echo "最小分享率: $min_ratio"
        echo "最小做种时间: $((min_seeding / 3600))小时"
        echo "删除文件: $delete_files"
        echo "Tracker过滤: ${tracker:-无}"
    else
        echo -e "状态: ${YELLOW}未启用${NC}"
    fi
}

config_subscription() {
    echo ""
    echo -e "${BOLD}=== 配置订阅模块 ===${NC}"
    echo ""
    
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_error "配置文件不存在"
        return
    fi
    
    read -r -p "RSS 订阅 URL (留空禁用): " rss_url
    read -r -p "检查间隔 (秒) [300]: " interval
    interval=${interval:-300}
    read -r -p "种子分类: " category
    read -r -p "下载路径: " download_path
    
    # 更新配置
    local tmp=$(mktemp)
    jq --arg url "$rss_url" \
       --argjson int "$interval" \
       --arg cat "$category" \
       --arg path "$download_path" \
       '.subscription_rss_url = $url | 
        .subscription_interval = $int |
        .subscription_category = $cat |
        .subscription_download_path = $path' \
       "$CONFIG_FILE" > "$tmp" && mv "$tmp" "$CONFIG_FILE"
    
    chmod 600 "$CONFIG_FILE"
    log_info "订阅配置已更新"
    
    if is_service_active && confirm "是否重启服务以应用更改？"; then
        service_restart
    fi
}

config_cleanup() {
    echo ""
    echo -e "${BOLD}=== 配置删种模块 ===${NC}"
    echo ""
    
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_error "配置文件不存在"
        return
    fi
    
    read -r -p "最小分享率 [1.0]: " min_ratio
    min_ratio=${min_ratio:-1.0}
    
    read -r -p "最小做种时间 (小时) [24]: " min_hours
    min_hours=${min_hours:-24}
    min_seeding=$((min_hours * 3600))
    
    read -r -p "删除文件? [y/N]: " del_files
    [[ "$del_files" =~ ^[Yy]$ ]] && delete_files="true" || delete_files="false"
    
    read -r -p "Tracker关键词过滤 (可选): " tracker
    
    # 更新配置
    local tmp=$(mktemp)
    jq --argjson ratio "$min_ratio" \
       --argjson seed "$min_seeding" \
       --argjson del "$delete_files" \
       --arg track "$tracker" \
       '.cleanup_min_ratio = $ratio | 
        .cleanup_min_seeding_time = $seed |
        .cleanup_delete_files = $del |
        .cleanup_tracker_keyword = $track' \
       "$CONFIG_FILE" > "$tmp" && mv "$tmp" "$CONFIG_FILE"
    
    chmod 600 "$CONFIG_FILE"
    log_info "删种配置已更新"
    
    if is_service_active && confirm "是否重启服务以应用更改？"; then
        service_restart
    fi
}

# ==================== 日志查看 ====================
view_logs() {
    print_header
    echo -e "${BOLD}查看日志${NC}"
    echo ""
    echo "  1) 实时日志 (Ctrl+C 退出)"
    echo "  2) 最近 50 行"
    echo "  3) 最近 200 行"
    echo "  4) 今日日志"
    echo "  0) 返回"
    echo ""
    read -r -p "请选择: " choice
    
    case $choice in
        1) journalctl -u "$SERVICE_NAME" -f ;;
        2) journalctl -u "$SERVICE_NAME" -n 50 --no-pager ;;
        3) journalctl -u "$SERVICE_NAME" -n 200 --no-pager ;;
        4) journalctl -u "$SERVICE_NAME" --since today --no-pager ;;
        0) return ;;
    esac
    
    echo ""
    read -r -p "按回车继续..."
}

# ==================== 配置编辑 ====================
edit_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_error "配置文件不存在"
        return
    fi
    
    local editor="${EDITOR:-nano}"
    if command -v "$editor" &>/dev/null; then
        "$editor" "$CONFIG_FILE"
    elif command -v vim &>/dev/null; then
        vim "$CONFIG_FILE"
    elif command -v vi &>/dev/null; then
        vi "$CONFIG_FILE"
    else
        log_error "未找到文本编辑器"
        return
    fi
    
    if confirm "是否重启服务以应用更改？"; then
        service_restart
    fi
}

# ==================== 更新函数 ====================
do_update() {
    print_header
    echo -e "${BOLD}更新 qBit Smart Limit${NC}"
    echo ""
    
    check_root
    
    if ! is_installed; then
        log_error "qBit Smart Limit 未安装，请先安装"
        return
    fi
    
    log_step "备份当前配置..."
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
    
    log_step "更新程序文件..."
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    cp "$script_dir/main.py" "$INSTALL_DIR/"
    cp "$script_dir/qsl/"*.py "$INSTALL_DIR/qsl/"
    
    log_step "更新依赖..."
    "$VENV_DIR/bin/pip" install --upgrade qbittorrent-api requests
    
    if is_service_active; then
        log_step "重启服务..."
        systemctl restart "$SERVICE_NAME"
    fi
    
    echo ""
    log_info "更新完成！"
}

# ==================== 主菜单 ====================
main_menu() {
    while true; do
        print_header
        
        # 显示状态
        if is_installed; then
            echo -ne "安装状态: ${GREEN}已安装${NC}"
            if is_service_active; then
                echo -e "  |  服务状态: ${GREEN}运行中${NC}"
            else
                echo -e "  |  服务状态: ${RED}已停止${NC}"
            fi
        else
            echo -e "安装状态: ${YELLOW}未安装${NC}"
        fi
        echo ""
        
        echo -e "${BOLD}=== 安装/卸载 ===${NC}"
        echo "  1) 安装"
        echo "  2) 更新"
        echo "  3) 卸载"
        echo ""
        echo -e "${BOLD}=== 服务控制 ===${NC}"
        echo "  4) 启动服务"
        echo "  5) 停止服务"
        echo "  6) 重启服务"
        echo "  7) 服务状态"
        echo ""
        echo -e "${BOLD}=== 配置管理 ===${NC}"
        echo "  8) 模块管理"
        echo "  9) 编辑配置"
        echo "  10) 查看日志"
        echo ""
        echo "  0) 退出"
        echo ""
        read -r -p "请选择: " choice
        
        case $choice in
            1) do_install ;;
            2) do_update ;;
            3) do_uninstall ;;
            4) service_start ;;
            5) service_stop ;;
            6) service_restart ;;
            7) service_status ;;
            8) module_menu ;;
            9) edit_config ;;
            10) view_logs ;;
            0) echo "再见！"; exit 0 ;;
            *) log_warn "无效选项" ;;
        esac
        
        if [[ "$choice" != "10" && "$choice" != "8" ]]; then
            echo ""
            read -r -p "按回车继续..."
        fi
    done
}

# ==================== 命令行参数 ====================
case "${1:-}" in
    install)
        do_install
        ;;
    uninstall)
        do_uninstall
        ;;
    update)
        do_update
        ;;
    start)
        check_root
        service_start
        ;;
    stop)
        check_root
        service_stop
        ;;
    restart)
        check_root
        service_restart
        ;;
    status)
        service_status
        ;;
    logs)
        journalctl -u "$SERVICE_NAME" -f
        ;;
    ""|menu)
        check_root
        main_menu
        ;;
    *)
        echo "用法: $0 {install|uninstall|update|start|stop|restart|status|logs|menu}"
        exit 1
        ;;
esac
