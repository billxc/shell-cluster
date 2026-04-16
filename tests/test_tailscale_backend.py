"""Unit tests for the Tailscale tunnel backend.

Tests cover TailscaleBackend methods (list_tunnels, connect, host, ensure_tunnel,
get_port_and_uri) and the tailscale_proxy TCP proxy script.
"""

import asyncio
import json
import signal
import sys

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ── Mock data ────────────────────────────────────────────────────────


MOCK_STATUS_RUNNING = {
    "BackendState": "Running",
    "Self": {
        "ID": "self-id",
        "HostName": "my-mac",
        "DNSName": "my-mac.tailnet-abc.ts.net.",
        "TailscaleIPs": ["100.64.0.1", "fd7a::1"],
        "Online": True,
        "OS": "macOS",
    },
    "Peer": {
        "nodekey:abc123": {
            "ID": "peer-1",
            "HostName": "work-pc",
            "DNSName": "work-pc.tailnet-abc.ts.net.",
            "TailscaleIPs": ["100.64.0.2", "fd7a::2"],
            "Online": True,
            "OS": "linux",
        },
        "nodekey:def456": {
            "ID": "peer-2",
            "HostName": "home-server",
            "DNSName": "home-server.tailnet-abc.ts.net.",
            "TailscaleIPs": ["100.64.0.3", "fd7a::3"],
            "Online": True,
            "OS": "linux",
        },
        "nodekey:ghi789": {
            "ID": "peer-3",
            "HostName": "phone",
            "DNSName": "phone.tailnet-abc.ts.net.",
            "TailscaleIPs": ["100.64.0.4", "fd7a::4"],
            "Online": False,
            "OS": "iOS",
        },
    },
}

MOCK_STATUS_NEEDS_LOGIN = {
    "BackendState": "NeedsLogin",
    "Self": {"HostName": "my-mac", "TailscaleIPs": [], "Online": False},
    "Peer": {},
}

MOCK_STATUS_NO_PEERS = {
    "BackendState": "Running",
    "Self": {
        "HostName": "my-mac",
        "TailscaleIPs": ["100.64.0.1"],
        "Online": True,
    },
    "Peer": {},
}


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def backend():
    """Create a TailscaleBackend with default port."""
    from shell_cluster.tunnel.tailscale import TailscaleBackend
    return TailscaleBackend(port=9876)


def _patch_run_tailscale(backend, mock_status):
    """Patch backend._run_tailscale to return mock JSON."""
    async def _mock_run(*args, check=True):
        if "status" in args and "--json" in args:
            return json.dumps(mock_status)
        return ""
    backend._run_tailscale = _mock_run


def _patch_run_tailscale_fail(backend):
    """Patch backend._run_tailscale to simulate tailscale not running."""
    async def _mock_run(*args, check=True):
        if check:
            raise RuntimeError("tailscale not running")
        return ""
    backend._run_tailscale = _mock_run


# ── list_tunnels tests ──────────────────────────────────────────────


async def test_list_tunnels_returns_online_peers(backend):
    """Online peers should be returned as TunnelInfo with hosting=True."""
    _patch_run_tailscale(backend, MOCK_STATUS_RUNNING)

    tunnels = await backend.list_tunnels("shellcluster")

    assert len(tunnels) == 2
    names = {t.tunnel_id for t in tunnels}
    assert names == {"work-pc", "home-server"}
    for t in tunnels:
        assert t.hosting is True
        assert t.port == 9876


async def test_list_tunnels_excludes_self(backend):
    """Self node should not appear in the list."""
    _patch_run_tailscale(backend, MOCK_STATUS_RUNNING)

    tunnels = await backend.list_tunnels("shellcluster")

    tunnel_ids = {t.tunnel_id for t in tunnels}
    assert "my-mac" not in tunnel_ids


async def test_list_tunnels_excludes_offline_peers(backend):
    """Offline peers should not be returned."""
    _patch_run_tailscale(backend, MOCK_STATUS_RUNNING)

    tunnels = await backend.list_tunnels("shellcluster")

    tunnel_ids = {t.tunnel_id for t in tunnels}
    assert "phone" not in tunnel_ids


async def test_list_tunnels_empty_when_no_peers(backend):
    """Should return empty list when no peers exist."""
    _patch_run_tailscale(backend, MOCK_STATUS_NO_PEERS)

    tunnels = await backend.list_tunnels("shellcluster")

    assert tunnels == []


async def test_list_tunnels_returns_empty_on_failure(backend):
    """Should return empty list when tailscale is not running."""
    _patch_run_tailscale_fail(backend)

    tunnels = await backend.list_tunnels("shellcluster")

    assert tunnels == []


async def test_list_tunnels_populates_hostname_to_ip(backend):
    """list_tunnels should populate the internal hostname-to-IP mapping."""
    _patch_run_tailscale(backend, MOCK_STATUS_RUNNING)

    await backend.list_tunnels("shellcluster")

    assert backend._hostname_to_ip["work-pc"] == "100.64.0.2"
    assert backend._hostname_to_ip["home-server"] == "100.64.0.3"
    assert "phone" not in backend._hostname_to_ip


