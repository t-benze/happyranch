from __future__ import annotations

from pathlib import Path

from runtime.config import Settings
from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus, ThreadInvocationPurpose, ThreadRecord, ThreadStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


def _seed_org(tmp_path: Path, slug: str = "test") -> Database:
    """Initialize a multi-org runtime with one seeded org and return its DB."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    org_root = runtime.orgs_dir / slug
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    return Database(org_root / "happyranch.db")


def _seed_org_with_orch(
    tmp_path: Path, slug: str = "test",
) -> tuple[Database, Orchestrator, TaskQueue]:
    """Seed an org + construct a real Orchestrator wired to a real queue.

    Mirrors the sweep's production wiring closely enough that the unified
    auto-revisit path is exercisable end-to-end.
    """
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


def test_sweep_in_progress_to_failed(tmp_path: Path) -> None:
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS)

    _sweep_on_startup(db, TaskQueue(), "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "daemon restart" in t.note


def test_sweep_parked_delegated_with_all_children_terminal_reenqueues(tmp_path):
    """Path B Branch 2 (the landmine): a parent parked on its children is stored
    in_progress(delegated) — NOT blocked. The sweep MUST re-enqueue it when all
    children are terminal, and MUST NOT force-fail it as a 'running' task."""
    db = _seed_org(tmp_path)
    # Parent in_progress(DELEGATED), child completed — lost the wake-up signal
    # to the daemon crash.
    db.insert_task(TaskRecord(id="T-PAR", brief="p"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", brief="c", parent_task_id="T-PAR"))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="done")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # Parent survives (not failed) AND is re-enqueued for its next decision step.
    assert db.get_task("T-PAR").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-PAR").block_kind == BlockKind.DELEGATED
    assert queue._queue.get_nowait() == ("test", "T-PAR", None)


def _seed_job(db: Database, job_id: str, task_id: str, status: str) -> None:
    """Insert a job row in the given status (bypasses the runner)."""
    from datetime import datetime, timezone

    from runtime.models import JobInterpreter, JobRecord
    db.insert_job(JobRecord(
        id=job_id, task_id=task_id, agent_name="dev_agent",
        title="t", rationale="r", script_text="echo x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    ))
    db._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    db._conn.commit()


def test_sweep_blocked_on_job_with_live_job_survives_restart(tmp_path):
    """Path B Branch 3 (THE LANDMINE, #1 reviewer focus): a task parked on a
    still-in-flight job is stored in_progress(blocked_on_job) with NO live
    subprocess. The pre-Path-B sweep had no branch for it (it was status=blocked
    and simply skipped); Path B makes it in_progress, so without the explicit
    Branch-3 exclusion it would fall into Branch 1 and be WRONGLY FAILED on
    every restart. Assert it SURVIVES untouched."""
    db = _seed_org(tmp_path)
    _seed_job(db, "JOB-1", "T-JOB", status="running")  # still in-flight
    db.insert_task(TaskRecord(id="T-JOB", brief="j"))
    db.update_task("T-JOB", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids='["JOB-1"]', note="waiting on jobs")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # SURVIVES: not failed, still parked, NOT re-enqueued (job still in flight).
    t = db.get_task("T-JOB")
    assert t.status == TaskStatus.IN_PROGRESS
    assert t.block_kind == BlockKind.BLOCKED_ON_JOB
    assert queue._queue.empty()


def test_sweep_blocked_on_job_with_terminal_job_reenqueues(tmp_path):
    """Path B Branch 3: when every blocking job is terminal at restart (the job
    finished while the daemon was down), the parked task is re-enqueued — the
    orphaned wake-up the live jobs_runner hook missed."""
    db = _seed_org(tmp_path)
    _seed_job(db, "JOB-1", "T-JOB", status="completed")  # finished while down
    db.insert_task(TaskRecord(id="T-JOB", brief="j"))
    db.update_task("T-JOB", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids='["JOB-1"]', note="waiting on jobs")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # Not failed; re-enqueued for its resume step.
    assert db.get_task("T-JOB").status == TaskStatus.IN_PROGRESS
    assert queue._queue.get_nowait() == ("test", "T-JOB", None)


def test_sweep_leaves_escalated_alone(tmp_path):
    """Path B Branch 5: an escalated task (top-level status, founder-owned) is
    visited by the sweep — get_nonterminal_task_ids now yields it — and left
    untouched, mirroring the pre-Path-B blocked(escalated) fall-through."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None,
                   note="needs founder")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert queue._queue.empty()


