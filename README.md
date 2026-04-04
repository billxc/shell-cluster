# shell-cluster

Remote access to all your shells across machines via tunnels.

## Install

```bash
uv tool install shell-cluster
```

## Quick Start

```bash
# Register this machine
shellcluster register --name my-machine

# Start the daemon
shellcluster start

# On another machine, see peers
shellcluster peers

# Connect to a peer
shellcluster connect my-machine

# Or use the TUI dashboard
shellcluster dashboard
```
