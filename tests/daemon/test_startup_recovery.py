from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _sweep_on_startup
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


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


def test_sweep_blocked_delegated_with_all_children_terminal_reenqueues(tmp_path):
    db = _seed_org(tmp_path)
    # Parent blocked(DELEGATED), child completed — lost the wake-up signal
    # to the daemon crash.
    db.insert_task(TaskRecord(id="T-PAR", brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", brief="c", parent_task_id="T-PAR"))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="done")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    assert queue._queue.get_nowait() == ("test", "T-PAR", None)


def test_sweep_blocked_delegated_with_live_child_cascades_via_auto_revisit(tmp_path):
    """Production path: when sweep force-fails an in-progress child of a
    BLOCKED+DELEGATED parent, the cascade propagates to FAIL the parent and
    spawns an auto-revisit at the root. The old behavior was to wake the
    parent for a re-decision step — that left the failed child as a poisoned
    sibling. Now cascade is unified."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-PAR", brief="p", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED,
        note="waiting",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        status=TaskStatus.IN_PROGRESS,
    ))

    # Suppress feishu side effects from the real orch.
    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed; parent cascade-failed (not woken for re-decision).
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    assert db.get_task("T-PAR").status == TaskStatus.FAILED
    # A fresh root was spawned via revisit_of_task_id=T-PAR.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-PAR"
    ]
    assert len(revisits) == 1
    # The queue gets the new auto-revisit root, NOT the old parent T-PAR
    # (the old behavior re-enqueued T-PAR for a manager re-decision; the
    # unified path replaces that with a fresh root).
    enqueued = []
    while not queue._queue.empty():
        enqueued.append(queue._queue.get_nowait())
    enqueued_ids = [tid for (_slug, tid, _md) in enqueued]
    assert "T-PAR" not in enqueued_ids
    assert revisits[0].id in enqueued_ids


def test_sweep_leaves_blocked_escalated_alone(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.ESCALATED, note="halt")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
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
    """Production path: in-progress task at restart routes through the
    unified auto-revisit primitive. A fresh root is spawned with
    revisit_of_task_id=root; notify_failed is suppressed because the work
    is being retried."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    # Root manager task is BLOCKED+DELEGATED waiting on its in-flight child.
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child work", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed.
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    # Cascade propagated to root.
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED
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
        status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED,
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

    from src.daemon.app import create_app
    from src.models import JobInterpreter, JobRecord, JobStatus

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

    from src.daemon import jobs_runner
    from src.models import JobInterpreter, JobRecord, JobStatus

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
