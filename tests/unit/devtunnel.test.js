'use strict';

/**
 * Unit tests for DevTunnel backend.
 * Since devtunnel CLI isn't available in test env, we mock _run and _runJson.
 */

const { DevTunnelBackend } = require('../../src/tunnel/devtunnel');

function createBackend() {
  return new DevTunnelBackend();
}

function patchRun(backend, responses = {}) {
  backend._run = jest.fn().mockImplementation(async (args) => {
    const key = args[0];
    if (responses[key] !== undefined) {
      if (responses[key] instanceof Error) throw responses[key];
      return responses[key];
    }
    return '';
  });
  backend._runJson = jest.fn().mockImplementation(async (args) => {
    const key = args[0];
    if (responses[key + '_json'] !== undefined) {
      if (responses[key + '_json'] instanceof Error) throw responses[key + '_json'];
      return responses[key + '_json'];
    }
    return {};
  });
}

describe('DevTunnelBackend', () => {
  describe('exists', () => {
    test('returns true when tunnel exists', async () => {
      const backend = createBackend();
      patchRun(backend, { show: 'tunnel exists' });

      expect(await backend.exists('my-tunnel')).toBe(true);
      expect(backend._run).toHaveBeenCalledWith(['show', 'my-tunnel']);
    });

    test('returns false when tunnel does not exist', async () => {
      const backend = createBackend();
      patchRun(backend, { show: new Error('not found') });

      expect(await backend.exists('my-tunnel')).toBe(false);
    });
  });

  describe('create', () => {
    test('creates tunnel with port and label', async () => {
      const backend = createBackend();
      patchRun(backend);

      const result = await backend.create('test-tunnel', 8765, 'shellcluster', '30d');

      expect(result.tunnelId).toBe('test-tunnel');
      expect(result.port).toBe(8765);
      expect(backend._run).toHaveBeenCalledWith(
        expect.arrayContaining(['create', 'test-tunnel', '--labels', 'shellcluster', '--expiration', '30d'])
      );
    });

    test('creates tunnel without expiration', async () => {
      const backend = createBackend();
      patchRun(backend);

      await backend.create('test-tunnel', 8765, 'shellcluster');

      const call = backend._run.mock.calls[0][0];
      expect(call).not.toContain('--expiration');
    });
  });

  describe('ensureTunnel', () => {
    test('reuses existing tunnel', async () => {
      const backend = createBackend();
      patchRun(backend, {
        show: 'exists',
        show_json: { tunnel: { ports: [{ portNumber: 8765 }] } },
      });

      await backend.ensureTunnel('test-tunnel', 8765, 'shellcluster');
    });

    test('creates new tunnel when not existing', async () => {
      const backend = createBackend();
      let showCallCount = 0;
      backend._run = jest.fn().mockImplementation(async (args) => {
        if (args[0] === 'show' && showCallCount++ === 0) {
          throw new Error('not found');
        }
        return '';
      });
      backend._runJson = jest.fn().mockResolvedValue({ tunnels: [] });

      await backend.ensureTunnel('test-tunnel', 8765, 'shellcluster');
    });
  });

  describe('listTunnels', () => {
    test('returns tunnels from JSON list', async () => {
      const backend = createBackend();
      patchRun(backend, {
        list_json: {
          tunnels: [
            { tunnelId: 't1', ports: [{ portNumber: 8765 }], hostConnections: 1, labels: ['shellcluster'] },
            { tunnelId: 't2', ports: [{ portNumber: 8766 }], hostConnections: 0, labels: ['shellcluster'] },
          ],
        },
      });

      const tunnels = await backend.listTunnels('shellcluster');

      expect(tunnels).toHaveLength(2);
      expect(tunnels[0].tunnelId).toBe('t1');
      expect(tunnels[0].port).toBe(8765);
      expect(tunnels[0].hosting).toBe(true);
      expect(tunnels[1].hosting).toBe(false);
    });

    test('returns empty on failure', async () => {
      const backend = createBackend();
      patchRun(backend, { list_json: new Error('failed') });

      const tunnels = await backend.listTunnels('shellcluster');
      expect(tunnels).toEqual([]);
    });

    test('handles array response format', async () => {
      const backend = createBackend();
      patchRun(backend, {
        list_json: [
          { tunnelId: 't1', ports: [{ portNumber: 8765 }], hostConnections: 1 },
        ],
      });

      const tunnels = await backend.listTunnels('shellcluster');
      expect(tunnels).toHaveLength(1);
    });
  });

  describe('getPortAndUri', () => {
    test('returns port and uri from tunnel data', async () => {
      const backend = createBackend();
      patchRun(backend, {
        show_json: {
          tunnel: { ports: [{ portNumber: 8765, portUri: 'https://example.devtunnels.ms' }] },
        },
      });

      const result = await backend.getPortAndUri('test-tunnel');
      expect(result.port).toBe(8765);
      expect(result.uri).toBe('https://example.devtunnels.ms');
    });

    test('returns zero port on failure', async () => {
      const backend = createBackend();
      patchRun(backend, { show_json: new Error('failed') });

      const result = await backend.getPortAndUri('test-tunnel');
      expect(result.port).toBe(0);
      expect(result.uri).toBe('');
    });
  });

  describe('getForwardingUri', () => {
    test('returns portUri when available', async () => {
      const backend = createBackend();
      patchRun(backend, {
        show_json: {
          tunnel: { ports: [{ portNumber: 8765, portUri: 'https://abc.devtunnels.ms' }] },
        },
      });

      const uri = await backend.getForwardingUri('test-tunnel', 8765);
      expect(uri).toBe('https://abc.devtunnels.ms');
    });

    test('falls back to portForwardingUris', async () => {
      const backend = createBackend();
      patchRun(backend, {
        show_json: {
          tunnel: { ports: [{ portNumber: 8765, portForwardingUris: ['https://forwarded.ms'] }] },
        },
      });

      const uri = await backend.getForwardingUri('test-tunnel', 8765);
      expect(uri).toBe('https://forwarded.ms');
    });

    test('returns empty on failure', async () => {
      const backend = createBackend();
      patchRun(backend, { show_json: new Error('failed') });

      const uri = await backend.getForwardingUri('test-tunnel', 8765);
      expect(uri).toBe('');
    });
  });

  describe('delete', () => {
    test('calls devtunnel delete with -f flag', async () => {
      const backend = createBackend();
      patchRun(backend);

      await backend.delete('test-tunnel');

      expect(backend._run).toHaveBeenCalledWith(['delete', 'test-tunnel', '-f']);
    });

    test('does not throw on failure', async () => {
      const backend = createBackend();
      patchRun(backend, { delete: new Error('not found') });

      await expect(backend.delete('test-tunnel')).resolves.toBeUndefined();
    });
  });

  describe('host', () => {
    test('returns a child process object (would spawn devtunnel host)', () => {
      // We can't actually spawn devtunnel in tests, but verify the method exists
      const backend = createBackend();
      expect(typeof backend.host).toBe('function');
    });
  });

  describe('connect', () => {
    test('method exists and is a function', () => {
      const backend = createBackend();
      expect(typeof backend.connect).toBe('function');
    });
  });
});
