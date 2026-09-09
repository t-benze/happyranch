from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from runtime.daemon.sessions import SessionTracker
from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence
from runtime.daemon.routes.tasks import CancelBody, cancel_task
from runtime.daemon.zombie_reaper import (
    FLAG_TTL_NO_FINGERPRINT_SECONDS,
    STALE_HEARTBEAT_SECONDS,
    _consume_zombie_fingerprint,
    _pid_is_dead,
    _sweep_org_zombies,
)
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, CompletionReport, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus
from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.run_step import _maybe_resume_blocked_task, run_step_impl
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


def _task(task_id: str, status: TaskStatus, parent: str | None = None, revisit: str | None = None, executor_pid: int | None = None) -> TaskRecord:
    return TaskRecord(id=task_id, status=status, brief="x", assigned_agent="dev_agent", current_session_id="session", parent_task_id=parent, revisit_of_task_id=revisit, executor_pid=executor_pid, completed_at=datetime.now(timezone.utc) if status == TaskStatus.COMPLETED else None)


def _proc(proc: Path, pid: int, root: str = "/", cwd: str = "/", fd: str | None = None) -> None:
    item = proc / str(pid); (item / "fd").mkdir(parents=True)
    (item / "stat").write_text(f"{pid} (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (item / "root").symlink_to(root); (item / "cwd").symlink_to(cwd)
    if fd: (item / "fd" / "3").symlink_to(fd)


def _sources(tmp_path: Path) -> tuple[Database, Path]:
    proc = tmp_path / "proc"; (proc / "sys/kernel/random").mkdir(parents=True); (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000\n"); _proc(proc, 42)
    return Database(tmp_path / "state.db"), proc


@pytest.fixture
def evidence_sources(tmp_path: Path):
    """New observation rows own their database lifetime even on assertion failure."""
    db, proc = _sources(tmp_path)
    try:
        yield db, proc
    finally:
        db.close()


def test_actual_db_and_independent_proc_probes_are_fail_closed(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); root = tmp_path / "root"; root.mkdir()
    good = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert good.eligible
    _proc(proc, 43, root=str(root), cwd=str(root), fd=str(root / "held"))
    bad = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert {"process_root_reference", "process_cwd_reference", "open_fd_reference"} <= set(bad.reasons)


def test_parent_fanout_is_not_cycle_but_directed_revisit_cycle_is(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-root", TaskStatus.COMPLETED)); db.insert_task(_task("TASK-child", TaskStatus.COMPLETED, parent="TASK-root"))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-root", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "lineage_cycle" not in evidence.reasons
    db2, proc2 = _sources(tmp_path / "cycle"); db2.insert_task(_task("TASK-a", TaskStatus.COMPLETED, revisit="TASK-b")); db2.insert_task(_task("TASK-b", TaskStatus.COMPLETED, revisit="TASK-a"))
    assert "lineage_cycle" in collect_task_scratch_evidence(db=db2, sessions=SessionTracker(), task_id="TASK-a", root=tmp_path / "root", proc_root=proc2, monotonic_now=31, daemon_started_monotonic=0).reasons


def test_active_job_and_cleared_tracker_do_not_become_safe(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING, created_at=datetime.now(timezone.utc).isoformat()))
    sessions = SessionTracker(); sessions.set_active("TASK-1", "dev_agent", "s"); sessions.clear("TASK-1", "dev_agent")
    evidence = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and "active_job" in evidence.reasons


def test_missing_boot_and_proc_are_unavailable_not_zero(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db"); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=tmp_path / "missing", monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and {"boot_id_unavailable", "process_scan_unavailable"} <= set(evidence.reasons)
    assert evidence.process_roots is evidence.process_cwds is evidence.open_fds is None


def test_blank_boot_and_malformed_pid_identity_are_unavailable_not_zero(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    (proc / "sys/kernel/random/boot_id").write_text(" \n")
    blank = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "boot_id_unavailable" in blank.reasons
    assert blank.process_roots is blank.process_cwds is blank.open_fds is None
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000\n")
    (proc / "42/stat").write_text("42 (agent) S malformed")
    malformed = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "process_identity_unavailable" in malformed.reasons
    assert malformed.process_roots is malformed.process_cwds is malformed.open_fds is None


def test_durable_and_process_changes_during_observation_fail_closed(tmp_path: Path, monkeypatch) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); root = tmp_path / "root"; root.mkdir()
    import runtime.daemon.task_scratch_evidence as subject
    original = subject._scan
    def changed(*args, **kwargs):
        db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING, created_at=datetime.now(timezone.utc).isoformat()))
        result = original(*args, **kwargs)
        _proc(proc, 43, cwd=str(root)); (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174001\n")
        return result
    monkeypatch.setattr(subject, "_scan", changed)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert {"active_job", "durable_state_changed_during_collection", "process_population_changed_during_collection", "boot_id_changed_during_collection"} <= set(evidence.reasons)
    assert evidence.process_roots is evidence.process_cwds is evidence.open_fds is None


def test_snapshot_admission_stops_result_and_jobs_after_task_read(tmp_path: Path, monkeypatch) -> None:
    """An observed expiry admits no further DB observation, but does not preempt one."""
    db, _proc_root = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    import runtime.daemon.task_scratch_evidence as subject
    expired = False
    original_get_task = db.get_task
    def get_task(task_id: str):
        nonlocal expired
        result = original_get_task(task_id); expired = True
        return result
    monkeypatch.setattr(db, "get_task", get_task)
    monkeypatch.setattr(subject, "_expired", lambda _deadline: expired)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("post-expiry DB read started")
    monkeypatch.setattr(db, "get_latest_task_result", forbidden)
    monkeypatch.setattr(db, "list_jobs_db", forbidden)
    reasons: set[str] = set()
    assert subject._snapshot(db, "TASK-1", reasons, 1) is None
    assert reasons == {"observation_timeout"}


def test_complete_zero_measurement_survives_independent_ineligibility(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.PENDING))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible
    assert "nonterminal_or_unresolved_lineage" in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


def test_exported_collector_keeps_complete_os_measurements_when_durable_snapshot_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    """Durable authority loss rejects eligibility without falsifying OS counts."""
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    import runtime.daemon.task_scratch_evidence as subject
    monkeypatch.setattr(subject, "_snapshot", lambda *_args: None)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible
    assert "executor_identity_unavailable" in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


def test_exported_collector_stops_fd_readlink_after_expired_iterator_advance(tmp_path: Path, monkeypatch) -> None:
    """An fd iterator result observed after expiry cannot start readlink."""
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); (proc / "42/cwd").unlink(); (proc / "42/cwd").symlink_to(root)
    import runtime.daemon.task_scratch_evidence as subject
    expired = False; original_scandir = subject.os.scandir; original_readlink = subject.os.readlink
    class FdEntries:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self): return self
        def __next__(self):
            nonlocal expired
            if expired: raise StopIteration
            expired = True
            return type("Fd", (), {"path": str(proc / "42/fd/0")})()
    def scandir(path): return FdEntries() if str(path).endswith("42/fd") else original_scandir(path)
    def readlink(path):
        if str(path).endswith("42/fd/0"): raise AssertionError("post-expiry fd readlink started")
        return original_readlink(path)
    monkeypatch.setattr(subject.os, "scandir", scandir)
    monkeypatch.setattr(subject.os, "readlink", readlink)
    monkeypatch.setattr(subject, "_expired", lambda _deadline: expired)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "process_scan_timeout" in evidence.reasons
    assert evidence.process_roots is evidence.process_cwds is evidence.open_fds is None


def test_non_numeric_proc_entries_do_not_consume_population_budget(tmp_path: Path, monkeypatch) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    import runtime.daemon.task_scratch_evidence as subject
    monkeypatch.setattr(subject, "MAX_PROCESSES", 1)
    (proc / "sys").mkdir(exist_ok=True)
    assert collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).eligible


def test_retained_blocked_result_after_terminal_failure_is_not_pending(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.FAILED))
    db.insert_task_result("TASK-1", "dev_agent", "session", "blocked", 10, status="blocked")
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible


