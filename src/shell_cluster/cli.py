"""CLI entry point for shell-cluster."""

from __future__ import annotations

import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table

from shell_cluster.config import Config, load_config, save_config

console = Console()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # websockets logs are too noisy for normal use:
    # INFO: "connection rejected" on every HTTP request
    # ERROR: "opening handshake failed" when connections drop (normal during reconnect)
    if not verbose:
        logging.getLogger("websockets").setLevel(logging.WARNING)


def _version_callback(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    from shell_cluster import get_version_string
    click.echo(f"shellcluster {get_version_string()}")
    ctx.exit()


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.option("--version", is_flag=True, callback=_version_callback, expose_value=False,
              is_eager=True, help="Show version and git hash")
def main(verbose: bool) -> None:
    """Shell Cluster - Remote access to all your shells via tunnels."""
    setup_logging(verbose)


@main.command()
@click.option("--name", prompt="Node name", default=None, help="Name for this machine")
@click.option("--label", default="shellcluster", help="Tunnel label for discovery")
@click.option("--backend", default="devtunnel", help="Tunnel backend (devtunnel)")
def register(name: str, label: str, backend: str) -> None:
    """Register this machine to the cluster."""
    config = load_config()
    if name:
        config.node.name = name
    config.node.label = label
    config.tunnel.backend = backend
    save_config(config)
    console.print(f"[green]Registered node '{config.node.name}'[/green]")
    console.print(f"  Label: {config.node.label}")
    console.print(f"  Backend: {config.tunnel.backend}")
    console.print(f"\nRun [bold]shellcluster start[/bold] to start the daemon.")


@main.command()
def unregister() -> None:
    """Unregister this machine: delete tunnel and remove config."""
    from shell_cluster.config import CONFIG_FILE
    from shell_cluster.tunnel.base import get_tunnel_backend, make_tunnel_id

    config = load_config()
    tunnel_id = make_tunnel_id(config.node.name)

    # Delete tunnel
    async def _delete():
        backend = get_tunnel_backend(config.tunnel.backend)
        console.print(f"Deleting tunnel [bold]{tunnel_id}[/bold]...")
        await backend.delete(tunnel_id)

    try:
        asyncio.run(_delete())
        console.print("[green]Tunnel deleted.[/green]")
    except Exception as e:
        console.print(f"[dim]Tunnel deletion skipped: {e}[/dim]")

    # Remove config file
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
        console.print(f"[green]Config removed: {CONFIG_FILE}[/green]")

    console.print("Done. Node unregistered.")


def _check_devtunnel() -> bool:
    """Check that devtunnel CLI is installed and logged in. Returns True if ok."""
    import shutil
    import subprocess

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


def _ensure_registered() -> Config:
    """Ensure device is registered. Prompt for node name if not."""
    from shell_cluster.config import CONFIG_FILE

    if CONFIG_FILE.exists():
        return load_config()

    console.print("[yellow]No config found. Let's register this node.[/yellow]")
    import socket
    default_name = socket.gethostname()
    name = click.prompt("Node name", default=default_name)

    config = Config()
    config.node.name = name
    save_config(config)
    console.print(f"[green]Registered node '{name}'[/green]")
    return config


@main.command()
@click.option("--no-tunnel", is_flag=True, help="Local mode: no tunnel, direct WebSocket")
@click.option("--name", default=None, help="Override node name")
@click.option("--port", default=None, type=int, help="Shell server port (required for --no-tunnel)")
@click.option("--no-open", is_flag=True, help="Don't auto-open browser")
@click.option("--show-self", is_flag=True, help="Show this node's sessions in dashboard")
@click.option("--no-dashboard", is_flag=True, help="Don't start dashboard server")
def start(no_tunnel: bool, name: str | None, port: int | None, no_open: bool, show_self: bool, no_dashboard: bool) -> None:
    """Start the daemon (tunnel + shell server + discovery + dashboard)."""
    from shell_cluster.daemon import Daemon

    if no_tunnel and not port:
        console.print("[red]--port is required in local mode (--no-tunnel).[/red]")
        console.print("Example: shellcluster start --no-tunnel --port 8765")
        return

    # Check devtunnel availability (unless local mode)
    if not no_tunnel and not _check_devtunnel():
        return

    # Auto-register if needed
    config = _ensure_registered()
    if name:
        config.node.name = name

    mode = "local" if no_tunnel else "tunnel"
    console.print(
        f"Starting daemon for [bold]{config.node.name}[/bold] (mode={mode})..."
    )
    daemon = Daemon(config, no_tunnel=no_tunnel, local_port=port, no_open=no_open, show_self=show_self, no_dashboard=no_dashboard or not config.node.dashboard)
    try:
        asyncio.run(daemon.run_forever())
    except KeyboardInterrupt:
        pass


@main.command()
def peers() -> None:
    """List discovered peers."""
    from shell_cluster.tunnel.discovery import PeerDiscovery
    from shell_cluster.tunnel.base import get_tunnel_backend, make_tunnel_id

    config = load_config()
    backend = get_tunnel_backend(config.tunnel.backend)
    tunnel_id = make_tunnel_id(config.node.name)
    discovery = PeerDiscovery(
        backend=backend,
        label=config.node.label,
        own_tunnel_id=tunnel_id,
    )

    async def _list() -> None:
        peer_list = await discovery.refresh()
        if not peer_list:
            console.print("[dim]No peers found.[/dim]")
            return
        table = Table(title="Peers")
        table.add_column("Name", style="cyan")
        table.add_column("Tunnel ID", style="dim")
        table.add_column("Status", style="green")
        table.add_column("URI", style="dim")
        for p in peer_list:
            status_style = "green" if p.status.value == "online" else "red"
            table.add_row(
                p.name,
                p.tunnel_id,
                f"[{status_style}]{p.status.value}[/{status_style}]",
                p.forwarding_uri or "-",
            )
        console.print(table)

    asyncio.run(_list())


@main.command()
@click.argument("key", required=False)
@click.argument("value", required=False)
def config(key: str | None, value: str | None) -> None:
    """Show or set config values.

    \b
    shellcluster config              # show config path and all values
    shellcluster config node.name    # show a specific value
    shellcluster config node.name X  # set a value
    """
    from shell_cluster.config import CONFIG_FILE

    if key is None:
        # Show config path + all values
        console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
        if CONFIG_FILE.exists():
            console.print()
            console.print(CONFIG_FILE.read_text())
        else:
            console.print("[dim]No config file yet. Run 'shellcluster register'.[/dim]")
        return

    cfg = load_config()

    # Parse key like "node.name" → section="node", field="name"
    parts = key.split(".", 1)
    if len(parts) != 2:
        console.print(f"[red]Invalid key '{key}'. Use section.field (e.g. node.name)[/red]")
        return
    section, field = parts

    section_obj = getattr(cfg, section, None)
    if section_obj is None or not hasattr(section_obj, field):
        console.print(f"[red]Unknown config key: {key}[/red]")
        return

    if value is None:
        # Show value
        console.print(f"{key} = {getattr(section_obj, field)!r}")
    else:
        # Set value — coerce type
        current = getattr(section_obj, field)
        if isinstance(current, bool):
            value = value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            value = int(value)
        setattr(section_obj, field, value)
        save_config(cfg)
        console.print(f"[green]{key} = {value!r}[/green]")


@main.command()
def dashboard() -> None:
    """Open the dashboard in your browser.

    The dashboard is served by the running daemon.
    Make sure 'shellcluster start' is running first.
    """
    import webbrowser
    config = load_config()
    url = f"http://127.0.0.1:{config.node.dashboard_port}"
    console.print(f"Opening [bold]{url}[/bold]...")
    webbrowser.open(url)


if __name__ == "__main__":
    main()
