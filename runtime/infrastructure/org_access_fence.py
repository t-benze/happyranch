"""OrgAccessFence@1: cooperative original-descriptor org access intervals.

The stable inode is never replaced/unlinked. Closing one holder never unlocks
an inherited open-file description. EX is exclusion, not native-owner closure
or permission to install. Generic Database deliberately knows nothing of this.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import os
import platform
import re
import stat
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class OrgAccessHeld(ValueError):
    """Admission or original custody is unavailable; no database open follows."""


_attached: dict[Path, OrgAccessFence] = {}
_attachment_lock = threading.RLock()
_uncertain: set[OrgAccessFence] = set()


def org_access_key(root: Path) -> str:
    return hashlib.sha256((str(root.parent.parent) + '\0' + root.name).encode('utf8')).hexdigest()


def bind_org_fence(fence: OrgAccessFence) -> None:
    with _attachment_lock:
        fence.assert_held()
        if fence.root in _attached:
            raise OrgAccessHeld('org_access_attachment_already_owned')
        _attached[fence.root] = fence


def original_org_fence(slug: str) -> OrgAccessFence:
    with _attachment_lock:
        owners = [f for root, f in _attached.items() if root.name == slug]
        if len(owners) != 1:
            raise OrgAccessHeld('org_access_original_owner_unknown')
        owners[0].assert_held()
        return owners[0]


def unbind_org_fence(fence: OrgAccessFence) -> None:
    with _attachment_lock:
        if _attached.get(fence.root) is not fence:
            raise OrgAccessHeld('org_access_original_owner_unknown')
        del _attached[fence.root]


def _directory(path: Path) -> int:
    if not path.is_absolute() or '..' in path.parts or str(path) != os.path.normpath(path):
        raise OrgAccessHeld('org_access_path_invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _local(fd: int) -> None:
    # Native LP64 fstatfs; unknown ABIs and filesystems refuse qualification.
    # Darwin statfs64 offsets match the existing disk-sweeper XNU ABI contract.
    if ctypes.sizeof(ctypes.c_void_p) != 8 or sys.platform not in ('linux', 'darwin'):
        raise OrgAccessHeld('org_access_filesystem_unknown')
    value = ctypes.create_string_buffer(4096)
    if sys.platform == 'darwin':
        machine = platform.machine()
        if machine not in ('arm64', 'x86_64'):
            raise OrgAccessHeld('org_access_filesystem_unknown')
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        call = getattr(libc, 'fstatfs' + ('' if machine == 'arm64' else '$INODE64'))
    else:
        call = ctypes.CDLL(None, use_errno=True).fstatfs
    call.argtypes = (ctypes.c_int, ctypes.c_void_p)
    call.restype = ctypes.c_int
    if call(fd, value) != 0:
        raise OrgAccessHeld('org_access_filesystem_unknown')
    if sys.platform == 'linux':
        kind = ctypes.c_long.from_buffer(value).value
        # ext[234], XFS, Btrfs, tmpfs; remote/FUSE/overlay/unknown refuse.
        local = kind in (0xEF53, 0x58465342, 0x9123683E, 0x01021994)
    else:
        flags = ctypes.c_uint32.from_buffer(value, 64).value
        kind = value.raw[72:88].split(b'\0', 1)[0]
        local = bool(flags & 0x1000) and kind in (b'apfs', b'hfs', b'tmpfs')
    if not local:
        raise OrgAccessHeld('org_access_filesystem_unsupported')


class OrgAccessFence:
    """One original descriptor, held until the owning interval positively closes."""

    def __init__(self, fd: int, parent: int, name: str, root: Path, exclusive: bool, namespace: Path) -> None:
        self.fd, self._parent, self._name = fd, parent, name
        self.root, self.exclusive, self._namespace = root, exclusive, namespace
        self._identity = os.fstat(fd)
        self._closed = False
        try:
            value = (root / 'happyranch.db').lstat()
        except FileNotFoundError:
            self.database_identity = None
        else:
            if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                    or value.st_uid != os.getuid()):
                raise OrgAccessHeld('org_access_database_unsafe')
            self.database_identity = value.st_dev, value.st_ino

    @classmethod
    def acquire(cls, root: Path, *, exclusive: bool = False,
                deadline: float | None = None) -> OrgAccessFence:
        """Acquire before any opener; all attempts share at most .5s admission.

        No automatic directory/DB discovery, constructor, SQL or PID inference.
        Callers with an earlier deadline pass that ORIGINAL deadline here.
        """
        from runtime.runtime import daemon_home

        root = Path(root)
        if root.parent.name != 'orgs' or re.fullmatch('[a-z0-9-]{1,40}', root.name) is None:
            raise OrgAccessHeld('org_access_org_identity_invalid')
        end = min(time.monotonic() + .5, deadline) if deadline is not None else time.monotonic()
        parent = fd = None
        try:
            # Resolve no aliases, including the runtime and daemon-home ancestors.
            runtime = root.parent.parent
            for path in (runtime, root.parent):
                check = _directory(path)
                try:
                    _local(check)
                finally:
                    os.close(check)
            # A positively fresh route may gate before creating its org folder.
            try:
                check = _directory(root)
            except FileNotFoundError:
                pass
            else:
                try:
                    _local(check)
                finally:
                    os.close(check)
            namespace = daemon_home() / 'org-access-fences'
            home = _directory(namespace.parent)
            try:
                try:
                    os.mkdir('org-access-fences', 0o700, dir_fd=home)
                except FileExistsError:
                    pass
                parent = os.open('org-access-fences', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
            finally:
                os.close(home)
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OrgAccessHeld('org_access_namespace_unsafe')
            _local(parent)
            name = org_access_key(root) + '.lock'
            fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise OrgAccessHeld('org_access_lock_unsafe')
            while True:
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise OrgAccessHeld('org_access_busy') from None
                    time.sleep(min(.005, max(0, end - time.monotonic())))
            result = cls(fd, parent, name, root, exclusive, namespace)
            result.assert_held()
            if deadline is not None and time.monotonic() >= end:
                raise OrgAccessHeld('org_access_admission_deadline')
            return result
        except BaseException as exc:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)
            if isinstance(exc, OSError):
                raise OrgAccessHeld('org_access_unavailable') from None
            raise

    def assert_held(self) -> None:
        if self._closed:
            raise OrgAccessHeld('org_access_original_custody_unavailable')
        directory = _directory(self._namespace)
        try:
            expected = os.fstat(self._parent)
            actual_parent = os.fstat(directory)
            if (actual_parent.st_dev, actual_parent.st_ino) != (expected.st_dev, expected.st_ino):
                raise OrgAccessHeld('org_access_namespace_changed')
        finally:
            os.close(directory)
        actual = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
        current = os.fstat(self.fd)
        def identity(value: os.stat_result) -> tuple:
            return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink
        if identity(actual) != identity(self._identity) or identity(current) != identity(self._identity):
            raise OrgAccessHeld('org_access_original_custody_changed')
        if self.database_identity is not None:
            database = (self.root / 'happyranch.db').lstat()
            if (not stat.S_ISREG(database.st_mode) or database.st_nlink != 1
                    or database.st_uid != os.getuid()
                    or (database.st_dev, database.st_ino) != self.database_identity):
                raise OrgAccessHeld('org_access_database_changed')

    def close(self) -> None:
        if not self._closed:
            # Never LOCK_UN: children/guardians share this original description.
            os.close(self.fd)
            os.close(self._parent)
            self._closed = True
            _uncertain.discard(self)

    def retain_unknown(self) -> None:
        if not self._closed:
            _uncertain.add(self)


@contextmanager
def org_access(root: Path, *, exclusive: bool = False,
               deadline: float | None = None) -> Iterator[OrgAccessFence]:
    fence = OrgAccessFence.acquire(root, exclusive=exclusive, deadline=deadline)
    try:
        yield fence
    except BaseException:
        fence.retain_unknown()
        raise
    else:
        fence.close()
