"""Dormant bounded observations for later scratch reconciliation, never a permit."""
from __future__ import annotations

import os
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.models import JobStatus, TaskStatus

if TYPE_CHECKING:
    from runtime.daemon.sessions import SessionTracker
    from runtime.infrastructure.database import Database

MAX_TASKS = MAX_JOBS = MAX_PROCESSES = MAX_FDS_PER_PROCESS = 10_000
SCAN_NS = 5_000_000_000
WARMUP_SECONDS = 30.0
TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED}
TERMINAL_JOBS = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED}
_BOOT_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


@dataclass(frozen=True)
class TaskScratchEvidence:
    task_id: str; eligible: bool; reasons: tuple[str, ...]; boot_id: str | None
    observed_at_ns: int; process_roots: int | None; process_cwds: int | None; open_fds: int | None
    freshness_limited: bool = True


def _under(value: str, root: Path) -> bool:
    value = os.path.normpath(value.removesuffix(" (deleted)")); base = os.path.normpath(str(root))
    return value == base or value.startswith(base + os.sep)


def _expired(deadline: int) -> bool: return time.monotonic_ns() > deadline


def _pid_start(entry: Path) -> str:
    """Use precisely one stat sample for a PID identity."""
    try:
        stat = (entry / "stat").read_text()
        token = stat[stat.rindex(")") + 1:].split()[19]
        if not token.isdecimal():
            raise ValueError("malformed PID start identity")
        return token
    except (OSError, ValueError, IndexError) as exc: raise RuntimeError("process_identity_unavailable") from exc


def _population(proc: Path, deadline: int) -> list[Path] | None:
    try:
        rows: list[Path] = []
        with os.scandir(proc) as entries:
            for entry in entries:
                if _expired(deadline): return None
                if not entry.name.isdecimal(): continue
                if len(rows) >= MAX_PROCESSES: return None
                rows.append(Path(entry.path))
        return sorted(rows)
    except OSError: return None


def _scan(proc: Path, root: Path, deadline: int) -> tuple[int | None, int | None, int | None, set[str], tuple[tuple[str, str], ...] | None]:
    entries = _population(proc, deadline)
    if entries is None: return None, None, None, {"process_scan_unavailable"}, None
    roots = cwds = fds = 0; reasons: set[str] = set(); identities: list[tuple[str, str]] = []
    try:
        for entry in entries:
            if _expired(deadline): raise RuntimeError("process_scan_timeout")
            identities.append((entry.name, _pid_start(entry)))
            for name, reason in (("root", "process_root_reference"), ("cwd", "process_cwd_reference")):
                if _expired(deadline): raise RuntimeError("process_scan_timeout")
                if _under(os.readlink(entry / name), root):
                    roots += name == "root"; cwds += name == "cwd"; reasons.add(reason)
            count = 0
            with os.scandir(entry / "fd") as fd_entries:
                for fd in fd_entries:
                    count += 1
                    if _expired(deadline): raise RuntimeError("process_scan_timeout")
                    if count > MAX_FDS_PER_PROCESS: raise RuntimeError("open_fd_scan_capped")
                    if _under(os.readlink(fd.path), root): fds += 1; reasons.add("open_fd_reference")
    except (OSError, RuntimeError) as exc:
        return None, None, None, {str(exc) or "process_reference_unavailable"}, None
    return roots, cwds, fds, reasons, tuple(identities)


def _component(tasks: list[object], task_id: str, reasons: set[str], deadline: int) -> set[str]:
    by_id = {task.id: task for task in tasks}
    if task_id not in by_id: return set()
    adj = {key: set() for key in by_id}
    for task in tasks:
        if _expired(deadline): reasons.add("observation_timeout"); return set()
        for edge in (task.parent_task_id, task.revisit_of_task_id):
            if edge in by_id: adj[task.id].add(edge); adj[edge].add(task.id)
    found: set[str] = set(); pending = [task_id]
    while pending:
        if _expired(deadline): reasons.add("observation_timeout"); return set()
        node = pending.pop()
        if node in found: continue
        found.add(node)
        if len(found) > MAX_TASKS: reasons.add("lineage_capped"); return found
        pending.extend(adj[node] - found)
    # Directed, iterative cycle detection only in the relevant component.
    color: dict[str, int] = {}
    for start in found:
        if _expired(deadline): reasons.add("observation_timeout"); return set()
        if color.get(start): continue
        stack = [(start, False)]
        while stack:
            if _expired(deadline): reasons.add("observation_timeout"); return set()
            node, leaving = stack.pop()
            if leaving: color[node] = 2; continue
            if color.get(node) == 1: reasons.add("lineage_cycle"); continue
            if color.get(node) == 2: continue
            color[node] = 1; stack.append((node, True))
            for edge in (by_id[node].parent_task_id, by_id[node].revisit_of_task_id):
                if edge and edge in found: stack.append((edge, False))
                elif edge: reasons.add("lineage_missing")
    return found


