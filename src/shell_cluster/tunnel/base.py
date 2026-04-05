"""Abstract tunnel backend interface and shared utilities."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from shell_cluster.models import TunnelInfo

# Tunnel ID format: shellcluster-<node-id>-shellcluster
# devtunnel appends a region suffix: shellcluster-<node-id>-shellcluster.jpe1
TUNNEL_PREFIX = "shellcluster-"
TUNNEL_SUFFIX = "-shellcluster"


def make_tunnel_id(node_name: str) -> str:
    """Create a tunnel ID from a node name."""
    return f"{TUNNEL_PREFIX}{node_name}{TUNNEL_SUFFIX}"


def parse_node_name(tunnel_id: str) -> str:
    """Extract node name from a tunnel ID, stripping region suffix.

    Examples:
        shellcluster-my-mac-shellcluster.jpe1 -> my-mac
        shellcluster-my-mac-shellcluster -> my-mac
    """
    base = tunnel_id.split(".")[0] if "." in tunnel_id else tunnel_id
    if base.startswith(TUNNEL_PREFIX) and base.endswith(TUNNEL_SUFFIX):
        return base[len(TUNNEL_PREFIX):-len(TUNNEL_SUFFIX)]
    return tunnel_id


def get_tunnel_backend(backend_name: str = "devtunnel") -> TunnelBackend:
    """Create a tunnel backend by name."""
    if backend_name == "devtunnel":
        from shell_cluster.tunnel.devtunnel import DevTunnelBackend
        return DevTunnelBackend()
    raise ValueError(f"Unknown tunnel backend: {backend_name}")


@runtime_checkable
class TunnelBackend(Protocol):
    """Protocol for tunnel backends (devtunnel, cloudflare, etc.)."""

    async def create(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        expiration: str = "8h",
    ) -> TunnelInfo:
        """Create a new tunnel with a port forwarding."""
        ...

    async def host(self, tunnel_id: str, port: int) -> asyncio.subprocess.Process:
        """Start hosting the tunnel. Returns the long-running process."""
        ...

    async def list_tunnels(self, label: str) -> list[TunnelInfo]:
        """List all tunnels with the given label."""
        ...

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """Get the public forwarding URI for a tunnel port."""
        ...

    async def get_port_and_uri(self, tunnel_id: str) -> tuple[int, str]:
        """Get (remote_port, forwarding_uri) for the first port on a tunnel."""
        ...

    async def connect(
        self, tunnel_id: str, remote_port: int, local_port: int = 0,
    ) -> tuple[asyncio.subprocess.Process, int]:
        """Map a tunnel's remote port to a local port.

        Returns (process, actual_local_port).
        """
        ...

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        ...

    async def exists(self, tunnel_id: str) -> bool:
        """Check if a tunnel exists."""
        ...

    async def ensure_tunnel(
        self, tunnel_id: str, port: int, label: str, expiration: str = "8h",
    ) -> None:
        """Ensure tunnel exists with the right port — reuse or create."""
        ...
