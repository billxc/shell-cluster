/**
 * Peer discovery via tunnel backend (Node.js port).
 */

'use strict';

const { parseNodeName } = require('./base');

const PEER_STATUS = {
  ONLINE: 'online',
  OFFLINE: 'offline',
  CONNECTING: 'connecting',
};

class PeerDiscovery {
  /**
   * @param {object} opts
   * @param {object} opts.backend - tunnel backend instance
   * @param {string} opts.label
   * @param {string} opts.ownTunnelId
   * @param {number} [opts.interval=300]
   * @param {function(Array):Promise} [opts.onPeersChanged]
   */
  constructor(opts) {
    this._backend = opts.backend;
    this._label = opts.label;
    this._ownTunnelId = opts.ownTunnelId;
    this._interval = opts.interval || 300;
    this._onPeersChanged = opts.onPeersChanged || null;
    this._peers = new Map(); // tunnelId -> peer object
    this._running = false;
    this._timer = null;
  }

  get peers() {
    return new Map(this._peers);
  }

  /**
   * Refresh the peer list from the tunnel backend.
   * @returns {Promise<Array>}
   */
  async refresh() {
    let tunnels;
    try {
      tunnels = await Promise.race([
        this._backend.listTunnels(this._label),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 10000)),
      ]);
    } catch (e) {
      console.warn(`[Discovery] Refresh failed: ${e.message}`);
      return Array.from(this._peers.values());
    }

    const seen = new Set();

    for (const t of tunnels) {
      if (!t.hosting) continue;
      seen.add(t.tunnelId);

      const existing = this._peers.get(t.tunnelId);
      if (existing && existing.status === PEER_STATUS.ONLINE) {
        // Check if port changed
        if (t.port && t.port !== existing.port) {
          console.log(`[Discovery] Peer ${existing.name} port changed ${existing.port} -> ${t.port}`);
          const result = await this._backend.getPortAndUri(t.tunnelId);
          if (result.port > 0) {
            existing.port = result.port;
            existing.forwardingUri = result.uri;
          }
        }
      } else if (existing) {
        // Was offline, now back online
        console.log(`[Discovery] Peer ${existing.name} back online, refreshing port`);
        const result = await this._backend.getPortAndUri(t.tunnelId);
        if (result.port > 0) {
          existing.port = result.port;
          existing.forwardingUri = result.uri;
          existing.status = PEER_STATUS.ONLINE;
        }
      } else {
        // New peer discovered
        const result = await this._backend.getPortAndUri(t.tunnelId);
        if (!result.port || result.port <= 0) {
          console.warn(`[Discovery] New peer ${t.tunnelId} has invalid port, skipping`);
          continue;
        }
        const name = parseNodeName(t.tunnelId);
        const peer = {
          name,
          tunnelId: t.tunnelId,
          port: result.port,
          forwardingUri: result.uri,
          status: PEER_STATUS.ONLINE,
        };
        this._peers.set(t.tunnelId, peer);
        console.log(`[Discovery] Discovered peer: ${name} (${t.tunnelId})`);
      }
    }

    // Mark unseen peers as offline
    for (const [tid, peer] of this._peers) {
      if (!seen.has(tid)) {
        peer.status = PEER_STATUS.OFFLINE;
      }
    }

    return Array.from(this._peers.values());
  }

  /**
   * Run discovery in a loop.
   * @param {object} [opts]
   * @param {boolean} [opts.skipFirst=false]
   */
  async runLoop(opts = {}) {
    this._running = true;
    let first = true;

    while (this._running) {
      if (first && opts.skipFirst) {
        first = false;
      } else {
        const peers = await this.refresh();
        if (this._onPeersChanged) {
          await this._onPeersChanged(peers);
        }
      }

      // Sleep with cancellation support
      await new Promise((resolve) => {
        this._timer = setTimeout(resolve, this._interval * 1000);
      });
    }
  }

  stop() {
    this._running = false;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }
}

module.exports = { PeerDiscovery, PEER_STATUS };
