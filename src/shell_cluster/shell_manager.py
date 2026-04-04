"""Manage multiple PTY shell sessions on the local machine."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
from typing import Awaitable, Callable

from shell_cluster.models import ShellSession

log = logging.getLogger(__name__)

# Callback types: async functions that will be called from the read loop
OnOutputCallback = Callable[[str, bytes], Awaitable[None]]
OnExitCallback = Callable[[str], Awaitable[None]]


class ShellManager:
    """Manages multiple PTY shell sessions."""

    def __init__(self, default_shell: str = ""):
        self._default_shell = default_shell or os.environ.get("SHELL", "/bin/sh")
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

        # Create PTY
        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Fork the shell process
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

        # Parent process
        os.close(slave_fd)
        # Keep master_fd BLOCKING - the read loop runs in a thread executor
        # so blocking reads won't block the event loop

        session = ShellSession(
            session_id=session_id,
            shell=os.path.basename(shell_cmd),
            pid=pid,
            master_fd=master_fd,
        )
        self._sessions[session_id] = session

        # Start reading output from the PTY
        if on_output:
            task = asyncio.create_task(
                self._read_loop(session_id, master_fd, on_output, on_exit)
            )
            self._readers[session_id] = task

        log.info("Created shell session %s (pid=%d, shell=%s)", session_id, pid, shell_cmd)
        return session

    async def _read_loop(
        self,
        session_id: str,
        master_fd: int,
        on_output: OnOutputCallback,
        on_exit: OnExitCallback | None,
    ) -> None:
        """Read from PTY in thread, dispatch output via async callbacks."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                # Blocking read in thread pool - fd is blocking mode
                data = await loop.run_in_executor(None, self._blocking_read, master_fd)
                if not data:
                    break
                await on_output(session_id, data)
        except (OSError, IOError):
            pass
        finally:
            if on_exit:
                await on_exit(session_id)

    @staticmethod
    def _blocking_read(fd: int) -> bytes:
        """Blocking read from file descriptor."""
        try:
            return os.read(fd, 4096)
        except OSError:
            return b""

    async def write(self, session_id: str, data: bytes) -> None:
        """Write data to a shell session's PTY."""
        session = self._sessions.get(session_id)
        if not session:
            return
        try:
            os.write(session.master_fd, data)
        except OSError:
            log.warning("Failed to write to session %s", session_id)

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a shell session's terminal."""
        session = self._sessions.get(session_id)
        if not session:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
            # Send SIGWINCH to the process group
            os.killpg(os.getpgid(session.pid), signal.SIGWINCH)
        except OSError:
            log.warning("Failed to resize session %s", session_id)

    async def close(self, session_id: str) -> None:
        """Close a shell session."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        # Cancel reader task
        reader = self._readers.pop(session_id, None)
        if reader:
            reader.cancel()

        # Close PTY
        try:
            os.close(session.master_fd)
        except OSError:
            pass

        # Kill the process
        try:
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
