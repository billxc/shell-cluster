# Shell Cluster

去中心化的跨机器远程 Shell 访问工具。不需要中心服务器。

**跨平台**（macOS / Windows / Linux）—— 每台机器运行一个轻量 daemon，节点通过共享的 tunnel 凭证自动发现彼此。从任何地方连接到任何机器的 shell，像 SSH 但不需要管理密钥或服务器。

[English](README.md)

## 工作原理

```
macOS (zsh)                Windows (PowerShell)         Linux (bash)
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│ daemon :8765 │          │ daemon :8765 │          │ daemon :8765 │
└──────┬───────┘          └──────┬───────┘          └──────┬───────┘
       │                         │                         │
  ═════╪═════════ Tunnel (P2P, 无服务器) ══════════════════╪═════
       │                         │                         │
  CLI / Web Dashboard (从任意机器、任意系统)
```

**没有中心服务器。** 每个节点地位平等。节点通过查询 tunnel 提供商的 API，筛选同账号下带有相同标签的 tunnel 来自动发现彼此。没有中继、没有协调器、没有单点故障。

## 平台支持

| 平台 | 服务端 (daemon) | 客户端 (connect) | Shell |
|------|:-:|:-:|---|
| macOS | Yes | Yes | zsh, bash, fish, ... |
| Windows | Yes | Yes | pwsh (PS 7+), PowerShell, cmd, Git Bash, ... |
| Linux | Yes | Yes | bash, zsh, fish, ... |

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 从源码安装
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv tool install .

# 或直接从 git 安装
uv tool install git+https://github.com/billxc/shell-cluster
```

macOS、Windows、Linux 使用相同的安装命令。

## 快速开始（本地模式）

不需要 tunnel，适合局域网或本机测试。

### 1. 启动 daemon

```bash
# 终端 1（比如你的 Mac）
shellcluster start --no-tunnel --name macbook --port 8765

# 终端 2（比如你的 Windows PC）
shellcluster start --no-tunnel --name windows-pc --port 8766
```

### 2. 连接

```bash
# 从任意机器
shellcluster connect ws://localhost:8765
```

你现在进入了远程 shell。输入 `exit` 或按 `~.`（新行后按波浪号再按点）断开。

### 3. Web Dashboard

在配置文件中添加 peers（`shellcluster register` 会创建配置文件）：

```toml
[[peers]]
name = "macbook"
uri = "ws://192.168.1.10:8765"

[[peers]]
name = "windows-pc"
uri = "ws://192.168.1.20:8766"
```

然后：

```bash
shellcluster dashboard
```

自动打开浏览器 —— 左侧显示所有节点，右侧是完整的 xterm.js 终端。点击节点即可打开 shell，支持多 tab 管理多个会话。

节点来源于**两个渠道**：配置文件 + devtunnel 自动发现，两者叠加。

## Tunnel 模式（跨网络）

适合不同网络的机器互连。目前支持 MS Dev Tunnel。

### 前置条件

在每台机器上安装 [Dev Tunnel CLI](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started)（支持 macOS、Windows、Linux），用 **同一个微软账号** 登录：

```bash
devtunnel user login
```

### 使用

```bash
# 在每台机器上：注册并启动
shellcluster register --name my-macbook
shellcluster start
```

这会自动：
- 创建一个带 `shellcluster` 标签的 Dev Tunnel
- 启动本地 WebSocket Shell 服务
- 通过 tunnel 暴露端口
- 发现同账号下的其他节点

```bash
# 查看节点
shellcluster peers

# 通过名称连接
shellcluster connect my-desktop
shellcluster connect my-desktop powershell    # 指定 shell 类型
```

## 为什么去中心化？

| | Shell Cluster | 传统方案（SSH + 跳板机） |
|---|---|---|
| 中心服务器 | 不需要 | 需要跳板机 |
| 密钥管理 | 不需要（tunnel 认证） | 每台机器都要配 SSH 密钥 |
| NAT 穿透 | tunnel 内置 | 需要端口转发 / VPN |
| 节点发现 | 自动 | 手动维护清单 |
| 单点故障 | 没有 | 跳板机挂了 = 全部断连 |
| 跨平台 | macOS + Windows + Linux | 各系统 SSH 服务配置不同 |

## 命令参考

| 命令 | 说明 |
|------|------|
| `shellcluster register` | 注册当前机器到 cluster |
| `shellcluster start` | 启动 daemon（tunnel + shell server + discovery） |
| `shellcluster start --no-tunnel` | 本地模式，不创建 tunnel |
| `shellcluster start --name X --port N` | 覆盖节点名和端口 |
| `shellcluster peers` | 列出已发现的节点 |
| `shellcluster connect <target>` | 连接到节点（名称或 `ws://host:port`） |
| `shellcluster connect <target> <shell>` | 连接并指定 shell 类型 |
| `shellcluster dashboard` | 打开 Web 管理面板（配置 peers + devtunnel 发现） |
| `-v` / `--verbose` | 开启调试日志 |

## 配置文件

| 系统 | 配置路径 |
|------|---------|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

```toml
[node]
name = "my-macbook"        # 节点名称，默认为 hostname
label = "shellcluster"     # tunnel 标签，用于节点发现
port = 8765                # WebSocket 服务端口

[tunnel]
backend = "devtunnel"      # tunnel 后端（目前仅 devtunnel）
expiration = "8h"          # tunnel 过期时间

[discovery]
interval_seconds = 30      # 节点发现刷新间隔
manual_peers = []          # 手动添加的节点 tunnel ID

[shell]
command = ""               # 默认 shell，留空则自动检测（Unix: $SHELL / Windows: %COMSPEC%）

# 本地/局域网模式的手动 peers（可选）
# [[peers]]
# name = "my-desktop"
# uri = "ws://192.168.1.20:8765"
```

## 开发

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv sync

# 本地测试：开两个终端
uv run shellcluster start --no-tunnel --name node-a --port 8765
uv run shellcluster start --no-tunnel --name node-b --port 8766

# 第三个终端连接
uv run shellcluster connect ws://localhost:8765
```

## Roadmap

- [x] macOS + Linux 支持（PTY）
- [x] Windows 支持（winpty/conpty）
- [x] 本地模式（无 tunnel）
- [x] MS Dev Tunnel 后端
- [ ] Cloudflare Tunnel 后端
- [ ] E2E 加密
- [x] Web Dashboard（xterm.js）
- [ ] 文件传输
- [ ] 与 [easy-service](https://github.com/billxc/easy-service) 集成，注册为系统服务

## License

MIT
