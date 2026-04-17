"""Tailscale tunnel backend using userspace networking mode."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from shell_cluster.models import TunnelInfo

log = logging.getLogger(__name__)


def _default_socket() -> str:
    """Return the tailscale socket path in shell-cluster's config directory."""
    if sys.platform == "win32":
        return ""
    from shell_cluster.config import CONFIG_DIR
    return str(CONFIG_DIR / "tailscaled.sock")


def _parse_hostname(hostname: str, default_port: int) -> tuple[str, int]:
    """Parse a Tailscale hostname, extracting an optional port suffix.

    Convention: ``<name>-p<port>`` encodes a custom port.

    Examples:
        "work-pc"       -> ("work-pc", default_port)
        "work-pc-p9877" -> ("work-pc", 9877)
        "server-2"      -> ("server-2", default_port)   # not a port suffix
    """
    if "-p" in hostname:
        base, _, suffix = hostname.rpartition("-p")
        if base and suffix.isdigit():
            return base, int(suffix)
    return hostname, default_port


class TailscaleBackend:
    """Tunnel backend using Tailscale mesh network.

    Requires tailscaled running in userspace-networking mode.
    Connections are piped through `tailscale nc` via a local TCP proxy.
    """

    def __init__(self, port: int = 9876):
        self._port = port
        self._socket = _default_socket()
        self._hostname_to_ip: dict[str, str] = {}

    def _socket_args(self) -> list[str]:
        """Return --socket args if a custom socket path is configured."""
        if self._socket:
            return ["--socket", self._socket]
        return []

    async def _run_tailscale(self, *args: str, check: bool = True) -> str:
        """Run a tailscale CLI command and return stdout."""
        cmd = ["tailscale", *self._socket_args(), *args]
        log.debug("Running: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"tailscale {args[0]} failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )
        return stdout.decode()

    async def _get_status(self) -> dict:
        """Get parsed output of `tailscale status --json`."""
        output = await self._run_tailscale("status", "--json")
        return json.loads(output)

    async def exists(self, tunnel_id: str) -> bool:
        """Always returns True — Tailscale manages peer existence."""
        return True

    async def create(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        expiration: str = "",
    ) -> TunnelInfo:
        """No-op. Returns a TunnelInfo with the provided parameters."""
        return TunnelInfo(
            tunnel_id=tunnel_id,
            labels=[label],
            port=port,
        )

    async def ensure_tunnel(
        self,
        tunnel_id: str,
        port: int,
        label: str,
        expiration: str = "30d",
    ) -> None:
        """Verify that Tailscale is connected."""
        try:
            status = await self._get_status()
        except Exception as e:
            raise RuntimeError(
                f"Tailscale is not running: {e}. "
                "Start it with: tailscaled --tun=userspace-networking"
            ) from e

        state = status.get("BackendState", "")
        if state != "Running":
            raise RuntimeError(
                f"Tailscale is not connected (state: {state}). "
                "Run 'tailscale up' to connect."
            )

    async def host(self, tunnel_id: str, port: int) -> asyncio.subprocess.Process | None:
        """No host process needed — tailscaled handles connectivity."""
        log.info("Tailscale backend: no host process needed")
        return None

    async def list_tunnels(self, label: str) -> list[TunnelInfo]:
        """List online Tailscale peers as TunnelInfo entries."""
        try:
            status = await self._get_status()
        except Exception as e:
            log.warning("Failed to get tailscale status: %s", e)
            return []

        self_hostname = status.get("Self", {}).get("HostName", "")
        peers = status.get("Peer", {})
        tunnels = []

        for _key, peer in peers.items():
            hostname = peer.get("HostName", "")
            online = peer.get("Online", False)

            if not hostname or not online:
                continue

            # Skip self (in case Self hostname appears in Peer list)
            if hostname == self_hostname:
                continue

            ips = peer.get("TailscaleIPs", [])
            if not ips:
                continue

            # Store the IPv4 address mapping
            ipv4 = next((ip for ip in ips if "." in ip), ips[0])
            self._hostname_to_ip[hostname] = ipv4

            # Parse optional port from hostname (e.g. "work-pc-p9877")
            name, port = _parse_hostname(hostname, self._port)

            tunnels.append(TunnelInfo(
                tunnel_id=hostname,
                hosting=True,
                port=port,
                description=name,
            ))

        return tunnels

    async def get_forwarding_uri(self, tunnel_id: str, port: int) -> str:
        """No forwarding URI in Tailscale — connections go through the proxy."""
        return ""

    async def get_port_and_uri(self, tunnel_id: str) -> tuple[int, str]:
        """Return the port for a peer. Parses hostname for custom port suffix."""
        _, port = _parse_hostname(tunnel_id, self._port)
        return port, ""

    async def connect(
        self,
        tunnel_id: str,
        remote_port: int,
        local_port: int = 0,
    ) -> tuple[asyncio.subprocess.Process | None, str]:
        """Connect to a peer by spawning a local TCP proxy through tailscale nc.

        Returns (proxy_process, ws_uri) where ws_uri is ws://localhost:<local_port>.
        """
        peer_ip = self._hostname_to_ip.get(tunnel_id)
        if not peer_ip:
            raise RuntimeError(
                f"Peer '{tunnel_id}' not discovered yet — "
                "run list_tunnels() first"
            )

        log.info("Connecting to peer %s (%s:%d) via tailscale proxy",
                 tunnel_id, peer_ip, remote_port)

        nc_cmd = ["tailscale", *self._socket_args(), "nc"]

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "shell_cluster.tunnel.tailscale_proxy",
            "--peer-ip", peer_ip,
            "--peer-port", str(remote_port),
            "--tailscale-cmd", *nc_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for the proxy to report its listening port
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Tailscale proxy for {tunnel_id} did not start in time"
            )

        line_str = line.decode().strip()
        if not line_str.startswith("LISTENING:"):
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Unexpected proxy output: {line_str}"
            )

        actual_port = int(line_str.split(":")[1])
        ws_uri = f"ws://localhost:{actual_port}"
        log.info("Proxy for %s listening on %s", tunnel_id, ws_uri)

        return proc, ws_uri

    async def delete(self, tunnel_id: str) -> None:
        """No-op — Tailscale peers are managed by the tailnet."""
        log.debug("Tailscale backend: delete is a no-op for %s", tunnel_id)
