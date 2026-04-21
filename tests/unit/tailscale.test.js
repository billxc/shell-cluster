'use strict';

/**
 * Unit tests for Tailscale backend — ported from archived/tests/test_tailscale_backend.py
 */

const { TailscaleBackend, parseHostname } = require('../../src/tunnel/tailscale');

// --- Mock data (matching Python test mocks) ---

const MOCK_STATUS_RUNNING = {
  BackendState: 'Running',
  Self: {
    ID: 'self-id',
    HostName: 'my-mac',
    DNSName: 'my-mac.tailnet-abc.ts.net.',
    TailscaleIPs: ['100.64.0.1', 'fd7a::1'],
    Online: true,
    OS: 'macOS',
  },
  Peer: {
    'nodekey:abc123': {
      ID: 'peer-1',
      HostName: 'work-pc',
      DNSName: 'work-pc.tailnet-abc.ts.net.',
      TailscaleIPs: ['100.64.0.2', 'fd7a::2'],
      Online: true,
      OS: 'linux',
    },
    'nodekey:def456': {
      ID: 'peer-2',
      HostName: 'home-server',
      DNSName: 'home-server.tailnet-abc.ts.net.',
      TailscaleIPs: ['100.64.0.3', 'fd7a::3'],
      Online: true,
      OS: 'linux',
    },
    'nodekey:ghi789': {
      ID: 'peer-3',
      HostName: 'phone',
      DNSName: 'phone.tailnet-abc.ts.net.',
      TailscaleIPs: ['100.64.0.4', 'fd7a::4'],
      Online: false,
      OS: 'iOS',
    },
  },
};

const MOCK_STATUS_NEEDS_LOGIN = {
  BackendState: 'NeedsLogin',
  Self: { HostName: 'my-mac', TailscaleIPs: [], Online: false },
  Peer: {},
};

const MOCK_STATUS_NO_PEERS = {
  BackendState: 'Running',
  Self: {
    HostName: 'my-mac',
    TailscaleIPs: ['100.64.0.1'],
    Online: true,
  },
  Peer: {},
};

// --- Helpers ---

function createBackend() {
  const b = new TailscaleBackend({ port: 9876 });
  b._socket = ''; // disable auto-detection for tests
  return b;
}

function patchGetStatus(backend, mockStatus) {
  backend._getStatus = async () => mockStatus;
}

function patchGetStatusFail(backend) {
  backend._getStatus = async () => { throw new Error('tailscale not running'); };
}

// --- parseHostname tests (ported from Python) ---

describe('parseHostname', () => {
  test('default port when no -p suffix', () => {
    const { name, port } = parseHostname('work-pc', 9876);
    expect(name).toBe('work-pc');
    expect(port).toBe(9876);
  });

  test('custom port from -p suffix', () => {
    const { name, port } = parseHostname('work-pc-p9877', 9876);
    expect(name).toBe('work-pc');
    expect(port).toBe(9877);
  });

  test('no false positive: hostname ending with digits but no -p', () => {
    const { name, port } = parseHostname('server-2', 9876);
    expect(name).toBe('server-2');
    expect(port).toBe(9876);
  });

  test('hostname with multiple dashes and -p suffix', () => {
    const { name, port } = parseHostname('my-home-server-p8080', 9876);
    expect(name).toBe('my-home-server');
    expect(port).toBe(8080);
  });
});

// --- listTunnels tests (ported from Python) ---

