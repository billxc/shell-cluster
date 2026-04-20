# Shell Cluster — 开发交接文档

## 项目概述

去中心化跨机器远程 Shell 访问工具。原 Python 实现已重写为 Node.js，使用 node-pty + xterm-headless 管理终端状态。

- **npm 包名**：`shell-cluster`（已发布至 npmjs.com）
- **当前版本**：1.0.8
- **仓库**：https://github.com/billxc/shell-cluster
- **许可证**：MIT

---

## 目录结构

```
shell-cluster/
├── package.json          # npm 包配置，bin: shellcluster → src/cli.js
├── package-lock.json
├── README.md             # 完整英文文档
├── .gitignore
├── src/                  # Node.js 服务端源码 (2640 行)
│   ├── cli.js            # CLI 入口，commander 实现 (222 行)
│   ├── config.js         # TOML 配置读写，跨平台路径 (139 行)
│   ├── daemon.js         # Daemon 编排：tunnel + shell + discovery + dashboard (408 行)
│   ├── dashboard-server.js  # HTTP API + WS 代理 + 静态文件 (251 行)
│   ├── index.js          # 独立入口（不带 tunnel/discovery）(107 行)
│   ├── shell-manager.js  # PTY 生命周期 + xterm-headless 状态 (285 行)
│   ├── shell-server.js   # WebSocket /raw 端点 + HTTP /sessions (263 行)
│   └── tunnel/
│       ├── base.js       # tunnel ID 工具 + backend 工厂 (60 行)
│       ├── checks.js     # devtunnel/tailscale CLI 预检 (149 行)
│       ├── devtunnel.js  # MS Dev Tunnel 后端 (295 行)
│       ├── discovery.js  # 节点发现循环 (146 行)
│       ├── tailscale-proxy.js  # TCP 代理 via tailscale nc (97 行)
│       └── tailscale.js  # Tailscale 后端 (218 行)
├── public/               # 前端 dashboard UI (908 行)
│   ├── index.html        # xterm.js 从 CDN 加载 (jsdelivr)
│   ├── app.js            # 前端逻辑：peer 列表、session 管理、WS 连接 (589 行)
│   └── style.css         # Catppuccin 暗色主题 (278 行)
├── scripts/
│   └── postinstall.js    # npm install 后修复 node-pty spawn-helper 权限 (44 行)
└── archived/             # Python 旧代码（完整保留，供参考）
    ├── src/shell_cluster/
    ├── tests/
    ├── docs/
    ├── pyproject.toml
    └── ...
```

---

## 架构

### 端口分配

| 端口 | 服务 | 说明 |
|------|------|------|
| 随机 (tunnel 模式) 或 `--port` 指定 | ShellServer | WS `/raw` + HTTP `/sessions` |
| 9000 (可通过 `--dashboard-port` 或 config 修改) | DashboardServer | HTTP `/api/peers` + `/api/refresh-peers` + `/api/version` + WS 代理 + 前端 UI |

### 核心数据流

```
浏览器 (xterm.js)
    ↕ WebSocket (binary: PTY data, text: JSON control)
DashboardServer (:9000)
    ↕ WS 代理 (bidirectional)
ShellServer (:随机)
    ↕ onOutput/onExit callbacks
ShellManager
    ↕ node-pty (PTY I/O) + xterm-headless (状态追踪)
Shell 进程 (zsh/bash/pwsh)
```

### 关键设计决策

1. **xterm-headless 替代 scrollback buffer**
   - 旧方案：64KB 环形 deque 保存原始字节，reconnect 时回放
   - 新方案：每个 session 一个 headless Terminal + SerializeAddon
   - `terminal.write(data)` 追踪所有 PTY 输出
   - `serializer.serialize()` 生成完整终端状态（含颜色、光标位置）
   - reconnect 时发送序列化状态，客户端 xterm.js 完美还原

