# Cloudflare Tunnel 后端 — 放弃记录

## 结论

Cloudflare Tunnel 不适合 shell-cluster 的去中心化 P2P 场景。已删除全部代码。

## 核心问题

### 1. 无法无域名直连

Cloudflare Tunnel **必须有 DNS 域名**才能让客户端连接。不存在"纯隧道、无域名"的连接方式。

- `<tunnel-uuid>.cfargotunnel.com` 不可 DNS 解析，这个只是 CNAME target
- `cloudflared access tcp --hostname <uuid>.cfargotunnel.com` → `no such host`
- `cloudflared access tcp --hostname <tunnel-name>` → `no such host`

**与 devtunnel 的对比**：`devtunnel connect <tunnel-id>` 可以直接用 tunnel ID 连接，不需要任何域名。

### 2. 连接方式只有两种，都依赖 DNS

| 方式 | 说明 | 问题 |
|------|------|------|
| Named tunnel + DNS CNAME | `tunnel route dns <name> sc-xxx.example.com` | 需要用户有自己的域名且托管在 Cloudflare |
| Quick tunnel | 自动分配 `*.trycloudflare.com` | 临时域名，无 SLA，不可控 |

### 3. 安全问题 — Access 认证无法通过 CLI 自动化

Cloudflare Tunnel 创建的域名**默认公开可访问**，任何人知道 URL 就能连。

要加认证保护需要：
- 调用 Cloudflare REST API 创建 Access Application + Access Policy
- 需要 API Token（需 `Access: Apps and Policies Write` 权限）
- `cloudflared` CLI 本身没有管理 Access 策略的命令
- 认证后客户端连接需要带 Service Token header 或 JWT

这对用户来说配置成本太高。

### 4. QUIC 协议在部分网络不可用

默认 `cloudflared tunnel run` 使用 QUIC（UDP）协议。在测试中 QUIC 连接持续超时：

```
ERR failed to dial to edge with quic: timeout: no recent network activity
```

必须加 `--protocol http2` 才能连通。这增加了不稳定因素。

### 5. Private Network Route 需要 WARP 客户端

`cloudflared tunnel route ip` 可以创建私有网络路由，但客户端必须安装 Cloudflare WARP 并登录 Zero Trust 组织。比直接装 cloudflared 还重。

## 测试过的方案

### 方案 A：Named tunnel + `access tcp`（失败）
```bash
cloudflared tunnel create shellcluster-xxx
cloudflared tunnel run --url http://localhost:PORT shellcluster-xxx
# 客户端：
cloudflared access tcp --hostname <uuid>.cfargotunnel.com --url localhost:19999
# 结果：no such host
```

### 方案 B：Named tunnel + DNS + `wss://`（可行但不实用）
```bash
cloudflared tunnel create shellcluster-xxx
cloudflared tunnel route dns shellcluster-xxx sc-xxx.example.com
cloudflared tunnel run --protocol http2 --url http://localhost:PORT shellcluster-xxx
# 客户端：
wss://sc-xxx.example.com/  # 可连，但公开可访问，无认证
```

### 方案 C：Quick tunnel + `wss://`（可行但不可控）
```bash
cloudflared tunnel --protocol http2 --url http://localhost:PORT
# 自动分配：https://random-words.trycloudflare.com
# 客户端：
wss://random-words.trycloudflare.com/  # 可连，WebSocket 正常
```

## 与 devtunnel 对比

| 特性 | devtunnel | Cloudflare Tunnel |
|------|-----------|-------------------|
| 无域名直连 | ✅ `devtunnel connect <id>` | ❌ 必须 DNS |
| CLI 一条命令创建 | ✅ | ✅ |
| 内置认证 | ✅ 基于 MS 账号 | ❌ 需额外配置 Access |
| Discovery（peer 发现） | ✅ `devtunnel list --labels` | ✅ `cloudflared tunnel list` |
| 客户端零依赖 | ❌ 需要 devtunnel CLI | ✅ 直接 wss://（如果有域名） |
| 安全性 | ✅ 默认需要认证 | ⚠️ 默认公开 |

## 未来可能性

如果以后要重新支持 Cloudflare，需要满足：
1. 用户必须有 Cloudflare 账号 + 自己的域名
2. 用户必须提供 API Token
3. 代码需自动创建 tunnel + DNS route + Access Application + Access Policy
4. 代码需处理 QUIC fallback 到 http2
5. 客户端连接需带 Service Token header

复杂度远高于 devtunnel 方案，收益有限。
