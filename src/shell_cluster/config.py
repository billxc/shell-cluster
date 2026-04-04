"""Configuration management for shell-cluster."""

from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

CONFIG_DIR = Path(user_config_dir("shell-cluster"))
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class NodeConfig:
    name: str = field(default_factory=socket.gethostname)
    label: str = "shellcluster"
    port: int = 8765


@dataclass
class TunnelConfig:
    backend: str = "devtunnel"
    expiration: str = "8h"


@dataclass
class DiscoveryConfig:
    interval_seconds: int = 30
    manual_peers: list[str] = field(default_factory=list)


@dataclass
class ShellConfig:
    command: str = ""  # empty = $SHELL or /bin/sh


@dataclass
class Config:
    node: NodeConfig = field(default_factory=NodeConfig)
    tunnel: TunnelConfig = field(default_factory=TunnelConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)

    def get_shell_command(self) -> str:
        if self.shell.command:
            return self.shell.command
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
    if "discovery" in data:
        for k, v in data["discovery"].items():
            if hasattr(config.discovery, k):
                setattr(config.discovery, k, v)
    if "shell" in data:
        for k, v in data["shell"].items():
            if hasattr(config.shell, k):
                setattr(config.shell, k, v)
    return config


def save_config(config: Config) -> None:
    """Save config to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "node": asdict(config.node),
        "tunnel": asdict(config.tunnel),
        "discovery": asdict(config.discovery),
        "shell": asdict(config.shell),
    }
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)
