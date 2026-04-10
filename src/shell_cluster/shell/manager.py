"""Manage multiple PTY shell sessions on the local machine.

Cross-platform: uses pty/fork on Unix, winpty on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Awaitable, Callable

from shell_cluster.models import ShellSession

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Callback types
OnOutputCallback = Callable[[str, bytes], Awaitable[None]]
OnExitCallback = Callable[[str], Awaitable[None]]


class ShellManager:
    """Manages multiple PTY shell sessions (cross-platform)."""

    def __init__(self, default_shell: str = ""):
        if default_shell:
            self._default_shell = default_shell
        elif IS_WINDOWS:
            self._default_shell = os.environ.get("COMSPEC", "cmd.exe")
        else:
            self._default_shell = os.environ.get("SHELL", "/bin/sh")

        self._sessions: dict[str, ShellSession] = {}
        self._readers: dict[str, asyncio.Task] = {}

    @property
    def sessions(self) -> dict[str, ShellSession]:
        return self._sessions

    async def create(
        self,
        session_id: str,
        shell: str = "",
        cols: int = 80,
        rows: int = 24,
        on_output: OnOutputCallback | None = None,
        on_exit: OnExitCallback | None = None,
    ) -> ShellSession:
        """Create a new PTY shell session."""
        import shutil

        shell_cmd = shell or self._default_shell
        if not shutil.which(shell_cmd):
            raise FileNotFoundError(f"Shell binary not found: {shell_cmd}")

        if IS_WINDOWS:
            session = await self._create_windows(session_id, shell_cmd, cols, rows)
        else:
            session = await self._create_unix(session_id, shell_cmd, cols, rows)

        self._sessions[session_id] = session

        if on_output:
            task = asyncio.create_task(
                self._read_loop(session, on_output, on_exit)
            )
            self._readers[session_id] = task

        log.info(
            "Created shell session %s (pid=%d, shell=%s)",
            session_id, session.pid, shell_cmd,
        )
        return session

    # ── Unix implementation ──────────────────────────────────────────

    async def _create_unix(
        self, session_id: str, shell_cmd: str, cols: int, rows: int,
    ) -> ShellSession:
        import fcntl
        import pty
        import struct
        import termios

        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env.setdefault("LANG", "en_US.UTF-8")
        env.setdefault("LC_CTYPE", "en_US.UTF-8")

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            # Close all inherited FDs (server sockets, other PTY masters, pipes)
            # to prevent the child from holding them open after exec.
            # os.sysconf("SC_OPEN_MAX") can overflow on Python 3.14/macOS,
            # so cap it to a reasonable value.
            try:
                max_fd = os.sysconf("SC_OPEN_MAX")
            except (ValueError, OSError):
                max_fd = 1024
            if max_fd > 65536:
                max_fd = 65536
            os.closerange(3, max_fd)
            os.execvpe(shell_cmd, [shell_cmd], env)
            os._exit(1)

        # Parent
        os.close(slave_fd)
        # Keep master_fd BLOCKING - read loop runs in thread executor

        return ShellSession(
            session_id=session_id,
            shell=os.path.basename(shell_cmd),
            pid=pid,
            _handle=master_fd,
        )

    # ── Windows implementation ───────────────────────────────────────

    async def _create_windows(
        self, session_id: str, shell_cmd: str, cols: int, rows: int,
    ) -> ShellSession:
        from winpty import PtyProcess

        proc = PtyProcess.spawn(
            shell_cmd,
            dimensions=(rows, cols),
        )

        return ShellSession(
            session_id=session_id,
            shell=os.path.basename(shell_cmd),
            pid=proc.pid,
            _handle=proc,
        )

    # ── Read loop (cross-platform) ──────────────────────────────────

    async def _read_loop(
        self,
        session: ShellSession,
        on_output: OnOutputCallback,
        on_exit: OnExitCallback | None,
    ) -> None:
        """Read from PTY in thread, dispatch output via async callbacks."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await loop.run_in_executor(
                    None, self._blocking_read, session
                )
                if not data:
                    break
                session.append_output(data)
                await on_output(session.session_id, data)
        except (OSError, IOError):
            pass
        finally:
            # Reap child process to avoid zombies
            self._reap_child(session)
            if on_exit:
                await on_exit(session.session_id)

    @staticmethod
    def _blocking_read(session: ShellSession) -> bytes:
        """Blocking read from PTY (cross-platform)."""
        try:
            if IS_WINDOWS:
                # winpty PtyProcess
                proc = session._handle
                return proc.read(4096).encode("utf-8", errors="replace")
            else:
                # Unix fd
                return os.read(session._handle, 4096)
        except (OSError, EOFError):
            return b""

    # ── Write (cross-platform) ──────────────────────────────────────

    async def write(self, session_id: str, data: bytes) -> bool:
        """Write data to a shell session's PTY. Returns False if session not found."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        try:
            if IS_WINDOWS:
                session._handle.write(data.decode("utf-8", errors="replace"))
            else:
                os.write(session._handle, data)
        except OSError as e:
            log.warning("Failed to write to session %s: %s (handle=%r, pid=%d)",
                        session_id, e, session._handle, session.pid)
            return False
        return True

    def attach(
        self,
        session_id: str,
        on_output: OnOutputCallback,
        on_exit: OnExitCallback | None,
    ) -> ShellSession | None:
        """Re-attach callbacks to an existing session (for reconnect).

        NOTE: Does NOT start the read loop. Caller must call
        start_reader() after sending scrollback to avoid a race
        where new PTY output arrives before the scrollback replay.
        """
        session = self._sessions.get(session_id)
        if not session:
            return None

        # Cancel old reader — new one started explicitly via start_reader()
        old_reader = self._readers.pop(session_id, None)
        if old_reader:
            old_reader.cancel()

        # Stash callbacks for start_reader()
        session._pending_on_output = on_output
        session._pending_on_exit = on_exit

        log.info("Re-attached to session %s (reader pending)", session_id)
        return session

    def start_reader(self, session_id: str) -> None:
        """Start the read loop for a session after scrollback has been sent."""
        session = self._sessions.get(session_id)
        if not session:
            return
        on_output = getattr(session, '_pending_on_output', None)
        on_exit = getattr(session, '_pending_on_exit', None)
        if not on_output:
            return
        task = asyncio.create_task(
            self._read_loop(session, on_output, on_exit)
        )
        self._readers[session_id] = task
        # Clean up stashed callbacks
        session._pending_on_output = None
        session._pending_on_exit = None

    # ── Resize (cross-platform) ─────────────────────────────────────

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a shell session's terminal."""
        session = self._sessions.get(session_id)
        if not session:
            return
        try:
            if IS_WINDOWS:
                session._handle.setwinsize(rows, cols)
            else:
                import fcntl
                import signal
                import struct
                import termios

                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(session._handle, termios.TIOCSWINSZ, winsize)
                os.killpg(os.getpgid(session.pid), signal.SIGWINCH)
        except OSError:
            log.warning("Failed to resize session %s", session_id)

    # ── Close (cross-platform) ──────────────────────────────────────

    def _reap_child(self, session: ShellSession) -> None:
        """Reap child process to prevent zombies (Unix only)."""
        if IS_WINDOWS or session.pid <= 0:
            return
        try:
            pid, _ = os.waitpid(session.pid, os.WNOHANG)
            if pid == 0:
                # Child hasn't exited yet, try once more after a short wait
                import time
                time.sleep(0.1)
                os.waitpid(session.pid, os.WNOHANG)
        except ChildProcessError:
            pass

    async def close(self, session_id: str) -> bool:
        """Close a shell session. Returns False if session not found."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return False

        # Cancel reader task
        reader = self._readers.pop(session_id, None)
        if reader:
            reader.cancel()

        try:
            if IS_WINDOWS:
                proc = session._handle
                if proc.isalive():
                    proc.terminate(force=True)
            else:
                try:
                    os.close(session._handle)
                except OSError:
                    pass
                import signal
                try:
                    os.kill(session.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # Wait briefly in executor so child has time to exit
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._reap_child, session)
        except OSError:
            pass

        log.info("Closed shell session %s", session_id)
        return True

    async def close_all(self) -> None:
        """Close all sessions."""
        for sid in list(self._sessions):
            await self.close(sid)
        # Force cancel any remaining reader tasks stuck in run_in_executor
        for sid, reader in list(self._readers.items()):
            if not reader.done():
                reader.cancel()
                try:
                    await asyncio.wait_for(reader, timeout=0.5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        self._readers.clear()

    def list_sessions(self) -> list[dict]:
        """Return session info as dicts for protocol messages."""
        return [
            {
                "id": s.session_id,
                "shell": s.shell,
                "created_at": s.created_at.isoformat(),
            }
            for s in self._sessions.values()
        ]
