"""MS Dev Tunnel backend implementation."""

from __future__ import annotations

import asyncio
import json
import logging

from shell_cluster.models import TunnelInfo
from shell_cluster.tunnel.base import parse_node_name

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
        output = output.strip()
        if not output:
            return {}
        # devtunnel may prepend non-JSON text (welcome banner etc.)
        # Find the first '{' or '['
        for i, ch in enumerate(output):
            if ch in ('{', '['):
                try:
                    return json.loads(output[i:])
                except json.JSONDecodeError:
                    pass
        log.warning("Could not parse devtunnel JSON output: %s", output[:200])
        return {}

    async def exists(self, tunnel_id: str) -> bool:
        """Check if a tunnel exists."""
        try:
            await self._run("show", tunnel_id, check=True)
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
        """Create a tunnel and add a port."""
        await self._run(
            "create", tunnel_id,
            "--labels", label,
            "--expiration", expiration,
        )

        await self._run("port", "create", tunnel_id, "-p", str(port))

        return TunnelInfo(
            tunnel_id=tunnel_id,
            labels=[label],
            port=port,
            description=parse_node_name(tunnel_id),
        )

    async def ensure_tunnel(self, tunnel_id: str, port: int, label: str, expiration: str = "8h") -> None:
        """Ensure tunnel exists with the right port — reuse if present, create if not."""
        if await self.exists(tunnel_id):
            log.info("Reusing existing tunnel %s", tunnel_id)
            # Update port: delete old ports and add the new one
            # (port may change on restart since we use random ports)
            try:
                data = await self._run_json("show", tunnel_id)
                tunnel_data = data.get("tunnel", data)
                for p in tunnel_data.get("ports", []):
                    old_port = p.get("portNumber", 0)
                    if old_port and old_port != port:
                        await self._run("port", "delete", tunnel_id, "-p", str(old_port), check=False)
                await self._run("port", "create", tunnel_id, "-p", str(port), check=False)
            except Exception as e:
                log.warning("Failed to update tunnel port: %s", e)
        else:
            log.info("Creating new tunnel %s", tunnel_id)
            await self.create(tunnel_id, port, label, expiration)

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
        items = data if isinstance(data, list) else data.get("tunnels", data.get("value", []))
        for item in items:
            tid = item.get("tunnelId", "")
            desc = item.get("description", "")
            labels = item.get("labels", [])
            ports = item.get("ports", [])
            port = ports[0].get("portNumber", 0) if ports else 0
            host_conns = item.get("hostConnections", 0)
            tunnels.append(TunnelInfo(
                tunnel_id=tid,
                labels=labels,
                port=port,
                description=desc,
                hosting=host_conns > 0,
            ))
        return tunnels

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """Get forwarding URI from devtunnel show --json."""
        try:
            data = await self._run_json("show", tunnel_id)
        except Exception:
            log.warning("Failed to get forwarding URI for %s", tunnel_id)
            return ""

        # JSON structure: {"tunnel": {"ports": [{"portNumber": N, "portUri": "..."}]}}
        tunnel_data = data.get("tunnel", data)
        for p in tunnel_data.get("ports", []):
            pnum = p.get("portNumber", 0)
            # Match specific port, or take first port if port=0 (from list without port details)
            if pnum == port or port == 0:
                uri = p.get("portUri", "")
                if uri:
                    return uri
                uris = p.get("portForwardingUris", [])
                if uris:
                    return uris[0]

        log.warning("Could not determine forwarding URI for %s:%d", tunnel_id, port)
        return ""

    async def get_port_and_uri(self, tunnel_id: str) -> tuple[int, str]:
        """Get (remote_port, forwarding_uri) for the first port on a tunnel."""
        try:
            data = await self._run_json("show", tunnel_id)
        except Exception:
            return 0, ""
        tunnel_data = data.get("tunnel", data)
        for p in tunnel_data.get("ports", []):
            port = p.get("portNumber", 0)
            uri = p.get("portUri", "")
            if port:
                return port, uri
        return 0, ""

    async def connect(
        self, tunnel_id: str, remote_port: int, local_port: int = 0,
    ) -> tuple[asyncio.subprocess.Process | None, str]:
        """Connect to a tunnel, mapping its ports locally.

        devtunnel connect automatically maps configured ports to the same
        local port numbers. Returns (process, ws_uri).
        """
        cmd = ["devtunnel", "connect", tunnel_id]
        log.info("Connecting tunnel: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Wait for connection to establish
        await asyncio.sleep(3)
        if proc.returncode is not None:
            stderr = (await proc.stderr.read()).decode() if proc.stderr else ""
            raise RuntimeError(f"devtunnel connect failed: {stderr}")
        # devtunnel maps remote port to the same local port
        return proc, f"ws://localhost:{remote_port}"

    async def delete(self, tunnel_id: str) -> None:
        """Delete a tunnel."""
        try:
            await self._run("delete", tunnel_id, "-f")
        except RuntimeError:
            log.warning("Failed to delete tunnel %s", tunnel_id)
