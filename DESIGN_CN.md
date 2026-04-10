# Shell Cluster — 设计文档

## 概述

Shell Cluster 是一个去中心化的跨平台远程 shell 工具。每台机器运行一个轻量 daemon，通过 WebSocket 暴露本地 shell。节点通过共享的 tunnel 提供商（MS Dev Tunnel）自动发现彼此 —— 不需要中心服务器、不需要 SSH 密钥、不需要端口转发。

用户通过 **Web Dashboard**（xterm.js）在浏览器中操作真实的终端会话。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                        机器 A                                │
│                                                             │
│  shell/manager         shell/server        DevTunnel        │
│  ├─ PTY 会话 1          WebSocket :随机端口  ├─ create        │
│  ├─ PTY 会话 2          (JSON 协议)         ├─ host          │
│  └─ PTY 会话 N                              └─ discovery     │
│                                                             │
│  Tunnel ID: shellcluster-<名称>-shellcluster                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    devtunnel host（出站连接到微软云）
                           │
                ═══════════╪═══════════════════════
                           │
                    devtunnel connect（客户端机器）
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                      客户端机器                               │
│                                                             │
│  Dashboard Server (:9000)                                   │
│  ├─ HTTP: 提供 index.html (xterm.js)                        │
│  ├─ WebSocket 代理: 浏览器 ↔ peer（通过 localhost）           │
│  └─ Tunnel 连接: devtunnel connect → localhost:<端口>        │
│                                                             │
│  浏览器                                                      │
│  ├─ 左侧边栏: 节点列表 + 会话列表                              │
│  └─ 右侧面板: xterm.js 终端（标签页）                          │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```
src/shell_cluster/
├── __init__.py          # 版本信息
├── cli.py               # CLI 入口 (click)
├── config.py            # 配置管理 (~/.config/shell-cluster/)
├── daemon.py            # Daemon 编排器
├── models.py            # 数据模型 (Peer, TunnelInfo, ShellSession)
├── protocol.py          # WebSocket JSON 协议
├── shell/               # ── Shell 服务层 ──
│   ├── __init__.py
│   ├── manager.py       # PTY 会话管理（跨平台）
│   └── server.py        # WebSocket Shell 服务端
├── tunnel/              # ── Tunnel 传输层 ──
│   ├── __init__.py
│   ├── base.py          # 抽象后端 + 命名工具
│   ├── devtunnel.py     # MS Dev Tunnel 实现
│   └── discovery.py     # 通过 tunnel API 发现节点
├── web/                 # ── Web Dashboard（API + WS 代理，端口 9000）──
│   ├── __init__.py
│   ├── server.py         # HTTP + WebSocket 代理服务器
│   └── static/
│       └── index.html    # xterm.js 单页 Dashboard（v1）
└── dashboard_v2/        # ── Dashboard v2（静态 UI，端口 9001）──
    ├── __init__.py
    ├── __main__.py       # 模块入口
    ├── server.py         # 静态文件 HTTP 服务（子进程）
    └── static/
        └── ...           # 新版 Dashboard UI 资源
```

### 分层设计

| 层 | 模块 | 职责 |
|----|------|------|
| **Shell** | `shell/manager.py`, `shell/server.py` | PTY、WebSocket、协议 |
| **Tunnel** | `tunnel/base.py`, `tunnel/devtunnel.py`, `tunnel/discovery.py` | Tunnel 生命周期、节点发现 |
| **Web** | `web/server.py`, `web/static/` | HTTP API、WebSocket 代理（端口 9000） |
| **Dashboard v2** | `dashboard_v2/server.py`, `dashboard_v2/static/` | 静态 UI 服务（端口 9001，子进程） |
| **编排** | `daemon.py`, `cli.py` | 组合两层（组合根） |
| **共享** | `models.py`, `protocol.py`, `config.py` | 纯数据结构 |

Shell 层和 Tunnel 层 **零交叉导入**。

## 组件

### 1. Shell Manager（`shell/manager.py`）
- **Unix**: `pty.openpty()` + `os.fork()` + `os.execvpe()`
- **Windows**: `winpty.PtyProcess.spawn()`
- 管理多个并发 PTY 会话
- 在 executor 线程中读取 → 异步回调输出/退出
- `attach()`: 重新绑定回调，支持浏览器刷新后重连
- **滚动缓冲区**: 每个会话 64KB 环形缓冲区（`deque`）；`shell.attach` 时回放，实现无缝重连

