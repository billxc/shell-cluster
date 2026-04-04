"""WebSocket client for connecting to remote shell sessions."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import termios
import tty

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


class ShellClient:
    """Connect to a remote shell server and bridge to local terminal."""

    def __init__(self, uri: str):
        self._uri = uri
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._session_id: str = ""

    async def connect_and_run(self, shell: str = "") -> None:
        """Connect to a remote peer, create a shell, and enter raw terminal mode."""
        ws_uri = self._uri.rstrip("/")
        if ws_uri.startswith("https://"):
            ws_uri = "wss://" + ws_uri[8:]
        elif not ws_uri.startswith("ws://") and not ws_uri.startswith("wss://"):
            ws_uri = "ws://" + ws_uri

        async with websockets.connect(ws_uri) as ws:
            self._ws = ws

            # Read peer info
            raw = await ws.recv()
            peer_info = Message.from_json(raw)
            if peer_info.type == MsgType.PEER_INFO:
                log.info("Connected to peer: %s", peer_info.name)

            # Get terminal size
            rows, cols = os.get_terminal_size()

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
            await self._raw_session(ws)

    async def _raw_session(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Run raw terminal session bridging local stdin/stdout to WebSocket."""
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
            await asyncio.gather(
                self._read_stdin(ws),
                self._read_ws(ws),
                return_exceptions=True,
            )
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)

    async def _read_stdin(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Read from stdin and send to WebSocket."""
        loop = asyncio.get_event_loop()
        escape_state = 0  # 0=normal, 1=after_newline, 2=after_tilde

        while True:
            data = await loop.run_in_executor(None, self._blocking_stdin_read)
            if not data:
                break

            # Detect ~. escape sequence (disconnect)
            for byte in data:
                if escape_state == 0 and byte in (ord("\r"), ord("\n")):
                    escape_state = 1
                elif escape_state == 1 and byte == ord("~"):
                    escape_state = 2
                elif escape_state == 2 and byte == ord("."):
                    # Disconnect
                    close_msg = make_shell_close(self._session_id)
                    await ws.send(close_msg.to_json())
                    return
                else:
                    escape_state = 0

            msg = make_shell_data(self._session_id, data)
            await ws.send(msg.to_json())

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
                    break
                elif msg.type == MsgType.ERROR:
                    print(f"\r\nError: {msg.error}\r\n", file=sys.stderr)
                    break
        except websockets.ConnectionClosed:
            pass

    @staticmethod
    def _blocking_stdin_read() -> bytes:
        """Blocking read from stdin."""
        try:
            return os.read(sys.stdin.fileno(), 4096)
        except OSError:
            return b""