# ── host tests ──────────────────────────────────────────────────────


async def test_host_returns_none(backend):
    """host() should return None — Tailscale daemon handles connectivity."""
    result = await backend.host("test-tunnel", 9876)

    assert result is None


# ── ensure_tunnel tests ─────────────────────────────────────────────


async def test_ensure_tunnel_succeeds_when_connected(backend):
    """Should not raise when BackendState is Running."""
    _patch_run_tailscale(backend, MOCK_STATUS_RUNNING)

    # Should not raise
    await backend.ensure_tunnel("test-tunnel", 9876, "shellcluster")


async def test_ensure_tunnel_fails_when_not_connected(backend):
    """Should raise RuntimeError when Tailscale is not connected."""
    _patch_run_tailscale(backend, MOCK_STATUS_NEEDS_LOGIN)

    with pytest.raises(RuntimeError, match="not connected"):
        await backend.ensure_tunnel("test-tunnel", 9876, "shellcluster")


async def test_ensure_tunnel_fails_when_tailscale_not_running(backend):
    """Should raise RuntimeError when tailscale CLI fails."""
    _patch_run_tailscale_fail(backend)

    with pytest.raises(RuntimeError):
        await backend.ensure_tunnel("test-tunnel", 9876, "shellcluster")


# ── get_port_and_uri tests ──────────────────────────────────────────


async def test_get_port_and_uri_returns_configured_port(backend):
    """Should return (configured_port, '') since Tailscale has no forwarding URI."""
    port, uri = await backend.get_port_and_uri("work-pc")

    assert port == 9876
    assert uri == ""


async def test_get_forwarding_uri_returns_empty(backend):
    """Should return empty string — no forwarding URI concept in Tailscale."""
    uri = await backend.get_forwarding_uri("work-pc", 9876)

    assert uri == ""


# ── no-op method tests ──────────────────────────────────────────────


async def test_create_returns_tunnel_info(backend):
    """create() is a no-op but should return a valid TunnelInfo."""
    info = await backend.create("test-tunnel", 9876, "shellcluster")

    assert info.tunnel_id == "test-tunnel"
    assert info.port == 9876


async def test_delete_is_noop(backend):
    """delete() should not raise."""
    await backend.delete("test-tunnel")


async def test_exists_returns_true(backend):
    """exists() should return True — Tailscale manages existence."""
    result = await backend.exists("test-tunnel")

    assert result is True


# ── connect tests ───────────────────────────────────────────────────


async def test_connect_raises_for_unknown_peer(backend):
    """connect() should raise when peer IP is not in the hostname-to-IP map."""
    with pytest.raises(RuntimeError, match="not discovered"):
        await backend.connect("unknown-peer", 9876)


async def test_connect_starts_proxy_and_returns_uri(backend):
    """connect() should spawn tailscale_proxy and return (process, ws_uri)."""
    # Pre-populate the IP mapping (normally done by list_tunnels)
    backend._hostname_to_ip["work-pc"] = "100.64.0.2"

    proc, ws_uri = await backend.connect("work-pc", 9876)

    try:
        assert proc is not None
        assert proc.returncode is None  # still running
        assert ws_uri.startswith("ws://localhost:")
        port = int(ws_uri.split(":")[-1])
        assert port > 0
    finally:
        proc.kill()
        await proc.wait()


# ── parse_node_name compatibility ───────────────────────────────────


def test_parse_node_name_passthrough_for_tailscale_hostnames():
    """Tailscale hostnames (no shellcluster prefix/suffix) should pass through."""
    from shell_cluster.tunnel.base import parse_node_name

    assert parse_node_name("work-pc") == "work-pc"
    assert parse_node_name("home-server") == "home-server"
    assert parse_node_name("my-mac") == "my-mac"


# ── _parse_hostname (port-in-hostname) tests ────────────────────────


def test_parse_hostname_default_port():
    """Hostname without -p suffix should use default port."""
    from shell_cluster.tunnel.tailscale import _parse_hostname

    name, port = _parse_hostname("work-pc", 9876)
    assert name == "work-pc"
    assert port == 9876


def test_parse_hostname_custom_port():
    """Hostname with -p suffix should extract port."""
    from shell_cluster.tunnel.tailscale import _parse_hostname

    name, port = _parse_hostname("work-pc-p9877", 9876)
    assert name == "work-pc"
    assert port == 9877


def test_parse_hostname_no_false_positive():
    """Hostname ending with digits but no -p should not be parsed as port."""
    from shell_cluster.tunnel.tailscale import _parse_hostname

    name, port = _parse_hostname("server-2", 9876)
    assert name == "server-2"
    assert port == 9876


