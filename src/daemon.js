/**
 * Daemon orchestrator (Node.js port).
 * Ties together tunnel, shell server, discovery, and dashboard.
 */

'use strict';

const http = require('http');
const { ShellManager } = require('./shell-manager');
const { ShellServer } = require('./shell-server');
const { DashboardServer } = require('./dashboard-server');
const { makeTunnelId, getTunnelBackend } = require('./tunnel/base');
const { PeerDiscovery } = require('./tunnel/discovery');
const { getShellCommand } = require('./config');

const DISCOVERY_INTERVAL = 300;   // seconds (5 minutes)
const HEALTH_CHECK_INTERVAL = 10; // seconds
const CONNECT_TIMEOUT = 15000;    // ms

// Track child processes for cleanup
const _childProcs = new Set();

function _cleanupChildren() {
  for (const proc of _childProcs) {
    try {
      proc.kill('SIGTERM');
    } catch (e) {
      // ignore
    }
  }
}

process.on('exit', _cleanupChildren);

class Daemon {
  /**
   * @param {object} config - loaded config
   * @param {object} opts
   * @param {boolean} [opts.noTunnel=false]
   * @param {number} [opts.localPort]
   */
  constructor(config, opts = {}) {
    this._config = config;
    this._noTunnel = opts.noTunnel || false;

    this._tunnelBackend = null;
    this._shellManager = new ShellManager(getShellCommand(config));

    const shellPort = this._noTunnel
      ? (opts.localPort || 0)
      : (config.tunnel.port > 0 ? config.tunnel.port : 0);

    this._shellServer = new ShellServer(this._shellManager, {
      port: shellPort,
      host: '127.0.0.1',
      nodeName: config.node.name,
    });

    this._tunnelId = makeTunnelId(config.node.name);
    this._hostProcess = null;
    this._discovery = null;
    this._discoveryRunning = false;
    this._dashboardServer = null;

    // Peer connection tracking
    this._tunnelConnectProcs = new Map(); // name -> childProcess
    this._peerUris = new Map(); // name -> ws:// URI
    this._peerStatus = new Map(); // name -> "online"|"offline"

    this._healthCheckTimer = null;
    this._stopping = false;
    this._stopped = false;
    this._stopResolve = null;
    this._stopPromise = new Promise((resolve) => { this._stopResolve = resolve; });
  }

  _getTunnelBackend() {
    if (!this._tunnelBackend) {
      this._tunnelBackend = getTunnelBackend(
        this._config.tunnel.backend,
        { port: this._config.tunnel.port }
      );
    }
    return this._tunnelBackend;
  }

  _getPeersForDashboard() {
    const peers = [];
    const seen = new Set();

    // Self
    const selfUri = `ws://localhost:${this._shellServer.port}`;
    peers.push({
      name: `${this._config.node.name} (local)`,
      uri: selfUri,
      status: 'online',
    });
    seen.add(this._config.node.name);

    // Config peers (manual)
    for (const p of (this._config.peers || [])) {
      if (seen.has(p.name)) continue;
      let uri = p.uri;
      if (!uri.startsWith('ws://') && !uri.startsWith('wss://')) {
        uri = `ws://${uri}`;
      }
      peers.push({ name: p.name, uri, status: 'online' });
      seen.add(p.name);
    }

    // Discovered peers
    for (const [name, uri] of this._peerUris) {
      if (seen.has(name)) continue;
      peers.push({
        name,
        uri,
        status: this._peerStatus.get(name) || 'offline',
      });
      seen.add(name);
    }

    return peers;
  }

  async _refreshPeers() {
    if (this._discovery) {
      const peers = await this._discovery.refresh();
      await this._onPeersChanged(peers);
    }
  }

