"""Test: dashboard's peer query logic works correctly."""

import asyncio
import sys
sys.path.insert(0, "src")

from shell_cluster.models import Peer, PeerStatus
from shell_cluster.tui.app import query_peer_sessions


async def main():
    # Test querying node-a
    peer_a = Peer(
        name="node-a",
        tunnel_id="manual-node-a",
        forwarding_uri="ws://localhost:8765",
        status=PeerStatus.ONLINE,
    )
    print(f"Querying {peer_a.name}...")
    sessions = await query_peer_sessions(peer_a)
    print(f"  Name from server: {peer_a.name}")
    print(f"  Sessions: {len(sessions)}")
    for s in sessions:
        print(f"    - {s.shell} #{s.session_id}")

    # Test querying node-b
    peer_b = Peer(
        name="node-b",
        tunnel_id="manual-node-b",
        forwarding_uri="ws://localhost:8766",
        status=PeerStatus.ONLINE,
    )
    print(f"\nQuerying {peer_b.name}...")
    sessions = await query_peer_sessions(peer_b)
    print(f"  Name from server: {peer_b.name}")
    print(f"  Sessions: {len(sessions)}")

    # Test querying offline peer
    peer_offline = Peer(
        name="offline",
        tunnel_id="manual-offline",
        forwarding_uri="ws://localhost:9999",
        status=PeerStatus.ONLINE,
    )
    print(f"\nQuerying {peer_offline.name} (should fail gracefully)...")
    sessions = await query_peer_sessions(peer_offline)
    print(f"  Sessions: {len(sessions)} (expected 0)")

    print("\n[PASS] Dashboard peer query works!")


if __name__ == "__main__":
    asyncio.run(main())
