"""Daemon orchestrator - ties together tunnel, shell server, and discovery."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from shell_cluster.config import Config
from shell_cluster.discovery import PeerDiscovery
from shell_cluster.server import ShellServer
from shell_cluster.shell_manager import ShellManager

log = logging.getLogger(__name__)


class Daemon:
    """Main daemon that manages the tunnel, shell server, and peer discovery."""

    def __init__(self, config: Config, no_tunnel: bool = False):
        self._config = config
        self._no_tunnel = no_tunnel
        self._tunnel_backend = None
        self._shell_manager = ShellManager(config.get_shell_command())
        self._server = ShellServer(
            self._shell_manager,
            config.node.name,
            config.node.port,
        )
        self._tunnel_id = f"shellcluster-{config.node.name}"
        self._host_process: asyncio.subprocess.Process | None = None
        self._discovery: PeerDiscovery | None = None
        self._discovery_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._stopping = False

    @property
    def discovery(self) -> PeerDiscovery | None:
        return self._discovery

    def _get_tunnel_backend(self):
        if self._tunnel_backend is None:
            from shell_cluster.tunnel.devtunnel import DevTunnelBackend
            self._tunnel_backend = DevTunnelBackend()
        return self._tunnel_backend

    async def start(self) -> None:
        """Start all components."""
        log.info("Starting daemon for node '%s'", self._config.node.name)

        # Register signal handlers
        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.stop()))

        if not self._no_tunnel:
            backend = self._get_tunnel_backend()

            # Create tunnel
            log.info("Creating tunnel %s...", self._tunnel_id)
            await backend.create(
                tunnel_id=self._tunnel_id,
                port=self._config.node.port,
                label=self._config.node.label,
                description=self._config.node.name,
                expiration=self._config.tunnel.expiration,
            )

            # Start hosting
            log.info("Starting tunnel host...")
            self._host_process = await backend.host(
                self._tunnel_id, self._config.node.port
            )

        # Start shell server
        await self._server.start()

        if not self._no_tunnel:
            # Start discovery
            backend = self._get_tunnel_backend()
            self._discovery = PeerDiscovery(
                backend=backend,
                label=self._config.node.label,
                own_tunnel_id=self._tunnel_id,
                interval=self._config.discovery.interval_seconds,
            )
            self._discovery_task = asyncio.create_task(self._discovery.run_loop())

        mode = "local" if self._no_tunnel else f"tunnel={self._tunnel_id}"
        log.info(
            "Daemon running: node=%s, %s, port=%d",
            self._config.node.name,
            mode,
            self._config.node.port,
        )

    async def stop(self) -> None:
        """Stop all components and clean up."""
        if self._stopping:
            return
        self._stopping = True
        log.info("Stopping daemon...")

        # Stop discovery
        if self._discovery:
            self._discovery.stop()
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass

        # Stop shell server
        await self._server.stop()

        # Kill host process
        if self._host_process:
            try:
                self._host_process.kill()
                await self._host_process.wait()
            except ProcessLookupError:
                pass

        # Delete tunnel
        if not self._no_tunnel:
            backend = self._get_tunnel_backend()
            log.info("Deleting tunnel %s...", self._tunnel_id)
            await backend.delete(self._tunnel_id)

        self._stop_event.set()
        log.info("Daemon stopped")

    async def run_forever(self) -> None:
        """Start and run until stopped."""
        await self.start()
        try:
            if self._host_process:
                # Wait for host process to end (or until stopped)
                await self._host_process.wait()
            else:
                # No tunnel mode - just wait until stop is called
                await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if not self._stopping:
                await self.stop()
