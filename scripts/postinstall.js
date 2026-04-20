#!/usr/bin/env node
'use strict';

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ptyDir = path.join(__dirname, '..', 'node_modules', 'node-pty');
if (!fs.existsSync(ptyDir)) process.exit(0);

// Fix spawn-helper permissions on macOS/Linux
try {
  const helper = path.join(ptyDir, 'prebuilds', `${process.platform}-${process.arch}`, 'spawn-helper');
  if (fs.existsSync(helper)) fs.chmodSync(helper, 0o755);
} catch (e) {}

// Test if node-pty loads
try {
  require('node-pty');
  process.exit(0);
} catch (e) {
  console.warn('[shell-cluster] node-pty prebuild failed, rebuilding...');
}

// Attempt rebuild
try {
  execSync('npm rebuild node-pty', {
    cwd: path.join(__dirname, '..'),
    stdio: 'inherit',
    timeout: 120000,
  });
} catch (e) {
  console.error('[shell-cluster] node-pty rebuild failed. Install C++ build tools and retry.');
  process.exit(0);
}

// Verify after rebuild
try {
  delete require.cache[require.resolve('node-pty')];
  require('node-pty');
  console.log('[shell-cluster] node-pty OK');
} catch (e) {
  console.error('[shell-cluster] node-pty still broken: ' + e.message.split('\n')[0]);
}
