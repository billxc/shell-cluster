"""WebSocket server that exposes shell sessions over the network."""

from __future__ import annotations

import asyncio
import logging

import websockets
from websockets.asyncio.server import ServerConnection

from shell_cluster.protocol import (
    Message,
    MsgType,
    decode_shell_data,
    make_error,
    make_peer_info,
    make_shell_closed,
    make_shell_created,
    make_shell_data,
    make_shell_list_response,
)
from shell_cluster.shell_manager import ShellManager

log = logging.getLogger(__name__)


class ShellServer:
    """WebSocket server that manages shell sessions for connected clients."""

    def __init__(self, shell_manager: ShellManager, node_name: str, port: int = 8765):
        self._shell_manager = shell_manager
        self._node_name = node_name
        self._port = port
        self._server: websockets.asyncio.server.Server | None = None
        self._clients: set[ServerConnection] = set()

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await websockets.asyncio.server.serve(
            self._handle_client,
            "0.0.0.0",
            self._port,
        )
        log.info("Shell server listening on port %d", self._port)

    async def stop(self) -> None:
        """Stop the server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self._shell_manager.close_all()

    async def _handle_client(self, ws: ServerConnection) -> None:
        """Handle a connected WebSocket client."""
        self._clients.add(ws)
        log.info("Client connected from %s", ws.remote_address)

        # Send peer info on connect
        info = make_peer_info(
            self._node_name,
            self._shell_manager.list_sessions(),
        )
        await ws.send(info.to_json())

        try:
            async for raw in ws:
                msg = Message.from_json(raw)
                await self._dispatch(ws, msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            log.info("Client disconnected")

    async def _dispatch(self, ws: ServerConnection, msg: Message) -> None:
        """Dispatch a received message to the appropriate handler."""
        handlers = {
            MsgType.SHELL_CREATE: self._handle_create,
            MsgType.SHELL_DATA: self._handle_data,
            MsgType.SHELL_RESIZE: self._handle_resize,
            MsgType.SHELL_CLOSE: self._handle_close,
            MsgType.SHELL_LIST: self._handle_list,
        }
        handler = handlers.get(msg.type)
        if handler:
            await handler(ws, msg)
        else:
            log.warning("Unknown message type: %s", msg.type)

    async def _handle_create(self, ws: ServerConnection, msg: Message) -> None:
        """Create a new shell session."""

        async def on_output(session_id: str, data: bytes) -> None:
            out_msg = make_shell_data(session_id, data)
            await self._send_to_client(ws, out_msg)

        async def on_exit(session_id: str) -> None:
            closed_msg = make_shell_closed(session_id)
            await self._send_to_client(ws, closed_msg)

        try:
            session = await self._shell_manager.create(
                session_id=msg.session_id,
                shell=msg.shell,
                cols=msg.cols or 80,
                rows=msg.rows or 24,
                on_output=on_output,
                on_exit=on_exit,
            )
            resp = make_shell_created(session.session_id, session.shell)
            await ws.send(resp.to_json())
        except Exception as e:
            log.error("Failed to create shell: %s", e)
            await ws.send(make_error(str(e), msg.session_id).to_json())

    async def _handle_data(self, ws: ServerConnection, msg: Message) -> None:
        """Forward input data to a shell session."""
        data = decode_shell_data(msg)
        await self._shell_manager.write(msg.session_id, data)

    async def _handle_resize(self, ws: ServerConnection, msg: Message) -> None:
        """Resize a shell session."""
        await self._shell_manager.resize(msg.session_id, msg.cols, msg.rows)

    async def _handle_close(self, ws: ServerConnection, msg: Message) -> None:
        """Close a shell session."""
        await self._shell_manager.close(msg.session_id)
        await ws.send(make_shell_closed(msg.session_id).to_json())

    async def _handle_list(self, ws: ServerConnection, msg: Message) -> None:
        """List all shell sessions."""
        sessions = self._shell_manager.list_sessions()
        resp = make_shell_list_response(sessions)
        await ws.send(resp.to_json())

    async def _send_to_client(self, ws: ServerConnection, msg: Message) -> None:
        """Send a message to a client, ignoring connection errors."""
        try:
            await ws.send(msg.to_json())
        except websockets.ConnectionClosed:
            pass
