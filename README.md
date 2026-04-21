# Shell Cluster

Decentralized remote shell access across all your machines. No central server required.

**Cross-platform** (macOS / Windows / Linux) -- each machine runs a lightweight daemon that peers discover automatically via shared tunnel credentials. Connect to any machine's shell from anywhere, like SSH but without managing keys or servers.

[中文文档](archived/README_CN.md)

## How It Works

```
macOS (zsh)                Windows (PowerShell)         Linux (bash)
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│    daemon    │          │    daemon    │          │    daemon    │
└──────┬───────┘          └──────┬───────┘          └──────┬───────┘
       │                         │                         │
  ═════╪═════════ Tunnel (P2P, no server) ═════════════════╪═════
       │                         │                         │
  CLI / Web Dashboard (from any machine, any OS)
```

**No central server.** Every node is equal. Nodes discover each other by querying the tunnel provider's API for tunnels tagged with a shared label -- all under the same account. No relay, no coordinator, no single point of failure.

## Platform Support

| Platform | Server (daemon) | Client (dashboard) | Shell |
|----------|:-:|:-:|---|
| macOS | Yes | Yes | zsh, bash, fish, ... |
| Windows | Yes | Yes | pwsh (PS 7+), PowerShell, cmd, Git Bash, ... |
| Linux | Yes | Yes | bash, zsh, fish, ... |

## Install

Requires Node.js 18+.

### Install globally

```bash
npm install -g shell-cluster
```

### Try without installing (macOS/Linux only)

```bash
npx shell-cluster start --no-tunnel --port 9876
```

> **Note:** `npx` does not work on Windows due to `node-pty` native addon file locking. Use `npm install -g` instead.

### Install as a background service (recommended)

Use [easy-service](https://github.com/billxc/easy-service) to run shell-cluster as a persistent background service that auto-starts on login. No admin/sudo required.

```bash
easy-service install shellcluster -- shellcluster start
```

This creates a native user-level service (LaunchAgent on macOS, systemd --user on Linux, Task Scheduler on Windows).

### Install from local source

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
npm install
npm link  # makes `shellcluster` available globally
```

## Quick Start

### Option A: Using Dev Tunnel (default)

#### 1. Install

```bash
npm install -g shell-cluster
```

#### 2. Login to Dev Tunnel (once per machine)

```bash
devtunnel user login
```

Use the **same Microsoft account** on all machines.

#### 3. Start (each machine)

```bash
shellcluster start
```

On first run, if no config exists, a default config is created with hostname as node name. The daemon checks that `devtunnel` is installed and logged in before starting.

#### 4. Open Dashboard (any machine)

```bash
shellcluster dashboard
```

Opens your browser — left sidebar shows all discovered peers, right side is a full xterm.js terminal. Click a peer to open a shell, manage multiple sessions in tabs. Use the **Discover** button to trigger an immediate peer refresh.

### Option B: Using Tailscale

Tailscale runs in userspace-networking mode — no sudo, no TUN device, no impact on your normal network.

#### 1. Install

```bash
npm install -g shell-cluster
brew install tailscale  # or see https://tailscale.com/download
```

#### 2. Start Tailscale (once per machine)

```bash
# Start the daemon (no sudo needed)
tailscaled --tun=userspace-networking

# Login (in another terminal)
tailscale login
```

Use the **same Tailscale account** on all machines.

#### 3. Configure shell-cluster to use Tailscale

```bash
shellcluster config tunnel.backend tailscale
shellcluster config tunnel.port 9876
```

All machines in the same cluster should use the **same port**. If the port conflicts on a specific machine, encode a custom port in the Tailscale hostname:

```bash
tailscale set --hostname=my-mac-p9877  # this machine uses port 9877
```

#### 4. Start (each machine)

```bash
shellcluster start
```

#### 5. Open Dashboard (any machine)

```bash
shellcluster dashboard
```

### Run as a background service (recommended)

```bash
easy-service install shellcluster -- shellcluster start
```

The daemon runs in the background and auto-starts on login.

## Architecture

- **node-pty** for cross-platform PTY management
- **xterm-headless** on server for full terminal state tracking per session
- **SerializeAddon** for perfect state replay on reconnect (replaces raw scrollback buffer)
- **WebSocket** `/raw` endpoint: binary frames for PTY data, JSON text frames for control
- **HTTP** `/sessions` endpoint: list active sessions
- **Dashboard API** (port 9000): `/api/peers`, `/api/version`, `/api/refresh-peers`, WebSocket proxy

## Why Decentralized?

| | Shell Cluster | Traditional (SSH + bastion) |
|---|---|---|
| Central server | None | Bastion host required |
| Key management | None (tunnel auth) | SSH keys on every machine |
| NAT traversal | Built-in via tunnel | Port forwarding / VPN |
| Discovery | Automatic | Manual inventory |
| Single point of failure | None | Bastion goes down = no access |
| Cross-platform | macOS + Windows + Linux | SSH server setup varies per OS |

## Command Reference

| Command | Description |
|---------|-------------|
| `shellcluster start` | Start daemon (tunnel + shell server + discovery + dashboard API) |
| `shellcluster start --no-tunnel --port 9876` | Start in local mode (no tunnel) |
| `shellcluster start --dashboard-port 19000` | Use custom dashboard API port |
| `shellcluster register` | Register this machine to the cluster |
| `shellcluster unregister` | Remove this machine from the cluster |
| `shellcluster peers` | List discovered peers |
| `shellcluster config` | Show config path and all values |
| `shellcluster config <key> [value]` | Get or set a config value (e.g. `node.name`) |
| `shellcluster dashboard` | Open web dashboard |
| `-v` / `--verbose` | Enable debug logging |

## Configuration

| OS | Config path |
|---|---|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%LOCALAPPDATA%\shell-cluster\config.toml` |

```toml
[node]
name = "my-macbook"        # Node name, shown in peers and dashboard
label = "shellcluster"     # Tunnel label — same label = same cluster
dashboard_port = 9000      # API + WebSocket proxy port

[tunnel]
backend = "devtunnel"      # Tunnel backend: "devtunnel" or "tailscale"
expiration = "30d"          # Tunnel auto-expiration (devtunnel only)
port = 0                   # Fixed port for shell server (0 = random, set for tailscale)

[shell]
command = ""               # Default shell (empty = auto-detect)
```

## Development

See [archived/DESIGN.md](archived/DESIGN.md) for architecture details.

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
npm install
node src/cli.js start --no-tunnel --port 9876
```

### Running Tests

```bash
npm test            # all tests
npm run test:unit   # unit tests only
npm run test:e2e    # end-to-end tests only
```

## Service Management

Manage the background service installed via [easy-service](https://github.com/billxc/easy-service):

```bash
easy-service status shellcluster    # Check if running
easy-service stop shellcluster      # Stop
easy-service start shellcluster     # Start again
easy-service restart shellcluster   # Restart
easy-service uninstall shellcluster # Remove the service
```

## Roadmap

- [x] macOS + Linux support (PTY via node-pty)
- [x] Windows support (ConPTY via node-pty)
- [x] Local mode (no tunnel)
- [x] MS Dev Tunnel backend
- [x] Tailscale backend (userspace networking)
- [x] xterm-headless server-side state tracking
- [x] Session reconnect with full terminal state replay
- [x] Server-side health checks (HTTP ping every 10s)
- [x] Auto-register on first start
- [x] [easy-service](https://github.com/billxc/easy-service) integration
- [ ] E2E encryption
- [ ] File transfer

## License

MIT
