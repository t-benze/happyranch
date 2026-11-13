"""Daemon-credential-provider seam (contract §6.1 step 8, §7
local_daemon_bearer).

The connector reads the daemon bearer only at the final hop. Missing,
unreadable, empty, or loosely-permissioned token files fail closed. The
bearer never appears in remote input/output, logs, diagnostics, exceptions,
process arguments, fixtures, or any non-loopback hop.
"""
from __future__ import annotations

import os
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


class DarwinFileDaemonCredentialProvider(FileDaemonCredentialProvider):
    """Same-user macOS read with no-follow, inode/owner/mode and size checks."""

    def read_bearer(self) -> str:
        from runtime.remote_access.launchd_files import read_private_file

        try:
            data = read_private_file(self._token_path.expanduser().absolute(), exact_mode=False)
            token = data.decode("utf-8").strip()
            if not token:
                raise ValueError("empty daemon token")
            return token
        except (OSError, ValueError, UnicodeError):
            raise CredentialUnavailable("macOS daemon token unavailable or unsafe") from None


def read_darwin_enrollment(path: Path) -> bytearray:
    """C5/C6 handoff: literal single-link current-euid 0600 key, <=64 KiB.

    Returns bytes like the Linux installer reader; no decoding, staging or
    enrollment side effect. Callers own the returned key's lifetime.
    """
    from runtime.remote_access.launchd_files import read_private_file

    try:
        return read_private_file(Path(path), exact_mode=True)
    except (OSError, ValueError):
        raise ValueError("macOS enrollment key unavailable or unsafe (requires current-user single-link regular 0600 file, nonempty, at most 64 KiB)") from None


class SystemdCredentialProvider:
    """Reads the daemon bearer from systemd's ``LoadCredential=`` injection
    (``$CREDENTIALS_DIRECTORY/daemon.token``).

    Least-privilege posture (THR-097 phase unit 3): the connector service user
    never needs direct read access to the daemon home — systemd copies the
    daemon token file into a 0600 runtime directory at unit start and the
    service reads only that copy. Fails closed exactly like the file provider
    when the credential is missing, unreadable, empty, loosely permissioned,
    or a symlink.
    """

    CREDENTIAL_NAME = "daemon.token"

    def __init__(self, credentials_directory: str | os.PathLike | None = None) -> None:
        self._credentials_directory = (
            os.environ.get("CREDENTIALS_DIRECTORY")
            if credentials_directory is None
            else os.fspath(credentials_directory)
        )

    def read_bearer(self) -> str:
        if not self._credentials_directory:
            raise CredentialUnavailable(
                "CREDENTIALS_DIRECTORY not set (not running under LoadCredential=)"
            )
        path = Path(self._credentials_directory) / self.CREDENTIAL_NAME
        if not path.is_file():
            raise CredentialUnavailable("daemon credential not injected")
        if path.is_symlink():
            raise CredentialUnavailable("daemon credential must not be a symlink")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise CredentialUnavailable("daemon credential unreadable") from exc
        if mode & 0o077:
            raise CredentialUnavailable("daemon credential permissions too loose")
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CredentialUnavailable("daemon credential unreadable") from exc
        if not token:
            raise CredentialUnavailable("daemon credential empty")
        return token
