'use strict';

/**
 * End-to-end tests for shell-cluster.
 *
 * Ported from archived Python tests:
 *   - test_local_connect.py: connect, create session, run command, verify output
 *   - test_exit.py: shell exit triggers graceful close
 *   - test_close_session.py: close one/many sessions, disconnect-preserves, close-then-reattach
 *
 * These tests start a real ShellServer on a random port and use WebSocket clients.
 */

const http = require('http');
const WebSocket = require('ws');
const { ShellManager } = require('../../src/shell-manager');
const { ShellServer } = require('../../src/shell-server');

// --- Helpers ---

function httpGet(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Create a message queue wrapper around a WebSocket.
 * Buffers all messages so none are lost between reads.
 */
function createMessageQueue(ws) {
  const queue = [];
  let waiter = null;

  ws.on('message', (data, isBinary) => {
    const item = { data, isBinary };
    if (waiter) {
      const resolve = waiter;
      waiter = null;
      resolve(item);
    } else {
      queue.push(item);
    }
  });

  return {
    next(timeout = 5000) {
      if (queue.length > 0) {
        return Promise.resolve(queue.shift());
      }
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          waiter = null;
          reject(new Error('timeout'));
        }, timeout);
        waiter = (item) => {
          clearTimeout(timer);
          resolve(item);
        };
      });
    },
  };
}

function createWsClient(port, sessionId, opts = {}) {
  const cols = opts.cols || 80;
  const rows = opts.rows || 24;
  const isAttach = opts.attach || false;
  const param = isAttach ? `attach=${sessionId}` : `session=${sessionId}`;
  const url = `ws://127.0.0.1:${port}/raw?${param}&cols=${cols}&rows=${rows}`;

  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('connect timeout')), 5000);

    // Attach message queue immediately so no messages are lost
    const mq = createMessageQueue(ws);
    ws._mq = mq;

    ws.on('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });

    ws.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

/**
 * Wait for a specific JSON message type from the WS message queue.
 */
async function waitForJsonType(ws, type, timeout = 10000) {
  const mq = ws._mq;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    try {
      const { data } = await mq.next(Math.min(remaining, 2000));
      const text = Buffer.isBuffer(data) ? data.toString('utf-8') : data.toString();
      try {
        const msg = JSON.parse(text);
        if (msg.type === type) return msg;
      } catch (e) {
        // not JSON — skip
      }
    } catch (e) {
      if (e.message === 'timeout' && Date.now() < deadline) continue;
      throw e;
    }
  }
  throw new Error(`Timed out waiting for ${type}`);
}

/**
 * Drain all pending messages (discard).
 */
async function drainOutput(ws, timeout = 500) {
  const mq = ws._mq;
  while (true) {
    try {
      await mq.next(timeout);
    } catch (e) {
      break;
    }
  }
}

/**
 * Send a command and collect output until marker found.
 */
async function sendCommand(ws, cmd, timeout = 5000) {
  const mq = ws._mq;
  const marker = `__MK_${Math.random().toString(16).slice(2, 10)}__`;
  const fullCmd = `${cmd}; echo ${marker}\n`;
  ws.send(Buffer.from(fullCmd));

  let output = Buffer.alloc(0);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    try {
      const { data } = await mq.next(Math.min(remaining, 1000));
      if (Buffer.isBuffer(data)) {
        output = Buffer.concat([output, data]);
      } else {
        output = Buffer.concat([output, Buffer.from(data)]);
      }
      if (output.includes(marker)) break;
    } catch (e) {
      break;
    }
  }
  return output;
}

/**
 * Send shell.close and wait for shell.closed.
 */
async function closeSession(ws, sessionId) {
  ws.send(JSON.stringify({ type: 'shell.close', session_id: sessionId }));
  try {
    await waitForJsonType(ws, 'shell.closed', 5000);
    return true;
  } catch (e) {
    return false;
  }
}

async function getSessions(port) {
  return httpGet(`http://127.0.0.1:${port}/sessions`);
}

// --- Test Suite ---

jest.setTimeout(30000);

