from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from runtime.daemon.sessions import SessionTracker
from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence
from runtime.infrastructure.database import Database
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


def _task(task_id: str, status: TaskStatus, parent: str | None = None, revisit: str | None = None) -> TaskRecord:
    return TaskRecord(id=task_id, status=status, brief="x", assigned_agent="dev_agent", current_session_id="session", parent_task_id=parent, revisit_of_task_id=revisit, completed_at=datetime.now(timezone.utc) if status == TaskStatus.COMPLETED else None)


def _proc(proc: Path, pid: int, root: str = "/", cwd: str = "/", fd: str | None = None) -> None:
    item = proc / str(pid); (item / "fd").mkdir(parents=True)
    (item / "stat").write_text(f"{pid} (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (item / "root").symlink_to(root); (item / "cwd").symlink_to(cwd)
    if fd: (item / "fd" / "3").symlink_to(fd)


def _sources(tmp_path: Path) -> tuple[Database, Path]:
    proc = tmp_path / "proc"; (proc / "sys/kernel/random").mkdir(parents=True); (proc / "sys/kernel/random/boot_id").write_text("boot\n"); _proc(proc, 42)
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
    (proc / "sys/kernel/random/boot_id").write_text("boot\n")
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
        _proc(proc, 43, cwd=str(root)); (proc / "sys/kernel/random/boot_id").write_text("new\n")
        return result
    monkeypatch.setattr(subject, "_scan", changed)
    evidence = subject.collect_task_scratch_evidence(db=db, sessions=SessionTracker(), task_id="TASK-1", root=root, proc_root=proc, monotonic_now=31, daemon_started_monotonic=0)
    assert {"active_job", "durable_state_changed_during_collection", "process_population_changed_during_collection", "boot_id_changed_during_collection"} <= set(evidence.reasons)


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
