from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from runtime.daemon.sessions import SessionTracker
from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence
from runtime.daemon.routes.tasks import CancelBody, cancel_task
from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint, _sweep_org_zombies
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus
from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
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


def test_cleared_session_with_live_executor_pid_is_ineligible(tmp_path: Path) -> None:
    db, proc = _sources(tmp_path); db.insert_task(_task("TASK-1", TaskStatus.COMPLETED)); db.update_task("TASK-1", executor_pid=42)
    sessions = SessionTracker(); sessions.set_active("TASK-1", "dev_agent", "session"); sessions.clear("TASK-1", "dev_agent")
    evidence = collect_task_scratch_evidence(db=db, sessions=sessions, task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert "executor_pid_live_or_ambiguous" in evidence.reasons
    db.update_task("TASK-1", executor_pid=-1)
    assert "executor_pid_unavailable" in collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=tmp_path / "root", proc_root=proc, monotonic_now=31, daemon_started_monotonic=0).reasons


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


def _recovery_fixture(tmp_path: Path) -> tuple[Database, Orchestrator, _Queue, Path]:
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


def test_blocked_job_resume_enqueues_then_shipping_cas_and_completion_run(tmp_path: Path, monkeypatch) -> None:
    """L3: enqueue is read-only; the shipping CAS is the later state change."""
    db, orch, queue, proc = _recovery_fixture(tmp_path)
    db.insert_task(TaskRecord(id="TASK-JOB", brief="resume", status=TaskStatus.IN_PROGRESS,
                              assigned_agent="dev_agent", current_session_id="resume-session"))
    db.update_task("TASK-JOB", block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids=json.dumps(["JOB-TERM"]))
    db.insert_job(JobRecord(id="JOB-TERM", task_id="TASK-JOB", agent_name="dev_agent",
                            title="x", rationale="x", script_text="true",
                            interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING,
                            created_at=datetime.now(timezone.utc).isoformat()))

    assert not _maybe_resume_blocked_task(orch, "TASK-JOB", trigger="job_terminal",
                                          triggering_job_id="JOB-TERM")
    assert queue.items == []
    # The job runner's durable job row is the authority consumed by the resume
    # predicate; this test changes only its temporary fixture row.
    db._conn.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", ("JOB-TERM",))
    db._conn.commit()
    assert _maybe_resume_blocked_task(orch, "TASK-JOB", trigger="job_terminal",
                                      triggering_job_id="JOB-TERM")
    assert queue.items == [("test", "TASK-JOB", {"trigger": "job_terminal", "triggering_job_id": "JOB-TERM"})]
    parked = db.get_task("TASK-JOB")
    assert parked is not None and parked.status is TaskStatus.IN_PROGRESS and parked.block_kind is BlockKind.BLOCKED_ON_JOB

    # Only the external agent execution is isolated.  Admission, CAS, resume
    # audit and terminal writer execute through the shipping implementation.
    monkeypatch.setattr(orch, "_run_agent", lambda *args: (_ for _ in ()).throw(RuntimeError("test-owned executor stub")))
    run_step_impl(orch, "TASK-JOB", metadata=queue.items[-1][2])
    after = db.get_task("TASK-JOB")
    assert after is not None and after.status is TaskStatus.FAILED and after.block_kind is None
    resumed = [row for row in db.get_audit_logs("TASK-JOB") if row["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1 and resumed[0]["payload"]["job_outcomes"] == {"JOB-TERM": "completed"}
    assert db.get_job_status("JOB-TERM") == "completed"
    assert "nonterminal_or_unresolved_lineage" not in collect_task_scratch_evidence(
        db=db, sessions=SessionTracker(), task_id="TASK-JOB", root=tmp_path / "root",
        proc_root=proc, monotonic_now=31, daemon_started_monotonic=0,
    ).reasons
    db.close()


def test_sweep_selection_warmup_and_flagged_fingerprint_use_shipping_path(tmp_path: Path, monkeypatch) -> None:
    """L4: selection belongs to the sweep, including warm-up and flag phases."""
    db, orch, queue, _ = _recovery_fixture(tmp_path)
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
    db.close()


def test_recovery_delegate_and_then_persist_real_lineage_effects(tmp_path: Path) -> None:
    for decision, requires_chain in (
        ({"action": "delegate", "agent": "dev_agent", "prompt": "implement"}, False),
        ({"action": "delegate", "agent": "dev_agent", "prompt": "implement",
          "then": [{"agent": "qa_engineer", "prompt": "verify"}]}, True),
    ):
        db, orch, queue, proc = _recovery_fixture(tmp_path / ("chain" if requires_chain else "plain"))
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
        db.close()


def test_recovery_fanout_persists_children_audit_queue_and_ineligible_lineage(tmp_path: Path) -> None:
    db, orch, queue, proc = _recovery_fixture(tmp_path)
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
    db.close()


def test_recovery_delegated_child_failure_wakes_parent_once_and_stays_ineligible(tmp_path: Path) -> None:
    db, orch, queue, proc = _recovery_fixture(tmp_path)
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
    db.close()


def test_recovery_sweep_replay_guard_has_no_second_effect(tmp_path: Path) -> None:
    db, orch, queue, _ = _recovery_fixture(tmp_path)
    _recover_decision(db, orch, {"action": "delegate", "agent": "dev_agent", "prompt": "implement"})
    before = (list(db.get_children("TASK-ROOT")), list(queue.items), list(db.get_audit_logs("TASK-ROOT")))
    _sweep_org_zombies(db, now=datetime.now(timezone.utc), uptime=999, warm_up_seconds=0, orchestrator=orch)
    after = (list(db.get_children("TASK-ROOT")), list(queue.items), list(db.get_audit_logs("TASK-ROOT")))
    assert after == before  # parked IN_PROGRESS(DELEGATED) is outside the shipping sweep allowlist.
    db.close()


def test_cancel_route_cascades_real_recovered_tree_and_preserves_conservative_evidence(tmp_path: Path) -> None:
    for cascade in (True, False):
        db, orch, queue, proc = _recovery_fixture(tmp_path / str(cascade))
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
        db.close()
