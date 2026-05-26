"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    # Seed a minimal teams.yaml so engineering_head is recognized as a manager
    # and dev_agent/product_manager/payment_agent as workers.
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    # Just must not raise
    orch.run_step("TASK-NOPE")


def test_run_step_noop_on_blocked_escalated(runtime, db):
    """A task in blocked(ESCALATED) isn't eligible for run_step — it waits
    for /resolve-escalation to transition it first. Second-hand enqueue
    must be silently ignored."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
                   note="halted")
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED


def test_run_step_over_budget_parks_escalated(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-1", orchestration_step_count=3)  # already at the cap

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
    assert t.note and "max steps" in t.note
    # Audit row
    escalations = [
        a for a in db.get_audit_logs("T-1") if a["action"] == "escalation"
    ]
    assert len(escalations) == 1
    assert "max steps" in escalations[0]["payload"]["reason"]


def test_run_step_transitions_pending_to_in_progress_and_increments_count(
    runtime, db, monkeypatch,
):
    """On pickup, run_step must flip to in_progress, clear block fields,
    and increment the step counter exactly once — BEFORE invoking the agent."""
    from src.orchestrator.orchestrator import Orchestrator, WorkspaceNotInitialized

    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    # Force _run_agent to raise so we can inspect the DB state mid-flight.
    captured: dict = {}
    def fail(task_id, agent, prompt, on_session_started=None):
        t = db.get_task(task_id)
        captured["status"] = t.status
        captured["count"] = t.orchestration_step_count
        captured["block_kind"] = t.block_kind
        captured["note"] = t.note
        raise WorkspaceNotInitialized("fake")
    monkeypatch.setattr(orch, "_run_agent", fail)

    orch.run_step("T-1")

    assert captured["status"] == TaskStatus.IN_PROGRESS
    assert captured["count"] == 1
    assert captured["block_kind"] is None
    assert captured["note"] is None


def _make_report(output_summary: str, status: str = "completed",
                 artifact_dir: str | None = None):
    from src.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary, artifact_dir=artifact_dir,
    )


def _make_result(success: bool = True, duration: int = 1):
    from src.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, session_id="sess-x", duration_seconds=duration,
    )

class _SlugQueue:
    """Test adapter: wraps asyncio.Queue so put_nowait(slug, task_id) works.
    
    Production code calls _queue.put_nowait(slug, task_id), but tests use a
    stdlib asyncio.Queue. This shim accepts the 2-arg form and stores the
    (slug, task_id) tuple on the underlying queue.
    """
    def __init__(self) -> None:
        import asyncio as _asyncio
        self._q: _asyncio.Queue = _asyncio.Queue()
    def put_nowait(self, slug: str, task_id: str) -> None:
        self._q.put_nowait((slug, task_id))
    def qsize(self) -> int:
        return self._q.qsize()
    def get_nowait(self):
        return self._q.get_nowait()



def test_run_step_done_completes_task_and_enqueues_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    # Parent in blocked(DELEGATED), child in pending.
    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    # Wire a fake queue
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "Looks great"}),
            artifact_dir="artifacts/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.note == "Looks great"
    assert child.final_artifact_dir == "artifacts/run-1"

    # Parent should be enqueued
    assert q.qsize() == 1
    assert q.get_nowait() == ("test", "T-PAR")


def test_run_step_escalate_parks_blocked_and_leaves_parent_parked(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.BLOCKED
    assert child.block_kind == BlockKind.ESCALATED
    assert child.note == "needs founder"

    # Parent stays parked — escalation is NOT a terminal for sibling-summing.
    assert q.qsize() == 0
    assert db.get_task("T-PAR").status == TaskStatus.BLOCKED

    # Audit row
    escalations = [a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"]
    assert any("needs founder" in e["payload"]["reason"] for e in escalations)


def test_run_step_delegate_spawns_child_and_blocks_self(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Write a PR",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    # Parent now blocked(DELEGATED)
    parent = db.get_task("T-1")
    assert parent.status == TaskStatus.BLOCKED
    assert parent.block_kind == BlockKind.DELEGATED
    assert "dev_agent" in (parent.note or "")

    # Exactly one child exists, is pending, and is enqueued
    children = db.get_children("T-1")
    assert len(children) == 1
    child_id = children[0]
    child = db.get_task(child_id)
    assert child.status == TaskStatus.PENDING
    assert child.assigned_agent == "dev_agent"
    assert child.brief == "Write a PR"
    assert child.parent_task_id == "T-1"
    assert q.get_nowait() == ("test", child_id)


def test_run_step_delegate_inherits_session_timeout(runtime, db, monkeypatch):
    """A delegated child copies the parent's session_timeout_seconds so a
    revisit-time bump propagates down the whole lineage."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="engineering_head",
        session_timeout_seconds=7200,
    ))
    orch = Orchestrator(
        db=db, settings=Settings(),
        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate", "agent": "dev_agent", "prompt": "Do it",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    children = db.get_children("T-1")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.session_timeout_seconds == 7200


def test_run_step_invalid_delegate_fails_task(runtime, db, monkeypatch):
    """A delegate with no agent name is unrecoverable — fail the task and
    notify the parent (which may itself be root — no-op in that case)."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "delegate", "prompt": "x"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "invalid delegate" in t.note


def test_run_step_session_failure_cascades_to_parent_no_retry(
    runtime, db, monkeypatch,
):
    """In-tree no-retry policy: when a delegated child fails, the parent
    must FAIL too with a cascading note — the EH does not get another
    decision step in the original lineage. (Re-enqueueing the parent has
    historically produced runs of 6+ failed retries: TASK-033..045.)

    The auto-revisit that fires alongside the cascade is a SEPARATE,
    independent root, not a re-enqueue of the parent — so this test only
    asserts the in-tree cascade behavior. See
    test_run_step_opaque_failure_spawns_auto_revisit for the new tree.
    """
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "session failed" in (child.note or "")

    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.FAILED
    assert parent.block_kind is None
    assert "T-CHD" in (parent.note or "")
    assert "delegated child" in (parent.note or "")
    # Queue holds the spawned auto-revisit root (NOT a re-enqueue of T-PAR).
    assert q.qsize() == 1
    slug, revisit_id = q.get_nowait()
    assert slug == "test"
    assert revisit_id != "T-PAR"
    revisit = db.get_task(revisit_id)
    assert revisit.parent_task_id is None
    assert revisit.revisit_of_task_id == "T-PAR"


def test_run_step_session_failure_cascades_up_chain(
    runtime, db, monkeypatch,
):
    """No-retry policy bubbles through the full ancestor chain: a failing
    grandchild fails its parent, which fails its grandparent, and so on.
    """
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="r",
                              assigned_agent="engineering_head"))
    db.update_task("T-ROOT", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-MID", brief="m",
        assigned_agent="engineering_head", parent_task_id="T-ROOT",
    ))
    db.update_task("T-MID", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-LEAF", brief="l",
        assigned_agent="dev_agent", parent_task_id="T-MID",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-LEAF")

    assert db.get_task("T-LEAF").status == TaskStatus.FAILED
    assert db.get_task("T-MID").status == TaskStatus.FAILED
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED
    # Cascade in-tree happens as before; the auto-revisit is a SEPARATE
    # new root (queued once, predecessor=T-ROOT). Queue contains the new
    # root only — no in-tree re-enqueues.
    assert orch._queue.qsize() == 1
    slug, revisit_id = orch._queue.get_nowait()
    assert slug == "test"
    assert revisit_id not in ("T-ROOT", "T-MID", "T-LEAF")
    revisit = db.get_task(revisit_id)
    assert revisit.parent_task_id is None
    assert revisit.revisit_of_task_id == "T-ROOT"


def test_run_step_session_failure_note_includes_diagnostics(
    runtime, db, monkeypatch,
):
    """The `agent session failed` note must include rc and a stderr tail
    so post-mortems don't need to grep daemon.log. TASK-044/045 class of
    failure (subprocess exits without calling back) is the motivating case.
    """
    from src.orchestrator.executors import ExecutorResult
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    import asyncio
    orch._queue = _SlugQueue()

    result = ExecutorResult(
        success=True,  # rc=0 but no report — the TASK-045 signature
        duration_seconds=703,
        session_id="sess-x",
        returncode=0,
        stdout_tail="wrote ExplorePage.tsx\n",
        stderr_tail="",
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    note = db.get_task("T-1").note or ""
    assert "rc=0" in note
    assert "no completion callback" in note
    assert "wrote ExplorePage.tsx" in note


def test_run_step_opaque_failure_spawns_auto_revisit_with_error_context(
    runtime, db, monkeypatch,
):
    """On the 3 opaque failures, the orchestrator packs structured error
    context and spawns a NEW root linked to the predecessor root via
    revisit_of_task_id. The team manager owns the new root and can decide
    what to do next."""
    import asyncio
    from src.orchestrator.executors import ExecutorResult
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              team="engineering",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    failing_result = ExecutorResult(
        success=True,  # rc=0 but no callback — TASK-045 class
        duration_seconds=120,
        session_id="sess-x",
        returncode=0,
        stdout_tail="wrote ExplorePage.tsx\n",
        stderr_tail="",
    )
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (failing_result, None))

    orch.run_step("T-CHD")

    slug, revisit_id = orch._queue.get_nowait()
    assert slug == "test"
    revisit = db.get_task(revisit_id)
    # New root inherits brief + team from the predecessor root.
    assert revisit.brief == "parent brief"
    assert revisit.team == "engineering"
    assert revisit.assigned_agent == "engineering_head"
    assert revisit.parent_task_id is None
    assert revisit.revisit_of_task_id == "T-PAR"
    assert revisit.status == TaskStatus.PENDING

    # Audit on the new root carries the structured error context.
    rows = db.get_audit_logs(revisit_id)
    auto_entry = next(r for r in rows if r["action"] == "auto_revisit_of")
    payload = auto_entry["payload"]
    assert payload["predecessor_root"] == "T-PAR"
    assert payload["failed_task"] == "T-CHD"
    assert payload["failed_agent"] == "dev_agent"
    assert payload["attempt"] == 1
    err = payload["error_context"]
    assert err["mode"] == "session_failure"
    assert err["rc"] == 0
    assert err["missing_callback"] is True
    assert "wrote ExplorePage.tsx" in err["stdout_tail"]


def test_run_step_opaque_failure_on_root_manager_spawns_auto_revisit(
    runtime, db, monkeypatch,
):
    """Manager-level opaque failure (root task itself crashes) also
    triggers auto-revisit. Predecessor is the failed root itself."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="root brief",
                              team="engineering",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-ROOT")

    assert db.get_task("T-ROOT").status == TaskStatus.FAILED
    slug, revisit_id = orch._queue.get_nowait()
    assert slug == "test"
    revisit = db.get_task(revisit_id)
    assert revisit.revisit_of_task_id == "T-ROOT"
    assert revisit.brief == "root brief"


def test_run_step_opaque_failure_on_exception_spawns_auto_revisit(
    runtime, db, monkeypatch,
):
    """Exception escaping _run_agent triggers auto-revisit with mode=exception."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def boom(task_id, agent, prompt, on_session_started=None):
        raise RuntimeError("workspace not initialized")

    monkeypatch.setattr(orch, "_run_agent", boom)

    orch.run_step("T-1")

    slug, revisit_id = orch._queue.get_nowait()
    assert slug == "test"
    rows = db.get_audit_logs(revisit_id)
    auto_entry = next(r for r in rows if r["action"] == "auto_revisit_of")
    err = auto_entry["payload"]["error_context"]
    assert err["mode"] == "exception"
    assert "workspace not initialized" in err["detail"]


def test_run_step_auto_revisit_capped_at_two(
    runtime, db, monkeypatch,
):
    """After 2 prior auto-revisits in the chain, no more are spawned —
    the cascade still runs but the queue stays empty."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    # Chain: T-ORIG <- T-AR1 (auto-revisit of T-ORIG) <- T-AR2 (auto of T-AR1)
    # T-AR2 is the current task; if it fails, no more auto-revisits.
    db.insert_task(TaskRecord(id="T-ORIG", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(
        id="T-AR1", brief="b", assigned_agent="engineering_head",
        revisit_of_task_id="T-ORIG", status=TaskStatus.FAILED,
    ))
    db.insert_task(TaskRecord(
        id="T-AR2", brief="b", assigned_agent="engineering_head",
        revisit_of_task_id="T-AR1",
    ))
    # Mark T-AR1 and T-AR2 as auto-revisits in the audit log. Both prior
    # entries are the SAME kind as the new failure we'll trigger below
    # (_make_result(success=False) with no error/rc → classifier returns
    # "session_failed") so the per-kind cap (spec §5) is exhausted at 2.
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(
        task_id="T-AR1", predecessor_root="T-ORIG",
        failed_task="T-ORIG", failed_agent="engineering_head",
        cascade=["T-ORIG"],
        failure_kind="session_failed",
        error_context={"mode": "session_failure"},
        attempt=1,
    )
    audit.log_auto_revisit_of(
        task_id="T-AR2", predecessor_root="T-AR1",
        failed_task="T-AR1", failed_agent="engineering_head",
        cascade=["T-AR1"],
        failure_kind="session_failed",
        error_context={"mode": "session_failure"},
        attempt=2,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-AR2")

    # T-AR2 fails; no further auto-revisit is spawned.
    assert db.get_task("T-AR2").status == TaskStatus.FAILED
    assert orch._queue.qsize() == 0


def test_run_step_self_blocked_does_not_spawn_auto_revisit(
    runtime, db, monkeypatch,
):
    """Self-blocked is a deliberate agent decision — not an opaque failure.
    No auto-revisit should be spawned."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(),
                                         _make_report("blocked on prereq",
                                                      status="blocked")))

    orch.run_step("T-1")

    assert db.get_task("T-1").status == TaskStatus.FAILED
    assert orch._queue.qsize() == 0


def test_run_step_auto_revisit_header_injected_on_first_step(
    runtime, db, monkeypatch,
):
    """The team manager's first prompt on the auto-revisit root must
    include AUTO-REVISIT CONTEXT with the structured error payload."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _build_agent_prompt

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              team="engineering",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(
        id="T-NEW", brief="parent brief", team="engineering",
        assigned_agent="engineering_head",
        revisit_of_task_id="T-PAR",
    ))
    from src.infrastructure.audit_logger import AuditLogger
    AuditLogger(db).log_auto_revisit_of(
        task_id="T-NEW", predecessor_root="T-PAR",
        failed_task="T-CHD", failed_agent="dev_agent",
        cascade=["T-PAR", "T-CHD"],
        failure_kind="no_callback",
        error_context={
            "mode": "session_failure", "rc": 0, "missing_callback": True,
            "stderr_tail": "", "stdout_tail": "wrote files",
            "executor_error": None,
        },
        attempt=1,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    task = db.get_task("T-NEW")
    prompt = _build_agent_prompt(orch, task, "engineering_head")
    assert "AUTO-REVISIT CONTEXT" in prompt
    assert "T-PAR" in prompt
    assert "T-CHD" in prompt
    assert "dev_agent" in prompt
    assert "no completion callback" in prompt
    assert "wrote files" in prompt
    # Shared discipline tail (TALK-028): manager must status-assess and choose
    # execute-with-divergence-note vs escalate, not improvise.
    assert "Status-assess before acting" in prompt
    assert "Do NOT improvise" in prompt


def test_run_step_worker_self_blocked_fails_task(runtime, db, monkeypatch):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="ran out of tokens", status="blocked")))

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and t.note.startswith("self-blocked:")