  async start() {
    console.log(`[Daemon] Starting for node '${this._config.node.name}'`);

    // Start shell server
    await this._shellServer.start();

    if (!this._noTunnel) {
      const backend = this._getTunnelBackend();
      const actualPort = this._shellServer.port;
      console.log(`[Daemon] Shell server on port ${actualPort}`);

      await backend.ensureTunnel(
        this._tunnelId,
        actualPort,
        this._config.node.label,
        this._config.tunnel.expiration || '30d'
      );

      console.log('[Daemon] Starting tunnel host...');
      this._hostProcess = backend.host(this._tunnelId, actualPort);
      if (this._hostProcess) {
        _childProcs.add(this._hostProcess);
        this._hostProcess.on('exit', () => {
          _childProcs.delete(this._hostProcess);
        });
      }

      // Start discovery
      this._discovery = new PeerDiscovery({
        backend,
        label: this._config.node.label,
        ownTunnelId: this._tunnelId,
        interval: DISCOVERY_INTERVAL,
        onPeersChanged: (peers) => this._onPeersChanged(peers),
      });

      console.log('[Daemon] Discovering peers...');
      const peers = await this._discovery.refresh();
      await this._onPeersChanged(peers);

      // Start discovery loop in background (non-blocking)
      this._discoveryRunning = true;
      this._discovery.runLoop({ skipFirst: true }).catch((e) => {
        if (this._discoveryRunning) {
          console.warn(`[Daemon] Discovery loop error: ${e.message}`);
        }
      });

      // Start health check loop
      this._startHealthCheckLoop();
    }

    // Dashboard server (port 9000) — API + WS proxy only
    this._dashboardServer = new DashboardServer({
      host: '127.0.0.1',
      port: this._config.node.dashboard_port,
      getPeers: () => this._getPeersForDashboard(),
      refreshPeers: () => this._refreshPeers(),
    });
    await this._dashboardServer.start();

    const mode = this._noTunnel ? 'local' : `tunnel=${this._tunnelId}`;
    console.log(`[Daemon] Running: node=${this._config.node.name}, ${mode}, shell=${this._shellServer.port}, dashboard=${this._config.node.dashboard_port}`);
  }

  async _onPeersChanged(peers) {
    const backend = this._getTunnelBackend();
    const currentNames = new Set();

    for (const peer of peers) {
      if (peer.name === this._config.node.name) continue;
      if (peer.status !== 'online') continue;
      currentNames.add(peer.name);

      const expectedUri = `ws://localhost:${peer.port}`;
      const existingUri = this._peerUris.get(peer.name);
      const existingProc = this._tunnelConnectProcs.get(peer.name);
      const procDead = existingProc && existingProc.exitCode !== null;

      if (existingUri && existingUri === expectedUri && !procDead) continue;

      try {
        const result = await Promise.race([
          backend.connect(peer.tunnelId, peer.port),
          new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), CONNECT_TIMEOUT)),
        ]);

        // Tear down old connection
        const oldProc = this._tunnelConnectProcs.get(peer.name);
        if (oldProc && oldProc.exitCode === null) {
          try { oldProc.kill(); _childProcs.delete(oldProc); } catch (e) { /* ignore */ }
        }

