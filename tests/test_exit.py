"""Test: shell exit causes client to disconnect gracefully."""

import asyncio
import base64
import json

import websockets


async def test_exit():
    uri = "ws://localhost:8765"
    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as ws:
        # Peer info
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "peer.info"
        print(f"  Connected to: {msg.get('name')}")

        # Create shell
        session_id = "exit-test"
        await ws.send(json.dumps({
            "type": "shell.create",
            "session_id": session_id,
            "cols": 80, "rows": 24,
        }))

        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "shell.created"
        print(f"  Shell: {msg.get('shell')}")

        # Drain initial output
        await asyncio.sleep(0.5)
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        # Send "exit\n"
        print("  Sending 'exit'...")
        cmd = b"exit\n"
        await ws.send(json.dumps({
            "type": "shell.data",
            "session_id": session_id,
            "data": base64.b64encode(cmd).decode(),
        }))

        # Wait for shell.closed
        got_closed = False
        for _ in range(30):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg["type"] == "shell.closed":
                    got_closed = True
                    print(f"  Received shell.closed for {msg.get('session_id')}")
                    break
            except asyncio.TimeoutError:
                break

        if got_closed:
            print("\n[PASS] Shell exit triggers graceful close!")
        else:
            print("\n[FAIL] Did not receive shell.closed")

    print("Test complete.")


if __name__ == "__main__":
    asyncio.run(test_exit())
