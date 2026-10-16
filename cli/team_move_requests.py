"""Private client request custody, separate from read-only service preview.

No automatic send/retry/poll. O_EXCL, no-follow reads and file/directory fsync
preserve the exact key/envelope before transport. Same-UID tampering remains
outside this cooperative bookkeeping guarantee.
"""
from __future__ import annotations

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

    def directory(self):
        for directory in (self.root.parent.parent, self.root.parent, self.root):
            created = False
            try:
                directory.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Private request directory ownership/type is unavailable")
            if directory != self.root.parent.parent and info.st_mode & 0o077:
                raise ValueError("Private request directory must have mode 0700")
            if created:
                parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)

    def path(self, key, kind):
        identifier(key)
        if kind not in {"selection", "apply"}:
            raise ValueError("Unsupported saved request kind")
        return self.root / (key + "." + kind + ".json")

    def save(self, key, kind, value):
        self.directory()
        path = self.path(key, kind)
        raw = encoded(value)
        if len(raw) > MAX_BYTES:
            raise ValueError("Saved request exceeds the supported bound")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if self.read(key, kind) != raw:
                raise ValueError("This key already binds different saved bytes; preserve it and use show") from None
            return raw
        # A failed/interrupted write deliberately retains the exclusive path;
        # it cannot be adopted/overwritten or silently replaced by a fresh key.
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except BaseException:
            raise
        return raw

    def read(self, key, kind):
        self.directory()
        fd = os.open(self.path(key, kind), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                raise ValueError("Saved request ownership/type/mode/bound is unavailable")
            raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Saved request exceeds the supported bound")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if encoded(value) != raw:
            raise ValueError("Saved request bytes are not the original canonical envelope")
        return raw
