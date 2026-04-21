'use strict';

const http = require('http');
const path = require('path');
const { DashboardServer } = require('../../src/dashboard-server');

function httpGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const opts = { timeout: 3000, headers };
    http.get(url, opts, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, data, headers: res.headers }));
    }).on('error', reject);
  });
}

describe('DashboardServer', () => {
  let server;
  let port;
  const mockPeers = [
    { name: 'node-a (local)', uri: 'ws://localhost:12345', status: 'online' },
    { name: 'node-b', uri: 'ws://localhost:12346', status: 'online' },
  ];

  beforeAll(async () => {
    server = new DashboardServer({
      host: '127.0.0.1',
      port: 0,
      getPeers: () => mockPeers,
      refreshPeers: async () => {},
    });
    await server.start();
    port = server._port;
  });

  afterAll(async () => {
    await server.stop();
  });

  describe('API endpoints', () => {
    test('GET /api/peers returns peers JSON', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/api/peers`);
      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toBe('application/json');
      const peers = JSON.parse(res.data);
      expect(peers).toHaveLength(2);
      expect(peers[0].name).toBe('node-a (local)');
    });

    test('GET /api/version returns version', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/api/version`);
      expect(res.status).toBe(200);
      const data = JSON.parse(res.data);
      expect(data).toHaveProperty('version');
      expect(typeof data.version).toBe('string');
    });

    test('GET /api/refresh-peers returns ok', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/api/refresh-peers`);
      expect(res.status).toBe(200);
      const data = JSON.parse(res.data);
      expect(data.ok).toBe(true);
    });
  });

  describe('static files', () => {
    test('GET / serves index.html', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/`);
      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toContain('text/html');
      expect(res.data).toContain('Shell Cluster');
    });

    test('GET /style.css serves CSS', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/style.css`);
      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toContain('text/css');
    });

    test('GET /app.js serves JavaScript', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/app.js`);
      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toContain('javascript');
    });

    test('GET /nonexistent returns 404', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/nonexistent.txt`);
      expect(res.status).toBe(404);
    });

    test('path traversal is blocked', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/../package.json`);
      // Should be 403 or 404
      expect([403, 404]).toContain(res.status);
    });
  });

  describe('CORS', () => {
    test('allows localhost origin', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/api/peers`, {
        Origin: 'http://localhost:9000',
      });
      expect(res.headers['access-control-allow-origin']).toBe('http://localhost:9000');
    });

    test('allows 127.0.0.1 origin', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/api/peers`, {
        Origin: 'http://127.0.0.1:9000',
      });
      expect(res.headers['access-control-allow-origin']).toBe('http://127.0.0.1:9000');
    });
  });

  describe('refresh-peers without handler', () => {
    test('returns ok:false when no refreshPeers handler', async () => {
      const srv2 = new DashboardServer({ host: '127.0.0.1', port: 0 });
      await srv2.start();
      const p2 = srv2._port;
      try {
        const res = await httpGet(`http://127.0.0.1:${p2}/api/refresh-peers`);
        const data = JSON.parse(res.data);
        expect(data.ok).toBe(false);
        expect(data.error).toContain('not available');
      } finally {
        await srv2.stop();
      }
    });
  });
});
