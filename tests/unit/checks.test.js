'use strict';

const { which } = require('../../src/tunnel/checks');

describe('tunnel/checks', () => {
  describe('which', () => {
    test('returns true for existing command (node)', () => {
      expect(which('node')).toBe(true);
    });

    test('returns false for non-existent command', () => {
      expect(which('nonexistent-command-xyz-12345')).toBe(false);
    });

    test('returns true for npm', () => {
      expect(which('npm')).toBe(true);
    });
  });

  describe('checkDevtunnel', () => {
    test('function exists', () => {
      const { checkDevtunnel } = require('../../src/tunnel/checks');
      expect(typeof checkDevtunnel).toBe('function');
    });
  });

  describe('checkTailscale', () => {
    test('function exists', () => {
      const { checkTailscale } = require('../../src/tunnel/checks');
      expect(typeof checkTailscale).toBe('function');
    });
  });
});