### 2. Shell Server（`shell/server.py`）
- WebSocket 服务端（websockets 库，`127.0.0.1:<端口>`）
- JSON 协议，终端数据 base64 编码
- HTTP 端点: `GET /sessions` 返回会话列表（供健康检查和前端使用）
- 客户端断连后会话保持（支持重连）
- `shell.attach` 时回放滚动缓冲区，并过滤终端查询序列
- 分发: `shell.create`, `shell.attach`, `shell.data`, `shell.resize`, `shell.close`, `shell.list`

### 3. 协议（`protocol.py`）
所有消息都是 JSON 文本帧：

| 类型 | 方向 | 用途 |
|------|------|------|
| `peer.info` | 服务端→客户端 | 连接时发送节点名 + 会话列表 |
| `shell.create` | 客户端→服务端 | 创建新 PTY 会话 |
| `shell.created` | 服务端→客户端 | 会话创建确认 |
| `shell.attach` | 客户端→服务端 | 重新接入已有会话 |
| `shell.attached` | 服务端→客户端 | 接入确认 |
| `shell.data` | 双向 | 终端数据（base64） |
| `shell.resize` | 客户端→服务端 | 终端大小变更 |
| `shell.close` | 客户端→服务端 | 关闭会话 |
| `shell.closed` | 服务端→客户端 | 会话已结束 |
| `shell.list` | 客户端→服务端 | 列出活跃会话 |
| `shell.list.response` | 服务端→客户端 | 会话列表 |
| `error` | 服务端→客户端 | 错误信息 |

### 4. Tunnel 后端（`tunnel/base.py`, `tunnel/devtunnel.py`）
抽象 `TunnelBackend` 协议，具体实现 `DevTunnelBackend`：

| 方法 | 用途 |
|------|------|
| `create()` | 创建 tunnel + 添加端口 |
| `ensure_tunnel()` | 复用已有或创建新的（处理端口变更） |
| `host()` | 启动 `devtunnel host` 子进程 |
| `connect()` | 启动 `devtunnel connect` 本地端口映射 |
| `list_tunnels()` | 按标签筛选 tunnel 列表 |
| `get_port_and_uri()` | 从 `show --json` 获取端口 + 转发 URI |
| `exists()` | 检查 tunnel 是否存在 |
| `delete()` | 删除 tunnel |

**Tunnel ID 格式**: `shellcluster-<节点名>-shellcluster`
- devtunnel 会追加区域后缀: `shellcluster-my-mac-shellcluster.jpe1`
- `parse_node_name()` 去除前后缀 + 区域后缀，提取节点名

**Tunnel 生命周期**:
- `start`: 服务端绑定随机端口 → `ensure_tunnel()`（复用或创建）→ `host()`
- `stop`: 杀掉 host 进程，保留 tunnel（有过期时间）
- 下次 `start`: 复用 tunnel，更新端口（如果变了），重新 host

### 5. 节点发现（`tunnel/discovery.py`）
- 调用 `backend.list_tunnels(label)` 查找节点
- 过滤 `hostConnections > 0`（只显示有 host 在运行的 tunnel）
- 对每个新节点调用 `backend.get_port_and_uri()` 获取端口信息
- 检测已在线节点的端口变化（两次发现周期间节点重启）
- 通过 `parse_node_name()` 从 tunnel ID 提取节点名
- 在 daemon 内以定时循环运行（5 分钟间隔）

