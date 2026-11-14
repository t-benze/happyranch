"""SQL-free, uncached disposable-venue fence; U3s has no runtime caller."""
from __future__ import annotations

import json
import os
import pwd
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = 'sequential-venue-start.json'
MARKER_FORMAT = 'happyranch-sequential-venue-start@1'
_MARKER_KEYS = {'format', 'daemon_home', 'runtime_root', 'org_slug', 'database_dev', 'database_ino'}


class SequentialVenueUnavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def live_daemon_home() -> tuple[Path, Path]:
    """Persistent account homes, independent of the venue's environment."""
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return (Path(os.path.realpath(account / '.happyranch')),
            Path(os.path.realpath(account / '.happyranch-release')))


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _venue_home(org_root: Path) -> Path:
    """Checks 1–2: metadata only, before opening even a directory descriptor."""
    value = os.environ.get('HAPPYRANCH_DAEMON_HOME')
    if not value or not Path(value).is_absolute():
        raise SequentialVenueUnavailable('sequential_venue_env_absent')
    home = Path(value)
    resolved = Path(os.path.realpath(home))
    live_homes = live_daemon_home()
    resolved_org = Path(os.path.realpath(org_root))
    # Classify a live alias as live_home too; never open it to diagnose it.
    if any(_inside(resolved, live) or _inside(resolved_org, live) for live in live_homes):
        raise SequentialVenueUnavailable('sequential_venue_live_home')
    try:
        info = home.lstat()
    except OSError as exc:
        raise SequentialVenueUnavailable('sequential_venue_env_absent') from exc
    if not stat.S_ISDIR(info.st_mode) or resolved != home:
        raise SequentialVenueUnavailable('sequential_venue_env_absent')
    return home


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _no_constant(value: str) -> None:
    raise ValueError('json_constant')


def _marker_info(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_size > 4096):
        raise ValueError('marker_metadata')


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _home_fd(home: Path) -> int:
    before = home.lstat()
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (before.st_dev, before.st_ino):
        os.close(fd)
        raise ValueError('daemon_home_changed')
    return fd


def _read_marker(home: Path) -> dict:
    directory = _home_fd(home)
    try:
        info = os.stat(MARKER_NAME, dir_fd=directory, follow_symlinks=False)
        _marker_info(info)
        fd = os.open(MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            if _signature(os.fstat(stream.fileno())) != _signature(info):
                raise ValueError('marker_changed')
            raw = stream.read(4097)
            if _signature(os.fstat(stream.fileno())) != _signature(info):
                raise ValueError('marker_changed')
        if (len(raw) > 4096 or _signature(os.stat(MARKER_NAME, dir_fd=directory,
                                                  follow_symlinks=False)) != _signature(info)):
            raise ValueError('marker_changed')
        value = json.loads(raw.decode('utf8', errors='strict'), object_pairs_hook=_unique,
                           parse_constant=_no_constant)
        if (type(value) is not dict or value.keys() != _MARKER_KEYS
                or any(type(value[key]) is not str for key in ('format', 'daemon_home', 'runtime_root', 'org_slug'))
                or any(type(value[key]) is not int or value[key] < 0 for key in ('database_dev', 'database_ino'))
                or value['format'] != MARKER_FORMAT):
            raise ValueError('marker_fields')
        return value
    finally:
        os.close(directory)


@dataclass(frozen=True)
class VenueObservation:
    daemon_home: Path
    runtime_root: Path
    org_slug: str
    database_dev: int
    database_ino: int

    def marker(self) -> dict:
        return dict(format=MARKER_FORMAT, daemon_home=str(self.daemon_home),
                    runtime_root=str(self.runtime_root), org_slug=self.org_slug,
                    database_dev=self.database_dev, database_ino=self.database_ino)


def check_venue_start(*, org_slug: str, org_root: Path, db_path: Path) -> VenueObservation:
    """Fresh filesystem observation outside any writer; grants no authority."""
    home = _venue_home(Path(org_root))
    try:
        marker = _read_marker(home)
    except FileNotFoundError as exc:
        raise SequentialVenueUnavailable('sequential_venue_marker_absent') from exc
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise SequentialVenueUnavailable('sequential_venue_marker_invalid') from exc
    try:
        root = Path(marker['runtime_root'])
        org = Path(org_root)
        db = Path(db_path)
        info = db.lstat()
        if (not root.is_absolute() or Path(os.path.realpath(root)) != root
                or marker['daemon_home'] != str(home) or marker['org_slug'] != org_slug
                or org != root / 'orgs' / org_slug or Path(os.path.realpath(org)) != org
                or db != org / 'happyranch.db' or Path(os.path.realpath(db)) != db
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (marker['database_dev'], marker['database_ino']) != (info.st_dev, info.st_ino)):
            raise ValueError('venue_identity')
        return VenueObservation(home, root, org_slug, info.st_dev, info.st_ino)
    except (OSError, ValueError) as exc:
        raise SequentialVenueUnavailable('sequential_venue_mismatch') from exc


def _existing_marker(directory: int) -> None:
    try:
        info = os.stat(MARKER_NAME, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _marker_info(info)


def write_marker(observation: VenueObservation) -> None:
    """Operator script only: exclusive inode, descriptor-relative replacement."""
    home = _venue_home(observation.runtime_root / 'orgs' / observation.org_slug)
    if home != observation.daemon_home:
        raise ValueError('daemon_home_changed')
    directory = _home_fd(home)
    pending = '.sequential-venue-' + secrets.token_hex(16)
    created = False
    try:
        _existing_marker(directory)
        raw = json.dumps(observation.marker(), sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf8')
        if len(raw) > 4096:
            raise ValueError('marker_too_large')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _existing_marker(directory)
        os.replace(pending, MARKER_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            os.unlink(pending, dir_fd=directory)
        os.close(directory)


def remove_marker(*, org_root: Path) -> None:
    """Operator script only: literal no-follow unlink, never a target traversal."""
    directory = _home_fd(_venue_home(org_root))
    try:
        _existing_marker(directory)
        try:
            os.unlink(MARKER_NAME, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.fsync(directory)
    finally:
        os.close(directory)
