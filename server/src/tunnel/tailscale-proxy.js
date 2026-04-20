#!/usr/bin/env node

/**
 * TCP proxy that pipes connections through 'tailscale nc'.
 *
 * Usage:
 *   node tailscale-proxy.js --peer-ip 100.64.0.2 --peer-port 9876
 *
 * Prints LISTENING:<port> to stdout when ready.
 * Accepts TCP connections and pipes each through `tailscale nc <ip> <port>`.
 */

'use strict';

const net = require('net');
const { spawn } = require('child_process');

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    peerIp: '',
    peerPort: 0,
    tailscaleCmd: ['tailscale', 'nc'],
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--peer-ip':
        opts.peerIp = args[++i];
        break;
      case '--peer-port':
        opts.peerPort = parseInt(args[++i], 10);
        break;
      case '--tailscale-cmd':
        opts.tailscaleCmd = args.slice(++i);
        i = args.length; // consume rest
        break;
    }
  }

  if (!opts.peerIp || !opts.peerPort) {
    console.error('Usage: tailscale-proxy.js --peer-ip IP --peer-port PORT [--tailscale-cmd ...]');
    process.exit(1);
  }

  return opts;
}

function handleClient(socket, peerIp, peerPort, ncCmd) {
  const proc = spawn(ncCmd[0], [...ncCmd.slice(1), peerIp, String(peerPort)], {
    stdio: ['pipe', 'pipe', 'ignore'],
  });

  // Bidirectional pipe: socket <-> tailscale nc
  socket.pipe(proc.stdin);
  proc.stdout.pipe(socket);

  socket.on('error', () => {
    try { proc.kill(); } catch (e) { /* ignore */ }
  });

  proc.on('error', () => {
    try { socket.destroy(); } catch (e) { /* ignore */ }
  });

  socket.on('close', () => {
    try { proc.kill(); } catch (e) { /* ignore */ }
  });

  proc.on('close', () => {
    try { socket.destroy(); } catch (e) { /* ignore */ }
  });
}

function main() {
  const opts = parseArgs();

  const server = net.createServer((socket) => {
    handleClient(socket, opts.peerIp, opts.peerPort, opts.tailscaleCmd);
  });

  server.listen(0, '127.0.0.1', () => {
    const port = server.address().port;
    process.stdout.write(`LISTENING:${port}\n`);
  });

  // Clean shutdown on signals
  const shutdown = () => {
    server.close();
    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main();