def _shape(task: object) -> tuple[tuple[str, str], ...]:
    """Include every persisted record member, not the list projection subset."""
    return tuple(sorted((name, repr(value)) for name, value in vars(task).items()))


def _snapshot(db: Database, task_id: str, reasons: set[str], deadline: int) -> tuple[tuple[object, ...], ...] | None:
    if _expired(deadline): reasons.add("observation_timeout"); return None
    try: tasks = db.list_tasks(limit=MAX_TASKS + 1)
    except Exception: reasons.add("task_scan_unavailable"); return None
    if len(tasks) > MAX_TASKS: reasons.add("task_scan_capped"); return None
    members = _component(tasks, task_id, reasons, deadline)
    if not members: reasons.add("task_missing"); return None
    out: list[tuple[object, ...]] = []
    for listed in tasks:
        if listed.id not in members: continue
        if _expired(deadline): reasons.add("observation_timeout"); return None
        try: task = db.get_task(listed.id)
        except Exception: reasons.add("task_authority_unavailable"); return None
        if task is None or task.id != listed.id: reasons.add("task_authority_unavailable"); return None
        out.append(("task", task.id, _shape(task)))
        if task.executor_pid is not None:
            if not isinstance(task.executor_pid, int) or isinstance(task.executor_pid, bool) or task.executor_pid <= 0:
                reasons.add("executor_pid_unavailable")
            else:
                out.append(("executor", task.id, task.executor_pid))
        if task.status not in TERMINAL or task.zombie_flagged_at or task.active_chain or task.active_fanout: reasons.add("nonterminal_or_unresolved_lineage")
        if not task.assigned_agent or not task.current_session_id: reasons.add("recovery_authority_unavailable")
        else:
            try:
                result = db.get_latest_task_result(task.id, task.assigned_agent, task.current_session_id)
                out.append(("result", task.id, repr(result)))
                # A retained blocked receipt is not itself authority that recovery is
                # pending: the real consumer may have failed the task and cleared its
                # block state while retaining that audit/result row.
                if result is not None and result.get("status") in {"in_progress", "working"}:
                    reasons.add("recovery_fingerprint_unresolved")
            except Exception: reasons.add("recovery_fingerprint_unavailable")
        try: jobs = db.list_jobs_db(task_id=task.id, limit=MAX_JOBS + 1)
        except Exception: reasons.add("job_scan_unavailable"); continue
        if len(jobs) > MAX_JOBS: reasons.add("job_scan_capped"); continue
        if sum(1 for item in out if item[0] == "job") + len(jobs) > MAX_JOBS:
            reasons.add("job_scan_capped"); return None
        for job in jobs:
            if _expired(deadline): reasons.add("observation_timeout"); return None
            out.append(("job", task.id, job.id, _shape(job)))
            if job.status not in TERMINAL_JOBS: reasons.add("active_job")
        if task.blocked_on_job_ids is not None:
            try:
                linked_ids = json.loads(task.blocked_on_job_ids)
                if not isinstance(linked_ids, list) or not linked_ids or any(not isinstance(value, str) or not value for value in linked_ids):
                    raise ValueError
            except (json.JSONDecodeError, ValueError, TypeError):
                reasons.add("linked_job_authority_unavailable")
                continue
            if len(linked_ids) > MAX_JOBS or len(set(linked_ids)) != len(linked_ids):
                reasons.add("linked_job_authority_unavailable")
                continue
            for linked_id in linked_ids:
                if _expired(deadline): reasons.add("observation_timeout"); return None
                try:
                    linked = db.get_job(linked_id)
                except Exception:
                    reasons.add("linked_job_authority_unavailable"); return None
                if linked is None or linked.task_id != task.id or linked.status not in TERMINAL_JOBS:
                    reasons.add("linked_job_authority_unavailable")
                else:
                    out.append(("linked_job", task.id, linked.id, linked.status.value))
    return tuple(sorted(out, key=repr))