2. **DEC Private Mode 追踪**
   - SerializeAddon 不保存终端模式（鼠标、bracketed paste 等）
   - `xterm-headless` 的 `terminal.modes` 属性不追踪 `write()` 设置的模式（已验证）
   - 改用正则 `ESC[?<modes>h/l` 在 PTY 输出时实时追踪 `_decModes` Set
   - reconnect 时在序列化内容后追加 `ESC[?<all active modes>h`

3. **Backpressure 处理**
   - PTY 输出 → `ws.send()` → 检查 `ws.bufferedAmount`
   - 超过 1MB → `pty.pause()`
   - 降到 256KB 以下 → `pty.resume()`
   - 防止 lazygit 等大量输出的 TUI 应用撑爆 WS 连接

4. **Terminal Query Stripping**
   - PTY 输出中的 DA/CPR/OSC/DECRQM 查询序列会触发客户端 xterm.js 响应
   - 响应通过 WS 回传到 PTY → 终端显示乱码
   - 发送前用正则 `TERMINAL_QUERY_RE` 去除这些序列

5. **前端直连 vs 代理**
   - 本地 peer：前端直连 `ws://localhost:<port>/raw`
   - 远程 peer（通过 tunnel）：前端通过 DashboardServer WS 代理
   - 代理协议：前端先发 `{target: "ws://...", path: "/raw?..."}` init 消息

---

## WebSocket 协议

### /raw 端点（ShellServer）

**客户端 → 服务端：**
- Binary frame：原始 PTY 输入（键盘数据）
- Text frame：JSON 控制消息
  - `{"type": "shell.resize", "cols": N, "rows": N}`
  - `{"type": "shell.close"}`

**服务端 → 客户端：**
- Binary frame：PTY 输出（终端数据）
- Text frame：JSON 控制消息
  - `{"type": "shell.created", "session_id": "...", "shell": "zsh"}`
  - `{"type": "shell.attached", "session_id": "...", "shell": "zsh"}`
  - `{"type": "shell.closed", "session_id": "..."}`

**URL 参数：**
- 创建：`/raw?session=<id>&cols=80&rows=24`
- 重连：`/raw?attach=<id>&cols=80&rows=24`

### HTTP 端点

| 端点 | 方法 | 响应 | 所在服务 |
|------|------|------|----------|
| `/sessions` | GET | `[{"id","shell","created_at"}]` | ShellServer |
| `/api/peers` | GET | `[{"name","uri","status"}]` | DashboardServer |
| `/api/refresh-peers` | POST | `{"ok": bool}` | DashboardServer |
| `/api/version` | GET | `{"version": "1.0.8"}` | DashboardServer |
| `/` | GET | Dashboard HTML | DashboardServer |

---

## 配置

**路径（与 Python 版共用同一文件）：**
- macOS: `~/Library/Application Support/shell-cluster/config.toml`
- Linux: `~/.config/shell-cluster/config.toml`
- Windows: `%LOCALAPPDATA%\shell-cluster\config.toml`

```toml
[node]
name = "my-macbook"
label = "shellcluster"       # 同 label = 同集群
dashboard_port = 9000

[tunnel]
backend = "devtunnel"        # "devtunnel" 或 "tailscale"
expiration = "30d"
port = 0                     # 0 = 随机

[shell]
command = ""                 # 空 = 自动检测
```

**Windows shell 自动检测顺序：** `pwsh` → `powershell.exe` → `cmd.exe`（返回完整路径）

**Tunnel ID 格式：** `shellcluster-<node-name-lowercase>-shellcluster`（强制小写，devtunnel 要求 `[a-z0-9-]`）

---

## CLI 命令

```
shellcluster start                           # tunnel 模式启动
shellcluster start --no-tunnel --port 9876   # 本地模式
shellcluster start --dashboard-port 19000    # 自定义 dashboard 端口（避免冲突）
shellcluster register --name mypc            # 注册节点
shellcluster unregister                      # 注销（删 tunnel + config）
shellcluster peers                           # 列出已发现节点
shellcluster config                          # 显示配置
shellcluster config node.name mypc           # 设置配置
shellcluster dashboard                       # 浏览器打开 dashboard
shellcluster --version                       # 显示版本
```

