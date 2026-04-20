"""CJK text round-trip test: send CJK characters via WebSocket, verify PTY echoes them back."""

import asyncio
import json
import base64
import sys

# Default server address — override via command line: python test_cjk_backend.py ws://host:port
SERVER_URI = "ws://localhost:8765"

# Test cases: (description, input_text, expected_in_output)
CJK_TEST_CASES = [
    ("Chinese: echo 你好", "echo CJK_你好_TEST\n", "CJK_你好_TEST"),
    ("Japanese: echo こんにちは", "echo CJK_こんにちは_TEST\n", "CJK_こんにちは_TEST"),
    ("Korean: echo 한글", "echo CJK_한글_TEST\n", "CJK_한글_TEST"),
    ("Mixed ASCII+CJK", "echo CJK_hello你好world_TEST\n", "CJK_hello你好world_TEST"),
    ("CJK punctuation", "echo CJK_「引号」、逗号。_TEST\n", "CJK_「引号」、逗号。_TEST"),
    (
        "Multi-byte boundary (near 4096)",
        # 1360 CJK chars × 3 bytes = 4080 bytes, plus 'echo ' (5) + marker + newline ≈ near 4096 boundary
        "echo CJK_" + "字" * 1360 + "_END\n",
        "CJK_" + "字" * 1360 + "_END",
    ),
]


async def recv_until(ws, marker, timeout=5.0):
    """Read WebSocket messages until marker is found in accumulated output or timeout."""
    parts = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
            msg = json.loads(raw)
            if msg["type"] == "shell.data":
                data = base64.b64decode(msg["data"])
                parts.append(data)
                combined = b"".join(parts)
                if marker.encode("utf-8") in combined:
                    return combined.decode("utf-8", errors="replace"), True
        except asyncio.TimeoutError:
            continue
    combined = b"".join(parts)
    return combined.decode("utf-8", errors="replace"), False


async def run_tests(uri):
    import websockets

    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as ws:
        # Receive peer.info
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "peer.info", f"Expected peer.info, got {msg['type']}"
        print(f"Connected to: {msg.get('name', 'unknown')}")

        # Create shell session
        session_id = "cjk_test_session"
        await ws.send(json.dumps({
            "type": "shell.create",
            "session_id": session_id,
            "shell": "",
            "cols": 200,  # Wide terminal to avoid line wrapping on long CJK strings
            "rows": 24,
        }))

        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "shell.created", f"Expected shell.created, got {msg['type']}"
        print(f"Shell created: {msg.get('shell', '?')}")

        # Wait for shell prompt and drain initial output
        await asyncio.sleep(1.0)
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        # Run test cases
        passed = 0
        failed = 0

        for desc, input_text, expected_marker in CJK_TEST_CASES:
            # Send the command
            cmd_bytes = input_text.encode("utf-8")
            await ws.send(json.dumps({
                "type": "shell.data",
                "session_id": session_id,
                "data": base64.b64encode(cmd_bytes).decode(),
            }))

            # Read output until we find the marker
            output, found = await recv_until(ws, expected_marker, timeout=5.0)

            if found:
                print(f"  [PASS] {desc}")
                passed += 1
            else:
                print(f"  [FAIL] {desc}")
                print(f"         Expected marker: {expected_marker[:60]}...")
                out_preview = output[:200].replace("\n", "\\n")
                print(f"         Got output: {out_preview}...")
                failed += 1

        # Cleanup
        await ws.send(json.dumps({"type": "shell.close", "session_id": session_id}))
        await asyncio.sleep(0.3)

        # Summary
        print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
        return failed == 0


if __name__ == "__main__":
    uri = sys.argv[1] if len(sys.argv) > 1 else SERVER_URI

    try:
        import websockets  # noqa: F401
    except ImportError:
        print("ERROR: 'websockets' package required. Install with: pip install websockets")
        sys.exit(1)

    success = asyncio.run(run_tests(uri))
    sys.exit(0 if success else 1)
