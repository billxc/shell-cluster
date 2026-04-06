"""Data models for shell-cluster."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

# Max scrollback buffer: 64KB
SCROLLBACK_MAX_BYTES = 64 * 1024


class PeerStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"


@dataclass
class TunnelInfo:
    """Information about a tunnel instance."""

    tunnel_id: str
    labels: list[str] = field(default_factory=list)
    port: int = 0
    description: str = ""
    forwarding_uri: str = ""
    hosting: bool = False  # True if a host process is connected


@dataclass
class ShellSession:
    """A single PTY shell session on a machine."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    shell: str = ""  # e.g. "zsh", "bash", "powershell"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pid: int = 0
    # Platform-specific handle: fd on Unix, winpty object on Windows
    _handle: object = field(default=None, repr=False)
    # Ring buffer of recent output for replay on reconnect
    _scrollback: deque = field(default_factory=deque, repr=False)
    _scrollback_size: int = field(default=0, repr=False)

    def append_output(self, data: bytes) -> None:
        """Append output to scrollback buffer, evicting oldest if over limit."""
        self._scrollback.append(data)
        self._scrollback_size += len(data)
        while self._scrollback_size > SCROLLBACK_MAX_BYTES and self._scrollback:
            evicted = self._scrollback.popleft()
            self._scrollback_size -= len(evicted)

    def get_scrollback(self) -> bytes:
        """Return full scrollback buffer as bytes."""
        return b"".join(self._scrollback)


@dataclass
class Peer:
    """A discovered remote machine."""

    name: str
    tunnel_id: str
    port: int = 0  # remote port on the peer's tunnel
    forwarding_uri: str = ""
    status: PeerStatus = PeerStatus.OFFLINE
    sessions: list[ShellSession] = field(default_factory=list)
