#!/usr/bin/env python3
"""Standalone Shell Cluster Dashboard Server.

A pure static file server that serves the web UI.
The frontend talks directly to the daemon for API and WebSocket.

Usage:
  # Default: serve on port 9001
  python server.py

  # Custom port:
  python server.py --port 8080
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


def serve(args: argparse.Namespace) -> None:
    server = HTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    log.info("Dashboard running at %s", url)

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Shell Cluster Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9001, help="HTTP port (default: 9001)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if not args.verbose:
        logging.getLogger("websockets").setLevel(logging.WARNING)

    serve(args)


if __name__ == "__main__":
    main()
