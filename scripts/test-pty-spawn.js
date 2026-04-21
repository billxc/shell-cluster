#!/usr/bin/env node
'use strict';

const http = require('http');
const { WebSocketServer, WebSocket } = require('ws');
const pty = require('node-pty');
const os = require('os');

const PORT = 19877;

// Test 1: Direct spawn
console.log('=== Test 1: Direct spawn ===');
try {
  const p = pty.spawn('/bin/zsh', [], {
    name: 'xterm-256color', cols: 80, rows: 24,
    cwd: os.homedir(),
    env: Object.assign({}, process.env, { TERM: 'xterm-256color' }),
  });
  console.log('OK: pid=' + p.pid);
  p.kill();
} catch (e) {
  console.log('FAIL:', e.message);
}

// Test 2: Spawn inside WS handler
console.log('\n=== Test 2: Spawn inside WebSocket handler ===');
const server = http.createServer();
const wss = new WebSocketServer({ noServer: true });

server.on('upgrade', (req, socket, head) => {
  wss.handleUpgrade(req, socket, head, (ws) => {
    console.log('  WS client connected');
    try {
      const p = pty.spawn('/bin/zsh', [], {
        name: 'xterm-256color', cols: 80, rows: 24,
        cwd: os.homedir(),
        env: Object.assign({}, process.env, { TERM: 'xterm-256color' }),
      });
      console.log('  OK: pid=' + p.pid);
      ws.send('OK:' + p.pid);
      p.kill();
    } catch (e) {
      console.log('  FAIL:', e.message);
      ws.send('FAIL:' + e.message);
    }
    ws.close();
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('  Server on port ' + PORT);
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/raw?session=test&cols=80&rows=24`);
  ws.on('message', (data) => {
    console.log('  Client received: ' + data.toString());
  });
  ws.on('close', () => {
    server.close(() => {
      console.log('\nDone.');
      process.exit(0);
    });
  });
  ws.on('error', (e) => {
    console.log('  Client error: ' + e.message);
    server.close(() => process.exit(1));
  });
});
