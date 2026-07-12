"""Tests for the ongoing zombie-task reaper (THR-090 Track B).

Covers the state machine: predicate, allowlist, warm-up grace,
fingerprint-tiered confidence, flag-then-cancel-on-TTL, and never-false-reap
guards.
"""
from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import MagicMock, patch

from runtime.daemon.zombie_reaper import (
    FLAG_TTL_FINGERPRINT_SECONDS,
    FLAG_TTL_NO_FINGERPRINT_SECONDS,
    STALE_HEARTBEAT_SECONDS,
    _consume_zombie_fingerprint,
    _sweep_org_zombies,
)
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(seconds: int) -> datetime:
    return _now() - timedelta(seconds=seconds)


ZOMBIE_PID = 99999  # guaranteed-non-existent pid


def _fresh_hb() -> datetime:
    """A heartbeat that is definitely fresh (just now)."""
    return _now()


def _stale_hb() -> datetime:
    """A heartbeat that is definitely stale (older than threshold)."""
    return _ago(STALE_HEARTBEAT_SECONDS + 10)


def _insert_zombie_candidate(
    db: Database,
    task_id: str = "T-ZOMBIE",
    *,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
    last_heartbeat: datetime | None = None,
    executor_pid: int | None = None,
    current_session_id: str | None = "sess-dead",
    assigned_agent: str = "dev_agent",
    zombie_flagged_at: datetime | None = None,
) -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="zombie test", team="engineering",
        assigned_agent=assigned_agent, status=status,
    ))
    # last_heartbeat, executor_pid, current_session_id are set via update_task
    # (they are not part of insert_task's INSERT statement).
    update_kwargs: dict = {
        "current_session_id": current_session_id,
    }
    if last_heartbeat is not None:
        update_kwargs["last_heartbeat"] = last_heartbeat.isoformat()
    if executor_pid is not None:
        update_kwargs["executor_pid"] = executor_pid
    db.update_task(task_id, **update_kwargs)
    if zombie_flagged_at is not None:
        db.update_task(task_id, zombie_flagged_at=zombie_flagged_at.isoformat())


def _insert_task_result(db: Database, task_id: str, agent: str,
                        session_id: str, status: str = "completed") -> None:
    db.insert_task_result(
        task_id=task_id, agent=agent, session_id=session_id,
        status=status, confidence_score=90, output_summary="ok",
    )


# ---------------------------------------------------------------------------
# predicate — allowlist + AND-gate
# ---------------------------------------------------------------------------

def test_healthy_in_progress_fresh_hb_untouched(db: Database):
    """A task with fresh heartbeat + live pid is not a zombie."""
    _insert_zombie_candidate(db, "T-1", last_heartbeat=_fresh_hb(),
                             executor_pid=os.getpid())
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-1")
    assert t.status == TaskStatus.IN_PROGRESS
    assert t.zombie_flagged_at is None


