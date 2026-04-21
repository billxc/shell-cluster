#!/usr/bin/env node
'use strict';
const { WebSocket } = require('ws');

const sessionId = 'diag-' + Date.now();
const url = `ws://127.0.0.1:19876/raw?session=${sessionId}&cols=80&rows=24`;
console.log('Connecting to:', url);

const ws = new WebSocket(url);

ws.on('open', () => console.log('WS opened'));

ws.on('message', (data, isBinary) => {
  if (isBinary) {
    console.log('Binary data:', data.length, 'bytes');
  } else {
    const text = data.toString();
    try {
      const msg = JSON.parse(text);
      console.log('JSON:', JSON.stringify(msg));
    } catch {
      console.log('Text:', text.slice(0, 200));
    }
  }
});

ws.on('close', (code, reason) => {
  console.log(`Closed: code=${code} reason=${reason.toString()}`);
  process.exit(0);
});

ws.on('error', (e) => {
  console.log('Error:', e.message);
  process.exit(1);
});

setTimeout(() => {
  console.log('Timeout - closing');
  ws.close();
}, 3000);
