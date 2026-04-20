#!/usr/bin/env node

/**
 * Entry point for shell-cluster Node.js server.
 *
 * Starts:
 *   - ShellServer (PTY + WebSocket on configurable port)
 *   - DashboardServer (HTTP API + WS proxy on port 9000)
 *   - StaticServer (dashboard_v2 static files on port 9001)
 */

'use strict';

const { ShellManager } = require('./shell-manager');
const { ShellServer } = require('./shell-server');
const { DashboardServer } = require('./dashboard-server');
const { StaticServer } = require('./static-server');

// --- CLI argument parsing ---
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    port: 0,
    dashboardPort: 9000,
    staticPort: 9001,
    host: '127.0.0.1',
    nodeName: require('os').hostname(),
    noDashboard: false,
    noStatic: false,
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--port':
        opts.port = parseInt(args[++i], 10);
        break;
      case '--dashboard-port':
        opts.dashboardPort = parseInt(args[++i], 10);
        break;
      case '--static-port':
        opts.staticPort = parseInt(args[++i], 10);
        break;
      case '--host':
        opts.host = args[++i];
        break;
      case '--node-name':
        opts.nodeName = args[++i];
        break;
      case '--no-dashboard':
        opts.noDashboard = true;
        break;
      case '--no-static':
        opts.noStatic = true;
        break;
      case '--no-tunnel':
        // accepted for compatibility, no-op in Node version
        break;
      case '--help':
        console.log(`Usage: node src/index.js [options]

Options:
  --port <n>            Shell server port (default: random)
  --dashboard-port <n>  Dashboard server port (default: 9000)
  --static-port <n>     Static file server port (default: 9001)
  --host <addr>         Bind address (default: 127.0.0.1)
  --node-name <name>    Node name (default: hostname)
  --no-dashboard        Don't start dashboard server
  --no-static           Don't start static file server
  --no-tunnel           No-op (compatibility)
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

  let dashboardServer = null;
  let staticServer = null;

  if (!opts.noDashboard) {
    dashboardServer = new DashboardServer({
      host: opts.host,
      port: opts.dashboardPort,
      getPeers: () => {
        // Local peer: this node
        return [{
          name: opts.nodeName,
          uri: `ws://${opts.host}:${shellServer.port}`,
          status: 'online',
        }];
      },
    });
    await dashboardServer.start();
  }

  if (!opts.noStatic) {
    staticServer = new StaticServer({
      host: opts.host,
      port: opts.staticPort,
    });
    await staticServer.start();
  }

  console.log(`\n[shell-cluster] Server ready.`);
  console.log(`  Shell server:     ws://${opts.host}:${shellServer.port}/raw`);
  if (dashboardServer) {
    console.log(`  Dashboard:        http://${opts.host}:${opts.dashboardPort}`);
  }
  if (staticServer) {
    console.log(`  Static frontend:  http://${opts.host}:${opts.staticPort}`);
  }

  // --- Graceful shutdown ---
  let shuttingDown = false;

  async function shutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n[shell-cluster] ${signal} received, shutting down...`);

    const withTimeout = (promise, ms) =>
      Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms))]);

    try {
      if (staticServer) await withTimeout(staticServer.stop(), 5000);
    } catch (e) {
      // ignore
    }
    try {
      if (dashboardServer) await withTimeout(dashboardServer.stop(), 5000);
    } catch (e) {
      // ignore
    }
    try {
      await withTimeout(shellServer.stop(), 5000);
    } catch (e) {
      // ignore
    }
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
