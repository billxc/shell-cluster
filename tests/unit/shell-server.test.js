'use strict';

const http = require('http');
const { ShellManager } = require('../../src/shell-manager');
const { ShellServer } = require('../../src/shell-server');

function httpGet(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, data, headers: res.headers }));
    }).on('error', reject);
  });
}

describe('ShellServer', () => {
  let manager;
  let server;
  let port;

  beforeAll(async () => {
    manager = new ShellManager();
    server = new ShellServer(manager, { port: 0, host: '127.0.0.1' });
    await server.start();
    port = server.port;
  });

  afterAll(async () => {
    manager.closeAll();
    await server.stop();
  });

  describe('HTTP endpoints', () => {
    test('GET /sessions returns JSON array', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/sessions`);
      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toBe('application/json');
      const sessions = JSON.parse(res.data);
      expect(Array.isArray(sessions)).toBe(true);
    });

    test('GET /sessions returns sessions after create', async () => {
      const session = manager.create('http-test-1', '', 80, 24, null, null);
      try {
        const res = await httpGet(`http://127.0.0.1:${port}/sessions`);
        const sessions = JSON.parse(res.data);
        expect(sessions.some(s => s.id === 'http-test-1')).toBe(true);
      } finally {
        manager.close('http-test-1');
      }
    });

    test('GET /unknown returns 404', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/unknown`);
      expect(res.status).toBe(404);
    });

    test('CORS headers are present', async () => {
      const res = await httpGet(`http://127.0.0.1:${port}/sessions`);
      expect(res.headers['access-control-allow-origin']).toBe('*');
    });
  });

  describe('port', () => {
    test('returns assigned port', () => {
      expect(server.port).toBeGreaterThan(0);
    });
  });
});
