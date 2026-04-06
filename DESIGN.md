 # Shell Cluster — Design Document

## Overview

Shell Cluster is a decentralized, cross-platform remote shell tool. Each machine runs a lightweight daemon that exposes its shell via WebSocket. Nodes discover each other through a shared tunnel provider (MS Dev Tunnel) — no central server, no SSH keys, no port forwarding.

The user interacts through a **web dashboard** (xterm.js) that renders real terminal sessions in the browser.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Machine A                            │
│                                                             │
│  shell/manager         shell/server        DevTunnel        │
│  ├─ PTY session 1      WebSocket :random   ├─ create        │
│  ├─ PTY session 2      (JSON protocol)     ├─ host          │
│  └─ PTY session N                          └─ discovery     │
│                                                             │
│  Tunnel ID: shellcluster-<name>-shellcluster                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    devtunnel host (outbound to MS cloud)
                           │
                ═══════════╪═══════════════════════
                           │
                    devtunnel connect (on client machine)
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                     Client Machine                          │
│                                                             │
│  Dashboard Server (:9000)                                   │
│  ├─ HTTP: serves index.html (xterm.js)                      │
│  ├─ WebSocket proxy: browser ↔ peer via localhost           │
│  └─ Tunnel connect: devtunnel connect → localhost:<port>    │
│                                                             │
│  Browser                                                    │
│  ├─ Left sidebar: peer list + session list                  │
│  └─ Right pane: xterm.js terminal (tabbed)                  │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
src/shell_cluster/
├── __init__.py          # Version info
├── cli.py               # CLI entry point (click)
├── config.py            # Config management (~/.config/shell-cluster/)
├── daemon.py            # Daemon orchestrator
├── models.py            # Data models (Peer, TunnelInfo, ShellSession)
├── protocol.py          # WebSocket JSON protocol
├── shell/               # ── Shell service layer ──
│   ├── __init__.py
│   ├── manager.py       # PTY session management (cross-platform)
│   └── server.py        # WebSocket shell server
├── tunnel/              # ── Tunnel transport layer ──
│   ├── __init__.py
│   ├── base.py          # Abstract backend + naming utilities
│   ├── devtunnel.py     # MS Dev Tunnel implementation
│   └── discovery.py     # Peer discovery via tunnel API
└── web/                 # ── Web dashboard ──
    ├── __init__.py
    ├── server.py         # HTTP + WebSocket proxy server
    └── static/
        └── index.html    # xterm.js single-page dashboard
