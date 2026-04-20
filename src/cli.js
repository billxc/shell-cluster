#!/usr/bin/env node

/**
 * CLI entry point for shell-cluster (Node.js port).
 * Uses commander for command parsing.
 */

'use strict';

const { Command } = require('commander');
const os = require('os');
const { loadConfig, saveConfig, CONFIG_FILE, CONFIG_DIR } = require('./config');

const pkg = require('../package.json');

const program = new Command();

program
  .name('shellcluster')
  .version(pkg.version)
  .description('Shell Cluster - Remote access to all your shells via tunnels.')
  .option('-v, --verbose', 'Enable debug logging');

// --- start command ---
program
  .command('start')
  .description('Start the daemon (tunnel + shell server + discovery + dashboard)')
  .option('--no-tunnel', 'Local mode: no tunnel, direct WebSocket')
  .option('--name <name>', 'Override node name')
  .option('--port <port>', 'Shell server port (required for --no-tunnel)', parseInt)
  .action(async (opts) => {
    const noTunnel = opts.tunnel === false;

    if (noTunnel && !opts.port) {
      console.error('--port is required in local mode (--no-tunnel).');
      console.error('Example: shellcluster start --no-tunnel --port 8765');
      process.exit(1);
    }

    // Check tunnel backend availability (unless local mode)
    if (!noTunnel) {
      const { checkDevtunnel, checkTailscale } = require('./tunnel/checks');
      const config = ensureRegistered();
      if (config.tunnel.backend === 'tailscale') {
        if (!checkTailscale()) process.exit(1);
      } else {
        if (!checkDevtunnel()) process.exit(1);
      }
    }

    const config = ensureRegistered();
    if (opts.name) {
      config.node.name = opts.name;
    }

    const mode = noTunnel ? 'local' : 'tunnel';
    console.log(`Starting daemon for ${config.node.name} (mode=${mode})...`);

    const { Daemon } = require('./daemon');
    const daemon = new Daemon(config, {
      noTunnel,
      localPort: opts.port,
    });

    try {
      await daemon.runForever();
    } catch (e) {
      console.error(e.message);
      process.exit(1);
    }
  });

// --- register command ---
program
  .command('register')
  .description('Register this machine to the cluster')
  .option('--name <name>', 'Name for this machine')
  .option('--label <label>', 'Tunnel label for discovery', 'shellcluster')
  .option('--backend <backend>', 'Tunnel backend (devtunnel)', 'devtunnel')
  .action((opts) => {
    const config = loadConfig();
    if (opts.name) config.node.name = opts.name;
    config.node.label = opts.label;
    config.tunnel.backend = opts.backend;
    saveConfig(config);
    console.log(`Registered node '${config.node.name}'`);
    console.log(`  Label: ${config.node.label}`);
    console.log(`  Backend: ${config.tunnel.backend}`);
    console.log(`\nRun 'shellcluster start' to start the daemon.`);
  });

// --- unregister command ---
program
  .command('unregister')
  .description('Unregister this machine: delete tunnel and remove config')
  .action(async () => {
    const fs = require('fs');
    const { makeTunnelId, getTunnelBackend } = require('./tunnel/base');
    const config = loadConfig();
    const tunnelId = makeTunnelId(config.node.name);

    try {
      const backend = getTunnelBackend(config.tunnel.backend);
      console.log(`Deleting tunnel ${tunnelId}...`);
      await backend.delete(tunnelId);
      console.log('Tunnel deleted.');
    } catch (e) {
      console.log(`Tunnel deletion skipped: ${e.message}`);
    }

    if (fs.existsSync(CONFIG_FILE)) {
      fs.unlinkSync(CONFIG_FILE);
      console.log(`Config removed: ${CONFIG_FILE}`);
    }
    console.log('Done. Node unregistered.');
  });

// --- peers command ---
program
  .command('peers')
  .description('List discovered peers')
  .action(async () => {
    const { makeTunnelId, getTunnelBackend } = require('./tunnel/base');
    const { PeerDiscovery } = require('./tunnel/discovery');
    const config = loadConfig();
    const backend = getTunnelBackend(config.tunnel.backend);
    const tunnelId = makeTunnelId(config.node.name);
    const discovery = new PeerDiscovery({
      backend,
      label: config.node.label,
      ownTunnelId: tunnelId,
    });

    const peerList = await discovery.refresh();
    if (peerList.length === 0) {
      console.log('No peers found.');
      return;
    }
    console.log('Peers:');
    for (const p of peerList) {
      console.log(`  ${p.name}  ${p.tunnelId}  [${p.status}]  ${p.forwardingUri || '-'}`);
    }
  });

// --- config command ---
program
  .command('config [key] [value]')
  .description('Show or set config values')
  .action((key, value) => {
    const fs = require('fs');

    if (!key) {
      console.log(`Config file: ${CONFIG_FILE}`);
      if (fs.existsSync(CONFIG_FILE)) {
        console.log('');
        console.log(fs.readFileSync(CONFIG_FILE, 'utf-8'));
      } else {
        console.log('No config file yet. Run "shellcluster register".');
      }
      return;
    }

    const config = loadConfig();
    const parts = key.split('.', 2);
    if (parts.length !== 2) {
      console.error(`Invalid key '${key}'. Use section.field (e.g. node.name)`);
      process.exit(1);
    }
    const [section, field] = parts;
    if (!(section in config) || !(field in config[section])) {
      console.error(`Unknown config key: ${key}`);
      process.exit(1);
    }

    if (value === undefined) {
      console.log(`${key} = ${JSON.stringify(config[section][field])}`);
    } else {
      const current = config[section][field];
      if (typeof current === 'boolean') {
        config[section][field] = ['true', '1', 'yes'].includes(value.toLowerCase());
      } else if (typeof current === 'number') {
        config[section][field] = parseInt(value, 10);
      } else {
        config[section][field] = value;
      }
      saveConfig(config);
      console.log(`${key} = ${JSON.stringify(config[section][field])}`);
    }
  });

// --- dashboard command ---
program
  .command('dashboard')
  .description('Open the dashboard in your browser')
  .action(() => {
    const config = loadConfig();
    const url = `http://127.0.0.1:${config.node.dashboard_port}`;
    console.log(`Opening ${url}...`);
    const { exec } = require('child_process');
    const cmd = process.platform === 'darwin' ? 'open' : process.platform === 'win32' ? 'start' : 'xdg-open';
    exec(`${cmd} ${url}`);
  });

/**
 * Ensure device is registered. Create default config if not exists.
 * @returns {object} config
 */
function ensureRegistered() {
  const fs = require('fs');
  if (fs.existsSync(CONFIG_FILE)) {
    return loadConfig();
  }

  console.log('No config found. Creating default config...');
  const config = loadConfig(); // creates defaults
  console.log(`Registered node '${config.node.name}'`);
  return config;
}

program.parse(process.argv);
