#!/usr/bin/env python3
"""Standalone Shell Cluster Dashboard Server.

Thin wrapper around shell_cluster.dashboard_v2.
Can also be run directly: python server.py

Usage:
  # Default: serve on port 9001
  python server.py

  # Custom port:
  python server.py --port 8080
"""

from shell_cluster.dashboard_v2.server import main

if __name__ == "__main__":
    main()
