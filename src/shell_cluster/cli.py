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


def _make_discovery(config: Config):
    """Create a PeerDiscovery instance from config."""
    from shell_cluster.discovery import PeerDiscovery
    from shell_cluster.tunnel.base import get_tunnel_backend, make_tunnel_id

    backend = get_tunnel_backend(config.tunnel.backend)
    tunnel_id = make_tunnel_id(config.node.name)
    return PeerDiscovery(
        backend=backend,
        label=config.node.label,
        own_tunnel_id=tunnel_id,
        interval=config.discovery.interval_seconds,
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
    if no_tunnel:
        console.print(
            f"Starting daemon for [bold]{config.node.name}[/bold] "
            f"(mode={mode}, port={config.node.port})..."
        )
    else:
        console.print(
            f"Starting daemon for [bold]{config.node.name}[/bold] "
            f"(mode={mode})..."
        )
    daemon = Daemon(config, no_tunnel=no_tunnel)
    try:
        asyncio.run(daemon.run_forever())
    except KeyboardInterrupt:
        pass


@main.command()
def peers() -> None:
    """List discovered peers."""
    config = load_config()
    discovery = _make_discovery(config)

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
    config = load_config()
    discovery = _make_discovery(config)

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

        if not peer.port:
            console.print(f"[red]No port info for peer '{target}'.[/red]")
            return

        # Map tunnel to local port via devtunnel connect
        from shell_cluster.tunnel.base import get_tunnel_backend
        backend = get_tunnel_backend(config.tunnel.backend)

        console.print(f"Mapping tunnel for [bold]{peer.name}[/bold]...")
        proc, local_port = await backend.connect(peer.tunnel_id, peer.port)
        try:
            console.print(f"Connected via localhost:{local_port}")
            console.print("[dim]Disconnect: ~. (tilde-dot after newline)[/dim]")
            client = ShellClient(f"ws://localhost:{local_port}")
            await client.connect_and_run(shell=shell_type)
            console.print(f"\nDisconnected from {peer.name}.")
        finally:
            proc.kill()
            await proc.wait()

    asyncio.run(_connect())


@main.command()
@click.option("--port", "dash_port", default=9000, help="Dashboard HTTP port")
@click.option("--no-open", is_flag=True, help="Don't auto-open browser")
def dashboard(dash_port: int, no_open: bool) -> None:
    """Open the web dashboard in your browser.

    Combines peers from config.toml and devtunnel auto-discovery.

    Add manual peers in config.toml:

        [[peers]]
        name = "macbook"
        uri = "ws://192.168.1.10:8765"
    """
    from shell_cluster.web.server import DashboardServer

    config = load_config()

    peer_list: list[dict] = []
    seen_names: set[str] = set()
    tunnel_procs: list = []

    # 1. Read manual peers from config (direct ws:// connections)
    for p in config.peers:
        uri = p.uri
        if not uri.startswith("ws://") and not uri.startswith("wss://"):
            uri = f"ws://{uri}"
        name = p.name or uri.replace("ws://", "").replace("wss://", "").replace(":", "-")
        peer_list.append({"name": name, "uri": uri, "status": "online"})
        seen_names.add(name)

    # 2. Discover tunnel peers and map to local ports
    discovery = _make_discovery(config)

    async def _setup_tunnel_peers():
        from shell_cluster.tunnel.base import get_tunnel_backend
        backend = get_tunnel_backend(config.tunnel.backend)
        try:
            console.print("[dim]Discovering peers...[/dim]")
            discovered = await discovery.refresh()
            for p in discovered:
                if p.name in seen_names or not p.port:
                    continue
                try:
                    proc, local_port = await backend.connect(p.tunnel_id, p.port)
                    tunnel_procs.append(proc)
                    peer_list.append({
                        "name": p.name,
                        "uri": f"ws://localhost:{local_port}",
                        "status": p.status.value,
                    })
                    seen_names.add(p.name)
                    console.print(f"  {p.name} -> localhost:{local_port}")
                except Exception as e:
                    console.print(f"  [dim]{p.name}: tunnel connect failed ({e})[/dim]")
        except Exception as e:
            console.print(f"[dim]Discovery skipped: {e}[/dim]")

    try:
        asyncio.run(_setup_tunnel_peers())
    except Exception:
        pass

    if not peer_list:
        console.print("[yellow]No peers found.[/yellow]")
        console.print("Add peers to config.toml:")
        console.print("  [[peers]]")
        console.print('  name = "my-pc"')
        console.print('  uri = "ws://192.168.1.10:8765"')
        return

    console.print(f"Starting dashboard on [bold]http://127.0.0.1:{dash_port}[/bold]")
    for p in peer_list:
        console.print(f"  Peer: {p['name']} -> {p['uri']}")

    server = DashboardServer(peer_list, port=dash_port, no_open=no_open)
    try:
        asyncio.run(server.run_forever())
    except KeyboardInterrupt:
        pass
    finally:
        for proc in tunnel_procs:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    main()
