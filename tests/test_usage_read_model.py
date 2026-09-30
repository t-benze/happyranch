from __future__ import annotations

from datetime import datetime, timedelta, timezone

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import (
    DreamRecord,
    DreamStatus,
    TaskRecord,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadRecord,
    TokenUsage,
)
from runtime.orchestrator.usage_read_model import read_efficiency, read_workload


NOW = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)


def _move_latest_audit(db: Database, when: datetime) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE audit_log SET timestamp=? WHERE id=(SELECT MAX(id) FROM audit_log)",
            (when.isoformat(),),
        )
        db._conn.commit()


def _move_latest_usage(db: Database, when: datetime) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE session_token_usage SET created_at=? "
            "WHERE id=(SELECT MAX(id) FROM session_token_usage)",
            (when.isoformat(),),
        )
        db._conn.commit()


def _usage(value: int = 10) -> TokenUsage:
    return TokenUsage(
        input_tokens=value,
        output_tokens=value,
        cache_read_tokens=value,
        cache_creation_tokens=0,
        reasoning_tokens=0,
        model="provider-model-must-not-attribute",
    )


def _task_start(
    db: Database,
    *,
    task_id: str,
    session_id: str,
    when: datetime,
    purpose: str = "worker_execution",
    executor: str | None = "codex",
    model: str | None = "gpt-5",
) -> None:
    AuditLogger(db).log_session_start(
        task_id,
        "dev_agent",
        "/workspace",
        session_id=session_id,
        invocation_purpose=purpose,
        executor=executor,
        model=model,
    )
    _move_latest_audit(db, when)


def _task_end(db: Database, *, task_id: str, when: datetime, duration: int) -> None:
    AuditLogger(db).log_session_end(task_id, "dev_agent", duration)
    _move_latest_audit(db, when)


def _task_usage(db: Database, task_id: str, session_id: str, when: datetime) -> None:
    db.insert_session_token_usage(
        task_id=task_id,
        agent="dev_agent",
        session_id=session_id,
        executor="codex",
        token_usage=_usage(),
        scope_type="task",
        scope_id=task_id,
    )
    _move_latest_usage(db, when)


def test_workload_runtime_pairing_bounds_and_left_join_coverage(db: Database) -> None:
    # Exact lower bound is included and exact upper bound is excluded.
    _task_start(db, task_id="TASK-LOWER", session_id="lower", when=NOW - timedelta(days=7))
    _task_start(db, task_id="TASK-LOWER", session_id="lower", when=NOW - timedelta(days=7) + timedelta(seconds=1))
    _task_end(db, task_id="TASK-LOWER", when=NOW - timedelta(days=7) + timedelta(minutes=1), duration=60)
    _task_usage(db, "TASK-LOWER", "lower", NOW - timedelta(days=7) + timedelta(minutes=1))

    # A segment with two starts before one end leaves both runtimes missing.
    _task_start(db, task_id="TASK-AMB", session_id="a", when=NOW - timedelta(days=2))
    _task_start(db, task_id="TASK-AMB", session_id="b", when=NOW - timedelta(days=2) + timedelta(seconds=1))
    _task_end(db, task_id="TASK-AMB", when=NOW - timedelta(days=2) + timedelta(seconds=2), duration=99)
    _task_usage(db, "TASK-AMB", "a", NOW - timedelta(days=2) + timedelta(seconds=3))

    # Orphan and upper-bound starts remain lifecycle runs without usage/runtime.
    _task_start(db, task_id="TASK-ORPHAN", session_id="orphan", when=NOW - timedelta(days=1))
    _task_start(db, task_id="TASK-UPPER", session_id="upper", when=NOW)

    # A start before the queried windows still makes its later segment
    # ambiguous under the audit-id pairing rule.
    _task_start(db, task_id="TASK-CROSS-BOUND", session_id="old", when=NOW - timedelta(days=15))
    _task_start(db, task_id="TASK-CROSS-BOUND", session_id="current", when=NOW - timedelta(hours=3))
    _task_end(db, task_id="TASK-CROSS-BOUND", when=NOW - timedelta(hours=2), duration=3600)

    result = read_workload(db, now=NOW, timezone_name="America/New_York", compare=True)
    row = next(item for item in result["agents"] if item["agent"] == "dev_agent")

    assert result["current_window"]["start_utc"] == "2026-03-03T12:00:00Z"
    assert result["current_window"]["end_utc"] == "2026-03-10T12:00:00Z"
    assert result["timezone"] == "America/New_York"
    assert row["current"]["task_runs"] == 5
    assert row["current"]["recorded_runtime"] == {
        "seconds": 60,
        "known": 1,
        "total": 5,
    }


