#!/usr/bin/env node
'use strict';
const { WebSocket } = require('ws');

// Test both localhost and 127.0.0.1
for (const host of ['localhost', '127.0.0.1']) {
  const sessionId = 'diag-' + host + '-' + Date.now();
  const url = `ws://${host}:19876/raw?session=${sessionId}&cols=80&rows=24`;
  console.log(`\n--- Testing ${host} ---`);
  console.log('URL:', url);

  const ws = new WebSocket(url);
  ws.on('open', () => console.log(`  [${host}] opened`));
  ws.on('message', (data, isBinary) => {
    if (!isBinary) {
      try {
        const msg = JSON.parse(data.toString());
        console.log(`  [${host}] JSON:`, msg.type);
      } catch {
        console.log(`  [${host}] text:`, data.toString().slice(0, 80));
      }
    } else {
      console.log(`  [${host}] binary: ${data.length} bytes`);
    }
  });
  ws.on('close', (code, reason) => {
    console.log(`  [${host}] closed: code=${code} reason=${reason.toString()}`);
  });
  ws.on('error', (e) => {
    console.log(`  [${host}] ERROR: ${e.message}`);
  });
  setTimeout(() => ws.close(), 2000);
}

setTimeout(() => process.exit(0), 3000);
