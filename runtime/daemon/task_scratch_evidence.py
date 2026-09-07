"""Dormant, fail-closed observations for later scratch reconciliation."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.models import JobStatus, TaskStatus

if TYPE_CHECKING:
    from runtime.daemon.sessions import SessionTracker
    from runtime.infrastructure.database import Database

MAX_TASKS = MAX_JOBS = MAX_PROCESSES = MAX_FDS_PER_PROCESS = 10_000
WARMUP_SECONDS = 30.0
TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED}
TERMINAL_JOBS = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED}


@dataclass(frozen=True)
class TaskScratchEvidence:
    """A deliberately short-lived observation, never an action permit."""
    task_id: str
    eligible: bool
    reasons: tuple[str, ...]
    boot_id: str | None
    observed_at_ns: int
    process_roots: int
    process_cwds: int
    open_fds: int
    freshness_limited: bool = True


def _under(value: str, root: Path) -> bool:
    value = os.path.normpath(value.removesuffix(" (deleted)"))
    root_text = os.path.normpath(str(root))
    return value == root_text or value.startswith(root_text + os.sep)


def _pid_start(entry: Path) -> str:
    try:
        return (entry / "stat").read_text()[((entry / "stat").read_text()).rindex(")") + 1:].split()[19]
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError("process_identity_unavailable") from exc


def _scan(proc_root: Path, root: Path) -> tuple[int, int, int, set[str]]:
    reasons: set[str] = set(); roots = cwds = fds = 0
    started = time.monotonic_ns()
    try: entries = sorted(p for p in proc_root.iterdir() if p.name.isdecimal())
    except OSError: return roots, cwds, fds, {"process_scan_unavailable"}
    if len(entries) > MAX_PROCESSES: return roots, cwds, fds, {"process_scan_capped"}
    identities: dict[Path, str] = {}
    for entry in entries:
        try:
            identities[entry] = _pid_start(entry)
            for name, marker in (("root", "process_root_reference"), ("cwd", "process_cwd_reference")):
                if _under(os.readlink(entry / name), root):
                    roots += name == "root"; cwds += name == "cwd"; reasons.add(marker)
            fd_entries = list((entry / "fd").iterdir())
            if len(fd_entries) > MAX_FDS_PER_PROCESS: reasons.add("open_fd_scan_capped"); continue
            for fd in fd_entries:
                if _under(os.readlink(fd), root): fds += 1; reasons.add("open_fd_reference")
        except (OSError, RuntimeError): reasons.add("process_reference_unavailable")
    for entry, identity in identities.items():
        try:
            if _pid_start(entry) != identity: reasons.add("process_identity_changed")
        except RuntimeError: reasons.add("process_identity_unavailable")
    if time.monotonic_ns() - started > 5_000_000_000: reasons.add("process_scan_timeout")
    return roots, cwds, fds, reasons


def _component(tasks: list[object], task_id: str, reasons: set[str]) -> set[str]:
    by_id = {task.id: task for task in tasks}
    if task_id not in by_id: return set()
    adjacent: dict[str, set[str]] = {key: set() for key in by_id}
    directed: dict[str, tuple[str, ...]] = {}
    for task in tasks:
        edges = tuple(edge for edge in (task.parent_task_id, task.revisit_of_task_id) if edge)
        directed[task.id] = edges
        for edge in edges:
            if edge not in by_id: reasons.add("lineage_missing")
            else: adjacent[task.id].add(edge); adjacent[edge].add(task.id)
    # Directed DFS distinguishes ordinary undirected parent traversal from cycles.
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting: reasons.add("lineage_cycle"); return
        if node in visited: return
        visiting.add(node)
        for edge in directed.get(node, ()):
            if edge in by_id: visit(edge)
        visiting.remove(node); visited.add(node)
    for node in by_id: visit(node)
    result: set[str] = set(); pending = [task_id]
    while pending:
        node = pending.pop()
        if node in result: continue
        result.add(node)
        if len(result) > MAX_TASKS: reasons.add("lineage_capped"); break
        pending.extend(adjacent[node] - result)
    return result


def collect_task_scratch_evidence(*, db: Database, sessions: SessionTracker, task_id: str, root: Path, proc_root: Path = Path("/proc"), monotonic_now: float | None = None, daemon_started_monotonic: float | None = None) -> TaskScratchEvidence:
    """Read actual sources twice; any ambiguity remains ineligible for B2b."""
    reasons: set[str] = set(); now = time.monotonic() if monotonic_now is None else monotonic_now
    if daemon_started_monotonic is None or now - daemon_started_monotonic < WARMUP_SECONDS: reasons.add("zombie_warmup")
    try: boot_id = (proc_root / "sys/kernel/random/boot_id").read_text().strip() or None
    except OSError: boot_id = None
    if boot_id is None: reasons.add("boot_id_unavailable")
    try: before = db.list_tasks(limit=MAX_TASKS + 1)
    except Exception: before = []; reasons.add("task_scan_unavailable")
    if len(before) > MAX_TASKS: reasons.add("task_scan_capped")
    members = _component(before, task_id, reasons)
    if not members: reasons.add("task_missing")
    for task in before:
        if task.id in members and (task.status not in TERMINAL or task.zombie_flagged_at is not None): reasons.add("nonterminal_or_flagged_lineage")
    sessions_before = tuple(sorted(sessions.iter_active()))
    if any(row[0] in members for row in sessions_before): reasons.add("active_session")
    for task in members:
        try: jobs = db.list_jobs_db(task_id=task, limit=MAX_JOBS + 1)
        except Exception: reasons.add("job_scan_unavailable"); continue
        if len(jobs) > MAX_JOBS: reasons.add("job_scan_capped")
        if any(job.status not in TERMINAL_JOBS for job in jobs): reasons.add("active_job")
        record = next((item for item in before if item.id == task), None)
        if record is None or not record.current_session_id:
            reasons.add("recovery_fingerprint_unavailable")
        else:
            try:
                if db.get_latest_task_result(task, record.assigned_agent, record.current_session_id) is not None: reasons.add("recovery_fingerprint_present")
            except Exception: reasons.add("recovery_fingerprint_unavailable")
    roots, cwds, fds, process_reasons = _scan(proc_root, root); reasons.update(process_reasons)
    try: after = db.list_tasks(limit=MAX_TASKS + 1)
    except Exception: after = []; reasons.add("task_revalidation_unavailable")
    if [(t.id, t.status, t.revisit_of_task_id, t.current_session_id, t.zombie_flagged_at) for t in before] != [(t.id, t.status, t.revisit_of_task_id, t.current_session_id, t.zombie_flagged_at) for t in after]: reasons.add("durable_state_changed_during_collection")
    if sessions_before != tuple(sorted(sessions.iter_active())): reasons.add("sessions_changed_during_collection")
    return TaskScratchEvidence(task_id, not reasons, tuple(sorted(reasons)), boot_id, time.time_ns(), roots, cwds, fds)