def test_dream_join_uses_dream_id_not_session_ids_and_refuses_ambiguity(db: Database) -> None:
    for ordinal in range(3):
        dream_id = f"DREAM-{ordinal + 1:03d}"
        when = NOW - timedelta(days=ordinal + 1)
        db.insert_dream(DreamRecord(
            id=dream_id,
            agent_name="dev_agent",
            local_date=f"2026-03-0{9 - ordinal}",
            scheduled_for=when,
            window_end=when,
            started_at=when,
            status=DreamStatus.COMPLETED,
            session_id=None,  # callback-completed dreams never write this field
            created_at=when,
        ))
        AuditLogger(db).log_dream_started(
            dream_id, "dev_agent", executor="codex", model="gpt-5",
        )
        _move_latest_audit(db, when)

    # Provider identity deliberately differs from every lifecycle identity.
    db.insert_session_token_usage(
        task_id=None, agent="dev_agent", session_id="provider-session-1",
        executor="codex", token_usage=_usage(11), scope_type="dream",
        scope_id="DREAM-001",
    )
    _move_latest_usage(db, NOW - timedelta(hours=20))

    # Multiple matching rows are ambiguous and must not be reported or covered.
    for sid in ("provider-a", "provider-b"):
        db.insert_session_token_usage(
            task_id=None, agent="dev_agent", session_id=sid,
            executor="codex", token_usage=_usage(20), scope_type="dream",
            scope_id="DREAM-003",
        )
        _move_latest_usage(db, NOW - timedelta(hours=18))

    result = read_efficiency(
        db,
        now=NOW,
        timezone_name="UTC",
        executor="codex",
        model="gpt-5",
    )
    dream = next(row for row in result["rows"] if row["run_type"] == "dream")

    assert dream["current"]["runs"] == 3
    assert dream["current"]["usage_coverage"] == {"known": 1, "total": 3, "ratio": 1 / 3}
    assert dream["current"]["fresh_input"] == {"value": 11, "n_reported": 1, "partial_count": 0}


def test_efficiency_keeps_lifecycle_only_runs_and_unpinned_cohort_separate(db: Database) -> None:
    _task_start(db, task_id="TASK-COVERED", session_id="covered", when=NOW - timedelta(days=1))
    _task_usage(db, "TASK-COVERED", "covered", NOW - timedelta(hours=23))
    _task_start(db, task_id="TASK-MISSING", session_id="missing", when=NOW - timedelta(hours=22))
    _task_start(
        db,
        task_id="TASK-UNPINNED",
        session_id="unpinned",
        when=NOW - timedelta(hours=20),
        model=None,
    )
    _task_usage(db, "TASK-UNPINNED", "unpinned", NOW - timedelta(hours=19))

    options = read_efficiency(db, now=NOW, timezone_name="UTC")
    assert options["cohorts"] == [
        {"executor": "codex", "model": None, "model_unpinned": True, "current_runs": 1, "previous_runs": 0},
        {"executor": "codex", "model": "gpt-5", "model_unpinned": False, "current_runs": 2, "previous_runs": 0},
    ]

    selected = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    worker = next(row for row in selected["rows"] if row["run_type"] == "worker_task")
    assert worker["current"]["runs"] == 2
    assert worker["current"]["usage_coverage"] == {"known": 1, "total": 2, "ratio": 0.5}


