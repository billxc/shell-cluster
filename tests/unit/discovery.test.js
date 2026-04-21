'use strict';

const { PeerDiscovery, PEER_STATUS } = require('../../src/tunnel/discovery');

// Mock backend
function createMockBackend(tunnels = []) {
  return {
    listTunnels: jest.fn().mockResolvedValue(tunnels),
    getPortAndUri: jest.fn().mockImplementation(async (tunnelId) => {
      const t = tunnels.find(t => t.tunnelId === tunnelId);
      return { port: t ? t.port : 0, uri: '' };
    }),
  };
}

describe('PeerDiscovery', () => {
  describe('constructor', () => {
    test('initializes with correct defaults', () => {
      const backend = createMockBackend();
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'shellcluster-my-mac-shellcluster',
      });

      expect(discovery._label).toBe('shellcluster');
      expect(discovery._interval).toBe(300);
      expect(discovery._running).toBe(false);
    });
  });

  describe('refresh', () => {
    test('discovers new peers', async () => {
      const tunnels = [
        { tunnelId: 'shellcluster-work-pc-shellcluster', hosting: true, port: 8765 },
        { tunnelId: 'shellcluster-home-server-shellcluster', hosting: true, port: 8766 },
      ];
      const backend = createMockBackend(tunnels);
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'shellcluster-my-mac-shellcluster',
      });

      const peers = await discovery.refresh();

      expect(peers).toHaveLength(2);
      expect(peers[0].status).toBe(PEER_STATUS.ONLINE);
      expect(backend.getPortAndUri).toHaveBeenCalledTimes(2);
    });

    test('skips non-hosting tunnels', async () => {
      const tunnels = [
        { tunnelId: 'peer-1', hosting: true, port: 8765 },
        { tunnelId: 'peer-2', hosting: false, port: 8766 },
      ];
      const backend = createMockBackend(tunnels);
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      const peers = await discovery.refresh();

      expect(peers).toHaveLength(1);
      expect(peers[0].tunnelId).toBe('peer-1');
    });

    test('marks unseen peers as offline', async () => {
      const backend = createMockBackend([
        { tunnelId: 'peer-1', hosting: true, port: 8765 },
      ]);
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      // First refresh: discover peer-1
      await discovery.refresh();

      // Second refresh: peer-1 disappeared
      backend.listTunnels.mockResolvedValue([]);
      const peers = await discovery.refresh();

      expect(peers).toHaveLength(1);
      expect(peers[0].status).toBe(PEER_STATUS.OFFLINE);
    });

    test('returns cached peers on failure', async () => {
      const backend = createMockBackend([
        { tunnelId: 'peer-1', hosting: true, port: 8765 },
      ]);
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      await discovery.refresh();

      // Make listTunnels fail
      backend.listTunnels.mockRejectedValue(new Error('network error'));
      const peers = await discovery.refresh();

      expect(peers).toHaveLength(1);
      expect(peers[0].tunnelId).toBe('peer-1');
    });

    test('handles timeout from listTunnels', async () => {
      const backend = {
        listTunnels: jest.fn().mockImplementation(() =>
          new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 100))
        ),
        getPortAndUri: jest.fn(),
      };
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      // Should not throw, returns empty on first call
      const peers = await discovery.refresh();
      expect(peers).toEqual([]);
    });

    test('skips peers with invalid port', async () => {
      const backend = {
        listTunnels: jest.fn().mockResolvedValue([
          { tunnelId: 'peer-1', hosting: true, port: 0 },
        ]),
        getPortAndUri: jest.fn().mockResolvedValue({ port: 0, uri: '' }),
      };
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      const peers = await discovery.refresh();
      expect(peers).toHaveLength(0);
    });
  });

  describe('stop', () => {
    test('stops running loop', () => {
      const backend = createMockBackend();
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
        interval: 1,
      });

      discovery._running = true;
      discovery.stop();
      expect(discovery._running).toBe(false);
    });
  });

  describe('peers getter', () => {
    test('returns copy of peers map', async () => {
      const backend = createMockBackend([
        { tunnelId: 'peer-1', hosting: true, port: 8765 },
      ]);
      const discovery = new PeerDiscovery({
        backend,
        label: 'shellcluster',
        ownTunnelId: 'self',
      });

      await discovery.refresh();
      const peers = discovery.peers;

      expect(peers).toBeInstanceOf(Map);
      expect(peers.size).toBe(1);
      // Verify it's a copy
      peers.clear();
      expect(discovery.peers.size).toBe(1);
    });
  });
});
