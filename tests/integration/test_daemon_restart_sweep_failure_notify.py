from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


pytestmark = pytest.mark.integration


def _real_orch(tmp_path: Path, slug: str = "acme") -> tuple[Database, Orchestrator, TaskQueue]:
    """Construct a sweep-ready org with real Orchestrator + queue."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=runtime.orgs_dir / slug)
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    db = Database(paths.db_path)
    queue = TaskQueue()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=paths, slug=slug,
        teams=TeamsRegistry.load(paths.root),
    )
    orch._queue = queue
    return db, orch, queue


def test_sweep_writes_daemon_restart_failure_not_escalation(tmp_path: Path):
    """Daemon-restart sweep must use the daemon_restart_failure audit action,
    never the escalation action — APPROVE/REJECT don't make sense for
    sweep-killed tasks."""
    db, orch, queue = _real_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    task = db.get_task("TASK-1")
    assert task.status == TaskStatus.FAILED

    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "daemon_restart_failure" in actions, (
        f"expected 'daemon_restart_failure' audit row; got: {actions}"
    )
    assert "escalation" not in actions, (
        f"'escalation' must not appear for a daemon-restart failure; "
        f"got: {actions}"
    )


def test_sweep_null_assigned_agent_fails_with_liveness_note(tmp_path: Path):
    """THR-079: if task.assigned_agent is None, the sweep still fails the
    task with a liveness-undeterminable note. No crash, no auto-revisit."""
    db, orch, queue = _real_orch(tmp_path)
    # A worker root with no parent and no block_kind — genuine root-level death.
    db.insert_task(TaskRecord(
        id="TASK-2", brief="x", team="engineering",
        assigned_agent=None,
        status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-2")
    assert t.status == TaskStatus.FAILED
    assert t.note is not None and "liveness undeterminable" in t.note

    # THR-079: NO auto-revisit twin.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "TASK-2"
    ]
    assert len(revisits) == 0


def test_sweep_consumes_orphaned_task_result_done(tmp_path: Path):
    """THR-090 Track A: when an in_progress task has a dead executor_pid
    AND an unconsumed task_result row, the boot sweep must consume the
    result (honor the completion) instead of marking it FAILED.

    Scenario: root task completed cleanly (decision='done'), daemon died
    before consuming. The sweep should transition it to COMPLETED."""
    db, orch, queue = _real_orch(tmp_path)

    # Insert an in_progress root task with NO parent.
    db.insert_task(TaskRecord(
        id="TASK-Z1", brief="do work", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))
    # Set current_session_id to match the orphaned result row's session —
    # this simulates a genuine unconsumed result from the current session.
    db.update_task("TASK-Z1", current_session_id="sess-orphan-1")

    # Insert an orphaned task_result row — the completion callback landed
    # but the daemon died before the orchestrator consumed it.
    db.insert_task_result(
        task_id="TASK-Z1",
        agent="dev_agent",
        session_id="sess-orphan-1",
        status="completed",
        output_summary="All done successfully",
        confidence_score=95,
        decision_json='{"action": "done", "summary": "All done successfully"}',
    )

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-Z1")
    assert t.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED (orphaned result consumed), got {t.status}"
    )
    assert t.note == "All done successfully"

    # Must NOT have a daemon_restart_failure audit row.
    actions = [r["action"] for r in db.get_audit_logs("TASK-Z1")]
    assert "daemon_restart_failure" not in actions, (
        f"orphaned result should be consumed, not failed; got actions: {actions}"
    )


def test_sweep_consumes_orphaned_task_result_wakes_parent(tmp_path: Path):
    """THR-090 Track A: consuming an orphaned result must also wake a
    waiting parent via _enqueue_parent_if_waiting."""
    db, orch, queue = _real_orch(tmp_path)

    # Spy on queue.put_nowait to verify the parent was enqueued.
    enqueued: list[str] = []
    _orig_put = queue.put_nowait
    def _spy_put(slug: str, task_id: str, *, metadata=None):
        enqueued.append(task_id)
        _orig_put(slug, task_id, metadata=metadata)
    queue.put_nowait = _spy_put  # type: ignore[method-assign]

    # Insert a parent task parked in in_progress(delegated) waiting on its child.
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="manage work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))

    # Insert the child subtask — in_progress with dead pid.
    db.insert_task(TaskRecord(
        id="TASK-CHILD", brief="do sub work", team="engineering",
        assigned_agent="dev_agent",
        parent_task_id="TASK-PARENT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))
    # Set current_session_id to match the orphaned result row's session.
    db.update_task("TASK-CHILD", current_session_id="sess-orphan-2")

    # Insert an orphaned task_result for the child.
    db.insert_task_result(
        task_id="TASK-CHILD",
        agent="dev_agent",
        session_id="sess-orphan-2",
        status="completed",
        output_summary="Sub work done",
        confidence_score=90,
    )

    _sweep_on_startup(db, queue, "acme", orch)

    child = db.get_task("TASK-CHILD")
    assert child.status == TaskStatus.COMPLETED, (
        f"expected child COMPLETED, got {child.status}"
    )

    # Parent should be enqueued (woken up).
    assert "TASK-PARENT" in enqueued, (
        f"parent should be enqueued after child completion; enqueued: {enqueued}"
    )


def test_sweep_orphaned_result_absent_falls_through_to_failed(tmp_path: Path):
    """When there is NO orphaned task_result, the existing FAILED path
    must still work unchanged."""
    db, orch, queue = _real_orch(tmp_path)

    db.insert_task(TaskRecord(
        id="TASK-Z2", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-Z2")
    assert t.status == TaskStatus.FAILED

    actions = [r["action"] for r in db.get_audit_logs("TASK-Z2")]
    assert "daemon_restart_failure" in actions


def test_sweep_old_session_result_does_not_replay_multi_step_task(tmp_path: Path):
    """THR-090 Track A fix: a multi-step task (orchestration_step_count >= 2)
    with a dead executor_pid and an OLD task_result row from a PRIOR session
    (already-consumed delegate/fanout result) must NOT be replayed.

    The sweep must scope orphaned-result detection to the CURRENT session
    only. A prior-step result row carries a different session_id and must
    never match — the task must fall through to the dead-pid FAILED path
    instead of re-applying an already-consumed decision.

    Reviewer's exact false-positive repro:
    - in_progress root task, orchestration_step_count=2
    - ONLY an old consumed delegate task_result present (under PRIOR session)
    - dead executor_pid
    - current_session_id set to a DIFFERENT (dead) session that wrote NO result
    -> sweep must drive status=FAILED with daemon_restart_failure note,
       NOT consume the old result & NOT insert a child task."""
    db, orch, queue = _real_orch(tmp_path)

    # Spy on the DB to detect any child-task insert (would indicate replay).
    child_inserted: list[str] = []
    _orig_insert = db.insert_task
    def _spy_insert(task):
        child_inserted.append(task.id)
        _orig_insert(task)
    db.insert_task = _spy_insert  # type: ignore[method-assign]

    # Insert a multi-step root task that previously delegated (step 1 consumed,
    # step 2 is the current in_progress live session). The task has an OLD
    # task_result row from a PRIOR session simulating the already-consumed
    # delegate step.
    db.insert_task(TaskRecord(
        id="TASK-MULTI", brief="multi-step work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS,
        task_type="task",
        orchestration_step_count=2,
    ))
    # Set current_session_id to the CURRENT (dead) session.
    db._conn.execute(
        "UPDATE tasks SET current_session_id = ? WHERE id = ?",
        ("sess-current-dead", "TASK-MULTI"),
    )
    db._conn.commit()
    # Set a dead executor_pid.
    db._conn.execute(
        "UPDATE tasks SET executor_pid = ? WHERE id = ?",
        (99999, "TASK-MULTI"),
    )
    db._conn.commit()

    # Insert an OLD task_result row under a PRIOR session id — this simulates
    # the already-consumed delegate result from orchestration step 1. It must
    # NOT be matched by the session-scoped sweep.
    db.insert_task_result(
        task_id="TASK-MULTI",
        agent="engineering_head",
        session_id="sess-old-prior",  # DIFFERENT from current_session_id
        status="completed",
        output_summary="Delegated to dev_agent (step 1)",
        confidence_score=90,
        decision_json='{"action": "delegate", "agent": "dev_agent", "prompt": "do sub work"}',
    )

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-MULTI")
    # MUST be FAILED — the old prior-session result must NOT be consumed.
    assert t.status == TaskStatus.FAILED, (
        f"expected FAILED (old prior-session result not replayed), got {t.status}"
    )
    assert t.note is not None and "executor pid not alive" in t.note, (
        f"expected daemon_restart_failure note, got: {t.note}"
    )

    # NO child task must have been inserted (the old delegate decision must
    # NOT be replayed).
    assert len(child_inserted) == 1, (
        f"only the original TASK-MULTI insert should exist; "
        f"replay wrongly inserted: {child_inserted}"
    )

    # Must have a daemon_restart_failure audit row.
    actions = [r["action"] for r in db.get_audit_logs("TASK-MULTI")]
    assert "daemon_restart_failure" in actions, (
        f"expected daemon_restart_failure audit; got: {actions}"
    )



# THR-350: retained integration source; no collection/execution on the live
# daemon host. Only OS observations are deterministic test-side substitutes.
@pytest.fixture
def unknown_session(tmp_path: Path, monkeypatch, request):
    import os
    from datetime import datetime, timedelta, timezone
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.infrastructure.task_pause_controls import install_pause_schema, TaskPauseStore
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    from runtime.infrastructure.workflow_task_ownership import install_task_ownership
    from runtime.orchestrator.task_pause import fingerprint, restore_pause_preparations

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon-home"))
    db, orch, queue = _real_orch(tmp_path)
    initialize_complete_org_schema(db, expected_org_slug="acme")
    install_task_ownership(db, org_slug="acme")
    install_pause_schema(db, org_slug="acme")
    store = db._task_pause_store = TaskPauseStore(db, "acme")
    moment = datetime.now(timezone.utc)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="manage", team="engineering", assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-CHILD", brief="work", team="engineering", assigned_agent="dev_agent",
        parent_task_id="TASK-PARENT", task_type="subtask", status=TaskStatus.IN_PROGRESS,
        orchestration_step_count=1, current_session_id="sess-crashed",
    ))
    db.update_task("TASK-CHILD", executor_pid=2147483000,
        last_heartbeat=(moment - timedelta(seconds=600)).isoformat())
    assert db.get_task("TASK-CHILD").executor_pid == 2147483000
    assert db.get_task("TASK-CHILD").last_heartbeat == moment - timedelta(seconds=600)
    AuditLogger(db).log_session_start("TASK-CHILD", "dev_agent", str(orch._paths.root),
        session_id="sess-crashed", invocation_purpose="worker_execution")
    identity = {}
    wake = getattr(request, "param", None)
    if wake is not None:
        from runtime.models import JobRecord
        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        db.insert_job(JobRecord(id="JOB-1", task_id="TASK-CHILD", agent_name="dev_agent",
            title="finished", rationale="fixture", script_text="true", interpreter="bash",
            status="completed", exit_code=0, created_at=moment.isoformat()))
        db.update_task("TASK-CHILD", block_kind=BlockKind.BLOCKED_ON_JOB, blocked_on_job_ids='["JOB-1"]')
        assert _maybe_resume_blocked_task(orch, "TASK-CHILD", trigger=wake,
            triggering_job_id="JOB-1" if wake == "job_terminal" else None)
        slug, task_id, identity = queue._queue.get_nowait()
        assert (slug, task_id) == ("acme", "TASK-CHILD")
        db.update_task("TASK-CHILD", block_kind=None, blocked_on_job_ids=None)
    with store.writer("TASK-CHILD") as (root, row):
        row = store.ensure_uncommitted(root["id"])
        row["journal"]["entries"].append({
            "id": "crash-owner", "org": "acme", "root_task_id": root["id"],
            "task_id": "TASK-CHILD", "agent": "dev_agent", "session_id": "sess-crashed",
            "owner": "ordinary", "owner_identity": identity, "fingerprint": fingerprint(
                db.get_task("TASK-CHILD").model_copy(update={"orchestration_step_count": 0})),
            "generation": 0, "phase": "running", "retry": {}, "context": {},
            "started_at": moment.isoformat(), "captured_generation": None,
        })
        store.save_journal_uncommitted(row)
    restore_pause_preparations(orch)
    original_kill = os.kill
    def dead_pid(pid: int, sig: int) -> None:
        if pid == 2147483000:
            raise ProcessLookupError(pid)
        original_kill(pid, sig)
    monkeypatch.setattr(os, "kill", dead_pid)
    original_read = Path.read_text
    def proc_stat(path: Path, *args, **kwargs) -> str:
        if path == Path("/proc/stat"):
            assert not db._conn.in_transaction
            assert not db._lock._is_owned()
            return "btime 1\n"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", proc_stat)
    wakes = []
    original_put = queue.put_nowait
    def observe_put(slug: str, task_id: str, *, metadata=None) -> None:
        wakes.append(task_id)
        original_put(slug, task_id, metadata=metadata)
    monkeypatch.setattr(queue, "put_nowait", observe_put)
    try:
        yield db, orch, queue, store, moment, wakes
    finally:
        db.close()


def _unknown_entry(store):
    with store.db._lock:
        return store.row_uncommitted("TASK-PARENT")["journal"]["entries"][0]


def _unknown_sweep(case, seam: str, *, seconds: int = 0) -> None:
    from datetime import timedelta
    from runtime.daemon.zombie_reaper import _sweep_org_zombies
    db, orch, queue, _, moment, _ = case
    if seam == "startup":
        _sweep_on_startup(db, queue, "acme", orch)
    else:
        _sweep_org_zombies(db, orchestrator=orch, queue=queue,
            now=moment + timedelta(seconds=seconds), uptime=1000, warm_up_seconds=30)


@pytest.mark.parametrize("seam", ["startup", "periodic"])
@pytest.mark.parametrize("child_first", [False, True])
@pytest.mark.parametrize("unknown_session", [None, "job_terminal", "block_submit"], indirect=True)
def test_unknown_dead_session_terminal_and_single_parent_wake(unknown_session, monkeypatch, seam, child_first):
    import sqlite3
    from runtime.orchestrator.task_pause import pause_deferred_owner
    db, orch, _, store, _, wakes = unknown_session
    expected = TaskStatus.FAILED if seam == "startup" else TaskStatus.CANCELLED
    original_save = store.save_journal_uncommitted
    def observe_settlement(row):
        if row["journal"]["entries"][0]["context"].get("producer_settled"):
            with sqlite3.connect(orch._paths.db_path) as observer:
                assert observer.execute("SELECT status FROM tasks WHERE id='TASK-CHILD'").fetchone()[0] == expected.value
        original_save(row)
    monkeypatch.setattr(store, "save_journal_uncommitted", observe_settlement)
    original_ids = db.get_nonterminal_task_ids
    monkeypatch.setattr(db, "get_nonterminal_task_ids", lambda: sorted(
        original_ids(), key=lambda tid: (tid != "TASK-CHILD") if child_first else (tid == "TASK-CHILD")))
    assert pause_deferred_owner(db, "TASK-CHILD")
    _unknown_sweep(unknown_session, seam)
    if seam == "periodic":
        assert db.get_task("TASK-CHILD").status == TaskStatus.IN_PROGRESS
        assert db.get_task("TASK-CHILD").zombie_flagged_at is not None
        assert wakes == []
        assert not _unknown_entry(store)["context"].get("producer_settled")
        _unknown_sweep(unknown_session, seam, seconds=151)
    action = "daemon_restart_failure" if seam == "startup" else "zombie_cancelled"
    assert db.get_task("TASK-CHILD").status == expected
    assert _unknown_entry(store)["phase"] == "unknown"
    assert _unknown_entry(store)["context"] == {"execution_unknown": True, "producer_settled": True}
    assert not pause_deferred_owner(db, "TASK-CHILD")
    for _ in range(2):
        _unknown_sweep(unknown_session, seam, seconds=500)
    logs = db.get_audit_logs("TASK-CHILD")
    assert sum(row["action"] == action for row in logs) == 1
    terminal_audit = next(row for row in logs if row["action"] == action)
    assert terminal_audit["task_id"] == "TASK-CHILD"
    assert terminal_audit["agent"] == "dev_agent"
    assert terminal_audit["payload"] == {"reason": (
        "daemon restarted mid-task" if seam == "startup" else "zombie cancelled after TTL expiry")}
    assert not any(row["action"] == ("zombie_cancelled" if seam == "startup" else "daemon_restart_failure") for row in logs)
    assert wakes == ["TASK-PARENT"]
    assert db.get_children("TASK-CHILD") == []


@pytest.mark.parametrize("seam", ["startup", "periodic"])
@pytest.mark.parametrize("probe", ["alive", "indeterminate", "missing"])
def test_unknown_session_without_death_proof_stays_deferred(unknown_session, monkeypatch, seam, probe):
    import os
    from runtime.orchestrator.task_pause import pause_deferred_owner
    db, _, _, store, _, wakes = unknown_session
    def probe_pid(pid: int, sig: int) -> None:
        if probe == "indeterminate":
            raise PermissionError(pid)
    monkeypatch.setattr(os, "kill", probe_pid)
    if probe == "missing":
        db.update_task("TASK-CHILD", executor_pid=None)
    before = _unknown_entry(store)
    _unknown_sweep(unknown_session, seam)
    _unknown_sweep(unknown_session, seam, seconds=500)
    assert db.get_task("TASK-CHILD").status == TaskStatus.IN_PROGRESS
    assert db.get_task("TASK-CHILD").zombie_flagged_at is None
    assert _unknown_entry(store) == before
    assert pause_deferred_owner(db, "TASK-CHILD")
    assert wakes == []


@pytest.mark.parametrize("seam,probe", [("startup", "dead"), ("periodic", "dead"),
    ("startup", "missing"), ("startup", "indeterminate")])
@pytest.mark.parametrize("result_sid", ["sess-crashed", "sess-prior"])
def test_unknown_result_is_scoped_and_wins_before_inferred_terminal(unknown_session, monkeypatch, seam, probe, result_sid):
    import os
    db, _, _, store, _, wakes = unknown_session
    if probe == "missing":
        db.update_task("TASK-CHILD", executor_pid=None)
    elif probe == "indeterminate":
        def denied(pid: int, sig: int) -> None:
            raise PermissionError(pid)
        monkeypatch.setattr(os, "kill", denied)
    db.insert_task_result(task_id="TASK-CHILD", agent="dev_agent", session_id=result_sid,
        status="completed", output_summary="authentic completion", confidence_score=90)
    _unknown_sweep(unknown_session, seam)
    _unknown_sweep(unknown_session, seam, seconds=151)
    _unknown_sweep(unknown_session, seam, seconds=500)
    expected = TaskStatus.COMPLETED if result_sid == "sess-crashed" else (TaskStatus.IN_PROGRESS
        if probe != "dead" else TaskStatus.FAILED if seam == "startup" else TaskStatus.CANCELLED)
    assert db.get_task("TASK-CHILD").status == expected
    actions = [r["action"] for r in db.get_audit_logs("TASK-CHILD")]
    if result_sid == "sess-crashed" or probe != "dead":
        assert "daemon_restart_failure" not in actions and "zombie_cancelled" not in actions
    if expected == TaskStatus.IN_PROGRESS:
        assert not _unknown_entry(store)["context"].get("producer_settled")
        assert wakes == []
    else:
        assert _unknown_entry(store)["phase"] == "unknown"
        assert _unknown_entry(store)["context"] == {"execution_unknown": True, "producer_settled": True}
        assert wakes == ["TASK-PARENT"]


@pytest.mark.parametrize("seam", ["startup", "periodic"])
@pytest.mark.parametrize("exclusion", ["job", "draft", "coding", "sequential", "recovery", "v2", "authority_identity", "invalid_job_wake", "stale_sid", "stale_count", "stale_agent", "other_deferral", "operator_hold", "recovery_ledger"])
def test_unknown_nonordinary_and_held_owners_untouched(unknown_session, seam, exclusion):
    from copy import deepcopy
    from runtime.daemon.sessions import SessionTracker
    db, _, _, store, moment, wakes = unknown_session
    with store.writer("TASK-CHILD") as (_, row):
        entry = row["journal"]["entries"][0]
        if exclusion in {"job", "draft", "coding", "sequential", "recovery", "v2"}:
            # An outstanding competing owner must prevent bypass, even though
            # job alone never defers. Its journal history must remain unchanged.
            other = deepcopy(entry)
            other.update(id="other-owner", owner=exclusion)
            row["journal"]["entries"].append(other)
        elif exclusion == "stale_sid":
            entry["session_id"] = "sess-prior"
        elif exclusion == "stale_count":
            entry["fingerprint"]["count"] = 5
        elif exclusion == "stale_agent":
            entry["agent"] = "engineering_head"
        elif exclusion == "other_deferral":
            entry["phase"] = "retry_deferred"
        elif exclusion == "authority_identity":
            entry["owner_identity"] = {"trigger": "job_terminal", "triggering_job_id": "JOB-1",
                "authority_v2_generation": "generation"}
        elif exclusion == "invalid_job_wake":
            entry["owner_identity"] = {"trigger": "job_terminal", "triggering_job_id": None}
        store.save_journal_uncommitted(row)
    if exclusion == "operator_hold":
        store.control("TASK-PARENT", held=True, expected_generation=0, actor="founder", sessions=SessionTracker())
    elif exclusion == "recovery_ledger":
        db._conn.execute("INSERT INTO task_completion_recoveries "
            "(task_id,agent,origin_session_id,recovery_session_id,provider_session_id,claimed_at,expires_at,state) "
            "VALUES (?,?,?,?,?,?,?,'restart_settled')",
            ("TASK-CHILD", "dev_agent", "sess-crashed", "sess-recovery", "provider", moment.isoformat(), moment.isoformat()))
        db._conn.commit()
    before = db.get_task("TASK-CHILD")
    _unknown_sweep(unknown_session, seam)
    _unknown_sweep(unknown_session, seam, seconds=500)
    assert db.get_task("TASK-CHILD") == before
    assert not _unknown_entry(store)["context"].get("producer_settled")
    assert wakes == []
    if exclusion == "operator_hold":
        assert store.row_uncommitted("TASK-PARENT")["held"] == 1


@pytest.mark.parametrize("seam", ["startup", "periodic"])
@pytest.mark.parametrize("start", ["before_boot", "equal_boot", "missing", "malformed", "wrong_sid", "duplicate", "non_utc", "missing_boot", "malformed_boot", "duplicate_boot"])
def test_unknown_reused_pid_needs_exact_persisted_start_before_boot(unknown_session, monkeypatch, seam, start):
    import os
    from datetime import datetime, timezone
    db, _, _, _, _, wakes = unknown_session
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    original_read = Path.read_text
    boot_text = {"missing_boot": "cpu 0\n", "malformed_boot": "btime bad\n",
                 "duplicate_boot": "btime 2000000000\nbtime 2000000000\n"}.get(start, "btime 2000000000\n")
    def read_boot(path: Path, *args, **kwargs) -> str:
        if path == Path("/proc/stat"):
            assert not db._conn.in_transaction
            assert not db._lock._is_owned()
            return boot_text
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_boot)
    if start == "missing":
        db._conn.execute("DELETE FROM audit_log WHERE task_id='TASK-CHILD' AND action='session_start'")
    else:
        stamp = datetime.fromtimestamp(1999999999 if start in {"before_boot", "missing_boot", "malformed_boot", "duplicate_boot"} else 2000000000, timezone.utc).isoformat()
        if start == "malformed":
            stamp = "not-a-time"
        elif start == "non_utc":
            stamp = "2033-05-18T00:00:00+08:00"
        db._conn.execute("UPDATE audit_log SET timestamp=? WHERE task_id='TASK-CHILD' AND action='session_start'", (stamp,))
        if start == "wrong_sid":
            db._conn.execute("UPDATE audit_log SET payload=json_set(payload,'$.session_id','sess-prior') WHERE action='session_start'")
        elif start == "duplicate":
            db._conn.execute("INSERT INTO audit_log(task_id,agent,action,payload,timestamp) "
                "SELECT task_id,agent,action,payload,timestamp FROM audit_log WHERE action='session_start'")
    db._conn.commit()
    _unknown_sweep(unknown_session, seam)
    _unknown_sweep(unknown_session, seam, seconds=151)
    expected = (TaskStatus.FAILED if seam == "startup" else TaskStatus.CANCELLED) if start == "before_boot" else TaskStatus.IN_PROGRESS
    assert db.get_task("TASK-CHILD").status == expected
    assert wakes == (["TASK-PARENT"] if start == "before_boot" else [])


@pytest.mark.parametrize("seam", ["startup", "periodic"])
@pytest.mark.parametrize("winner", ["operator_hold", "new_session", "result", "session_start"])
def test_unknown_terminal_writer_rechecks_competing_winner(unknown_session, monkeypatch, seam, winner):
    import os
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from runtime.daemon.sessions import SessionTracker
    from runtime.orchestrator.task_pause import OrdinaryUnknownOwner
    db, _, _, store, _, wakes = unknown_session
    if winner == "session_start":
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        original_read = Path.read_text
        def read_boot(path: Path, *args, **kwargs) -> str:
            if path == Path("/proc/stat"):
                assert not db._conn.in_transaction
                assert not db._lock._is_owned()
                return "btime 2000000000\n"
            return original_read(path, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read_boot)
        db._conn.execute("UPDATE audit_log SET timestamp=? WHERE action='session_start'",
            (datetime.fromtimestamp(1999999999, timezone.utc).isoformat(),))
        db._conn.commit()
    if seam == "periodic":
        _unknown_sweep(unknown_session, seam)
    original_writer = store.writer
    injected = False
    @contextmanager
    def competing_writer(task_id, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            if winner == "operator_hold":
                store.control("TASK-PARENT", held=True, expected_generation=0, actor="founder", sessions=SessionTracker())
            elif winner == "new_session":
                db.update_task("TASK-CHILD", current_session_id="sess-new")
            elif winner == "session_start":
                db._conn.execute("UPDATE audit_log SET timestamp=? WHERE action='session_start'",
                    (datetime.fromtimestamp(2000000000, timezone.utc).isoformat(),))
                db._conn.commit()
            else:
                db.insert_task_result(task_id="TASK-CHILD", agent="dev_agent", session_id="sess-crashed",
                    status="completed", output_summary="winner", confidence_score=90)
        with original_writer(task_id, **kwargs) as observed:
            yield observed
    original_guard = OrdinaryUnknownOwner.writer
    @contextmanager
    def arm_winner(owner, **kwargs):
        monkeypatch.setattr(store, "writer", competing_writer)
        with original_guard(owner, **kwargs) as allowed:
            yield allowed
    monkeypatch.setattr(OrdinaryUnknownOwner, "writer", arm_winner)
    _unknown_sweep(unknown_session, seam, seconds=151)
    assert injected
    assert db.get_task("TASK-CHILD").status == TaskStatus.IN_PROGRESS
    assert not _unknown_entry(store)["context"].get("producer_settled")
    assert wakes == []
    if winner == "result":
        _unknown_sweep(unknown_session, seam, seconds=500)
        assert db.get_task("TASK-CHILD").status == TaskStatus.COMPLETED
        assert wakes == ["TASK-PARENT"]