def test_sweep_blocked_delegated_with_live_child_cascades_via_auto_revisit(tmp_path):
    """TASK-573: when sweep force-fails an in-progress child of an
    in_progress(delegated) parent, the parent gets a bounded-wake decision step
    (enqueued, NOT cascade-failed). The auto-revisit is still spawned."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-PAR", brief="p", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        note="waiting",
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    # Suppress feishu side effects from the real orch.
    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed.
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    # TASK-573: parent stays in_progress(delegated) for bounded-wake, not FAILED.
    assert db.get_task("T-PAR").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-PAR").block_kind == BlockKind.DELEGATED
    # A fresh root was spawned via revisit_of_task_id=T-PAR.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-PAR"
    ]
    assert len(revisits) == 1
    # Queue gets BOTH the auto-revisit root AND the parent bounded-wake enqueue.
    enqueued = []
    while not queue._queue.empty():
        enqueued.append(queue._queue.get_nowait())
    enqueued_ids = [tid for (_slug, tid, _md) in enqueued]
    assert revisits[0].id in enqueued_ids
    assert "T-PAR" in enqueued_ids  # parent bounded-wake


def test_sweep_leaves_blocked_escalated_alone(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None, note="halt")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert t.block_kind is None
    assert queue._queue.empty()


def test_sweep_pending_stays_pending_but_gets_enqueued(tmp_path):
    """Pending rows from before the crash need a nudge — their original
    POST /tasks enqueue was lost when the daemon died."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    assert db.get_task("T-1").status == TaskStatus.PENDING
    assert queue._queue.get_nowait() == ("test", "T-1", None)


def test_sweep_works_without_orchestrator_arg(tmp_path):
    """Degraded mode: with no orchestrator (test convenience only — production
    always passes one), the IN_PROGRESS branch marks-failed-and-audits and
    skips auto-revisit / cascade / notify entirely."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-BC", brief="x"))
    db.update_task("T-BC", status=TaskStatus.IN_PROGRESS)
    _sweep_on_startup(db, TaskQueue(), "test")
    assert db.get_task("T-BC").status == TaskStatus.FAILED
    actions = [r["action"] for r in db.get_audit_logs("T-BC")]
    assert "daemon_restart_failure" in actions
    # No auto-revisit in degraded mode.
    assert "auto_revisit_of" not in actions


def test_sweep_in_progress_spawns_auto_revisit(tmp_path):
    """TASK-573: in-progress task at restart routes through the
    unified auto-revisit primitive. A fresh root is spawned with
    revisit_of_task_id=root; parent gets bounded-wake (in_progress+delegated),
    not cascade-failed. notify_failed is suppressed because the work
    is being retried."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    # Root parent task is in_progress+delegated waiting on its in-flight child.
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child work", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed.
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    # TASK-573: bounded-wake, not cascade-fail.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED
    # A new root was spawned via revisit_of_task_id=T-ROOT.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 1, (
        f"expected 1 auto-revisit pointing at T-ROOT; got {len(revisits)}"
    )
    ar = revisits[0]
    assert ar.parent_task_id is None  # new root
    assert ar.assigned_agent == "engineering_head"  # inherited from root
    # Audit row on the new root carries failure_kind=daemon_restart.
    ar_actions = db.get_audit_logs(ar.id)
    auto_rows = [r for r in ar_actions if r["action"] == "auto_revisit_of"]
    assert len(auto_rows) == 1
    assert auto_rows[0]["payload"]["failure_kind"] == "daemon_restart"
    # notify_failed is suppressed because the work is being retried.
    assert notify_calls == []


