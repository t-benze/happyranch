"""Finite task-scratch coverage consumed by report-only coordination; never a cleanup permit.

Every source operation is admitted through one bounded observer. A complete
observation compares two snapshots; it cannot preempt an already-admitted
blocking syscall, exclude future writers, or detect a hostile final-syscall swap.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from runtime.orchestrator.task_scratch import TaskScratchError, validate_task_scratch_manifest

MAX_ENTRIES = 100_000
MAX_READS = 10_000
MAX_MANIFEST_BYTES = 64 * 1024
MAX_TOTAL_MANIFEST_BYTES = 4 * 1024 * 1024
SCAN_NS = 5_000_000_000
_TASK_ID = re.compile(r"[A-Z][A-Z0-9-]{0,63}(?<!-)\Z")


@dataclass(frozen=True)
class CoverageBucket:
    relative_path: str
    classification: str
    allocated_bytes: int
    apparent_bytes: int
    entries: int


@dataclass(frozen=True)
class TaskScratchCoverageObservation:
    workspace: str
    boot_id: str | None
    complete: bool
    coverage_ready: bool
    reasons: tuple[str, ...]
    buckets: tuple[CoverageBucket, ...]
    dominant: tuple[str, ...]
    observed_at_ns: int
    freshness_limited: bool = True


@dataclass(frozen=True)
class _CoverageBinding:
    """Private comparison-ready snapshot; never part of report serialization."""
    observation: TaskScratchCoverageObservation
    snapshot: _Snapshot | None


@dataclass(frozen=True)
class _Item:
    rel: str
    dev: int
    ino: int
    blocks: int
    size: int
    mode: int


@dataclass(frozen=True)
class _Snapshot:
    boot: str | None
    workspace_id: tuple[int, int] | None
    buckets: tuple[CoverageBucket, ...]
    items: tuple[_Item, ...]
    populations: tuple[tuple[str, tuple[str, ...]], ...]
    manifests: tuple[tuple[str, bytes | None, str], ...]


class _Budget:
    def __init__(self, deadline: int, reasons: set[str]) -> None:
        self.deadline, self.reasons = deadline, reasons
        self.entries = self.reads = self.manifest_bytes = 0

    def admit(self, *, read: bool = False, manifest_bytes: int = 0) -> bool:
        if time.monotonic_ns() > self.deadline:
            self.reasons.add("observation_timeout")
            return False
        if read:
            if self.reads >= MAX_READS:
                self.reasons.add("read_cap")
                return False
            self.reads += 1
        else:
            if self.entries >= MAX_ENTRIES:
                self.reasons.add("entry_cap")
                return False
            self.entries += 1
        if manifest_bytes:
            if self.manifest_bytes + manifest_bytes > MAX_TOTAL_MANIFEST_BYTES:
                self.reasons.add("manifest_byte_cap")
                return False
            self.manifest_bytes += manifest_bytes
        return True

    def manifest_read_size(self) -> int | None:
        """Admit the dependent read and reserve only bytes it can actually return."""
        if not self.admit(read=True):
            return None
        remaining = MAX_TOTAL_MANIFEST_BYTES - self.manifest_bytes
        if remaining <= 0:
            self.reasons.add("manifest_byte_cap")
            return None
        return min(MAX_MANIFEST_BYTES + 1, remaining)

    def account_manifest_bytes(self, count: int) -> None:
        # The request was capped by the remaining allowance, so this cannot
        # cross the shared cap even if the file grew after its metadata read.
        self.manifest_bytes += count


def _stat(path: Path, budget: _Budget) -> os.stat_result | None:
    if not budget.admit():
        return None
    try:
        return path.stat(follow_symlinks=False)
    except OSError:
        budget.reasons.add("metadata_unavailable")
        return None


def _item(path: Path, root_dev: int, budget: _Budget, items: list[_Item], workspace: Path) -> tuple[int, int, int] | None:
    row = _stat(path, budget)
    if row is None:
        return None
    if row.st_dev != root_dev:
        budget.reasons.add("device_boundary")
        return None
    blocks, size = getattr(row, "st_blocks", None), getattr(row, "st_size", None)
    if type(blocks) is not int or blocks < 0 or type(size) is not int or size < 0:
        budget.reasons.add("metadata_unavailable")
        return None
    items.append(_Item(str(path.relative_to(workspace)), row.st_dev, row.st_ino, blocks, size, row.st_mode))
    return blocks * 512, size, 1


def _scandir(path: Path, budget: _Budget) -> list[os.DirEntry[str]] | None:
    if not budget.admit(read=True):
        return None
    try:
        with os.scandir(path) as rows:
            out: list[os.DirEntry[str]] = []
            while True:
                if not budget.admit(read=True):
                    return None
                try:
                    out.append(next(rows))
                except StopIteration:
                    return out
    except OSError:
        budget.reasons.add("population_unavailable")
        return None


def _walk(path: Path, root_dev: int, budget: _Budget, items: list[_Item], workspace: Path) -> tuple[int, int, int] | None:
    own = _item(path, root_dev, budget, items, workspace)
    if own is None:
        return None
    row = _stat(path, budget)
    if row is None or not stat.S_ISDIR(row.st_mode) or stat.S_ISLNK(row.st_mode):
        return own
    children = _scandir(path, budget)
    if children is None:
        return None
    total = list(own)
    for child in children:
        child_total = _walk(Path(child.path), root_dev, budget, items, workspace)
        if child_total is None:
            return None
        total = [a + b for a, b in zip(total, child_total)]
    return tuple(total)


def _boot(proc_root: Path, budget: _Budget) -> str | None:
    if not budget.admit(read=True):
        return None
    try:
        fd = os.open(proc_root / "sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not budget.admit(read=True):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as handle:
            value = handle.read(128).decode("ascii").strip()
        if str(uuid.UUID(value)) != value.lower():
            raise ValueError
        return value
    except (OSError, UnicodeDecodeError, ValueError):
        budget.reasons.add("boot_id_unavailable")
        return None


def _manifest(path: Path, workspace: Path, budget: _Budget) -> tuple[bytes | None, str]:
    if not _TASK_ID.fullmatch(path.name):
        return None, "residual_unknown"
    manifests = workspace / ".happyranch" / "task-scratch-manifests"
    parent = _literal_directory(manifests, budget)
    if parent is not True:
        return None, "residual_unmanifested"
    manifest = manifests / f"{path.name}.json"
    row = _stat(manifest, budget)
    if row is None or not stat.S_ISREG(row.st_mode) or row.st_size > MAX_MANIFEST_BYTES:
        return None, "residual_unmanifested"
    # Opening and consuming a descriptor are separate source operations.
    if not budget.admit(read=True):
        return None, "residual_unmanifested"
    try:
        fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            limit = budget.manifest_read_size()
            if limit is None:
                return None, "residual_unmanifested"
            raw = handle.read(limit)
        budget.account_manifest_bytes(len(raw))
        if len(raw) > MAX_MANIFEST_BYTES:
            budget.reasons.add("manifest_byte_cap")
            return None, "residual_unmanifested"
        producers = validate_task_scratch_manifest(json.loads(raw), expected_task_id=path.name, expected_root=path)
        return raw, "canonical_regenerable" if producers else "residual_unmanifested"
    except (OSError, ValueError, TypeError, json.JSONDecodeError, TaskScratchError):
        return None, "residual_unmanifested"


def _candidate_safe(path: Path, root_dev: int, budget: _Budget) -> bool | None:
    """Bounded metadata/name inspection only; arbitrary contents are never read."""
    literal = _literal_directory(path, budget)
    if literal is not True:
        return None if literal is None else False
    rows = _scandir(path, budget)
    if rows is None:
        return None
    for row in rows:
        info = _stat(Path(row.path), budget)
        if info is None or info.st_dev != root_dev:
            return None
        if row.name == ".git" or any(check(info.st_mode) for check in (stat.S_ISSOCK, stat.S_ISFIFO, stat.S_ISBLK, stat.S_ISCHR)):
            return False
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            nested = _candidate_safe(Path(row.path), root_dev, budget)
            if nested is not True:
                return nested
    return True


def _dominant(buckets: list[CoverageBucket]) -> set[str]:
    out: set[str] = set()
    for metric in ("allocated_bytes", "entries"):
        total = sum(getattr(row, metric) for row in buckets)
        if not total:
            continue
        current = 0
        for row in sorted(buckets, key=lambda row: (-getattr(row, metric), os.fsencode(row.relative_path))):
            current += getattr(row, metric)
            out.add(row.relative_path)
            if current * 5 >= total * 4:
                break
        out.update(row.relative_path for row in buckets if getattr(row, metric) * 10 >= total)
    return out


def _population(path: Path, budget: _Budget) -> tuple[str, ...] | None:
    info = _stat(path, budget)
    if info is None or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return None
    rows = _scandir(path, budget)
    return None if rows is None else tuple(sorted((row.name for row in rows), key=os.fsencode))


def _literal_directory(path: Path, budget: _Budget) -> bool | None:
    """Return True for a literal directory, False for absent/malformed, None on error."""
    if not budget.admit():
        return None
    try:
        row = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        budget.reasons.add("metadata_unavailable")
        return None
    return stat.S_ISDIR(row.st_mode) and not stat.S_ISLNK(row.st_mode)


def _optional_literal_directory(path: Path, budget: _Budget) -> tuple[bool | None, bool]:
    """Return literal-directory state plus whether the optional path exists."""
    if not budget.admit():
        return None, False
    try:
        row = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False, False
    except OSError:
        budget.reasons.add("metadata_unavailable")
        return None, True
    return stat.S_ISDIR(row.st_mode) and not stat.S_ISLNK(row.st_mode), True


def _parents(workspace: Path, budget: _Budget) -> tuple[Path, Path, bool | None, bool | None, bool, bool]:
    owned, tmp = workspace / ".happyranch", workspace / ".happyranch" / "task-tmp"
    # Optional parents are absent rather than unavailable; present malformed parents
    # are one noncanonical residual and must never be traversed.
    owned_state, owned_present = _optional_literal_directory(owned, budget)
    if owned_state is not True:
        if owned_state is False and owned_present:
            budget.reasons.add("noncanonical_ancestor")
        return owned, tmp, owned_state, None, owned_present, False
    tmp_state, tmp_present = _optional_literal_directory(tmp, budget)
    if tmp_state is False and tmp_present:
        budget.reasons.add("noncanonical_ancestor")
    return owned, tmp, owned_state, tmp_state, owned_present, tmp_present


def _snapshot(workspace: Path, proc_root: Path, budget: _Budget) -> _Snapshot:
    boot = _boot(proc_root, budget)
    work_info = _stat(workspace, budget)
    workspace_id = None if work_info is None else (work_info.st_dev, work_info.st_ino)
    if sys.platform != "linux" or workspace_id is None:
        budget.reasons.add("unsupported_or_unavailable_workspace")
        return _Snapshot(boot, workspace_id, (), (), (), ())
    root_dev = workspace_id[0]
    items: list[_Item] = []; populations: list[tuple[str, tuple[str, ...]]] = []; manifests: list[tuple[str, bytes | None, str]] = []
    partitions: list[tuple[Path, str]] = [(workspace, "workspace_metadata")]
    owned, tmp, owned_state, tmp_state, owned_present, tmp_present = _parents(workspace, budget)
    # The partition is fixed: an optional parent affects only its own subtree,
    # never ordinary workspace siblings or another valid parent.
    for parent, skip, kind in ((workspace, ".happyranch", "workspace_child"),):
        rows = _scandir(parent, budget)
        if rows is not None:
            partitions.extend((Path(row.path), kind) for row in rows if row.name != skip)
    if owned_state is True:
        partitions.append((owned, "owned_metadata"))
        rows = _scandir(owned, budget)
        if rows is not None:
            partitions.extend((Path(row.path), "owned_child") for row in rows if row.name != "task-tmp")
        if tmp_state is True:
            partitions.append((tmp, "task_tmp_metadata"))
            rows = _scandir(tmp, budget)
            if rows is not None:
                partitions.extend((Path(row.path), "task_tmp_child") for row in rows)
        elif tmp_state is False and tmp_present:
            partitions.append((tmp, "noncanonical_ancestor"))
    elif owned_state is False and owned_present:
        partitions.append((owned, "noncanonical_ancestor"))
    buckets: list[CoverageBucket] = []; seen: set[str] = set()
    for path, kind in partitions:
        rel = "." if path == workspace else str(path.relative_to(workspace))
        if rel in seen:
            continue
        seen.add(rel)
        population = _population(path, budget)
        if population is not None:
            populations.append((rel, population))
        measured = _item(path, root_dev, budget, items, workspace) if kind.endswith("metadata") or kind == "noncanonical_ancestor" else _walk(path, root_dev, budget, items, workspace)
        if measured is None:
            continue
        classification = "protected_metadata" if kind.endswith("metadata") else "residual_unknown"
        if kind == "noncanonical_ancestor":
            classification = "residual_noncanonical"
        elif kind == "task_tmp_child":
            literal = _literal_directory(path, budget)
            if literal is not True:
                classification = "residual_noncanonical"
            else:
                raw, classification = _manifest(path, workspace, budget)
                manifests.append((rel, raw, classification))
                if classification == "canonical_regenerable":
                    candidate_safe = _candidate_safe(path, root_dev, budget)
                    if candidate_safe is False:
                        classification = "residual_repository_or_special"
                    elif candidate_safe is None:
                        # An interrupted candidate inspection is uncertainty,
                        # never evidence that the candidate is safe.
                        classification = "residual_unknown"
        buckets.append(CoverageBucket(rel, classification, *measured))
    return _Snapshot(boot, workspace_id, tuple(buckets), tuple(items), tuple(populations), tuple(manifests))


def collect_task_scratch_coverage(*, workspace: Path, proc_root: Path = Path("/proc"), deadline_ns: int | None = None) -> TaskScratchCoverageObservation:
    """Collect two bounded snapshots; the returned description never permits action."""
    reasons: set[str] = set(); deadline = time.monotonic_ns() + SCAN_NS if deadline_ns is None else deadline_ns
    budget = _Budget(deadline, reasons)
    before = _snapshot(Path(workspace), Path(proc_root), budget)
    before_complete = not reasons
    after = _snapshot(Path(workspace), Path(proc_root), budget)
    # A capped/unavailable pass is not an observation of change.  It is already
    # fail-closed through its own reason, so compare only two complete passes.
    if before_complete and not reasons:
        if before.boot != after.boot: reasons.add("boot_changed_during_collection")
        if before.workspace_id != after.workspace_id: reasons.add("workspace_changed_during_collection")
        if before.items != after.items: reasons.add("metadata_changed_during_collection")
        if before.populations != after.populations: reasons.add("population_changed_during_collection")
        if before.manifests != after.manifests or before.buckets != after.buckets: reasons.add("classification_changed_during_collection")
    buckets = list(before.buckets); dominant = _dominant(buckets)
    if not buckets or not sum(row.allocated_bytes for row in buckets) or not sum(row.entries for row in buckets) or not any(row.relative_path.startswith(".happyranch/task-tmp/") for row in buckets): reasons.add("zero_or_empty_observation")
    by_path = {row.relative_path: row for row in buckets}
    ready = bool(dominant) and not reasons and all(by_path[name].classification == "canonical_regenerable" for name in dominant)
    return TaskScratchCoverageObservation(str(workspace), before.boot, not reasons, ready, tuple(sorted(reasons)), tuple(buckets), tuple(sorted(dominant)), time.time_ns())


def _collect_private_coverage(**kwargs: object) -> _CoverageBinding:
    """Retain a bounded private binding alongside the unchanged public view."""
    observation = collect_task_scratch_coverage(**kwargs)  # type: ignore[arg-type]
    reasons: set[str] = set()
    budget = _Budget(time.monotonic_ns() + SCAN_NS, reasons)
    snapshot = _snapshot(Path(kwargs["workspace"]), Path(kwargs.get("proc_root", Path("/proc"))), budget)
    return _CoverageBinding(observation, None if reasons else snapshot)
