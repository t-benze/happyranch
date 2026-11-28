"""Daemon-local guest transit folders; no caller-supplied paths or link traversal."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
from typing import BinaryIO, Iterator

from runtime.infrastructure.thread_scoped_attachment_store import MAX_THREAD_ATTACHMENT_BYTES

# Unmeasured starting allowances from THR-362 sections 6/8.2.
MAX_UNATTACHED_FILES = 10
MAX_UNATTACHED_BYTES = 50 * 1024 * 1024
MAX_INCOMING_BYTES = 100 * 1024 * 1024


@contextmanager
def _directory(root: Path, parts: tuple[str, ...], *, create: bool = False) -> Iterator[int]:
    opened = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(fd)
        for name in parts:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            opened.append(fd)
            if create:
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError("guest directory is not private and owned")
        yield fd
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()


class ThreadGuestFiles:
    def __init__(self, root: Path, app_id: str, guest_id: str) -> None:
        if app_id not in ("codex_desktop", "claude_desktop") or not re.fullmatch(r"G-[1-9][0-9]*", guest_id):
            raise ValueError("invalid guest folder identity")
        self.root = root
        self.parts = ("guests", app_id, "files", guest_id)

    @staticmethod
    def existing_seats(root: Path) -> set[str]:
        """Sweep extant transit folders, rather than probing every historical seat."""
        result = set()
        for app in ("codex_desktop", "claude_desktop"):
            try:
                with _directory(root, ("guests", app, "files")) as fd:
                    result.update(name for name in os.listdir(fd) if re.fullmatch(r"G-[1-9][0-9]*", name))
            except FileNotFoundError:
                pass
        return result

    def provision(self) -> dict[str, str]:
        result = {}
        for name in ("out", "in"):
            with self.directory(name):
                result[name + "_dir"] = str(self.root.joinpath(*self.parts, name))
        return result

    def directory(self, name: str):
        return _directory(self.root, (*self.parts, name), create=True)

    @staticmethod
    def read_outgoing(fd: int, name: str) -> tuple[bytes, os.stat_result]:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("a direct out file is required")
        # NONBLOCK makes FIFOs/devices refuse without ever blocking the daemon.
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not _regular(before) or before.st_size > MAX_THREAD_ATTACHMENT_BYTES:
                raise ValueError("a regular file of at most 10 MiB is required")
            content = stream.read(MAX_THREAD_ATTACHMENT_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (len(content) != before.st_size or len(content) > MAX_THREAD_ATTACHMENT_BYTES
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError("out file changed while uploading")
        return content, after

    @staticmethod
    def consume(fd: int, name: str, expected: os.stat_result) -> None:
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns, expected.st_ctime_ns):
            raise ValueError("out file changed before removal")
        os.unlink(name, dir_fd=fd)

    def receive(self, stream: BinaryIO, size: int, display_name: str) -> str:
        if not display_name or display_name in (".", "..") or "/" in display_name or "\\" in display_name or any(ord(c) < 32 for c in display_name):
            raise ValueError("invalid attachment display name")
        if size < 0 or size > MAX_THREAD_ATTACHMENT_BYTES:
            raise ValueError("download exceeds 10 MiB")
        with self.directory("in") as fd:
            copies = []
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not _regular(info):
                    raise ValueError("unsafe entry in guest in folder")
                copies.append((info.st_mtime_ns, name, info.st_size))
            used = sum(info[2] for info in copies)
            for _, old, old_size in sorted(copies):
                if used + size <= MAX_INCOMING_BYTES:
                    break
                os.unlink(old, dir_fd=fd)
                used -= old_size
            # Names are unique: repeated fetches never overwrite a caller's file.
            name = secrets.token_hex(12) + "-" + display_name.encode("utf-8")[:200].decode("utf-8", errors="ignore")
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(target, "wb") as output:
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("incomplete attachment")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise ValueError("attachment size changed")
            except BaseException:
                os.unlink(name, dir_fd=fd)
                raise
        return str(self.root.joinpath(*self.parts, "in", name))

    def open_upload(self, thread_id: str, attachment_id: str) -> tuple[BinaryIO, int]:
        with _directory(self.root, ("threads", thread_id, "attachments")) as fd:
            source = os.open(attachment_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stream = os.fdopen(source, "rb")
        info = os.fstat(source)
        if not _regular(info):
            stream.close()
            raise ValueError("attachment is not a regular file")
        return stream, info.st_size

    def cleanup(self) -> None:
        try:
            with _directory(self.root, self.parts[:-1]) as fd:
                info = os.stat(self.parts[-1], dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or not shutil.rmtree.avoids_symlink_attacks:
                    raise ValueError("unsafe guest folder cleanup")
                shutil.rmtree(self.parts[-1], dir_fd=fd)
        except FileNotFoundError:
            pass

    def delete_upload(self, thread_id: str, attachment_id: str) -> None:
        if not re.fullmatch(r"THR-[0-9]+", thread_id) or not re.fullmatch(r"att-[0-9]+", attachment_id):
            raise ValueError("invalid stored attachment identity")
        try:
            with _directory(self.root, ("threads", thread_id, "attachments")) as fd:
                os.unlink(attachment_id, dir_fd=fd)
        except FileNotFoundError:
            pass
