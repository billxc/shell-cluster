"""Peer discovery via tunnel backend."""

from __future__ import annotations

import asyncio
import logging

from typing import Awaitable, Callable

from shell_cluster.models import Peer, PeerStatus
from shell_cluster.tunnel.base import TunnelBackend, parse_node_name

log = logging.getLogger(__name__)

OnPeersChanged = Callable[[list[Peer]], Awaitable[None]]


class PeerDiscovery:
    """Discovers peers by listing tunnels with a shared label."""

    def __init__(
        self,
        backend: TunnelBackend,
        label: str,
        own_tunnel_id: str,
        interval: int = 30,
        on_peers_changed: OnPeersChanged | None = None,
    ):
        self._backend = backend
        self._label = label
        self._own_tunnel_id = own_tunnel_id
        self._interval = interval
        self._on_peers_changed = on_peers_changed
        self._peers: dict[str, Peer] = {}
        self._running = False

    @property
    def peers(self) -> dict[str, Peer]:
        return dict(self._peers)

    async def refresh(self) -> list[Peer]:
        """Refresh the peer list from the tunnel backend."""
        try:
            tunnels = await asyncio.wait_for(
                self._backend.list_tunnels(self._label), timeout=10.0
            )
        except asyncio.TimeoutError:
            log.warning("Discovery refresh timed out")
            return list(self._peers.values())
        except Exception as e:
            log.warning("Discovery refresh failed: %s", e)
            return list(self._peers.values())

        seen: set[str] = set()
        for t in tunnels:
            # Skip tunnels that are not actively hosted
            if not t.hosting:
                continue
            seen.add(t.tunnel_id)

            existing = self._peers.get(t.tunnel_id)
            if existing and existing.status == PeerStatus.ONLINE:
                # Already online — check if port changed (peer restarted between cycles)
                if t.port and t.port != existing.port:
                    log.info("Peer %s port changed %d -> %d (list_tunnels), refreshing",
                             existing.name, existing.port, t.port)
                    remote_port, forwarding_uri = await self._backend.get_port_and_uri(t.tunnel_id)
                    if remote_port and remote_port > 0:
                        existing.port = remote_port
                        existing.forwarding_uri = forwarding_uri
            elif existing:
                # Was offline, now back online — re-query port (may have changed)
                log.info("Peer %s back online, refreshing port info", existing.name)
                remote_port, forwarding_uri = await self._backend.get_port_and_uri(t.tunnel_id)
                if remote_port and remote_port > 0:
                    existing.port = remote_port
                    existing.forwarding_uri = forwarding_uri
                    existing.status = PeerStatus.ONLINE
                else:
                    log.warning("Peer %s returned invalid port %s, keeping offline", existing.name, remote_port)
            else:
                # New peer discovered — get port info
                remote_port, forwarding_uri = await self._backend.get_port_and_uri(t.tunnel_id)
                if not remote_port or remote_port <= 0:
                    log.warning("New peer %s has invalid port %s, skipping", t.tunnel_id, remote_port)
                    continue
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

    async def run_loop(self) -> None:
        """Run discovery in a loop, calling on_peers_changed when list changes."""
        self._running = True
        while self._running:
            peers = await self.refresh()
            if self._on_peers_changed:
                await self._on_peers_changed(peers)
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False
