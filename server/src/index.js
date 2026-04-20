#!/usr/bin/env node

/**
 * Standalone entry point for shell-cluster Node.js server.
 *
 * Starts:
 *   - ShellServer (PTY + WebSocket on configurable port)
 *   - DashboardServer (HTTP API + WS proxy on port 9000)
 *
 * For full daemon with tunnel/discovery, use cli.js instead.
 */

'use strict';

const { ShellManager } = require('./shell-manager');
const { ShellServer } = require('./shell-server');
const { DashboardServer } = require('./dashboard-server');

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    port: 0,
    dashboardPort: 9000,
    host: '127.0.0.1',
    nodeName: require('os').hostname(),
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--port':
        opts.port = parseInt(args[++i], 10);
        break;
      case '--dashboard-port':
        opts.dashboardPort = parseInt(args[++i], 10);
        break;
      case '--host':
        opts.host = args[++i];
        break;
      case '--node-name':
        opts.nodeName = args[++i];
        break;
      case '--help':
        console.log(`Usage: node src/index.js [options]

Options:
  --port <n>            Shell server port (default: random)
  --dashboard-port <n>  Dashboard API port (default: 9000)
  --host <addr>         Bind address (default: 127.0.0.1)
  --node-name <name>    Node name (default: hostname)
`);
        process.exit(0);
    }
  }
  return opts;
}

async function main() {
  const opts = parseArgs();

  const shellManager = new ShellManager();
  const shellServer = new ShellServer(shellManager, {
    port: opts.port,
    host: opts.host,
    nodeName: opts.nodeName,
  });

  await shellServer.start();

  const dashboardServer = new DashboardServer({
    host: opts.host,
    port: opts.dashboardPort,
    getPeers: () => [{
      name: opts.nodeName,
      uri: `ws://${opts.host}:${shellServer.port}`,
      status: 'online',
    }],
  });
  await dashboardServer.start();

  console.log(`\n[shell-cluster] Server ready.`);
  console.log(`  Shell server:  ws://${opts.host}:${shellServer.port}/raw`);
  console.log(`  Dashboard API: http://${opts.host}:${opts.dashboardPort}`);

  let shuttingDown = false;
  async function shutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n[shell-cluster] ${signal} received, shutting down...`);

    const withTimeout = (p, ms) =>
      Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);

    try { await withTimeout(dashboardServer.stop(), 5000); } catch (e) { /* ignore */ }
    try { await withTimeout(shellServer.stop(), 5000); } catch (e) { /* ignore */ }
    shellManager.closeAll();
    console.log('[shell-cluster] Shutdown complete.');
    process.exit(0);
  }

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