def test_run_step_worker_completion_is_done_not_parsed_as_eh_decision(
    runtime, db, monkeypatch,
):
    """P1 regression: workers don't speak the NextStep JSON protocol. A plain
    prose output_summary from a delegated worker must be treated as `done`,
    not escalated as "non-JSON EH decision"."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    # Parent (EH) delegated to dev_agent (worker).
    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary="Shipped the PR — see branch feat/x",
            artifact_dir="artifacts/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.block_kind is None
    assert child.note == "Shipped the PR — see branch feat/x"
    assert child.final_artifact_dir == "artifacts/run-1"
    # Parent wakes on the child terminal.
    assert q.get_nowait() == ("test", "T-PAR")


def test_run_step_delegated_worker_emits_review_verdict_and_scorecard(
    runtime, db, monkeypatch,
):
    """P1 regression: tiers are computed from review_verdict audit rows. When
    a delegated worker reaches a terminal state, the EH's implicit verdict
    (approved on COMPLETED, rejected on FAILED) must be logged — otherwise
    every delegated agent stays on stale performance data."""
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-OK", brief="ok",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    ))
    db.insert_task(TaskRecord(
        id="T-BAD", brief="bad",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Success path.
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="done")))
    orch.run_step("T-OK")

    # Failure path (session failed, no report).
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))
    orch.run_step("T-BAD")

    ok_verdicts = [a for a in db.get_audit_logs("T-OK")
                   if a["action"] == "review_verdict"]
    bad_verdicts = [a for a in db.get_audit_logs("T-BAD")
                    if a["action"] == "review_verdict"]
    assert len(ok_verdicts) == 1
    assert ok_verdicts[0]["agent"] == "engineering_head"
    assert ok_verdicts[0]["payload"]["verdict"] == "approved"
    assert ok_verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"
    assert len(bad_verdicts) == 1
    assert bad_verdicts[0]["payload"]["verdict"] == "rejected"
    assert bad_verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"
    # Scorecard persisted for the delegated worker.
    assert db.get_scorecard("dev_agent") is not None


def test_run_step_root_eh_task_skips_review_verdict(runtime, db, monkeypatch):
    """Root tasks (no parent) are EH-assigned and must NOT produce verdict
    rows — the EH is not reviewing itself."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="r",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps(
                                {"action": "done", "summary": "ok"}))))
    orch.run_step("T-ROOT")

    verdicts = [a for a in db.get_audit_logs("T-ROOT")
                if a["action"] == "review_verdict"]
    assert verdicts == []


