"""Textual TUI application for shell-cluster dashboard."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from shell_cluster.config import Config
from shell_cluster.discovery import PeerDiscovery
from shell_cluster.models import Peer, PeerStatus
from shell_cluster.tunnel.devtunnel import DevTunnelBackend
from shell_cluster.tui.widgets.session_list import SessionList


class ShellClusterApp(App):
    """Shell Cluster TUI Dashboard."""

    TITLE = "Shell Cluster"
    CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 35;
        border-right: solid $accent;
    }
    #main {
        width: 1fr;
    }
    SessionList {
        height: 100%;
    }
    #status-area {
        height: 100%;
        padding: 1 2;
    }
    .peer-group-title {
        color: $accent;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("n", "new_shell", "New Shell"),
        Binding("d", "close_shell", "Close Shell"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._backend = DevTunnelBackend()
        self._tunnel_id = f"shellcluster-{config.node.name}"
        self._discovery = PeerDiscovery(
            backend=self._backend,
            label=config.node.label,
            own_tunnel_id=self._tunnel_id,
            interval=config.discovery.interval_seconds,
        )
        self._peers: list[Peer] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield SessionList(id="sidebar")
            from textual.widgets import Static
            yield Static(
                "Select a session or press [bold]n[/bold] to create a new shell.\n\n"
                "Use [bold]Enter[/bold] to connect.",
                id="status-area",
            )
        yield Footer()

    async def on_mount(self) -> None:
        """Start discovery loop on mount."""
        self.run_worker(self._discovery_loop())

    async def _discovery_loop(self) -> None:
        """Periodically refresh peers and update the session list."""
        while True:
            try:
                self._peers = await self._discovery.refresh()
                sidebar = self.query_one("#sidebar", SessionList)
                sidebar.update_peers(self._peers)
            except Exception:
                pass
            await asyncio.sleep(self._config.discovery.interval_seconds)

    async def action_refresh(self) -> None:
        """Manual refresh."""
        self._peers = await self._discovery.refresh()
        sidebar = self.query_one("#sidebar", SessionList)
        sidebar.update_peers(self._peers)

    async def action_new_shell(self) -> None:
        """Create a new shell on the selected peer."""
        sidebar = self.query_one("#sidebar", SessionList)
        peer = sidebar.selected_peer
        if not peer or not peer.forwarding_uri:
            self.notify("No peer selected or peer has no URI", severity="warning")
            return

        # Suspend TUI, run raw terminal session, resume
        from shell_cluster.client import ShellClient

        self.notify(f"Connecting to {peer.name}...")

        with self.suspend():
            client = ShellClient(peer.forwarding_uri)
            await client.connect_and_run()

        self.notify(f"Disconnected from {peer.name}")

    async def action_close_shell(self) -> None:
        """Close the selected shell session."""
        self.notify("Close shell: not yet implemented", severity="warning")

    async def action_quit(self) -> None:
        self.exit()
