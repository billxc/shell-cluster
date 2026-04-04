# Shell Cluster

Decentralized remote shell access across all your machines. No central server required.

Each machine runs a lightweight daemon that peers discover automatically via shared tunnel credentials. Connect to any machine's shell from anywhere -- like SSH, but without managing keys or servers.

## How It Works

```
Machine A                              Machine B
┌───────────────────┐                 ┌───────────────────┐
│ daemon            │                 │ daemon            │
│  ├ zsh #1         │                 │  ├ bash #1        │
│  └ bash #2        │                 │  └ zsh #2         │
│ WebSocket :8765   │                 │ WebSocket :8765   │
└────────┬──────────┘                 └────────┬──────────┘
         │                                     │
    ═════╪══ Tunnel (P2P, no server) ══════════╪═════
         │                                     │
    CLI / TUI Dashboard (from any machine)
```

**No central server.** Every node is equal. Nodes discover each other by querying the tunnel provider's API for tunnels tagged with a shared label -- all under the same account. No relay, no coordinator, no single point of failure.

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

## Quick Start (Local Mode)

No tunnel needed. Works over LAN or localhost for testing.

### 1. Start daemons

```bash
# Terminal 1
shellcluster start --no-tunnel --name node-a --port 8765

# Terminal 2
shellcluster start --no-tunnel --name node-b --port 8766
```

### 2. Connect

```bash
# Terminal 3
shellcluster connect ws://localhost:8765
```

You're now in node-a's shell. Type `exit` or press `~.` (tilde-dot after newline) to disconnect.

### 3. TUI Dashboard

```bash
shellcluster dashboard
```

## Tunnel Mode (Across Networks)

For machines on different networks. Currently supports MS Dev Tunnel.

### Prerequisites

Install [Dev Tunnel CLI](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started) and log in with the **same Microsoft account** on every machine:

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

# Connect by name
shellcluster connect my-desktop
shellcluster connect my-desktop bash    # specify shell
```

## Why Decentralized?

| | Shell Cluster | Traditional (SSH + bastion) |
|---|---|---|
| Central server | None | Bastion host required |
| Key management | None (tunnel auth) | SSH keys on every machine |
| NAT traversal | Built-in via tunnel | Port forwarding / VPN |
| Discovery | Automatic | Manual inventory |
| Single point of failure | None | Bastion goes down = no access |

## Command Reference

| Command | Description |
|---------|-------------|
| `shellcluster register` | Register this machine to the cluster |
| `shellcluster start` | Start daemon (tunnel + shell server + discovery) |
| `shellcluster start --no-tunnel` | Local mode, no tunnel |
| `shellcluster start --name X --port N` | Override node name and port |
| `shellcluster peers` | List discovered peers |
| `shellcluster connect <target>` | Connect by name or `ws://host:port` |
| `shellcluster connect <target> <shell>` | Connect with specific shell type |
| `shellcluster dashboard` | Open TUI dashboard |
| `-v` / `--verbose` | Enable debug logging |

## Configuration

Path: `~/.config/shell-cluster/config.toml`

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
command = ""               # Default shell, empty = $SHELL
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

- [ ] Cloudflare Tunnel backend
- [ ] E2E encryption
- [ ] Windows support (conpty)
- [ ] Web UI (HTML)
- [ ] File transfer
- [ ] [easy-service](https://github.com/billxc/easy-service) integration for system service registration

## License

MIT
