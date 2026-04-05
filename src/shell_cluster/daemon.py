"""Daemon orchestrator - ties together tunnel, shell server, discovery, and dashboard."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import sys

from shell_cluster.config import Config
from shell_cluster.tunnel.discovery import PeerDiscovery
from shell_cluster.shell.server import ShellServer
from shell_cluster.shell.manager import ShellManager
from shell_cluster.web.server import DashboardServer

log = logging.getLogger(__name__)

DISCOVERY_INTERVAL = 30  # seconds

# Track child PIDs globally so atexit can clean them up
_child_pids: set[int] = set()


def _cleanup_children() -> None:
    """Kill any remaining child processes on exit."""
    for pid in _child_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


atexit.register(_cleanup_children)


class Daemon:
    """Main daemon that manages tunnel, shell server, discovery, and dashboard."""

    def __init__(self, config: Config, no_tunnel: bool = False, local_port: int | None = None, no_open: bool = False):
        self._config = config
        self._no_tunnel = no_tunnel
        self._no_open = no_open
        self._tunnel_backend = None
        self._shell_manager = ShellManager(config.get_shell_command())
        if not self._no_tunnel:
            self._server = ShellServer(
                self._shell_manager,
                config.node.name,
                port=0,
            )
        else:
            self._server = ShellServer(
                self._shell_manager,
                config.node.name,
                local_port or 8765,
            )
        from shell_cluster.tunnel.base import make_tunnel_id
        self._tunnel_id = make_tunnel_id(config.node.name)
        self._host_process: asyncio.subprocess.Process | None = None
        self._discovery: PeerDiscovery | None = None
        self._discovery_task: asyncio.Task | None = None
        self._dashboard: DashboardServer | None = None
        self._tunnel_connect_procs: dict[str, asyncio.subprocess.Process] = {}
        self._peer_ports: dict[str, int] = {}  # peer_name -> local_port
        self._stop_event = asyncio.Event()
        self._stopping = False

    def _get_tunnel_backend(self):
        if self._tunnel_backend is None:
            from shell_cluster.tunnel.base import get_tunnel_backend
            self._tunnel_backend = get_tunnel_backend(self._config.tunnel.backend)
        return self._tunnel_backend

    def _get_peers_for_dashboard(self) -> list[dict]:
        """Build peer list for dashboard: self + config peers + discovered peers."""
        peers: list[dict] = []
        seen: set[str] = set()

        # Self (always first)
        self_uri = f"ws://localhost:{self._server.port}"
        peers.append({
            "name": self._config.node.name,
            "uri": self_uri,
            "status": "online",
        })
        seen.add(self._config.node.name)

        # Config peers (manual)
        for p in self._config.peers:
            if p.name in seen:
                continue
            uri = p.uri
            if not uri.startswith("ws://") and not uri.startswith("wss://"):
                uri = f"ws://{uri}"
            peers.append({"name": p.name, "uri": uri, "status": "online"})
            seen.add(p.name)

        # Discovered peers (mapped to localhost via devtunnel connect)
        for name, local_port in self._peer_ports.items():
            if name in seen:
                continue
            peers.append({
                "name": name,
                "uri": f"ws://localhost:{local_port}",
                "status": "online",
            })
            seen.add(name)

        return peers

    async def start(self) -> None:
        """Start all components."""
        log.info("Starting daemon for node '%s'", self._config.node.name)

        # Register signal handlers
        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.stop()))

        if not self._no_tunnel:
            await self._server.start()
            actual_port = self._server.port
            log.info("Shell server on port %d", actual_port)

            backend = self._get_tunnel_backend()
            await backend.ensure_tunnel(
                tunnel_id=self._tunnel_id,
                port=actual_port,
                label=self._config.node.label,
                expiration=self._config.tunnel.expiration,
            )

            log.info("Starting tunnel host...")
            self._host_process = await backend.host(self._tunnel_id, actual_port)
            if self._host_process.pid:
                _child_pids.add(self._host_process.pid)

            # Start discovery loop
            backend = self._get_tunnel_backend()
            self._discovery = PeerDiscovery(
                backend=backend,
                label=self._config.node.label,
                own_tunnel_id=self._tunnel_id,
                interval=DISCOVERY_INTERVAL,
                on_peers_changed=self._on_peers_changed,
            )
            self._discovery_task = asyncio.create_task(self._discovery.run_loop())
        else:
            await self._server.start()

        # Start dashboard server
        self._dashboard = DashboardServer(
            host="127.0.0.1",
            port=self._config.node.dashboard_port,
            no_open=self._no_open,
            get_peers=self._get_peers_for_dashboard,
        )
        await self._dashboard.start()

        mode = "local" if self._no_tunnel else f"tunnel={self._tunnel_id}"
        log.info(
            "Daemon running: node=%s, %s, shell=%d, dashboard=%d",
            self._config.node.name,
            mode,
            self._server.port,
            self._config.node.dashboard_port,
        )

    async def _on_peers_changed(self, peers: list) -> None:
        """Called when discovery finds new/lost peers. Manage devtunnel connect."""
        backend = self._get_tunnel_backend()
        current_names = {p.name for p in peers if p.name != self._config.node.name}
        connected_names = set(self._peer_ports.keys())

        # Connect to new peers
        for peer in peers:
            if peer.name == self._config.node.name:
                continue
            if peer.name in connected_names:
                continue
            if not peer.port:
                continue
            try:
                proc, local_port = await backend.connect(peer.tunnel_id, peer.port)
                self._tunnel_connect_procs[peer.name] = proc
                self._peer_ports[peer.name] = local_port
                if proc.pid:
                    _child_pids.add(proc.pid)
                log.info("Mapped peer %s -> localhost:%d", peer.name, local_port)
            except Exception as e:
                log.warning("Failed to connect to peer %s: %s", peer.name, e)

        # Disconnect lost peers
        for name in connected_names - current_names:
            proc = self._tunnel_connect_procs.pop(name, None)
            if proc:
                try:
                    proc.kill()
                    if proc.pid:
                        _child_pids.discard(proc.pid)
                except ProcessLookupError:
                    pass
            self._peer_ports.pop(name, None)
            log.info("Disconnected peer %s", name)

    async def stop(self) -> None:
        """Stop all components and clean up."""
        if self._stopping:
            return
        self._stopping = True
        log.info("Stopping daemon...")

        if self._discovery:
            self._discovery.stop()
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass

        if self._dashboard:
            await self._dashboard.stop()

        await self._server.stop()

        # Kill tunnel connect processes
        for name, proc in self._tunnel_connect_procs.items():
            try:
                proc.kill()
                if proc.pid:
                    _child_pids.discard(proc.pid)
            except ProcessLookupError:
                pass
        self._tunnel_connect_procs.clear()
        self._peer_ports.clear()

        # Kill host process
        if self._host_process:
            try:
                self._host_process.kill()
                await self._host_process.wait()
                if self._host_process.pid:
                    _child_pids.discard(self._host_process.pid)
            except ProcessLookupError:
                pass

        self._stop_event.set()
        log.info("Daemon stopped")

    async def run_forever(self) -> None:
        """Start and run until stopped."""
        await self.start()
        try:
            if self._host_process:
                await self._host_process.wait()
            else:
                await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if not self._stopping:
                await self.stop()
