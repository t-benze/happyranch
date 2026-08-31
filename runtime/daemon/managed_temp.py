"""Prospective, receipt-owned temporary roots for daemon jobs.

Only roots created by :func:`create_job_temp_root` are candidates.  Discovery
by name, age, uid, or apparent contents is deliberately absent.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from runtime.models import JobStatus, TaskStatus


_VERSION = 1
_OWNER_FILE = ".happyranch-owned.json"
_RECEIPTS = "receipts"
_ACTIVE = "active"
_QUARANTINE = "quarantine"
_STORE_MARKER = ".happyranch-temp-store.json"
_EXPIRY = timedelta(days=7)
_TERMINAL_TASKS = frozenset({
    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SUPERSEDED,
    TaskStatus.CANCELLED,
})
_TERMINAL_JOBS = frozenset({
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED,
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def default_store_root(org_root: Path) -> Path:
    """Return the scoped store inside an already authoritative org root."""
    return Path(org_root) / "managed-temp"


def _write_json_new(path: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("receipt is not an object")
    return value


def _identity(path: Path) -> tuple[int, int]:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError("owned root is not a real directory")
    return st.st_dev, st.st_ino


def _safe_component(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\0" in value:
        raise ValueError("identity is not a safe path component")
    return value


def _ensure_store(root: Path, org_slug: str) -> None:
    """Create a new private store, or validate its inode-bound marker.

    An existing directory without the marker is ambiguous and is never
    adopted.  Its children are not inspected.
    """
    root = Path(root)
    if root.is_symlink():
        raise ValueError("temporary store must not be a symlink")
    if not root.exists():
        root.mkdir(mode=0o700, parents=True)
        os.chmod(root, 0o700)
        dev, ino = _identity(root)
        _write_json_new(root / _STORE_MARKER, {
            "version": _VERSION, "org_slug": org_slug,
            "uid": os.getuid(), "device": dev, "inode": ino,
        })
    marker = _load(root / _STORE_MARKER)
    dev, ino = _identity(root)
    st = root.lstat()
    if (
        marker != {"version": _VERSION, "org_slug": org_slug,
                   "uid": os.getuid(), "device": dev, "inode": ino}
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) & 0o077
    ):
        raise ValueError("temporary store ownership is unproven")
    for name in (_ACTIVE, _QUARANTINE, _RECEIPTS):
        child = root / name
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_symlink() or child.lstat().st_dev != dev:
            raise ValueError("temporary store crosses a device or symlink")


@contextmanager
def _pinned_rename_parents(
    store_root: Path, *, org_slug: str, source_name: str, target_name: str,
) -> Iterator[tuple[int, int]]:
    """Retain validated store-child inodes through a relative rename."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = source_fd = target_fd = -1
    try:
        root_fd = os.open(store_root, flags)
        root_stat = os.fstat(root_fd)
        path_stat = store_root.lstat()
        marker_fd = os.open(_STORE_MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        with os.fdopen(marker_fd, "r", encoding="utf-8") as stream:
            marker = json.load(stream)
        expected_marker = {
            "version": _VERSION, "org_slug": org_slug, "uid": os.getuid(),
            "device": root_stat.st_dev, "inode": root_stat.st_ino,
        }
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or (root_stat.st_dev, root_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
            or root_stat.st_uid != os.getuid()
            or stat.S_IMODE(root_stat.st_mode) & 0o077
            or marker != expected_marker
        ):
            raise ValueError("temporary store identity changed")
        source_fd = os.open(source_name, flags, dir_fd=root_fd)
        target_fd = os.open(target_name, flags, dir_fd=root_fd)
        for name, directory_fd in ((source_name, source_fd), (target_name, target_fd)):
            descriptor_stat = os.fstat(directory_fd)
            pathname_stat = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                != (pathname_stat.st_dev, pathname_stat.st_ino)
                or descriptor_stat.st_dev != root_stat.st_dev
                or descriptor_stat.st_uid != os.getuid()
                or stat.S_IMODE(descriptor_stat.st_mode) & 0o077
            ):
                raise ValueError("temporary store parent identity changed")
        yield source_fd, target_fd
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("temporary store parent identity changed") from exc
    finally:
        for directory_fd in (target_fd, source_fd, root_fd):
            if directory_fd >= 0:
                os.close(directory_fd)


