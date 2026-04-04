"""MS Dev Tunnel backend implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from shell_cluster.models import TunnelInfo

log = logging.getLogger(__name__)


class DevTunnelBackend:
    """Wraps the `devtunnel` CLI for tunnel management."""

    async def _run(self, *args: str, check: bool = True) -> str:
        """Run a devtunnel command and return stdout."""
        cmd = ["devtunnel", *args]
        log.debug("Running: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"devtunnel {args[0]} failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )
        return stdout.decode()

    async def _run_json(self, *args: str) -> dict:
        """Run a devtunnel command with --json flag and parse output."""
        output = await self._run(*args, "--json")
        return json.loads(output)

    async def create(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        description: str = "",
        expiration: str = "8h",
    ) -> TunnelInfo:
        """Create a tunnel, add a port, set description."""
        # Create tunnel with label and expiration
        await self._run(
            "create", tunnel_id,
            "--labels", label,
            "--expiration", expiration,
            "--allow-anonymous",
        )

        # Add port
        await self._run("port", "create", tunnel_id, "-p", str(port))

        # Set description (node name)
        if description:
            await self._run("update", tunnel_id, "-d", description)

        return TunnelInfo(
            tunnel_id=tunnel_id,
            labels=[label],
            port=port,
            description=description,
        )

    async def host(self, tunnel_id: str, port: int) -> asyncio.subprocess.Process:
        """Start hosting the tunnel as a long-running subprocess."""
        cmd = ["devtunnel", "host", tunnel_id]
        log.info("Starting tunnel host: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc

    async def list_tunnels(self, label: str) -> list[TunnelInfo]:
        """List all tunnels with the given label."""
        try:
            data = await self._run_json("list", "--labels", label)
        except RuntimeError:
            log.warning("Failed to list tunnels")
            return []

        tunnels = []
        for item in data if isinstance(data, list) else data.get("value", []):
            tid = item.get("tunnelId", "")
            desc = item.get("description", "")
            labels = item.get("labels", [])
            ports = item.get("ports", [])
            port = ports[0].get("portNumber", 0) if ports else 0
            tunnels.append(TunnelInfo(
                tunnel_id=tid,
                labels=labels,
                port=port,
                description=desc,
            ))
        return tunnels

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """Get forwarding URI by parsing verbose show output."""
        try:
            output = await self._run("show", tunnel_id, "-v", check=False)
        except Exception:
            log.warning("Failed to get forwarding URI for %s", tunnel_id)
            return ""

        # Try to find portForwardingUris in the verbose output
        # The verbose output contains HTTP response bodies as JSON
        uri_pattern = re.compile(
            r'"portForwardingUris"\s*:\s*\[\s*"(https?://[^"]+)"'
        )
        match = uri_pattern.search(output)
        if match:
            uri = match.group(1)
            # Convert https:// to wss:// for WebSocket
            return uri

        # Fallback: try to construct from tunnel info
        # Pattern: https://<tunnelId>-<port>.<cluster>.devtunnels.ms/
        # We can't reliably construct this, so try the show --json approach
        try:
            data = await self._run_json("show", tunnel_id)
            for p in data.get("ports", []):
                if p.get("portNumber") == port:
                    uris = p.get("portForwardingUris", [])
                    if uris:
                        return uris[0]
        except Exception:
            pass

        log.warning("Could not determine forwarding URI for %s:%d", tunnel_id, port)
        return ""

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        try:
            await self._run("delete", tunnel_id, "-f")
        except RuntimeError:
            log.warning("Failed to delete tunnel %s", tunnel_id)