def test_parse_hostname_with_dashes():
    """Hostname with multiple dashes and -p suffix should parse correctly."""
    from shell_cluster.tunnel.tailscale import _parse_hostname

    name, port = _parse_hostname("my-home-server-p8080", 9876)
    assert name == "my-home-server"
    assert port == 8080


async def test_list_tunnels_parses_port_from_hostname(backend):
    """list_tunnels should parse custom port from hostname -p suffix."""
    status = {
        "BackendState": "Running",
        "Self": {"HostName": "my-mac", "TailscaleIPs": ["100.64.0.1"], "Online": True},
        "Peer": {
            "nodekey:aaa": {
                "HostName": "server-p9877",
                "TailscaleIPs": ["100.64.0.10"],
                "Online": True,
            },
            "nodekey:bbb": {
                "HostName": "desktop",
                "TailscaleIPs": ["100.64.0.11"],
                "Online": True,
            },
        },
    }
    _patch_run_tailscale(backend, status)

    tunnels = await backend.list_tunnels("shellcluster")

    by_id = {t.tunnel_id: t for t in tunnels}
    assert by_id["server-p9877"].port == 9877
    assert by_id["server-p9877"].description == "server"
    assert by_id["desktop"].port == 9876  # default
    assert by_id["desktop"].description == "desktop"


async def test_get_port_and_uri_respects_hostname_port(backend):
    """get_port_and_uri should parse port from tunnel_id hostname."""
    port, uri = await backend.get_port_and_uri("server-p9877")
    assert port == 9877
    assert uri == ""


# ── tailscale_proxy E2E tests ──────────────────────────────────────


async def _start_echo_server():
    """Start a simple TCP echo server that echoes back received data."""
    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_proxy_pipes_data_bidirectionally():
    """Proxy should forward data between client and target via tailscale nc.

    Since we can't use real tailscale nc in tests, we test the proxy
    with a mock nc script that just cats stdin to stdout (echo behavior).
    """
    echo_server, echo_port = await _start_echo_server()

    # Create a mock "tailscale" script that acts like `tailscale nc` by
    # connecting to our echo server instead
    import tempfile
    import os
    import stat

    mock_script = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="mock_tailscale_nc_"
    )
    mock_script.write(f"""#!/usr/bin/env python3
import asyncio, sys

async def main():
    # Ignore "nc" arg and IP/port args, connect to echo server
    r, w = await asyncio.open_connection("127.0.0.1", {echo_port})
    async def pipe_in():
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            data = await reader.read(4096)
            if not data:
                break
            w.write(data)
            await w.drain()
        w.close()
    async def pipe_out():
        while True:
            data = await r.read(4096)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    await asyncio.gather(pipe_in(), pipe_out())

asyncio.run(main())
""")
    mock_script.close()

    try:
        # Start the proxy, overriding the tailscale command to use our mock
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "shell_cluster.tunnel.tailscale_proxy",
            "--peer-ip", "127.0.0.1", "--peer-port", str(echo_port),
            "--tailscale-cmd", sys.executable, mock_script.name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Read the LISTENING line
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        proxy_port = int(line.decode().strip().split(":")[-1])

        # Connect through the proxy
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)

        # Send data and verify echo
        test_data = b"hello tailscale proxy"
        writer.write(test_data)
        await writer.drain()

        response = await asyncio.wait_for(reader.read(len(test_data)), timeout=5)
        assert response == test_data

        writer.close()
        await writer.wait_closed()
    finally:
        proc.kill()
        await proc.wait()
        echo_server.close()
        await echo_server.wait_closed()
        os.unlink(mock_script.name)


async def test_proxy_handles_unreachable_target():
    """Proxy should not crash when the target is unreachable."""
    # Start proxy pointing to a port that nothing listens on
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "shell_cluster.tunnel.tailscale_proxy",
        "--peer-ip", "127.0.0.1", "--peer-port", "1",  # port 1 — nothing listens here
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        # Read the LISTENING line — proxy should still start
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        proxy_port = int(line.decode().strip().split(":")[-1])

        # Connect to the proxy — the connection may succeed but data won't flow
        # or the connection will be immediately reset. Either is fine.
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(b"test")
            await writer.drain()
            # Wait briefly for proxy to handle the failed nc
            await asyncio.sleep(0.5)
            writer.close()
            await writer.wait_closed()
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
            pass

        # Proxy should still be running (not crashed)
        assert proc.returncode is None
    finally:
        proc.kill()
        await proc.wait()


async def test_proxy_exits_on_sigterm():
    """Proxy should exit cleanly on SIGTERM."""
    if sys.platform == "win32":
        pytest.skip("SIGTERM not supported on Windows")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "shell_cluster.tunnel.tailscale_proxy",
        "--peer-ip", "127.0.0.1", "--peer-port", "9876",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        # Wait for proxy to be ready
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        assert line.decode().strip().startswith("LISTENING:")

        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)

        # Should exit within 2 seconds
        await asyncio.wait_for(proc.wait(), timeout=2)
        assert proc.returncode is not None
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        pytest.fail("Proxy did not exit after SIGTERM")
