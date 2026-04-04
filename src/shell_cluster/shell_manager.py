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
        shell_cmd = shell or self._default_shell

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
                await on_output(session.session_id, data)
        except (OSError, IOError):
            pass
        finally:
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

    async def write(self, session_id: str, data: bytes) -> None:
        """Write data to a shell session's PTY."""
        session = self._sessions.get(session_id)
        if not session:
            return
        try:
            if IS_WINDOWS:
                session._handle.write(data.decode("utf-8", errors="replace"))
            else:
                os.write(session._handle, data)
        except OSError:
            log.warning("Failed to write to session %s", session_id)

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

    async def close(self, session_id: str) -> None:
        """Close a shell session."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

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
                os.close(session._handle)
                import signal
                os.kill(session.pid, signal.SIGTERM)
        except OSError:
            pass

        log.info("Closed shell session %s", session_id)

    async def close_all(self) -> None:
        """Close all sessions."""
        for sid in list(self._sessions):
            await self.close(sid)

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
