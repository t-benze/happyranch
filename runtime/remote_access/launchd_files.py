"""Darwin-only connector paths and literal, owner-only local file access."""
from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DARWIN_CONNECTOR_STATE_ROOT = Path("~/Library/Application Support/HappyRanch/connector")
DARWIN_ENROLLMENT_KEY_PATH = DARWIN_CONNECTOR_STATE_ROOT / "happyranch-connector/enrollment.key"
MAX_CREDENTIAL_BYTES = 65536
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@contextmanager
def literal_directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Pin each literal parent without following aliases; create private dirs.

    Existing ancestors (e.g. Library) keep their modes. A writable state leaf
    must already be owned by this euid with mode 0700, or be newly created.
    """
    if ".." in path.parts:
        raise OSError("macOS connector directory requires a literal path")
    path = path.absolute()
    descriptor = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if create and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise OSError("macOS connector state directory must be current-user owned 0700")
        yield descriptor
    finally:
        os.close(descriptor)


def read_private_file(path: Path, *, exact_mode: bool) -> bytearray:
    """Read at most 64 KiB through pinned parents and a verified open inode.

    Enrollment requires exactly 0600. The same-user daemon token accepts any
    owner bits but no group/other bits. Callers translate all failures to fixed
    non-secret reasons. No path, file content or native exception is rendered.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("credential requires an absolute literal path")

    def validate(info: os.stat_result) -> None:
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or (mode != 0o600 if exact_mode else bool(mode & 0o077))
                or info.st_size > MAX_CREDENTIAL_BYTES):
            raise ValueError("credential file metadata refused")

    with literal_directory(path.parent) as parent:
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        validate(named)
        descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            validate(info)
            if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("credential changed while opening")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = bytearray(stream.read(MAX_CREDENTIAL_BYTES + 1))
            after = os.fstat(descriptor)
            validate(after)
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("credential changed while reading")
            if not data.strip() or len(data) > MAX_CREDENTIAL_BYTES:
                raise ValueError("credential must be nonempty and at most 64 KiB")
            return data
        finally:
            os.close(descriptor)
