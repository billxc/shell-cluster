"""Pre-flight checks for tunnel backends (CLI startup)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from rich.console import Console

console = Console()


def check_devtunnel() -> bool:
    """Check that devtunnel CLI is installed and logged in. Returns True if ok."""
    if not shutil.which("devtunnel"):
        console.print("[red]devtunnel CLI is not installed.[/red]")
        console.print("Install it: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started")
        return False

    try:
        result = subprocess.run(
            ["devtunnel", "list", "--limit", "1"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode().strip()
            if "login" in stderr.lower() or "sign in" in stderr.lower() or "unauthorized" in stderr.lower():
                console.print("[red]devtunnel is not logged in.[/red]")
                console.print("Run: [bold]devtunnel user login[/bold]")
                return False
            # Other errors — might still work, let it try
    except subprocess.TimeoutExpired:
        console.print("[yellow]devtunnel login check timed out, proceeding anyway.[/yellow]")
    except Exception:
        pass

    return True


def _start_tailscaled(socket: str) -> bool:
    """Start tailscaled in userspace-networking mode. Returns True if started ok."""
    if not shutil.which("tailscaled"):
        console.print("[red]tailscaled is not installed.[/red]")
        console.print("Install it: [bold]brew install tailscale[/bold]")
        return False

    console.print("[yellow]Starting tailscaled...[/yellow]")
    args = ["tailscaled", "--tun=userspace-networking"]
    if socket:
        args += ["--socket", socket]

    # Start as a detached background process
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for socket to appear
    for _ in range(10):
        time.sleep(0.5)
        if socket and os.path.exists(socket):
            console.print("[green]tailscaled started.[/green]")
            return True
    console.print("[green]tailscaled started.[/green]")
    return True


def check_tailscale() -> bool:
    """Check that tailscale CLI is installed and connected. Returns True if ok."""
    if not shutil.which("tailscale"):
        console.print("[red]tailscale CLI is not installed.[/red]")
        console.print("Install it: [bold]brew install tailscale[/bold]")
        return False

    from shell_cluster.tunnel.tailscale import _default_socket
    socket = _default_socket()

    cmd = ["tailscale"]
    if socket:
        cmd += ["--socket", socket]
    cmd += ["status", "--json"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            # tailscaled not running — try to start it
            if not _start_tailscaled(socket):
                return False
            # Re-check after starting
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode != 0:
                console.print("[red]tailscaled started but not responding.[/red]")
                return False
        status = json.loads(result.stdout)
        state = status.get("BackendState", "")
        if state != "Running":
            console.print(f"[yellow]tailscale is not logged in (state: {state}).[/yellow]")
            console.print("Run: [bold]tailscale --socket '{socket}' up[/bold]" if socket else "Run: [bold]tailscale up[/bold]")
            return False
    except subprocess.TimeoutExpired:
        console.print("[yellow]tailscale status check timed out, proceeding anyway.[/yellow]")
    except Exception:
        pass

    return True
