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

| 平台 | 服务端 (daemon) | 客户端 (dashboard) | Shell |
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

## 快速开始

### 1. 安装

```bash
uv tool install git+https://github.com/billxc/shell-cluster
```

### 2. 登录 Dev Tunnel（每台机器一次）

```bash
devtunnel user login
```

所有机器使用 **同一个微软账号**。

### 3. 注册并启动（每台机器）

```bash
shellcluster register --name my-macbook
shellcluster start
```

### 4. 打开 Dashboard（任意机器）

```bash
shellcluster dashboard
```

自动打开浏览器 —— 左侧显示所有发现的节点，右侧是完整的 xterm.js 终端。点击节点即可打开 shell，支持多 tab 管理多个会话。

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
| `shellcluster unregister` | 从 cluster 移除当前机器 |
| `shellcluster start` | 启动 daemon（tunnel + shell server + discovery） |
| `shellcluster peers` | 列出已发现的节点 |
| `shellcluster dashboard` | 打开 Web 管理面板 |
| `-v` / `--verbose` | 开启调试日志 |

## 配置文件

| 系统 | 配置路径 |
|------|---------|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

```toml
[node]
name = "my-macbook"        # 节点名称，显示在 peers 和 dashboard 中
label = "shellcluster"     # Tunnel 标签 —— 相同标签 = 同一个 cluster
port = 8765                # WebSocket 端口（仅本地模式）

[tunnel]
backend = "devtunnel"      # Tunnel 后端
expiration = "8h"          # Tunnel 自动过期时间

[discovery]
interval_seconds = 30      # 节点刷新间隔（秒）

[shell]
command = ""               # 默认 shell（留空 = 自动检测）
```

## 开发

架构详情见 [DESIGN_CN.md](DESIGN_CN.md)（[English](DESIGN.md)）。

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv sync
uv run shellcluster start --no-tunnel --name test --port 8765
```

## Roadmap

- [x] macOS + Linux 支持（PTY）
- [x] Windows 支持（winpty/conpty）
- [x] 本地模式（无 tunnel）
- [x] MS Dev Tunnel 后端
- [ ] E2E 加密
- [x] Web Dashboard（xterm.js）
- [ ] 文件传输
- [ ] 与 [easy-service](https://github.com/billxc/easy-service) 集成，注册为系统服务

## License

MIT