@dataclass(frozen=True)
class TempReceipt:
    receipt_id: str
    org_slug: str
    task_id: str
    job_id: str
    agent: str
    root: str
    device: int
    inode: int
    uid: int
    created_at: str


@dataclass(frozen=True)
class Eligibility:
    """The sole mutation input, bound to one receipt and one exact inode."""
    receipt: TempReceipt
    receipt_path: str
    action: str
    eligible_at: str
    quarantine_path: str | None = None
    expires_at: str | None = None


def lifecycle_is_inactive(db, sessions, receipt: TempReceipt) -> bool:
    """Authoritative job/task/session predicate; lookup errors fail closed."""
    try:
        task = db.get_task(receipt.task_id)
        job = db.get_job(receipt.job_id)
        if (
            task is None or job is None
            or task.assigned_agent != receipt.agent
            or job.task_id != receipt.task_id
            or job.agent_name != receipt.agent
            or task.status not in _TERMINAL_TASKS
            or job.status not in _TERMINAL_JOBS
        ):
            return False
        return sessions is None or sessions.get_active(receipt.task_id, receipt.agent) is None
    except Exception:
        return False


def create_job_temp_root(
    store_root: Path, *, org_slug: str, task_id: str, job_id: str, agent: str,
) -> TempReceipt:
    """Create one prospectively owned job root and durable provenance."""
    for value in (org_slug, task_id, job_id, agent):
        _safe_component(value)
    _ensure_store(store_root, org_slug)
    receipt_id = uuid.uuid4().hex
    path = store_root / _ACTIVE / receipt_id
    path.mkdir(mode=0o700)
    dev, ino = _identity(path)
    receipt = TempReceipt(
        receipt_id=receipt_id, org_slug=org_slug, task_id=task_id,
        job_id=job_id, agent=agent, root=str(path.resolve(strict=True)),
        device=dev, inode=ino, uid=os.getuid(), created_at=_iso(_now()),
    )
    payload = {"version": _VERSION, **asdict(receipt)}
    try:
        _write_json_new(path / _OWNER_FILE, payload)
        _write_json_new(store_root / _RECEIPTS / f"{receipt_id}.created.json", payload)
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return receipt


def _receipt_from_file(path: Path) -> TempReceipt:
    payload = _load(path)
    if payload.pop("version", None) != _VERSION:
        raise ValueError("unsupported receipt")
    return TempReceipt(**payload)


def _tree_is_closed(path: Path, device: int) -> bool:
    """Reject symlinks, sockets, special files, and nested mounts."""
    stack = [path]
    while stack:
        current = stack.pop()
        if current != path and os.path.ismount(current):
            return False
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    st = entry.stat(follow_symlinks=False)
                    if st.st_dev != device or stat.S_ISLNK(st.st_mode):
                        return False
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
                    elif not stat.S_ISREG(st.st_mode):
                        return False
        except OSError:
            return False
    return True


