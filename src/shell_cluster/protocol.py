"""WebSocket communication protocol for shell-cluster.

All messages are JSON text frames multiplexed over a single WebSocket connection.
Terminal data is base64-encoded within JSON to avoid encoding issues.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# Message types
class MsgType:
    # Shell lifecycle
    SHELL_CREATE = "shell.create"
    SHELL_CREATED = "shell.created"
    SHELL_ATTACH = "shell.attach"
    SHELL_ATTACHED = "shell.attached"
    SHELL_DATA = "shell.data"
    SHELL_RESIZE = "shell.resize"
    SHELL_CLOSE = "shell.close"
    SHELL_CLOSED = "shell.closed"

    # Session management
    SHELL_LIST = "shell.list"
    SHELL_LIST_RESPONSE = "shell.list.response"

    # Peer info
    PEER_INFO = "peer.info"

    # Errors
    ERROR = "error"


@dataclass
class Message:
    type: str
    session_id: str = ""
    data: str = ""  # base64-encoded terminal data
    shell: str = ""
    cols: int = 0
    rows: int = 0
    name: str = ""
    sessions: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_json(self) -> str:
        d = {k: v for k, v in asdict(self).items() if v or k == "type"}
        return json.dumps(d)

    @classmethod
    def from_json(cls, text: str) -> Message:
        d = json.loads(text)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def make_shell_create(shell: str = "", cols: int = 80, rows: int = 24) -> Message:
    return Message(
        type=MsgType.SHELL_CREATE,
        session_id=uuid.uuid4().hex[:12],
        shell=shell,
        cols=cols,
        rows=rows,
    )


def make_shell_data(session_id: str, data: bytes) -> Message:
    return Message(
        type=MsgType.SHELL_DATA,
        session_id=session_id,
        data=base64.b64encode(data).decode(),
    )


def make_shell_resize(session_id: str, cols: int, rows: int) -> Message:
    return Message(
        type=MsgType.SHELL_RESIZE,
        session_id=session_id,
        cols=cols,
        rows=rows,
    )


def make_shell_close(session_id: str) -> Message:
    return Message(type=MsgType.SHELL_CLOSE, session_id=session_id)


def make_shell_list() -> Message:
    return Message(type=MsgType.SHELL_LIST)


def make_shell_list_response(sessions: list[dict[str, Any]]) -> Message:
    return Message(type=MsgType.SHELL_LIST_RESPONSE, sessions=sessions)


def make_shell_created(session_id: str, shell: str) -> Message:
    return Message(type=MsgType.SHELL_CREATED, session_id=session_id, shell=shell)


def make_shell_closed(session_id: str) -> Message:
    return Message(type=MsgType.SHELL_CLOSED, session_id=session_id)


def make_shell_attach(session_id: str, cols: int = 80, rows: int = 24) -> Message:
    return Message(type=MsgType.SHELL_ATTACH, session_id=session_id, cols=cols, rows=rows)


def make_shell_attached(session_id: str, shell: str) -> Message:
    return Message(type=MsgType.SHELL_ATTACHED, session_id=session_id, shell=shell)


def make_peer_info(name: str, sessions: list[dict[str, Any]]) -> Message:
    return Message(type=MsgType.PEER_INFO, name=name, sessions=sessions)


def make_error(error: str, session_id: str = "") -> Message:
    return Message(type=MsgType.ERROR, error=error, session_id=session_id)


def decode_shell_data(msg: Message) -> bytes:
    """Decode base64 terminal data from a shell.data message."""
    return base64.b64decode(msg.data)