def test_active_chain_fanout_and_deep_or_unrelated_lineage(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-0", TaskStatus.COMPLETED))
    for index in range(1, 1501): db.insert_task(_task(f"TASK-{index}", TaskStatus.COMPLETED, parent=f"TASK-{index - 1}"))
    # An unrelated cycle is not a target-lineage defect.
    db.insert_task(_task("OTHER-A", TaskStatus.COMPLETED, revisit="OTHER-B")); db.insert_task(_task("OTHER-B", TaskStatus.COMPLETED, revisit="OTHER-A"))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-0", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "lineage_cycle" not in evidence.reasons
    db.update_task("TASK-0", status=TaskStatus.PENDING)
    assert "nonterminal_or_unresolved_lineage" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-0", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons


def test_full_get_task_row_and_retained_completion_are_authoritative(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path)
    db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    db.insert_task_result("TASK-1", "dev_agent", "session", "done", 100)
    good = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert good.eligible
    db.update_task_active_chain("TASK-1", "{\"state\": \"pending\"}")
    chained = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "nonterminal_or_unresolved_lineage" in chained.reasons
    db.update_task_active_chain("TASK-1", None); db.update_task_active_fanout("TASK-1", "{\"state\": \"pending\"}")
    fanout = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "nonterminal_or_unresolved_lineage" in fanout.reasons


def test_active_session_and_unresolved_recovery_are_not_safe(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    sessions = SessionTracker(); sessions.set_active("TASK-1", "dev_agent", "session")
    active = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "active_session" in active.reasons
    sessions.clear("TASK-1", "dev_agent"); db.insert_task_result("TASK-1", "dev_agent", "session", "working", 10, status="in_progress")
    unresolved = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "recovery_fingerprint_unresolved" in unresolved.reasons


def test_frozen_c1_unrelated_active_session_does_not_block_completed_target(tmp_path: Path) -> None:
    """Frozen #30: a tracker entry for another task is not target authority."""
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    sessions = SessionTracker(); sessions.set_active("TASK-OTHER", "dev_agent", "other")
    evidence = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible and "active_session" not in evidence.reasons


def test_frozen_c2_owned_pending_job_blocks_completed_target(tmp_path: Path) -> None:
    """Frozen #43: an owned PENDING job is an active_job authority denial."""
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    db.insert_job(JobRecord(id="JOB-PENDING", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.PENDING, created_at=datetime.now(timezone.utc).isoformat()))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and "active_job" in evidence.reasons


def test_boot_is_uuid_and_final_change_is_rejected(tmp_path: Path, monkeypatch) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    (proc / "sys/kernel/random/boot_id").write_text("not-a-uuid\n")
    assert "boot_id_unavailable" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000\n")
    import runtime.daemon.task_scratch_evidence as subject
    original = subject._snapshot
    calls = 0
    def changing(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2: (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174001\n")
        return result
    monkeypatch.setattr(subject, "_snapshot", changing)
    assert "boot_id_changed_during_collection" in subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons


def test_linked_jobs_require_owned_terminal_records_and_fresh_snapshot(tmp_path: Path, monkeypatch) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", blocked_on_job_ids=json.dumps(["JOB-1"]))
    bad = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "linked_job_authority_unavailable" in bad.reasons
    db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    assert collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).eligible
    db.update_task("TASK-1", blocked_on_job_ids="[]")
    assert "linked_job_authority_unavailable" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons


def test_exported_collector_charges_unsuccessful_linked_lookups_before_read(tmp_path: Path, monkeypatch) -> None:
    """Each snapshot admits at most MAX_JOBS linked reads, regardless of row validity."""
    db, proc = _sources(tmp_path)
    for task_id, linked in (("TASK-1", "MISSING-1"), ("TASK-2", "MISSING-2")):
        db.insert_task(_task(task_id, TaskStatus.COMPLETED, parent="TASK-1" if task_id == "TASK-2" else None))
        db.update_task(task_id, blocked_on_job_ids=json.dumps([linked]))
    import runtime.daemon.task_scratch_evidence as subject
    calls: list[str] = []
    original = db.get_job
    def observed(job_id: str):
        calls.append(job_id)
        return original(job_id)
    monkeypatch.setattr(subject, "MAX_JOBS", 1)
    monkeypatch.setattr(db, "get_job", observed)
    evidence = subject.collect_task_scratch_evidence(
        db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root",
        proc_root=proc, monotonic_now=31, daemon_started_monotonic=0,
    )
    assert "job_scan_capped" in evidence.reasons
    assert calls == ["MISSING-2", "MISSING-2"]


def _observe_all_evidence_sources(
    *, db: Database, proc: Path, sessions: SessionTracker, monkeypatch: pytest.MonkeyPatch,
    trigger: str | None = None,
) -> tuple[list[tuple[str, str, str, str, bool]], list[int]]:
    """Transparent A1/A2 observer: forward every production reader unchanged.

    The clock changes only after the selected real reader returns.  Recording is
    deliberately outside collector exception handling so an assertion below
    sees the complete attempted observation, including iterator exhaustion.
    """
    import runtime.daemon.task_scratch_evidence as subject
    clock = [0]
    events: list[tuple[str, str, str, str, bool]] = []
    proc_opens = 0; stats: dict[str, int] = {}; calls: dict[str, int] = {}

    def record(kind: str, family: str, identity: str, phase: str) -> None:
        events.append((kind, family, identity, phase, clock[0] > subject.SCAN_NS))

    def fire(family: str, identity: str, phase: str) -> None:
        nonlocal proc_opens
        if trigger is None:
            return
        selected = (
            family == "db" and trigger == f"db:{identity}:{calls[identity]}" or
            trigger == "boot" and family == "boot" or
            trigger == "sessions" and family == "sessions" or
            trigger == "cwd" and identity.endswith(":cwd") or
            trigger == "fd-readlink" and family == "readlink" and ":fd:" in identity or
            trigger == "fd-next" and family == "iterator" and identity.endswith(":fd:1") or
            trigger == "population-next" and family == "iterator" and identity.endswith(":proc:1") or
            trigger == "initial-population-open" and family == "scandir" and identity == "proc" and phase == "initial" or
            trigger == "final-population-open" and family == "scandir" and identity == "proc" and phase == "final" or
            trigger == "final-pid-stat" and family == "stat" and identity == "42" and phase == "final"
        )
        if selected:
            clock[0] = subject.SCAN_NS + 1

    for name in ("list_tasks", "get_task", "get_latest_task_result", "list_jobs_db", "get_job"):
        original = getattr(db, name)
        def wrapped(*args, _name=name, _original=original, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            record("START", "db", _name, "snapshot")
            value = _original(*args, **kwargs)
            record("RETURN", "db", _name, "snapshot"); fire("db", _name, "snapshot")
            return value
        monkeypatch.setattr(db, name, wrapped)

    original_text, original_scandir, original_readlink = Path.read_text, subject.os.scandir, subject.os.readlink
    def read_text(path: Path, *args, **kwargs):
        text = str(path)
        if text.endswith("boot_id"):
            family, identity, phase = "boot", "boot", "read"
        elif text.startswith(str(proc)) and text.endswith("/stat"):
            identity = Path(path).parent.name; stats[identity] = stats.get(identity, 0) + 1
            family, phase = "stat", "initial" if stats[identity] == 1 else "final"
        else:
            return original_text(path, *args, **kwargs)
        record("START", family, identity, phase)
        value = original_text(path, *args, **kwargs)
        record("RETURN", family, identity, phase); fire(family, identity, phase)
        return value
    def scandir(path):
        nonlocal proc_opens
        text = str(path)
        if text == str(proc):
            proc_opens += 1; family, identity, phase = "scandir", "proc", "initial" if proc_opens == 1 else "final"
        elif text.startswith(str(proc)) and text.endswith("/fd"):
            family, identity, phase = "scandir", f"{Path(path).parent.name}:fd", "scan"
        else:
            return original_scandir(path)
        record("START", family, identity, phase)
        value = original_scandir(path)
        record("RETURN", family, identity, phase); fire(family, identity, phase)
        kind = "proc" if identity == "proc" else "fd"
        advances = 0
        class Iterator:
            def __enter__(self): value.__enter__(); return self
            def __exit__(self, *args): return value.__exit__(*args)
            def __iter__(self): return self
            def __next__(self):
                nonlocal advances
                number = advances + 1; item_identity = f"{identity}:{kind}:{number}"
                record("START", "iterator", item_identity, phase)
                try: item = next(value)
                except StopIteration:
                    record("EXHAUSTED", "iterator", kind, phase); raise
                advances = number
                record("RETURN", "iterator", item_identity, phase); fire("iterator", item_identity, phase)
                return item
        return Iterator()
    def readlink(path):
        text = str(path)
        if not text.startswith(str(proc)):
            return original_readlink(path)
        bits = Path(path).parts; pid = Path(path).parent.name if "/fd/" not in text else Path(path).parent.parent.name
        name = Path(path).name; identity = f"{pid}:{name}" if "/fd/" not in text else f"{pid}:fd:{name}"
        record("START", "readlink", identity, "read")
        value = original_readlink(path)
        record("RETURN", "readlink", identity, "read"); fire("readlink", identity, "read")
        return value
    original_iter = sessions.iter_active
    def iter_active():
        record("START", "sessions", "iter_active", "snapshot")
        value = original_iter()
        record("RETURN", "sessions", "iter_active", "snapshot"); fire("sessions", "iter_active", "snapshot")
        return value
    monkeypatch.setattr(Path, "read_text", read_text); monkeypatch.setattr(subject.os, "scandir", scandir)
    monkeypatch.setattr(subject.os, "readlink", readlink); monkeypatch.setattr(sessions, "iter_active", iter_active)
    monkeypatch.setattr(subject.time, "monotonic_ns", lambda: clock[0])
    return events, clock


@pytest.mark.parametrize("reader,call", [
    ("get_task", 1), ("get_task", 2),
    ("get_latest_task_result", 1), ("get_latest_task_result", 2),
    ("list_jobs_db", 1), ("list_jobs_db", 2), ("get_job", 1),
])
def test_exported_collector_stops_all_source_reads_after_db_return(
    tmp_path: Path, monkeypatch, evidence_sources, reader: str, call: int,
) -> None:
    """A1p: each admitted DB return is a shared-deadline admission boundary."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    if reader == "get_job":
        db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
        db.update_task("TASK-1", blocked_on_job_ids='["JOB-1"]')
    sessions = SessionTracker(); events, clock = _observe_all_evidence_sources(db=db, proc=proc, sessions=sessions, monkeypatch=monkeypatch, trigger=f"db:{reader}:{call}")
    import runtime.daemon.task_scratch_evidence as subject
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert clock[0] > subject.SCAN_NS and "observation_timeout" in evidence.reasons
    assert sum(event[0] == "RETURN" and event[1:4] == ("db", reader, "snapshot") for event in events) >= call, events
    assert not [event for event in events if event[0] == "START" and event[4]], events
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (None, None, None)


def test_exported_collector_unexpired_control_observes_every_source_family(
    tmp_path: Path, monkeypatch, evidence_sources,
) -> None:
    """A1p control: forwarding instrumentation reaches DB, boot, proc and sessions."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    db.update_task("TASK-1", blocked_on_job_ids='["JOB-1"]')
    sessions = SessionTracker(); events, _clock = _observe_all_evidence_sources(db=db, proc=proc, sessions=sessions, monkeypatch=monkeypatch)
    import runtime.daemon.task_scratch_evidence as subject
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    families = {event[1] for event in events}
    assert evidence.eligible and {"db", "boot", "stat", "scandir", "iterator", "readlink", "sessions"} <= families, events
    assert {("stat", "42", "initial"), ("stat", "42", "final"), ("scandir", "proc", "initial"), ("scandir", "proc", "final")} <= {event[1:4] for event in events}, events
    assert any(event[0] == "EXHAUSTED" and event[2] == "proc" for event in events), events


@pytest.mark.parametrize("trigger,fds", [
    ("cwd", 1), ("fd-next", 1), ("fd-readlink", 2),
    ("initial-population-open", 0), ("final-population-open", 0),
    ("population-next", 0), ("final-pid-stat", 0), ("boot", 0), ("sessions", 0),
])
def test_exported_collector_expiry_boundaries_do_not_start_dependent_proc_reads(
    tmp_path: Path, monkeypatch, evidence_sources, trigger: str, fds: int,
) -> None:
    """A2p: source opens/advances are admitted; an in-flight call is not cancelled."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); _proc(proc, 43, root=str(root), cwd=str(root), fd=str(root / "held"))
    if fds == 2:
        (proc / "43/fd" / "4").symlink_to(root / "held-second")
    sessions = SessionTracker(); events, clock = _observe_all_evidence_sources(db=db, proc=proc, sessions=sessions, monkeypatch=monkeypatch, trigger=trigger)
    import runtime.daemon.task_scratch_evidence as subject
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert clock[0] > subject.SCAN_NS, (trigger, events)
    assert not evidence.eligible
    assert not [event for event in events if event[0] == "START" and event[4]], (trigger, events)
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (None, None, None)


@pytest.mark.parametrize("kind,expected_calls,reason", [
    ("foreign", 2, "linked_job_authority_unavailable"), ("running", 0, "job_scan_capped"),
    ("terminal", 0, "job_scan_capped"), ("duplicate", 0, "linked_job_authority_unavailable"),
])
def test_exported_collector_linked_job_cap_is_per_snapshot_and_pre_read(
    tmp_path: Path, monkeypatch, evidence_sources, kind: str, expected_calls: int, reason: str,
) -> None:
    """A3p: listed rows and every linked attempt consume the same per-snapshot cap."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    linked = "JOB-X" if kind != "duplicate" else "JOB-X"
    db.update_task("TASK-1", blocked_on_job_ids=json.dumps([linked, linked] if kind == "duplicate" else [linked]))
    if kind != "foreign":
        db.insert_job(JobRecord(id=linked, task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING if kind == "running" else JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    else:
        db.insert_job(JobRecord(id=linked, task_id="OTHER", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    import runtime.daemon.task_scratch_evidence as subject
    calls: list[str] = []; original = db.get_job
    monkeypatch.setattr(subject, "MAX_JOBS", 1)
    monkeypatch.setattr(db, "get_job", lambda job_id: calls.append(job_id) or original(job_id))
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert calls == [linked] * expected_calls and reason in evidence.reasons


def test_exported_collector_terminal_link_has_default_cap_positive(tmp_path: Path, monkeypatch, evidence_sources) -> None:
    """A3p: each snapshot admits the same owned terminal link at the real cap."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    db.insert_job(JobRecord(id="JOB-T", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    db.update_task("TASK-1", blocked_on_job_ids='["JOB-T"]')
    calls: list[str] = []; original = db.get_job
    monkeypatch.setattr(db, "get_job", lambda job_id: calls.append(job_id) or original(job_id))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible and calls == ["JOB-T", "JOB-T"]


@pytest.mark.parametrize("case,reason", [
    ("warmup", "zombie_warmup"), ("session", "active_session"), ("job", "active_job"), ("missing", "recovery_authority_unavailable"), ("pid", "executor_pid_live_or_ambiguous"),
])
def test_exported_collector_retains_complete_zero_counts_for_independent_rejection(tmp_path: Path, evidence_sources, case: str, reason: str) -> None:
    """B1p: independent authority rejection never turns a complete zero into unavailable."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED));
    if case == "pid": db.update_task("TASK-1", executor_pid=42)
    sessions = SessionTracker(); now = 0 if case == "warmup" else 31
    if case == "session": sessions.set_active("TASK-1", "dev_agent", "session")
    if case == "job": db.insert_job(JobRecord(id="JOB-1", task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING, created_at=datetime.now(timezone.utc).isoformat()))
    if case == "missing": db.update_task("TASK-1", assigned_agent=None)
    evidence = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=now, daemon_started_monotonic=0)
    assert reason in evidence.reasons and (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


@pytest.mark.parametrize("reader", ["list_tasks", "get_job"])
def test_exported_collector_retains_complete_measurements_on_real_durable_reader_failure(tmp_path: Path, monkeypatch, evidence_sources, reader: str) -> None:
    """B1p: a real durable-reader failure rejects authority but not a complete scan."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    if reader == "get_job": db.update_task("TASK-1", blocked_on_job_ids='["JOB-BOOM"]')
    def boom(*_args, **_kwargs): raise RuntimeError("reader failure")
    monkeypatch.setattr(db, reader, boom)
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    expected = "task_scan_unavailable" if reader == "list_tasks" else "linked_job_authority_unavailable"
    assert not evidence.eligible and expected in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


def test_exported_collector_open_fd_cap_is_unavailable_not_complete_zero(tmp_path: Path, monkeypatch, evidence_sources) -> None:
    """B1p: a capped fd scan is a measurement failure, never a zero count."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); _proc(proc, 43, fd=str(root / "one")); (proc / "43" / "fd" / "4").symlink_to(root / "two")
    import runtime.daemon.task_scratch_evidence as subject
    monkeypatch.setattr(subject, "MAX_FDS_PER_PROCESS", 1)
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "open_fd_scan_capped" in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (None, None, None)


def test_exported_collector_missing_only_owned_pid_fd_is_unavailable(tmp_path: Path, evidence_sources) -> None:
    """C3 #72: valid boot/PID/root/cwd do not turn a missing PID fd dir into zero."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); _proc(proc, 43, root=str(root), cwd=str(root))
    for child in (proc / "43/fd").iterdir(): child.unlink()
    (proc / "43/fd").rmdir()
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible
    assert "process_population_unavailable" in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (None, None, None)


@pytest.mark.parametrize("probe,relation,expected_reason", [
    ("root", "exact", "process_root_reference"), ("root", "descendant", "process_root_reference"), ("root", "prefix", None),
    ("cwd", "exact", "process_cwd_reference"), ("cwd", "descendant", "process_cwd_reference"), ("cwd", "prefix", None),
    ("fd", "exact", "open_fd_reference"), ("fd", "descendant", "open_fd_reference"), ("fd", "prefix", None),
])
def test_exported_collector_isolated_containment_counts(tmp_path: Path, evidence_sources, probe: str, relation: str, expected_reason: str) -> None:
    """C3 #62-70: each source and containment relation is measured independently."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); descendant = root / "child"; descendant.mkdir(); sibling = tmp_path / "root-sibling"; sibling.mkdir()
    target = {"exact": root, "descendant": descendant, "prefix": sibling}[relation]
    values = {"root": "/", "cwd": "/", "fd": None}; values[probe] = str(target)
    _proc(proc, 43, root=values["root"], cwd=values["cwd"], fd=values["fd"])
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    expected = (0, 0, 0) if expected_reason is None else {"root": (1, 0, 0), "cwd": (0, 1, 0), "fd": (0, 0, 1)}[probe]
    assert (evidence.eligible if expected_reason is None else not evidence.eligible)
    assert expected_reason is None or expected_reason in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == expected


def test_exported_collector_complete_zero_is_eligible(tmp_path: Path, evidence_sources) -> None:
    """B1p/C3 #71: a complete independent zero is a truthful eligible observation."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible and evidence.reasons == ()
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


def test_exported_collector_combined_fake_proc_counts_are_exact_and_ineligible(tmp_path: Path, evidence_sources) -> None:
    """C3: synthetic proc sources retain the exact combined (1, 1, 1) evidence."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); _proc(proc, 43, root=str(root), cwd=str(root), fd=str(root / "held"))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible
    assert {"process_root_reference", "process_cwd_reference", "open_fd_reference"} <= set(evidence.reasons)
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (1, 1, 1)


def test_exported_collector_unreadable_fd_is_unavailable(tmp_path: Path, monkeypatch, evidence_sources) -> None:
    """C3 #73: test-owned permission ambiguity is unavailable, never a zero."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); root = tmp_path / "root"; root.mkdir(); _proc(proc, 43)
    import runtime.daemon.task_scratch_evidence as subject
    original_scandir = subject.os.scandir
    def denied(path):
        if str(path).endswith("/43/fd"): raise PermissionError("test-owned fd denial")
        return original_scandir(path)
    monkeypatch.setattr(subject.os, "scandir", denied)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and "test-owned fd denial" in evidence.reasons
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (None, None, None)


@pytest.mark.skipif(os.name != "posix" or not Path("/proc").is_dir(), reason="requires Linux-style procfs for the bounded child observation")
def test_exported_collector_observes_only_test_owned_child_cwd_and_fd(tmp_path: Path, evidence_sources) -> None:
    """C3 #34: one owned child is projected into a synthetic proc root and reaped."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    root = tmp_path / "root"; root.mkdir(); held = root / "held"; held.write_text("x")
    child = subprocess.Popen([sys.executable, "-c", "import time; f=open('held'); print('ready', flush=True); time.sleep(30)"], cwd=root, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready and child.stdout.readline() == "ready\n"
        (proc / str(child.pid)).symlink_to(Path("/proc") / str(child.pid), target_is_directory=True)
        evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
        assert not evidence.eligible
        assert "process_cwd_reference" in evidence.reasons and "open_fd_reference" in evidence.reasons
        assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 1, 1)
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()


def test_cleared_session_with_live_executor_pid_is_ineligible(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", executor_pid=42)
    sessions = SessionTracker(); sessions.set_active("TASK-1", "dev_agent", "session"); sessions.clear("TASK-1", "dev_agent")
    evidence = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "executor_pid_live_or_ambiguous" in evidence.reasons
    db.update_task("TASK-1", executor_pid=-1)
    assert "executor_pid_unavailable" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons


@pytest.mark.parametrize("change", ["pid", "session"])
def test_exported_collector_independently_detects_pid_identity_flip_and_session_transition(tmp_path: Path, monkeypatch, evidence_sources, change: str) -> None:
    """C1 #27/#31: final OS and session observations are independent evidence."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", executor_pid=42)
    import runtime.daemon.task_scratch_evidence as subject
    sessions = SessionTracker(); original_scan = subject._scan
    def change_after_initial(*args, **kwargs):
        result = original_scan(*args, **kwargs)
        if change == "pid": (proc / "42/stat").write_text("42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 10 0")
        else: sessions.set_active("TASK-1", "dev_agent", "later")
        return result
    monkeypatch.setattr(subject, "_scan", change_after_initial)
    changed = subject.collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    expected = {"executor_pid_live_or_ambiguous", "executor_identity_changed_during_collection", "process_population_changed_during_collection"} if change == "pid" else {"sessions_changed_during_collection", "executor_pid_live_or_ambiguous"}
    assert expected <= set(changed.reasons)
    sessions.clear("TASK-1", "dev_agent")
    monkeypatch.setattr(subject, "_scan", lambda *args, **kwargs: original_scan(*args, **kwargs))
    original_population = subject._population; calls = 0
    def disappearing(*args, **kwargs):
        nonlocal calls
        value = original_population(*args, **kwargs); calls += 1
        if calls == 2: (proc / "42").rename(proc / "gone")
        return value
    monkeypatch.setattr(subject, "_population", disappearing)
    gone = subject.collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert {"process_identity_unavailable", "executor_identity_changed_during_collection"} <= set(gone.reasons)
    assert (gone.process_roots, gone.process_cwds, gone.open_fds) == (None, None, None)


def test_exported_collector_accepts_persisted_pid_already_absent_with_complete_os_counts(tmp_path: Path, evidence_sources) -> None:
    """C1: an absent persisted PID is not mistaken for a live ambiguous PID."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", executor_pid=4242)
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible and evidence.reasons == ()
    assert (evidence.process_roots, evidence.process_cwds, evidence.open_fds) == (0, 0, 0)


@pytest.mark.parametrize("field", ["assigned_agent", "current_session_id"])
def test_exported_collector_missing_recovery_authority_has_exact_reason(tmp_path: Path, evidence_sources, field: str) -> None:
    """C1 #32/#33: each persisted authority half is mandatory."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", **{field: None})
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and "recovery_authority_unavailable" in evidence.reasons


def test_exported_collector_task_and_job_n_plus_one_caps_are_unavailable(tmp_path: Path, monkeypatch, evidence_sources) -> None:
    """C2 #48/#55/#61: every bounded source rejects its N+1th member."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.insert_task(_task("TASK-2", TaskStatus.COMPLETED))
    import runtime.daemon.task_scratch_evidence as subject
    monkeypatch.setattr(subject, "MAX_TASKS", 1)
    task_capped = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "task_scan_capped" in task_capped.reasons
    monkeypatch.setattr(subject, "MAX_TASKS", 10); db.update_task("TASK-2", parent_task_id="TASK-1")
    for ident in ("JOB-1", "JOB-2"):
        db.insert_job(JobRecord(id=ident, task_id="TASK-1", agent_name="dev_agent", title="x", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    monkeypatch.setattr(subject, "MAX_JOBS", 1)
    job_capped = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "job_scan_capped" in job_capped.reasons


@pytest.mark.parametrize("mutation", ["result", "job_identity", "job_insertion", "parent", "descendant"])
def test_exported_collector_rechecks_late_durable_mutations(tmp_path: Path, monkeypatch, evidence_sources, mutation: str) -> None:
    """C2 #50/#52-54: a post-scan durable mutation invalidates the observation."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    if mutation == "job_identity":
        db.insert_job(JobRecord(id="JOB-fixed", task_id="TASK-1", agent_name="dev_agent", title="before", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
    import runtime.daemon.task_scratch_evidence as subject
    original = subject._scan
    def mutate(*args, **kwargs):
        value = original(*args, **kwargs)
        if mutation == "result": db.insert_task_result("TASK-1", "dev_agent", "session", "late", 1)
        elif mutation == "job_identity":
            db._conn.execute("UPDATE jobs SET title = ? WHERE id = ?", ("after", "JOB-fixed"))
            db._conn.commit()
        elif mutation == "job_insertion": db.insert_job(JobRecord(id="JOB-late", task_id="TASK-1", agent_name="dev_agent", title="late", rationale="x", script_text="true", interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc).isoformat()))
        elif mutation == "descendant": db.insert_task(_task("TASK-late", TaskStatus.PENDING, parent="TASK-1"))
        else:
            db._conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", ("TASK-late", "TASK-1"))
            db._conn.commit()
        return value
    monkeypatch.setattr(subject, "_scan", mutate)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "durable_state_changed_during_collection" in evidence.reasons and not evidence.eligible


def test_exported_collector_isolates_same_task_id_between_databases(tmp_path: Path) -> None:
    """C4 #78: identical task IDs cannot transfer authority between org databases."""
    db_a, proc_a = _sources(tmp_path / "a"); db_b, proc_b = _sources(tmp_path / "b")
    try:
        db_a.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db_b.insert_task(_task("TASK-1", TaskStatus.PENDING))
        assert collect_task_scratch_evidence(db=db_a, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc_a, monotonic_now=31, daemon_started_monotonic=0).eligible
        denied = collect_task_scratch_evidence(db=db_b, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc_b, monotonic_now=31, daemon_started_monotonic=0)
        assert not denied.eligible and "nonterminal_or_unresolved_lineage" in denied.reasons
    finally:
        db_a.close(); db_b.close()


@pytest.mark.parametrize("status", [TaskStatus.CANCELLED, TaskStatus.SUPERSEDED])
def test_exported_collector_accepts_terminal_cancelled_and_superseded_alone(tmp_path: Path, evidence_sources, status: TaskStatus) -> None:
    """C8 #12/#13: terminal state alone is eligible without a hidden parent rule."""
    db, proc = evidence_sources; db.insert_task(_task("TASK-1", status))
    assert collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).eligible


def test_exported_collector_reports_missing_target_and_lineage_edge(tmp_path: Path, evidence_sources) -> None:
    """C8 #14/#22: absent target and explicit missing lineage edge fail closed."""
    db, proc = evidence_sources
    missing = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-none", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "task_missing" in missing.reasons
    db.insert_task(_task("TASK-1", TaskStatus.COMPLETED, parent="TASK-gone"))
    edge = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "lineage_missing" in edge.reasons and not edge.eligible


def test_exported_collector_rejects_nonterminal_revisit_descendant(tmp_path: Path, evidence_sources) -> None:
    """C8 #16: a revisit descendant remains in the target component."""
    db, proc = evidence_sources
    db.insert_task(_task("TASK-1", TaskStatus.COMPLETED))
    db.insert_task(_task("TASK-revisit", TaskStatus.PENDING, revisit="TASK-1"))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible and "nonterminal_or_unresolved_lineage" in evidence.reasons


def test_real_zombie_sweep_clears_permission_indeterminate_pid_once(tmp_path: Path, monkeypatch) -> None:
    """C6: the shipping sweep treats an unprobeable PID as alive and clears once."""
    db, _proc_root = _sources(tmp_path)
    try:
        db.insert_task(_task("TASK-1", TaskStatus.IN_PROGRESS)); now = datetime.now(timezone.utc)
        db.update_task("TASK-1", executor_pid=4242, last_heartbeat=(now - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 1)).isoformat(), zombie_flagged_at=(now - timedelta(seconds=1)).isoformat())
        import runtime.daemon.zombie_reaper as subject
        def denied(_pid: int, _signal: int) -> None: raise PermissionError("test-owned pid probe denial")
        monkeypatch.setattr(subject.os, "kill", denied)
        _sweep_org_zombies(db, now=now, uptime=31, warm_up_seconds=30)
        assert db.get_task("TASK-1").zombie_flagged_at is None
        assert [row["action"] for row in db.get_audit_logs("TASK-1")].count("zombie_cleared") == 1
        _sweep_org_zombies(db, now=now, uptime=31, warm_up_seconds=30)
        assert [row["action"] for row in db.get_audit_logs("TASK-1")].count("zombie_cleared") == 1
    finally:
        db.close()


def _durable_recovery_snapshot(db: Database, task_id: str, queue: _Queue) -> tuple[object, list[dict], list[dict], list[tuple[str, str]]]:
    """Exact relevant effects around the shipping sweep/consumer seam."""
    return db.get_task(task_id), db.get_task_results(task_id), db.get_audit_logs(task_id), list(queue.items)


def test_zombie_sweep_does_not_consume_stale_session_fingerprint(tmp_path: Path, monkeypatch, request: pytest.FixtureRequest) -> None:
    """C7: only a current-session result can consume a flagged fingerprint."""
    db, orch, queue, _proc_root = _recovery_fixture(tmp_path, request)
    try:
        now = datetime.now(timezone.utc); db.insert_task(TaskRecord(id="TASK-1", brief="x", status=TaskStatus.IN_PROGRESS, task_type="subtask", assigned_agent="dev_agent", current_session_id="session"))
        db.update_task("TASK-1", executor_pid=4242, last_heartbeat=(now - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 1)).isoformat(), zombie_flagged_at=(now - timedelta(seconds=1)).isoformat())
        db.insert_task_result("TASK-1", "dev_agent", "old-session", "old", 1, status="completed")
        before = _durable_recovery_snapshot(db, "TASK-1", queue)
        import runtime.daemon.zombie_reaper as subject
        monkeypatch.setattr(subject, "_pid_is_dead", lambda _pid: True)
        _sweep_org_zombies(db, now=now, uptime=31, warm_up_seconds=30, orchestrator=orch)
        still = db.get_task("TASK-1")
        assert still.status is TaskStatus.IN_PROGRESS and still.zombie_flagged_at is not None
        assert _durable_recovery_snapshot(db, "TASK-1", queue) == before
    finally:
        db.close()


def test_zombie_sweep_keeps_current_fingerprint_flagged_without_orchestrator_then_consumes(tmp_path: Path, monkeypatch, request: pytest.FixtureRequest) -> None:
    """C7: absence of the consumer preserves the flag; the shipping consumer consumes it."""
    db, orch, queue, _proc_root = _recovery_fixture(tmp_path, request)
    try:
        now = datetime.now(timezone.utc); db.insert_task(TaskRecord(id="TASK-1", brief="x", status=TaskStatus.IN_PROGRESS, task_type="subtask", assigned_agent="dev_agent", current_session_id="session"))
        db.update_task("TASK-1", executor_pid=4242, last_heartbeat=(now - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 1)).isoformat(), zombie_flagged_at=(now - timedelta(seconds=1)).isoformat())
        db.insert_task_result("TASK-1", "dev_agent", "session", "done", 1, status="completed")
        import runtime.daemon.zombie_reaper as subject
        monkeypatch.setattr(subject, "_pid_is_dead", lambda _pid: True)
        before = _durable_recovery_snapshot(db, "TASK-1", queue)
        _sweep_org_zombies(db, now=now, uptime=31, warm_up_seconds=30)
        assert db.get_task("TASK-1").zombie_flagged_at is not None
        assert _durable_recovery_snapshot(db, "TASK-1", queue) == before
        _sweep_org_zombies(db, now=now, uptime=31, warm_up_seconds=30, orchestrator=orch)
        after = db.get_task("TASK-1")
        after_results, after_audit = db.get_task_results("TASK-1"), db.get_audit_logs("TASK-1")
        # The completion tail owns exactly these task fields; all other
        # persisted task identity/lifecycle inputs survive recovery unchanged.
        assert after.status is TaskStatus.COMPLETED
        assert after.zombie_flagged_at is None
        assert after.block_kind is None and after.note == "done" and after.final_output_dir is None
        assert after.completed_at is not None and after.completed_at >= now
        for field in ("id", "brief", "task_type", "assigned_agent", "current_session_id", "executor_pid", "last_heartbeat", "parent_task_id", "revisit_of_task_id", "blocked_on_job_ids"):
            assert getattr(after, field) == getattr(before[0], field), field
        assert after_results == before[1] and queue.items == before[3]
        assert after_audit[:-1] == before[2]
        assert len(after_audit) == len(before[2]) + 1
        cleared = after_audit[-1]
        assert cleared["task_id"] == "TASK-1" and cleared["agent"] == "dev_agent"
        assert cleared["action"] == "zombie_cleared"
        assert cleared["payload"] == {"reason": "zombie recovered — flag cleared"}
        assert isinstance(cleared["id"], int) and cleared["id"] > 0
        assert isinstance(cleared["timestamp"], str) and datetime.fromisoformat(cleared["timestamp"]) >= now
    finally:
        db.close()


def _recovery_orchestrator(db: Database) -> MagicMock:
    """Minimal adapter: only external notification/execution remains stubbed.

    The recovery consumer, task/result writes and audit writer are the shipping
    implementations.  These tests intentionally do not pre-seed a terminal
    task and call it a consumed recovery result.
    """
    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._update_task_history = MagicMock()
    return orch


class _Queue:
    """Minimal real put/get queue seam used by the production dispatcher."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def put_nowait(self, slug: str, task_id: str) -> None:
        self.items.append((slug, task_id))

    def enqueue(self, slug: str, task_id: str, *, metadata: dict) -> None:
        """Record the shipping resume queue shape as well as child dispatch."""
        self.items.append((slug, task_id, metadata))


def _recovery_fixture(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Database, Orchestrator, _Queue, Path]:
    """Build an isolated org with actual persistence and dispatch writers."""
    runtime = RuntimeDir.init(tmp_path / "runtime")
    paths = OrgPaths(root=runtime.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n"
        "    workers: [dev_agent, qa_engineer]\n"
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    for name, role in (("engineering_head", "manager"), ("dev_agent", "worker"), ("qa_engineer", "worker")):
        definition = AgentDef(name=name, team="engineering", role=role, executor="claude",
                              allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
                              enrolled_at=None, system_prompt=name, description="", model=None)
        (paths.agents_dir / f"{name}.md").write_text(render_agent_text(definition))
        (paths.workspaces_dir / name).mkdir(parents=True, exist_ok=True)
    db = Database(paths.db_path)
    request.addfinalizer(db.close)
    orch = Orchestrator(db=db, settings=Settings(), paths=paths, slug="test", teams=TeamsRegistry.load(paths.root))
    queue = _Queue()
    orch._queue = queue
    _, proc = _sources(tmp_path / "evidence")
    return db, orch, queue, proc


def _recover_decision(db: Database, orch: Orchestrator, decision: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    db.insert_task(TaskRecord(id="TASK-ROOT", brief="root", status=TaskStatus.IN_PROGRESS,
                              assigned_agent="engineering_head", current_session_id="root-session",
                              task_type="task", zombie_flagged_at=now, last_heartbeat=now))
    db.insert_task_result("TASK-ROOT", "engineering_head", "root-session", "decision", 1,
                          status="completed", decision_json=json.dumps(decision))
    fingerprint = db.get_latest_task_result("TASK-ROOT", "engineering_head", "root-session")
    assert fingerprint is not None
    _consume_zombie_fingerprint(db, "TASK-ROOT", fingerprint, db.get_task("TASK-ROOT"), orch)
    db.update_task("TASK-ROOT", zombie_flagged_at=None)
    return fingerprint


def test_real_zombie_consumer_done_retains_result_and_becomes_observable_safe(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-DONE", status=TaskStatus.IN_PROGRESS, task_type="subtask",
        brief="x", assigned_agent="dev_agent", current_session_id="session",
    ))
    db.insert_task_result("TASK-DONE", "dev_agent", "session", "done", 100, status="completed")
    fingerprint = db.get_latest_task_result("TASK-DONE", "dev_agent", "session")
    assert fingerprint is not None

    _consume_zombie_fingerprint(db, "TASK-DONE", fingerprint, db.get_task("TASK-DONE"), _recovery_orchestrator(db))

    after = db.get_task("TASK-DONE")
    assert after.status is TaskStatus.COMPLETED
    assert after.block_kind is None
    assert db.get_latest_task_result("TASK-DONE", "dev_agent", "session")["id"] == fingerprint["id"]
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-DONE", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible


def test_real_zombie_consumer_blocked_without_jobs_fails_but_retains_result(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-BLOCKED", status=TaskStatus.IN_PROGRESS, task_type="subtask",
        brief="x", assigned_agent="dev_agent", current_session_id="session",
    ))
    db.insert_task_result("TASK-BLOCKED", "dev_agent", "session", "waiting", 0, status="blocked")
    fingerprint = db.get_latest_task_result("TASK-BLOCKED", "dev_agent", "session")
    assert fingerprint is not None

    _consume_zombie_fingerprint(db, "TASK-BLOCKED", fingerprint, db.get_task("TASK-BLOCKED"), _recovery_orchestrator(db))

    after = db.get_task("TASK-BLOCKED")
    assert after.status is TaskStatus.FAILED
    assert after.block_kind is None
    assert db.get_latest_task_result("TASK-BLOCKED", "dev_agent", "session")["id"] == fingerprint["id"]
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-BLOCKED", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert evidence.eligible


def test_real_zombie_consumer_blocked_with_owned_job_remains_ineligible(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-WAIT", status=TaskStatus.IN_PROGRESS, task_type="subtask",
        brief="x", assigned_agent="dev_agent", current_session_id="session",
    ))
    db.insert_job(JobRecord(
        id="JOB-WAIT", task_id="TASK-WAIT", agent_name="dev_agent", title="x",
        rationale="x", script_text="true", interpreter=JobInterpreter.BASH,
        status=JobStatus.RUNNING, created_at=datetime.now(timezone.utc).isoformat(),
    ))
    db.insert_task_result(
        "TASK-WAIT", "dev_agent", "session", "waiting", 0, status="blocked",
        waiting_on_job_ids=["JOB-WAIT"],
    )
    fingerprint = db.get_latest_task_result("TASK-WAIT", "dev_agent", "session")
    assert fingerprint is not None

    _consume_zombie_fingerprint(db, "TASK-WAIT", fingerprint, db.get_task("TASK-WAIT"), _recovery_orchestrator(db))

    after = db.get_task("TASK-WAIT")
    assert after.status is TaskStatus.IN_PROGRESS
    assert after.blocked_on_job_ids == '["JOB-WAIT"]'
    assert any(row["action"] == "task_blocked_on_jobs" for row in db.get_audit_logs("TASK-WAIT"))
    evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-WAIT", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert not evidence.eligible
    assert {"active_job", "nonterminal_or_unresolved_lineage"} <= set(evidence.reasons)


@pytest.mark.parametrize("terminal_status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED])
def test_recovered_blocked_job_resumes_through_shipping_cas_and_typed_completion(
    tmp_path: Path, monkeypatch, request: pytest.FixtureRequest, terminal_status: JobStatus,
) -> None:
    """L3: real recovery and terminal writers precede the resume CAS/completion."""
    db, orch, queue, proc = _recovery_fixture(tmp_path, request)
    db.insert_task(TaskRecord(id="TASK-JOB", brief="resume", status=TaskStatus.IN_PROGRESS,
                              task_type="subtask", assigned_agent="dev_agent", current_session_id="resume-session"))
    db.insert_job(JobRecord(id="JOB-TERM", task_id="TASK-JOB", agent_name="dev_agent",
                            title="x", rationale="x", script_text="true",
                            interpreter=JobInterpreter.BASH,
                            status=JobStatus.PENDING if terminal_status is JobStatus.REJECTED else JobStatus.RUNNING,
                            created_at=datetime.now(timezone.utc).isoformat()))
    db.insert_task_result("TASK-JOB", "dev_agent", "resume-session", "waiting", 0,
                          status="blocked", waiting_on_job_ids=["JOB-TERM"])
    fingerprint = db.get_latest_task_result("TASK-JOB", "dev_agent", "resume-session")
    assert fingerprint is not None
    _consume_zombie_fingerprint(db, "TASK-JOB", fingerprint, db.get_task("TASK-JOB"), orch)
    parked = db.get_task("TASK-JOB")
    assert parked is not None and parked.block_kind is BlockKind.BLOCKED_ON_JOB
    assert parked.blocked_on_job_ids == '["JOB-TERM"]'
    assert db.get_latest_task_result("TASK-JOB", "dev_agent", "resume-session")["id"] == fingerprint["id"]
    parked_evidence = collect_task_scratch_evidence(
        db=db, sessions=SessionTracker(), task_id="TASK-JOB", root=tmp_path / "root",
        proc_root=proc, monotonic_now=31, daemon_started_monotonic=0,
    )
    assert not parked_evidence.eligible
    assert "nonterminal_or_unresolved_lineage" in parked_evidence.reasons

    assert not _maybe_resume_blocked_task(orch, "TASK-JOB", trigger="job_terminal",
                                          triggering_job_id="JOB-TERM")
    assert queue.items == []
    if terminal_status is JobStatus.REJECTED:
        db.transition_job_to_rejected("JOB-TERM", reviewer="qa_engineer", reason="test", reviewed_at="2026-01-01T00:00:00+00:00")
    else:
        db.transition_job_to_terminal("JOB-TERM", status=terminal_status, exit_code=0 if terminal_status is JobStatus.COMPLETED else 1,
                                      finished_at="2026-01-01T00:00:00+00:00", duration_ms=1,
                                      stdout_head="test", stderr_head="" if terminal_status is JobStatus.COMPLETED else "failed")
    terminal = db.get_job("JOB-TERM")
    assert terminal is not None and terminal.status is terminal_status
    if terminal_status is JobStatus.REJECTED:
        assert terminal.reviewed_by == "qa_engineer" and terminal.reject_reason == "test"
    else:
        assert terminal.exit_code == (0 if terminal_status is JobStatus.COMPLETED else 1)
    assert _maybe_resume_blocked_task(orch, "TASK-JOB", trigger="job_terminal",
                                      triggering_job_id="JOB-TERM")
    assert queue.items == [("test", "TASK-JOB", {"trigger": "job_terminal", "triggering_job_id": "JOB-TERM"})]
    # Immediately after the shipping enqueue, before external execution, the
    # persisted task and same fingerprint result retain their parked authority.
    post_enqueue_task = db.get_task("TASK-JOB")
    post_enqueue_result = db.get_latest_task_result("TASK-JOB", "dev_agent", "resume-session")
    assert post_enqueue_task is not None
    assert (post_enqueue_task.status, post_enqueue_task.block_kind, post_enqueue_task.blocked_on_job_ids) == (
        TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB, '["JOB-TERM"]',
    )
    assert post_enqueue_result is not None and post_enqueue_result["id"] == fingerprint["id"]

    observed: list[tuple[TaskStatus, BlockKind | None, str | None, int]] = []
    def _successful_external_executor(*_args):
        active = db.get_task("TASK-JOB")
        assert active is not None
        reread = db.get_latest_task_result("TASK-JOB", "dev_agent", "resume-session")
        assert reread is not None
        observed.append((active.status, active.block_kind, active.blocked_on_job_ids, reread["id"]))
        in_executor = collect_task_scratch_evidence(
            db=db, sessions=SessionTracker(), task_id="TASK-JOB", root=tmp_path / "root",
            proc_root=proc, monotonic_now=31, daemon_started_monotonic=0,
        )
        assert not in_executor.eligible
        assert "nonterminal_or_unresolved_lineage" in in_executor.reasons
        return (
            ExecutorResult(success=True, duration_seconds=1, session_id="resumed-session", returncode=0),
            CompletionReport(task_id="TASK-JOB", agent="dev_agent", status="completed", confidence=100,
                             output_summary="typed normal completion"),
        )
    # Only external execution is stubbed; admission, CAS, audit, result and
    # completion consumer remain the shipping seams.
    monkeypatch.setattr(orch, "_run_agent", _successful_external_executor)
    run_step_impl(orch, "TASK-JOB", metadata=queue.items[-1][2])
    after = db.get_task("TASK-JOB")
    # The CAS clears the parked discriminator; the historical linked-job
    # field is retained by the shipping writer for resume provenance.
    assert observed == [(TaskStatus.IN_PROGRESS, None, '["JOB-TERM"]', fingerprint["id"])]
    assert after is not None and after.status is TaskStatus.COMPLETED and after.block_kind is None
    assert after.blocked_on_job_ids == '["JOB-TERM"]'
    resumed = [row for row in db.get_audit_logs("TASK-JOB") if row["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1 and resumed[0]["payload"]["job_outcomes"] == {"JOB-TERM": terminal_status.value}
    assert resumed[0]["payload"] == {
        "trigger": "job_terminal", "triggering_job_id": "JOB-TERM",
        "job_outcomes": {"JOB-TERM": terminal_status.value}, "blocking_job_ids": ["JOB-TERM"],
    }
    assert db.get_job_status("JOB-TERM") == terminal_status.value
    retained = db.get_latest_task_result("TASK-JOB", "dev_agent", "resume-session")
    assert retained is not None and retained["id"] == fingerprint["id"]
    # The external executor return is not a transport-persistence claim: this
    # seam retains the recovered blocked result and proves the consumer's
    # terminal task transition, not a newly fabricated task-result row.
    evidence = collect_task_scratch_evidence(
        db=db, sessions=SessionTracker(), task_id="TASK-JOB", root=tmp_path / "root",
        proc_root=proc, monotonic_now=31, daemon_started_monotonic=0,
    )
    assert evidence.eligible


def test_sweep_selection_warmup_and_flagged_fingerprint_use_shipping_path(tmp_path: Path, monkeypatch, request: pytest.FixtureRequest) -> None:
    """L4: selection belongs to the sweep, including warm-up and flag phases."""
    db, orch, queue, _ = _recovery_fixture(tmp_path, request)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.insert_task(TaskRecord(id="TASK-SWEEP", brief="x", status=TaskStatus.IN_PROGRESS,
                              assigned_agent="engineering_head", current_session_id="sweep-session",
                              last_heartbeat=(now.replace(year=2025)).isoformat(), executor_pid=123))
    db.update_task("TASK-SWEEP", last_heartbeat=(now.replace(year=2025)).isoformat(), executor_pid=123)
    db.insert_task_result("TASK-SWEEP", "engineering_head", "sweep-session", "done", 100, status="completed")
    monkeypatch.setattr("runtime.daemon.zombie_reaper._pid_is_dead", lambda pid: True)
    _sweep_org_zombies(db, now=now, uptime=0, warm_up_seconds=30, orchestrator=orch)
    assert db.get_task("TASK-SWEEP").zombie_flagged_at is None
    _sweep_org_zombies(db, now=now, uptime=999, warm_up_seconds=30, orchestrator=orch)
    assert db.get_task("TASK-SWEEP").zombie_flagged_at is not None
    _sweep_org_zombies(db, now=now, uptime=999, warm_up_seconds=30, orchestrator=orch)
    after = db.get_task("TASK-SWEEP")
    # A root result with no decision follows the shipping conservative escalation
    # branch; the assertion is selection/consumption, not an invented success.
    assert after is not None and after.status is TaskStatus.ESCALATED and after.zombie_flagged_at is None
    actions = [row["action"] for row in db.get_audit_logs("TASK-SWEEP")]
    assert actions.count("zombie_flagged") == 1 and actions.count("zombie_cleared") == 1
    assert queue.items == []


def test_sweep_warmup_heartbeat_process_and_no_fingerprint_ttl_selection(
    tmp_path: Path, monkeypatch, request: pytest.FixtureRequest,
) -> None:
    """L4: real sweep predicates retain state until the exact safe selection."""
    db, orch, queue, proc = _recovery_fixture(tmp_path, request)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stale = (now - timedelta(seconds=STALE_HEARTBEAT_SECONDS)).isoformat()
    db.insert_task(TaskRecord(id="TASK-L4", brief="x", status=TaskStatus.IN_PROGRESS,
                              assigned_agent="dev_agent", current_session_id="s",
                              last_heartbeat=stale, executor_pid=123))
    db.update_task("TASK-L4", last_heartbeat=stale, executor_pid=123)
    before = (db.get_task("TASK-L4"), db.get_task_results("TASK-L4"), db.get_audit_logs("TASK-L4"), list(queue.items))
    monkeypatch.setattr("runtime.daemon.zombie_reaper._pid_is_dead", lambda _pid: True)
    _sweep_org_zombies(db, now=now, uptime=29, warm_up_seconds=30, orchestrator=orch)
    assert (db.get_task("TASK-L4"), db.get_task_results("TASK-L4"), db.get_audit_logs("TASK-L4"), list(queue.items)) == before
    assert "zombie_warmup" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-L4", root=tmp_path / "root", proc_root=proc, monotonic_now=29, daemon_started_monotonic=0).reasons
    _sweep_org_zombies(db, now=now, uptime=30, warm_up_seconds=30, orchestrator=orch)
    flagged = db.get_task("TASK-L4")
    assert flagged is not None and flagged.zombie_flagged_at is not None
    assert not collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-L4", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).eligible
    db.update_task("TASK-L4", last_heartbeat=now.isoformat())
    _sweep_org_zombies(db, now=now, uptime=30, warm_up_seconds=30, orchestrator=orch)
    assert db.get_task("TASK-L4").zombie_flagged_at is None
    assert [r["action"] for r in db.get_audit_logs("TASK-L4")].count("zombie_cleared") == 1
    db.update_task("TASK-L4", last_heartbeat=stale, zombie_flagged_at=now.isoformat())
    monkeypatch.setattr("runtime.daemon.zombie_reaper._pid_is_dead", lambda _pid: False)
    _sweep_org_zombies(db, now=now, uptime=30, warm_up_seconds=30, orchestrator=orch)
    assert db.get_task("TASK-L4").zombie_flagged_at is None
    db.update_task("TASK-L4", zombie_flagged_at=now.isoformat(), executor_pid=None)
    _sweep_org_zombies(db, now=now, uptime=30, warm_up_seconds=30, orchestrator=orch)
    assert db.get_task("TASK-L4").zombie_flagged_at is not None
    db.update_task("TASK-L4", executor_pid=123,
                   zombie_flagged_at=(now - timedelta(seconds=FLAG_TTL_NO_FINGERPRINT_SECONDS)).isoformat())
    monkeypatch.setattr("runtime.daemon.zombie_reaper._pid_is_dead", lambda _pid: True)
    _sweep_org_zombies(db, now=now, uptime=30, warm_up_seconds=30, orchestrator=orch)
    cancelled = db.get_task("TASK-L4")
    assert cancelled is not None and cancelled.status is TaskStatus.CANCELLED and cancelled.block_kind is None
    assert [r["action"] for r in db.get_audit_logs("TASK-L4")].count("zombie_cancelled") == 1
    assert db.get_task_results("TASK-L4") == [] and queue.items == []
    assert not collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-L4", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).eligible


def test_pid_permission_ambiguity_is_not_a_dead_process(monkeypatch) -> None:
    """L4 process probe: permission ambiguity has the shipping conservative effect."""
    def denied(_pid: int, _signal: int) -> None:
        raise PermissionError
    monkeypatch.setattr("runtime.daemon.zombie_reaper.os.kill", denied)
    assert not _pid_is_dead(123)


def test_recovery_delegate_and_then_persist_real_lineage_effects(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    for decision, requires_chain in (
        ({"action": "delegate", "agent": "dev_agent", "prompt": "implement"}, False),
        ({"action": "delegate", "agent": "dev_agent", "prompt": "implement",
          "then": [{"agent": "qa_engineer", "prompt": "verify"}]}, True),
    ):
        db, orch, queue, proc = _recovery_fixture(tmp_path / ("chain" if requires_chain else "plain"), request)
        fingerprint = _recover_decision(db, orch, decision)
        parent = db.get_task("TASK-ROOT")
        children = db.get_children("TASK-ROOT")
        assert parent is not None and len(children) == 1
        child = db.get_task(children[0])
        assert child is not None
        assert (parent.status, parent.block_kind) == (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED)
        assert (child.status, child.parent_task_id, child.assigned_agent, child.brief) == (TaskStatus.PENDING, "TASK-ROOT", "dev_agent", "implement")
        assert (parent.active_chain is not None) is requires_chain
        assert queue.items == [("test", child.id)]
        assert db.get_latest_task_result("TASK-ROOT", "engineering_head", "root-session")["id"] == fingerprint["id"]
        assert "orchestration_step" in [row["action"] for row in db.get_audit_logs("TASK-ROOT")]
        for task_id in ("TASK-ROOT", child.id):
            evidence = collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id=task_id,
                                                     root=tmp_path / "root", proc_root=proc,
                                                     monotonic_now=31, daemon_started_monotonic=0)
            assert not evidence.eligible


def test_recovery_fanout_persists_children_audit_queue_and_ineligible_lineage(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    db, orch, queue, proc = _recovery_fixture(tmp_path, request)
    fingerprint = _recover_decision(db, orch, {
        "action": "fanout", "children": [
            {"agent": "dev_agent", "prompt": "one"}, {"agent": "qa_engineer", "prompt": "two"},
        ], "width_cap_ack": 2,
    })
    parent = db.get_task("TASK-ROOT")
    children = [db.get_task(child_id) for child_id in db.get_children("TASK-ROOT")]
    assert parent is not None and len(children) == 2 and parent.active_fanout is not None
    assert (parent.status, parent.block_kind) == (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED)
    assert {(child.parent_task_id, child.assigned_agent, child.status) for child in children if child} == {
        ("TASK-ROOT", "dev_agent", TaskStatus.PENDING), ("TASK-ROOT", "qa_engineer", TaskStatus.PENDING),
    }
    assert {task_id for _, task_id in queue.items} == {child.id for child in children if child}
    assert {"orchestration_step", "fanout_spawned"} <= {row["action"] for row in db.get_audit_logs("TASK-ROOT")}
    assert db.get_latest_task_result("TASK-ROOT", "engineering_head", "root-session")["id"] == fingerprint["id"]
    for task_id in ["TASK-ROOT", *[child.id for child in children if child]]:
        assert not collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id=task_id,
                                                 root=tmp_path / "root", proc_root=proc,
                                                 monotonic_now=31, daemon_started_monotonic=0).eligible


def test_recovery_delegated_child_failure_wakes_parent_once_and_stays_ineligible(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    db, orch, queue, proc = _recovery_fixture(tmp_path, request)
    _recover_decision(db, orch, {"action": "delegate", "agent": "dev_agent", "prompt": "implement"})
    child_id = db.get_children("TASK-ROOT")[0]
    queue.items.clear()
    db.update_task(child_id, status=TaskStatus.IN_PROGRESS, current_session_id="child-session")
    db.insert_task_result(child_id, "dev_agent", "child-session", "blocked", 1, status="blocked")
    fingerprint = db.get_latest_task_result(child_id, "dev_agent", "child-session")
    assert fingerprint is not None
    _consume_zombie_fingerprint(db, child_id, fingerprint, db.get_task(child_id), orch)
    child = db.get_task(child_id)
    assert child is not None and child.status is TaskStatus.FAILED and child.block_kind is None
    assert db.get_latest_task_result(child_id, "dev_agent", "child-session")["id"] == fingerprint["id"]
    assert queue.items == [("test", "TASK-ROOT")]
    for task_id in ("TASK-ROOT", child_id):
        assert not collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id=task_id,
                                                 root=tmp_path / "root", proc_root=proc,
                                                 monotonic_now=31, daemon_started_monotonic=0).eligible


def test_recovery_sweep_replay_guard_has_no_second_effect(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    db, orch, queue, _ = _recovery_fixture(tmp_path, request)
    _recover_decision(db, orch, {"action": "delegate", "agent": "dev_agent", "prompt": "implement"})
    before = (list(db.get_children("TASK-ROOT")), list(queue.items), list(db.get_audit_logs("TASK-ROOT")))
    _sweep_org_zombies(db, now=datetime.now(timezone.utc), uptime=999, warm_up_seconds=0, orchestrator=orch)
    after = (list(db.get_children("TASK-ROOT")), list(queue.items), list(db.get_audit_logs("TASK-ROOT")))
    assert after == before  # parked IN_PROGRESS(DELEGATED) is outside the shipping sweep allowlist.


def test_cancel_route_cascades_real_recovered_tree_and_preserves_conservative_evidence(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    for cascade in (True, False):
        db, orch, queue, proc = _recovery_fixture(tmp_path / str(cascade), request)
        _recover_decision(db, orch, {"action": "delegate", "agent": "dev_agent", "prompt": "implement"})
        child_id = db.get_children("TASK-ROOT")[0]
        org = MagicMock(db=db, orchestrator=orch, sessions=SessionTracker(), db_lock=asyncio.Lock())
        org.event_bus.publish = AsyncMock()
        result = asyncio.run(cancel_task("TASK-ROOT", CancelBody(rationale="test", cascade=cascade), org))
        parent, child = db.get_task("TASK-ROOT"), db.get_task(child_id)
        assert result["cancelled"] == (["TASK-ROOT", child_id] if cascade else ["TASK-ROOT"])
        assert parent is not None and parent.status is TaskStatus.CANCELLED and parent.cancelled_at
        assert child is not None and child.status is (TaskStatus.CANCELLED if cascade else TaskStatus.PENDING)
        assert {"task_cancelled"} <= {row["action"] for row in db.get_audit_logs("TASK-ROOT")}
        assert not collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-ROOT",
                                                 root=tmp_path / "root", proc_root=proc,
                                                 monotonic_now=31, daemon_started_monotonic=0).eligible