def _validate_owned(receipt: TempReceipt, path: Path, store_root: Path) -> bool:
    try:
        canonical_store = store_root.resolve(strict=True)
        canonical = path.resolve(strict=True)
        canonical.relative_to(canonical_store)
        dev, ino = _identity(canonical)
        st = canonical.lstat()
        owner = _receipt_from_file(canonical / _OWNER_FILE)
        return (
            canonical == path.absolute()
            and dev == receipt.device and ino == receipt.inode
            and dev == canonical_store.lstat().st_dev
            and st.st_uid == receipt.uid == os.getuid()
            and owner == receipt
            and _tree_is_closed(canonical, dev)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _outside_protected(path: Path, protected_roots: tuple[Path, ...]) -> bool:
    """Fail closed when an exact path overlaps any protected root."""
    try:
        canonical = path.resolve(strict=True)
        for protected_root in protected_roots:
            protected = protected_root.resolve(strict=True)
            if (
                canonical == protected
                or protected in canonical.parents
                or canonical in protected.parents
            ):
                return False
        return True
    except OSError:
        return False


def plan_quarantine(
    store_root: Path, *, org_slug: str,
    inactive: Callable[[TempReceipt], bool],
    protected_roots: tuple[Path, ...] = (), now: datetime | None = None,
) -> list[Eligibility]:
    """Build the final eligibility ledger; unreceipted trees are invisible."""
    try:
        _ensure_store(store_root, org_slug)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    protected: list[Path] = []
    for path in protected_roots:
        try:
            protected.append(path.resolve(strict=True))
        except OSError:
            continue
    ledger: list[Eligibility] = []
    for receipt_file in sorted((store_root / _RECEIPTS).glob("*.created.json")):
        try:
            receipt = _receipt_from_file(receipt_file)
            path = Path(receipt.root)
            if receipt.org_slug != org_slug or not _validate_owned(receipt, path, store_root):
                continue
            canonical = path.resolve(strict=True)
            if not _outside_protected(canonical, tuple(protected)):
                continue
            if not inactive(receipt):
                continue
            ledger.append(Eligibility(
                receipt=receipt, receipt_path=str(receipt_file), action="quarantine",
                eligible_at=_iso(now or _now()),
            ))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return ledger


def quarantine(
    entry: Eligibility, *, store_root: Path,
    inactive: Callable[[TempReceipt], bool],
    protected_roots: tuple[Path, ...] = (), now: datetime | None = None,
) -> Eligibility:
    """Revalidate and same-device rename one exact eligible root."""
    if entry.action != "quarantine":
        raise ValueError("wrong eligibility action")
    receipt = entry.receipt
    source = Path(receipt.root)
    target = store_root / _QUARANTINE / receipt.receipt_id
    if (
        not inactive(receipt)
        or not _validate_owned(receipt, source, store_root)
        or not _outside_protected(source, protected_roots)
    ):
        raise ValueError("candidate is no longer eligible")
    if target.exists() or target.is_symlink():
        raise ValueError("quarantine target already exists")
    current = now or _now()
    result = Eligibility(
        receipt=receipt, receipt_path=entry.receipt_path, action="expire",
        eligible_at=_iso(current), quarantine_path=str(target),
        expires_at=_iso(current + _EXPIRY),
    )
    operation_id = uuid.uuid4().hex
    payload = {"version": _VERSION, **asdict(result)}
    # Durable intent precedes mutation.  On interruption after rename,
    # plan_expiry can recover this exact inode from the intent alone.
    _write_json_new(
        store_root / _RECEIPTS
        / f"{receipt.receipt_id}.{operation_id}.quarantine-intent.json",
        payload,
    )
    with _pinned_rename_parents(
        store_root, org_slug=receipt.org_slug,
        source_name=_ACTIVE, target_name=_QUARANTINE,
    ) as (source_fd, target_fd):
        try:
            os.stat(receipt.receipt_id, dir_fd=target_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("quarantine target already exists")
        source_stat = os.stat(
            receipt.receipt_id, dir_fd=source_fd, follow_symlinks=False,
        )
        if (source_stat.st_dev, source_stat.st_ino) != (receipt.device, receipt.inode):
            raise ValueError("candidate identity changed before quarantine")
        os.rename(
            receipt.receipt_id, receipt.receipt_id,
            src_dir_fd=source_fd, dst_dir_fd=target_fd,
        )
    _write_json_new(
        store_root / _RECEIPTS
        / f"{receipt.receipt_id}.{operation_id}.quarantined.json",
        payload,
    )
    return result


def plan_expiry(
    store_root: Path, *, org_slug: str,
    inactive: Callable[[TempReceipt], bool],
    protected_roots: tuple[Path, ...] = (), now: datetime | None = None,
) -> list[Eligibility]:
    """Return only expired, still-owned quarantine receipts."""
    try:
        _ensure_store(store_root, org_slug)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    current = now or _now()
    newest: dict[tuple[int, int], Eligibility] = {}
    files = sorted((store_root / _RECEIPTS).glob("*.quarantined.json"))
    files += sorted((store_root / _RECEIPTS).glob("*.quarantine-intent.json"))
    for receipt_file in files:
        try:
            payload = _load(receipt_file)
            if payload.pop("version", None) != _VERSION:
                continue
            payload["receipt"] = TempReceipt(**payload["receipt"])
            entry = Eligibility(**payload)
            identity = (entry.receipt.device, entry.receipt.inode)
            if (
                entry.expires_at and entry.quarantine_path
                and (
                    identity not in newest
                    or _parse(entry.eligible_at) > _parse(newest[identity].eligible_at)
                )
            ):
                newest[identity] = entry
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return [
        entry for entry in newest.values()
        if current >= _parse(entry.expires_at or "")
        and inactive(entry.receipt)
        and _validate_owned(
            entry.receipt, Path(entry.quarantine_path or ""), store_root,
        )
        and _outside_protected(Path(entry.quarantine_path or ""), protected_roots)
    ]


def restore(
    entry: Eligibility, *, store_root: Path,
    protected_roots: tuple[Path, ...] = (), now: datetime | None = None,
) -> Path:
    """Restore an unexpired quarantined inode to its exact original path."""
    if entry.action != "expire" or not entry.quarantine_path:
        raise ValueError("not a quarantine receipt")
    source = Path(entry.quarantine_path)
    target = Path(entry.receipt.root)
    if entry.expires_at is None or (now or _now()) >= _parse(entry.expires_at):
        raise ValueError("quarantine receipt has expired")
    if (
        target.exists() or target.is_symlink()
        or not _validate_owned(entry.receipt, source, store_root)
        or not _outside_protected(source, protected_roots)
    ):
        raise ValueError("restore identity check failed")
    with _pinned_rename_parents(
        store_root, org_slug=entry.receipt.org_slug,
        source_name=_QUARANTINE, target_name=_ACTIVE,
    ) as (source_fd, target_fd):
        try:
            os.stat(entry.receipt.receipt_id, dir_fd=target_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("restore target already exists")
        source_stat = os.stat(
            entry.receipt.receipt_id, dir_fd=source_fd, follow_symlinks=False,
        )
        if (source_stat.st_dev, source_stat.st_ino) != (
            entry.receipt.device, entry.receipt.inode,
        ):
            raise ValueError("candidate identity changed before restore")
        os.rename(
            entry.receipt.receipt_id, entry.receipt.receipt_id,
            src_dir_fd=source_fd, dst_dir_fd=target_fd,
        )
    _write_json_new(
        store_root / _RECEIPTS
        / f"{entry.receipt.receipt_id}.{uuid.uuid4().hex}.restored.json",
        {"version": _VERSION, "restored_at": _iso(_now()), **asdict(entry.receipt)},
    )
    return target


def expire(
    entry: Eligibility, *, store_root: Path,
    inactive: Callable[[TempReceipt], bool],
    protected_roots: tuple[Path, ...] = (), now: datetime | None = None,
) -> bool:
    """Revalidate a positive expiry receipt, then unlink only that tree."""
    if entry.action != "expire" or not entry.quarantine_path or not entry.expires_at:
        raise ValueError("not an expiry eligibility entry")
    current = now or _now()
    path = Path(entry.quarantine_path)
    if current < _parse(entry.expires_at):
        return False
    if (
        not inactive(entry.receipt)
        or not _validate_owned(entry.receipt, path, store_root)
        or not _outside_protected(path, protected_roots)
    ):
        raise ValueError("quarantine is no longer eligible")
    # The closed-tree revalidation immediately above excludes symlinks,
    # special files, and nested mounts.  Mutation starts only from this exact
    # received path; no broad root or glob is ever passed to rmtree.
    shutil.rmtree(path)
    _write_json_new(
        store_root / _RECEIPTS
        / f"{entry.receipt.receipt_id}.{uuid.uuid4().hex}.expired.json",
        {"version": _VERSION, "expired_at": _iso(current), **asdict(entry.receipt)},
    )
    return True
