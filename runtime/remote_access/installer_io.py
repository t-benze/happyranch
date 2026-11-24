"""Retained no-follow filesystem custody shared by installation and retirement."""
from __future__ import annotations

import ctypes
import errno
import os
import stat
import json
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Iterator

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CREDSTORE = "etc/credstore"
_STATE_DIRS = ("var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar")


class InstallerError(RuntimeError):
    """An unsafe path, incompatible account, command failure, or unit drift."""


class InstallerIO:
    """Callers supply their existing layout, identity and native observations."""

    @contextmanager
    def _directory(self, relative: str = "", *, create: bool = False) -> Iterator[int]:
        """Walk literal parents with directory descriptors, never symlinks."""
        descriptor = os.open("/", _DIR_FLAGS)
        try:
            for part in self.root.parts[1:]:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            self._check_directory(descriptor, str(self.root), None)
            layout = self._layout()
            parts = Path(relative).parts
            for index, part in enumerate(parts):
                prefix = str(Path(*parts[:index + 1]))
                expected = layout.get(prefix)
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    mode, owner = expected or (0o755, self.root_owner)
                    if owner is None:
                        raise InstallerError("cannot create service directories without a validated happyranch account")
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    try:
                        os.fchown(child, *owner)
                        os.fchmod(child, mode)
                    except BaseException:
                        os.close(child)
                        raise
                os.close(descriptor)
                descriptor = child
                self._check_directory(descriptor, prefix, expected)
            yield descriptor
        finally:
            os.close(descriptor)

    def _check_directory(
        self, descriptor: int, path: str, expected: tuple[int, tuple[int, int] | None] | None,
    ) -> None:
        info = os.fstat(descriptor)
        if (expected is None and path == "var/log" and info.st_uid == self.root_owner[0]
                and not info.st_mode & 0o002):
            # Ubuntu's root:syslog 0775 ancestor trusts syslog members for log
            # entries. Never extend that trust to managed directories/parents.
            try:
                log_gid = self._lookup_group("syslog").gr_gid
            except KeyError:
                pass
            else:
                if info.st_gid == log_gid:
                    return
        owner = expected[1] if expected else self.root_owner
        allowed_modes = (expected[0],) if expected else ()
        if expected and path == _CREDSTORE:
            allowed_modes += (0o755,)
        if expected and path in {_STATE_DIRS[0], "run/happyranch-connector"}:
            # Creation follows systemd's mode; the trust-state store tightens
            # state on save; the identity unit also tightens its runtime dir.
            # Preserve either known safe mode without changing ownership.
            allowed_modes += (0o700,)
        if ((owner is not None and (info.st_uid, info.st_gid) != owner) or info.st_mode & 0o022
                or (expected and stat.S_IMODE(info.st_mode) not in allowed_modes)):
            raise InstallerError(f"unsafe directory ownership or mode: {path}")
        if owner is None:
            raise InstallerError(f"cannot validate existing service directory ownership without a happyranch account: {path}")

    @staticmethod
    def _read_file(
        parent: int, name: str, *, owner: tuple[int, int] | None = None,
        mode: int | None = None, source_uid: int | None = None,
    ) -> bytes:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise InstallerError("unsafe provisioning file type")
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                    or info.st_mode & 0o022 or (owner is not None and (info.st_uid, info.st_gid) != owner)
                    or (source_uid is not None and info.st_uid != source_uid)
                    or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
                    or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)):
                raise InstallerError("unsafe provisioning file ownership, mode, links or size")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(65536)
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            def signature(value: os.stat_result) -> tuple[int, ...]:
                return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
                        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if (len(data) > 65536 or not data.strip()
                    or len(data) != info.st_size or signature(named) != signature(info)
                    or signature(info) != signature(after) or signature(info) != signature(named_after)):
                raise InstallerError("empty, oversized or changing provisioning file")
            return data
        finally:
            os.close(descriptor)

    @staticmethod
    def _json(data: bytes) -> dict:
        def pairs(items: list[tuple[str, object]]) -> dict:
            result: dict = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        try:
            value = json.loads(data, object_pairs_hook=pairs)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise InstallerError("invalid provisioning JSON object") from None

    @contextmanager
    def _publication_parent(self, relative: str = "etc/happyranch") -> Iterator[tuple[int, Callable[[], None]]]:
        """Pin every literal ancestor until the last pre-rename validation."""
        descriptor = os.open("/", _DIR_FLAGS)
        opened = [descriptor]
        parents = []
        layout = self._layout()
        path = Path("/")
        try:
            for part in (self.root / relative).parts[1:]:
                named = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                opened.append(child)
                info = os.fstat(child)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise InstallerError("publication parent changed while opening")
                path /= part
                managed_relative = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else None
                if managed_relative is not None:
                    self._check_directory(child, str(self.root) if managed_relative == "." else managed_relative,
                                          layout.get(managed_relative))
                parents.append((descriptor, part, child, info))
                descriptor = child

            def revalidate() -> None:
                try:
                    for ancestor, name, child, before in parents:
                        named = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                        current = os.fstat(child)
                        expected = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode)
                        for info in (named, current):
                            if ((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != expected
                                    or not stat.S_ISDIR(info.st_mode)):
                                raise InstallerError("publication parent changed before rename")
                except OSError:
                    raise InstallerError("publication parent unavailable before rename") from None

            yield descriptor, revalidate
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)

    @staticmethod
    def _rename_provision_entry(parent: int, source: str, target: str, *, updating: bool = False) -> None:
        # Plain rename can clobber a late target. Keep the shared O_EXCL
        # publication/quarantine contract atomic; no fallback.
        try:
            rename = ctypes.CDLL(None, use_errno=True).renameat2
        except AttributeError:
            raise InstallerError("atomic no-replace rename is unavailable") from None
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, os.fsencode(source), parent, os.fsencode(target), 0 if updating else 1):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise InstallerError("provisioning destination appeared before rename")
            raise OSError(error, "atomic provisioning rename failed")
