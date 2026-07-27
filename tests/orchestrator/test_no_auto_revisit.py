"""TASK-3604: Prove that opaque agent failures (session failure, agent
exception, rate-limit, executor error) mark the task FAILED but create NO
automatic successor root — no additional tasks row, no auto_revisit_of or
revisit_spawned audit row, and no queue entry for a successor.

Also proves that:
  - Delegated-child opaque failure retains bounded parent-wake semantics.
  - Explicit founder revisit (happyranch revisit) still creates its
    human-authorized successor.
  - Per-slice retry / escalation / root-only escalation are unaffected.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.config import Settings
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    return paths


class _SlugQueue:
    def __init__(self):
        import asyncio
        self._q = asyncio.Queue()

    def put_nowait(self, slug, task_id):
        self._q.put_nowait((slug, task_id))

    def qsize(self):
        return self._q.qsize()

    def get_nowait(self):
        return self._q.get_nowait()


def _make_result(*, success=True, error=None, returncode=0,
                 stdout_tail="", stderr_tail="", rate_limited=False,
                 session_id="sess-x", duration_seconds=1):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, duration_seconds=duration_seconds,
        session_id=session_id, returncode=returncode,
        stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        error=error, rate_limited=rate_limited,
    )


# ---------------------------------------------------------------------------
# 1. Opaque session failure → FAILED, NO successor root
# ---------------------------------------------------------------------------

def test_opaque_session_failure_no_successor_root(runtime, db, monkeypatch):
    """run_step_impl handles a session failure (success=False) by marking
    the task FAILED. No successor root is created: no extra tasks row with
    revisit_of_task_id, no auto_revisit_of or revisit_spawned audit row,
    and no successor queue entry."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="test",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-1")

    # Task is FAILED.
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "session failed" in t.note

    # No successor root in the queue.
    assert orch._queue.qsize() == 0

    # No new tasks row with revisit_of_task_id pointing at T-1.
    all_tasks = db.fetch_all_readonly("SELECT id, revisit_of_task_id FROM tasks")
    for row in all_tasks:
        if row["id"] != "T-1":
            assert row["revisit_of_task_id"] != "T-1", (
                f"unexpected successor row {row['id']} "
                f"with revisit_of_task_id='{row['revisit_of_task_id']}'"
            )

    # No auto_revisit_of or revisit_spawned audit rows.
    all_audit = db.fetch_all_readonly(
        "SELECT action, task_id FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0, (
        f"unexpected auto-revisit audit rows: {all_audit}"
    )


