"""Shell Cluster - Remote access to all your shells across machines via tunnels."""

import subprocess

__version__ = "0.1.0"


def get_git_hash() -> str:
    """Get short git commit hash, or 'unknown' if not in a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def get_version_string() -> str:
    return f"{__version__} (git: {get_git_hash()})"
