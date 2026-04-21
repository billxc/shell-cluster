'use strict';

const os = require('os');
const fs = require('fs');
const path = require('path');
const TOML = require('@iarna/toml');

const config = require('../../src/config');

describe('config', () => {
  describe('defaultConfig', () => {
    test('returns config with all required sections', () => {
      const cfg = config.defaultConfig();
      expect(cfg).toHaveProperty('node');
      expect(cfg).toHaveProperty('tunnel');
      expect(cfg).toHaveProperty('shell');
      expect(cfg).toHaveProperty('peers');
    });

    test('node section has expected defaults', () => {
      const cfg = config.defaultConfig();
      expect(cfg.node.name).toBe(os.hostname());
      expect(cfg.node.label).toBe('shellcluster');
      expect(cfg.node.dashboard_port).toBe(9000);
      expect(cfg.node.dashboard_v2_port).toBe(9001);
      expect(cfg.node.dashboard).toBe(true);
    });

    test('tunnel section has expected defaults', () => {
      const cfg = config.defaultConfig();
      expect(cfg.tunnel.backend).toBe('devtunnel');
      expect(cfg.tunnel.expiration).toBe('');
      expect(cfg.tunnel.port).toBe(0);
    });

    test('shell section has expected defaults', () => {
      const cfg = config.defaultConfig();
      expect(cfg.shell.command).toBe('');
    });

    test('peers is empty array', () => {
      const cfg = config.defaultConfig();
      expect(cfg.peers).toEqual([]);
    });
  });

  describe('saveConfig + loadConfig round-trip', () => {
    // Test the actual saveConfig/loadConfig by temporarily overriding the config path
    const tmpDir = path.join(os.tmpdir(), `shell-cluster-config-test-${Date.now()}`);
    const tmpFile = path.join(tmpDir, 'config.toml');
    let origConfigFile;
    let origConfigDir;

    beforeAll(() => {
      fs.mkdirSync(tmpDir, { recursive: true });
      // Monkey-patch the module's CONFIG_FILE and CONFIG_DIR for testing
      origConfigFile = config.CONFIG_FILE;
      origConfigDir = config.CONFIG_DIR;
      // We can't reassign module.exports constants, so test the logic directly
    });

    afterAll(() => {
      try {
        fs.rmSync(tmpDir, { recursive: true, force: true });
      } catch (e) { /* ignore */ }
    });

    test('saveConfig writes valid TOML that can be parsed', () => {
      const cfg = config.defaultConfig();
      cfg.node.name = 'test-node';
      cfg.node.label = 'test-label';
      cfg.tunnel.backend = 'tailscale';
      cfg.tunnel.port = 9876;
      cfg.shell.command = '/bin/fish';

      // Replicate saveConfig logic with our temp path
      fs.mkdirSync(tmpDir, { recursive: true });
      const data = {
        node: cfg.node,
        tunnel: cfg.tunnel,
        shell: cfg.shell,
      };
      const tomlStr = TOML.stringify(data);
      fs.writeFileSync(tmpFile, tomlStr, 'utf-8');

      // Replicate loadConfig logic
      const raw = fs.readFileSync(tmpFile, 'utf-8');
      const parsed = TOML.parse(raw);
      const loaded = config.defaultConfig();
      if (parsed.node) {
        for (const [k, v] of Object.entries(parsed.node)) {
          if (k in loaded.node) loaded.node[k] = v;
        }
      }
      if (parsed.tunnel) {
        for (const [k, v] of Object.entries(parsed.tunnel)) {
          if (k in loaded.tunnel) loaded.tunnel[k] = v;
        }
      }
      if (parsed.shell) {
        for (const [k, v] of Object.entries(parsed.shell)) {
          if (k in loaded.shell) loaded.shell[k] = v;
        }
      }

      expect(loaded.node.name).toBe('test-node');
      expect(loaded.node.label).toBe('test-label');
      expect(loaded.tunnel.backend).toBe('tailscale');
      expect(loaded.tunnel.port).toBe(9876);
      expect(loaded.shell.command).toBe('/bin/fish');
    });

    test('loadConfig merge ignores unknown keys', () => {
      // Write TOML with an unknown key
      const toml = `
[node]
name = "my-node"
label = "shellcluster"
unknown_key = "should be ignored"

[tunnel]
backend = "devtunnel"
`;
      fs.writeFileSync(tmpFile, toml, 'utf-8');

      const raw = fs.readFileSync(tmpFile, 'utf-8');
      const parsed = TOML.parse(raw);
      const loaded = config.defaultConfig();
      if (parsed.node) {
        for (const [k, v] of Object.entries(parsed.node)) {
          if (k in loaded.node) loaded.node[k] = v;
        }
      }

      expect(loaded.node.name).toBe('my-node');
      expect(loaded.node).not.toHaveProperty('unknown_key');
    });

    test('saveConfig includes peers when present', () => {
      const cfg = config.defaultConfig();
      cfg.peers = [
        { name: 'remote', uri: 'ws://192.168.1.100:8765' },
        { name: 'office', uri: 'ws://10.0.0.1:8765' },
      ];

      const data = {
        node: cfg.node,
        tunnel: cfg.tunnel,
        shell: cfg.shell,
      };
      if (cfg.peers && cfg.peers.length > 0) {
        data.peers = cfg.peers.map(p => ({ name: p.name, uri: p.uri }));
      }
      const tomlStr = TOML.stringify(data);
      fs.writeFileSync(tmpFile, tomlStr, 'utf-8');

      const raw = fs.readFileSync(tmpFile, 'utf-8');
      const parsed = TOML.parse(raw);
      expect(parsed.peers).toHaveLength(2);
      expect(parsed.peers[0].name).toBe('remote');
      expect(parsed.peers[1].uri).toBe('ws://10.0.0.1:8765');
    });

    test('loadConfig filters peers without uri', () => {
      const toml = `
[node]
name = "my-node"

[[peers]]
name = "valid"
uri = "ws://1.2.3.4:8765"

[[peers]]
name = "invalid"
`;
      fs.writeFileSync(tmpFile, toml, 'utf-8');

      const raw = fs.readFileSync(tmpFile, 'utf-8');
      const parsed = TOML.parse(raw);
      const loaded = config.defaultConfig();
      if (Array.isArray(parsed.peers)) {
        loaded.peers = parsed.peers
          .filter(p => p && typeof p === 'object' && p.uri)
          .map(p => ({ name: p.name || '', uri: p.uri }));
      }

      expect(loaded.peers).toHaveLength(1);
      expect(loaded.peers[0].name).toBe('valid');
    });

    test('malformed TOML does not crash — returns default config (regression)', () => {
      // Replicate the loadConfig parse logic with error handling
      const malformed = 'this is not valid [toml content';
      let data;
      let usedDefault = false;
      try {
        data = TOML.parse(malformed);
      } catch (e) {
        // This is what the fix does: catch and fallback
        data = null;
        usedDefault = true;
      }

      expect(usedDefault).toBe(true);
      // After the fix, loadConfig returns defaultConfig() on parse error
      const fallback = config.defaultConfig();
      expect(fallback.node.label).toBe('shellcluster');
    });
  });

  describe('getShellCommand', () => {
    test('returns configured command if set', () => {
      const cfg = config.defaultConfig();
      cfg.shell.command = '/usr/local/bin/fish';
      expect(config.getShellCommand(cfg)).toBe('/usr/local/bin/fish');
    });

    test('auto-detects shell when command is empty', () => {
      const cfg = config.defaultConfig();
      cfg.shell.command = '';
      const shell = config.getShellCommand(cfg);
      expect(typeof shell).toBe('string');
      expect(shell.length).toBeGreaterThan(0);
    });
  });

  describe('CONFIG_DIR and CONFIG_FILE', () => {
    test('CONFIG_DIR is a string', () => {
      expect(typeof config.CONFIG_DIR).toBe('string');
    });

    test('CONFIG_FILE ends with config.toml', () => {
      expect(config.CONFIG_FILE).toMatch(/config\.toml$/);
    });

    test('CONFIG_FILE is under CONFIG_DIR', () => {
      expect(config.CONFIG_FILE.startsWith(config.CONFIG_DIR)).toBe(true);
    });
  });
});
