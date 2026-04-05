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
@click.option("--port", default=8765, help="Local port for shell server")
@click.option("--label", default="shellcluster", help="Tunnel label for discovery")
@click.option("--backend", default="devtunnel", help="Tunnel backend (devtunnel)")
def register(name: str, port: int, label: str, backend: str) -> None:
    """Register this machine to the cluster."""
    config = load_config()
    if name:
        config.node.name = name
    config.node.port = port
    config.node.label = label
    config.tunnel.backend = backend
    save_config(config)
    console.print(f"[green]Registered node '{config.node.name}'[/green]")
    console.print(f"  Port: {config.node.port}")
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


@main.command()
@click.option("--no-tunnel", is_flag=True, help="Local mode: no tunnel, direct WebSocket")
@click.option("--name", default=None, help="Override node name")
@click.option("--port", default=None, type=int, help="Override shell server port")
@click.option("--no-open", is_flag=True, help="Don't auto-open browser")
def start(no_tunnel: bool, name: str | None, port: int | None, no_open: bool) -> None:
    """Start the daemon (tunnel + shell server + discovery + dashboard)."""
    from shell_cluster.daemon import Daemon

    config = load_config()
    if name:
        config.node.name = name
    if port:
        config.node.port = port

    mode = "local" if no_tunnel else "tunnel"
    console.print(
        f"Starting daemon for [bold]{config.node.name}[/bold] (mode={mode})..."
    )
    daemon = Daemon(config, no_tunnel=no_tunnel, no_open=no_open)
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
