"""Shell Cluster Dashboard v2 Server.

A static file server that serves the new web UI.
The frontend talks directly to the daemon (port 9000) for API and WebSocket.

Can be run standalone or spawned by the shellcluster daemon.

Usage:
  # As installed command:
  shell-dashboard

  # As module:
  python -m shell_cluster.dashboard_v2

  # Custom port:
  shell-dashboard --port 8080
"""

from __future__ import annotations

import argparse
import logging
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves static files from STATIC_DIR."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


def serve(host: str, port: int, no_open: bool = False) -> None:
    server = HTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"
    log.info("Dashboard v2 running at %s", url)

    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shell Cluster Dashboard v2")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9001, help="HTTP port (default: 9001)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    serve(args.host, args.port, args.no_open)


if __name__ == "__main__":
    main()