---

## 部署

```bash
# npm 全局安装
npm install -g shell-cluster

# 或从 GitHub
npm install -g github:billxc/shell-cluster

# 本地开发
cd shell-cluster
npm install
npm link  # 全局可用 shellcluster 命令

# 发布新版本
# 1. 改 package.json version
# 2. git commit
# 3. npm publish

# LaunchAgent (macOS, via easy-service)
easy-service install shellcluster -- shellcluster start
```

**Windows 注意事项：**
- `npx` 不兼容 node-pty（native addon 文件锁定），必须用 `npm install -g`
- npm 缓存问题：`npm cache clean --force` 后重装
- 残留文件问题：手动 `rd /s /q` 删除 node_modules 后重装

---

## 已知问题 & 待解决

### 1. `posix_spawnp failed` (优先级：高)
- **现象**：网页创建 session 时报 `Error: posix_spawnp failed.`，但本地直接调 `ShellManager.create()` 正常
- **可能原因**：
  - npx 安装的版本 spawn-helper 权限丢失
  - npm link 后 node_modules 路径不对
  - node-pty prebuild 和当前 Node.js 版本不匹配
- **排查方向**：在 shell-server.js catch 块加详细日志（stack trace），确认是哪个进程的 node-pty 出了问题
- **临时方案**：`postinstall.js` 已有 chmod + rebuild fallback

### 2. lazygit session 断连 (优先级：高)
- **现象**：lazygit 运行中，前端 session 突然显示断连，但服务端 session 还在
- **已做**：加了 backpressure（pause PTY when bufferedAmount > 1MB）
- **未验证**：改动后未实际测试
- **如果 backpressure 没解决**：可能是 WS 库级别的问题，考虑：
  - 限制单次 `ws.send()` 的数据量（分片发送）
  - 在 onOutput 中做 debounce/batch
  - 检查 ws 库是否有 `maxPayload` 或内部 buffer 限制

### 3. lazygit session resume 后鼠标不工作 (优先级：中)
- **现象**：reconnect 后 lazygit 界面恢复但鼠标点击无效
- **已做**：DEC private mode 追踪（正则扫描 `ESC[?<modes>h/l`），reconnect 时追加 mode 恢复序列
- **未验证**：改动后未实际测试
- **注意**：`xterm-headless` 的 `terminal.modes` 公开 API **不追踪** `write()` 设置的模式（已用代码验证），所以必须手动正则追踪

### 4. LaunchAgent 找不到 node (优先级：低)
- **现象**：`easy-service` 创建的 LaunchAgent 用 `zsh -lc shellcluster start`，但 nvm 在 `.zshrc` 不在 `.zprofile`，login shell 找不到 node
- **用户说自己处理**
- **可能方案**：
  - 在 `.zprofile` 加 nvm PATH（只加 PATH 不 source nvm.sh，避免拖慢启动）
  - plist 里用 node 完整路径（不够通用）

### 5. Python/Node.js 混合集群兼容性 (优先级：低)
- **协议层完全兼容**：WS 消息格式、HTTP 端点、tunnel ID、config.toml 路径全部一致
- **内部数据结构命名不一致**：Node.js discovery 用 camelCase（`tunnelId`, `forwardingUri`），Python 用 snake_case（`tunnel_id`, `forwarding_uri`）
- **不影响运行**：因为 discovery 数据不在节点间直接交换，各 daemon 独立查询 tunnel API

---

## 代码导航

### session 创建流程
1. `public/app.js:createSession()` → 构建 WS URL → `new WebSocket(peer.uri + '/raw?session=...')`
2. `src/shell-server.js:_handleRawClient()` → 解析 query params
3. `src/shell-manager.js:create()` → `pty.spawn()` + `new Terminal()` + `new SerializeAddon()`
4. PTY output → `ptyProcess.onData()` → `terminal.write()` + `_decModes` 追踪 + 通知所有 listeners
5. listener (`onOutput` in shell-server.js) → `stripTerminalQueries()` → `ws.send(Buffer)` (binary frame)

