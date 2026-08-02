"""Cross-platform default location for Nexolith's persistent state database.

No new dependency (e.g. `platformdirs`) for this -- the convention per
platform is small and stable enough to implement directly, consistent with
the project's existing preference for the standard library when it's
enough.
"""

import os
import sys
from pathlib import Path

_ENV_STATE_DIR = "NEXOLITH_STATE_DIR"
_APP_NAME = "Nexolith"
_DATABASE_FILENAME = "state.db"


def default_state_dir() -> Path:
    """The directory Nexolith's state database lives in by default.

    Honors `NEXOLITH_STATE_DIR` first, for explicit operator/deployment
    control. Otherwise follows each platform's own convention for
    per-application local data: `%LOCALAPPDATA%\\Nexolith` on Windows,
    `~/Library/Application Support/Nexolith` on macOS, and
    `$XDG_DATA_HOME/nexolith` (or `~/.local/share/nexolith`) elsewhere.
    """
    override = os.environ.get(_ENV_STATE_DIR)
    if override:
        return Path(override)

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / _APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_NAME.lower()


def default_database_path() -> Path:
    return default_state_dir() / _DATABASE_FILENAME