describe('TailscaleBackend.listTunnels', () => {
  test('returns online peers', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_RUNNING);

    const tunnels = await backend.listTunnels('shellcluster');

    expect(tunnels).toHaveLength(2);
    const names = new Set(tunnels.map(t => t.tunnelId));
    expect(names).toEqual(new Set(['work-pc', 'home-server']));
    for (const t of tunnels) {
      expect(t.hosting).toBe(true);
      expect(t.port).toBe(9876);
    }
  });

  test('excludes self', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_RUNNING);

    const tunnels = await backend.listTunnels('shellcluster');

    const ids = new Set(tunnels.map(t => t.tunnelId));
    expect(ids.has('my-mac')).toBe(false);
  });

  test('excludes offline peers', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_RUNNING);

    const tunnels = await backend.listTunnels('shellcluster');

    const ids = new Set(tunnels.map(t => t.tunnelId));
    expect(ids.has('phone')).toBe(false);
  });

  test('empty when no peers', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_NO_PEERS);

    const tunnels = await backend.listTunnels('shellcluster');
    expect(tunnels).toEqual([]);
  });

  test('returns empty on failure', async () => {
    const backend = createBackend();
    patchGetStatusFail(backend);

    const tunnels = await backend.listTunnels('shellcluster');
    expect(tunnels).toEqual([]);
  });

  test('populates hostname-to-IP mapping', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_RUNNING);

    await backend.listTunnels('shellcluster');

    expect(backend._hostnameToIp['work-pc']).toBe('100.64.0.2');
    expect(backend._hostnameToIp['home-server']).toBe('100.64.0.3');
    expect(backend._hostnameToIp['phone']).toBeUndefined();
  });

  test('parses port from hostname -p suffix', async () => {
    const backend = createBackend();
    const status = {
      BackendState: 'Running',
      Self: { HostName: 'my-mac', TailscaleIPs: ['100.64.0.1'], Online: true },
      Peer: {
        'nodekey:aaa': {
          HostName: 'server-p9877',
          TailscaleIPs: ['100.64.0.10'],
          Online: true,
        },
        'nodekey:bbb': {
          HostName: 'desktop',
          TailscaleIPs: ['100.64.0.11'],
          Online: true,
        },
      },
    };
    patchGetStatus(backend, status);

    const tunnels = await backend.listTunnels('shellcluster');

    const byId = {};
    for (const t of tunnels) byId[t.tunnelId] = t;
    expect(byId['server-p9877'].port).toBe(9877);
    expect(byId['server-p9877'].description).toBe('server');
    expect(byId['desktop'].port).toBe(9876);
    expect(byId['desktop'].description).toBe('desktop');
  });
});

// --- host tests ---

describe('TailscaleBackend.host', () => {
  test('returns null (Tailscale handles connectivity)', () => {
    const backend = createBackend();
    const result = backend.host('test-tunnel', 9876);
    expect(result).toBeNull();
  });
});

// --- ensureTunnel tests ---

describe('TailscaleBackend.ensureTunnel', () => {
  test('succeeds when connected', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_RUNNING);

    await expect(backend.ensureTunnel('test-tunnel', 9876, 'shellcluster')).resolves.toBeUndefined();
  });

  test('fails when not connected', async () => {
    const backend = createBackend();
    patchGetStatus(backend, MOCK_STATUS_NEEDS_LOGIN);

    await expect(backend.ensureTunnel('test-tunnel', 9876, 'shellcluster'))
      .rejects.toThrow('not connected');
  });

  test('fails when tailscale not running', async () => {
    const backend = createBackend();
    patchGetStatusFail(backend);

    await expect(backend.ensureTunnel('test-tunnel', 9876, 'shellcluster'))
      .rejects.toThrow();
  });
});

// --- getPortAndUri tests ---

describe('TailscaleBackend.getPortAndUri', () => {
  test('returns configured port', async () => {
    const backend = createBackend();
    const { port, uri } = await backend.getPortAndUri('work-pc');

    expect(port).toBe(9876);
    expect(uri).toBe('');
  });

  test('respects hostname port', async () => {
    const backend = createBackend();
    const { port, uri } = await backend.getPortAndUri('server-p9877');

    expect(port).toBe(9877);
    expect(uri).toBe('');
  });
});

// --- no-op method tests ---

describe('TailscaleBackend no-op methods', () => {
  test('create returns tunnel info', async () => {
    const backend = createBackend();
    const info = await backend.create('test-tunnel', 9876, 'shellcluster');

    expect(info.tunnelId).toBe('test-tunnel');
    expect(info.port).toBe(9876);
  });

  test('delete is noop', async () => {
    const backend = createBackend();
    await expect(backend.delete('test-tunnel')).resolves.toBeUndefined();
  });

  test('exists returns true', async () => {
    const backend = createBackend();
    const result = await backend.exists('test-tunnel');
    expect(result).toBe(true);
  });

  test('getForwardingUri returns empty', async () => {
    const backend = createBackend();
    const uri = await backend.getForwardingUri('work-pc', 9876);
    expect(uri).toBe('');
  });
});

// --- connect tests ---

describe('TailscaleBackend.connect', () => {
  test('raises for unknown peer', async () => {
    const backend = createBackend();
    await expect(backend.connect('unknown-peer', 9876)).rejects.toThrow('not discovered');
  });
});
