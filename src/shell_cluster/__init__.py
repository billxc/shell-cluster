"""Shell Cluster - Remote access to all your shells across machines via tunnels."""

import json
import subprocess
from pathlib import Path

__version__ = "0.1.0"


def get_git_hash() -> str:
    """Get short git commit hash from install metadata or git."""
    # 1. Try direct_url.json (present for git-based pip/uv installs)
    try:
        dist_info = Path(__file__).parent.parent / f"shell_cluster-{__version__}.dist-info" / "direct_url.json"
        if dist_info.is_file():
            data = json.loads(dist_info.read_text())
            commit = data.get("vcs_info", {}).get("commit_id", "")
            if commit:
                return commit[:7]
    except Exception:
        pass

    # 2. Try git (development checkout)
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=Path(__file__).parent,
        ).strip()
    except Exception:
        pass

    return "unknown"


def get_version_string() -> str:
    return f"{__version__} (git: {get_git_hash()})"
