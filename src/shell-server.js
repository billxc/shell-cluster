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

      httpServer.on('clientError', (err, socket) => {
        console.error(`[ShellServer] Client error: ${err.message}`);
      });

      wss.on('error', (err) => {
        console.error(`[ShellServer] WSS error: ${err.message}`);
      });
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

    // --- Batched output: accumulate PTY data, flush every 16ms (~60fps) ---
    let outputBuf = [];
    let flushTimer = null;

    const flushOutput = () => {
      flushTimer = null;
      if (outputBuf.length === 0) return;
      if (ws.readyState !== ws.OPEN) {
        outputBuf = [];
        return;
      }

      const combined = Buffer.concat(outputBuf);
      outputBuf = [];

      let str = combined.toString('utf-8');
      str = stripTerminalQueries(str);
      if (!str) return;

      try {
        ws.send(Buffer.from(str, 'utf-8'), (err) => {
          if (err) {
            console.warn(`[ShellServer] ws.send error session=${sessionId}: ${err.message}`);
            return;
          }
          // Backpressure: pause PTY if WS buffer > 1MB
          if (ws.bufferedAmount > 1024 * 1024) {
            console.log(`[ShellServer] Backpressure ON session=${sessionId} buffered=${ws.bufferedAmount}`);
            this._shellManager.pausePty(sessionId);
            const check = () => {
              if (ws.readyState !== ws.OPEN) {
                // WS gone — resume PTY so it doesn't stay paused forever
                this._shellManager.resumePty(sessionId);
                return;
              }
              if (ws.bufferedAmount < 256 * 1024) {
                console.log(`[ShellServer] Backpressure OFF session=${sessionId}`);
                this._shellManager.resumePty(sessionId);
              } else {
                setTimeout(check, 50);
              }
            };
            setTimeout(check, 50);
          }
        });
      } catch (e) {
        console.warn(`[ShellServer] ws.send threw session=${sessionId}: ${e.message}`);
      }
    };

    const onOutput = (sid, data) => {
      if (ws.readyState !== ws.OPEN) return;
      outputBuf.push(data);
      if (!flushTimer) {
        flushTimer = setTimeout(flushOutput, 67);
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
      console.log(`[ShellServer] ERROR: Session setup failed for session=${sessionId}: ${e.message}`);
      if (e.stack) console.log(e.stack);
      try {
        ws.send(JSON.stringify({ type: 'error', error: e.message }));
      } catch (_) {}
      const reason = e.message.length > 123 ? e.message.slice(0, 120) + '...' : e.message;
      ws.close(1008, reason);
      return;
    }

    // --- Batched PTY input: reduce write frequency to ConPTY ---
    // Also dedup mouse move events — only keep the latest position per batch.
    // SGR mouse move: \x1b[<35;X;YM  (button=35 means motion, no button pressed)
    // SGR mouse drag: \x1b[<32;X;YM  \x1b[<33;X;YM  \x1b[<34;X;YM
    const MOUSE_MOVE_RE = /^\x1b\[<(3[2-5]);(\d+);(\d+)M$/;
    let inputBuf = [];
    let lastMouseMove = null; // deduplicated: only keep latest mouse move
    let inputTimer = null;

    const flushInput = () => {
      inputTimer = null;
      const parts = inputBuf;
      const tail = lastMouseMove;
      inputBuf = [];
      lastMouseMove = null;
      if (parts.length === 0 && !tail) return;
      if (tail) parts.push(tail);
      const combined = Buffer.concat(parts);
      this._shellManager.write(sessionId, combined);
    };

    // Handle incoming messages
    ws.on('message', (data, isBinary) => {
      if (isBinary) {
        // Binary frame = PTY input — batch + dedup mouse moves
        const str = data.toString('utf-8');
        if (MOUSE_MOVE_RE.test(str)) {
          // Mouse move/drag: replace previous, don't accumulate
          lastMouseMove = data;
        } else {
          // Non-mouse data: flush any pending mouse move first, then queue
          if (lastMouseMove) {
            inputBuf.push(lastMouseMove);
            lastMouseMove = null;
          }
          inputBuf.push(data);
        }
        if (!inputTimer) {
          inputTimer = setTimeout(flushInput, 8);
        }
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

    ws.on('close', (code, reason) => {
      const reasonStr = reason ? reason.toString() : '';
      console.log(`[ShellServer] WS closed session=${sessionId} code=${code} reason="${reasonStr}"`);
      // Clean up timers
      if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
      if (inputTimer) { clearTimeout(inputTimer); inputTimer = null; }
      outputBuf = [];
      inputBuf = [];
      this._shellManager.detach(sessionId, onOutput, onExit);
      // Ensure PTY is resumed in case backpressure left it paused
      this._shellManager.resumePty(sessionId);
    });

    ws.on('error', (err) => {
      console.error(`[ShellServer] WS error session=${sessionId}: ${err.message}`);
      if (err.code) console.error(`[ShellServer]   code=${err.code}`);
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
