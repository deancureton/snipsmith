"""platform-specific base directories."""

import os
from pathlib import Path
import sys


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def app_data_root() -> Path:
    """where electron apps keep per-user data."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support"
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"])
    return xdg_config_home()
