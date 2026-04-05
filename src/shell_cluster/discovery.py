"""Peer discovery via tunnel backend."""

from __future__ import annotations

import asyncio
import logging

from shell_cluster.models import Peer, PeerStatus, TunnelInfo
from shell_cluster.tunnel.base import TunnelBackend, parse_node_name

log = logging.getLogger(__name__)


class PeerDiscovery:
    """Discovers peers by listing tunnels with a shared label."""

    def __init__(
        self,
        backend: TunnelBackend,
        label: str,
        own_tunnel_id: str,
        interval: int = 30,
    ):
        self._backend = backend
        self._label = label
        self._own_tunnel_id = own_tunnel_id
        self._interval = interval
        self._peers: dict[str, Peer] = {}
        self._running = False

    @property
    def peers(self) -> dict[str, Peer]:
        return dict(self._peers)

    async def refresh(self) -> list[Peer]:
        """Refresh the peer list from the tunnel backend."""
        try:
            tunnels = await self._backend.list_tunnels(self._label)
        except Exception as e:
            log.warning("Discovery refresh failed: %s", e)
            return list(self._peers.values())

        seen: set[str] = set()
        for t in tunnels:
            # Skip tunnels that are not actively hosted
            if not t.hosting:
                continue
            seen.add(t.tunnel_id)

            if t.tunnel_id in self._peers:
                self._peers[t.tunnel_id].status = PeerStatus.ONLINE
            else:
                # New peer discovered — get port info
                remote_port, forwarding_uri = await self._backend.get_port_and_uri(t.tunnel_id)
                name = parse_node_name(t.tunnel_id)
                peer = Peer(
                    name=name,
                    tunnel_id=t.tunnel_id,
                    port=remote_port,
                    forwarding_uri=forwarding_uri,
                    status=PeerStatus.ONLINE,
                )
                self._peers[t.tunnel_id] = peer
                log.info("Discovered peer: %s (%s)", name, t.tunnel_id)

        # Mark unseen peers as offline
        for tid in list(self._peers):
            if tid not in seen:
                self._peers[tid].status = PeerStatus.OFFLINE

        return list(self._peers.values())

    async def run_loop(
        self,
        on_update: asyncio.Event | None = None,
    ) -> None:
        """Run discovery in a loop."""
        self._running = True
        while self._running:
            await self.refresh()
            if on_update:
                on_update.set()
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False
