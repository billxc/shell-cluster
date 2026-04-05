"""Data models for shell-cluster."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


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


@dataclass
class Peer:
    """A discovered remote machine."""

    name: str
    tunnel_id: str
    port: int = 0  # remote port on the peer's tunnel
    forwarding_uri: str = ""
    status: PeerStatus = PeerStatus.OFFLINE
    sessions: list[ShellSession] = field(default_factory=list)
