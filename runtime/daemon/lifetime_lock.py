"""One daemon per home, with conservative previous-boot evidence.

Never unlink/replace daemon.lock: flock protects the open inode. The marker
assumes pre-lock binaries are not subsequently run against this same home.
This is a cooperating-daemon fence, not isolation from the same OS user.
"""
from __future__ import annotations

import errno
import fcntl
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

_MARKER = b"happyranch-daemon-lock@1\n"


@dataclass(frozen=True)
class LockProof:
    """Only records already present at acquisition can be previous-boot work.

    A held lock excludes earlier lock holders. On transition boots it does
    not exclude a pre-lock daemon without a positive dead-PID observation.
    The narrow thread startup pass consumes this proof together with recorded
    lifecycle quiescence; it grants no record release by itself.
    """

    proven: bool
    transition_boot: bool
    reason: str
    previous_pid: int | None


@dataclass(frozen=True)
class DaemonLock:
    # Intentionally retained until process death, including main's error tails.
    # Non-inheritable across exec; never hand this descriptor to a child.
    fd: int
    proof: LockProof


def _read_pid(path: Path) -> int | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_size > 64):
                return None
            raw = os.read(fd, 65).strip()
            if not raw or len(raw) > 64 or not raw.isdigit() or raw.startswith(b"0"):
                return None
            pid = int(raw)
            return pid if pid <= 2**31 - 1 else None
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def _proof(lock_era: bool, pid: int | None) -> LockProof:
    if lock_era:
        return LockProof(True, False, "earlier_lock_holders_gone", pid)
    if pid is None:
        return LockProof(False, True, "previous_pid_unavailable", None)
    if pid == os.getpid():
        return LockProof(False, True, "previous_pid_is_self", pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return LockProof(True, True, "previous_pid_confirmed_dead", pid)
    except OSError:
        return LockProof(False, True, "previous_pid_probe_ambiguous", pid)
    return LockProof(False, True, "previous_pid_alive", pid)


def _previous_pid(home: Path) -> int | None:
    # Prefer the serving PID, including any unsafe/unreadable evidence. Only
    # genuine absence permits the lock-holder sidecar retained across clean stop.
    path = home / "daemon.pid"
    try:
        path.lstat()
    except FileNotFoundError:
        return _read_pid(home / "daemon.lock.pid")
    except OSError:
        return None
    return _read_pid(path)


def record_daemon_pid(home: Path) -> None:
    """Normal main's lock owner records its PID before serving publication.

    Keep this separate from daemon.pid so clean shutdown can remove serving
    markers without losing the next boot's dead-PID probe target. Replace only
    the sidecar, never the flock inode; a partial write must not become a PID.
    """
    fd, pending = tempfile.mkstemp(prefix=".daemon.lock.pid-", dir=home)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(f"{os.getpid()}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, home / "daemon.lock.pid")
    finally:
        Path(pending).unlink(missing_ok=True)


def acquire_daemon_lock(home: Path) -> DaemonLock:
    """Acquire before DB/token/settings/sweep/port/PID publication.

    O_NOFOLLOW refuses even a dangling symlink. A NOT PROVEN transition must
    not publish the marker: otherwise its uncertainty would vanish next boot.
    The caller keeps the returned fd open for the entire process lifetime.
    """
    path = home / "daemon.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()):
            raise OSError(errno.EINVAL, "daemon lock must be an owned regular file without links", path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            pid = _read_pid(home / "daemon.pid")
            raise OSError(exc.errno,
                          "another daemon is already running on this runtime; "
                          f"daemon.pid={pid if pid is not None else 'unreadable'}", path) from None
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise OSError(errno.EINVAL, "daemon lock changed during acquisition", path)
        os.fchmod(fd, 0o600)
        lock_era = os.pread(fd, len(_MARKER) + 1, 0) == _MARKER
        proof = _proof(lock_era, _previous_pid(home))
        if proof.proven and not lock_era:
            if os.pwrite(fd, _MARKER, 0) != len(_MARKER):
                raise OSError(errno.EIO, "incomplete daemon lock marker", path)
            os.ftruncate(fd, len(_MARKER))
            os.fsync(fd)
        return DaemonLock(fd, proof)
    except BaseException:
        os.close(fd)
        raise
