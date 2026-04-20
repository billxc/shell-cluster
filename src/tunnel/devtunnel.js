/**
 * MS Dev Tunnel backend implementation (Node.js port).
 */

'use strict';

const { spawn } = require('child_process');
const { parseNodeName } = require('./base');

class DevTunnelBackend {
  /**
   * Run a devtunnel command and return stdout.
   * @param {string[]} args
   * @param {object} [opts]
   * @param {boolean} [opts.check=true]
   * @returns {Promise<string>}
   */
  _run(args, opts = {}) {
    const check = opts.check !== false;
    const cmd = 'devtunnel';
    const fullArgs = [...args];

    return new Promise((resolve, reject) => {
      const proc = spawn(cmd, fullArgs, {
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => { stdout += data.toString(); });
      proc.stderr.on('data', (data) => { stderr += data.toString(); });

      proc.on('error', (err) => {
        reject(new Error(`Failed to run devtunnel: ${err.message}`));
      });

      proc.on('close', (code) => {
        if (check && code !== 0) {
          reject(new Error(`devtunnel ${args[0]} failed (exit ${code}): ${stderr.trim()}`));
        } else {
          resolve(stdout);
        }
      });
    });
  }

  /**
   * Run a devtunnel command with --json flag and parse output.
   * @param {string[]} args
   * @returns {Promise<object>}
   */
  async _runJson(args) {
    const output = (await this._run([...args, '--json'])).trim();
    if (!output) return {};

    // devtunnel may prepend non-JSON text (welcome banner etc.)
    for (let i = 0; i < output.length; i++) {
      if (output[i] === '{' || output[i] === '[') {
        try {
          return JSON.parse(output.slice(i));
        } catch (e) {
          // continue looking
        }
      }
    }
    console.warn('[DevTunnel] Could not parse JSON output:', output.slice(0, 200));
    return {};
  }

  /**
   * Check if a tunnel exists.
   * @param {string} tunnelId
   * @returns {Promise<boolean>}
   */
  async exists(tunnelId) {
    try {
      await this._run(['show', tunnelId]);
      return true;
    } catch (e) {
      return false;
    }
  }

  /**
   * Create a tunnel and add a port.
   * @param {string} tunnelId
   * @param {number} port
   * @param {string} label
   * @param {string} [expiration='']
   * @returns {Promise<object>}
   */
  async create(tunnelId, port, label, expiration = '') {
    const args = ['create', tunnelId, '--labels', label];
    if (expiration) {
      args.push('--expiration', expiration);
    }
    await this._run(args);
    await this._run(['port', 'create', tunnelId, '-p', String(port)]);

    return {
      tunnelId,
      labels: [label],
      port,
      description: parseNodeName(tunnelId),
    };
  }

  /**
   * Ensure tunnel exists with the right port - reuse if present, create if not.
   */
  async ensureTunnel(tunnelId, port, label, expiration = '30d') {
    if (await this.exists(tunnelId)) {
      console.log(`[DevTunnel] Reusing existing tunnel ${tunnelId}`);
      try {
        const data = await this._runJson(['show', tunnelId]);
        const tunnelData = data.tunnel || data;
        for (const p of (tunnelData.ports || [])) {
          const oldPort = p.portNumber || 0;
          if (oldPort && oldPort !== port) {
            await this._run(['port', 'delete', tunnelId, '-p', String(oldPort)], { check: false });
          }
        }
        await this._run(['port', 'create', tunnelId, '-p', String(port)], { check: false });
      } catch (e) {
        console.warn(`[DevTunnel] Failed to update tunnel port: ${e.message}`);
      }
    } else {
      // Check for queued-for-delete tunnel
      await this._deleteIfQueuedForDelete(tunnelId, label);
      console.log(`[DevTunnel] Creating new tunnel ${tunnelId}`);
      await this.create(tunnelId, port, label, expiration);
    }
  }

  async _deleteIfQueuedForDelete(tunnelId, label) {
    try {
      const data = await this._runJson(['list', '--labels', label]);
      const items = Array.isArray(data) ? data : (data.tunnels || data.value || []);
      for (const item of items) {
        if (item.tunnelId === tunnelId) {
          if (item.tunnelExpiration === 'queued for delete') {
            console.log(`[DevTunnel] Tunnel ${tunnelId} is queued for delete, deleting first`);
            await this.delete(tunnelId);
            return true;
          }
          break;
        }
      }
    } catch (e) {
      console.warn(`[DevTunnel] Failed to check queued-for-delete: ${e.message}`);
    }
    return false;
  }

  /**
   * Start hosting the tunnel as a long-running subprocess.
   * @param {string} tunnelId
   * @param {number} port
   * @returns {import('child_process').ChildProcess}
   */
  host(tunnelId, port) {
    const cmd = 'devtunnel';
    const args = ['host', tunnelId];
    console.log(`[DevTunnel] Starting tunnel host: devtunnel ${args.join(' ')}`);
    const proc = spawn(cmd, args, {
      stdio: ['pipe', 'ignore', 'ignore'],
    });
    return proc;
  }

  /**
   * List all tunnels with the given label.
   * @param {string} label
   * @returns {Promise<object[]>}
   */
  async listTunnels(label) {
    let data;
    try {
      data = await this._runJson(['list', '--labels', label]);
    } catch (e) {
      console.warn('[DevTunnel] Failed to list tunnels');
      return [];
    }

    const items = Array.isArray(data) ? data : (data.tunnels || data.value || []);
    return items.map((item) => {
      const ports = item.ports || [];
      const port = ports.length > 0 ? (ports[0].portNumber || 0) : 0;
      const hostConns = item.hostConnections || 0;
      return {
        tunnelId: item.tunnelId || '',
        labels: item.labels || [],
        port,
        description: item.description || '',
        hosting: hostConns > 0,
      };
    });
  }

  /**
   * Get forwarding URI from devtunnel show --json.
   * @param {string} tunnelId
   * @param {number} port
   * @returns {Promise<string>}
   */
  async getForwardingUri(tunnelId, port) {
    let data;
    try {
      data = await this._runJson(['show', tunnelId]);
    } catch (e) {
      console.warn(`[DevTunnel] Failed to get forwarding URI for ${tunnelId}`);
      return '';
    }

    const tunnelData = data.tunnel || data;
    for (const p of (tunnelData.ports || [])) {
      const pnum = p.portNumber || 0;
      if (pnum === port || port === 0) {
        if (p.portUri) return p.portUri;
        const uris = p.portForwardingUris || [];
        if (uris.length > 0) return uris[0];
      }
    }

    console.warn(`[DevTunnel] Could not determine forwarding URI for ${tunnelId}:${port}`);
    return '';
  }

  /**
   * Get (remotePort, forwardingUri) for the first port on a tunnel.
   * @param {string} tunnelId
   * @returns {Promise<{port: number, uri: string}>}
   */
  async getPortAndUri(tunnelId) {
    let data;
    try {
      data = await this._runJson(['show', tunnelId]);
    } catch (e) {
      return { port: 0, uri: '' };
    }

    const tunnelData = data.tunnel || data;
    for (const p of (tunnelData.ports || [])) {
      const port = p.portNumber || 0;
      const uri = p.portUri || '';
      if (port) return { port, uri };
    }
    return { port: 0, uri: '' };
  }

  /**
   * Connect to a tunnel, mapping its ports locally.
   * @param {string} tunnelId
   * @param {number} remotePort
   * @param {number} [localPort=0]
   * @returns {Promise<{proc: ChildProcess, wsUri: string}>}
   */
  connect(tunnelId, remotePort, localPort = 0) {
    return new Promise((resolve, reject) => {
      const cmd = 'devtunnel';
      const args = ['connect', tunnelId];
      console.log(`[DevTunnel] Connecting tunnel: devtunnel ${args.join(' ')}`);

      const proc = spawn(cmd, args, {
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      // Wait for connection to establish
      setTimeout(() => {
        if (proc.exitCode !== null) {
          reject(new Error(`devtunnel connect failed (exit ${proc.exitCode})`));
        } else {
          resolve({ proc, wsUri: `ws://localhost:${remotePort}` });
        }
      }, 3000);

      proc.on('error', reject);
    });
  }

  /**
   * Delete a tunnel.
   * @param {string} tunnelId
   */
  async delete(tunnelId) {
    try {
      await this._run(['delete', tunnelId, '-f']);
    } catch (e) {
      console.warn(`[DevTunnel] Failed to delete tunnel ${tunnelId}`);
    }
  }
}

module.exports = { DevTunnelBackend };
