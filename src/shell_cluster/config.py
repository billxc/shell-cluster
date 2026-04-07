"""Configuration management for shell-cluster."""

from __future__ import annotations

import os
import socket
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

CONFIG_DIR = Path(user_config_dir("shell-cluster"))
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class NodeConfig:
    name: str = field(default_factory=socket.gethostname)  # Node name, shown in peers/dashboard
    label: str = "shellcluster"  # Tunnel label for peer discovery (same label = same cluster)
    dashboard_port: int = 9000  # Dashboard HTTP server port
    dashboard: bool = True  # Start dashboard server on daemon startup


@dataclass
class TunnelConfig:
    backend: str = "devtunnel"  # Tunnel backend: "devtunnel"
    expiration: str = ""  # Tunnel expiration (empty = devtunnel default 30d)


@dataclass
class PeerConfig:
    """A manually configured peer for LAN/direct connections."""
    name: str = ""  # Display name
    uri: str = ""  # WebSocket URI, e.g. ws://192.168.1.10:8765


@dataclass
class ShellConfig:
    command: str = ""  # Default shell. Empty = auto-detect ($SHELL on Unix, pwsh/powershell/cmd on Windows)


@dataclass
class Config:
    node: NodeConfig = field(default_factory=NodeConfig)
    tunnel: TunnelConfig = field(default_factory=TunnelConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    peers: list[PeerConfig] = field(default_factory=list)

    def get_shell_command(self) -> str:
        if self.shell.command:
            return self.shell.command
        if sys.platform == "win32":
            # Prefer pwsh (PowerShell 7+) over legacy powershell.exe
            import shutil

            if shutil.which("pwsh"):
                return "pwsh.exe"
            return os.environ.get("COMSPEC", "powershell.exe")
        return os.environ.get("SHELL", "/bin/sh")


def load_config() -> Config:
    """Load config from file, creating defaults if not exists."""
    if not CONFIG_FILE.exists():
        config = Config()
        save_config(config)
        return config

    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)

    config = Config()
    if "node" in data:
        for k, v in data["node"].items():
            if hasattr(config.node, k):
                setattr(config.node, k, v)
    if "tunnel" in data:
        for k, v in data["tunnel"].items():
            if hasattr(config.tunnel, k):
                setattr(config.tunnel, k, v)
    if "shell" in data:
        for k, v in data["shell"].items():
            if hasattr(config.shell, k):
                setattr(config.shell, k, v)
    if "peers" in data:
        for p in data["peers"]:
            if isinstance(p, dict) and "uri" in p:
                config.peers.append(PeerConfig(
                    name=p.get("name", ""),
                    uri=p["uri"],
                ))
    return config


def save_config(config: Config) -> None:
    """Save config to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "node": asdict(config.node),
        "tunnel": asdict(config.tunnel),
        "shell": asdict(config.shell),
    }
    if config.peers:
        data["peers"] = [{"name": p.name, "uri": p.uri} for p in config.peers]
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)
