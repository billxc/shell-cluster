"""WebSocket client for connecting to remote shell sessions.

Cross-platform: uses termios/tty on Unix, msvcrt/ctypes on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import websockets

from shell_cluster.protocol import (
    Message,
    MsgType,
    decode_shell_data,
    make_shell_close,
    make_shell_create,
    make_shell_data,
    make_shell_resize,
)

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


class ShellClient:
    """Connect to a remote shell server and bridge to local terminal."""

    def __init__(self, uri: str):
        self._uri = uri
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._session_id: str = ""
        self._done = False

    async def connect_and_run(self, shell: str = "") -> None:
        """Connect to a remote peer, create a shell, and enter raw terminal mode."""
        ws_uri = self._uri.rstrip("/")
        if ws_uri.startswith("https://"):
            ws_uri = "wss://" + ws_uri[8:]
        elif not ws_uri.startswith("ws://") and not ws_uri.startswith("wss://"):
            ws_uri = "ws://" + ws_uri

        async with websockets.connect(ws_uri) as ws:
            self._ws = ws
            self._done = False

            # Read peer info
            raw = await ws.recv()
            peer_info = Message.from_json(raw)
            if peer_info.type == MsgType.PEER_INFO:
                log.info("Connected to peer: %s", peer_info.name)

            # Get terminal size
            try:
                size = os.get_terminal_size()
                cols, rows = size.columns, size.lines
            except OSError:
                cols, rows = 80, 24

            # Create a shell session
            create_msg = make_shell_create(shell=shell, cols=cols, rows=rows)
            self._session_id = create_msg.session_id
            await ws.send(create_msg.to_json())

            # Wait for shell.created
            raw = await ws.recv()
            created = Message.from_json(raw)
            if created.type == MsgType.ERROR:
                print(f"Error: {created.error}", file=sys.stderr)
                return
            if created.type == MsgType.SHELL_CREATED:
                log.info("Shell session created: %s (%s)", created.session_id, created.shell)

            # Enter raw terminal mode
            if IS_WINDOWS:
                await self._raw_session_windows(ws)
            else:
                await self._raw_session_unix(ws)

    # ── Unix raw terminal ────────────────────────────────────────────

    async def _raw_session_unix(
        self, ws: websockets.asyncio.client.ClientConnection,
    ) -> None:
        import signal
        import termios
        import tty

        old_settings = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())

            # Handle terminal resize (SIGWINCH)
            loop = asyncio.get_event_loop()

            def on_resize() -> None:
                try:
                    size = os.get_terminal_size()
                    msg = make_shell_resize(self._session_id, size.columns, size.lines)
                    asyncio.ensure_future(ws.send(msg.to_json()))
                except (OSError, websockets.ConnectionClosed):
                    pass

            loop.add_signal_handler(signal.SIGWINCH, on_resize)

            # Use tasks so we can cancel stdin reader when ws reader finishes
            stdin_task = asyncio.create_task(self._read_stdin_unix(ws))
            ws_task = asyncio.create_task(self._read_ws(ws))

            # Wait for either to finish (ws_task finishes when shell exits)
            done, pending = await asyncio.wait(
                [stdin_task, ws_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the other task
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            loop.remove_signal_handler(signal.SIGWINCH)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)

    async def _read_stdin_unix(
        self, ws: websockets.asyncio.client.ClientConnection,
    ) -> None:
        loop = asyncio.get_event_loop()
        escape_state = 0

        while not self._done:
            try:
                data = await asyncio.wait_for(
                    loop.run_in_executor(None, self._blocking_read_unix),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            if not data:
                break

            # Detect ~. escape sequence
            for byte in data:
                if escape_state == 0 and byte in (ord("\r"), ord("\n")):
                    escape_state = 1
                elif escape_state == 1 and byte == ord("~"):
                    escape_state = 2
                elif escape_state == 2 and byte == ord("."):
                    close_msg = make_shell_close(self._session_id)
                    try:
                        await ws.send(close_msg.to_json())
                    except websockets.ConnectionClosed:
                        pass
                    return
                else:
                    escape_state = 0

            try:
                msg = make_shell_data(self._session_id, data)
                await ws.send(msg.to_json())
            except websockets.ConnectionClosed:
                return

    @staticmethod
    def _blocking_read_unix() -> bytes:
        try:
            return os.read(sys.stdin.fileno(), 4096)
        except OSError:
            return b""

    # ── Windows raw terminal ─────────────────────────────────────────

    async def _raw_session_windows(
        self, ws: websockets.asyncio.client.ClientConnection,
    ) -> None:
        old_mode = self._enable_win_vt_input()
        try:
            stdin_task = asyncio.create_task(self._read_stdin_windows(ws))
            ws_task = asyncio.create_task(self._read_ws(ws))

            done, pending = await asyncio.wait(
                [stdin_task, ws_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            self._restore_win_console_mode(old_mode)

    @staticmethod
    def _enable_win_vt_input() -> int | None:
        """Enable raw virtual terminal input on Windows. Returns old mode."""
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            STD_INPUT_HANDLE = -10
            ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            old_mode = wintypes.DWORD()
            kernel32.GetConsoleMode(handle, ctypes.byref(old_mode))

            new_mode = ENABLE_VIRTUAL_TERMINAL_INPUT
            kernel32.SetConsoleMode(handle, new_mode)
            return old_mode.value
        except Exception:
            return None

    @staticmethod
    def _restore_win_console_mode(old_mode: int | None) -> None:
        if old_mode is None:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)
            kernel32.SetConsoleMode(handle, old_mode)
        except Exception:
            pass

    async def _read_stdin_windows(
        self, ws: websockets.asyncio.client.ClientConnection,
    ) -> None:
        import msvcrt

        loop = asyncio.get_event_loop()
        escape_state = 0

        def _read_win() -> bytes:
            buf = b""
            while msvcrt.kbhit():
                ch = msvcrt.getch()
                buf += ch
            if not buf:
                import time
                time.sleep(0.05)
            return buf

        while not self._done:
            data = await loop.run_in_executor(None, _read_win)
            if not data:
                continue

            for byte in data:
                if escape_state == 0 and byte in (ord("\r"), ord("\n")):
                    escape_state = 1
                elif escape_state == 1 and byte == ord("~"):
                    escape_state = 2
                elif escape_state == 2 and byte == ord("."):
                    close_msg = make_shell_close(self._session_id)
                    try:
                        await ws.send(close_msg.to_json())
                    except websockets.ConnectionClosed:
                        pass
                    return
                else:
                    escape_state = 0

            try:
                msg = make_shell_data(self._session_id, data)
                await ws.send(msg.to_json())
            except websockets.ConnectionClosed:
                return

    # ── WebSocket reader (shared) ────────────────────────────────────

    async def _read_ws(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Read from WebSocket and write to stdout."""
        try:
            async for raw in ws:
                msg = Message.from_json(raw)
                if msg.type == MsgType.SHELL_DATA:
                    data = decode_shell_data(msg)
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                elif msg.type == MsgType.SHELL_CLOSED:
                    self._done = True
                    break
                elif msg.type == MsgType.ERROR:
                    print(f"\r\nError: {msg.error}\r\n", file=sys.stderr)
                    self._done = True
                    break
        except websockets.ConnectionClosed:
            pass
        self._done = True