def test_dead_pid_stale_hb_fresh_hb_pid_alive_not_flagged(db: Database):
    """A task with a dead pid (not alive) + stale heartbeat is zombie,
    but a task with a live pid + stale heartbeat is NOT (pid-gated)."""
    # Dead pid + stale hb → flagged
    _insert_zombie_candidate(db, "T-DEAD", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-DEAD")
    assert t.zombie_flagged_at is not None  # flagged

    # Live pid + fresh hb → not flagged
    _insert_zombie_candidate(db, "T-LIVE", last_heartbeat=_fresh_hb(),
                             executor_pid=os.getpid())
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t2 = db.get_task("T-LIVE")
    assert t2.zombie_flagged_at is None


def test_blocked_task_not_touched(db: Database):
    """Tasks with block_kind set (delegated/blocked_on_job) are never zombies."""
    for bk in ["delegated", "blocked_on_job"]:
        tid = f"T-BLOCK-{bk}"
        db.insert_task(TaskRecord(
            id=tid, brief="x", team="engineering",
            assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
            block_kind=bk,  # type: ignore[arg-type]
            last_heartbeat=_stale_hb(), executor_pid=ZOMBIE_PID,
        ))
        _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
        t = db.get_task(tid)
        assert t.zombie_flagged_at is None, f"block_kind={bk} should be excluded"


def test_terminal_task_not_touched(db: Database):
    """Terminal states (completed, failed, cancelled) are never zombies."""
    for st in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
        tid = f"T-TERM-{st.value}"
        db.insert_task(TaskRecord(
            id=tid, brief="x", team="engineering",
            status=st, last_heartbeat=_stale_hb(), executor_pid=ZOMBIE_PID,
        ))
        _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
        t = db.get_task(tid)
        assert t.zombie_flagged_at is None, f"terminal {st} should be excluded"


def test_pending_task_not_touched(db: Database):
    """Pending tasks are never zombies (not yet in_progress)."""
    _insert_zombie_candidate(db, "T-PEND", status=TaskStatus.PENDING,
                             last_heartbeat=_stale_hb(), executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-PEND")
    assert t.zombie_flagged_at is None


def test_escalated_task_not_touched(db: Database):
    """Escalated tasks are never zombies."""
    _insert_zombie_candidate(db, "T-ESC", status=TaskStatus.ESCALATED,
                             last_heartbeat=_stale_hb(), executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-ESC")
    assert t.zombie_flagged_at is None


# ---------------------------------------------------------------------------
# zombie detection → flag
# ---------------------------------------------------------------------------

def test_zombie_flagged_on_first_detection(db: Database):
    """A zombie (dead pid + stale hb + not flagged yet) gets flagged but NOT cancelled."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is not None, "should be flagged on first detection"
    assert t.status == TaskStatus.IN_PROGRESS, "should NOT be cancelled on first flag"
    # Audit row emitted
    actions = [r["action"] for r in db.get_audit_logs("T-Z")]
    assert "zombie_flagged" in actions


def test_zombie_not_double_flagged(db: Database):
    """A zombie that was already flagged is not re-flagged (idempotent flag)."""
    flag_time = _ago(10)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    # Flag time should be unchanged (idempotent)
    assert t.zombie_flagged_at == flag_time


# ---------------------------------------------------------------------------
# flag-then-cancel-on-TTL: no fingerprint (long TTL)
# ---------------------------------------------------------------------------

def test_zombie_cancelled_after_ttl_no_fingerprint(db: Database):
    """Zombie without task_result fingerprint → cancelled after longer TTL."""
    flag_time = _ago(FLAG_TTL_NO_FINGERPRINT_SECONDS + 5)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.status == TaskStatus.CANCELLED
    assert t.cancelled_at is not None
    actions = [r["action"] for r in db.get_audit_logs("T-Z")]
    assert "zombie_cancelled" in actions


def test_zombie_not_cancelled_before_ttl_no_fingerprint(db: Database):
    """Zombie flagged recently → not yet past TTL → NOT cancelled."""
    flag_time = _ago(FLAG_TTL_NO_FINGERPRINT_SECONDS - 10)  # still within window
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.status == TaskStatus.IN_PROGRESS, "should NOT be cancelled before TTL"
    assert t.zombie_flagged_at == flag_time


# ---------------------------------------------------------------------------
# flag-then-cancel-on-TTL: WITH fingerprint (short TTL → consume)
# ---------------------------------------------------------------------------

def test_zombie_with_fingerprint_consumed_after_short_ttl(db: Database):
    """Zombie with task_result fingerprint → consumed (not cancelled) after short TTL."""
    flag_time = _ago(FLAG_TTL_FINGERPRINT_SECONDS + 5)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time,
                             current_session_id="sess-fp")
    _insert_task_result(db, "T-Z", "dev_agent", "sess-fp", status="completed")
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    # The sweep matched the fingerprint → should NOT cancel; the result
    # should be consumed (honored). Since we don't have an orchestrator
    # in unit tests, the completion report consumption path won't fully
    # run but the fingerprint tier should still be detected and the
    # task should NOT be in cancelled.
    assert t.status != TaskStatus.CANCELLED, (
        "fingerprint present → should consume/honor, not cancel"
    )


def test_zombie_with_fingerprint_not_cancelled_even_after_long_ttl(db: Database):
    """Even after long TTL, fingerprint-present zombie should not be cancelled
    — it should be consumed/honored."""
    flag_time = _ago(FLAG_TTL_NO_FINGERPRINT_SECONDS + 60)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time,
                             current_session_id="sess-fp")
    _insert_task_result(db, "T-Z", "dev_agent", "sess-fp", status="completed")
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.status != TaskStatus.CANCELLED, (
        "fingerprint present → should never cancel, always honor"
    )


# ---------------------------------------------------------------------------
# recovery → clear flag
# ---------------------------------------------------------------------------

def test_flag_cleared_on_heartbeat_recovery(db: Database):
    """If a flagged zombie gets a fresh heartbeat → clear the flag."""
    flag_time = _ago(60)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_fresh_hb(),  # now fresh!
                             executor_pid=ZOMBIE_PID,
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "flag should be cleared on recovery"
    actions = [r["action"] for r in db.get_audit_logs("T-Z")]
    assert "zombie_cleared" in actions


def test_flag_cleared_on_pid_recovery(db: Database):
    """If a flagged zombie's pid becomes alive → clear the flag."""
    flag_time = _ago(60)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=os.getpid(),  # alive!
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "flag should be cleared when pid is alive"


# ---------------------------------------------------------------------------
# warm-up grace
# ---------------------------------------------------------------------------

def test_warm_up_window_exempt(db: Database):
    """During warm-up (uptime < 1 heartbeat interval), no tasks are flagged."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=10, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "warm-up window should exempt"


def test_after_warm_up_detects(db: Database):
    """After warm-up (uptime >= 1 heartbeat interval), zombies are detected."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=31, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is not None, "should detect after warm-up"


# ---------------------------------------------------------------------------
# never-false-reap: no executor_pid → not flagged (err toward miss)
# ---------------------------------------------------------------------------

def test_no_executor_pid_not_flagged(db: Database):
    """Without an executor_pid, we can't probe → err toward miss, don't flag."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=None)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "NULL pid → err toward miss"


def test_no_last_heartbeat_not_flagged(db: Database):
    """Without a last_heartbeat, we can't determine staleness → don't flag."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=None,
                             executor_pid=ZOMBIE_PID)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "NULL heartbeat → err toward miss"


def test_stale_but_alive_pid_not_flagged(db: Database):
    """Stale heartbeat + ALIVE pid → NOT flagged (pid probe is definitive)."""
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=os.getpid())
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    assert t.zombie_flagged_at is None, "live pid should never be flagged"


# ---------------------------------------------------------------------------
# probe edge cases
# ---------------------------------------------------------------------------

def test_permission_error_pid_probe_not_flagged(db: Database):
    """If os.kill raises PermissionError → indeterminate → err toward miss."""
    flag_time = _ago(FLAG_TTL_NO_FINGERPRINT_SECONDS + 60)
    _insert_zombie_candidate(db, "T-Z", last_heartbeat=_stale_hb(),
                             executor_pid=1,  # pid 1 usually needs root
                             zombie_flagged_at=flag_time)
    _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30)
    t = db.get_task("T-Z")
    # PermissionError → not ProcessLookupError → indeterminate → err toward miss
    # Should NOT cancel; flag may remain
    assert t.status != TaskStatus.CANCELLED, (
        "PermissionError on pid probe → indeterminate, should not cancel"
    )


