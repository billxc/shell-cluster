"""TCP proxy that pipes connections through 'tailscale nc'.

Usage:
    python -m shell_cluster.tunnel.tailscale_proxy --peer-ip 100.64.0.2 --peer-port 9876

Prints LISTENING:<port> to stdout when ready.
Accepts TCP connections and pipes each through `tailscale nc <ip> <port>`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

log = logging.getLogger(__name__)


async def _pipe(reader: asyncio.StreamReader, writer) -> None:
    """Read from reader and write to writer until EOF."""
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            if hasattr(writer, "close"):
                writer.close()
        except Exception:
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer_ip: str,
    peer_port: int,
    nc_cmd: list[str],
) -> None:
    """Handle a single TCP client by piping through tailscale nc."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *nc_cmd, peer_ip, str(peer_port),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.error("tailscale command not found: %s", nc_cmd[0])
        client_writer.close()
        return
    except Exception as e:
        log.error("Failed to start tailscale nc: %s", e)
        client_writer.close()
        return

    try:
        # Bidirectional pipe: client <-> tailscale nc
        tasks = [
            asyncio.create_task(_pipe(client_reader, proc.stdin)),
            asyncio.create_task(_pipe(proc.stdout, client_writer)),
        ]
        # Wait for either direction to finish
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except Exception as e:
        log.debug("Connection error: %s", e)
    finally:
        # Clean up tailscale nc process
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        # Close client connection
        try:
            client_writer.close()
        except Exception:
            pass


async def _run(peer_ip: str, peer_port: int, nc_cmd: list[str]) -> None:
    """Main proxy loop: listen and forward connections."""
    loop = asyncio.get_event_loop()

    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, peer_ip, peer_port, nc_cmd),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]

    # Signal the parent process that we are ready
    sys.stdout.write(f"LISTENING:{port}\n")
    sys.stdout.flush()

    # Register signal handlers for clean shutdown
    stop = asyncio.Event()

    def _on_signal():
        stop.set()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _on_signal)

    try:
        await stop.wait()
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP proxy via tailscale nc")
    parser.add_argument("--peer-ip", required=True, help="Tailscale IP of the peer")
    parser.add_argument("--peer-port", required=True, type=int, help="Port on the peer")
    parser.add_argument(
        "--tailscale-cmd",
        nargs="+",
        default=["tailscale", "nc"],
        help="Command prefix for tailscale nc (default: tailscale nc)",
    )
    args = parser.parse_args()

    asyncio.run(_run(args.peer_ip, args.peer_port, args.tailscale_cmd))


if __name__ == "__main__":
    main()
