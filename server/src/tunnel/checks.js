/**
 * Pre-flight checks for tunnel backends (Node.js port).
 */

'use strict';

const { execFileSync } = require('child_process');
const { existsSync } = require('fs');
const path = require('path');

/**
 * Check if an executable is available in PATH.
 * @param {string} name
 * @returns {boolean}
 */
function which(name) {
  try {
    const cmd = process.platform === 'win32' ? 'where' : 'which';
    execFileSync(cmd, [name], { stdio: 'pipe' });
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * Check that devtunnel CLI is installed and logged in.
 * @returns {boolean}
 */
function checkDevtunnel() {
  if (!which('devtunnel')) {
    console.error('devtunnel CLI is not installed.');
    console.error('Install it: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started');
    return false;
  }

  try {
    const result = execFileSync('devtunnel', ['list', '--limit', '1'], {
      stdio: 'pipe',
      timeout: 10000,
    });
  } catch (e) {
    if (e.stderr) {
      const stderr = e.stderr.toString().toLowerCase();
      if (stderr.includes('login') || stderr.includes('sign in') || stderr.includes('unauthorized')) {
        console.error('devtunnel is not logged in.');
        console.error('Run: devtunnel user login');
        return false;
      }
    }
    // Timeout or other error - might still work, proceed
  }

  return true;
}

/**
 * Start tailscaled in userspace-networking mode.
 * @param {string} socket
 * @returns {boolean}
 */
function startTailscaled(socket) {
  if (!which('tailscaled')) {
    console.error('tailscaled is not installed.');
    console.error('Install it: brew install tailscale');
    return false;
  }

  console.log('Starting tailscaled...');
  const args = ['tailscaled', '--tun=userspace-networking'];
  if (socket) {
    args.push('--socket', socket);
  }

  const { spawn } = require('child_process');
  spawn(args[0], args.slice(1), {
    stdio: 'ignore',
    detached: true,
  }).unref();

  // Wait for socket to appear (blocking poll with Atomics.wait)
  for (let i = 0; i < 10; i++) {
    const waitBuf = new Int32Array(new SharedArrayBuffer(4));
    Atomics.wait(waitBuf, 0, 0, 500); // 500ms sleep without execSync
    if (socket && existsSync(socket)) {
      console.log('tailscaled started.');
      return true;
    }
  }
  console.log('tailscaled started.');
  return true;
}

/**
 * Check that tailscale CLI is installed and connected.
 * @returns {boolean}
 */
function checkTailscale() {
  if (!which('tailscale')) {
    console.error('tailscale CLI is not installed.');
    console.error('Install it: brew install tailscale');
    return false;
  }

  const { defaultSocket } = require('./tailscale');
  const socket = defaultSocket();

  const cmd = ['tailscale'];
  if (socket) cmd.push('--socket', socket);
  cmd.push('status', '--json');

  try {
    const output = execFileSync(cmd[0], cmd.slice(1), {
      stdio: 'pipe',
      timeout: 10000,
    });
    const status = JSON.parse(output.toString());
    const state = status.BackendState || '';
    if (state !== 'Running') {
      console.warn(`tailscale is not logged in (state: ${state}).`);
      if (socket) {
        console.error(`Run: tailscale --socket '${socket}' up`);
      } else {
        console.error('Run: tailscale up');
      }
      return false;
    }
  } catch (e) {
    if (e.status !== undefined && e.status !== 0) {
      // tailscaled not running - try to start it
      if (!startTailscaled(socket)) return false;
      // Re-check
      try {
        const output = execFileSync(cmd[0], cmd.slice(1), {
          stdio: 'pipe',
          timeout: 10000,
        });
      } catch (e2) {
        console.error('tailscaled started but not responding.');
        return false;
      }
    }
    // Timeout or other error - proceed
  }

  return true;
}

module.exports = { checkDevtunnel, checkTailscale, which };