### session 重连流程
1. `public/app.js:createSession(peer, existingId)` → WS URL 用 `attach=<id>`
2. `src/shell-server.js` → `shellManager.attach()` → 注册新 callbacks
3. `shellManager.getSerializedState()` → `serializer.serialize()` + 追加 `_decModes` 恢复序列
4. 发送 `shell.attached` (text) → 发送序列化状态 (binary) → 后续正常转发

### tunnel 发现流程
1. `src/daemon.js:start()` → `PeerDiscovery.refresh()`
2. `src/tunnel/discovery.js:refresh()` → `backend.listTunnels(label)` → 过滤 hosting 的
3. 新 peer → `backend.getPortAndUri()` → 存入 peers 列表
4. `daemon._onPeersChanged()` → `backend.connect()` → 建立 tunnel 连接 → 存入 `_peerUris`
5. health check 每 10s ping 各 peer `/sessions`，掉线则触发重连

### 前端 peer 列表刷新
1. `public/app.js:init()` → `refreshData()` → `fetchPeers()` + `fetchSessions()`
2. `fetchPeers()` → `GET /api/peers` from DashboardServer
3. `fetchSessions()` → 并行 `GET /sessions` from 每个 peer（直接 HTTP，3s 超时）
4. 每 30s 自动刷新，启动后 3s 和 10s 各补刷一次

---

## 开发 & 测试

```bash
# 本地开发（不冲突 prod）
shellcluster start --no-tunnel --port <空闲端口> --dashboard-port <空闲端口>

# 发布流程
vim package.json  # 改 version
git add -A && git commit -m "..."
npm publish

# 其他机器更新
npm install -g shell-cluster@latest
# 如果遇到缓存问题：
npm cache clean --force && npm install -g shell-cluster@latest
```

---

## 依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `node-pty` | ^1.0.0 | 跨平台 PTY (macOS/Linux/Windows) |
| `@xterm/headless` | ^5.5.0 | 服务端终端状态追踪 |
| `@xterm/addon-serialize` | ^0.13.0 | 终端状态序列化 |
| `ws` | ^8.18.0 | WebSocket 服务端 |
| `@iarna/toml` | ^2.2.5 | TOML 配置解析/写入 |
| `commander` | ^12.0.0 | CLI 命令解析 |

**前端 CDN 依赖（不打包）：**
- `@xterm/xterm@5.5.0`
- `@xterm/addon-fit@0.10.0`
- `@xterm/addon-attach@0.11.0`
- `@xterm/addon-unicode11@0.8.0`

---

## Commit 历史（本次重构）

```
18a7bef fix: add backpressure handling + DEC mode tracking for session resume
bccd00c feat: add --dashboard-port CLI option to avoid port conflicts
56aaf96 feat: add /api/version endpoint
f04e396 fix: restore terminal modes (mouse, bracketed paste) on session resume
a94bdc4 fix: remove unnecessary shell detection cache, bump 1.0.5
a8e00e1 fix: return full path from 'where' for Windows shell detection
65b9461 fix: Windows shell fallback: pwsh -> powershell.exe -> cmd.exe
99cd1f9 feat: support multiple clients viewing the same session
1427257 fix: handle node-pty native module failure gracefully across platforms
40a4d77 feat: add built-in dashboard UI with xterm.js via CDN
ff4bf8a fix: Windows config path, tunnel ID casing, and --version support
31b7006 fix: make postinstall cross-platform for Windows
b56f419 refactor: move package.json and src/ to project root
4836d86 refactor: move Python code to archived/, add Node.js README
11b726e refactor: remove port 9001 static server, keep 9000 as API-only
8b92c50 feat: rewrite server in Node.js with node-pty + xterm-headless
```
