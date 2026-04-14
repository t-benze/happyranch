"""Locations under ``~/.opc/`` for daemon lifecycle state."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

_DEFAULT_HOME = Path.home() / ".opc"


def daemon_home() -> Path:
    """Return the directory the daemon stores its state in.

    Honors the ``OPC_DAEMON_HOME`` environment variable for tests; falls
    back to ``~/.opc/``.
    """
    override = os.environ.get("OPC_DAEMON_HOME")
    return Path(override) if override else _DEFAULT_HOME


def ensure_daemon_home() -> Path:
    home = daemon_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def pid_file() -> Path:
    return daemon_home() / "daemon.pid"


def port_file() -> Path:
    return daemon_home() / "daemon.port"


def token_file() -> Path:
    return daemon_home() / "daemon.token"


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
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return token


def read_token() -> str | None:
    path = token_file()
    if not path.exists():
        return None
    return path.read_text().strip()
