"""Read-only owner binding for the fixed system-bus helper.

The UID comes from bus credentials. Neither the environment nor request data
selects a user, home, record or command. Filesystem/passwd roots are injectable
for isolated callers; the installed entry always uses the host defaults.
"""
from __future__ import annotations

import importlib.util
import os
import pwd
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Avoid the remote_access initializer and its bundled connector dependencies.
_SPEC = importlib.util.spec_from_file_location(
    'happyranch_helper_release_verifier', Path(__file__).with_name('release_verifier.py'))
_verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _verifier
_SPEC.loader.exec_module(_verifier)

FAILURES = frozenset({'owner_unknown', 'owner_ambiguous', 'owner_unsupported',
                      'owner_mismatch', 'release_home_invalid'})
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class OwnerRefused(Exception):
    def __init__(self, category: str) -> None:
        self.category = category if category in FAILURES else 'release_home_invalid'
        super().__init__(self.category)


class Passwd(Protocol):
    def getpwall(self) -> list[pwd.struct_passwd]: ...


@dataclass(frozen=True)
class Owner:
    uid: int
    data_home: Path


def _path(value: str) -> Path:
    path = Path(value)
    if (path.anchor != '/' or str(path) != value or '..' in path.parts
            or any(ord(c) < 32 for c in value)):
        raise OwnerRefused('release_home_invalid')
    return path


def _validate_home(candidate: Path, home: Path, uid: int, fs_root: Path) -> None:
    if (candidate == Path('/') or candidate.parts[1] in ('etc', 'var')
            or candidate.is_relative_to(home / '.happyranch')):
        raise OwnerRefused('release_home_invalid')
    descriptor = os.open(fs_root, _FLAGS)
    try:
        for part in candidate.parts[1:]:
            child = os.open(part, _FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
                or info.st_mode & 0o022):
            raise OwnerRefused('release_home_invalid')
    finally:
        os.close(descriptor)


def bootstrap_owner(sender_uid: int, *, passwd: Passwd = pwd,
                    fs_root: Path = Path('/')) -> Owner:
    """Return exactly one validated binding, or only a closed refusal code.

    Reads only: it never creates a release home or publishes a binding record.
    """
    if type(sender_uid) is not int or not 1000 <= sender_uid < 2**32 - 1:
        raise OwnerRefused('owner_unsupported')
    try:
        rows = [row for row in passwd.getpwall() if row.pw_uid == sender_uid]
    except (OSError, KeyError):
        raise OwnerRefused('owner_unknown') from None
    if not rows:
        raise OwnerRefused('owner_unknown')
    if len(rows) != 1:
        raise OwnerRefused('owner_ambiguous')
    try:
        row = rows[0]
        home = _path(row.pw_dir)
        record_path = fs_root / _verifier.RECORD_PATH.relative_to('/')
        try:
            data = _verifier._read(record_path, uid=0, limit=4096, trusted=True)
        except FileNotFoundError:
            candidate = home / '.local/share/happyranch'
        else:
            record = _verifier._object(data)
            if record.keys() != {'user', 'uid', 'data_home', 'unit', 'fragment_path'}:
                raise OwnerRefused('release_home_invalid')
            if record['uid'] != sender_uid or record['user'] != row.pw_name:
                raise OwnerRefused('owner_mismatch')
            _verifier.validate_record(record)
            candidate = _path(record['data_home'])
        _validate_home(candidate, home, sender_uid, fs_root)
        return Owner(sender_uid, candidate)
    except OwnerRefused:
        raise
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        raise OwnerRefused('release_home_invalid') from None
