"""Native-owned, installation-local language preference; stdlib only.

Closed API: load() and current() lazily initialize once and return a fresh
{"current": "en" | "zh-CN", "persisted": bool} snapshot. set(value) returns
the same shape, retaining a session-only choice if persistence fails. Invalid
values raise ValueError without changing memory or disk. Repeated load() never
discards a session-only choice; restart the process to re-read saved state.

The file is $XDG_CONFIG_HOME/happyranch/locale (absolute XDG path only), else
~/.config/happyranch/locale: ASCII en or zh-CN, with an optional final newline.
Missing/invalid/unsafe/unreadable files fall back to the unchanged LANG rule:
LANG.lower().startswith("zh") -> zh-CN, otherwise en. Reads are bounded to 64
bytes, nonblocking, regular-file only, with no symlink traversal. Writes use a
private same-directory temporary file, flush/fsync, atomic replace and directory
fsync. A post-replace sync failure can leave new bytes visible, but returns
persisted=False because durability was not confirmed.

One native process owns this module with stable environment after first access.
A single lock covers state AND disk I/O: threaded calls are linearized in lock
acquisition order, so an older write cannot complete after a newer write. There
is no cross-process ordering guarantee. Callers receive snapshots, not shared
mutable state. This module is not wired into the shell yet; later wiring owns
live web translation and native menus/dialogs taking effect at next launch.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import threading

_lock = threading.Lock()
_state: dict[str, str | bool] | None = None
_limit = 64


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
        for index, component in enumerate(path.parts[1:], 1):
            if component == "..":
                raise ValueError("nonliteral config directory")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    if index == len(path.parts) - 1:
                        # An earlier creation may have failed to sync this entry.
                        os.fsync(fd)
                else:
                    os.fsync(fd)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read() -> str:
    directory = _open_directory(False)
    try:
        fd = os.open("locale", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > _limit:
                raise ValueError("invalid locale file")
            raw = stream.read(_limit + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) != before.st_size or len(raw) > _limit
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError("locale file changed")
            if raw not in (b"en", b"en\n", b"zh-CN", b"zh-CN\n"):
                raise ValueError("unknown locale")
            return raw.decode("ascii").rstrip("\n")
    finally:
        os.close(directory)


def _write(value: str) -> None:
    directory = _open_directory(True)
    created = False
    try:
        pending = ".locale-" + secrets.token_hex(16)
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write((value + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, "locale", src_dir_fd=directory, dst_dir_fd=directory)
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
    global _state
    if _state is None:
        try:
            value, persisted = _read(), True
        except (OSError, ValueError, RuntimeError):
            value = "zh-CN" if os.environ.get("LANG", "").lower().startswith("zh") else "en"
            persisted = False
        _state = {"current": value, "persisted": persisted}
    return dict(_state)


def load() -> dict[str, str | bool]:
    """Initialize once from disk/LANG, or return the existing session snapshot."""
    with _lock:
        return _load_locked()


def current() -> dict[str, str | bool]:
    """Return a fresh current snapshot, initializing on first access."""
    return load()


def set(value: str) -> dict[str, str | bool]:
    """Choose en/zh-CN; raise ValueError for anything else, without effects."""
    global _state
    if type(value) is not str or value not in ("en", "zh-CN"):
        raise ValueError("locale must be 'en' or 'zh-CN'")
    with _lock:
        _state = {"current": value, "persisted": False}
        try:
            _write(value)
        except (OSError, ValueError, RuntimeError):
            pass
        else:
            _state["persisted"] = True
        return dict(_state)