def test_comparison_zero_run_cases_metric_baselines_and_row_suppression(db: Database) -> None:
    previous = NOW - timedelta(days=8)
    _task_start(db, task_id="TASK-PREV", session_id="prev", when=previous)
    _task_usage(db, "TASK-PREV", "prev", previous + timedelta(minutes=1))

    result = read_efficiency(
        db,
        now=NOW,
        timezone_name="UTC",
        compare=True,
        executor="codex",
        model="gpt-5",
    )
    worker = next(row for row in result["rows"] if row["run_type"] == "worker_task")
    dream = next(row for row in result["rows"] if row["run_type"] == "dream")
    assert worker["deltas"]["runs"] == {"kind": "absolute", "value": -1, "withheld_reason": None}
    assert worker["deltas"]["fresh_input"] == {
        "kind": "withheld", "value": None, "withheld_reason": "invalid_baseline",
    }
    assert dream["deltas"]["runs"] == {"kind": "no_change", "value": 0, "withheld_reason": None}

    # Add an uncovered current run: row-level coverage now suppresses Runs too.
    _task_start(db, task_id="TASK-CUR", session_id="cur", when=NOW - timedelta(hours=1))
    result = read_efficiency(
        db,
        now=NOW,
        timezone_name="UTC",
        compare=True,
        executor="codex",
        model="gpt-5",
    )
    worker = next(row for row in result["rows"] if row["run_type"] == "worker_task")
    assert worker["deltas"]["runs"]["kind"] == "withheld"
    assert worker["deltas"]["runs"]["withheld_reason"] == "usage_coverage_below_95_percent"


def test_rolling_windows_remain_exactly_168_hours_across_dst(db: Database) -> None:
    dst_now = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)
    result = read_workload(
        db, now=dst_now, timezone_name="America/New_York", compare=True,
    )
    current_start = datetime.fromisoformat(result["current_window"]["start_utc"].replace("Z", "+00:00"))
    current_end = datetime.fromisoformat(result["current_window"]["end_utc"].replace("Z", "+00:00"))
    previous_start = datetime.fromisoformat(result["previous_window"]["start_utc"].replace("Z", "+00:00"))
    assert current_end - current_start == timedelta(hours=168)
    assert current_start - previous_start == timedelta(hours=168)


def _move_latest_result(db: Database, when: datetime) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE task_results SET created_at=? WHERE id=(SELECT MAX(id) FROM task_results)",
            (when.isoformat(),),
        )
        db._conn.commit()


def _completed_task(db: Database, task_id: str, *, task_status: TaskStatus = TaskStatus.COMPLETED) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief="fixture",
        assigned_agent="dev_agent",
        status=task_status,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=1),
    ))


def test_deliveries_require_completed_worker_result_and_dedupe_task(db: Database) -> None:
    cases = (
        ("TASK-DELIVERED", "worker_execution", TaskStatus.COMPLETED, "completed"),
        ("TASK-MANAGER", "manager_decision", TaskStatus.COMPLETED, "completed"),
        ("TASK-LEGACY", "unattributed", TaskStatus.COMPLETED, "completed"),
        ("TASK-FAILED-RESULT", "worker_execution", TaskStatus.COMPLETED, "failed"),
        ("TASK-BLOCKED", "worker_execution", TaskStatus.IN_PROGRESS, "completed"),
        ("TASK-CANCELLED", "worker_execution", TaskStatus.CANCELLED, "completed"),
        ("TASK-FAILED", "worker_execution", TaskStatus.FAILED, "completed"),
    )
    for ordinal, (task_id, purpose, task_status, result_status) in enumerate(cases):
        session_id = f"result-{ordinal}"
        _completed_task(db, task_id, task_status=task_status)
        _task_start(
            db, task_id=task_id, session_id=session_id,
            when=NOW - timedelta(hours=3), purpose=purpose,
        )
        db.insert_task_result(
            task_id=task_id, agent="dev_agent", session_id=session_id,
            output_summary="done", confidence_score=90, status=result_status,
        )
        _move_latest_result(db, NOW - timedelta(hours=2))

    # A recovery/repeated callback for the same delivered task does not add a delivery.
    db.insert_task_result(
        task_id="TASK-DELIVERED", agent="dev_agent", session_id="result-0",
        output_summary="duplicate", confidence_score=90, status="completed",
    )
    _move_latest_result(db, NOW - timedelta(hours=1))

    result = read_workload(db, now=NOW, timezone_name="UTC")
    row = next(item for item in result["agents"] if item["agent"] == "dev_agent")
    assert row["current"]["deliveries"] == 1
    assert row["current"]["delivery_unclassified_results"] == 1