# ---------------------------------------------------------------------------
# FIX 1 (Finding 1 HIGH): flagged zombie + fingerprint before TTL → cleared immediately
# ---------------------------------------------------------------------------

def test_flagged_zombie_with_fingerprint_cleared_immediately_before_ttl(db: Database):
    """Finding 1 (HIGH): flagged task + fingerprint appearing before TTL →
    consumed immediately (no TTL wait), flag cleared, zombie_cleared audit emitted.

    Per protocol/05c recovery clause: 'or a result appears' triggers immediate
    flag clearing. Merely NULL-ing zombie_flagged_at without consuming would
    re-flag next tick, so the clear is paired with fingerprint consumption.
    """
    task_id = "T-FP-IMMEDIATE"
    agent = "dev_agent"
    session_id = "sess-fp-imm"

    # Flag the zombie RECENTLY — well within the 30s fingerprint TTL.
    flag_time = _ago(10)
    _insert_zombie_candidate(
        db, task_id, last_heartbeat=_stale_hb(),
        executor_pid=ZOMBIE_PID,
        zombie_flagged_at=flag_time,
        current_session_id=session_id,
        assigned_agent=agent,
    )
    # Insert a task_result fingerprint.
    _insert_task_result(db, task_id, agent, session_id, status="completed")

    # Mock orchestrator with real db.
    mock_orch = MagicMock()
    mock_orch._db = db

    with patch(
        "runtime.orchestrator.run_step._consume_completion_report"
    ) as mock_consume:
        _sweep_org_zombies(
            db, now=_now(), uptime=999, warm_up_seconds=30,
            orchestrator=mock_orch,
        )
        # The fingerprint should be consumed immediately (no TTL wait).
        mock_consume.assert_called_once()

    # Flag must be cleared.
    t = db.get_task(task_id)
    assert t.zombie_flagged_at is None, (
        "flag should be cleared on fingerprint recovery"
    )

    # zombie_cleared audit must be emitted.
    actions = [r["action"] for r in db.get_audit_logs(task_id)]
    assert "zombie_cleared" in actions, (
        "zombie_cleared audit row must be present"
    )


