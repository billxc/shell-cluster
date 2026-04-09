# Shell Cluster

Decentralized remote shell access across all your machines. No central server required.

**Cross-platform** (macOS / Windows / Linux) -- each machine runs a lightweight daemon that peers discover automatically via shared tunnel credentials. Connect to any machine's shell from anywhere, like SSH but without managing keys or servers.

[中文文档](README_CN.md)

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

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

### Try without installing

```bash
# Run directly (no install needed)
uvx --from git+https://github.com/billxc/shell-cluster shellcluster start

# Or with uv run
uv run --with git+https://github.com/billxc/shell-cluster shellcluster dashboard
```

### Install globally

```bash
uv tool install git+https://github.com/billxc/shell-cluster
```

Works on macOS, Windows, and Linux with the same command.

### Install as a background service (recommended)

Use [easy-service](https://github.com/billxc/easy-service) to run shell-cluster as a persistent background service that auto-starts on login. No admin/sudo required.

```bash
# Install easy-service
uv tool install git+https://github.com/billxc/easy-service.git

# Install shell-cluster as a service (auto-starts immediately)
easy-service install shellcluster -- shellcluster start --no-open
```

This creates a native user-level service (LaunchAgent on macOS, systemd --user on Linux, Task Scheduler on Windows).

### Install from local source

If you have both repos cloned side by side:

```bash
cd shell-cluster
uv tool install .

# Optional: install as a service
cd ../easy-service
uv tool install .
easy-service install shellcluster -- shellcluster start --no-open
```

## Quick Start

### 1. Install

```bash
uv tool install git+https://github.com/billxc/shell-cluster
uv tool install git+https://github.com/billxc/easy-service.git
```

### 2. Login to Dev Tunnel (once per machine)

```bash
devtunnel user login
```

Use the **same Microsoft account** on all machines.

### 3. Start (each machine)

```bash
shellcluster start
```

On first run, if no config exists, you'll be prompted for a node name (defaults to hostname). The daemon checks that `devtunnel` is installed and logged in before starting.

### 4. Open Dashboard (any machine)

```bash
shellcluster dashboard
```

Opens your browser — left sidebar shows all discovered peers, right side is a full xterm.js terminal. Click a peer to open a shell, manage multiple sessions in tabs. Use the **Discover** button to trigger an immediate peer refresh.

### Run as a background service (recommended)

```bash
easy-service install shellcluster -- shellcluster start --no-open
```

The daemon runs in the background and auto-starts on login.

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
| `shellcluster start` | Start daemon (tunnel + shell server + discovery + dashboard) |
| `shellcluster start --no-tunnel --port 8765` | Start in local mode (no tunnel) |
| `shellcluster start --show-self` | Include this node's sessions in the dashboard |
| `shellcluster start --no-open` | Don't auto-open browser on start |
| `shellcluster register` | Register this machine to the cluster |
| `shellcluster unregister` | Remove this machine from the cluster |
| `shellcluster peers` | List discovered peers |
| `shellcluster config` | Show config path and all values |
| `shellcluster config <key> [value]` | Get or set a config value (e.g. `node.name`) |
| `shellcluster dashboard` | Open web dashboard |
| `--version` | Show version and git hash |
| `-v` / `--verbose` | Enable debug logging |

## Configuration

| OS | Config path |
|---|---|
| macOS | `~/Library/Application Support/shell-cluster/config.toml` |
| Linux | `~/.config/shell-cluster/config.toml` |
| Windows | `%APPDATA%\shell-cluster\config.toml` |

```toml
[node]
name = "my-macbook"        # Node name, shown in peers and dashboard
label = "shellcluster"     # Tunnel label — same label = same cluster
dashboard_port = 9000      # Dashboard HTTP server port

[tunnel]
backend = "devtunnel"      # Tunnel backend
expiration = "30d"          # Tunnel auto-expiration

[shell]
command = ""               # Default shell (empty = auto-detect)
```

## Development

See [DESIGN.md](DESIGN.md) ([中文](DESIGN_CN.md)) for architecture details.

```bash
git clone git@github.com:billxc/shell-cluster.git
cd shell-cluster
uv sync
uv run shellcluster start --no-tunnel --name test --port 8765
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

### Preview service manifest

```bash
easy-service render shellcluster -- shellcluster start --no-open
```

Prints the service manifest (plist / systemd unit / task XML) without installing.

### Programmatic usage

Other Python projects can also register shell-cluster as a service:

```python
from easy_service import ServiceSpec, manager_for_platform

spec = ServiceSpec(
    name="shellcluster",
    command=["shellcluster", "start", "--no-open"],
    keep_alive=True,
)

manager = manager_for_platform()
manager.install(spec)       # install + auto-start
manager.status("shellcluster")  # check status
```

## Roadmap

- [x] macOS + Linux support (PTY)
- [x] Windows support (winpty/conpty)
- [x] Local mode (no tunnel)
- [x] MS Dev Tunnel backend
- [x] Web Dashboard (xterm.js)
- [x] Session reconnect with scrollback replay
- [x] Server-side health checks (HTTP ping every 10s)
- [x] Auto-register on first start
- [x] [easy-service](https://github.com/billxc/easy-service) integration for system service registration
- [ ] E2E encryption
- [ ] File transfer

## License

MIT
