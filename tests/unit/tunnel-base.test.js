'use strict';

const { makeTunnelId, parseNodeName, getTunnelBackend, TUNNEL_PREFIX, TUNNEL_SUFFIX } = require('../../src/tunnel/base');

describe('tunnel/base', () => {
  describe('makeTunnelId', () => {
    test('creates ID with prefix and suffix', () => {
      expect(makeTunnelId('my-mac')).toBe('shellcluster-my-mac-shellcluster');
    });

    test('lowercases the name', () => {
      expect(makeTunnelId('My-Mac')).toBe('shellcluster-my-mac-shellcluster');
    });

    test('handles simple hostname', () => {
      expect(makeTunnelId('server1')).toBe('shellcluster-server1-shellcluster');
    });
  });

  describe('parseNodeName', () => {
    test('extracts name from full tunnel ID', () => {
      expect(parseNodeName('shellcluster-my-mac-shellcluster')).toBe('my-mac');
    });

    test('strips region suffix', () => {
      expect(parseNodeName('shellcluster-my-mac-shellcluster.jpe1')).toBe('my-mac');
    });

    test('returns input if not matching prefix/suffix', () => {
      expect(parseNodeName('some-other-id')).toBe('some-other-id');
    });

    test('handles names with dashes', () => {
      expect(parseNodeName('shellcluster-my-home-server-shellcluster')).toBe('my-home-server');
    });

    // Ported from Python: tailscale hostnames pass through
    test('passes through tailscale hostnames (no prefix/suffix)', () => {
      expect(parseNodeName('work-pc')).toBe('work-pc');
      expect(parseNodeName('home-server')).toBe('home-server');
      expect(parseNodeName('my-mac')).toBe('my-mac');
    });
  });

  describe('getTunnelBackend', () => {
    test('returns DevTunnelBackend for "devtunnel"', () => {
      const backend = getTunnelBackend('devtunnel');
      expect(backend.constructor.name).toBe('DevTunnelBackend');
    });

    test('returns TailscaleBackend for "tailscale"', () => {
      const backend = getTunnelBackend('tailscale', { port: 9876 });
      expect(backend.constructor.name).toBe('TailscaleBackend');
    });

    test('defaults to devtunnel', () => {
      const backend = getTunnelBackend();
      expect(backend.constructor.name).toBe('DevTunnelBackend');
    });

    test('throws on unknown backend', () => {
      expect(() => getTunnelBackend('cloudflare')).toThrow('Unknown tunnel backend');
    });
  });

  describe('constants', () => {
    test('TUNNEL_PREFIX is correct', () => {
      expect(TUNNEL_PREFIX).toBe('shellcluster-');
    });

    test('TUNNEL_SUFFIX is correct', () => {
      expect(TUNNEL_SUFFIX).toBe('-shellcluster');
    });
  });
});
