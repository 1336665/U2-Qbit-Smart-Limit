# qBit Smart Limit

智能 qBittorrent 上传限速控制器，基于 PID 控制和 Kalman 滤波实现平滑限速。

## 功能特性

### 核心功能
- **PID 智能限速**: 根据缓冲区大小自动调节上传速度
- **Kalman 滤波**: 平滑速度测量，避免抖动
- **状态持久化**: SQLite 数据库保存运行状态
- **Telegram 双向交互**: 通知 + 远程命令控制

### 扩展模块
- **订阅模块**: RSS 订阅自动添加新种子
- **删种模块**: 仅按“剩余空间 + 上传速度”规则自动清理（默认启用空间规则）

## 项目结构

```
qsl_refactored/
├── main.py              # 主入口，Controller 类整合所有模块
├── install.sh           # 安装器和遥控器
├── config.example.json  # 配置文件示例
├── qsl/                 # 模块包
│   ├── __init__.py      # 包导出
│   ├── utils.py         # 工具函数、常量、日志
│   ├── config.py        # 配置管理
│   ├── database.py      # SQLite 持久化
│   ├── telegram.py      # Telegram 交互
│   ├── u2_helper.py     # U2 站点辅助
│   ├── core.py          # 核心控制器 (PID/Kalman/状态管理)
│   ├── subscription.py  # 订阅模块
│   └── cleanup.py       # 删种模块
```

## 安装

### 快速安装
```bash
chmod +x install.sh
sudo ./install.sh
```

### 手动安装
```bash
# 1. 安装依赖
pip install qbittorrent-api requests

# 2. 复制配置文件
cp config.example.json config.json

# 3. 编辑配置
nano config.json

# 4. 运行
python main.py
```

## 配置说明

### 基础配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `qb_host` | qBittorrent 地址 | `127.0.0.1` |
| `qb_port` | qBittorrent 端口 | `8080` |
| `qb_username` | 用户名 | `admin` |
| `qb_password` | 密码 | - |

### 限速配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `max_upload_speed` | 最大上传速度 (字节/秒) | `20971520` (20MB/s) |
| `min_upload_speed` | 最小上传速度 (字节/秒) | `1048576` (1MB/s) |
| `target_buffer_size` | 目标缓冲区大小 (字节) | `209715200` (200MB) |

### PID 参数
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `pid_kp` | 比例系数 | `0.5` |
| `pid_ki` | 积分系数 | `0.1` |
| `pid_kd` | 微分系数 | `0.05` |

### Telegram 配置
| 参数 | 说明 |
|------|------|
| `tg_token` | Bot Token (从 @BotFather 获取) |
| `tg_chat_id` | Chat ID (从 @userinfobot 获取) |
| `tg_enable_commands` | 是否启用命令 |

### 订阅模块配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `subscription_rss_url` | RSS 订阅 URL | - |
| `subscription_interval` | 检查间隔 (秒) | `300` |
| `subscription_category` | 种子分类 | - |
| `subscription_download_path` | 下载路径 | - |
| `subscription_paused` | 添加后暂停 | `false` |

### 删种模块配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `cleanup_enabled` | 是否启用自动删种模块 | `false` |
| `cleanup_interval` | 检查间隔 (秒) | `300` |
| `cleanup_delete_files` | 是否删除文件 | `false` |
| `cleanup_tracker_keyword` | Tracker 关键词过滤（可选） | - |
| `cleanup_reannounce_before_delete` | 删除前强制汇报一次 | `true` |
| `cleanup_reannounce_wait` | 汇报后等待秒数 | `5` |
| `cleanup_space_rule1_gb` | 规则1：剩余空间阈值 (GB) | `10` |
| `cleanup_space_rule1_upload_kib` | 规则1：上传速度阈值 (KiB/s) | `1024` |
| `cleanup_space_rule1_download_kib` | 规则1：下载速度阈值 (KiB/s) | `1024` |
| `cleanup_space_rule2_gb` | 规则2：剩余空间阈值 (GB) | `20` |
| `cleanup_space_rule2_upload_kib` | 规则2：上传速度阈值 (KiB/s) | `512` |
| `cleanup_space_rule2_download_kib` | 规则2：下载速度阈值 (KiB/s) | `512` |
| `cleanup_space_rule3_gb` | 规则3：剩余空间阈值 (GB, 紧急) | `5` |
| `cleanup_space_rule3_upload_kib` | 规则3：上传速度阈值 (KiB/s, 紧急) | `5120` |
| `cleanup_space_rule3_download_kib` | 规则3：下载速度阈值 (KiB/s, 紧急) | `5120` |

