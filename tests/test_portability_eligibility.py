"""Tests for the pure portability eligibility + zombie predicate (THR-187 Slice A)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from runtime.portability.eligibility import (
    STALE_HEARTBEAT_SECONDS,
    Eligibility,
    TaskLiveness,
    compute_eligibility,
    is_true_zombie,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stale() -> datetime:
    return _now() - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 10)


def _fresh() -> datetime:
    return _now()


DEAD_PID = 99999
LIVE_PID = 1234


def _pid_is_dead(pid: int) -> bool:
    return pid == DEAD_PID


def _task(task_id: str, status: str, *, block_kind: str | None = None,
          hb: datetime | None = None, pid: int | None = None,
          agent: str | None = "dev_agent") -> TaskLiveness:
    return TaskLiveness(
        task_id=task_id, status=status, block_kind=block_kind,
        last_heartbeat=hb, executor_pid=pid, assigned_agent=agent,
    )


def _compute(*, tasks=None, sessions=0, queued=0, invocations=0, jobs=None,
             dreams=None, work_hours=None, schedules=None) -> Eligibility:
    return compute_eligibility(
        tasks=tasks or [],
        active_session_count=sessions,
        queued_for_org=queued,
        pending_invocation_count=invocations,
        active_job_ids=jobs or [],
        active_dream_ids=dreams or [],
        active_work_hour_ids=work_hours or [],
        active_schedule_ids=schedules or [],
        now=_now(),
        pid_is_dead=_pid_is_dead,
    )


def test_empty_inputs_eligible() -> None:
    result = _compute()
    assert result.eligible is True
    assert result.blockers() == {
        "tasks": [], "active_sessions": 0, "queued_items": 0,
        "pending_thread_invocations": 0, "active_jobs": [],
        "active_dreams": [], "active_work_hours": [], "active_schedules": [],
    }
    assert result.possible_zombies == []


def test_each_nonterminal_status_is_blocker() -> None:
    for status in ("pending", "in_progress", "escalated"):
        result = _compute(tasks=[_task("T-1", status)])
        assert result.eligible is False, status
        assert result.tasks == ["T-1"], status


def test_terminal_statuses_are_not_blockers() -> None:
    for status in ("completed", "failed", "cancelled", "superseded"):
        result = _compute(tasks=[_task("T-1", status)])
        assert result.eligible is True, status
        assert result.tasks == [], status


def test_in_progress_block_kinds_are_blockers_but_not_zombies() -> None:
    for block_kind in ("delegated", "blocked_on_job"):
        result = _compute(tasks=[_task("T-1", "in_progress", block_kind=block_kind,
                                      hb=_stale(), pid=DEAD_PID)])
        assert result.eligible is False
        assert result.tasks == ["T-1"]
        # parked tasks are never possible zombies merely because old
        assert result.possible_zombies == []


def test_active_session_count_is_blocker() -> None:
    assert _compute(sessions=1).eligible is False
    assert _compute(sessions=1).active_session_count == 1


def test_queued_for_org_is_blocker() -> None:
    assert _compute(queued=1).eligible is False


def test_pending_invocation_is_blocker() -> None:
    assert _compute(invocations=1).eligible is False


def test_active_jobs_block() -> None:
    assert _compute(jobs=["JOB-1"]).eligible is False


def test_active_dreams_block() -> None:
    assert _compute(dreams=["DREAM-1"]).eligible is False


def test_active_work_hours_block() -> None:
    assert _compute(work_hours=["WORKHOUR-1"]).eligible is False


def test_active_schedules_block() -> None:
    assert _compute(schedules=["SCHEDULE-1"]).eligible is False


def test_possible_zombie_reported_but_not_resolved() -> None:
    result = _compute(tasks=[_task("T-Z", "in_progress", hb=_stale(), pid=DEAD_PID)])
    assert result.eligible is False
    assert [z.task_id for z in result.possible_zombies] == ["T-Z"]


def test_fresh_heartbeat_not_possible_zombie() -> None:
    result = _compute(tasks=[_task("T-1", "in_progress", hb=_fresh(), pid=DEAD_PID)])
    assert result.possible_zombies == []
    assert result.eligible is False  # still in_progress → blocker


def test_live_pid_not_possible_zombie() -> None:
    result = _compute(tasks=[_task("T-1", "in_progress", hb=_stale(), pid=LIVE_PID)])
    assert result.possible_zombies == []


def test_missing_heartbeat_not_possible_zombie() -> None:
    result = _compute(tasks=[_task("T-1", "in_progress", hb=None, pid=DEAD_PID)])
    assert result.possible_zombies == []


def test_missing_pid_not_possible_zombie() -> None:
    result = _compute(tasks=[_task("T-1", "in_progress", hb=_stale(), pid=None)])
    assert result.possible_zombies == []


# ── is_true_zombie (reconcile gate) ──────────────────────────────────────────

def _true_zombie(**overrides) -> tuple[bool, str | None]:
    kw = dict(status="in_progress", block_kind=None, last_heartbeat=_stale(),
              executor_pid=DEAD_PID, now=_now(), pid_is_dead=_pid_is_dead)
    kw.update(overrides)
    return is_true_zombie(**kw)


def test_true_zombie_positive() -> None:
    ok, reason = _true_zombie()
    assert ok is True
    assert reason is None


def test_terminal_status_not_zombie() -> None:
    for status in ("pending", "escalated", "completed", "failed", "cancelled"):
        ok, reason = _true_zombie(status=status)
        assert ok is False, status
        assert "in_progress" in reason


def test_blocked_kind_not_zombie() -> None:
    for block_kind in ("delegated", "blocked_on_job"):
        ok, reason = _true_zombie(block_kind=block_kind)
        assert ok is False, block_kind
        assert "never zombies" in reason


def test_fresh_heartbeat_not_zombie() -> None:
    ok, reason = _true_zombie(last_heartbeat=_fresh())
    assert ok is False
    assert "fresh" in reason


def test_live_pid_not_zombie() -> None:
    ok, reason = _true_zombie(executor_pid=LIVE_PID)
    assert ok is False
    assert "alive" in reason


def test_missing_heartbeat_not_zombie() -> None:
    ok, reason = _true_zombie(last_heartbeat=None)
    assert ok is False
    assert "last_heartbeat" in reason


def test_missing_pid_not_zombie() -> None:
    ok, reason = _true_zombie(executor_pid=None)
    assert ok is False
    assert "executor_pid" in reason
