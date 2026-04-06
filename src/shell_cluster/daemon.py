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

    def __init__(self, config: Config, no_tunnel: bool = False, local_port: int | None = None, no_open: bool = False, show_self: bool = False):
        self._config = config
        self._no_tunnel = no_tunnel
        self._no_open = no_open
        self._show_self = show_self
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
        self._peer_uris: dict[str, str] = {}  # peer_name -> ws:// or wss:// URI
        self._stop_event = asyncio.Event()
        self._stopping = False
        self._stopped = False

    def _get_tunnel_backend(self):
        if self._tunnel_backend is None:
            from shell_cluster.tunnel.base import get_tunnel_backend
            self._tunnel_backend = get_tunnel_backend(self._config.tunnel.backend)
        return self._tunnel_backend

    def _get_peers_for_dashboard(self) -> list[dict]:
        """Build peer list for dashboard: self (optional) + config peers + discovered peers."""
        peers: list[dict] = []
        seen: set[str] = set()

        # Self (only if --show-self)
        if self._show_self:
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

        # Discovered peers (connected via tunnel)
        for name, uri in self._peer_uris.items():
            if name in seen:
                continue
            peers.append({
                "name": name,
                "uri": uri,
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
                loop.add_signal_handler(sig, self._handle_signal)

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

            # Start discovery — do first refresh before opening browser
            backend = self._get_tunnel_backend()
            self._discovery = PeerDiscovery(
                backend=backend,
                label=self._config.node.label,
                own_tunnel_id=self._tunnel_id,
                interval=DISCOVERY_INTERVAL,
                on_peers_changed=self._on_peers_changed,
            )
            log.info("Discovering peers...")
            await self._discovery.refresh()
            await self._on_peers_changed(list(self._discovery.peers.values()))
            self._discovery_task = asyncio.create_task(self._discovery.run_loop())
        else:
            await self._server.start()

        # Start dashboard server (peers are already loaded)
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
        from shell_cluster.models import PeerStatus
        current_names = {p.name for p in peers if p.name != self._config.node.name and p.status == PeerStatus.ONLINE}
        connected_names = set(self._peer_uris.keys())

        # Connect to new peers, or reconnect if port changed / process died
        for peer in peers:
            if peer.name == self._config.node.name:
                continue
            if peer.status != PeerStatus.ONLINE:
                continue

            expected_uri = f"ws://localhost:{peer.port}"
            existing_uri = self._peer_uris.get(peer.name)
            existing_proc = self._tunnel_connect_procs.get(peer.name)

            # Check if existing connect process is still alive
            proc_dead = existing_proc is not None and existing_proc.returncode is not None

            if existing_uri and existing_uri == expected_uri and not proc_dead:
                # Already connected with correct port and process alive — skip
                continue

            # Tear down stale connection (port changed OR process died)
            if existing_uri:
                reason = "process died" if proc_dead else f"port changed ({existing_uri} -> {expected_uri})"
                log.info("Peer %s: %s, reconnecting", peer.name, reason)
                old_proc = self._tunnel_connect_procs.pop(peer.name, None)
                if old_proc:
                    try:
                        old_proc.kill()
                        if old_proc.pid:
                            _child_pids.discard(old_proc.pid)
                    except ProcessLookupError:
                        pass
                self._peer_uris.pop(peer.name, None)

            try:
                proc, ws_uri = await backend.connect(peer.tunnel_id, peer.port)
                if proc:
                    self._tunnel_connect_procs[peer.name] = proc
                    if proc.pid:
                        _child_pids.add(proc.pid)
                self._peer_uris[peer.name] = ws_uri
                log.info("Mapped peer %s -> %s", peer.name, ws_uri)
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
            self._peer_uris.pop(name, None)
            log.info("Disconnected peer %s", name)

    def _handle_signal(self) -> None:
        """Handle SIGINT/SIGTERM. Second signal forces immediate exit."""
        if self._stopping:
            log.warning("Second signal received, forcing exit")
            _cleanup_children()
            os._exit(1)
        self._stopping = True
        asyncio.create_task(self.stop())

    async def stop(self) -> None:
        """Stop all components and clean up (with timeouts to avoid hanging)."""
        if self._stopped:
            return
        self._stopped = True
        self._stopping = True
        log.info("Stopping daemon...")

        if self._discovery:
            self._discovery.stop()
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await asyncio.wait_for(self._discovery_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._dashboard:
            try:
                await asyncio.wait_for(self._dashboard.stop(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("Dashboard stop timed out")

        try:
            await asyncio.wait_for(self._server.stop(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("Shell server stop timed out")

        # Kill tunnel connect processes
        for name, proc in self._tunnel_connect_procs.items():
            try:
                proc.kill()
                if proc.pid:
                    _child_pids.discard(proc.pid)
            except ProcessLookupError:
                pass
        self._tunnel_connect_procs.clear()
        self._peer_uris.clear()

        # Kill host process
        if self._host_process:
            try:
                self._host_process.kill()
                await asyncio.wait_for(self._host_process.wait(), timeout=2.0)
                if self._host_process.pid:
                    _child_pids.discard(self._host_process.pid)
            except (ProcessLookupError, asyncio.TimeoutError):
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
            # Force exit — PTY reader threads may block normal shutdown
            _cleanup_children()
            os._exit(0)
