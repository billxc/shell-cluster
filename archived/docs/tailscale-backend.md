# Tailscale 后端 — 设计与实现

## 概述

Tailscale 后端是 shell-cluster 的第二种隧道方案，与现有的 DevTunnel 后端并存，用户通过配置文件选择使用哪种。

### 为什么选择 Tailscale

之前调研过 Cloudflare Tunnel（见 `cloudflare-tunnel-postmortem.md`），因为必须绑定域名、认证复杂等原因放弃。Tailscale 解决了这些问题：

- **无需域名** — 每台设备自动分配 `100.x.y.z` 私有 IP
- **内置认证** — 加入 tailnet 即完成认证
- **Peer 发现有 API** — `tailscale status --json` 返回所有设备及在线状态
- **跨平台** — macOS / Linux / Windows 全支持
- **免费** — 个人使用 100 台设备免费

### Userspace Networking 模式

shell-cluster 使用 Tailscale 的 userspace networking 模式：

```bash
tailscaled --tun=userspace-networking
```

- **无需 sudo** — 不创建 TUN 虚拟网卡，不修改系统路由
- **不影响正常网络** — 系统流量完全不受影响
- **连接方式** — 必须通过 `tailscale nc` 转发，不能直接访问 100.x.y.z

## 与 DevTunnel 的差异

| 维度 | DevTunnel | Tailscale |
|------|-----------|-----------|
| host() | `devtunnel host` 常驻子进程 | 无需（返回 None），tailscaled 管理连通性 |
| connect() | `devtunnel connect` 常驻子进程 | TCP 代理 + `tailscale nc` 管道 |
| create/delete | 管理云端 tunnel 资源 | 无需（no-op） |
| list_tunnels() | `devtunnel list --labels` | `tailscale status --json` |
| ensure_tunnel() | 检查/创建 tunnel | 检查 tailscale 连接状态 |
| 端口 | 随机端口，存在 tunnel 元数据中 | 固定端口（配置文件指定） |
| Peer 识别 | tunnel label 过滤 | tailnet 内所有在线设备 |
| 中继 | 全部走 Microsoft Cloud | P2P 优先，DERP 中继兜底 |
| 依赖 | `devtunnel` CLI + MS 账号 | `tailscale` CLI + tailnet 账号 |

## 架构

### 连接流程

```
Host 端 (Machine A)                          Client 端 (Machine B)
┌──────────────────────┐                     ┌──────────────────────────────┐
│ ShellServer          │                     │ Daemon                       │
│   ws://127.0.0.1:9876│                     │                              │
└──────────┬───────────┘                     │  TailscaleBackend.connect()  │
           │                                 │   └─ tailscale_proxy.py      │
   tailscaled 接收入站                        │       localhost:随机 ──┐     │
   连接并转发到 localhost:9876                │                       │     │
           │                                 │       tailscale nc     │     │
      ═════╪═══════════════════              │       100.x.y.z 9876 ◄┘     │
           │    Tailscale Mesh               └────────────┬────────────────┘
           │    (P2P 或 DERP)                             │
      ═════╪═══════════════════                    tailscaled 发起出站
           └──────────────────────────────────────────────┘
```

### 发现流程

```
Daemon 启动
  │
  ├─ ensure_tunnel()
  │    └─ tailscale status --json → 检查 BackendState == "Running"
  │
  ├─ host() → 返回 None（无需 host 进程）
  │
  └─ PeerDiscovery.refresh()
       └─ list_tunnels()
            └─ tailscale status --json
                 ├─ 解析 Peer 段，跳过 Self
                 ├─ 过滤 Online == true
                 └─ 返回 TunnelInfo(tunnel_id=hostname, port=固定端口)

每个新发现的 peer:
  └─ connect(hostname, port)
       ├─ 查找 hostname → IP 映射
       ├─ 启动 tailscale_proxy.py 子进程
       ├─ 读取 stdout 获取本地监听端口
       └─ 返回 (proc, ws://localhost:本地端口)
```

## TCP 代理设计（tailscale_proxy.py）

因为 userspace networking 模式下无法直接访问 Tailscale IP，需要一个本地 TCP 代理做桥接。

### 工作原理

`tailscale_proxy.py` 是独立脚本，由 `TailscaleBackend.connect()` 作为子进程启动：

```bash
python -m shell_cluster.tunnel.tailscale_proxy --peer-ip 100.64.0.2 --peer-port 9876
```

1. 监听 `127.0.0.1:0`（随机端口）
2. 打印 `LISTENING:<port>` 到 stdout，通知父进程
3. 接受 TCP 连接
4. 对每个连接，启动 `tailscale nc <ip> <port>` 并双向管道传输数据
5. 连接断开时清理 `tailscale nc` 子进程

### 数据流

```
Browser/Dashboard
  │
  │  ws://localhost:54321
  ▼
tailscale_proxy (TCP 监听)
  │
  │  stdin/stdout pipe
  ▼
tailscale nc 100.64.0.2 9876
  │
  │  Tailscale mesh (WireGuard)
  ▼
对端 tailscaled → 127.0.0.1:9876 → ShellServer
```

### 为什么是子进程

- 返回真实的 `asyncio.subprocess.Process`，与 daemon 现有进程管理一致
- daemon 可以 `kill()` 它来断开连接
- daemon 通过 `returncode` 检测代理是否崩溃
- 与 `devtunnel connect` 子进程的管理方式完全一致

## 配置

```toml
[tunnel]
backend = "tailscale"
port = 9876              # ShellServer 监听的固定端口，同一集群的所有机器需一致
```

- `port` 默认 0（DevTunnel 使用随机端口）
- 设置 `backend = "tailscale"` 时建议同时设置 `port`
- 同一集群的所有机器需要使用相同的端口号

## 边缘情况

### 非 shell-cluster 的 Tailscale 设备

`list_tunnels()` 返回 tailnet 中所有在线设备，包括不运行 shell-cluster 的。处理方式：

1. daemon 尝试 `connect()` → tailscale_proxy 启动
2. 健康检查 ping `/sessions` 失败 → 标记为 offline
3. 不会持续重试（下次 discovery 周期才会再尝试）

### Tailscale 未运行

`ensure_tunnel()` 检查 `tailscale status --json`，如果 BackendState 不是 "Running"，daemon 启动失败并提示用户运行 `tailscale up`。

### 端口冲突

固定端口可能被其他程序占用。ShellServer 在绑定端口时会检测并报错。用户可通过修改 `tunnel.port` 解决。

## 未来改进

1. **Tailscale Tags 过滤** — 用 `tag:shellcluster` 标记设备，`list_tunnels()` 只返回有此 tag 的 peer
2. **Tailscale Serve** — 用 `tailscale serve` 直接暴露端口，省去 TCP 代理
3. **ACL 访问控制** — 通过 Tailscale ACL 限制哪些设备可以访问 shell-cluster
4. **端口自动协商** — 从 label/tag 派生端口号，避免手动配置