### 6. Daemon（`daemon.py`）
编排所有组件：
```
start:
  1. 快速失败：检查端口 9000 和 9001 是否可用
  2. 检查 devtunnel 是否安装和登录（tunnel 模式）
  3. 如果没有配置文件，自动注册（提示输入节点名）
  4. 绑定 WebSocket 服务（tunnel 模式随机端口，本地模式固定端口）
  5. ensure_tunnel() + host()（仅 tunnel 模式）
  6. 启动 discovery 循环（5 分钟间隔，仅 tunnel 模式）
  7. 启动健康检查循环（10 秒 HTTP ping 各节点的 /sessions）
  8. 启动 Dashboard API + WS 代理服务 (:9000)
  9. 启动 Dashboard v2 UI 服务 (:9001) 作为子进程（除非 --no-dashboard）
  10. 注册 atexit 清理子进程

stop:
  1. 停止 discovery + 健康检查
  2. 杀掉 Dashboard v2 子进程
  3. 停止 Dashboard API 服务
  4. 停止 WebSocket 服务
  5. 杀掉 devtunnel connect 进程（节点连接）
  6. 杀掉 devtunnel host 进程
  7. 保留 tunnel（依赖过期机制清理）
```

**健康检查循环**: 每 10 秒 HTTP GET 各连接节点的 `/sessions` 端点。当节点不可达时，杀掉对应的 `devtunnel connect` 进程，确保下次 discovery 刷新时能重新建立连接。

### 7. Web Dashboard

Dashboard 由两个服务组成，均由 daemon 管理：

**端口 9000 — API + WebSocket 代理**（`web/server.py`）
- HTTP 端点: `/api/peers`, `/api/refresh-peers`
- WebSocket 代理: 浏览器 ↔ peer shell 服务（通过 localhost 映射端口）
- 在 `/` 提供旧版 v1 HTML dashboard
- 随 daemon 一起启动

**端口 9001 — Dashboard v2 UI**（`dashboard_v2/server.py`）
- daemon 以子进程方式启动的静态文件服务
- 前端直接连接 peer 的 `/raw` WebSocket 端点进行终端会话
- 通过 `/api/peers` 与端口 9000 通信获取节点列表
- 使用 `--no-dashboard` 参数可跳过启动
- 可独立运行: `shell-dashboard` 或 `python -m shell_cluster.dashboard_v2`

**连接流程:**
- 浏览器 → Dashboard v2 (:9001) → 从 API (:9000) 获取节点列表 → 直连 peer WS
- 浏览器刷新后通过 `shell.attach` 恢复会话 + 滚动缓冲区回放
- 前端直接并发查询每个 peer 的 `/sessions` HTTP 端点（3 秒超时）
- **Discover** 按钮触发即时 tunnel API 刷新（带确认对话框）
- 自动刷新节点列表和会话（30 秒）

### 8. 配置（`config.py`）
TOML 配置文件，平台特定路径：

| 系统 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

配置节: `[node]`, `[tunnel]`, `[shell]`, `[[peers]]`

### 配置字段说明

```toml
[node]
name = "my-macbook"        # 节点名称，显示在 peers 列表和 dashboard 中
# 默认为本机 hostname
label = "shellcluster"     # Tunnel 标签 —— 相同标签的节点互相发现
dashboard_port = 9000      # API + WebSocket 代理端口
dashboard_v2_port = 9001   # Dashboard v2 UI 端口
dashboard = true           # daemon 启动时是否启动 Dashboard v2

[tunnel]
backend = "devtunnel"      # Tunnel 后端（目前仅 "devtunnel"）
expiration = "30d"          # Tunnel 过期时间，到期后云端自动清理

[shell]
command = ""               # 默认 shell，留空 = 自动检测
                           # Unix: $SHELL → /bin/sh
                           # Windows: pwsh → powershell → cmd

# 手动 peers，用于局域网/直连（可选，与 tunnel 发现叠加）
# [[peers]]
# name = "my-desktop"
# uri = "ws://192.168.1.20:8765"
```

## CLI 命令

### `shellcluster register --name <名称>`
1. 加载或创建配置文件
2. 保存节点名、端口、标签、后端到配置
3. 打印确认信息

### `shellcluster unregister`
1. 加载配置，从节点名推导 tunnel ID
2. 调用 `backend.delete(tunnel_id)` 从云端删除 tunnel
3. 删除本地配置文件

