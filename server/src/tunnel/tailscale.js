/**
 * Tailscale tunnel backend (Node.js port).
 */

'use strict';

const { spawn } = require('child_process');
const path = require('path');
const { CONFIG_DIR } = require('../config');

function defaultSocket() {
  if (process.platform === 'win32') return '';
  return path.join(CONFIG_DIR, 'tailscaled.sock');
}

/**
 * Parse a Tailscale hostname, extracting an optional port suffix.
 * Convention: <name>-p<port> encodes a custom port.
 */
function parseHostname(hostname, defaultPort) {
  if (hostname.includes('-p')) {
    const lastIdx = hostname.lastIndexOf('-p');
    const base = hostname.slice(0, lastIdx);
    const suffix = hostname.slice(lastIdx + 2);
    if (base && /^\d+$/.test(suffix)) {
      return { name: base, port: parseInt(suffix, 10) };
    }
  }
  return { name: hostname, port: defaultPort };
}

class TailscaleBackend {
  constructor(opts = {}) {
    this._port = opts.port || 9876;
    this._socket = defaultSocket();
    this._hostnameToIp = {};
  }

  _socketArgs() {
    if (this._socket) return ['--socket', this._socket];
    return [];
  }

  /**
   * Run a tailscale CLI command and return stdout.
   */
  _runTailscale(args, opts = {}) {
    const check = opts.check !== false;
    const fullArgs = [...this._socketArgs(), ...args];

    return new Promise((resolve, reject) => {
      const proc = spawn('tailscale', fullArgs, {
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => { stdout += data.toString(); });
      proc.stderr.on('data', (data) => { stderr += data.toString(); });

      proc.on('error', (err) => {
        reject(new Error(`Failed to run tailscale: ${err.message}`));
      });

      proc.on('close', (code) => {
        if (check && code !== 0) {
          reject(new Error(`tailscale ${args[0]} failed (exit ${code}): ${stderr.trim()}`));
        } else {
          resolve(stdout);
        }
      });
    });
  }

  async _getStatus() {
    const output = await this._runTailscale(['status', '--json']);
    return JSON.parse(output);
  }

  async exists(tunnelId) {
    return true;
  }

  async create(tunnelId, port, label, expiration = '') {
    return {
      tunnelId,
      labels: [label],
      port,
    };
  }

  async ensureTunnel(tunnelId, port, label, expiration = '30d') {
    let status;
    try {
      status = await this._getStatus();
    } catch (e) {
      throw new Error(
        `Tailscale is not running: ${e.message}. Start it with: tailscaled --tun=userspace-networking`
      );
    }

    const state = status.BackendState || '';
    if (state !== 'Running') {
      throw new Error(
        `Tailscale is not connected (state: ${state}). Run 'tailscale up' to connect.`
      );
    }
  }

  host(tunnelId, port) {
    console.log('[Tailscale] No host process needed');
    return null;
  }

  async listTunnels(label) {
    let status;
    try {
      status = await this._getStatus();
    } catch (e) {
      console.warn(`[Tailscale] Failed to get status: ${e.message}`);
      return [];
    }

    const selfHostname = (status.Self || {}).HostName || '';
    const peers = status.Peer || {};
    const tunnels = [];

    for (const [, peer] of Object.entries(peers)) {
      const hostname = peer.HostName || '';
      const online = peer.Online || false;
      if (!hostname || !online) continue;
      if (hostname === selfHostname) continue;

      const ips = peer.TailscaleIPs || [];
      if (ips.length === 0) continue;

      const ipv4 = ips.find(ip => ip.includes('.')) || ips[0];
      this._hostnameToIp[hostname] = ipv4;

      const parsed = parseHostname(hostname, this._port);
      tunnels.push({
        tunnelId: hostname,
        hosting: true,
        port: parsed.port,
        description: parsed.name,
      });
    }

    return tunnels;
  }

  async getForwardingUri(tunnelId, port) {
    return '';
  }

  async getPortAndUri(tunnelId) {
    const parsed = parseHostname(tunnelId, this._port);
    return { port: parsed.port, uri: '' };
  }

  /**
   * Connect to a peer by spawning a local TCP proxy through tailscale nc.
   */
  async connect(tunnelId, remotePort, localPort = 0) {
    const peerIp = this._hostnameToIp[tunnelId];
    if (!peerIp) {
      throw new Error(`Peer '${tunnelId}' not discovered yet - run listTunnels() first`);
    }

    console.log(`[Tailscale] Connecting to peer ${tunnelId} (${peerIp}:${remotePort}) via proxy`);

    const ncCmd = ['tailscale', ...this._socketArgs(), 'nc'];
    const proxyScript = path.resolve(__dirname, 'tailscale-proxy.js');

    const proc = spawn('node', [
      proxyScript,
      '--peer-ip', peerIp,
      '--peer-port', String(remotePort),
      '--tailscale-cmd', ...ncCmd,
    ], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        proc.kill();
        reject(new Error(`Tailscale proxy for ${tunnelId} did not start in time`));
      }, 5000);

      let firstLine = '';
      proc.stdout.once('data', (data) => {
        clearTimeout(timeout);
        firstLine = data.toString().trim();
        if (!firstLine.startsWith('LISTENING:')) {
          proc.kill();
          reject(new Error(`Unexpected proxy output: ${firstLine}`));
          return;
        }
        const actualPort = parseInt(firstLine.split(':')[1], 10);
        const wsUri = `ws://localhost:${actualPort}`;
        console.log(`[Tailscale] Proxy for ${tunnelId} listening on ${wsUri}`);
        resolve({ proc, wsUri });
      });

      proc.on('error', (err) => {
        clearTimeout(timeout);
        reject(err);
      });
    });
  }

  async delete(tunnelId) {
    // No-op for Tailscale
  }
}

module.exports = { TailscaleBackend, parseHostname, defaultSocket };
