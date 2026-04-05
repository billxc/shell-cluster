"""Cloudflare Tunnel backend implementation (TCP mode).

Uses `cloudflared` CLI for tunnel management. Pure TCP forwarding — no domain,
no SSL certificates, no public URLs. Same-account `cloudflared login` = auth.

Server: cloudflared tunnel run --url tcp://localhost:PORT <tunnel-name>
Client: cloudflared access tcp --hostname <tunnel-uuid>.cfargotunnel.com --url localhost:PORT
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

from shell_cluster.models import TunnelInfo
from shell_cluster.tunnel.base import TUNNEL_PREFIX, TUNNEL_SUFFIX, parse_node_name

log = logging.getLogger(__name__)


class CloudflareBackend:
    """Wraps the `cloudflared` CLI for TCP tunnel management."""

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

    async def _get_tunnel_uuid(self, tunnel_name: str) -> str:
        """Get the UUID for a tunnel by name."""
        try:
            data = await self._run_json("tunnel", "list")
        except RuntimeError:
            return ""
        items = data if isinstance(data, list) else []
        for item in items:
            if item.get("name") == tunnel_name:
                return item.get("id", "")
        return ""

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
        """Create a tunnel (no DNS, no domain)."""
        await self._run("tunnel", "create", tunnel_id)
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
        """Start hosting the tunnel (TCP mode)."""
        cmd = [
            "cloudflared", "tunnel", "run",
            "--url", f"tcp://localhost:{port}",
            tunnel_id,
        ]
        log.info("Starting tunnel host: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return proc

    async def list_tunnels(self, label: str) -> list[TunnelInfo]:
        """List all tunnels matching our naming convention."""
        try:
            data = await self._run_json("tunnel", "list")
        except RuntimeError:
            log.warning("Failed to list tunnels")
            return []

        tunnels = []
        items = data if isinstance(data, list) else []
        for item in items:
            name = item.get("name", "")
            if not (name.startswith(TUNNEL_PREFIX) and TUNNEL_SUFFIX in name):
                continue

            conns = item.get("connections", [])
            hosting = len(conns) > 0
            uuid = item.get("id", "")

            tunnels.append(TunnelInfo(
                tunnel_id=name,
                labels=[label],
                port=0,
                description=parse_node_name(name),
                hosting=hosting,
                forwarding_uri=uuid,  # store UUID for connect()
            ))
        return tunnels

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """Get the tunnel UUID (used as hostname for access tcp)."""
        return await self._get_tunnel_uuid(tunnel_id)

    async def get_port_and_uri(self, tunnel_id: str) -> tuple[int, str]:
        """Get (port=0, tunnel_uuid)."""
        uuid = await self._get_tunnel_uuid(tunnel_id)
        return 0, uuid

    async def connect(
        self, tunnel_id: str, remote_port: int, local_port: int = 0,
    ) -> tuple[asyncio.subprocess.Process | None, str]:
        """Connect to a peer via cloudflared access tcp, mapping to localhost."""
        # Get tunnel UUID
        uuid = await self._get_tunnel_uuid(tunnel_id)
        if not uuid:
            raise RuntimeError(f"Cannot find UUID for tunnel {tunnel_id}")

        hostname = f"{uuid}.cfargotunnel.com"

        # Allocate random local port
        if local_port == 0:
            with socket.socket() as s:
                s.bind(("", 0))
                local_port = s.getsockname()[1]

        cmd = [
            "cloudflared", "access", "tcp",
            "--hostname", hostname,
            "--url", f"localhost:{local_port}",
        ]
        log.info("Connecting tunnel: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(2)
        if proc.returncode is not None:
            raise RuntimeError(f"cloudflared access tcp failed for {tunnel_id}")
        return proc, f"ws://localhost:{local_port}"

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        try:
            await self._run("tunnel", "cleanup", tunnel_id, check=False)
            await self._run("tunnel", "delete", tunnel_id, check=True)
        except RuntimeError:
            log.warning("Failed to delete tunnel %s", tunnel_id)
