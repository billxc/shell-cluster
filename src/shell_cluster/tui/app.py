"""Textual TUI application for shell-cluster dashboard."""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static

from shell_cluster.config import Config
from shell_cluster.models import Peer, PeerStatus, ShellSession
from shell_cluster.protocol import Message, MsgType, make_shell_list
from shell_cluster.tui.widgets.session_list import SessionList

log = logging.getLogger(__name__)


async def query_peer_sessions(peer: Peer) -> list[ShellSession]:
    """Connect to a peer and query its active shell sessions."""
    if not peer.forwarding_uri:
        return []
    uri = peer.forwarding_uri
    if not uri.startswith("ws://") and not uri.startswith("wss://"):
        uri = f"ws://{uri}"
    try:
        async with websockets.connect(uri, open_timeout=3, close_timeout=2) as ws:
            # Read peer.info
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            info = Message.from_json(raw)

            # Request session list
            await ws.send(make_shell_list().to_json())
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            resp = Message.from_json(raw)

            sessions = []
            if resp.type == MsgType.SHELL_LIST_RESPONSE:
                for s in resp.sessions:
                    sessions.append(ShellSession(
                        session_id=s.get("id", ""),
                        shell=s.get("shell", ""),
                    ))
            # Also update peer name from server info
            if info.type == MsgType.PEER_INFO and info.name:
                peer.name = info.name
            return sessions
    except Exception as e:
        log.debug("Failed to query %s: %s", peer.name, e)
        return []


class ShellClusterApp(App):
    """Shell Cluster TUI Dashboard."""

    TITLE = "Shell Cluster"
    CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 38;
        border-right: solid $accent;
    }
    #status-area {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }
    SessionList {
        height: 100%;
    }
    """

    BINDINGS = [
        Binding("n", "new_shell", "New Shell", show=True),
        Binding("enter", "connect_shell", "Connect", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        config: Config,
        manual_peers: list[Peer] | None = None,
    ):
        super().__init__()
        self._config = config
        self._manual_peers = manual_peers or []
        self._use_tunnel_discovery = not self._manual_peers
        self._peers: list[Peer] = list(self._manual_peers)
        self._discovery = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield SessionList(id="sidebar")
            yield Static(
                self._status_text(),
                id="status-area",
            )
        yield Footer()

    def _status_text(self) -> str:
        n = len(self._peers)
        mode = "tunnel" if self._use_tunnel_discovery else "local"
        return (
            f"Mode: {mode}  |  Peers: {n}\n\n"
            "Keys:\n"
            "  [bold]Enter[/bold]  Connect to selected peer\n"
            "  [bold]n[/bold]      New shell on selected peer\n"
            "  [bold]r[/bold]      Refresh peer list\n"
            "  [bold]q[/bold]      Quit\n"
        )

    async def on_mount(self) -> None:
        """Refresh peers on mount."""
        await self._do_refresh()
        if self._use_tunnel_discovery:
            self.run_worker(self._discovery_loop())

    async def _do_refresh(self) -> None:
        """Refresh peer list and query sessions."""
        if self._use_tunnel_discovery:
            await self._refresh_tunnel_peers()
        else:
            # For manual peers, probe each to check online status + sessions
            for peer in self._peers:
                try:
                    sessions = await query_peer_sessions(peer)
                    peer.status = PeerStatus.ONLINE
                    peer.sessions = sessions
                except Exception:
                    peer.status = PeerStatus.OFFLINE
                    peer.sessions = []

        sidebar = self.query_one("#sidebar", SessionList)
        sidebar.update_peers(self._peers)
        self.query_one("#status-area", Static).update(self._status_text())

    async def _refresh_tunnel_peers(self) -> None:
        """Refresh peers via tunnel discovery."""
        if not self._discovery:
            from shell_cluster.discovery import PeerDiscovery
            from shell_cluster.tunnel.devtunnel import DevTunnelBackend

            self._discovery = PeerDiscovery(
                backend=DevTunnelBackend(),
                label=self._config.node.label,
                own_tunnel_id=f"shellcluster-{self._config.node.name}",
                interval=self._config.discovery.interval_seconds,
            )
        self._peers = await self._discovery.refresh()

    async def _discovery_loop(self) -> None:
        """Periodically refresh peers (tunnel mode only)."""
        while True:
            await asyncio.sleep(self._config.discovery.interval_seconds)
            try:
                await self._do_refresh()
            except Exception:
                pass

    def _get_selected_peer(self) -> Peer | None:
        sidebar = self.query_one("#sidebar", SessionList)
        return sidebar.selected_peer

    async def action_refresh(self) -> None:
        self.notify("Refreshing...")
        await self._do_refresh()
        self.notify("Refreshed")

    async def action_connect_shell(self) -> None:
        """Connect to the selected peer's shell (new session)."""
        peer = self._get_selected_peer()
        if not peer:
            self.notify("Select a peer first", severity="warning")
            return
        if not peer.forwarding_uri:
            self.notify(f"No URI for {peer.name}", severity="warning")
            return

        from shell_cluster.client import ShellClient

        self.notify(f"Connecting to {peer.name}...")
        with self.suspend():
            client = ShellClient(peer.forwarding_uri)
            await client.connect_and_run()

        self.notify(f"Disconnected from {peer.name}")
        await self._do_refresh()

    async def action_new_shell(self) -> None:
        """Same as connect - creates a new shell on the selected peer."""
        await self.action_connect_shell()

    async def action_quit(self) -> None:
        self.exit()
