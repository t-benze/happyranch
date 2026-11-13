"""Private Linux/Darwin filesystem observations for THR307; no legacy cleanup policy.

Paths are walked from / with O_NOFOLLOW, then all tree operations are dir-fd
relative. No chmod follows symlinks. Mountpoints (including same-device bind
mounts) refuse. POSIX pathname syscalls cannot fence a hostile same-UID rename
between a final identity check and unlink; filesystem observations are not a writer
lock. The daemon supervisor bounds the owning process group, not a thread.
"""
from __future__ import annotations

import ctypes as ct
import os
import platform
import re
import stat
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any


# Observation is invocation-local and has no authority over the operation. In
# particular, neither clock nor observer failure may escape into a safety gate.
_observer = ContextVar('disk_sweep_observer', default=None)


def observe(event: str, phase: str = '', **values: Any) -> None:
    observer = _observer.get()
    if observer is None:
        return
    try:
        observer.record(event, phase, **values)
    except Exception as exc:
        try:
            observer.errors['count'] += 1
            if len(observer.errors['types']) < 8:
                observer.errors['types'].append(type(exc).__name__[:64])
        except Exception:
            pass


def measurement_clock() -> float | None:
    try:
        return time.perf_counter()
    except Exception as exc:
        observe('measurement_error', error=type(exc).__name__)
        return None


def measurement_elapsed(started: float | None) -> float | None:
    try:
        ended = measurement_clock()
        return max(0, ended - started) if started is not None and ended is not None else None
    except Exception as exc:
        observe('measurement_error', error=type(exc).__name__)
        return None


@contextmanager
def observing(event: str, *args: Any, **kwargs: Any) -> Iterator[None]:
    started = measurement_clock()
    observe(event, 'start', args=args, kwargs=kwargs)
    error = None
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        observe(event, 'finish', args=args, kwargs=kwargs, error=error,
                duration=measurement_elapsed(started))


def observed(event: str) -> Callable:
    """Time existing calls, including unsuccessful ones, without extra I/O."""
    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def call(*args: Any, **kwargs: Any) -> Any:
            started = measurement_clock()
            observe(event, 'start', args=args, kwargs=kwargs)
            value, error = None, None
            try:
                value = function(*args, **kwargs)
                return value
            except BaseException as exc:
                error = exc
                raise
            finally:
                observe(event, 'finish', args=args, kwargs=kwargs, value=value,
                        error=error, duration=measurement_elapsed(started))
        return call
    return decorate


class Retain(RuntimeError):
    """A failed observation is retention, never negative evidence."""


class Expired(RuntimeError):
    pass


def check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise Expired("total sweep deadline")


def identity(s: os.stat_result) -> tuple[int, int, int]:
    return s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode)


def signature(s: os.stat_result) -> tuple:
    return identity(s) + (s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)


def trees_equal(left: dict[str, os.stat_result], right: dict[str, os.stat_result], deadline: float) -> bool:
    """Compare complete captured trees with constant additional storage.

    Mismatch does not skip later work or its original-deadline checks. A pair
    already in progress is not independently preemptible.
    """
    check(deadline)
    equal = len(left) == len(right)
    for path, observed in left.items():
        check(deadline)
        partner = right.get(path)
        if partner is None or signature(observed) != signature(partner):
            equal = False
    check(deadline)
    return equal


def names(fd: int, deadline: float) -> list[str]:
    result = []
    with os.scandir(fd) as entries:
        while True:
            check(deadline)
            try:
                result.append(next(entries).name)
            except StopIteration:
                break
    return sorted(result)


