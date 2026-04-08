"""HTTP + WebSocket proxy server for the web dashboard.

Serves static HTML, provides /api/peers endpoint,
and proxies WebSocket connections to peer shell servers via localhost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import webbrowser
from pathlib import Path
from typing import Awaitable, Callable

from websockets.asyncio.server import ServerConnection
import websockets

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class DashboardServer:
    """Serves the web dashboard and proxies WebSocket connections to peers."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        no_open: bool = False,
        get_peers: Callable[[], list[dict]] | None = None,
        refresh_peers: Callable[[], Awaitable[None]] | None = None,
    ):
        self._host = host
        self._port = port
        self._no_open = no_open
        self._get_peers = get_peers or (lambda: [])
        self._refresh_peers = refresh_peers
        self._server = None
        self._index_html: str | None = None

    def _build_index_html(self) -> str:
        """Build index.html (peers loaded via /api/peers, not injected)."""
        if self._index_html is None:
            html_path = STATIC_DIR / "index.html"
            self._index_html = html_path.read_text(encoding="utf-8")
        return self._index_html

    async def start(self) -> None:
        """Start the dashboard server."""
        index_html = self._build_index_html()
        get_peers = self._get_peers
        refresh_peers = self._refresh_peers

        def _add_cors(response, request):
            """Allow CORS from localhost origins."""
            origin = request.headers.get("Origin", "")
            if origin and ("://localhost" in origin or "://127.0.0.1" in origin):
                response.headers["Access-Control-Allow-Origin"] = origin

        async def process_request(connection, request):
            """Serve static files and API for non-WebSocket requests."""
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None

            path = request.path

            # API: return current peer list
            if path == "/api/peers":
                peers_json = json.dumps(get_peers())
                response = connection.respond(200, peers_json)
                response.headers["Content-Type"] = "application/json"
                _add_cors(response, request)
                return response

            # API: trigger discovery refresh
            if path == "/api/refresh-peers":
                if refresh_peers:
                    try:
                        await refresh_peers()
                        body = json.dumps({"ok": True})
                    except Exception as e:
                        log.warning("Refresh peers failed: %s", e)
                        body = json.dumps({"ok": False, "error": "refresh failed"})
                else:
                    body = json.dumps({"ok": False, "error": "discovery not available"})
                response = connection.respond(200, body)
                response.headers["Content-Type"] = "application/json"
                _add_cors(response, request)
                return response

            if path == "/" or path == "/index.html":
                response = connection.respond(200, index_html)
                response.headers["Content-Type"] = "text/html; charset=utf-8"
                return response

            # Serve other static files
            safe_path = path.lstrip("/")
            file_path = STATIC_DIR / safe_path
            try:
                file_path = file_path.resolve()
                if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                    return connection.respond(403, "Forbidden")
            except (ValueError, OSError):
                return connection.respond(403, "Forbidden")

            if file_path.is_file():
                content = file_path.read_text(encoding="utf-8", errors="replace")
                content_type = _guess_content_type(file_path.suffix)
                response = connection.respond(200, content)
                response.headers["Content-Type"] = content_type
                return response

            return connection.respond(404, "Not Found")

        async def handle_ws(ws: ServerConnection) -> None:
            """Proxy WebSocket: browser <-> peer shell server."""
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                init = json.loads(raw)
                target_uri = init.get("target")
                if not target_uri:
                    await ws.close(1008, "Missing target URI")
                    return
                valid_uris = {p["uri"] for p in get_peers()}
                if target_uri not in valid_uris:
                    await ws.close(1008, "Unknown target")
                    return
            except Exception:
                await ws.close(1008, "Invalid init message")
                return

            log.info("Proxying to %s", target_uri)

            try:
                async with websockets.connect(target_uri) as peer_ws:
                    async def browser_to_peer():
                        try:
                            async for msg in ws:
                                await peer_ws.send(msg)
                        except websockets.ConnectionClosed:
                            pass

                    async def peer_to_browser():
                        try:
                            async for msg in peer_ws:
                                await ws.send(msg)
                        except websockets.ConnectionClosed:
                            pass

                    tasks = [
                        asyncio.create_task(browser_to_peer()),
                        asyncio.create_task(peer_to_browser()),
                    ]
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
            except Exception as e:
                log.error("Proxy connection failed: %s", e)
                try:
                    await ws.send(json.dumps({"type": "error", "error": "Connection to peer failed"}))
                except websockets.ConnectionClosed:
                    pass

        self._server = await websockets.asyncio.server.serve(
            handle_ws,
            self._host,
            self._port,
            process_request=process_request,
            max_size=1_048_576,
        )

        url = f"http://{self._host}:{self._port}"
        log.info("Dashboard running at %s", url)

        if not self._no_open:
            webbrowser.open(url)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("Dashboard server wait_closed timed out")


def _guess_content_type(suffix: str) -> str:
    types = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }
    return types.get(suffix, "application/octet-stream")
