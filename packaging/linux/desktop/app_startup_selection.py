"""Installation-local startup selection; contains only local/unsaved/opaque ref.

Uses app_locale.py's bounded no-follow reads, locked atomic write and directory
fsync. No hosts, names or credentials are persisted. Invalid records suppress
local startup; only an absent record selects local by default.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import secrets
import stat
import threading

_lock = threading.Lock()
_state: dict[str, str | bool] | None = None
_confirmed: str | None = None
_limit = 128


def _valid(value: object) -> bool:
    return type(value) is str and (value in ("local", "remote:unsaved")
        or re.fullmatch(r"remote:[0-9a-f]{64}", value) is not None)


def _config_directory() -> Path:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    base = xdg if xdg.is_absolute() else Path.home() / ".config"
    return base / "happyranch"


def _open_directory(create: bool) -> int:
    # Copy the desktop descriptor-relative O_NOFOLLOW traversal convention;
    # do not import session/window modules with GTK or credential dependencies.
    path = _config_directory()
    if not path.is_absolute():
        raise ValueError("nonabsolute config directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:]:
            if component == "..":
                raise ValueError("nonliteral config directory")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            try:
                if create:
                    # Any entry may survive an earlier failed creation sync.
                    os.fsync(fd)
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read() -> str:
    directory = _open_directory(False)
    try:
        fd = os.open("startup-selection", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > _limit:
                raise ValueError("invalid startup selection file")
            raw = stream.read(_limit + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) != before.st_size or len(raw) > _limit
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError("startup selection file changed")
            if not _valid(raw.decode("ascii").removesuffix("\n")):
                raise ValueError("unknown startup selection")
            return raw.decode("ascii").rstrip("\n")
    finally:
        os.close(directory)


def _write(value: str) -> None:
    directory = _open_directory(True)
    created = False
    try:
        pending = ".startup-selection-" + secrets.token_hex(16)
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write((value + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, "startup-selection", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            if created:
                try:
                    os.unlink(pending, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)


def _load_locked() -> dict[str, str | bool]:
    global _state, _confirmed
    if _state is None:
        try:
            value, persisted = _read(), True
        except FileNotFoundError:
            value, persisted = "local", True
        except (OSError, ValueError, RuntimeError, UnicodeError):
            value, persisted = "remote:unsaved", False
        _state = {"current": value, "persisted": persisted}
        _confirmed = value if persisted else None
    return dict(_state)


def load() -> dict[str, str | bool]:
    """Initialize once; absent selects local, invalid/unreadable suppresses start."""
    with _lock:
        return _load_locked()


def current() -> dict[str, str | bool]:
    """Return a fresh current snapshot, initializing on first access."""
    return load()


def set(value: str) -> dict[str, str | bool]:
    """Record only confirmed selection; failure is visible without undoing commit."""
    if not _valid(value):
        raise ValueError("invalid startup selection")
    with _lock:
        _load_locked()
        return _set_locked(value)


def _set_locked(value: str) -> dict[str, str | bool]:
    global _state, _confirmed
    _state = {"current": value, "persisted": False}
    try:
        _write(value)
    except (OSError, ValueError, RuntimeError):
        pass
    else:
        _state["persisted"] = True
        _confirmed = value
    return dict(_state)


def clear_reference(ref: str) -> dict[str, str | bool]:
    """Remove/Revoke clears either the session or last confirmed matching ref."""
    with _lock:
        current = _load_locked()
        if "remote:" + ref in (current["current"], _confirmed):
            return _set_locked("local")
        return current
