"""CLI entry point for shell-cluster."""

from __future__ import annotations

import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table

from shell_cluster.config import load_config, save_config

console = Console()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
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
@click.option("--no-tunnel", is_flag=True, help="Local mode: no tunnel, direct WebSocket")
@click.option("--name", default=None, help="Override node name")
@click.option("--port", default=None, type=int, help="Override port")
def start(no_tunnel: bool, name: str | None, port: int | None) -> None:
    """Start the daemon (tunnel + shell server + discovery)."""
    from shell_cluster.daemon import Daemon

    config = load_config()
    if name:
        config.node.name = name
    if port:
        config.node.port = port

    mode = "local" if no_tunnel else "tunnel"
    console.print(
        f"Starting daemon for [bold]{config.node.name}[/bold] "
        f"(mode={mode}, port={config.node.port})..."
    )
    daemon = Daemon(config, no_tunnel=no_tunnel)
    try:
        asyncio.run(daemon.run_forever())
    except KeyboardInterrupt:
        pass


@main.command()
def peers() -> None:
    """List discovered peers."""
    from shell_cluster.discovery import PeerDiscovery
    from shell_cluster.tunnel.devtunnel import DevTunnelBackend

    config = load_config()
    backend = DevTunnelBackend()
    tunnel_id = f"shellcluster-{config.node.name}"
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


def _is_uri(s: str) -> bool:
    """Check if a string looks like a direct URI (ws:// or host:port)."""
    return s.startswith("ws://") or s.startswith("wss://") or ":" in s


@main.command()
@click.argument("target")
@click.argument("shell_type", default="")
def connect(target: str, shell_type: str) -> None:
    """Connect to a remote peer's shell.

    TARGET can be a peer name or a direct URI (ws://host:port).
    """
    from shell_cluster.client import ShellClient

    if _is_uri(target):
        # Direct URI connection (no tunnel needed)
        uri = target
        if not uri.startswith("ws://") and not uri.startswith("wss://"):
            uri = f"ws://{uri}"

        console.print(f"Connecting to [bold]{target}[/bold]...")
        console.print("[dim]Disconnect: ~. (tilde-dot after newline)[/dim]")
        client = ShellClient(uri)
        try:
            asyncio.run(client.connect_and_run(shell=shell_type))
        except KeyboardInterrupt:
            pass
        console.print(f"\nDisconnected from {target}.")
        return

    # Peer name lookup via discovery
    from shell_cluster.discovery import PeerDiscovery
    from shell_cluster.tunnel.devtunnel import DevTunnelBackend

    config = load_config()
    backend = DevTunnelBackend()
    tunnel_id = f"shellcluster-{config.node.name}"
    discovery = PeerDiscovery(
        backend=backend,
        label=config.node.label,
        own_tunnel_id=tunnel_id,
    )

    async def _connect() -> None:
        peer_list = await discovery.refresh()
        peer = None
        for p in peer_list:
            if p.name == target or p.tunnel_id == target:
                peer = p
                break

        if not peer:
            console.print(f"[red]Peer '{target}' not found.[/red]")
            available = [p.name for p in peer_list]
            if available:
                console.print(f"Available peers: {', '.join(available)}")
            return

        if not peer.forwarding_uri:
            console.print(f"[red]No forwarding URI for peer '{target}'.[/red]")
            return

        console.print(f"Connecting to [bold]{peer.name}[/bold]...")
        console.print("[dim]Disconnect: ~. (tilde-dot after newline)[/dim]")
        client = ShellClient(peer.forwarding_uri)
        await client.connect_and_run(shell=shell_type)
        console.print(f"\nDisconnected from {peer.name}.")

    asyncio.run(_connect())


@main.command()
def dashboard() -> None:
    """Open the TUI dashboard."""
    from shell_cluster.tui.app import ShellClusterApp

    config = load_config()
    app = ShellClusterApp(config)
    app.run()


if __name__ == "__main__":
    main()
