"""Test: two daemons running locally, client connects to each and runs commands."""

import asyncio
import base64
import json

import websockets


async def test_node(name: str, port: int):
    uri = f"ws://localhost:{port}"
    print(f"\n--- Testing {name} at {uri} ---")

    async with websockets.connect(uri) as ws:
        # Peer info
        raw = await ws.recv()
        msg = json.loads(raw)
        print(f"  Peer: {msg.get('name', '?')}")
        assert msg["type"] == "peer.info"

        # Create shell
        session_id = f"test-{name}"
        await ws.send(json.dumps({
            "type": "shell.create",
            "session_id": session_id,
            "cols": 80, "rows": 24,
        }))

        raw = await ws.recv()
        msg = json.loads(raw)
        print(f"  Shell created: {msg.get('shell', '?')}")
        assert msg["type"] == "shell.created"

        # Drain initial output
        await asyncio.sleep(0.5)
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        # Send a unique command
        marker = f"MARKER-{name}-{port}"
        cmd = f"echo {marker}\n".encode()
        await ws.send(json.dumps({
            "type": "shell.data",
            "session_id": session_id,
            "data": base64.b64encode(cmd).decode(),
        }))

        # Read output
        output_parts = []
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg["type"] == "shell.data":
                    output_parts.append(base64.b64decode(msg["data"]))
                    if marker.encode() in b"".join(output_parts):
                        break
            except asyncio.TimeoutError:
                break

        combined = b"".join(output_parts)
        found = marker.encode() in combined
        status = "PASS" if found else "FAIL"
        print(f"  Output contains marker: {found}")
        print(f"  [{status}] {name}")

        # Cleanup
        await ws.send(json.dumps({"type": "shell.close", "session_id": session_id}))
        await asyncio.sleep(0.2)

    return found


async def main():
    r1 = await test_node("node-a", 8765)
    r2 = await test_node("node-b", 8766)

    print("\n=== Summary ===")
    print(f"  node-a (8765): {'PASS' if r1 else 'FAIL'}")
    print(f"  node-b (8766): {'PASS' if r2 else 'FAIL'}")

    if r1 and r2:
        print("\n  All tests passed!")
    else:
        print("\n  Some tests failed!")


if __name__ == "__main__":
    asyncio.run(main())