### `shellcluster start`
1. 快速失败: 检查端口 9000 和 9001 是否可用
2. 检查 `devtunnel` 是否安装和登录（仅 tunnel 模式）
3. 如果没有配置文件，自动注册（提示输入节点名）
4. 加载配置
5. 创建 `ShellManager`（使用默认 shell）
6. 创建 `ShellServer`（端口 0 = 系统随机分配）
7. 启动 WebSocket 服务 → 获取实际端口
8. `ensure_tunnel()` — 检查 tunnel 是否存在，不存在则创建，端口变了则更新
9. `devtunnel host` — 启动 tunnel host 子进程
10. 启动 `PeerDiscovery` 循环（5 分钟间隔）
11. 启动健康检查循环（10 秒 HTTP ping）
12. 启动 Dashboard API + WS 代理服务 (:9000)
13. 启动 Dashboard v2 UI 服务 (:9001) 作为子进程（除非 `--no-dashboard`）
14. 永久等待（直到 Ctrl+C 或 host 进程退出）

### `shellcluster start --no-tunnel`
同上但跳过步骤 2、8-11。服务绑定指定的固定端口而非随机端口。

### `shellcluster peers`
1. 创建 `PeerDiscovery`（devtunnel 后端）
2. 调用 `discovery.refresh()` → `list_tunnels(label)` → 过滤 `hostConnections > 0`
3. 对每个节点: `get_port_and_uri()` 通过 `devtunnel show --json`
4. 用 Rich 表格打印（名称、tunnel ID、状态、URI）

### `shellcluster dashboard`
1. 加载配置获取 dashboard 端口
2. 打开浏览器到 `http://127.0.0.1:<dashboard_port>`

注意: Dashboard 由运行中的 daemon 提供服务。请确保 `shellcluster start` 已在运行。

## 连接流程

### 服务端（tunnel 模式）
```
register → 保存配置
start → 服务端 :随机端口 → ensure_tunnel → host
           (端口 52992)     (复用/创建)     (出站到微软云)
```

### 客户端
```
dashboard
  → discovery: 列出 tunnels → 找到节点
  → 每个节点: devtunnel connect → localhost:<相同端口>
  → 启动 HTTP 服务 :9000 → 打开浏览器
  → 用户点击节点 → WS 代理 → localhost:<映射端口> → tunnel → peer
```

### 会话重连（浏览器刷新）
```
页面加载 → 并发查询每个 peer 的 /sessions HTTP 端点
        → 在侧边栏显示远程会话（↻ 图标）
        → 用户点击 → shell.attach → 滚动缓冲区回放 (64KB) → 恢复
```

## 设计决策

1. **Tunnel 模式随机端口** — 避免端口冲突，tunnel 层处理映射
2. **`devtunnel connect` 而非直连 wss://** — 正确的层分离，所有 WS 走 localhost
3. **断连后会话保持** — 允许浏览器刷新不丢失状态
4. **无中心服务器** — 节点通过同账号下共享标签的 tunnel 互相发现
5. **Web Dashboard 而非 TUI** — xterm.js 终端模拟更好，跨平台渲染一致
6. **Tunnel 复用** — `ensure_tunnel()` 避免 daemon 重启时昂贵的重建
7. **`shellcluster-<名称>-shellcluster` 命名** — 可靠的节点名提取
8. **Shell/Tunnel 层分离** — 零交叉导入，随时可加新后端
9. **服务端健康检查** — daemon 通过 HTTP 检查节点状态，而非前端；发现不可达时杀掉旧连接确保干净重连
10. **滚动缓冲区回放 + 查询序列过滤** — 回放前移除终端 DA/DSR 查询序列，防止回显乱码

## 依赖

| 包 | 用途 |
|----|------|
| `click` | CLI |
| `websockets` | WebSocket 服务 + 代理 |
| `platformdirs` | 配置目录 |
| `tomli-w` | TOML 写入 |
| `rich` | 终端美化输出 |
| `pywinpty` | Windows PTY（条件依赖） |

## 路线图

- [x] macOS + Linux PTY 支持
- [x] Windows PTY 支持（winpty）
- [x] 本地模式（无 tunnel）
- [x] MS Dev Tunnel 后端
- [x] Web Dashboard（xterm.js）
- [x] 会话持久化（shell.attach）
- [x] 重连时滚动缓冲区回放（64KB 环形缓冲区）
- [x] 服务端健康检查（HTTP ping）
- [x] 首次启动自动注册
- [x] 系统服务集成（easy-service）
- [ ] E2E 加密
- [ ] 文件传输
