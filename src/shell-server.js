/**
 * WebSocket + HTTP server for shell sessions.
 *
 * Exposes:
 *   - GET  /sessions  — JSON list of active sessions
 *   - WS   /raw       — binary PTY protocol (compatible with frontend app.js)
 */

'use strict';

const http = require('http');
const url = require('url');
const { WebSocketServer } = require('ws');

// Terminal query sequences that trigger echo from xterm.js.
// Must be stripped before sending output to client.
const TERMINAL_QUERY_RE =
  /\x1b\[[>?=]?[0-9]*[cn]|\x1b\]1[0-2];?\x07|\x1b\]1[0-2];\?\x1b\\|\x1b\[\??[0-9]+\$p/g;

function stripTerminalQueries(str) {
  return str.replace(TERMINAL_QUERY_RE, '');
}

class ShellServer {
  /**
   * @param {import('./shell-manager').ShellManager} shellManager
   * @param {object} opts
   * @param {number} [opts.port=0]
   * @param {string} [opts.host='127.0.0.1']
   * @param {string} [opts.nodeName='node']
   */
  constructor(shellManager, opts = {}) {
    this._shellManager = shellManager;
    this._port = opts.port || 0;
    this._host = opts.host || '127.0.0.1';
    this._nodeName = opts.nodeName || 'node';
    this._httpServer = null;
    this._wss = null;
  }

  get port() {
    return this._port;
  }

  start() {
    return new Promise((resolve, reject) => {
      const httpServer = http.createServer((req, res) => {
        this._handleHttp(req, res);
      });

      const wss = new WebSocketServer({ noServer: true });

      httpServer.on('upgrade', (req, socket, head) => {
        const pathname = url.parse(req.url).pathname;
        if (pathname === '/raw') {
          wss.handleUpgrade(req, socket, head, (ws) => {
            this._handleRawClient(ws, req);
          });
        } else {
          socket.destroy();
        }
      });

      httpServer.listen(this._port, this._host, () => {
        this._port = httpServer.address().port;
        this._httpServer = httpServer;
        this._wss = wss;
        console.log(`[ShellServer] Listening on ${this._host}:${this._port}`);
        resolve();
      });

      httpServer.on('error', reject);
    });
  }

  _handleHttp(req, res) {
    const pathname = url.parse(req.url).pathname;

    // CORS headers
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    if (pathname === '/sessions') {
      const sessions = this._shellManager.listSessions();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(sessions));
      return;
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
  }

  /**
   * Handle a /raw WebSocket connection.
   * Binary frames = PTY data, text frames = JSON control.
   */
  _handleRawClient(ws, req) {
    const parsed = url.parse(req.url, true);
    const query = parsed.query;

    const cols = parseInt(query.cols, 10) || 80;
    const rows = parseInt(query.rows, 10) || 24;
    const attachId = query.attach || null;
    const sessionId = attachId || query.session || null;

    if (!sessionId) {
      ws.close(1008, 'Missing session or attach param');
      return;
    }

    const isAttach = !!attachId;
    console.log(`[ShellServer] Raw client: ${isAttach ? 'attach' : 'create'} session=${sessionId} ${cols}x${rows}`);

    const onOutput = (sid, data) => {
      if (ws.readyState !== ws.OPEN) return;
      // data is a Buffer; convert to string for query stripping, then back
      let str = data.toString('utf-8');
      str = stripTerminalQueries(str);
      if (str) {
        try {
          ws.send(Buffer.from(str, 'utf-8'));
        } catch (e) {
          // connection closed
        }
      }
    };

    const onExit = (sid) => {
      if (ws.readyState !== ws.OPEN) return;
      try {
        ws.send(JSON.stringify({ type: 'shell.closed', session_id: sid }));
      } catch (e) {
        // connection closed
      }
    };

    try {
      if (isAttach) {
        const session = this._shellManager.attach(sessionId, onOutput, onExit);
        if (!session) {
          ws.close(1008, `Session ${sessionId} not found`);
          return;
        }
        this._shellManager.resize(sessionId, cols, rows);
        // Send attached message
        ws.send(JSON.stringify({
          type: 'shell.attached',
          session_id: sessionId,
          shell: session.shell,
        }));
        // Send serialized terminal state for reconnect
        const state = this._shellManager.getSerializedState(sessionId);
        if (state) {
          const stripped = stripTerminalQueries(state);
          if (stripped) {
            ws.send(Buffer.from(stripped, 'utf-8'));
          }
        }
      } else {
        // Check if session already exists
        if (this._shellManager.sessions.has(sessionId)) {
          ws.close(1008, `Session ${sessionId} already exists`);
          return;
        }
        const session = this._shellManager.create(sessionId, '', cols, rows, onOutput, onExit);
        ws.send(JSON.stringify({
          type: 'shell.created',
          session_id: sessionId,
          shell: session.shell,
        }));
      }
    } catch (e) {
      console.error(`[ShellServer] Session setup failed:`, e.message);
      ws.close(1008, e.message);
      return;
    }

    // Handle incoming messages
    ws.on('message', (data, isBinary) => {
      if (isBinary) {
        // Binary frame = PTY input
        this._shellManager.write(sessionId, data);
      } else {
        // Text frame — try JSON control
        const text = data.toString('utf-8');
        try {
          const ctrl = JSON.parse(text);
          if (ctrl.type === 'shell.resize') {
            this._shellManager.resize(
              sessionId,
              ctrl.cols || 80,
              ctrl.rows || 24,
            );
          } else if (ctrl.type === 'shell.close') {
            this._shellManager.close(sessionId);
            try {
              ws.send(JSON.stringify({ type: 'shell.closed', session_id: sessionId }));
            } catch (e) {
              // ignore
            }
          } else {
            // Unknown JSON — treat as PTY input
            this._shellManager.write(sessionId, Buffer.from(text, 'utf-8'));
          }
        } catch (e) {
          // Not JSON — plain text PTY input
          this._shellManager.write(sessionId, Buffer.from(text, 'utf-8'));
        }
      }
    });

    ws.on('close', () => {
      console.log(`[ShellServer] Raw client disconnected: session=${sessionId}`);
      // Session persists for reconnect — don't close it
    });

    ws.on('error', (err) => {
      console.warn(`[ShellServer] WebSocket error for session=${sessionId}:`, err.message);
    });
  }

  async stop() {
    if (this._wss) {
      // Close all WebSocket connections
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

module.exports = { ShellServer };
