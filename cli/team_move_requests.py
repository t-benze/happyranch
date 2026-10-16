"""Private client request custody, separate from read-only service preview.

No automatic send/retry/poll. O_EXCL, no-follow reads and file/directory fsync
preserve the exact key/envelope before transport. Same-UID tampering remains
outside this cooperative bookkeeping guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat

from runtime.infrastructure.team_move_store import MAX_BYTES, _unique_object


def identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) is None:
        raise ValueError("Expected an ASCII permanent ID, org slug or operation key")
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


class PrivateMoveRequests:
    def __init__(self, slug):
        identifier(slug)
        # Always client-owned storage, never an org/workspace/server artifact.
        self.root = Path.home() / ".happyranch" / "client-team-moves" / slug

    @contextmanager
    def directory(self):
        """Pin each no-follow directory; sync reused entries as well as new ones.

        A previous mkdir/fsync/close failure can leave apparently complete
        storage. Existence is never a completed-save receipt.
        """
        descriptors = []
        entries = []
        primary = None
        try:
            parent = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(parent)
            if os.fstat(parent).st_uid != os.getuid():
                raise ValueError("Client home ownership is unavailable")
            for index, name in enumerate((".happyranch", "client-team-moves", self.root.name)):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(child)
                info = os.fstat(child)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or (index > 0 and stat.S_IMODE(info.st_mode) != 0o700)):
                    raise ValueError("Private request directory ownership/type/mode is unavailable")
                entries.append((parent, name, child, info))
                parent = child
            yield parent
            # File validation/fsync/close has succeeded. Pin/check the actual
            # entries and sync bottom-up, including their reused parents.
            for parent, name, child, info in reversed(entries):
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(child)
                fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
                if any(getattr(current, f) != getattr(info, f) or getattr(opened, f) != getattr(info, f) for f in fields):
                    raise ValueError("Private request directory identity changed")
                os.fsync(child)
            os.fsync(descriptors[0])
        except BaseException as exc:
            primary = exc
            raise
        finally:
            failure = None
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if failure is None:
                        failure = exc
            if failure is not None:
                if primary is None:
                    raise failure
                primary.add_note("Private request directory close failed")

    def path(self, key, kind):
        identifier(key)
        if kind not in {"selection", "apply"}:
            raise ValueError("Unsupported saved request kind")
        return self.root / (key + "." + kind + ".json")

    def save(self, key, kind, value):
        name = self.path(key, kind).name
        raw = encoded(value)
        if len(raw) > MAX_BYTES:
            raise ValueError("Saved request exceeds the supported bound")
        with self.directory() as parent:
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            except FileExistsError:
                # Exact complete bytes from a failed save can be reused ONLY
                # after the same positive durability protocol as a fresh save.
                self._read(parent, name, expected=raw)
            else:
                # Failure preserves the exclusive path. Partial/changed bytes
                # refuse; there is no overwrite, replacement key or auto-send.
                with os.fdopen(fd, "wb") as stream:
                    info = os.fstat(stream.fileno())
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._read(parent, name, expected=raw, identity=(info.st_dev, info.st_ino))
        return raw

    def _read(self, parent, name, *, expected=None, identity=None):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > MAX_BYTES
                    or (identity is not None and (info.st_dev, info.st_ino) != identity)):
                raise ValueError("Saved request ownership/type/mode/identity/bound is unavailable")
            raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Saved request exceeds the supported bound")
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if encoded(value) != raw or (expected is not None and raw != expected):
                raise ValueError("Preserve this key: saved bytes are partial, changed or noncanonical; use show")
            os.fsync(stream.fileno())
            fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            opened = os.fstat(stream.fileno())
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if any(getattr(opened, f) != getattr(info, f) or getattr(current, f) != getattr(info, f) for f in fields):
                raise ValueError("Saved request identity/bytes changed during custody")
        return raw

    def read(self, key, kind):
        """Return only after exact file AND containing entries are durable.

        This is also the explicit resume boundary after an earlier failed save;
        canonical JSON/existence alone never authorizes transport.
        """
        name = self.path(key, kind).name
        with self.directory() as parent:
            raw = self._read(parent, name)
        return raw
