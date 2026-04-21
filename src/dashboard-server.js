/**
 * Dashboard API + WebSocket proxy server.
 *
 * Runs on port 9000 (configurable). Provides:
 *   - GET  /                  — dashboard UI (static files from public/)
 *   - GET  /api/peers         — peer list JSON
 *   - POST /api/refresh-peers — trigger discovery refresh
 *   - WS   /                  — proxy browser WS to peer shell servers
 */

'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');
const { WebSocket, WebSocketServer } = require('ws');

const STATIC_DIR = path.resolve(__dirname, '../public');

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

class DashboardServer {
  /**
   * @param {object} opts
   * @param {string} [opts.host='127.0.0.1']
   * @param {number} [opts.port=9000]
   * @param {function():Array} [opts.getPeers]
   * @param {function():Promise} [opts.refreshPeers]
   */
  constructor(opts = {}) {
    this._host = opts.host || '127.0.0.1';
    this._port = opts.port !== undefined ? opts.port : 9000;
    this._getPeers = opts.getPeers || (() => []);
    this._refreshPeers = opts.refreshPeers || null;
    this._httpServer = null;
    this._wss = null;
  }

  start() {
    return new Promise((resolve, reject) => {
      const httpServer = http.createServer((req, res) => {
        this._handleHttp(req, res);
      });

      const wss = new WebSocketServer({ noServer: true });

      httpServer.on('upgrade', (req, socket, head) => {
        wss.handleUpgrade(req, socket, head, (ws) => {
          this._handleWsProxy(ws, req);
        });
      });

      httpServer.listen(this._port, this._host, () => {
        this._port = httpServer.address().port;
        this._httpServer = httpServer;
        this._wss = wss;
        console.log(`[DashboardServer] Listening on http://${this._host}:${this._port}`);
        resolve();
      });

      httpServer.on('error', reject);
    });
  }

  _addCors(req, res) {
    const origin = req.headers.origin || '';
    if (origin) {
      try {
        const parsed = new URL(origin);
        if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
          res.setHeader('Access-Control-Allow-Origin', origin);
          res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
          res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
        }
      } catch (e) {
        // invalid origin URL — ignore
      }
    }
  }

  async _handleHttp(req, res) {
    const pathname = url.parse(req.url).pathname;

    // CORS preflight
    if (req.method === 'OPTIONS') {
      this._addCors(req, res);
      res.writeHead(204);
      res.end();
      return;
    }

    // API: peer list
    if (pathname === '/api/peers') {
      this._addCors(req, res);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(this._getPeers()));
      return;
    }

    // API: version
    if (pathname === '/api/version') {
      this._addCors(req, res);
      const pkg = require('../package.json');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ version: pkg.version }));
      return;
    }

    // API: refresh peers
    if (pathname === '/api/refresh-peers') {
      this._addCors(req, res);
      if (this._refreshPeers) {
        try {
          await this._refreshPeers();
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true }));
        } catch (e) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'refresh failed' }));
        }
      } else {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'discovery not available' }));
      }
      return;
    }

    // Static files from public/
    let filePath = pathname === '/' ? '/index.html' : pathname;
    const resolved = path.resolve(STATIC_DIR, filePath.slice(1));
    if (!resolved.startsWith(STATIC_DIR)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    try {
      const stat = await fs.promises.stat(resolved);
      if (!stat.isFile()) throw new Error('not a file');
      const ext = path.extname(resolved);
      res.writeHead(200, { 'Content-Type': CONTENT_TYPES[ext] || 'application/octet-stream' });
      fs.createReadStream(resolved).pipe(res);
    } catch (e) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not Found');
    }
  }

  /**
   * WebSocket proxy: browser sends init message with target, we proxy bidirectionally.
   */
  _handleWsProxy(browserWs, req) {
    let peerWs = null;
    let initReceived = false;

    const initTimeout = setTimeout(() => {
      if (!initReceived) {
        browserWs.close(1008, 'Init timeout');
      }
    }, 5000);

    browserWs.once('message', (data) => {
      clearTimeout(initTimeout);
      initReceived = true;

      let init;
      try {
        init = JSON.parse(data.toString('utf-8'));
      } catch (e) {
        console.warn(`[DashboardServer] Proxy: invalid init JSON: ${data.toString('utf-8').slice(0, 200)}`);
        browserWs.close(1008, 'Invalid init message');
        return;
      }

      console.log(`[DashboardServer] Proxy init: target=${init.target} path=${init.path}`);
      const targetUri = init.target;
      if (!targetUri) {
        browserWs.close(1008, 'Missing target URI');
        return;
      }

      const validUris = new Set(this._getPeers().map(p => p.uri));
      if (!validUris.has(targetUri)) {
        browserWs.close(1008, 'Unknown target');
        return;
      }

      const targetPath = init.path || '';
      const connectUri = targetUri + targetPath;

      console.log(`[DashboardServer] Proxying to ${connectUri}`);

      peerWs = new WebSocket(connectUri);

      peerWs.on('open', () => {
        browserWs.on('message', (msg, isBinary) => {
          if (peerWs.readyState === WebSocket.OPEN) {
            peerWs.send(msg, { binary: isBinary });
          }
        });
      });

      peerWs.on('message', (msg, isBinary) => {
        if (browserWs.readyState === WebSocket.OPEN) {
          browserWs.send(msg, { binary: isBinary });
        }
      });

      peerWs.on('close', (code, reason) => {
        console.log(`[DashboardServer] Peer WS closed code=${code} reason="${reason || ''}"`);
        browserWs.close();
      });

      peerWs.on('error', (err) => {
        console.error(`[DashboardServer] Proxy error:`, err.message);
        try {
          browserWs.send(JSON.stringify({ type: 'error', error: 'Connection to peer failed' }));
        } catch (e) {
          // ignore
        }
        browserWs.close();
      });
    });

    browserWs.on('close', (code, reason) => {
      console.log(`[DashboardServer] Browser WS closed code=${code} reason="${reason || ''}"`);
      if (peerWs && (peerWs.readyState === WebSocket.OPEN || peerWs.readyState === WebSocket.CONNECTING)) {
        peerWs.close();
      }
    });

    browserWs.on('error', () => {
      if (peerWs && (peerWs.readyState === WebSocket.OPEN || peerWs.readyState === WebSocket.CONNECTING)) {
        peerWs.close();
      }
    });
  }

  async stop() {
    if (this._wss) {
      for (const client of this._wss.clients) {
        client.close();
      }
    }
    if (this._httpServer) {
      return new Promise((resolve) => {
        this._httpServer.close(() => resolve());
      });
    }
  }
}

module.exports = { DashboardServer };
