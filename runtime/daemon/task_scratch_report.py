"""Bounded task-scoped audit observations; no removal or future-action authority.

Collectors are bracketed by literal no-follow identities. These observations
cannot exclude future writers or hostile same-UID races. A blocking admitted
OS/DB read cannot be preempted; elapsed bounds prevent starting further work.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from runtime.daemon.task_scratch_coverage import collect_task_scratch_coverage
from runtime.daemon.task_scratch_evidence import SCAN_NS as EVIDENCE_SCAN_NS
from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence
from runtime.orchestrator.task_scratch import validate_task_scratch_manifest

if TYPE_CHECKING:
    from runtime.daemon.sessions import SessionTracker
    from runtime.infrastructure.database import Database

logger = logging.getLogger(__name__)
AUDIT_ACTION = "task_scratch_report"
_STARTED_MONOTONIC = time.monotonic()
_PROC_ROOT = Path("/proc")
_MAX_CANDIDATES = 64
_MAX_DISCOVERY_ENTRIES = 256
_SCAN_NS = 12_000_000_000
_MAX_REASONS = 32
_TASK_ID = re.compile(r"TASK-[A-Z0-9-]{1,59}(?<!-)\Z")


@dataclass(frozen=True)
class TaskScratchReport:
    decision: Literal["would_reclaim", "retain", "unavailable"]
    reasons: tuple[str, ...]
    payload: dict[str, object]


def _reasons(*groups: object) -> tuple[str, ...]:
    values: set[str] = set()
    for group in groups:
        if isinstance(group, str):
            values.add(group[:160])
        elif group:
            values.update(str(value)[:160] for value in group)
    return tuple(sorted(values))[:_MAX_REASONS]


def _admit(deadline: int) -> None:
    if time.monotonic_ns() >= deadline:
        raise ValueError("observation_timeout")


def _open_directory(path: Path, deadline: int) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("discovery_boundary_invalid")
    _admit(deadline)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            _admit(deadline)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(workspace: Path, task_id: str, deadline: int) -> tuple:
    """Open each literal ancestor and candidate member without following links."""
    if not workspace.is_absolute() or ".." in workspace.parts or not _TASK_ID.fullmatch(task_id):
        raise ValueError("canonical_boundary_invalid")
    identities = []
    device = None
    for relative in ("", ".happyranch/task-tmp/" + task_id,
                     ".happyranch/task-scratch-manifests/" + task_id + ".json"):
        path = workspace / relative
        _admit(deadline)
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, component in enumerate(path.parts[1:]):
                _admit(deadline)
                leaf_file = bool(relative.endswith(".json") and index == len(path.parts) - 2)
                child = os.open(component, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                (0 if leaf_file else os.O_DIRECTORY), dir_fd=fd)
                os.close(fd)
                fd = child
                _admit(deadline)
                info = os.fstat(fd)
                # Only workspace and descendants must share a device.
                if index >= len(workspace.parts) - 2:
                    if device is None:
                        device = info.st_dev
                    if info.st_dev != device:
                        raise ValueError("candidate_device_changed")
                    identities.append((component, info.st_dev, info.st_ino, info.st_mode,
                                       info.st_size, info.st_mtime_ns, info.st_ctime_ns))
                if leaf_file:
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                        raise ValueError("manifest_invalid")
                    _admit(deadline)
                    raw = os.read(fd, 65537)
                    if len(raw) > 65536:
                        raise ValueError("manifest_invalid")
                    validate_task_scratch_manifest(json.loads(raw), expected_task_id=task_id,
                        expected_root=workspace / ".happyranch/task-tmp" / task_id)
                    identities.append(hashlib.sha256(raw).hexdigest())
        finally:
            os.close(fd)
    return tuple(identities)


def report_task_scratch(*, db: Database, sessions: SessionTracker | None, task_id: str,
                        agent: str, workspace: Path,
                        source: Literal["teardown", "weekly"], observation_id: str,
                        proc_root: Path | None = None,
                        daemon_started_monotonic: float | None = None,
                        coverage: object | None = None,
                        deadline_ns: int | None = None) -> TaskScratchReport:
    """Append a fresh observation; supplied uncorrelated coverage is recollected."""
    start = time.time_ns()
    deadline = min(deadline_ns if deadline_ns is not None else time.monotonic_ns() + _SCAN_NS,
                   time.monotonic_ns() + _SCAN_NS)
    payload: dict[str, object] = {
        "report_only": True, "source": source, "observation_id": uuid.uuid4().hex,
        "producer_observation_id": observation_id[:160], "started_at_ns": start,
        "freshness_limited": True, "observation_budget_ns": _SCAN_NS,
        "provenance": "literal_identity_bracketed_collectors",
        "coverage_recollected": coverage is not None,
        "actual_reclaimed_bytes": 0, "actual_reclaimed_inodes": 0,
    }
    decision: Literal["would_reclaim", "retain", "unavailable"] = "unavailable"
    reasons: tuple[str, ...] = ()
    try:
        workspace = Path(workspace)
        proc_root = _PROC_ROOT if proc_root is None else proc_root
        root = workspace / ".happyranch/task-tmp" / task_id
        payload["root"] = str(root)[:4096]
        if sessions is None:
            raise ValueError("session_observer_unavailable")
        before = _identity(workspace, task_id, deadline)
        payload["candidate_identity"] = hashlib.sha256(repr(before).encode()).hexdigest()
        payload["manifest_status"] = "ok"
        _admit(deadline)
        # The unchanged evidence API owns its five-second budget. Admit it
        # only when that entire budget fits inside the coordinator deadline.
        if time.monotonic_ns() + EVIDENCE_SCAN_NS > deadline:
            raise ValueError("observation_timeout")
        evidence = collect_task_scratch_evidence(
            db=db, sessions=sessions, task_id=task_id, root=root, proc_root=proc_root,
            monotonic_now=time.monotonic(), daemon_started_monotonic=(
                _STARTED_MONOTONIC if daemon_started_monotonic is None else daemon_started_monotonic),
        )
        reasons = _reasons(evidence.reasons)
        payload.update(boot_id=evidence.boot_id, evidence_observed_at_ns=evidence.observed_at_ns,
                       evidence_freshness_limited=evidence.freshness_limited,
                       process_roots=evidence.process_roots, process_cwds=evidence.process_cwds,
                       open_fds=evidence.open_fds)
        _admit(deadline)
        coverage_start = time.time_ns()
        observed = collect_task_scratch_coverage(workspace=workspace, proc_root=proc_root,
                                               deadline_ns=min(deadline, time.monotonic_ns() + 5_000_000_000))
        reasons = _reasons(reasons, observed.reasons)
        payload.update(coverage_boot_id=observed.boot_id,
                       coverage_observed_at_ns=observed.observed_at_ns,
                       coverage_complete=observed.complete, coverage_ready=observed.coverage_ready,
                       coverage_freshness_limited=observed.freshness_limited)
        after = _identity(workspace, task_id, deadline)
        now = time.time_ns()
        if before != after:
            raise ValueError("candidate_identity_changed")
        if not (start <= evidence.observed_at_ns <= coverage_start <= observed.observed_at_ns <= now
                and now - start <= _SCAN_NS):
            raise ValueError("observation_stale_or_unordered")
        if not evidence.boot_id or evidence.boot_id != observed.boot_id:
            raise ValueError("evidence_coverage_boot_mismatch")
        if observed.workspace != str(workspace):
            raise ValueError("coverage_workspace_mismatch")
        bucket = next((row for row in observed.buckets
                       if row.relative_path == str(root.relative_to(workspace))), None)
        if bucket is None:
            raise ValueError("candidate_coverage_missing")
        # Incomplete partitions never supply a made-up zero measurement.
        if observed.complete:
            payload.update(allocated_bytes=bucket.allocated_bytes, entries=bucket.entries)
        if not observed.complete or not observed.coverage_ready:
            raise ValueError("coverage_not_ready")
        if bucket.classification != "canonical_regenerable":
            decision, reasons = "retain", _reasons(reasons, "candidate_" + bucket.classification)
        elif not evidence.eligible:
            decision = "retain"
        else:
            decision, reasons = "would_reclaim", ("report_only_noop",)
    except Exception as exc:
        reasons = _reasons(reasons, str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                           "observer_unavailable")
    payload.update(decision=decision, reasons=list(reasons), observed_at_ns=time.time_ns())
    try:
        db.insert_audit_log(task_id, agent, AUDIT_ACTION, payload)
    except Exception:
        logger.warning("task scratch report publication unavailable for %.80s", task_id)
        decision, reasons = "unavailable", _reasons(reasons, "publication_unavailable")
        payload.update(decision=decision, reasons=list(reasons))
    return TaskScratchReport(decision, reasons, payload)


def report_registered_agent_task_scratch(*, db: Database, sessions: SessionTracker | None,
                                         agent: str, workspace: Path,
                                         proc_root: Path | None = None) -> tuple[TaskScratchReport, ...]:
    """Bound every discovery advance, including rejected names; isolate candidates."""
    deadline = time.monotonic_ns() + _SCAN_NS
    reports: list[TaskScratchReport] = []
    try:
        workspace = Path(workspace)
        # Literal parents only; no symlink traversal during discovery either.
        parent = workspace / ".happyranch/task-scratch-manifests"
        for path in (workspace, workspace / ".happyranch", parent):
            _admit(deadline)
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise ValueError("discovery_boundary_invalid")
        _admit(deadline)
        fd = _open_directory(parent, deadline)
        try:
            with os.scandir(fd) as rows:
                for _ in range(_MAX_DISCOVERY_ENTRIES):
                    _admit(deadline)
                    try:
                        row = next(rows)
                    except StopIteration:
                        return tuple(reports)
                    _admit(deadline)
                    if not row.name.endswith(".json") or not _TASK_ID.fullmatch(row.name[:-5]):
                        continue
                    if len(reports) >= _MAX_CANDIDATES:
                        raise ValueError("discovery_candidate_capped")
                    try:
                        reports.append(report_task_scratch(
                            db=db, sessions=sessions, task_id=row.name[:-5], agent=agent,
                            workspace=workspace, source="weekly", observation_id="weekly",
                            proc_root=proc_root, deadline_ns=deadline))
                    except Exception:
                        logger.warning("task scratch candidate observation unavailable for %.80s", row.name)
                raise ValueError("discovery_entries_capped")
        finally:
            os.close(fd)
    except Exception as exc:
        # No fabricated task scope when discovery cannot attribute a candidate.
        logger.warning("task scratch discovery unavailable for %.80s: %.160s", agent, str(exc))
    return tuple(reports)
