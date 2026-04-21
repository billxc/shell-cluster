'use strict';

const { ShellManager } = require('../../src/shell-manager');

describe('ShellManager', () => {
  let manager;

  beforeEach(() => {
    manager = new ShellManager();
  });

  afterEach(() => {
    manager.closeAll();
  });

  describe('constructor', () => {
    test('creates with default shell', () => {
      expect(manager._sessions).toBeInstanceOf(Map);
      expect(manager._sessions.size).toBe(0);
    });

    test('creates with custom shell', () => {
      const m = new ShellManager('/bin/bash');
      expect(m._defaultShell).toBe('/bin/bash');
      m.closeAll();
    });
  });

  describe('ptyError', () => {
    test('returns null when pty is available', () => {
      // node-pty should be available in test environment
      expect(manager.ptyError).toBeNull();
    });
  });

  describe('create', () => {
    test('creates a session with given ID', () => {
      const onOutput = jest.fn();
      const onExit = jest.fn();
      const session = manager.create('s1', '', 80, 24, onOutput, onExit);

      expect(session.sessionId).toBe('s1');
      expect(session.pid).toBeGreaterThan(0);
      expect(session._disposed).toBe(false);
      expect(manager._sessions.has('s1')).toBe(true);
    });

    test('session has terminal and serializer', () => {
      const session = manager.create('s2', '', 80, 24, null, null);

      expect(session.terminal).toBeDefined();
      expect(session.serializer).toBeDefined();
      expect(session.createdAt).toBeInstanceOf(Date);
    });

    test('calls onOutput when PTY produces data', (done) => {
      const onOutput = jest.fn((sid, data) => {
        expect(sid).toBe('s3');
        expect(Buffer.isBuffer(data)).toBe(true);
        done();
      });
      manager.create('s3', '', 80, 24, onOutput, null);
      // PTY should produce some initial output (prompt)
    }, 5000);

    test('calls onExit when shell exits', (done) => {
      const onExit = jest.fn((sid) => {
        expect(sid).toBe('s4');
        done();
      });
      const session = manager.create('s4', '', 80, 24, null, onExit);
      // Send exit command to trigger shell exit
      session.pty.write('exit\n');
    }, 10000);
  });

  describe('write', () => {
    test('writes string data to session', () => {
      manager.create('s5', '', 80, 24, null, null);
      const result = manager.write('s5', 'echo hello\n');
      expect(result).toBe(true);
    });

    test('writes buffer data to session', () => {
      manager.create('s6', '', 80, 24, null, null);
      const result = manager.write('s6', Buffer.from('echo hello\n'));
      expect(result).toBe(true);
    });

    test('returns false for non-existent session', () => {
      const result = manager.write('nonexistent', 'test');
      expect(result).toBe(false);
    });
  });

  describe('resize', () => {
    test('resizes session terminal and PTY', () => {
      manager.create('s7', '', 80, 24, null, null);
      // Should not throw
      manager.resize('s7', 120, 40);
    });

    test('ignores resize for non-existent session', () => {
      // Should not throw
      manager.resize('nonexistent', 80, 24);
    });
  });

  describe('attach / detach', () => {
    test('attaches new listener to existing session', () => {
      manager.create('s8', '', 80, 24, null, null);

      const onOutput = jest.fn();
      const onExit = jest.fn();
      const session = manager.attach('s8', onOutput, onExit);

      expect(session).not.toBeNull();
      expect(session._outputs.has(onOutput)).toBe(true);
      expect(session._exits.has(onExit)).toBe(true);
    });

    test('returns null for non-existent session', () => {
      const result = manager.attach('nonexistent', jest.fn(), jest.fn());
      expect(result).toBeNull();
    });

    test('detaches listener from session', () => {
      const onOutput = jest.fn();
      manager.create('s9', '', 80, 24, onOutput, null);

      manager.detach('s9', onOutput, null);

      const session = manager._sessions.get('s9');
      expect(session._outputs.has(onOutput)).toBe(false);
    });
  });

  describe('getSerializedState', () => {
    test('returns string for existing session', () => {
      manager.create('s10', '', 80, 24, null, null);
      const state = manager.getSerializedState('s10');
      expect(typeof state).toBe('string');
    });

    test('returns empty string for non-existent session', () => {
      const state = manager.getSerializedState('nonexistent');
      expect(state).toBe('');
    });
  });

  describe('close', () => {
    test('closes existing session', () => {
      manager.create('s11', '', 80, 24, null, null);
      expect(manager._sessions.has('s11')).toBe(true);

      const result = manager.close('s11');
      expect(result).toBe(true);
      expect(manager._sessions.has('s11')).toBe(false);
    });

    test('returns false for non-existent session', () => {
      const result = manager.close('nonexistent');
      expect(result).toBe(false);
    });
  });

  describe('closeAll', () => {
    test('closes all sessions', () => {
      manager.create('a1', '', 80, 24, null, null);
      manager.create('a2', '', 80, 24, null, null);
      expect(manager._sessions.size).toBe(2);

      manager.closeAll();
      expect(manager._sessions.size).toBe(0);
    });
  });

  describe('listSessions', () => {
    test('lists all active sessions', () => {
      manager.create('l1', '', 80, 24, null, null);
      manager.create('l2', '', 80, 24, null, null);

      const list = manager.listSessions();
      expect(list).toHaveLength(2);
      const ids = list.map(s => s.id);
      expect(ids).toContain('l1');
      expect(ids).toContain('l2');
    });

    test('includes shell and created_at', () => {
      manager.create('l3', '', 80, 24, null, null);

      const list = manager.listSessions();
      expect(list[0]).toHaveProperty('shell');
      expect(list[0]).toHaveProperty('created_at');
      expect(typeof list[0].created_at).toBe('string');
    });

    test('returns empty array when no sessions', () => {
      expect(manager.listSessions()).toEqual([]);
    });
  });

  describe('sessions getter', () => {
    test('returns the internal map', () => {
      expect(manager.sessions).toBeInstanceOf(Map);
      expect(manager.sessions).toBe(manager._sessions);
    });
  });

  describe('pausePty / resumePty', () => {
    test('does not throw on valid session', () => {
      manager.create('p1', '', 80, 24, null, null);
      expect(() => manager.pausePty('p1')).not.toThrow();
      expect(() => manager.resumePty('p1')).not.toThrow();
    });

    test('does not throw on non-existent session', () => {
      expect(() => manager.pausePty('nonexistent')).not.toThrow();
      expect(() => manager.resumePty('nonexistent')).not.toThrow();
    });
  });
});
