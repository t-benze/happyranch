"""Daemon-credential-provider seam (contract §6.1 step 8, §7
local_daemon_bearer).

The connector reads the daemon bearer only at the final hop. Missing,
unreadable, empty, or loosely-permissioned token files fail closed. The
bearer never appears in remote input/output, logs, diagnostics, exceptions,
process arguments, fixtures, or any non-loopback hop.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Protocol


class CredentialUnavailable(Exception):
    """Raised when the daemon bearer cannot be safely read."""


class DaemonCredentialProvider(Protocol):
    def read_bearer(self) -> str: ...


class StaticDaemonCredentialProvider:
    """A fixed bearer for tests/harness (never a production value)."""

    def __init__(self, bearer: str) -> None:
        self._bearer = bearer

    def read_bearer(self) -> str:
        return self._bearer


class FileDaemonCredentialProvider:
    """Reads the daemon token file (existing home-only protected file).

    Fails closed when the file is missing, unreadable, empty, or readable by
    group/other (owner-only ``0600`` contract).
    """

    def __init__(self, token_path: Path) -> None:
        self._token_path = Path(token_path)

    def read_bearer(self) -> str:
        path = self._token_path
        if not path.is_file():
            raise CredentialUnavailable("daemon token file missing")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise CredentialUnavailable("daemon token file unreadable") from exc
        if mode & 0o077:
            raise CredentialUnavailable("daemon token file permissions too loose")
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CredentialUnavailable("daemon token file unreadable") from exc
        if not token:
            raise CredentialUnavailable("daemon token file empty")
        return token