def test_thread_fallback_join_and_system_declines_are_not_decline_waste(db: Database) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="usage"))
    own = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(
        own.invocation_token, session_id="runtime-session", executor="codex", model="gpt-5",
    )
    db.mark_invocation_declined(own.invocation_token, decline_reason="not useful")
    system = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent", triggering_seq=2,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(
        system.invocation_token, session_id="runtime-system", executor="codex", model="gpt-5",
    )
    db.mark_invocation_declined(system.invocation_token, decline_reason="participant_removed")
    missing_decline = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent", triggering_seq=3,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(
        missing_decline.invocation_token, session_id="runtime-missing", executor="codex", model="gpt-5",
    )
    db.mark_invocation_declined(missing_decline.invocation_token, decline_reason="not relevant")
    for seq, status in ((4, ThreadInvocationStatus.FAILED), (5, ThreadInvocationStatus.TIMEOUT)):
        failed = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="dev_agent", triggering_seq=seq,
            purpose=ThreadInvocationPurpose.REPLY,
        )
        db.stamp_invocation_started(
            failed.invocation_token, session_id=f"runtime-{seq}", executor="codex", model="gpt-5",
        )
        db.fail_invocation(failed.invocation_token, status=status, decline_reason=status.value)
    with db._lock:
        db._conn.execute(
            "UPDATE thread_invocations SET started_at=?, consumed_at=?",
            ((NOW - timedelta(hours=2)).isoformat(), (NOW - timedelta(hours=1)).isoformat()),
        )
        db._conn.commit()
    # Exercise the documented fallback: provider omitted runtime session_id,
    # so persistence used invocation_token instead.
    db.insert_session_token_usage(
        task_id=None, agent="dev_agent", session_id=own.invocation_token,
        executor="codex", token_usage=_usage(7), scope_type="thread",
        scope_id="THR-001", thread_id="THR-001", invocation_purpose="reply",
    )
    _move_latest_usage(db, NOW - timedelta(minutes=30))

    result = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    row = next(item for item in result["rows"] if item["run_type"] == "thread_reply")
    assert row["current"]["runs"] == 5
    assert row["current"]["usage_coverage"] == {"known": 1, "total": 5, "ratio": 0.2}
    assert row["current"]["decline_waste"]["declined"] == 2
    assert row["current"]["decline_waste"]["usage_known"] == 1
    assert row["current"]["decline_waste"]["fresh_input"] == {"value": 7, "n_reported": 1}


def test_completion_recovery_counts_in_workload_but_not_efficiency_rows(db: Database) -> None:
    db.insert_task(TaskRecord(
        id="TASK-RECOVERY", brief="fixture", assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS, current_session_id="origin",
    ))
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at=(NOW - timedelta(hours=2)).isoformat(), expires_at=NOW.isoformat(),
    )
    _task_start(
        db, task_id="TASK-RECOVERY", session_id="recovery",
        when=NOW - timedelta(hours=1), purpose="unattributed",
    )

    workload = read_workload(db, now=NOW, timezone_name="UTC")
    efficiency = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    assert workload["agents"][0]["current"]["task_runs"] == 1
    assert efficiency["unattributed"]["current"]["recovery"] == 1
    assert all(row["current"]["runs"] == 0 for row in efficiency["rows"])
