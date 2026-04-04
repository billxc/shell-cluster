"""Abstract tunnel backend interface."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from shell_cluster.models import TunnelInfo


@runtime_checkable
class TunnelBackend(Protocol):
    """Protocol for tunnel backends (devtunnel, cloudflare, etc.)."""

    async def create(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        description: str = "",
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

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        ...
