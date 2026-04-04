"""Session list widget for the TUI sidebar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, Tree
from textual.widgets._tree import TreeNode

from shell_cluster.models import Peer, PeerStatus


class SessionList(Static):
    """Sidebar widget showing all peers and their sessions."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._peers: list[Peer] = []
        self._tree: Tree | None = None
        self._selected_peer: Peer | None = None

    @property
    def selected_peer(self) -> Peer | None:
        return self._selected_peer

    def compose(self) -> ComposeResult:
        tree: Tree[dict] = Tree("Sessions")
        tree.show_root = False
        self._tree = tree
        yield tree

    def update_peers(self, peers: list[Peer]) -> None:
        """Update the session tree with new peer data."""
        self._peers = peers
        if not self._tree:
            return

        self._tree.clear()
        for peer in peers:
            status_icon = "[green]●[/green]" if peer.status == PeerStatus.ONLINE else "[red]●[/red]"
            node = self._tree.root.add(
                f"{status_icon} {peer.name}",
                data={"peer": peer},
            )
            if peer.sessions:
                for session in peer.sessions:
                    node.add_leaf(
                        f"  {session.shell} #{session.session_id[:6]}",
                        data={"peer": peer, "session": session},
                    )
            else:
                node.add_leaf(
                    "  [dim](no sessions)[/dim]",
                    data={"peer": peer},
                )
            node.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle selection of a tree node."""
        data = event.node.data
        if data and "peer" in data:
            self._selected_peer = data["peer"]
