"""IME 测试用 shell server，带 PTY 收发日志。端口 18765，与生产隔离。"""
import asyncio
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from shell_cluster.shell.server import ShellServer
from shell_cluster.shell.manager import ShellManager

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
LOG_FILE = os.path.join(os.path.dirname(__file__), "pty_log.jsonl")

# Patch ShellManager.write to log PTY input
_orig_write = ShellManager.write

async def _logged_write(self, session_id, data):
    entry = {
        "ts": time.time(),
        "dir": "input",
        "session": session_id,
        "bytes": list(data),
        "text": data.decode("utf-8", errors="replace"),
        "hex": data.hex(),
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[PTY←JS] session={session_id} {repr(data.decode('utf-8', errors='replace'))}")
    return await _orig_write(self, session_id, data)

ShellManager.write = _logged_write

# Patch on_output callback to log PTY output
_orig_create = ShellManager.create

async def _logged_create(self, **kwargs):
    orig_on_output = kwargs.get("on_output")
    session_id = kwargs.get("session_id", "?")

    async def logged_on_output(sid, data):
        entry = {
            "ts": time.time(),
            "dir": "output",
            "session": sid,
            "bytes": list(data[:200]),
            "text": data.decode("utf-8", errors="replace")[:200],
            "hex": data[:200].hex(),
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        text_preview = data.decode("utf-8", errors="replace")[:80].replace("\n", "\\n").replace("\r", "\\r")
        print(f"[PTY→JS] session={sid} {repr(text_preview)}")
        if orig_on_output:
            await orig_on_output(sid, data)

    kwargs["on_output"] = logged_on_output
    return await _orig_create(self, **kwargs)

ShellManager.create = _logged_create


async def main():
    # 清空日志
    with open(LOG_FILE, "w") as f:
        pass

    manager = ShellManager()
    server = ShellServer(manager, node_name="ime-test", port=PORT, bind_host="127.0.0.1")
    await server.start()
    print(f"")
    print(f"  IME test shell server: ws://localhost:{PORT}")
    print(f"  PTY log: {LOG_FILE}")
    print(f"  Press Ctrl+C to stop")
    print(f"")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped")
