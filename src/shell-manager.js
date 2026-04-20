/**
 * Shell session manager using node-pty + xterm-headless.
 *
 * Each session owns:
 *   - A node-pty IPty instance (the real PTY)
 *   - An xterm-headless Terminal (full terminal state tracking)
 *   - A SerializeAddon (to snapshot state for reconnect)
 */

'use strict';

const os = require('os');
const path = require('path');
const { Terminal } = require('@xterm/headless');
const { SerializeAddon } = require('@xterm/addon-serialize');

// node-pty requires platform-specific native binaries.
// Detect availability at load time so we can fail gracefully.
let pty = null;
let ptyLoadError = null;
try {
  pty = require('node-pty');
} catch (e) {
  ptyLoadError = e.message;
}

const IS_WINDOWS = process.platform === 'win32';

function defaultShell() {
  if (IS_WINDOWS) {
    return process.env.COMSPEC || 'cmd.exe';
  }
  return process.env.SHELL || '/bin/sh';
}

class ShellManager {
  constructor(shell) {
    this._defaultShell = shell || defaultShell();
    /** @type {Map<string, object>} */
    this._sessions = new Map();
  }

  /**
   * Create a new PTY session.
   * @param {string} sessionId
   * @param {string} shell - shell binary (empty = default)
   * @param {number} cols
   * @param {number} rows
   * @param {function(string, Buffer):void} onOutput - (sessionId, data)
   * @param {function(string):void} onExit - (sessionId)
   * @returns {object} session
   */
  /** @returns {string|null} Error message if PTY is unavailable, null if OK */
  get ptyError() {
    return ptyLoadError;
  }

  create(sessionId, shell, cols, rows, onOutput, onExit) {
    if (!pty) {
      throw new Error(`PTY not available: ${ptyLoadError}`);
    }
    const shellCmd = shell || this._defaultShell;
    const shellName = path.basename(shellCmd);

    const env = Object.assign({}, process.env, {
      TERM: 'xterm-256color',
    });
    if (!env.LANG) env.LANG = 'en_US.UTF-8';
    if (!env.LC_CTYPE) env.LC_CTYPE = 'en_US.UTF-8';

    const ptyProcess = pty.spawn(shellCmd, [], {
      name: 'xterm-256color',
      cols,
      rows,
      cwd: os.homedir(),
      env,
    });

    // Create headless terminal for state tracking
    const terminal = new Terminal({ cols, rows, allowProposedApi: true });
    const serializer = new SerializeAddon();
    terminal.loadAddon(serializer);

    const session = {
      sessionId,
      shell: shellName,
      createdAt: new Date(),
      pid: ptyProcess.pid,
      pty: ptyProcess,
      terminal,
      serializer,
      _outputs: new Set(onOutput ? [onOutput] : []),
      _exits: new Set(onExit ? [onExit] : []),
      _disposed: false,
    };

    // Wire PTY output -> headless terminal + all listeners
    ptyProcess.onData((data) => {
      if (session._disposed) return;
      // data is a string from node-pty
      terminal.write(data);
      const buf = Buffer.from(data, 'utf-8');
      for (const cb of session._outputs) {
        cb(sessionId, buf);
      }
    });

    ptyProcess.onExit(() => {
      if (session._disposed) return;
      session._disposed = true;
      for (const cb of session._exits) {
        cb(sessionId);
      }
      // Clean up: remove from map and dispose terminal
      this._sessions.delete(sessionId);
      try {
        terminal.dispose();
      } catch (e) {
        // ignore
      }
      console.log(`[ShellManager] Session ${sessionId} exited and cleaned up`);
    });

    this._sessions.set(sessionId, session);
    console.log(`[ShellManager] Created session ${sessionId} (pid=${ptyProcess.pid}, shell=${shellCmd})`);
    return session;
  }

  /**
   * Write input data to a session's PTY.
   * @param {string} sessionId
   * @param {Buffer|string} data
   * @returns {boolean}
   */
  write(sessionId, data) {
    const session = this._sessions.get(sessionId);
    if (!session || session._disposed) return false;
    try {
      session.pty.write(typeof data === 'string' ? data : data.toString('utf-8'));
      return true;
    } catch (e) {
      console.warn(`[ShellManager] Write failed for ${sessionId}:`, e.message);
      return false;
    }
  }

  /**
   * Resize a session's PTY and headless terminal.
   */
  resize(sessionId, cols, rows) {
    const session = this._sessions.get(sessionId);
    if (!session || session._disposed) return;
    try {
      session.pty.resize(cols, rows);
      session.terminal.resize(cols, rows);
    } catch (e) {
      console.warn(`[ShellManager] Resize failed for ${sessionId}:`, e.message);
    }
  }

  /**
   * Attach a new listener to an existing session (supports multiple clients).
   * Returns the session if found, null otherwise.
   */
  attach(sessionId, onOutput, onExit) {
    const session = this._sessions.get(sessionId);
    if (!session || session._disposed) return null;
    if (onOutput) session._outputs.add(onOutput);
    if (onExit) session._exits.add(onExit);
    console.log(`[ShellManager] Attached to session ${sessionId} (${session._outputs.size} clients)`);
    return session;
  }

  /**
   * Remove a listener from a session (when a client disconnects).
   */
  detach(sessionId, onOutput, onExit) {
    const session = this._sessions.get(sessionId);
    if (!session) return;
    if (onOutput) session._outputs.delete(onOutput);
    if (onExit) session._exits.delete(onExit);
  }

  /**
   * Get serialized terminal state for reconnect replay.
   * Returns escape sequences that reconstruct the full terminal state.
   */
  getSerializedState(sessionId) {
    const session = this._sessions.get(sessionId);
    if (!session) return '';
    try {
      return session.serializer.serialize();
    } catch (e) {
      console.warn(`[ShellManager] Serialize failed for ${sessionId}:`, e.message);
      return '';
    }
  }

  /**
   * Close a session and clean up resources.
   * @returns {boolean} true if session was found and closed
   */
  close(sessionId) {
    const sess = this._sessions.get(sessionId);
    if (!sess) return false;
    this._sessions.delete(sessionId);

    sess._disposed = true;
    try {
      sess.pty.kill();
    } catch (e) {
      // ignore
    }
    try {
      sess.terminal.dispose();
    } catch (e) {
      // ignore
    }
    console.log(`[ShellManager] Closed session ${sessionId}`);
    return true;
  }

  /**
   * Close all sessions.
   */
  closeAll() {
    for (const sessionId of [...this._sessions.keys()]) {
      this.close(sessionId);
    }
  }

  /**
   * List all sessions as plain objects.
   */
  listSessions() {
    const result = [];
    for (const s of this._sessions.values()) {
      if (!s._disposed) {
        result.push({
          id: s.sessionId,
          shell: s.shell,
          created_at: s.createdAt.toISOString(),
        });
      }
    }
    return result;
  }

  get sessions() {
    return this._sessions;
  }
}

module.exports = { ShellManager };
