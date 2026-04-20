/**
 * Dashboard HTTP + WebSocket proxy server.
 *
 * Runs on port 9000 (configurable). Provides:
 *   - GET  /api/peers         — peer list JSON
 *   - GET  /api/refresh-peers — trigger discovery refresh
 *   - WS   /                  — proxy browser WS to peer shell servers
 *   - Static files from dashboard_v2/static/ at /
 */

'use strict';

const http = require('http');
const url = require('url');
const path = require('path');
const { WebSocket, WebSocketServer } = require('ws');
const { serveStaticFile } = require('./serve-static');

class DashboardServer {
  /**
   * @param {object} opts
   * @param {string} [opts.host='127.0.0.1']
   * @param {number} [opts.port=9000]
   * @param {function():Array} [opts.getPeers]
   * @param {function():Promise} [opts.refreshPeers]
   * @param {string} [opts.staticDir] - path to dashboard_v2/static/
   */
  constructor(opts = {}) {
    this._host = opts.host || '127.0.0.1';
    this._port = opts.port || 9000;
    this._getPeers = opts.getPeers || (() => []);
    this._refreshPeers = opts.refreshPeers || null;
    this._staticDir = opts.staticDir || path.resolve(__dirname, '../public');
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
    if (origin && (origin.includes('://localhost') || origin.includes('://127.0.0.1'))) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
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
      const peers = this._getPeers();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(peers));
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

    // Static files
    serveStaticFile(req, res, pathname, this._staticDir);
  }

  /**
   * WebSocket proxy: browser sends init message with target, we proxy bidirectionally.
   */
  _handleWsProxy(browserWs, req) {
    let peerWs = null;
    let initReceived = false;

    // Wait for init message with target
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
        browserWs.close(1008, 'Invalid init message');
        return;
      }

      const targetUri = init.target;
      if (!targetUri) {
        browserWs.close(1008, 'Missing target URI');
        return;
      }

      // Validate target against known peers
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
        // Forward any buffered messages from browser after init
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

      peerWs.on('close', () => {
        browserWs.close();
      });

      peerWs.on('error', (err) => {
        console.error(`[DashboardServer] Proxy connection failed:`, err.message);
        try {
          browserWs.send(JSON.stringify({ type: 'error', error: 'Connection to peer failed' }));
        } catch (e) {
          // ignore
        }
        browserWs.close();
      });
    });

    browserWs.on('close', () => {
      if (peerWs && peerWs.readyState === WebSocket.OPEN) {
        peerWs.close();
      }
    });

    browserWs.on('error', () => {
      if (peerWs && peerWs.readyState === WebSocket.OPEN) {
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
