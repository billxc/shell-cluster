/**
 * Configuration management for shell-cluster (Node.js port).
 * Reads/writes config.toml from the platform-specific config directory.
 */

'use strict';

const os = require('os');
const path = require('path');
const fs = require('fs');
const fsp = fs.promises;
const TOML = require('@iarna/toml');

/**
 * Get the platform-specific config directory.
 */
function getConfigDir() {
  switch (process.platform) {
    case 'darwin':
      return path.join(os.homedir(), 'Library', 'Application Support', 'shell-cluster');
    case 'win32':
      return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'shell-cluster');
    default:
      return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'shell-cluster');
  }
}

const CONFIG_DIR = getConfigDir();
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.toml');

/**
 * Default config structure matching Python dataclasses.
 */
function defaultConfig() {
  return {
    node: {
      name: os.hostname(),
      label: 'shellcluster',
      dashboard_port: 9000,
      dashboard_v2_port: 9001,
      dashboard: true,
    },
    tunnel: {
      backend: 'devtunnel',
      expiration: '',
      port: 0,
    },
    shell: {
      command: '',
    },
    peers: [],
  };
}

/**
 * Load config from file, creating defaults if not exists.
 * @returns {object} config
 */
function loadConfig() {
  if (!fs.existsSync(CONFIG_FILE)) {
    const config = defaultConfig();
    saveConfig(config);
    return config;
  }

  const raw = fs.readFileSync(CONFIG_FILE, 'utf-8');
  let data;
  try {
    data = TOML.parse(raw);
  } catch (e) {
    console.error(`[Config] Failed to parse ${CONFIG_FILE}: ${e.message}`);
    console.error('[Config] Using default configuration');
    return defaultConfig();
  }
  const config = defaultConfig();

  if (data.node) {
    for (const [k, v] of Object.entries(data.node)) {
      if (k in config.node) config.node[k] = v;
    }
  }
  if (data.tunnel) {
    for (const [k, v] of Object.entries(data.tunnel)) {
      if (k in config.tunnel) config.tunnel[k] = v;
    }
  }
  if (data.shell) {
    for (const [k, v] of Object.entries(data.shell)) {
      if (k in config.shell) config.shell[k] = v;
    }
  }
  if (Array.isArray(data.peers)) {
    config.peers = data.peers
      .filter(p => p && typeof p === 'object' && p.uri)
      .map(p => ({ name: p.name || '', uri: p.uri }));
  }

  return config;
}

/**
 * Save config to file.
 * @param {object} config
 */
function saveConfig(config) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true });
  const data = {
    node: config.node,
    tunnel: config.tunnel,
    shell: config.shell,
  };
  if (config.peers && config.peers.length > 0) {
    data.peers = config.peers.map(p => ({ name: p.name, uri: p.uri }));
  }
  const tomlStr = TOML.stringify(data);
  fs.writeFileSync(CONFIG_FILE, tomlStr, 'utf-8');
}

/**
 * Get the shell command from config (auto-detect if empty).
 * @param {object} config
 * @returns {string}
 */
function getShellCommand(config) {
  if (config.shell.command) return config.shell.command;
  if (process.platform === 'win32') {
    const { execFileSync } = require('child_process');
    for (const shell of ['pwsh', 'powershell.exe']) {
      try {
        const fullPath = execFileSync('where', [shell], { stdio: 'pipe' }).toString().trim().split('\n')[0].trim();
        if (fullPath) return fullPath;
      } catch (e) {}
    }
    return process.env.COMSPEC || 'cmd.exe';
  }
  return process.env.SHELL || '/bin/sh';
}

module.exports = {
  CONFIG_DIR,
  CONFIG_FILE,
  defaultConfig,
  loadConfig,
  saveConfig,
  getShellCommand,
};
