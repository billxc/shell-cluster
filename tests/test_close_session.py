"""E2E tests for close session via raw WebSocket protocol.

Tests verify that shell.close properly kills sessions, that other sessions
survive, and that disconnecting without shell.close preserves sessions.
"""

import asyncio
import json
import uuid
from urllib.request import urlopen

import pytest
import pytest_asyncio
import websockets

from shell_cluster.shell.manager import ShellManager
from shell_cluster.shell.server import ShellServer

pytestmark = pytest.mark.asyncio


# ── Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def server():
    """Start a ShellServer on a random port, yield (manager, port), then stop."""
    manager = ShellManager()
    srv = ShellServer(shell_manager=manager, node_name="test-node", port=0)
    await srv.start()
    port = srv.port
    yield manager, port
    # Close PTY sessions via ptyprocess lifecycle (closes fd + kills child)
    for session in list(manager.sessions.values()):
        try:
            session._handle.close(force=True)
        except Exception:
            pass
    # Wait for reader threads to notice closed FDs
    for task in list(manager._readers.values()):
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()
    manager._sessions.clear()
    manager._readers.clear()
    # Close the WS server (no sessions left, so stop() won't block)
    srv._server.close()


# ── Helpers ─────────────────────────────────────────────────────────


async def create_session(port: int, session_id: str, cols: int = 80, rows: int = 24):
    """Open a raw WS connection, create a session, return the websocket."""
    uri = f"ws://localhost:{port}/raw?session={session_id}&cols={cols}&rows={rows}"
    ws = await websockets.connect(uri)
    for _ in range(20):
        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        if isinstance(msg, str):
            try:
                data = json.loads(msg)
                if data.get("type") == "shell.created":
                    break
            except json.JSONDecodeError:
                pass
    await drain_output(ws, timeout=0.5)
    return ws


async def attach_session(port: int, session_id: str, cols: int = 80, rows: int = 24):
    """Open a raw WS connection, attach to existing session, return the websocket."""
    uri = f"ws://localhost:{port}/raw?attach={session_id}&cols={cols}&rows={rows}"
    ws = await websockets.connect(uri)
    for _ in range(20):
        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        if isinstance(msg, str):
            try:
                data = json.loads(msg)
                if data.get("type") == "shell.attached":
                    break
            except json.JSONDecodeError:
                pass
    await drain_output(ws, timeout=0.5)
    return ws


async def send_command(ws, cmd: str, timeout: float = 5.0) -> bytes:
    """Send a command and collect output until marker is found or overall timeout."""
    marker = f"__MK_{uuid.uuid4().hex[:8]}__"
    full_cmd = f"{cmd}; echo {marker}\n"
    await ws.send(full_cmd.encode())
    output = b""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
            if isinstance(msg, bytes):
                output += msg
                if marker.encode() in output:
                    break
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            break
    return output


async def close_session(ws, session_id: str) -> bool:
    """Send shell.close and wait for shell.closed. Returns True if received."""
    await ws.send(json.dumps({"type": "shell.close", "session_id": session_id}))
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
            if isinstance(msg, str):
                try:
                    data = json.loads(msg)
                    if data.get("type") == "shell.closed":
                        return True
                except json.JSONDecodeError:
                    pass
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            break
    return False


async def drain_output(ws, timeout: float = 0.3):
    """Read and discard all pending frames."""
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            break


