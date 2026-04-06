"""WebSocket server that exposes shell sessions over the network."""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from websockets.asyncio.server import ServerConnection

from shell_cluster.protocol import (
    Message,
    MsgType,
    decode_shell_data,
    make_error,
    make_peer_info,
    make_shell_attached,
    make_shell_closed,
    make_shell_created,
    make_shell_data,
    make_shell_list_response,
)
from shell_cluster.shell.manager import ShellManager

log = logging.getLogger(__name__)


class ShellServer:
    """WebSocket server that manages shell sessions for connected clients."""

    def __init__(self, shell_manager: ShellManager, node_name: str, port: int = 8765, bind_host: str = "127.0.0.1"):
        self._shell_manager = shell_manager
        self._node_name = node_name
        self._port = port
        self._bind_host = bind_host
        self._server: websockets.asyncio.server.Server | None = None
        self._clients: set[ServerConnection] = set()
        # Track which sessions belong to which client
        self._client_sessions: dict[ServerConnection, set[str]] = {}

    async def start(self) -> None:
        """Start the WebSocket server."""
        shell_manager = self._shell_manager

        async def process_request(connection, request):
            """Handle HTTP requests (non-WebSocket)."""
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None

            if request.path == "/sessions":
                if request.method == "OPTIONS":
                    response = connection.respond(204, "")
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
                    return response
                body = json.dumps(shell_manager.list_sessions())
                response = connection.respond(200, body)
                response.headers["Content-Type"] = "application/json"
                response.headers["Access-Control-Allow-Origin"] = "*"
                return response

            return connection.respond(404, "Not Found")

        self._server = await websockets.asyncio.server.serve(
            self._handle_client,
            self._bind_host,
            self._port,
            process_request=process_request,
            max_size=1_048_576,
        )
        # Update port in case 0 was used (OS-assigned)
        for sock in self._server.sockets:
            addr = sock.getsockname()
            if addr[1] != 0:
                self._port = addr[1]
                break
        log.info("Shell server listening on port %d", self._port)

    @property
    def port(self) -> int:
        return self._port

    async def stop(self) -> None:
        """Stop the server and clean up."""
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("Shell server wait_closed timed out")
        await self._shell_manager.close_all()

    async def _handle_client(self, ws: ServerConnection) -> None:
        """Handle a connected WebSocket client."""
        self._clients.add(ws)
        self._client_sessions[ws] = set()
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
            # Don't auto-close sessions on disconnect — they persist for reconnect
            self._client_sessions.pop(ws, None)
            self._clients.discard(ws)
            log.info("Client disconnected")

    async def _dispatch(self, ws: ServerConnection, msg: Message) -> None:
        """Dispatch a received message to the appropriate handler."""
        handlers = {
            MsgType.SHELL_CREATE: self._handle_create,
            MsgType.SHELL_ATTACH: self._handle_attach,
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
        if msg.session_id in self._shell_manager.sessions:
            await ws.send(make_error(f"Session {msg.session_id} already exists", msg.session_id).to_json())
            return

        async def on_output(session_id: str, data: bytes) -> None:
            out_msg = make_shell_data(session_id, data)
            await self._send_to_client(ws, out_msg)

        async def on_exit(session_id: str) -> None:
            closed_msg = make_shell_closed(session_id)
            await self._send_to_client(ws, closed_msg)

        try:
            session = await self._shell_manager.create(
                session_id=msg.session_id,
                shell="",
                cols=msg.cols or 80,
                rows=msg.rows or 24,
                on_output=on_output,
                on_exit=on_exit,
            )
            # Track session ownership
            if ws in self._client_sessions:
                self._client_sessions[ws].add(session.session_id)
            resp = make_shell_created(session.session_id, session.shell)
            await ws.send(resp.to_json())
        except Exception as e:
            log.error("Failed to create shell: %s", e)
            await ws.send(make_error(str(e), msg.session_id).to_json())

    async def _handle_data(self, ws: ServerConnection, msg: Message) -> None:
        """Forward input data to a shell session."""
        data = decode_shell_data(msg)
        if not await self._shell_manager.write(msg.session_id, data):
            await self._send_to_client(ws, make_shell_closed(msg.session_id))

    async def _handle_attach(self, ws: ServerConnection, msg: Message) -> None:
        """Re-attach to an existing shell session."""

        async def on_output(session_id: str, data: bytes) -> None:
            out_msg = make_shell_data(session_id, data)
            await self._send_to_client(ws, out_msg)

        async def on_exit(session_id: str) -> None:
            closed_msg = make_shell_closed(session_id)
            await self._send_to_client(ws, closed_msg)

        session = self._shell_manager.attach(msg.session_id, on_output, on_exit)
        if session:
            if ws in self._client_sessions:
                self._client_sessions[ws].add(session.session_id)
            if msg.cols and msg.rows:
                await self._shell_manager.resize(msg.session_id, msg.cols, msg.rows)
            resp = make_shell_attached(session.session_id, session.shell)
            await ws.send(resp.to_json())
        else:
            await ws.send(make_error(f"Session {msg.session_id} not found", msg.session_id).to_json())

    async def _handle_resize(self, ws: ServerConnection, msg: Message) -> None:
        """Resize a shell session."""
        await self._shell_manager.resize(msg.session_id, msg.cols, msg.rows)

    async def _handle_close(self, ws: ServerConnection, msg: Message) -> None:
        """Close a shell session."""
        closed = await self._shell_manager.close(msg.session_id)
        if ws in self._client_sessions:
            self._client_sessions[ws].discard(msg.session_id)
        if closed:
            await ws.send(make_shell_closed(msg.session_id).to_json())
        else:
            await ws.send(make_error(f"Session {msg.session_id} not found", msg.session_id).to_json())

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