        if (result.proc) {
          this._tunnelConnectProcs.set(peer.name, result.proc);
          _childProcs.add(result.proc);
          result.proc.on('exit', () => _childProcs.delete(result.proc));
        }
        this._peerUris.set(peer.name, result.wsUri);
        this._peerStatus.set(peer.name, 'online');
        console.log(`[Daemon] Mapped peer ${peer.name} -> ${result.wsUri}`);
      } catch (e) {
        console.warn(`[Daemon] Failed to connect to peer ${peer.name}: ${e.message}`);
      }
    }

    // Disconnect lost peers
    for (const name of this._peerUris.keys()) {
      if (!currentNames.has(name)) {
        const proc = this._tunnelConnectProcs.get(name);
        if (proc) {
          try { proc.kill(); _childProcs.delete(proc); } catch (e) { /* ignore */ }
          this._tunnelConnectProcs.delete(name);
        }
        this._peerUris.delete(name);
        this._peerStatus.delete(name);
        console.log(`[Daemon] Disconnected peer ${name}`);
      }
    }
  }

  _startHealthCheckLoop() {
    const loop = async () => {
      if (this._stopping) return;

      const urisSnapshot = new Map(this._peerUris);
      const peersToReconnect = [];

      for (const [name, uri] of urisSnapshot) {
        const httpUrl = uri.replace('wss://', 'https://').replace('ws://', 'http://') + '/sessions';
        const alive = await this._pingPeer(httpUrl);
        const oldStatus = this._peerStatus.get(name) || 'online';

        if (this._peerUris.has(name)) {
          this._peerStatus.set(name, alive ? 'online' : 'offline');
        }

        if (!alive && oldStatus === 'online') {
          console.log(`[Daemon] Peer ${name} unreachable, killing connect process`);
          const proc = this._tunnelConnectProcs.get(name);
          if (proc && proc.exitCode === null) {
            try { proc.kill(); _childProcs.delete(proc); } catch (e) { /* ignore */ }
          }
          peersToReconnect.push(name);
        }
      }

      if (peersToReconnect.length > 0 && this._discovery && !this._stopping) {
        console.log(`[Daemon] Triggering reconnect for: ${peersToReconnect.join(', ')}`);
        try {
          const peers = await this._discovery.refresh();
          await this._onPeersChanged(peers);
        } catch (e) {
          console.warn(`[Daemon] Reconnect refresh failed: ${e.message}`);
        }
      }

      if (!this._stopping) {
        this._healthCheckTimer = setTimeout(loop, HEALTH_CHECK_INTERVAL * 1000);
      }
    };

    this._healthCheckTimer = setTimeout(loop, HEALTH_CHECK_INTERVAL * 1000);
  }

  /**
   * Ping a peer's /sessions endpoint.
   */
  _pingPeer(url) {
    return new Promise((resolve) => {
      const mod = url.startsWith('https') ? require('https') : http;
      const req = mod.get(url, { timeout: 2000 }, (res) => {
        resolve(res.statusCode === 200);
        res.resume(); // consume response
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    });
  }

  async stop() {
    if (this._stopped) return;
    this._stopped = true;
    this._stopping = true;
    console.log('[Daemon] Stopping...');

    // Cancel background tasks
    if (this._discovery) this._discovery.stop();
    this._discoveryRunning = false;
    if (this._healthCheckTimer) {
      clearTimeout(this._healthCheckTimer);
      this._healthCheckTimer = null;
    }

    // Kill tunnel connect processes
    for (const [name, proc] of this._tunnelConnectProcs) {
      try { proc.kill(); _childProcs.delete(proc); } catch (e) { /* ignore */ }
    }
    this._tunnelConnectProcs.clear();
    this._peerUris.clear();
    this._peerStatus.clear();

    // Kill host process
    if (this._hostProcess) {
      try { this._hostProcess.kill(); _childProcs.delete(this._hostProcess); } catch (e) { /* ignore */ }
    }

    // Close PTY sessions
    this._shellManager.closeAll();

    const withTimeout = (promise, ms) =>
      Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms))]);

    // Stop servers
    if (this._dashboardServer) {
      try { await withTimeout(this._dashboardServer.stop(), 2000); } catch (e) { /* ignore */ }
    }
    try { await withTimeout(this._shellServer.stop(), 2000); } catch (e) { /* ignore */ }

    console.log('[Daemon] Stopped');
    if (this._stopResolve) this._stopResolve();
  }

  /**
   * Start and run until stopped.
   */
  async runForever() {
    await this.start();

    // Set up signal handlers
    let secondSignal = false;
    const handleSignal = (signal) => {
      if (secondSignal) {
        console.warn('[Daemon] Second signal received, forcing exit');
        _cleanupChildren();
        process.exit(1);
      }
      secondSignal = true;

      // Stop with timeout
      Promise.race([
        this.stop(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
      ]).then(() => {
        process.exit(0);
      }).catch(() => {
        console.error('[Daemon] Graceful stop timed out, forcing exit');
        _cleanupChildren();
        process.exit(1);
      });
    };

    process.on('SIGINT', () => handleSignal('SIGINT'));
    process.on('SIGTERM', () => handleSignal('SIGTERM'));

    // Wait for host process to exit, or stop event
    if (this._hostProcess) {
      await new Promise((resolve) => {
        this._hostProcess.on('exit', resolve);
        this._stopPromise.then(resolve);
      });
    } else {
      await this._stopPromise;
    }
  }
}

module.exports = { Daemon, DISCOVERY_INTERVAL, HEALTH_CHECK_INTERVAL, CONNECT_TIMEOUT };