**空间检测说明**：默认优先使用 qBittorrent WebAPI 上报的 `free_space_on_disk`（由 qB 所在机器计算），因此脚本可以部署在另一台机器上也不会误判空间；仅当无法读取时才回退使用脚本所在机器的磁盘空间。

空间规则（默认启用，优先级：规则3 > 规则1 > 规则2）：

- **上传规则**：仅对做种/上传状态生效（seeding / stalledUP 等）
- **下载规则**：仅对下载中状态生效（downloading / stalledDL 等）
- **安全**：queued/paused 等等待任务不会自动删除
- **执行方式**：每次只删除 1 个 → 等待 30 秒 → 重新检测空间，不够则继续循环

上传规则（与之前一致）：
1) 剩余<10G 且 上传<1024KiB/s → 删除
2) 剩余<20G 且 已完成 且 上传<512KiB/s → 删除
3) 剩余<5G 且 上传<5120KiB/s → 删除（紧急，优先级最高）

下载规则（新增）：
1) 剩余<10G 且 下载<1024KiB/s → 删除
2) 剩余<20G 且 下载<512KiB/s → 删除
3) 剩余<5G 且 下载<5120KiB/s → 删除（紧急）

## Telegram 命令

### 基础命令
- `/status` - 查看运行状态
- `/speed` - 查看当前速度
- `/torrents` - 查看种子列表
- `/help` - 帮助信息

### 订阅模块
- `/sub status` - 订阅状态
- `/sub start` - 启动订阅
- `/sub stop` - 停止订阅

### 删种模块
- `/cleanup status` - 删种状态
- `/cleanup start` - 启动删种
- `/cleanup stop` - 停止删种

## 任务文件

### 订阅任务文件 (`subscription_tasks.json`)
```json
[
  {
    "url": "https://example.com/torrent.torrent",
    "name": "种子名称",
    "category": "movies",
    "download_path": "/downloads",
    "paused": false
  }
]
```

### 删种任务文件 (`cleanup_tasks.json`)
```json
[
  {
    "action": "delete",
    "hash": "abc123...",
    "delete_files": true,
    "reason": "手动删除"
  },
  {
    "action": "protect",
    "hash": "def456..."
  },
  {
    "action": "unprotect",
    "hash": "def456..."
  }
]
```

## 服务管理

```bash
# 使用安装器
sudo ./install.sh menu

# 使用 systemctl
sudo systemctl start qsl
sudo systemctl stop qsl
sudo systemctl restart qsl
sudo systemctl status qsl

# 查看日志
journalctl -u qsl -f
```

## 开发说明

### 模块职责
- **utils.py**: 常量定义、工具函数、日志系统
- **config.py**: 配置加载、验证、热重载
- **database.py**: SQLite 操作封装
- **telegram.py**: Bot 交互、命令处理
- **core.py**: PID 控制器、Kalman 滤波、种子状态管理
- **subscription.py**: RSS 解析、任务文件处理、种子添加
- **cleanup.py**: 空间规则检查、种子删除

### 添加新模块
1. 在 `qsl/` 创建新模块文件
2. 在 `__init__.py` 添加导出
3. 在 `main.py` 的 `Controller` 类中集成
4. 在 `telegram.py` 添加相关命令 (可选)

## License

MIT License
