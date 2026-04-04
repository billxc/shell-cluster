"""Test: verify server cleans up sessions when client disconnects."""

import asyncio
import json
import websockets


async def test_cleanup():
    uri = "ws://localhost:8765"

    # 1) Create a session, then disconnect abruptly
    print("Step 1: Connect and create a session...")
    async with websockets.connect(uri) as ws:
        raw = await ws.recv()  # peer.info
        info = json.loads(raw)

        create_msg = {
            "type": "shell.create",
            "session_id": "cleanup-test-1",
            "shell": "",
            "cols": 80,
            "rows": 24,
        }
        await ws.send(json.dumps(create_msg))
        raw = await ws.recv()  # shell.created
        msg = json.loads(raw)
        assert msg["type"] == "shell.created"
        print(f"  Created session: {msg['session_id']}")

    # Client disconnected — server should clean up the session
    print("Step 2: Disconnected. Waiting for cleanup...")
    await asyncio.sleep(1)

    # 2) Reconnect and check session list
    print("Step 3: Reconnect and verify session was cleaned up...")
    async with websockets.connect(uri) as ws:
        raw = await ws.recv()  # peer.info

        # Request session list
        await ws.send(json.dumps({"type": "shell.list"}))
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "shell.list.response"

        sessions = msg.get("sessions", [])
        session_ids = [s["id"] for s in sessions]
        print(f"  Active sessions: {session_ids}")

        if "cleanup-test-1" in session_ids:
            print("\n[FAIL] Session was NOT cleaned up after disconnect!")
        else:
            print("\n[PASS] Session cleaned up after client disconnect!")

    print("Test complete.")


if __name__ == "__main__":
    asyncio.run(test_cleanup())