def test_sweep_per_root_dedup(tmp_path):
    """Two in-flight children of the same root at restart spawn EXACTLY ONE
    auto-revisit — not two. The per-restart dedup avoids burning the
    per-kind cap with parallel retry trees pointing at the same lineage."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="T-CHD-A", brief="a", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))
    db.insert_task(TaskRecord(
        id="T-CHD-B", brief="b", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 1, (
        f"expected exactly 1 auto-revisit (per-root dedup); got {len(revisits)}"
    )
    # Both siblings still cascade-suppressed: zero founder pings.
    assert notify_calls == []


def test_lifespan_recovers_orphaned_running_jobs(tmp_home, daemon_state):
    """Job rows left in 'running' state on daemon startup are force-failed."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from runtime.daemon.app import create_app
    from runtime.models import JobInterpreter, JobRecord, JobStatus

    org = daemon_state.orgs["alpha"]
    # Seed: insert a pending job then mark it running manually.
    job = JobRecord(
        id="JOB-001",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="echo x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_job(job)
    org.db._conn.execute(
        "UPDATE jobs SET status='running', started_at='2026-05-23T00:00:00Z' WHERE id='JOB-001'"
    )
    org.db._conn.commit()

    # Boot lifespan via TestClient context manager — startup hook fires.
    app = create_app(daemon_state)
    with TestClient(app):
        # Query inside the context so the DB is still open (lifespan teardown
        # calls close_all() on __exit__, after which the connection is gone).
        fetched = org.db.get_job("JOB-001")

    assert fetched is not None
    assert fetched.status == JobStatus.FAILED
    assert fetched.finished_at is not None
    # Recovery must distinguish a crash-orphan from a normal failure so the
    # founder UX and audit story preserve the cause.
    assert fetched.reason == "daemon_crash"


def test_terminate_all_inflight_awaits_runner_tasks(tmp_home, daemon_state):
    """Regression: clean shutdown must let in-flight runner tasks persist
    terminal state BEFORE the per-org DB is closed. Without this, a job sits
    in `running` until the next startup recovery scan."""
    import asyncio
    from datetime import datetime, timezone

    from runtime.daemon import jobs_runner
    from runtime.models import JobInterpreter, JobRecord, JobStatus

    org = daemon_state.orgs["alpha"]
    job = JobRecord(
        id="JOB-100",
        task_id="TASK-100",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_job(job)
    org.db._conn.execute(
        "UPDATE jobs SET status='running' WHERE id='JOB-100'"
    )
    org.db._conn.commit()

    # Simulate a runner task that's still mid-flight: it sleeps briefly, then
    # transitions the row to FAILED. terminate_all_inflight must await this.
    async def fake_runner() -> None:
        await asyncio.sleep(0.05)
        org.db.transition_job_to_terminal(
            "JOB-100",
            status=JobStatus.FAILED,
            exit_code=-15,
            finished_at="2026-05-23T00:00:01Z",
            duration_ms=50,
            stdout_head="",
            stderr_head="killed by shutdown",
        )

    async def run_test() -> None:
        task = asyncio.create_task(fake_runner())
        jobs_runner.register_runner_task("JOB-100", task)
        # No subprocesses to kill — just await the runner task.
        await jobs_runner.terminate_all_inflight(
            grace_seconds=0, persist_timeout_seconds=2.0,
        )

    asyncio.run(run_test())

    fetched = org.db.get_job("JOB-100")
    assert fetched.status == JobStatus.FAILED, (
        "shutdown returned before the runner task persisted terminal state — "
        "row would have stayed `running` until next startup"
    )


# ── Thread invocation sweep (THR-046 message-112) ────────────────────────

def test_sweep_reconciles_pending_invocation_to_failed(tmp_path):
    """Branch 6: orphaned pending thread invocations are reaped to failed on
    daemon restart so the UI reply box (queued/working render) clears."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status.value == "pending"
    # Verify wire render is 'queued' (no started_at)
    wire_entry = _responder_entry({
        "agent_name": "alpha", "status": "pending",
        "consumed_at": None, "started_at": None,
    })
    assert wire_entry.status == "queued"

    _sweep_on_startup(db, TaskQueue(), "test")

    # DB row is now terminal.
    reel = db.get_invocation_any_status(inv.invocation_token)
    assert reel is not None
    assert reel.status.value == "failed"
    assert reel.decline_reason == "daemon_restart"
    assert reel.consumed_at is not None

    # Wire render is now 'failed' (box clears).
    wire_after = _responder_entry({
        "agent_name": "alpha", "status": reel.status.value,
        "consumed_at": reel.consumed_at.isoformat() if reel.consumed_at else None,
        "started_at": reel.started_at.isoformat() if reel.started_at else None,
    })
    assert wire_after.status == "failed", (
        f"expected wire status 'failed' after sweep; got '{wire_after.status}'"
    )


def test_sweep_reconciles_working_invocation_to_failed(tmp_path):
    """Branch 6: a started (working) pending invocation is also reaped to
    failed. The wire render flips from 'working' to 'failed'."""
    from datetime import datetime, timezone

    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    # Simulate a subprocess that started before the daemon was killed.
    started_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db._conn.execute(
        "UPDATE thread_invocations SET started_at = ? WHERE invocation_token = ?",
        (started_ts, inv.invocation_token),
    )
    db._conn.commit()

    # Wire renders 'working' because started_at is set.
    wire_entry = _responder_entry({
        "agent_name": "alpha", "status": "pending",
        "consumed_at": None, "started_at": started_ts,
    })
    assert wire_entry.status == "working"

    _sweep_on_startup(db, TaskQueue(), "test")

    reel = db.get_invocation_any_status(inv.invocation_token)
    assert reel is not None
    assert reel.status.value == "failed"
    assert reel.decline_reason == "daemon_restart"

    wire_after = _responder_entry({
        "agent_name": "alpha", "status": reel.status.value,
        "consumed_at": reel.consumed_at.isoformat() if reel.consumed_at else None,
        "started_at": reel.started_at.isoformat() if reel.started_at else None,
    })
    assert wire_after.status == "failed"


def test_sweep_reconciles_all_threads_pending_invocations(tmp_path):
    """Branch 6: reaps pending invocations across ALL threads, not just open
    ones. A pending invocation is orphaned regardless of thread status."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    # Thread 1 — has pending invocation
    db.insert_thread(ThreadRecord(id="THR-001", subject="open"))
    inv1 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    # Thread 2 — archived thread (past conversation), also has pending
    db.insert_thread(ThreadRecord(id="THR-002", subject="archived"))
    db.set_thread_status("THR-002", status=ThreadStatus.ARCHIVED)
    inv2 = db.mint_thread_invocation(
        thread_id="THR-002", agent_name="bravo", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )

    _sweep_on_startup(db, TaskQueue(), "test")

    for token in (inv1.invocation_token, inv2.invocation_token):
        reel = db.get_invocation_any_status(token)
        assert reel is not None
        assert reel.status.value == "failed", (
            f"invocation {token} should be failed after sweep, got "
            f"{reel.status.value}"
        )
        assert reel.decline_reason == "daemon_restart"


def test_sweep_leaves_already_terminal_invocations_alone(tmp_path):
    """Branch 6: terminal invocations (consumed, declined, timeout) are NOT
    touched — only genuinely pending rows are reaped."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))

    # Already-consumed invocation.
    consumed = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.consume_invocation(consumed.invocation_token)

    # Already-declined.
    declined = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bravo", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mark_invocation_declined(declined.invocation_token, decline_reason="agent_declined")

    _sweep_on_startup(db, TaskQueue(), "test")

    # Consumed stays consumed.
    c = db.get_invocation_any_status(consumed.invocation_token)
    assert c is not None and c.status.value == "consumed", \
        f"consumed invocation was altered to {c.status.value if c else 'None'}"

    # Declined stays declined.
    d = db.get_invocation_any_status(declined.invocation_token)
    assert d is not None and d.status.value == "declined", \
        f"declined invocation was altered to {d.status.value if d else 'None'}"
