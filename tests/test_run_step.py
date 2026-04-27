"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    # Seed a minimal teams.yaml so engineering_head is recognized as a manager
    # and dev_agent/product_manager/payment_agent as workers.
    rt.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    rt.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
    )
    return rt


@pytest.fixture
def db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, runtime=runtime, teams=TeamsRegistry.load(runtime))
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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
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

    orch = Orchestrator(db=db, settings=settings, runtime=runtime, teams=TeamsRegistry.load(runtime))
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
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime, teams=TeamsRegistry.load(runtime))

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
                        runtime=runtime, teams=TeamsRegistry.load(runtime))
    # Wire a fake queue
    q: asyncio.Queue = asyncio.Queue()
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
    assert q.get_nowait() == "T-PAR"


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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    q: asyncio.Queue = asyncio.Queue()
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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    q: asyncio.Queue = asyncio.Queue()
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
    assert q.get_nowait() == child_id


def test_run_step_invalid_delegate_fails_task(runtime, db, monkeypatch):
    """A delegate with no agent name is unrecoverable — fail the task and
    notify the parent (which may itself be root — no-op in that case)."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    orch._queue = asyncio.Queue()

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
    """No-retry policy: when a delegated child fails, the parent must FAIL
    too with a cascading note. The EH does not get another decision step —
    the alternative (re-enqueueing the parent) has historically produced
    runs of 6+ failed retries on the same brief (TASK-033..038, TASK-041..045).
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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    q: asyncio.Queue = asyncio.Queue()
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
    assert q.qsize() == 0


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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    orch._queue = asyncio.Queue()
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-LEAF")

    assert db.get_task("T-LEAF").status == TaskStatus.FAILED
    assert db.get_task("T-MID").status == TaskStatus.FAILED
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED
    assert orch._queue.qsize() == 0


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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    import asyncio
    orch._queue = asyncio.Queue()

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


def test_run_step_worker_self_blocked_fails_task(runtime, db, monkeypatch):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    orch._queue = asyncio.Queue()

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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    q: asyncio.Queue = asyncio.Queue()
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
    assert q.get_nowait() == "T-PAR"


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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    orch._queue = asyncio.Queue()

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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
    orch._queue = asyncio.Queue()

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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
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

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))
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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))

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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))

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
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime, teams=TeamsRegistry.load(runtime))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert "Founder note:" not in captured["prompt"]


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
                        runtime=runtime, teams=TeamsRegistry.load(runtime))

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
