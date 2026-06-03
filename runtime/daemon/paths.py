"""Locations under ``~/.happyranch/`` for daemon lifecycle state."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

# The CLI-needed locators (daemon_home, port_file, token_file, read_token) live
# in runtime.runtime so the top-level cli/ package can import them without
# reaching into runtime.daemon. They are re-exported here so the existing
# daemon call sites (``from runtime.daemon import paths; paths.port_file()``,
# etc.) keep working unchanged.
from runtime.runtime import daemon_home, port_file, read_token, token_file

__all__ = [
    "daemon_home",
    "port_file",
    "token_file",
    "read_token",
    "ensure_daemon_home",
    "pid_file",
    "log_file",
    "runtimes_file",
    "ensure_token",
]


def ensure_daemon_home() -> Path:
    home = daemon_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def pid_file() -> Path:
    return daemon_home() / "daemon.pid"


def log_file() -> Path:
    return daemon_home() / "daemon.log"


def runtimes_file() -> Path:
    return daemon_home() / "runtimes.yaml"


def ensure_token() -> str:
    """Return the daemon's auth token, generating it on first call.

    Writes the token with ``0600`` perms.
    """
    path = token_file()
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return token
