"""Where Dogspeak keeps things on each operating system. Stdlib only.

This is the one module that decides by platform; the rest of the code asks it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


def venv_python(root=ROOT, windowed=False):
    """The project environment's Python. windowed picks pythonw.exe on Windows."""
    if WINDOWS:
        return Path(root) / ".venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return Path(root) / ".venv" / "bin" / "python"


def data_dir():
    """Drafts, the database and recordings in progress."""
    override = os.environ.get("DOGSPEAK_DATA_DIR")
    if override:
        return Path(override)
    if WINDOWS:
        return _local_appdata() / "Dogspeak"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "dogspeak"


def state_dir():
    """Logs."""
    if WINDOWS:
        return _local_appdata() / "Dogspeak" / "logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dogspeak"


def _local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def no_window_flags():
    """creationflags that keep a console child of a GUI app from opening a console window."""
    return getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
