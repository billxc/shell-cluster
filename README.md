# Shell Cluster

Decentralized remote shell access across all your machines. No central server required.

**Cross-platform** (macOS / Windows / Linux) -- each machine runs a lightweight daemon that peers discover automatically via shared tunnel credentials. Connect to any machine's shell from anywhere, like SSH but without managing keys or servers.

[中文文档](README_CN.md)

## How It Works

```
macOS (zsh)                Windows (PowerShell)         Linux (bash)
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│ daemon :8765 │          │ daemon :8765 │          │ daemon :8765 │
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

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install from source
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv tool install .

# Or directly from git
uv tool install git+https://github.com/billxc/shell-cluster
```

Works on macOS, Windows, and Linux with the same command.

## Quick Start (Local Mode)

No tunnel needed. Works over LAN or localhost for testing.

### 1. Start daemons

```bash
# Terminal 1 (e.g., your Mac)
shellcluster start --no-tunnel --name macbook --port 8765

# Terminal 2 (e.g., your Windows PC)
shellcluster start --no-tunnel --name windows-pc --port 8766
```

### 2. Web Dashboard

Add peers to your config file (`shellcluster register` creates it):

```toml
[[peers]]
name = "macbook"
uri = "ws://192.168.1.10:8765"

[[peers]]
name = "windows-pc"
uri = "ws://192.168.1.20:8766"
```

Then:

```bash
shellcluster dashboard
```

Opens your browser with a terminal dashboard -- left sidebar shows all peers, right side is a full xterm.js terminal. Click a peer to open a shell, manage multiple sessions in tabs.

Peers come from **both** sources: config file + devtunnel auto-discovery.

## Tunnel Mode (Across Networks)

For machines on different networks. Currently supports MS Dev Tunnel.

### Prerequisites

Install [Dev Tunnel CLI](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started) on each machine (available for macOS, Windows, and Linux) and log in with the **same Microsoft account**:

```bash
devtunnel user login
```

### Usage

```bash
# On each machine: register and start
shellcluster register --name my-macbook
shellcluster start
```

This automatically:
- Creates a Dev Tunnel tagged `shellcluster`
- Starts a local WebSocket shell server
- Exposes it through the tunnel
- Discovers other nodes under the same account

```bash
# List peers
shellcluster peers

# Open dashboard
shellcluster dashboard
```

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
| `shellcluster register` | Register this machine to the cluster |
| `shellcluster start` | Start daemon (tunnel + shell server + discovery) |
| `shellcluster start --no-tunnel` | Local mode, no tunnel |
| `shellcluster start --name X --port N` | Override node name and port |
| `shellcluster peers` | List discovered peers |
| `shellcluster dashboard` | Open web dashboard (config peers + devtunnel discovery) |
| `-v` / `--verbose` | Enable debug logging |

## Configuration

| OS | Config path |
|---|---|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

```toml
[node]
name = "my-macbook"        # Node name, defaults to hostname
label = "shellcluster"     # Tunnel label for peer discovery
port = 8765                # WebSocket server port

[tunnel]
backend = "devtunnel"      # Tunnel backend (devtunnel for now)
expiration = "8h"          # Tunnel expiration

[discovery]
interval_seconds = 30      # Discovery refresh interval
manual_peers = []          # Manually added tunnel IDs

[shell]
command = ""               # Default shell, empty = $SHELL (Unix) / %COMSPEC% (Windows)

# Manual peers for local/LAN mode (optional)
# [[peers]]
# name = "my-desktop"
# uri = "ws://192.168.1.20:8765"
```

## Development

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv sync

# Local test with two nodes
uv run shellcluster start --no-tunnel --name node-a --port 8765
uv run shellcluster start --no-tunnel --name node-b --port 8766

# Connect from a third terminal
uv run shellcluster connect ws://localhost:8765
```

## Roadmap

- [x] macOS + Linux support (PTY)
- [x] Windows support (winpty/conpty)
- [x] Local mode (no tunnel)
- [x] MS Dev Tunnel backend
- [ ] Cloudflare Tunnel backend
- [ ] E2E encryption
- [x] Web Dashboard (xterm.js)
- [ ] File transfer
- [ ] [easy-service](https://github.com/billxc/easy-service) integration for system service registration

## License

MIT
