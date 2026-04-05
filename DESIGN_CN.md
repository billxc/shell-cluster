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
└── web/                 # ── Web Dashboard ──
    ├── __init__.py
    ├── server.py         # HTTP + WebSocket 代理服务器
    └── static/
        └── index.html    # xterm.js 单页 Dashboard
```

### 分层设计

| 层 | 模块 | 职责 |
|----|------|------|
| **Shell** | `shell/manager.py`, `shell/server.py` | PTY、WebSocket、协议 |
| **Tunnel** | `tunnel/base.py`, `tunnel/devtunnel.py`, `tunnel/discovery.py` | Tunnel 生命周期、节点发现 |
| **Web** | `web/server.py`, `web/static/` | HTTP、WebSocket 代理 |
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

### 2. Shell Server（`shell/server.py`）
- WebSocket 服务端（websockets 库，`0.0.0.0:<端口>`）
- JSON 协议，终端数据 base64 编码
- 客户端断连后会话保持（支持重连）
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
- 通过 `parse_node_name()` 从 tunnel ID 提取节点名
- 在 daemon 内以定时循环运行

### 6. Daemon（`daemon.py`）
编排所有组件：
```
start:
  1. 绑定 WebSocket 服务（tunnel 模式随机端口，本地模式固定端口）
  2. ensure_tunnel() + host()（仅 tunnel 模式）
  3. 启动 discovery 循环（仅 tunnel 模式）
  4. 注册 atexit 清理子进程

stop:
  1. 停止 discovery
  2. 停止 WebSocket 服务
  3. 杀掉 devtunnel host 进程
  4. 保留 tunnel（依赖过期机制清理）
```

### 7. Web Dashboard（`web/server.py`, `web/static/index.html`）
- Python: HTTP 服务（提供 HTML）+ WebSocket 代理
- HTML: 单页应用，xterm.js，Catppuccin 主题
- 连接流程: 浏览器 → WS 代理 (:9000) → 初始化消息 → 代理连接 peer → 双向转发
- 浏览器刷新后通过 `shell.attach` 恢复会话
- 页面加载时查询每个 peer 的会话列表，显示可重连的会话

### 8. 配置（`config.py`）
TOML 配置文件，平台特定路径：

| 系统 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

配置节: `[node]`, `[tunnel]`, `[discovery]`, `[shell]`, `[[peers]]`

## CLI 命令

| 命令 | 说明 |
|------|------|
| `shellcluster register --name X` | 保存节点配置 |
| `shellcluster unregister` | 删除 tunnel + 配置文件 |
| `shellcluster start` | 启动 daemon（tunnel 模式） |
| `shellcluster start --no-tunnel` | 启动 daemon（本地模式） |
| `shellcluster peers` | 列出已发现的节点 |
| `shellcluster dashboard` | 打开 Web Dashboard |

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
页面加载 → 查询每个 peer 的会话列表
        → 在侧边栏显示远程会话（↻ 图标）
        → 用户点击 → shell.attach → 重新绑定回调 → 恢复
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
- [ ] Cloudflare Tunnel 后端
- [ ] E2E 加密
- [ ] 文件传输
- [ ] 系统服务集成（easy-service）