describe('E2E: Shell Server', () => {
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

  // --- Ported from test_local_connect.py ---

  describe('connect and execute command', () => {
    test('creates session and receives shell.created', async () => {
      const ws = await createWsClient(port, 'connect-test-1');
      try {
        const msg = await waitForJsonType(ws, 'shell.created');
        expect(msg.type).toBe('shell.created');
        expect(msg.session_id).toBe('connect-test-1');
        expect(msg.shell).toBeTruthy();
      } finally {
        manager.close('connect-test-1');
        ws.close();
        await sleep(100);
      }
    });

    test('runs echo command and receives output', async () => {
      const ws = await createWsClient(port, 'echo-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        const output = await sendCommand(ws, 'echo hello-shell-cluster');
        expect(output.toString()).toContain('hello-shell-cluster');
      } finally {
        manager.close('echo-test');
        ws.close();
        await sleep(100);
      }
    });
  });

  // --- Ported from test_exit.py ---

  describe('shell exit triggers graceful close', () => {
    test('sends exit and receives shell.closed', async () => {
      const ws = await createWsClient(port, 'exit-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        ws.send(Buffer.from('exit\n'));

        const msg = await waitForJsonType(ws, 'shell.closed', 10000);
        expect(msg.type).toBe('shell.closed');
        expect(msg.session_id).toBe('exit-test');
      } finally {
        ws.close();
        await sleep(100);
      }
    });
  });

  // --- Ported from test_close_session.py ---

  describe('close sessions', () => {
    test('TC-1: close one of many — others survive', async () => {
      const ws1 = await createWsClient(port, 's1');
      const ws2 = await createWsClient(port, 's2');
      const ws3 = await createWsClient(port, 's3');

      try {
        await waitForJsonType(ws1, 'shell.created');
        await waitForJsonType(ws2, 'shell.created');
        await waitForJsonType(ws3, 'shell.created');
        await drainOutput(ws1);
        await drainOutput(ws2);
        await drainOutput(ws3);

        let sessions = await getSessions(port);
        expect(sessions).toHaveLength(3);

        const out1 = await sendCommand(ws1, 'echo ALIVE-S1');
        expect(out1.toString()).toContain('ALIVE-S1');
        const out2 = await sendCommand(ws2, 'echo ALIVE-S2');
        expect(out2.toString()).toContain('ALIVE-S2');
        const out3 = await sendCommand(ws3, 'echo ALIVE-S3');
        expect(out3.toString()).toContain('ALIVE-S3');

        expect(await closeSession(ws2, 's2')).toBe(true);
        await sleep(300);

        sessions = await getSessions(port);
        const ids = sessions.map(s => s.id);
        expect(ids).not.toContain('s2');
        expect(ids).toContain('s1');
        expect(ids).toContain('s3');

        const still1 = await sendCommand(ws1, 'echo STILL-S1');
        expect(still1.toString()).toContain('STILL-S1');
        const still3 = await sendCommand(ws3, 'echo STILL-S3');
        expect(still3.toString()).toContain('STILL-S3');
      } finally {
        manager.close('s1');
        manager.close('s3');
        ws1.close();
        ws2.close();
        ws3.close();
        await sleep(100);
      }
    });

    test('TC-2: close all sessions sequentially', async () => {
      const ws1 = await createWsClient(port, 'seq-1');
      const ws2 = await createWsClient(port, 'seq-2');
      const ws3 = await createWsClient(port, 'seq-3');

      try {
        await waitForJsonType(ws1, 'shell.created');
        await waitForJsonType(ws2, 'shell.created');
        await waitForJsonType(ws3, 'shell.created');
        await drainOutput(ws1);
        await drainOutput(ws2);
        await drainOutput(ws3);

        expect(await getSessions(port)).toHaveLength(3);

        expect(await closeSession(ws1, 'seq-1')).toBe(true);
        await sleep(200);
        expect(await getSessions(port)).toHaveLength(2);

        expect(await closeSession(ws2, 'seq-2')).toBe(true);
        await sleep(200);
        expect(await getSessions(port)).toHaveLength(1);

        expect(await closeSession(ws3, 'seq-3')).toBe(true);
        await sleep(200);
        expect(await getSessions(port)).toHaveLength(0);
      } finally {
        ws1.close();
        ws2.close();
        ws3.close();
        await sleep(100);
      }
    });

    test('TC-3: disconnect without close preserves session', async () => {
      const ws = await createWsClient(port, 'persist-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        const output = await sendCommand(ws, 'echo PERSIST-MARKER');
        expect(output.toString()).toContain('PERSIST-MARKER');
      } finally {
        ws.close();
      }

      await sleep(300);
      const sessions = await getSessions(port);
      expect(sessions.some(s => s.id === 'persist-test')).toBe(true);
      manager.close('persist-test');
    });

    test('TC-4: after shell.close, re-attach fails', async () => {
      const ws = await createWsClient(port, 'gone-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);
        expect(await closeSession(ws, 'gone-test')).toBe(true);
      } finally {
        ws.close();
      }

      await sleep(300);
      expect((await getSessions(port)).some(s => s.id === 'gone-test')).toBe(false);

      const ws2 = await createWsClient(port, 'gone-test', { attach: true });
      try {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('timeout')), 5000);
          ws2.on('close', (code) => {
            clearTimeout(timer);
            expect(code).toBe(1008);
            resolve();
          });
        });
      } finally {
        try { ws2.close(); } catch (e) { /* ignore */ }
      }
    });

    test('TC-5: close during active output', async () => {
      const ws = await createWsClient(port, 'busy-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        ws.send(Buffer.from('for i in $(seq 1 100); do echo LINE-$i; done\n'));
        await sleep(200);

        expect(await closeSession(ws, 'busy-test')).toBe(true);

        await sleep(300);
        expect((await getSessions(port)).some(s => s.id === 'busy-test')).toBe(false);
      } finally {
        ws.close();
        await sleep(100);
      }
    });
  });

  // --- Additional E2E tests ---

  describe('resize', () => {
    test('resize command does not break session', async () => {
      const ws = await createWsClient(port, 'resize-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        ws.send(JSON.stringify({ type: 'shell.resize', cols: 120, rows: 40 }));
        await sleep(200);

        const output = await sendCommand(ws, 'echo AFTER-RESIZE');
        expect(output.toString()).toContain('AFTER-RESIZE');
      } finally {
        manager.close('resize-test');
        ws.close();
        await sleep(100);
      }
    });
  });

  describe('attach to existing session', () => {
    test('attach receives shell.attached and serialized state', async () => {
      const ws1 = await createWsClient(port, 'attach-test');
      try {
        await waitForJsonType(ws1, 'shell.created');
        await drainOutput(ws1);

        const output = await sendCommand(ws1, 'echo ATTACH-MARKER');
        expect(output.toString()).toContain('ATTACH-MARKER');

        ws1.close();
        await sleep(200);

        const ws2 = await createWsClient(port, 'attach-test', { attach: true });
        try {
          const msg = await waitForJsonType(ws2, 'shell.attached');
          expect(msg.type).toBe('shell.attached');
          expect(msg.session_id).toBe('attach-test');

          await drainOutput(ws2);
          const out2 = await sendCommand(ws2, 'echo AFTER-ATTACH');
          expect(out2.toString()).toContain('AFTER-ATTACH');
        } finally {
          manager.close('attach-test');
          ws2.close();
          await sleep(100);
        }
      } finally {
        try { ws1.close(); } catch (e) { /* ignore */ }
      }
    });
  });

  describe('session already exists', () => {
    test('creating duplicate session ID is rejected', async () => {
      const ws1 = await createWsClient(port, 'dup-test');
      try {
        await waitForJsonType(ws1, 'shell.created');

        const ws2 = await createWsClient(port, 'dup-test');
        await new Promise((resolve) => {
          const timer = setTimeout(() => {
            try { ws2.close(); } catch (e) { /* ignore */ }
            resolve();
          }, 5000);
          ws2.on('close', (code) => {
            clearTimeout(timer);
            expect(code).toBe(1008);
            resolve();
          });
        });
      } finally {
        manager.close('dup-test');
        ws1.close();
        await sleep(100);
      }
    });
  });

  describe('missing session param', () => {
    test('WS without session/attach param is rejected', async () => {
      const url = `ws://127.0.0.1:${port}/raw?cols=80&rows=24`;
      const ws = new WebSocket(url);
      await new Promise((resolve) => {
        ws.on('close', (code) => {
          expect(code).toBe(1008);
          resolve();
        });
        ws.on('error', () => resolve());
      });
    });
  });

  // --- Regression tests ---

  describe('regression: unknown JSON types not written to PTY', () => {
    test('unknown JSON control type is ignored, not typed into shell', async () => {
      const ws = await createWsClient(port, 'json-ignore-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        // Send unknown JSON type — should be silently ignored
        ws.send(JSON.stringify({ type: 'shell.ping' }));
        await sleep(200);

        // Now send a real command to check the shell is clean
        const output = await sendCommand(ws, 'echo CLEAN');
        const text = output.toString();
        // The unknown JSON should NOT appear as typed text
        expect(text).not.toContain('shell.ping');
        expect(text).toContain('CLEAN');
      } finally {
        manager.close('json-ignore-test');
        ws.close();
        await sleep(100);
      }
    });
  });

  describe('regression: close() fires shell.closed via exit callback', () => {
    test('manager.close() sends shell.closed to WS client', async () => {
      const ws = await createWsClient(port, 'close-notify-test');
      try {
        await waitForJsonType(ws, 'shell.created');
        await drainOutput(ws);

        // Close from server side (simulates dashboard "kill session")
        manager.close('close-notify-test');

        // Client should receive shell.closed
        const msg = await waitForJsonType(ws, 'shell.closed', 5000);
        expect(msg.type).toBe('shell.closed');
        expect(msg.session_id).toBe('close-notify-test');
      } finally {
        ws.close();
        await sleep(100);
      }
    });
  });
});