@contextmanager
def directory(path: Path, deadline: float):
    """Open every absolute component without following symlinks."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            check(deadline)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


_DARWIN_MOUNT_LOCK = Lock()


class _DarwinStatfs(ct.Structure):
    """Public LP64 statfs ABI pinned to XNU xnu-11417.101.15 bsd/sys/mount.h.

    https://github.com/apple-oss-distributions/xnu/blob/xnu-11417.101.15/bsd/sys/mount.h
    __DARWIN_STRUCT_STATFS64, MAXPATHLEN=1024, fsid_t=int32_t[2].
    """

    _fields_ = [
        ('bsize', ct.c_uint32), ('iosize', ct.c_int32),
        ('blocks', ct.c_uint64), ('bfree', ct.c_uint64),
        ('bavail', ct.c_uint64), ('files', ct.c_uint64), ('ffree', ct.c_uint64),
        ('fsid', ct.c_int32 * 2), ('owner', ct.c_uint32),
        ('type', ct.c_uint32), ('flags', ct.c_uint32), ('subtype', ct.c_uint32),
        ('fstype', ct.c_char * 16), ('mountpoint', ct.c_char * 1024),
        ('source', ct.c_char * 1024), ('flags_ext', ct.c_uint32),
        ('reserved', ct.c_uint32 * 7),
    ]


def _darwin_mounts(deadline: float) -> set[str]:
    """Bounded native evidence; no procfs, shell parsing or silent ABI fallback."""
    check(deadline)
    offsets = dict(bsize=0, iosize=4, blocks=8, bfree=16, bavail=24,
                   files=32, ffree=40, fsid=48, owner=56, type=60, flags=64,
                   subtype=68, fstype=72, mountpoint=88, source=1112,
                   flags_ext=2136, reserved=2140)
    machine = platform.machine()
    if (machine not in ('arm64', 'x86_64') or ct.sizeof(ct.c_void_p) != 8
            or ct.sizeof(_DarwinStatfs) != 2168 or ct.alignment(_DarwinStatfs) != 8
            or any(getattr(_DarwinStatfs, field).offset != offset for field, offset in offsets.items())):
        raise Retain('unknown_mount_evidence')

    def decode(value: _DarwinStatfs) -> str:
        raw = bytes(value)
        strings = []
        for field, width in (('fstype', 16), ('mountpoint', 1024), ('source', 1024)):
            check(deadline)
            start = offsets[field]
            content, nul, _ = raw[start:start + width].partition(b'\0')
            if not content or not nul:
                raise Retain('unknown_mount_evidence')
            strings.append(os.fsdecode(content))
        mountpoint = strings[1]
        if (value.bsize == 0 or not mountpoint.startswith('/')
                or mountpoint.startswith('//') or os.path.normpath(mountpoint) != mountpoint):
            raise Retain('unknown_mount_evidence')
        return mountpoint

    try:
        libc = ct.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        suffix = '' if machine == 'arm64' else '$INODE64'
        getmntinfo = getattr(libc, 'getmntinfo' + suffix)
        getmntinfo.argtypes = [ct.POINTER(ct.POINTER(_DarwinStatfs)), ct.c_int]
        getmntinfo.restype = ct.c_int
        entries = ct.POINTER(_DarwinStatfs)()
        # Serialize this reader's use of getmntinfo's process-global buffer.
        if not _DARWIN_MOUNT_LOCK.acquire(timeout=max(0, deadline - time.monotonic())):
            check(deadline)
            raise Retain('unknown_mount_evidence')
        try:
            check(deadline)
            count = getmntinfo(ct.byref(entries), 2)  # MNT_NOWAIT in the pinned header
            check(deadline)
            if not entries or not 0 < count <= 4096:
                raise Retain('unknown_mount_evidence')
            captured = ct.string_at(entries, count * ct.sizeof(_DarwinStatfs))
        finally:
            _DARWIN_MOUNT_LOCK.release()
        result = set()
        root = None
        for index in range(count):
            check(deadline)
            value = _DarwinStatfs.from_buffer_copy(captured, index * 2168)
            mountpoint = decode(value)
            result.add(mountpoint)
            if mountpoint == '/':
                root = (tuple(value.fsid), value.type, value.bsize)
        if root is None:
            raise Retain('unknown_mount_evidence')
        # Check the selected ABI against an independent native root observation.
        statfs = getattr(libc, 'statfs' + suffix)
        statfs.argtypes = [ct.c_char_p, ct.POINTER(_DarwinStatfs)]
        statfs.restype = ct.c_int
        value = _DarwinStatfs()
        if statfs(b'/', ct.byref(value)) != 0:
            raise Retain('unknown_mount_evidence')
        check(deadline)
        if decode(value) != '/' or (tuple(value.fsid), value.type, value.bsize) != root:
            raise Retain('unknown_mount_evidence')
        check(deadline)
        return result
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        raise Retain('unknown_mount_evidence') from exc


def mounts(deadline: float) -> set[str]:
    check(deadline)
    if sys.platform == 'darwin':
        return _darwin_mounts(deadline)
    if sys.platform != 'linux':
        raise Retain('unknown_mount_evidence')
    result = set()
    with open('/proc/self/mountinfo', encoding='utf-8', errors='strict') as stream:
        for line in stream:
            check(deadline)
            fields = line.split()
            if len(fields) < 7:
                raise Retain('unknown_mount_evidence')
            result.add(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
    return result


@observed('snapshot')
def snapshot(path: Path, deadline: float, *, scratch: bool = False) -> dict[str, os.stat_result]:
    """Complete no-follow tree stat, including root; ctime AND mtime age.

    ctime detects recent writes/chmods even when mtime was backdated; atime is
    deliberately excluded because this observation itself may update atime.
    Unreadable descendants and special objects refuse the whole candidate.
    Only task scratch admits sockets/FIFOs as metadata-only disposable entries;
    Git metadata and worktree callers retain the strict default.
    """
    mountpoints = mounts(deadline)
    result = {}
    with directory(path, deadline) as root:
        device = os.fstat(root).st_dev

        def walk(fd, relative):
            check(deadline)
            s = os.fstat(fd)
            if not relative:
                observe('entry')
            absolute = str(path / relative) if relative else str(path)
            if s.st_dev != device or absolute in mountpoints:
                raise Retain('filesystem_boundary')
            result[relative] = s
            for name in names(fd, deadline):
                check(deadline)
                rel = f'{relative}/{name}' if relative else name
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                observe('entry')
                if s.st_dev != device or str(path / rel) in mountpoints:
                    raise Retain('filesystem_boundary')
                if stat.S_ISDIR(s.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if identity(os.fstat(child)) != identity(s):
                            raise Retain('identity_changed')
                        walk(child, rel)
                    finally:
                        os.close(child)
                elif (stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode)
                      or scratch and (stat.S_ISSOCK(s.st_mode) or stat.S_ISFIFO(s.st_mode))):
                    result[rel] = s
                else:
                    raise Retain('unknown_special_entry')
        walk(root, '')
    return result


def allocated(tree: dict[str, os.stat_result], seen: set[tuple]) -> tuple[int, int]:
    """Observed allocation and exclusive-link attribution ceiling, inode deduped."""
    occurrences = Counter(identity(s) for s in tree.values())
    total = exclusive = 0
    for s in tree.values():
        key = identity(s)
        if key in seen:
            continue
        seen.add(key)
        amount = s.st_blocks * 512
        total += amount
        if stat.S_ISDIR(s.st_mode) or s.st_nlink == occurrences[key]:
            exclusive += amount
    return total, exclusive


@observed('mutation')
def mutate(path: Path, tree: dict, deadline: float, *, remove: bool, scratch: bool = False) -> dict:
    """One admitted operation: contained permission repair and optional removal.

    The caller refreshes ALL common gates immediately before entry. Each
    syscall rechecks the recorded inode and device. No external-link chmod,
    recursion into links, generic rm or unbounded chmod subprocess is used.
    The scratch-only socket/FIFO policy matches snapshot. Linux O_PATH pins
    metadata and observes it after unlink. Darwin never opens a leaf: a final
    no-follow stat precedes unlink-by-name, with no post-unlink observation.
    Both paths have a non-atomic final-check/unlink window under the same-UID
    threat model. Neither performs endpoint/content I/O or leaf mode repair.
    """
    repairs = {}
    own_inode_updates = {}

    def matches(current: os.stat_result, expected: os.stat_result) -> bool:
        expected_signature = signature(expected)
        update = own_inode_updates.get(identity(expected))
        if sys.platform == 'darwin':
            if update is None:
                return signature(current) == expected_signature
            # Only our own successful unlink permits nlink/ctime movement.
            return signature(current)[:-2] == expected_signature[:-2] and current.st_nlink == update
        return signature(current) == (update if update is not None else expected_signature)

    with directory(path.parent, deadline) as parent:
        check(deadline)
        original = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if signature(original) != signature(tree['']):
            raise Retain('identity_changed_before_mutation')
        mountpoints = mounts(deadline)

        def visit(parent_fd, name, relative):
            check(deadline)
            expected = tree[relative]
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            observe('entry')
            if not matches(current, expected):
                raise Retain('tree_changed_before_mutation')
            if str(path / relative) in mountpoints or current.st_dev != original.st_dev:
                raise Retain('filesystem_boundary')
            if not (stat.S_ISDIR(current.st_mode) or stat.S_ISREG(current.st_mode)
                    or stat.S_ISLNK(current.st_mode)
                    or scratch and (stat.S_ISSOCK(current.st_mode) or stat.S_ISFIFO(current.st_mode))):
                raise Retain('unknown_special_entry')
            if stat.S_ISDIR(current.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    if signature(os.fstat(fd)) != signature(expected):
                        raise Retain('identity_changed')
                    check(deadline)
                    # Repair directories only, through the verified open fd.
                    # Files need no mode changes for unlink.
                    if not current.st_mode & stat.S_IWUSR:
                        os.fchmod(fd, stat.S_IMODE(current.st_mode) | stat.S_IWUSR)
                        repairs[relative] = (signature(os.fstat(fd)), current)
                    for child in names(fd, deadline):
                        rel = f'{relative}/{child}' if relative else child
                        if rel not in tree:
                            raise Retain('new_entry_during_mutation')
                        visit(fd, child, rel)
                    if remove:
                        check(deadline)
                        if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != identity(expected):
                            raise Retain('identity_changed')
                        os.rmdir(name, dir_fd=parent_fd)
                finally:
                    os.close(fd)
            elif remove and sys.platform == 'darwin':
                # Never open ANY Darwin leaf, including regular files and links.
                # The verified parent fd contains this final no-follow check.
                check(deadline)
                try:
                    final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except OSError as exc:
                    raise Retain('identity_changed') from exc
                if not matches(final, expected) or signature(final) != signature(current):
                    raise Retain('identity_changed')
                check(deadline)
                os.unlink(name, dir_fd=parent_fd)
                if final.st_nlink > 1:
                    own_inode_updates[identity(expected)] = final.st_nlink - 1
            elif remove:
                # O_PATH observes the link inode itself, including symlinks;
                # track nlink/ctime changes caused by our own prior unlink so
                # internal hardlinks need neither double counts nor a bypass.
                leaf = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    if signature(os.fstat(leaf)) != signature(current):
                        raise Retain('identity_changed')
                    check(deadline)
                    os.unlink(name, dir_fd=parent_fd)
                    own_inode_updates[identity(expected)] = signature(os.fstat(leaf))
                finally:
                    os.close(leaf)
        visit(parent, path.name, '')
    return repairs