def test_run_step_skips_task_with_cancelled_at(runtime, db, monkeypatch):
    """Entry guard: once /cancel stamps cancelled_at on a row, a late queue
    entry must be a silent no-op — no in_progress transition, no _run_agent
    call, no step-count increment. The row stays exactly as /cancel left it."""
    from datetime import datetime, timezone

    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-CNL", brief="x",
        assigned_agent="engineering_head",
    ))
    # /cancel's phase-1 writes: FAILED + cancelled_at + founder note.
    now = datetime.now(timezone.utc).isoformat()
    db.update_task(
        "T-CNL",
        status=TaskStatus.FAILED,
        block_kind=None,
        note="cancelled by founder: enough",
        cancelled_at=now,
        completed_at=now,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    called = {"n": 0}
    def sentinel(*a, **k):
        called["n"] += 1
        raise AssertionError("_run_agent must not be called after cancel")
    monkeypatch.setattr(orch, "_run_agent", sentinel)

    orch.run_step("T-CNL")

    t = db.get_task("T-CNL")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: enough"
    assert t.cancelled_at is not None
    assert t.orchestration_step_count == 0
    assert called["n"] == 0


def test_fail_idempotent_on_terminal_task(runtime, db):
    """The post-Popen classifier must not overwrite the founder's note.
    After /cancel flips the row to FAILED, a stray _fail() call (from the
    run_step that was mid-flight when SIGTERM arrived) must no-op."""
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _fail

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="dev_agent"))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, block_kind=None,
                   note="cancelled by founder: stop", cancelled_at=now,
                   completed_at=now)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    _fail(orch, "T-1", note="agent session failed rc=-15")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"  # unchanged