# ---------------------------------------------------------------------------
# FIX 2 (was FIX 1): parent-wake on Tier-2 zombie cancel
# ---------------------------------------------------------------------------

def test_parent_woken_when_zombie_child_cancelled(db: Database):
    """A delegated parent parked on a zombie child is enqueued/woken when the
    reaper cancels the child (FIX 1 — code_reviewer HIGH)."""
    parent_id = "T-PARENT"
    child_id = "T-CHILD"

    # Create parent in in_progress(delegated), waiting on child.
    db.insert_task(TaskRecord(
        id=parent_id, brief="parent", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task(parent_id, block_kind=BlockKind.DELEGATED)

    # Create zombie child with parent_task_id at insert time.
    flag_time = _ago(FLAG_TTL_NO_FINGERPRINT_SECONDS + 5)
    db.insert_task(TaskRecord(
        id=child_id, brief="zombie child", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        parent_task_id=parent_id,
    ))
    db.update_task(
        child_id,
        current_session_id="sess-dead",
        last_heartbeat=_stale_hb().isoformat(),
        executor_pid=ZOMBIE_PID,
    )
    db.update_task(child_id, zombie_flagged_at=flag_time.isoformat())

    # Create a mock orchestrator with access to the real DB.
    mock_orch = MagicMock()
    mock_orch._db = db

    with patch(
        "runtime.orchestrator.run_step._enqueue_parent_if_waiting"
    ) as mock_enqueue:
        _sweep_org_zombies(db, now=_now(), uptime=999, warm_up_seconds=30,
                           orchestrator=mock_orch)
        # _enqueue_parent_if_waiting should have been called for the child.
        mock_enqueue.assert_called_once_with(mock_orch, child_id)

    # Child should be cancelled.
    t = db.get_task(child_id)
    assert t.status == TaskStatus.CANCELLED
    assert t.cancelled_at is not None
    assert t.completed_at is not None
    assert t.block_kind is None
    assert t.note == "zombie reaped: session died without completing"


# ---------------------------------------------------------------------------
# FIX 2: double-deserialize of waiting_on_job_ids
# ---------------------------------------------------------------------------

def test_fingerprint_with_waiting_on_job_ids_no_typeerror(db: Database):
    """A fingerprinted report with non-empty waiting_on_job_ids is consumed
    via _consume_completion_report without raising TypeError (FIX 2 —
    code_reviewer MEDIUM: get_latest_task_result already deserializes)."""
    task_id = "T-FP"
    agent = "dev_agent"
    session_id = "sess-fp"
    job_ids = ["job-1", "job-2"]

    # Insert a zombie candidate.
    _insert_zombie_candidate(db, task_id,
                             last_heartbeat=_stale_hb(),
                             executor_pid=ZOMBIE_PID,
                             current_session_id=session_id,
                             assigned_agent=agent)

    # Insert a task_result with non-empty waiting_on_job_ids.
    db.insert_task_result(
        task_id=task_id, agent=agent, session_id=session_id,
        status="blocked", confidence_score=90,
        output_summary="waiting on jobs",
        waiting_on_job_ids=job_ids,
    )

    # Fetch the fingerprint — get_latest_task_result already deserializes.
    fingerprint = db.get_latest_task_result(task_id, agent, session_id)
    assert fingerprint is not None
    assert fingerprint["waiting_on_job_ids"] == job_ids  # already a list

    # Create a mock orchestrator and mock _consume_completion_report.
    mock_orch = MagicMock()
    mock_orch._db = db
    task = db.get_task(task_id)

    with patch(
        "runtime.orchestrator.run_step._consume_completion_report"
    ) as mock_consume:
        # This must NOT raise TypeError.
        _consume_zombie_fingerprint(db, task_id, fingerprint, task, mock_orch)
        mock_consume.assert_called_once()
        # Verify the CompletionReport has the correct waiting_on_job_ids.
        called_report = mock_consume.call_args[0][2]
        assert called_report.waiting_on_job_ids == job_ids
