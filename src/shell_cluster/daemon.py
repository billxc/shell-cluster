"""Daemon orchestrator - ties together tunnel, shell server, discovery, and dashboard."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
from urllib.error import URLError

from shell_cluster.config import Config
from shell_cluster.tunnel.discovery import PeerDiscovery
from shell_cluster.shell.server import ShellServer
from shell_cluster.shell.manager import ShellManager
from shell_cluster.web.server import DashboardServer

log = logging.getLogger(__name__)

DISCOVERY_INTERVAL = 300  # seconds (5 minutes)
HEALTH_CHECK_INTERVAL = 10  # seconds
CONNECT_TIMEOUT = 15  # seconds — max wait for a single backend.connect()

# Track child PIDs globally so atexit can clean them up
_child_pids: set[int] = set()


def _cleanup_children() -> None:
    """Kill any remaining child processes on exit."""
    for pid in list(_child_pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


atexit.register(_cleanup_children)


class Daemon:
    """Main daemon that manages tunnel, shell server, discovery, and dashboard."""

    def __init__(self, config: Config, no_tunnel: bool = False, local_port: int | None = None, no_open: bool = False, no_dashboard: bool = False):
        self._config = config
        self._no_tunnel = no_tunnel
        self._no_open = no_open
        self._no_dashboard = no_dashboard
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
        self._peer_status: dict[str, str] = {}  # peer_name -> "online" | "offline"
        self._peer_lock = asyncio.Lock()  # guards _peer_uris, _peer_status, _tunnel_connect_procs
        self._health_pool = ThreadPoolExecutor(max_workers=4)  # dedicated pool for health pings
        self._health_check_task: asyncio.Task | None = None
        self._dashboard_v2_proc: asyncio.subprocess.Process | None = None
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

        # Self — always shown with (local) suffix
        self_uri = f"ws://localhost:{self._server.port}"
        peers.append({
            "name": f"{self._config.node.name} (local)",
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
                "status": self._peer_status.get(name, "offline"),
            })
            seen.add(name)

        return peers

    async def _refresh_peers(self) -> None:
        """Trigger an immediate discovery refresh."""
        if self._discovery:
            peers = await self._discovery.refresh()
            await self._on_peers_changed(peers)

    async def _start_dashboard_v2(self) -> None:
        """Spawn the dashboard v2 server as a subprocess."""
        port = self._config.node.dashboard_v2_port
        cmd = [
            sys.executable, "-m", "shell_cluster.dashboard_v2",
            "--port", str(port),
        ]
        if self._no_open:
            cmd.append("--no-open")
        self._dashboard_v2_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if self._dashboard_v2_proc.pid:
            _child_pids.add(self._dashboard_v2_proc.pid)
        log.info("Dashboard v2 started on port %d (pid=%s)", port, self._dashboard_v2_proc.pid)

    async def start(self) -> None:
        """Start all components."""
        log.info("Starting daemon for node '%s'", self._config.node.name)

        # Fail fast: check ports before slow tunnel/discovery work
        import socket
        # 9000 (API + WS proxy) always starts with the daemon
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", self._config.node.dashboard_port))
            except OSError:
                raise RuntimeError(
                    f"Dashboard port {self._config.node.dashboard_port} is already in use. "
                    f"Stop the other process or change the port."
                )
        # 9001 (static UI) only when not --no-dashboard
        if not self._no_dashboard:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", self._config.node.dashboard_v2_port))
                except OSError:
                    raise RuntimeError(
                        f"Dashboard v2 port {self._config.node.dashboard_v2_port} is already in use. "
                        f"Stop the other process or change the port."
                    )

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

            # Start discovery — first refresh happens inside run_loop
            backend = self._get_tunnel_backend()
            self._discovery = PeerDiscovery(
                backend=backend,
                label=self._config.node.label,
                own_tunnel_id=self._tunnel_id,
                interval=DISCOVERY_INTERVAL,
                on_peers_changed=self._on_peers_changed,
            )
            log.info("Discovering peers...")
            peers = await self._discovery.refresh()
            await self._on_peers_changed(peers)
            self._discovery_task = asyncio.create_task(self._discovery.run_loop(skip_first=True))
            self._health_check_task = asyncio.create_task(self._health_check_loop())
        else:
            await self._server.start()

        # Port 9000: API + WS proxy — always starts with daemon
        self._dashboard = DashboardServer(
            host="127.0.0.1",
            port=self._config.node.dashboard_port,
            no_open=True,
            get_peers=self._get_peers_for_dashboard,
            refresh_peers=self._refresh_peers,
        )
        await self._dashboard.start()

        # Port 9001: static UI dashboard (subprocess, skipped with --no-dashboard)
        if not self._no_dashboard:
            await self._start_dashboard_v2()

        mode = "local" if self._no_tunnel else f"tunnel={self._tunnel_id}"
        v2_info = f"+{self._config.node.dashboard_v2_port}" if not self._no_dashboard else ""
        dashboard_info = f"dashboard={self._config.node.dashboard_port}{v2_info}"
        log.info(
            "Daemon running: node=%s, %s, shell=%d, %s",
            self._config.node.name,
            mode,
            self._server.port,
            dashboard_info,
        )

    async def _on_peers_changed(self, peers: list) -> None:
        """Called when discovery finds new/lost peers. Manage devtunnel connect."""
        async with self._peer_lock:
            await self._on_peers_changed_locked(peers)

    async def _on_peers_changed_locked(self, peers: list) -> None:
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

            reason = "new peer"
            if existing_uri:
                reason = "process died" if proc_dead else f"port changed ({existing_uri} -> {expected_uri})"
                log.info("Peer %s: %s, reconnecting", peer.name, reason)

            try:
                proc, ws_uri = await asyncio.wait_for(
                    backend.connect(peer.tunnel_id, peer.port),
                    timeout=CONNECT_TIMEOUT,
                )
                # New connection succeeded — tear down old one now
                old_proc = self._tunnel_connect_procs.pop(peer.name, None)
                if old_proc and old_proc.returncode is None:
                    try:
                        old_proc.kill()
                        if old_proc.pid:
                            _child_pids.discard(old_proc.pid)
                    except ProcessLookupError:
                        pass
                if proc:
                    self._tunnel_connect_procs[peer.name] = proc
                    if proc.pid:
                        _child_pids.add(proc.pid)
                self._peer_uris[peer.name] = ws_uri
                self._peer_status[peer.name] = "online"
                log.info("Mapped peer %s -> %s", peer.name, ws_uri)
            except asyncio.TimeoutError:
                log.warning("Timeout connecting to peer %s (tunnel=%s)", peer.name, peer.tunnel_id)
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
            self._peer_status.pop(name, None)
            log.info("Disconnected peer %s", name)

    async def _health_check_loop(self) -> None:
        """Periodically ping each peer's HTTP /sessions endpoint to check liveness.

        When a peer becomes unreachable, kill its devtunnel connect process and
        trigger an immediate reconnect instead of waiting for the next discovery cycle.
        """
        loop = asyncio.get_event_loop()
        while not self._stopping:
            # Snapshot all peer dicts under lock to avoid races with _on_peers_changed
            async with self._peer_lock:
                uris_snapshot = dict(self._peer_uris)
                status_snapshot = dict(self._peer_status)
                procs_snapshot = dict(self._tunnel_connect_procs)

            peers_to_reconnect: list[str] = []

            for name, uri in uris_snapshot.items():
                http_url = uri.replace("wss://", "https://").replace("ws://", "http://") + "/sessions"
                try:
                    alive = await asyncio.wait_for(
                        loop.run_in_executor(self._health_pool, self._ping_peer, http_url),
                        timeout=3.0,
                    )
                except (asyncio.TimeoutError, Exception):
                    alive = False

                old_status = status_snapshot.get(name, "online")

                # Update status under lock
                async with self._peer_lock:
                    # Re-check: peer may have been removed by _on_peers_changed
                    if name in self._peer_uris:
                        self._peer_status[name] = "online" if alive else "offline"

                # Peer went offline — kill stale connect process and schedule reconnect
                if not alive and old_status == "online":
                    log.info("Peer %s unreachable, killing connect process", name)
                    proc = procs_snapshot.get(name)
                    if proc and proc.returncode is None:
                        try:
                            proc.kill()
                            if proc.pid:
                                _child_pids.discard(proc.pid)
                        except ProcessLookupError:
                            pass
                    peers_to_reconnect.append(name)

            # Trigger immediate reconnect for peers that went offline
            if peers_to_reconnect and self._discovery and not self._stopping:
                log.info("Triggering reconnect for: %s", ", ".join(peers_to_reconnect))
                try:
                    peers = await self._discovery.refresh()
                    await self._on_peers_changed(peers)
                except Exception as e:
                    log.warning("Reconnect refresh failed: %s", e)

            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    @staticmethod
    def _ping_peer(url: str) -> bool:
        """Blocking HTTP GET to check if peer is reachable."""
        try:
            with urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except (URLError, OSError):
            return False

    def _handle_signal(self) -> None:
        """Handle SIGINT/SIGTERM. Second signal forces immediate exit."""
        if self._stopping:
            log.warning("Second signal received, forcing exit")
            _cleanup_children()
            os._exit(1)
        self._stopping = True
        self._stop_task = asyncio.create_task(self._safe_stop())

    async def _safe_stop(self) -> None:
        """Wrapper around stop() that ensures _stop_event is always set."""
        try:
            await self.stop()
        except Exception as e:
            log.error("Error during stop: %s", e)
        finally:
            self._stop_event.set()

    async def stop(self) -> None:
        """Stop all components and clean up (with timeouts to avoid hanging)."""
        if self._stopped:
            return
        self._stopped = True
        self._stopping = True
        log.info("Stopping daemon...")

        # Cancel background loops first (non-blocking)
        if self._discovery:
            self._discovery.stop()
        if self._discovery_task:
            self._discovery_task.cancel()
        if self._health_check_task:
            self._health_check_task.cancel()

        # Kill tunnel connect processes immediately (don't wait)
        for name, proc in self._tunnel_connect_procs.items():
            try:
                proc.kill()
                if proc.pid:
                    _child_pids.discard(proc.pid)
            except ProcessLookupError:
                pass
        self._tunnel_connect_procs.clear()
        self._peer_uris.clear()
        self._peer_status.clear()

        # Kill host process immediately so run_forever() unblocks
        if self._host_process:
            try:
                self._host_process.kill()
                if self._host_process.pid:
                    _child_pids.discard(self._host_process.pid)
            except ProcessLookupError:
                pass

        # Close PTY sessions via ptyprocess lifecycle (closes fd + kills child)
        for session in self._shell_manager.sessions.values():
            try:
                session._handle.close(force=True)
            except Exception:
                pass

        # Stop servers with short timeouts
        # Stop dashboard v2 subprocess
        if self._dashboard_v2_proc and self._dashboard_v2_proc.returncode is None:
            try:
                self._dashboard_v2_proc.kill()
                if self._dashboard_v2_proc.pid:
                    _child_pids.discard(self._dashboard_v2_proc.pid)
            except ProcessLookupError:
                pass

        if self._dashboard:
            try:
                await asyncio.wait_for(self._dashboard.stop(), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass

        try:
            await asyncio.wait_for(self._server.stop(), timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            pass

        # Shut down health check thread pool
        self._health_pool.shutdown(wait=False)

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
            if not self._stopped:
                await self.stop()
            # Force exit — PTY reader threads may block normal shutdown
            _cleanup_children()
            os._exit(0)