def test_complete_idempotent_on_terminal_task(runtime, db):
    """If the subprocess happened to finish cleanly just before SIGTERM,
    _complete must not resurrect the cancelled row back to COMPLETED."""
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _complete

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="dev_agent"))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, block_kind=None,
                   note="cancelled by founder: stop", cancelled_at=now,
                   completed_at=now)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    _complete(orch, "T-1", note="looks great", artifact_dir="artifacts/run-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.final_artifact_dir is None  # unchanged


def test_run_step_revisit_header_injected_on_first_step(
    runtime, db, monkeypatch,
):
    """New-root task with a revisit_of audit entry and no orchestration_step
    entry: EH prompt must start with the revisit context header."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="Add Alipay support",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052",
            "flagged": "TASK-058",
            "cascade": ["TASK-052", "TASK-053", "TASK-058"],
            "prior_status": "failed",
            "founder_note": "PR #103 already merged",
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    prompt = captured["prompt"]
    assert prompt.startswith("REVISIT CONTEXT:")
    assert "TASK-052" in prompt
    assert "failed" in prompt
    assert "TASK-058" in prompt
    assert "TASK-052 -> TASK-053 -> TASK-058" in prompt or \
           "TASK-052 → TASK-053 → TASK-058" in prompt
    assert "PR #103 already merged" in prompt
    # Shared discipline tail (TALK-028).
    assert "Status-assess before acting" in prompt
    assert "Do NOT improvise" in prompt


def test_run_step_revisit_header_absent_on_second_step(
    runtime, db, monkeypatch,
):
    """After the first orchestration_step audit entry lands, the header must
    disappear — subsequent EH cycles see a vanilla capabilities prompt."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="x",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-072", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    db.insert_audit_log(
        task_id="TASK-072", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "done"}},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert not captured["prompt"].startswith("REVISIT CONTEXT:")


