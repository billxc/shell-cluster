"""Cloudflare Tunnel backend implementation.

Uses `cloudflared` CLI for tunnel management and Cloudflare API for discovery.
Requires: `cloudflared tunnel login` on each machine (same Cloudflare account).
"""

from __future__ import annotations

import asyncio
import json
import logging

from shell_cluster.models import TunnelInfo
from shell_cluster.tunnel.base import parse_node_name

log = logging.getLogger(__name__)


class CloudflareBackend:
    """Wraps the `cloudflared` CLI for tunnel management."""

    def __init__(self, domain: str = ""):
        self._domain = domain  # e.g. "shellcluster.yourdomain.com"

    def set_domain(self, domain: str) -> None:
        self._domain = domain

    async def _run(self, *args: str, check: bool = True) -> str:
        """Run a cloudflared command and return stdout."""
        cmd = ["cloudflared", *args]
        log.debug("Running: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"cloudflared {args[0]} failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )
        return stdout.decode()

    async def _run_json(self, *args: str) -> list | dict:
        """Run a cloudflared command with --output json and parse output."""
        output = await self._run(*args, "--output", "json")
        output = output.strip()
        if not output:
            return []
        for i, ch in enumerate(output):
            if ch in ('{', '['):
                try:
                    return json.loads(output[i:])
                except json.JSONDecodeError:
                    pass
        return []

    async def exists(self, tunnel_id: str) -> bool:
        """Check if a tunnel exists."""
        try:
            await self._run("tunnel", "info", tunnel_id, check=True)
            return True
        except RuntimeError:
            return False

    async def create(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        expiration: str = "8h",
    ) -> TunnelInfo:
        """Create a tunnel and set up DNS route."""
        await self._run("tunnel", "create", tunnel_id)

        # Route DNS: node-name.shellcluster.yourdomain.com → tunnel
        if self._domain:
            node_name = parse_node_name(tunnel_id)
            hostname = f"{node_name}.{self._domain}"
            try:
                await self._run("tunnel", "route", "dns", tunnel_id, hostname)
                log.info("DNS route: %s -> %s", hostname, tunnel_id)
            except RuntimeError as e:
                log.warning("DNS route failed (may already exist): %s", e)

        return TunnelInfo(
            tunnel_id=tunnel_id,
            labels=[label],
            port=port,
            description=parse_node_name(tunnel_id),
        )

    async def ensure_tunnel(
        self, tunnel_id: str, port: int, label: str, expiration: str = "8h",
    ) -> None:
        """Ensure tunnel exists — reuse if present, create if not."""
        if await self.exists(tunnel_id):
            log.info("Reusing existing tunnel %s", tunnel_id)
        else:
            log.info("Creating new tunnel %s", tunnel_id)
            await self.create(tunnel_id, port, label, expiration)

    async def host(self, tunnel_id: str, port: int) -> asyncio.subprocess.Process:
        """Start hosting the tunnel as a long-running subprocess."""
        cmd = [
            "cloudflared", "tunnel", "run",
            "--url", f"http://localhost:{port}",
            tunnel_id,
        ]
        log.info("Starting tunnel host: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc

    async def list_tunnels(self, label: str) -> list[TunnelInfo]:
        """List all tunnels matching our naming convention."""
        try:
            data = await self._run_json("tunnel", "list")
        except RuntimeError:
            log.warning("Failed to list tunnels")
            return []

        from shell_cluster.tunnel.base import TUNNEL_PREFIX, TUNNEL_SUFFIX

        tunnels = []
        items = data if isinstance(data, list) else []
        for item in items:
            tid = item.get("name", "") or item.get("id", "")
            # Filter by naming convention
            if not (tid.startswith(TUNNEL_PREFIX) and TUNNEL_SUFFIX in tid):
                continue

            # Check if actively connected
            conns = item.get("connections", [])
            hosting = len(conns) > 0

            tunnels.append(TunnelInfo(
                tunnel_id=tid,
                labels=[label],
                port=0,
                description=parse_node_name(tid),
                hosting=hosting,
            ))
        return tunnels

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """Get the public URI for a tunnel."""
        if self._domain:
            node_name = parse_node_name(tunnel_id)
            return f"wss://{node_name}.{self._domain}"
        return ""

    async def get_port_and_uri(self, tunnel_id: str) -> tuple[int, str]:
        """Get (port, forwarding_uri). Port is 0 for cloudflare (single ingress)."""
        uri = await self.get_forwarding_uri(tunnel_id, 0)
        # Port doesn't matter for cloudflare — one tunnel = one service
        return 0, uri

    async def connect(
        self, tunnel_id: str, remote_port: int, local_port: int = 0,
    ) -> tuple[asyncio.subprocess.Process | None, str]:
        """Connect to a peer. Cloudflare uses direct wss:// — no local proxy needed."""
        uri = await self.get_forwarding_uri(tunnel_id, remote_port)
        if not uri:
            raise RuntimeError(f"No domain configured for tunnel {tunnel_id}")
        # No process needed — browser/proxy connects directly to public URL
        return None, uri

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        try:
            # Clean up connections first
            await self._run("tunnel", "cleanup", tunnel_id, check=False)
            await self._run("tunnel", "delete", tunnel_id, check=True)
        except RuntimeError:
            log.warning("Failed to delete tunnel %s", tunnel_id)