def _executor_pids(snapshot: tuple[tuple[object, ...], ...] | None) -> set[str] | None:
    if snapshot is None:
        return None
    values = {str(row[2]) for row in snapshot if row and row[0] == "executor"}
    return values


def collect_task_scratch_evidence(*, db: Database, sessions: SessionTracker, task_id: str, root: Path, proc_root: Path = Path("/proc"), monotonic_now: float | None = None, daemon_started_monotonic: float | None = None) -> TaskScratchEvidence:
    """Finite shared-deadline observation; blocking OS/DB calls are not preemptible."""
    reasons: set[str] = set(); now = time.monotonic() if monotonic_now is None else monotonic_now
    if daemon_started_monotonic is None or now - daemon_started_monotonic < WARMUP_SECONDS: reasons.add("zombie_warmup")
    deadline = time.monotonic_ns() + SCAN_NS
    def boot() -> str | None:
        if _expired(deadline): reasons.add("observation_timeout"); return None
        try:
            value = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
            if not _BOOT_UUID.fullmatch(value):
                reasons.add("boot_id_unavailable")
                return None
            return value
        except OSError: reasons.add("boot_id_unavailable"); return None
    old_boot = boot(); before = _snapshot(db, task_id, reasons, deadline)
    if _expired(deadline): reasons.add("observation_timeout"); sessions_before = ()
    else: sessions_before = tuple(sorted(sessions.iter_active()))
    member_ids = {item[1] for item in before or () if item and item[0] == "task"}
    if any(row[0] in member_ids for row in sessions_before): reasons.add("active_session")
    roots, cwds, fds, scan_reasons, identities = _scan(proc_root, root, deadline); reasons.update(scan_reasons)
    before_scan_boot = boot(); after = _snapshot(db, task_id, reasons, deadline)
    if before != after: reasons.add("durable_state_changed_during_collection")
    if _expired(deadline): reasons.add("observation_timeout")
    elif sessions_before != tuple(sorted(sessions.iter_active())): reasons.add("sessions_changed_during_collection")
    population = _population(proc_root, deadline)
    final_identities: list[tuple[str, str]] | None = None
    if identities is None or population is None: reasons.add("process_population_unavailable")
    else:
        try:
            final_identities = []
            for entry in population:
                if _expired(deadline): raise RuntimeError("process_scan_timeout")
                final_identities.append((entry.name, _pid_start(entry)))
            if tuple(final_identities) != identities: reasons.add("process_population_changed_during_collection")
        except RuntimeError: reasons.add("process_identity_unavailable")
    final_boot = boot()
    before_executor_pids = _executor_pids(before)
    after_executor_pids = _executor_pids(after)
    if before_executor_pids != after_executor_pids:
        reasons.add("executor_pid_changed_during_collection")
    elif before_executor_pids is None or identities is None or final_identities is None:
        reasons.add("executor_identity_unavailable")
    else:
        initial_identity = dict(identities)
        final_identity = dict(final_identities)
        for pid in before_executor_pids:
            # A persisted PID has no persisted start/boot identity.  A live
            # matching PID is therefore ambiguous and must never be released.
            if pid in initial_identity or pid in final_identity:
                reasons.add("executor_pid_live_or_ambiguous")
            if (pid in initial_identity) != (pid in final_identity) or (
                pid in initial_identity and initial_identity[pid] != final_identity[pid]
            ):
                reasons.add("executor_identity_changed_during_collection")
    if old_boot != before_scan_boot or before_scan_boot != final_boot: reasons.add("boot_id_changed_during_collection")
    if _expired(deadline): reasons.add("observation_timeout")
    if reasons or roots is None or cwds is None or fds is None or old_boot is None or before_scan_boot is None or final_boot is None:
        roots = cwds = fds = None
    return TaskScratchEvidence(task_id, not reasons, tuple(sorted(reasons)), final_boot, time.time_ns(), roots, cwds, fds)