async def get_sessions(port: int) -> list[dict]:
    """HTTP GET /sessions — run in executor to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    def _fetch():
        with urlopen(f"http://localhost:{port}/sessions", timeout=3) as resp:
            return json.loads(resp.read())
    return await loop.run_in_executor(None, _fetch)


# ── Tests ───────────────────────────────────────────────────────────


async def test_close_one_of_many(server):
    """TC-1: Close one session among many — others survive."""
    manager, port = server

    ws1 = await create_session(port, "s1")
    ws2 = await create_session(port, "s2")
    ws3 = await create_session(port, "s3")

    try:
        assert len(await get_sessions(port)) == 3

        assert b"ALIVE-S1" in await send_command(ws1, "echo ALIVE-S1")
        assert b"ALIVE-S2" in await send_command(ws2, "echo ALIVE-S2")
        assert b"ALIVE-S3" in await send_command(ws3, "echo ALIVE-S3")

        # Close S2
        assert await close_session(ws2, "s2"), "Did not receive shell.closed for s2"

        await asyncio.sleep(0.3)
        sessions = await get_sessions(port)
        session_ids = [s["id"] for s in sessions]
        assert "s2" not in session_ids
        assert "s1" in session_ids
        assert "s3" in session_ids

        # S1 and S3 should still work
        assert b"STILL-S1" in await send_command(ws1, "echo STILL-S1")
        assert b"STILL-S3" in await send_command(ws3, "echo STILL-S3")
    finally:
        for ws in [ws1, ws2, ws3]:
            await ws.close()


async def test_close_all_sessions_sequentially(server):
    """TC-2: Close all sessions one by one."""
    manager, port = server

    ws1 = await create_session(port, "s1")
    ws2 = await create_session(port, "s2")
    ws3 = await create_session(port, "s3")

    try:
        assert len(await get_sessions(port)) == 3

        assert await close_session(ws1, "s1")
        await asyncio.sleep(0.2)
        sessions = await get_sessions(port)
        assert len(sessions) == 2
        assert "s1" not in [s["id"] for s in sessions]

        assert await close_session(ws2, "s2")
        await asyncio.sleep(0.2)
        sessions = await get_sessions(port)
        assert len(sessions) == 1
        assert sessions[0]["id"] == "s3"

        assert await close_session(ws3, "s3")
        await asyncio.sleep(0.2)
        assert len(await get_sessions(port)) == 0
    finally:
        for ws in [ws1, ws2, ws3]:
            await ws.close()


async def test_disconnect_without_close_preserves_session(server):
    """TC-3: WS disconnect without shell.close — session persists."""
    manager, port = server

    ws = await create_session(port, "persist-test")
    assert b"PERSIST-MARKER" in await send_command(ws, "echo PERSIST-MARKER")

    # Disconnect WITHOUT sending shell.close
    await ws.close()
    await asyncio.sleep(0.3)

    # Session should still exist (not auto-destroyed on disconnect)
    sessions = await get_sessions(port)
    assert any(s["id"] == "persist-test" for s in sessions)


async def test_close_then_reattach_fails(server):
    """TC-4: After shell.close, re-attach should fail."""
    manager, port = server

    ws = await create_session(port, "gone-test")
    assert await close_session(ws, "gone-test")
    await ws.close()
    await asyncio.sleep(0.3)

    assert not any(s["id"] == "gone-test" for s in await get_sessions(port))

    uri = f"ws://localhost:{port}/raw?attach=gone-test&cols=80&rows=24"
    try:
        ws2 = await websockets.connect(uri)
        try:
            await asyncio.wait_for(ws2.recv(), timeout=3)
            await ws2.close()
            pytest.fail("Expected connection to be closed with 1008")
        except websockets.ConnectionClosed as e:
            assert e.code == 1008
            assert "not found" in str(e.reason).lower()
    except websockets.ConnectionClosed as e:
        assert e.code == 1008


async def test_close_during_active_output(server):
    """TC-5: Close works even while output is streaming."""
    manager, port = server

    ws = await create_session(port, "busy-test")

    try:
        await ws.send(b"for i in $(seq 1 100); do echo LINE-$i; done\n")
        await asyncio.sleep(0.2)

        assert await close_session(ws, "busy-test"), "Did not receive shell.closed during active output"

        await asyncio.sleep(0.3)
        assert not any(s["id"] == "busy-test" for s in await get_sessions(port))
    finally:
        await ws.close()
