"""Quick test: connect to local shell server, run 'echo hello', verify output."""

import asyncio
import json
import base64
import websockets


async def test_connect():
    uri = "ws://localhost:8765"
    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as ws:
        # Should get peer.info on connect
        raw = await ws.recv()
        msg = json.loads(raw)
        print(f"Received: {msg['type']} - name={msg.get('name', '?')}")
        assert msg["type"] == "peer.info", f"Expected peer.info, got {msg['type']}"

        # Create a shell session
        session_id = "test123"
        create_msg = {
            "type": "shell.create",
            "session_id": session_id,
            "shell": "",
            "cols": 80,
            "rows": 24,
        }
        await ws.send(json.dumps(create_msg))

        # Should get shell.created
        raw = await ws.recv()
        msg = json.loads(raw)
        print(f"Received: {msg['type']} - shell={msg.get('shell', '?')}")
        assert msg["type"] == "shell.created", f"Expected shell.created, got {msg['type']}"

        # Wait a bit for shell prompt
        await asyncio.sleep(0.5)

        # Read any initial output (prompt, motd, etc.)
        output_parts = []
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg["type"] == "shell.data":
                    data = base64.b64decode(msg["data"])
                    output_parts.append(data)
        except asyncio.TimeoutError:
            pass

        if output_parts:
            initial = b"".join(output_parts)
            print(f"Initial output ({len(initial)} bytes): {initial[:200]!r}")

        # Send "echo hello-shell-cluster\n"
        cmd = b"echo hello-shell-cluster\n"
        data_msg = {
            "type": "shell.data",
            "session_id": session_id,
            "data": base64.b64encode(cmd).decode(),
        }
        await ws.send(json.dumps(data_msg))

        # Read output until we see our marker
        output_parts = []
        found = False
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg["type"] == "shell.data":
                    data = base64.b64decode(msg["data"])
                    output_parts.append(data)
                    combined = b"".join(output_parts)
                    if b"hello-shell-cluster" in combined:
                        found = True
                        break
            except asyncio.TimeoutError:
                break

        combined = b"".join(output_parts)
        print(f"Command output: {combined!r}")

        if found:
            print("\n[PASS] Successfully connected to shell and executed command!")
        else:
            print("\n[FAIL] Did not receive expected output")

        # Close the session
        close_msg = {"type": "shell.close", "session_id": session_id}
        await ws.send(json.dumps(close_msg))
        await asyncio.sleep(0.3)

    print("Test complete.")


if __name__ == "__main__":
    asyncio.run(test_connect())
