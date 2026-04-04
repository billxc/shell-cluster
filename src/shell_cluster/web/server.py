"""HTTP + WebSocket proxy server for the web dashboard.

Serves static HTML and proxies WebSocket connections to remote peers,
avoiding browser CORS/mixed-content issues.
"""

from __future__ import annotations

import asyncio
import json
import logging
import webbrowser
from pathlib import Path

from websockets.asyncio.server import ServerConnection
import websockets

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


async def _handle_http(ws: ServerConnection) -> None:
    """Handle HTTP requests for static files (upgrade-less connections)."""
    pass  # websockets library handles upgrade; we use process_request


def _build_index_html(peers: list[dict]) -> str:
    """Inject peer config into the HTML template."""
    html_path = STATIC_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8")
    peers_json = json.dumps(peers)
    return html.replace("__PEERS_CONFIG__", peers_json)


class DashboardServer:
    """Serves the web dashboard and proxies WebSocket connections to peers."""

    def __init__(
        self,
        peers: list[dict],
        host: str = "127.0.0.1",
        port: int = 9000,
        no_open: bool = False,
    ):
        self._peers = peers  # [{"name": "x", "uri": "ws://..."}]
        self._host = host
        self._port = port
        self._no_open = no_open
        self._server = None

    async def start(self) -> None:
        """Start the dashboard server."""
        index_html = _build_index_html(self._peers)

        async def process_request(connection, request):
            """Serve static files for non-WebSocket requests."""
            # Let WebSocket upgrades pass through
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None

            path = request.path

            if path == "/" or path == "/index.html":
                response = connection.respond(200, index_html)
                response.headers["Content-Type"] = "text/html; charset=utf-8"
                return response

            # Serve other static files
            safe_path = path.lstrip("/")
            file_path = STATIC_DIR / safe_path
            # Prevent path traversal
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
            # The browser sends the first message with target peer URI
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                init = json.loads(raw)
                target_uri = init.get("target")
                if not target_uri:
                    await ws.close(1008, "Missing target URI")
                    return
            except Exception:
                await ws.close(1008, "Invalid init message")
                return

            log.info("Proxying to %s", target_uri)

            try:
                async with websockets.connect(target_uri) as peer_ws:
                    # Bidirectional proxy
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
                    await ws.send(json.dumps({"type": "error", "error": str(e)}))
                except websockets.ConnectionClosed:
                    pass

        self._server = await websockets.asyncio.server.serve(
            handle_ws,
            self._host,
            self._port,
            process_request=process_request,
        )

        url = f"http://{self._host}:{self._port}"
        log.info("Dashboard running at %s", url)

        if not self._no_open:
            webbrowser.open(url)

    async def run_forever(self) -> None:
        """Start and run until interrupted."""
        await self.start()
        stop = asyncio.Event()

        import signal
        import sys
        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)

        await stop.wait()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()


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
