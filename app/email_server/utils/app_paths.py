"""Per-user application data directory for BriefKorb's runtime state
(OAuth tokens, encrypted caches, entity graph, local rule files)."""

import os
import platform
from pathlib import Path

from .constants import AppInfo


def get_app_data_dir() -> Path:
    """Return the per-user data directory, creating it if needed.

    BRIEFKORB_DATA_DIR overrides the OS default. Defaults:
    Windows ``%LOCALAPPDATA%\\BriefKorb``, macOS
    ``~/Library/Application Support/BriefKorb``, otherwise
    ``$XDG_DATA_HOME/BriefKorb`` (``~/.local/share/BriefKorb``).
    Windows uses the local, non-roaming profile because the encrypted cache's
    key lives in the machine's OS keyring.
    """
    override = os.environ.get("BRIEFKORB_DATA_DIR", "").strip()
    if override:
        data_dir = Path(override)
    else:
        system = platform.system().lower()
        if system == "windows":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        elif system == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        data_dir = Path(base) / AppInfo.APP_IDENTIFIER
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def resolve_data_path(path: str | Path) -> Path:
    """Absolute paths are returned unchanged; relative ones resolve against
    the app data directory."""
    p = Path(path)
    return p if p.is_absolute() else get_app_data_dir() / p


def portable_data_path(path: str | Path) -> str:
    """Inverse of resolve_data_path for writing config: a path inside the
    app data directory becomes relative to it, so the config stays valid if
    the data directory moves (e.g. a different user profile)."""
    p = Path(path)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(get_app_data_dir()))
    except ValueError:
        return str(p)