```

### Layer separation

| Layer | Modules | Knows about |
|-------|---------|-------------|
| **Shell** | `shell/manager.py`, `shell/server.py` | PTY, WebSocket, protocol |
| **Tunnel** | `tunnel/base.py`, `tunnel/devtunnel.py`, `tunnel/discovery.py` | Tunnel lifecycle, peer discovery |
| **Web** | `web/server.py`, `web/static/` | HTTP, WebSocket proxy |
| **Orchestration** | `daemon.py`, `cli.py` | Both layers (composition root) |
| **Shared** | `models.py`, `protocol.py`, `config.py` | Data structures only |

Shell layer and tunnel layer have **zero cross-imports**.

## Components

### 1. Shell Manager (`shell/manager.py`)
- **Unix**: `pty.openpty()` + `os.fork()` + `os.execvpe()`
- **Windows**: `winpty.PtyProcess.spawn()`
- Manages multiple concurrent PTY sessions
- Read loop in executor thread → async callbacks for output/exit
- `attach()`: re-bind callbacks for session reconnect after browser refresh

### 2. Shell Server (`shell/server.py`)
- WebSocket server (websockets library, `0.0.0.0:<port>`)
- JSON protocol with base64-encoded terminal data
- Sessions persist across client disconnects (for reconnect)
- Dispatches: `shell.create`, `shell.attach`, `shell.data`, `shell.resize`, `shell.close`, `shell.list`

### 3. Protocol (`protocol.py`)
All messages are JSON text frames:

| Type | Direction | Purpose |
|------|-----------|---------|
| `peer.info` | server→client | Node name + session list on connect |
| `shell.create` | client→server | Create new PTY session |
| `shell.created` | server→client | Session created confirmation |
| `shell.attach` | client→server | Re-attach to existing session |
| `shell.attached` | server→client | Attach confirmation |
| `shell.data` | bidirectional | Terminal data (base64) |
| `shell.resize` | client→server | Terminal size change |
| `shell.close` | client→server | Close session |
| `shell.closed` | server→client | Session ended |
| `shell.list` | client→server | List active sessions |
| `shell.list.response` | server→client | Session list |
| `error` | server→client | Error message |

### 4. Tunnel Backend (`tunnel/base.py`, `tunnel/devtunnel.py`)
Abstract `TunnelBackend` protocol with concrete `DevTunnelBackend`:

| Method | Purpose |
|--------|---------|
| `create()` | Create tunnel + add port |
| `ensure_tunnel()` | Reuse existing or create new (handles port changes) |
| `host()` | Start `devtunnel host` subprocess |
| `connect()` | Start `devtunnel connect` for local port mapping |
| `list_tunnels()` | List tunnels with label filter |
| `get_port_and_uri()` | Get port + forwarding URI from `show --json` |
| `exists()` | Check if tunnel exists |
| `delete()` | Delete tunnel |

**Tunnel ID format**: `shellcluster-<node-name>-shellcluster`
- devtunnel appends region suffix: `shellcluster-my-mac-shellcluster.jpe1`
- `parse_node_name()` strips prefix + suffix + region to extract node name

**Tunnel lifecycle**:
- `start`: server binds random port → `ensure_tunnel()` (reuse or create) → `host()`
- `stop`: kill host process, keep tunnel alive (has expiration)
- Next `start`: reuse tunnel, update port if changed, host again

### 5. Discovery (`tunnel/discovery.py`)
- Calls `backend.list_tunnels(label)` to find peers
- Filters by `hostConnections > 0` (only actively hosted tunnels)
- Calls `backend.get_port_and_uri()` for each new peer
- Extracts node name from tunnel ID via `parse_node_name()`
- Periodic refresh loop when running inside daemon

### 6. Daemon (`daemon.py`)
Orchestrates all components:
```
start:
  1. Bind WebSocket server (random port in tunnel mode, fixed in local mode)
  2. ensure_tunnel() + host() (tunnel mode only)
  3. Start discovery loop (tunnel mode only)
  4. Register atexit handler to kill child processes

stop:
  1. Stop discovery
  2. Stop WebSocket server
  3. Kill devtunnel host process
  4. Keep tunnel alive for fast restart
```

### 7. Web Dashboard (`web/server.py`, `web/static/index.html`)
- Python: HTTP server (serves HTML) + WebSocket proxy
- HTML: Single page with xterm.js, Catppuccin theme
- Connection: browser → WS proxy (:9000) → init message → proxy connects to peer → bidirectional relay
- Sessions persist across browser refresh via `shell.attach`
- Frontend queries each peer's session list on load, shows reconnectable sessions

### 8. Config (`config.py`)
TOML config at platform-specific path:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

Sections: `[node]`, `[tunnel]`, `[discovery]`, `[shell]`, `[[peers]]`

### Config fields

```toml
[node]
name = "my-macbook"        # Node name, shown in peers list and dashboard
label = "shellcluster"     # Tunnel label — same label = same cluster
port = 8765                # WebSocket port for local mode; tunnel mode uses random port

[tunnel]
backend = "devtunnel"      # Tunnel backend ("devtunnel" for now)
expiration = "8h"          # Tunnel expiration (cloud auto-cleanup)

[discovery]
interval_seconds = 30      # Peer list refresh interval (seconds) when daemon is running
manual_peers = []          # Reserved, not used yet

[shell]
command = ""               # Default shell. Empty = auto-detect
                           # Unix: $SHELL → /bin/sh
                           # Windows: pwsh → powershell → cmd

