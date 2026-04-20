/**
 * Tunnel base utilities and factory.
 */

'use strict';

const TUNNEL_PREFIX = 'shellcluster-';
const TUNNEL_SUFFIX = '-shellcluster';

/**
 * Create a tunnel ID from a node name.
 * @param {string} nodeName
 * @returns {string}
 */
function makeTunnelId(nodeName) {
  return `${TUNNEL_PREFIX}${nodeName.toLowerCase()}${TUNNEL_SUFFIX}`;
}

/**
 * Extract node name from a tunnel ID, stripping region suffix.
 * Examples:
 *   shellcluster-my-mac-shellcluster.jpe1 -> my-mac
 *   shellcluster-my-mac-shellcluster -> my-mac
 * @param {string} tunnelId
 * @returns {string}
 */
function parseNodeName(tunnelId) {
  const base = tunnelId.includes('.') ? tunnelId.split('.')[0] : tunnelId;
  if (base.startsWith(TUNNEL_PREFIX) && base.endsWith(TUNNEL_SUFFIX)) {
    return base.slice(TUNNEL_PREFIX.length, -TUNNEL_SUFFIX.length);
  }
  return tunnelId;
}

/**
 * Create a tunnel backend by name.
 * @param {string} backendName - "devtunnel" or "tailscale"
 * @param {object} [opts]
 * @param {number} [opts.port]
 * @returns {object} tunnel backend instance
 */
function getTunnelBackend(backendName = 'devtunnel', opts = {}) {
  if (backendName === 'devtunnel') {
    const { DevTunnelBackend } = require('./devtunnel');
    return new DevTunnelBackend();
  }
  if (backendName === 'tailscale') {
    const { TailscaleBackend } = require('./tailscale');
    return new TailscaleBackend({ port: opts.port || 9876 });
  }
  throw new Error(`Unknown tunnel backend: ${backendName}`);
}

module.exports = {
  TUNNEL_PREFIX,
  TUNNEL_SUFFIX,
  makeTunnelId,
  parseNodeName,
  getTunnelBackend,
};