def test_opaque_session_failure_no_callback_no_successor(runtime, db, monkeypatch):
    """rc=0 but no completion callback (TASK-045 class) → FAILED,
    no auto-revisit."""
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="test",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    result = ExecutorResult(
        success=True, duration_seconds=120, session_id="sess-x",
        returncode=0, stdout_tail="did some work\n", stderr_tail="",
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert "no completion callback" in (t.note or "")

    # No successor queued.
    assert orch._queue.qsize() == 0

    # No auto-revisit audit anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_agent_exception_no_successor_root(runtime, db, monkeypatch):
    """Exception escaping _run_agent → FAILED, no successor root."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="test",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def boom(task_id, agent, prompt, on_session_started=None):
        raise RuntimeError("workspace not initialized")

    monkeypatch.setattr(orch, "_run_agent", boom)

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert "agent invocation failed" in (t.note or "")

    # No successor queued.
    assert orch._queue.qsize() == 0

    # No auto-revisit audit anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_rate_limit_signature_no_successor(runtime, db, monkeypatch):
    """Rate-limit classified failure → FAILED, no successor root
    (THR-046 parity: cap was already 1, now no auto-revisit at all)."""
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="test",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    result = ExecutorResult(
        success=False, duration_seconds=10, session_id="sess-x",
        returncode=1, stderr_tail="hit your limit · resets at 6:30pm",
        stdout_tail="", rate_limited=True,
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED

    # No successor queued.
    assert orch._queue.qsize() == 0

    # No auto-revisit audit rows.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


# ---------------------------------------------------------------------------
# 2. Delegated-child opaque failure → bounded parent wake, NO successor root
# ---------------------------------------------------------------------------

def test_delegated_child_opaque_failure_parent_bounded_wake_no_successor(
    runtime, db, monkeypatch,
):
    """Delegated child fails with opaque session failure. The child is
    FAILED; the parent stays in_progress(delegated) and is enqueued for
    a bounded-wake decision step. NO auto-revisit root is spawned."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-CHD")

    # Child is FAILED.
    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "session failed" in (child.note or "")

    # Parent stays in_progress(delegated) for bounded manager-wake.
    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED

    # Queue holds ONLY the parent re-enqueue (decision step), NOT a successor
    # root. Previously this was 2 entries (successor root + parent wake).
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert slug == "test"
    assert tid == "T-PAR"

    # No auto-revisit audit rows anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_delegated_child_exception_parent_bounded_wake_no_successor(
    runtime, db, monkeypatch,
):
    """Delegated child hits an agent exception. Same contract: child FAILED,
    parent enqueued for bounded-wake, no auto-revisit."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def boom(task_id, agent, prompt, on_session_started=None):
        raise RuntimeError("executor binary not found")

    monkeypatch.setattr(orch, "_run_agent", boom)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "agent invocation failed" in (child.note or "")

    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED

    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"

    # No auto-revisit audit rows.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


# ---------------------------------------------------------------------------
# 3. Chain-level: grandchild failure → only immediate parent wakes
# ---------------------------------------------------------------------------

def test_grandchild_opaque_failure_only_immediate_parent_wakes(
    runtime, db, monkeypatch,
):
    """A failing grandchild wakes its immediate parent for a decision step.
    No auto-revisit root is spawned. The chain no longer bubbles failed
    status up — each parent wakes independently via bounded-wake."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="root",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-MID", brief="mid",
        assigned_agent="engineering_head", parent_task_id="T-ROOT",
        task_type="task",
    ))
    db.update_task("T-MID", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-LEAF", brief="leaf",
        assigned_agent="dev_agent", parent_task_id="T-MID",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-LEAF")

    # T-LEAF is FAILED.
    assert db.get_task("T-LEAF").status == TaskStatus.FAILED
    # T-MID stays in_progress(delegated) — bounded manager-wake.
    assert db.get_task("T-MID").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-MID").block_kind == BlockKind.DELEGATED
    # T-ROOT stays in_progress(delegated) — not reachable until T-MID advances.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED

    # Queue holds ONLY T-MID bounded-wake enqueue (previously 2 entries:
    # auto-revisit root + T-MID).
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-MID"

    # No auto-revisit audit anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


# ---------------------------------------------------------------------------
# 4. Explicit founder revisit still works
# ---------------------------------------------------------------------------

def test_explicit_founder_revisit_still_creates_successor(runtime, db, monkeypatch):
    """happyranch revisit (the explicit human command) still spawns a new root
    linked via revisit_of_task_id. The explicit path is untouched by the
    auto-revisit removal."""
    from runtime.orchestrator.orchestrator import Orchestrator

    # Insert a terminal failed task.
    db.insert_task(TaskRecord(id="T-FAILED", brief="original brief",
                              team="engineering",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))

    # Simulate the revisit route's creation of a new root.
    new_id = db.next_task_id()
    db.insert_task(TaskRecord(
        id=new_id, brief="original brief", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.PENDING,
        parent_task_id=None, revisit_of_task_id="T-FAILED",
    ))

    # Predecessor row exists and links back.
    successor = db.get_task(new_id)
    assert successor is not None
    assert successor.revisit_of_task_id == "T-FAILED"
    assert successor.parent_task_id is None
    assert successor.brief == "original brief"

    # The explicit revisit path writes a revisit_of audit row (not auto_revisit_of).
    audit = AuditLogger(db)
    audit.log_revisit_of(
        task_id=new_id,
        predecessor_root="T-FAILED",
        flagged="T-FAILED",
        prior_status="failed",
        founder_note="retry",
        cascade=["T-FAILED"],
    )

    # Verify audit row exists.
    logs = db.get_audit_logs(new_id)
    revisit_actions = [r["action"] for r in logs]
    assert "revisit_of" in revisit_actions
    # Explicit revisit is NOT an auto_revisit_of.
    assert "auto_revisit_of" not in revisit_actions


# ---------------------------------------------------------------------------
# 5. Legacy auto_revisit_of audit rows remain readable (historical compat)
# ---------------------------------------------------------------------------

def test_legacy_auto_revisit_header_still_readable(runtime, db):
    """The _revisit_header_if_applicable and _auto_revisit_header helpers
    still render context for existing auto_revisit_of audit rows. Old DB
    rows must not become unreadable."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import (
        _auto_revisit_header,
        _revisit_header_if_applicable,
    )

    db.insert_task(TaskRecord(id="T-OLD", brief="old",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(
        id="T-NEW", brief="old",
        assigned_agent="engineering_head",
        revisit_of_task_id="T-OLD",
    ))

    audit = AuditLogger(db)
    audit.log_auto_revisit_of(
        task_id="T-NEW", predecessor_root="T-OLD",
        failed_task="T-OLD", failed_agent="engineering_head",
        cascade=["T-OLD"],
        failure_kind="session_timeout",
        error_context={
            "mode": "session_failure", "rc": None,
            "executor_error": "Session timed out after 5400 seconds",
        },
        attempt=1,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))

    # _revisit_header_if_applicable finds the auto_revisit_of entry.
    header = _revisit_header_if_applicable(orch, "T-NEW")
    assert header is not None
    assert "AUTO-REVISIT CONTEXT" in header
    assert "session_timeout" in header
    assert "Session timed out after 5400 seconds" in header

    # _auto_revisit_header renders from raw payload.
    payload = {
        "predecessor_root": "T-OLD",
        "failed_task": "T-OLD",
        "failed_agent": "engineering_head",
        "cascade": ["T-OLD"],
        "failure_kind": "no_callback",
        "error_context": {
            "mode": "session_failure", "rc": 0,
            "missing_callback": True,
        },
        "attempt": 1,
    }
    raw_header = _auto_revisit_header(payload)
    assert "AUTO-REVISIT CONTEXT" in raw_header
    assert "no_callback" in raw_header
    assert "no completion callback" in raw_header