# Manual peers for LAN/direct connections (optional, additive with tunnel discovery)
# [[peers]]
# name = "my-desktop"
# uri = "ws://192.168.1.20:8765"
```

## CLI Commands

### `shellcluster register --name <name>`
1. Load or create config file
2. Save node name, port, label, backend to config
3. Print confirmation

### `shellcluster unregister`
1. Load config, derive tunnel ID from node name
2. Call `backend.delete(tunnel_id)` to remove tunnel from cloud
3. Delete local config file

### `shellcluster start`
1. Load config
2. Create `ShellManager` with default shell
3. Create `ShellServer` (port 0 = random)
4. Start WebSocket server → get actual port
5. `ensure_tunnel()` — check if tunnel exists, create if not, update port if changed
6. `devtunnel host` — start tunnel host subprocess
7. Start `PeerDiscovery` loop (periodic `list_tunnels`)
8. Register `atexit` to kill host process on exit
9. Wait forever (until Ctrl+C or host process exit)

### `shellcluster start --no-tunnel`
Same as above but skip steps 5-7. Server binds to configured port instead of random.

### `shellcluster peers`
1. Create `PeerDiscovery` with devtunnel backend
2. Call `discovery.refresh()` → `list_tunnels(label)` → filter `hostConnections > 0`
3. For each peer: `get_port_and_uri()` via `devtunnel show --json`
4. Print Rich table (name, tunnel ID, status, URI)

### `shellcluster dashboard`
1. Load config peers (manual `[[peers]]`)
2. Create `PeerDiscovery`, call `refresh()`
3. For each discovered peer: `devtunnel connect <tunnel-id>` → map to localhost
4. Merge manual peers + discovered peers (dedup by name)
5. Start HTTP server (:9000) serving `index.html` with peer list injected
6. Start WebSocket proxy (browser → localhost mapped port → tunnel → peer)
7. Open browser
8. On exit: kill all `devtunnel connect` processes

## Connection Flow

### Server side (tunnel mode)
```
register → config saved
start → server :random → ensure_tunnel → host
           (port 52992)   (reuse/create)   (outbound to MS cloud)
```

### Client side
```
dashboard
  → discovery: list tunnels → find peers
  → for each peer: devtunnel connect → localhost:<same-port>
  → start HTTP server :9000 → open browser
  → user clicks peer → WS proxy → localhost:<mapped-port> → tunnel → peer
```

### Session reconnect (browser refresh)
```
page load → query each peer's session list
         → show remote sessions in sidebar (↻ icon)
         → user clicks → shell.attach → re-bind callbacks → resume
```

## Design Decisions

1. **Random port in tunnel mode** — avoids port conflicts, tunnel layer handles mapping
2. **`devtunnel connect` not direct wss://** — proper layer separation, all WS goes through localhost
3. **Sessions persist on disconnect** — allows browser refresh without losing state
4. **No central server** — peers discover via shared tunnel label under same account
5. **Web dashboard over TUI** — xterm.js provides better terminal emulation, cross-platform rendering
6. **Tunnel reuse** — `ensure_tunnel()` avoids expensive re-creation on daemon restart
7. **`shellcluster-<name>-shellcluster` naming** — reliable node name extraction from tunnel ID
8. **Shell/tunnel layer separation** — zero cross-imports, ready for alternative backends

## Dependencies

| Package | Purpose |
|---------|---------|
| `click` | CLI |
| `websockets` | WebSocket server + proxy |
| `platformdirs` | Config directory |
| `tomli-w` | TOML write |
| `rich` | Pretty terminal output |
| `pywinpty` | Windows PTY (conditional) |

## Roadmap

- [x] macOS + Linux PTY support
- [x] Windows PTY support (winpty)
- [x] Local mode (no tunnel)
- [x] MS Dev Tunnel backend
- [x] Web Dashboard (xterm.js)
- [x] Session persistence (shell.attach)
- [ ] E2E encryption
- [ ] File transfer
- [x] System service integration (easy-service)
