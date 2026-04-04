# Shell Cluster

跨机器远程访问你所有的 Shell，基于 tunnel 技术，无需中心服务器。

## 原理

```
Machine A (daemon)                    Machine B (daemon)
┌─────────────────────┐              ┌─────────────────────┐
│  ShellManager       │              │  ShellManager       │
│  ├─ zsh #1          │              │  ├─ bash #1         │
│  ├─ bash #2         │              │  └─ zsh #2          │
│  WebSocket Server   │              │  WebSocket Server   │
└────────┬────────────┘              └────────┬────────────┘
         │ :8765                              │ :8765
    (tunnel / 直连)                      (tunnel / 直连)
         │                                    │
    ═════╪════════════════════════════════════╪═════
         │                                    │
    Dashboard TUI / CLI (任意机器)
```

每台机器运行一个 daemon，暴露一个 WebSocket 端口。多个 shell 会话在同一连接上多路复用。节点之间通过 tunnel（MS Dev Tunnel）或局域网直连互相访问。

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

安装后 `shellcluster` 命令即可用。

## 快速开始（本地模式）

不需要 tunnel，适合本机测试或局域网使用。

### 1. 启动 daemon

```bash
# 终端 1：启动节点 A
shellcluster start --no-tunnel --name node-a --port 8765

# 终端 2：启动节点 B
shellcluster start --no-tunnel --name node-b --port 8766
```

### 2. 连接到远程 shell

```bash
# 终端 3：直连 node-a
shellcluster connect ws://localhost:8765

# 或者连 node-b
shellcluster connect localhost:8766
```

连接后你会进入对端机器的 shell，就像 SSH 一样。

**断开连接：** 在新行按 `~.`（先按回车，再按波浪号，再按点）。

### 3. TUI Dashboard

```bash
shellcluster dashboard
```

可视化管理所有节点和 shell 会话。

## 通过 Tunnel 使用（跨网络）

适合跨网络、跨地域的机器互连。目前支持 MS Dev Tunnel。

### 前置条件

每台机器安装 [Dev Tunnel CLI](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started) 并登录**同一个微软账号**：

```bash
devtunnel user login
```

### 1. 注册节点

在每台机器上执行：

```bash
shellcluster register --name my-macbook
# 或自定义端口
shellcluster register --name my-macbook --port 9000
```

配置保存在 `~/.config/shell-cluster/config.toml`。

### 2. 启动 daemon

```bash
shellcluster start
```

这会自动：
- 创建一个带 `shellcluster` 标签的 Dev Tunnel
- 在本地端口启动 WebSocket Shell 服务
- 通过 tunnel 暴露该端口
- 开始定期发现同账号下的其他节点

### 3. 查看节点

```bash
shellcluster peers
```

输出示例：
```
┌──────────────────────────────────────────────────────────┐
│ Peers                                                    │
├──────────┬─────────────────────────────┬────────┬───────┤
│ Name     │ Tunnel ID                   │ Status │ URI   │
├──────────┼─────────────────────────────┼────────┼───────┤
│ desktop  │ shellcluster-desktop.usw2   │ online │ ...   │
│ server   │ shellcluster-server.jpe1    │ online │ ...   │
└──────────┴─────────────────────────────┴────────┴───────┘
```

### 4. 连接

```bash
# 通过节点名连接
shellcluster connect desktop

# 指定 shell 类型
shellcluster connect desktop bash
```

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
| `shellcluster dashboard` | 打开 TUI 管理面板 |
| `-v` / `--verbose` | 开启调试日志 |

## 配置文件

路径：`~/.config/shell-cluster/config.toml`

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
command = ""               # 默认 shell，留空则使用 $SHELL
```

## 项目结构

```
src/shell_cluster/
  cli.py              # CLI 入口
  config.py            # 配置管理
  models.py            # 数据模型
  protocol.py          # WebSocket 通信协议
  daemon.py            # Daemon 编排器
  server.py            # WebSocket Shell 服务端
  client.py            # WebSocket 客户端
  shell_manager.py     # 本机 PTY 多会话管理
  discovery.py         # 节点发现
  tunnel/
    base.py            # Tunnel 抽象接口
    devtunnel.py       # MS Dev Tunnel 实现
  tui/
    app.py             # Textual TUI 应用
    widgets/
      session_list.py  # 会话列表组件
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

- [ ] Cloudflare Tunnel 后端
- [ ] E2E 加密
- [ ] Windows 支持（conpty）
- [ ] HTML Web UI
- [ ] 文件传输
- [ ] 与 [easy-service](https://github.com/billxc/easy-service) 集成，注册为系统服务

## License

MIT
