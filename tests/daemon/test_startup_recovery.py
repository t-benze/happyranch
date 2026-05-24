from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _sweep_on_startup
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.runtime import RuntimeDir


def _seed_org(tmp_path: Path, slug: str = "test") -> Database:
    """Initialize a multi-org runtime with one seeded org and return its DB."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    org_root = runtime.orgs_dir / slug
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    return Database(org_root / "grassland.db")


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

    assert queue._queue.get_nowait() == ("test", "T-PAR")


def test_sweep_blocked_delegated_with_live_children_does_not_reenqueue(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-PAR", brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", brief="c", parent_task_id="T-PAR"))
    # Child was in progress at crash — the sweep will fail it, which in
    # turn should re-enqueue the parent. So after full sweep, parent IS
    # enqueued, but via the child's failure, not its own blocked row.
    db.update_task("T-CHD", status=TaskStatus.IN_PROGRESS)

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # T-CHD was in_progress → swept to failed → parent enqueued
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    assert queue._queue.get_nowait() == ("test", "T-PAR")


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
    assert queue._queue.get_nowait() == ("test", "T-1")


def test_sweep_calls_notify_failed_on_in_progress_recovery(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-RECOV", brief="x", assigned_agent="eng_worker"))
    db.update_task("T-RECOV", status=TaskStatus.IN_PROGRESS)

    seen: list[dict] = []

    class _FakeOrch:
        def notify_failed(self, **kwargs):
            seen.append(kwargs)

    _sweep_on_startup(db, TaskQueue(), "test", _FakeOrch())
    assert seen and seen[0]["task_id"] == "T-RECOV"
    assert seen[0]["agent"] == "eng_worker"
    assert seen[0]["failure_kind"] == "daemon_restart"


def test_sweep_works_without_orchestrator_arg(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-BC", brief="x"))
    db.update_task("T-BC", status=TaskStatus.IN_PROGRESS)
    _sweep_on_startup(db, TaskQueue(), "test")
    assert db.get_task("T-BC").status == TaskStatus.FAILED


def test_lifespan_recovers_orphaned_running_scripts(tmp_home, daemon_state):
    """SR rows left in 'running' state on daemon startup are force-failed."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from src.daemon.app import create_app
    from src.models import ScriptInterpreter, ScriptRequestRecord, ScriptRequestStatus

    org = daemon_state.orgs["alpha"]
    # Seed: insert a pending SR then mark it running manually.
    sr = ScriptRequestRecord(
        id="SR-001",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="echo x",
        interpreter=ScriptInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_script_request(sr)
    org.db._conn.execute(
        "UPDATE script_requests SET status='running', started_at='2026-05-23T00:00:00Z' WHERE id='SR-001'"
    )
    org.db._conn.commit()

    # Boot lifespan via TestClient context manager — startup hook fires.
    app = create_app(daemon_state)
    with TestClient(app):
        # Query inside the context so the DB is still open (lifespan teardown
        # calls close_all() on __exit__, after which the connection is gone).
        fetched = org.db.get_script_request("SR-001")

    assert fetched is not None
    assert fetched.status == ScriptRequestStatus.FAILED
    assert fetched.finished_at is not None


def test_terminate_all_inflight_awaits_runner_tasks(tmp_home, daemon_state):
    """Regression: clean shutdown must let in-flight runner tasks persist
    terminal state BEFORE the per-org DB is closed. Without this, an SR sits
    in `running` until the next startup recovery scan."""
    import asyncio
    from datetime import datetime, timezone

    from src.daemon import scripts_runner
    from src.models import ScriptInterpreter, ScriptRequestRecord, ScriptRequestStatus

    org = daemon_state.orgs["alpha"]
    sr = ScriptRequestRecord(
        id="SR-100",
        task_id="TASK-100",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="x",
        interpreter=ScriptInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_script_request(sr)
    org.db._conn.execute(
        "UPDATE script_requests SET status='running' WHERE id='SR-100'"
    )
    org.db._conn.commit()

    # Simulate a runner task that's still mid-flight: it sleeps briefly, then
    # transitions the row to FAILED. terminate_all_inflight must await this.
    async def fake_runner() -> None:
        await asyncio.sleep(0.05)
        org.db.transition_script_to_terminal(
            "SR-100",
            status=ScriptRequestStatus.FAILED,
            exit_code=-15,
            finished_at="2026-05-23T00:00:01Z",
            duration_ms=50,
            stdout_head="",
            stderr_head="killed by shutdown",
        )

    async def run_test() -> None:
        task = asyncio.create_task(fake_runner())
        scripts_runner.register_runner_task("SR-100", task)
        # No subprocesses to kill — just await the runner task.
        await scripts_runner.terminate_all_inflight(
            grace_seconds=0, persist_timeout_seconds=2.0,
        )

    asyncio.run(run_test())

    fetched = org.db.get_script_request("SR-100")
    assert fetched.status == ScriptRequestStatus.FAILED, (
        "shutdown returned before the runner task persisted terminal state — "
        "row would have stayed `running` until next startup"
    )