def test_run_step_revisit_header_omits_note_line_when_none(
    runtime, db, monkeypatch,
):
    """founder_note == None => no 'Founder note:' line in the header."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="x",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert "Founder note:" not in captured["prompt"]


def test_run_step_resolved_escalation_header_injected_after_approve(
    runtime, db, monkeypatch,
):
    """After /resolve-escalation --approve, the task is re-enqueued (PENDING).
    On the manager's next decision step, the prompt must start with the
    ESCALATION RESOLVED header so the manager sees the founder's verdict."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-080", brief="Refund $800?",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-080", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-080", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "escalate"}},
    )
    db.insert_audit_log(
        task_id="TASK-080", agent="founder", action="escalation_resolved",
        payload={"decision": "approve", "rationale": "approved one-time exception"},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-080")

    prompt = captured["prompt"]
    assert prompt.startswith("ESCALATION RESOLVED:")
    assert "approved one-time exception" in prompt
    assert "founder approved" in prompt


def test_run_step_resolved_escalation_header_absent_after_next_step(
    runtime, db, monkeypatch,
):
    """Once the manager has taken a decision step after the resolution, the
    header must disappear — its trigger is `latest escalation_resolved id >
    latest orchestration_step id`."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-081", brief="x",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-081", orchestration_step_count=2)
    db.insert_audit_log(
        task_id="TASK-081", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "escalate"}},
    )
    db.insert_audit_log(
        task_id="TASK-081", agent="founder", action="escalation_resolved",
        payload={"decision": "approve", "rationale": "ok"},
    )
    db.insert_audit_log(
        task_id="TASK-081", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 2, "decision": {"action": "done"}},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-081")

    assert not captured["prompt"].startswith("ESCALATION RESOLVED:")


def test_run_step_concurrent_claim_spawns_only_one_agent(
    runtime, db, monkeypatch,
):
    """Regression: when two workers pop the same task_id (e.g. a multi-child
    fan-in race double-enqueued the parent), exactly one must claim the step
    and call _run_agent. The other must observe the claimed state and
    silently no-op.

    Without an atomic CAS on the BLOCKED+DELEGATED → IN_PROGRESS transition,
    both threads pass the eligibility check at run_step steps 1 and both
    write IN_PROGRESS at step 3 → both _run_agent calls fire, producing two
    EH subprocesses on the same brief.
    """
    import json
    import threading
    from src.orchestrator.orchestrator import Orchestrator

    # Parent blocked(DELEGATED) with two children, both terminal → eligible
    # for exactly one EH decision step.
    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.insert_task(TaskRecord(id="T-C1", brief="c1",
                              assigned_agent="dev_agent", parent_task_id="T-PAR"))
    db.insert_task(TaskRecord(id="T-C2", brief="c2",
                              assigned_agent="dev_agent", parent_task_id="T-PAR"))
    db.update_task("T-C1", status=TaskStatus.COMPLETED)
    db.update_task("T-C2", status=TaskStatus.COMPLETED)
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    # Barrier-sync the two threads AFTER each has read the parent row at the
    # top of run_step_impl — both then observe BLOCKED+DELEGATED before either
    # writes IN_PROGRESS. This is the exact race window we're closing.
    barrier = threading.Barrier(2, timeout=5.0)
    original_get_task = db.get_task
    par_reads = [0]
    par_reads_lock = threading.Lock()
    def synced_get_task(task_id):
        result = original_get_task(task_id)
        if task_id == "T-PAR":
            with par_reads_lock:
                par_reads[0] += 1
                should_sync = par_reads[0] <= 2
            if should_sync:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
        return result
    monkeypatch.setattr(db, "get_task", synced_get_task)

    agent_calls: list[tuple[str, str]] = []
    agent_calls_lock = threading.Lock()
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        with agent_calls_lock:
            agent_calls.append((task_id, agent))
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "ok"})
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    errs: list[BaseException] = []
    def worker():
        try:
            orch.run_step("T-PAR")
        except BaseException as e:
            errs.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(timeout=5.0); t2.join(timeout=5.0)

    assert not t1.is_alive() and not t2.is_alive(), "worker thread hung"
    assert not errs, f"worker threads raised: {errs}"
    # The assertion: exactly one EH subprocess spawned, not two.
    assert len(agent_calls) == 1, (
        f"expected 1 _run_agent call, got {len(agent_calls)}: {agent_calls}"
    )
    # And the step counter incremented exactly once — not twice.
    par = db.get_task("T-PAR")
    assert par.orchestration_step_count == 1, (
        f"expected orchestration_step_count=1, got {par.orchestration_step_count}"
    )


def test_revisit_header_includes_sr_summary(runtime, db):
    """When the predecessor task submitted SRs, revisit header lists them."""
    from datetime import datetime, timezone

    from src.infrastructure.audit_logger import AuditLogger
    from src.models import (
        ScriptInterpreter,
        ScriptRequestRecord,
        ScriptRequestStatus,
        TaskRecord,
        TaskStatus,
    )
    from src.orchestrator.run_step import _revisit_header_if_applicable

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    predecessor = TaskRecord(
        id="TASK-001",
        assigned_agent="engineering_head",
        team="engineering",
        brief="orig",
        status=TaskStatus.FAILED,
    )
    revisit = TaskRecord(
        id="TASK-002",
        assigned_agent="engineering_head",
        team="engineering",
        brief="retry",
        status=TaskStatus.IN_PROGRESS,
    )
    db.insert_task(predecessor)
    db.insert_task(revisit)

    # Seed an SR submitted by the predecessor.
    sr = ScriptRequestRecord(
        id="SR-019",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="Close PR #247 with approval comment",
        rationale="r",
        script_text="echo x",
        interpreter=ScriptInterpreter.BASH,
        status=ScriptRequestStatus.COMPLETED,
        created_at=now,
    )
    db.insert_script_request(sr)

    # Audit: script_submitted on predecessor, revisit_of on revisit.
    audit = AuditLogger(db)
    audit.log_script_submitted(
        task_id="TASK-001",
        sr_id="SR-019",
        agent="engineering_head",
        title="Close PR #247 with approval comment",
        interpreter="bash",
        cwd_hint=None,
        byte_size=10,
        line_count=1,
    )
    db.insert_audit_log(
        task_id="TASK-002",
        agent="founder",
        action="revisit_of",
        payload={
            "predecessor_root": "TASK-001",
            "flagged": "TASK-001",
            "prior_status": "failed",
            "cascade": ["TASK-001"],
            "founder_note": "retry",
        },
    )

    # Mock orchestrator: just needs ._db.
    class _MockOrch:
        def __init__(self, d):
            self._db = d

    header = _revisit_header_if_applicable(_MockOrch(db), "TASK-002")
    assert header is not None
    assert "SR-019" in header
    assert "Close PR #247" in header
    assert "grassland scripts show SR-019" in header
    assert "grassland scripts output SR-019" in header


# ---- Cancel-race Guard B: post-_run_agent re-check ----
# See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.2.

def test_run_step_drops_delegate_when_cancelled_during_session(runtime, db, monkeypatch):
    """Guard B: /cancel can land between try_claim_for_step and subprocess exit.
    The l.41 entry guard only catches NEW enqueues. When `_run_agent` returns
    with a delegate decision but the task is now cancelled, no child task may
    be spawned and the founder-set status / note must remain intact.

    This is the regression check for the TASK-497 cancel race documented in
    docs/superpowers/specs/2026-05-26-cancel-race-design.md.
    """
    import json
    from datetime import datetime, timezone
    from src.orchestrator.orchestrator import Orchestrator
    from src.models import TokenUsage

    # Workspace must exist so _validate_delegate doesn't error out and
    # take us through the (already-idempotent) _fail path instead of the
    # (not-yet-guarded) delegate path we're testing.
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-RACE", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def cancel_then_delegate(*a, **k):
        # Simulate /cancel landing while the subprocess was running. By the
        # time _run_agent returns, the founder has already stamped the row.
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            "T-RACE",
            status=TaskStatus.FAILED,
            block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now,
            completed_at=now,
        )
        # The EH session would have produced its decision before SIGTERM
        # took effect; we model that by returning a delegate decision anyway.
        result = _make_result()
        result.token_usage = TokenUsage(
            input_tokens=10, output_tokens=20, model="claude-opus",
        )
        report = _make_report(
            output_summary=json.dumps({
                "action": "delegate", "agent": "dev_agent", "prompt": "ship it",
            }),
        )
        return result, report

    monkeypatch.setattr(orch, "_run_agent", cancel_then_delegate)

    orch.run_step("T-RACE")

    t = db.get_task("T-RACE")
    # Founder's terminal state preserved.
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # No child task spawned by the delegate decision.
    assert db.get_children("T-RACE") == []
    # Queue stays empty — nothing to dispatch.
    assert orch._queue.qsize() == 0
    # Token usage IS persisted regardless of cancel (spec §5.2 — provider
    # really charged for the session; /tokens rollups must reflect spend).
    usage_rows = db.list_session_token_usage(task_id="T-RACE")
    assert len(usage_rows) == 1
    assert usage_rows[0]["input_tokens"] == 10
    assert usage_rows[0]["output_tokens"] == 20


# ---- Cancel-race Guard C: shared terminal predicate ----
# See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.

def test_is_already_terminal_predicate(runtime, db):
    """Single source of truth for the `done` / `delegate` / `escalate` / `_fail`
    / `_complete` idempotence guards. Returns True for missing tasks, for
    terminal statuses (COMPLETED, FAILED), and for cancelled rows even if their
    status hasn't yet flipped to FAILED.
    """
    from datetime import datetime, timezone
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _is_already_terminal

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))

    # Missing task → True (treat as terminal; nothing to act on).
    assert _is_already_terminal(orch, "T-NOPE") is True

    # PENDING → False.
    db.insert_task(TaskRecord(id="T-A", brief="a"))
    assert _is_already_terminal(orch, "T-A") is False

    # IN_PROGRESS → False.
    db.update_task("T-A", status=TaskStatus.IN_PROGRESS)
    assert _is_already_terminal(orch, "T-A") is False

    # BLOCKED → False. (Parent in blocked(delegated) is waiting on a child,
    # not terminal; a fresh manager step is allowed.)
    db.update_task("T-A", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    assert _is_already_terminal(orch, "T-A") is False

    # COMPLETED → True.
    db.update_task("T-A", status=TaskStatus.COMPLETED, block_kind=None)
    assert _is_already_terminal(orch, "T-A") is True

    # FAILED → True.
    db.insert_task(TaskRecord(id="T-B", brief="b"))
    db.update_task("T-B", status=TaskStatus.FAILED)
    assert _is_already_terminal(orch, "T-B") is True

    # Cancelled even if status hasn't yet been flipped to FAILED — defense
    # in depth against a future code path that stamps cancelled_at without
    # touching status. Per spec §5.3.
    db.insert_task(TaskRecord(id="T-C", brief="c"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-C", status=TaskStatus.IN_PROGRESS, cancelled_at=now)
    assert _is_already_terminal(orch, "T-C") is True


def test_run_step_delegate_atomic_against_cancel_between_recheck_and_cas(
    runtime, db, monkeypatch,
):
    """Codex P1 on PR #34: even after Guard B's re-fetch passes, /cancel can
    land between the re-fetch and the delegate's insert+update. The atomic
    CAS in db.try_delegate must close this window — no child created, parent
    state preserved.

    Simulated by monkey-patching db.try_delegate to invoke /cancel just before
    its conditional UPDATE runs. This reproduces the worst-case interleaving
    that the Python-level check-then-act would have lost.
    """
    import json
    from datetime import datetime, timezone
    from src.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-RACE2", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # _run_agent returns a delegate without cancelling — Guard B re-fetch
    # will pass. The cancel races in via the monkey-patched try_delegate.
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps({
                                "action": "delegate", "agent": "dev_agent",
                                "prompt": "ship it",
                            }),
                        )))

    # Wrap try_delegate so the cancel lands at the worst moment: AFTER Guard B
    # re-checks but BEFORE the CAS write. The atomic SELECT inside try_delegate
    # should observe the cancel and return False.
    real_try_delegate = db.try_delegate
    def racy_try_delegate(parent_id, child, *, parent_note):
        # Simulate founder cancel landing just before the CAS SELECT.
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            parent_id,
            status=TaskStatus.FAILED, block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now, completed_at=now,
        )
        return real_try_delegate(parent_id, child, parent_note=parent_note)
    monkeypatch.setattr(db, "try_delegate", racy_try_delegate)

    orch.run_step("T-RACE2")

    t = db.get_task("T-RACE2")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # CRITICAL: no child created — the atomic CAS observed the cancel and bailed.
    assert db.get_children("T-RACE2") == []
    assert orch._queue.qsize() == 0


def test_run_step_escalate_atomic_against_cancel_between_recheck_and_cas(
    runtime, db, monkeypatch,
):
    """Codex P2 on PR #34: same race shape for the escalate branch — cancel
    landing between Guard B and the conditional UPDATE must not resurrect a
    cancelled row into BLOCKED(ESCALATED)."""
    import json
    from datetime import datetime, timezone
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-ESC", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps({
                                "action": "escalate", "reason": "blocked on creds",
                            }),
                        )))

    real_try_escalate = db.try_escalate
    def racy_try_escalate(task_id, *, reason):
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            task_id,
            status=TaskStatus.FAILED, block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now, completed_at=now,
        )
        return real_try_escalate(task_id, reason=reason)
    monkeypatch.setattr(db, "try_escalate", racy_try_escalate)

    orch.run_step("T-ESC")

    t = db.get_task("T-ESC")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # block_kind stays None (cancel cleared it); not BLOCKED(ESCALATED).
    assert t.block_kind is None
