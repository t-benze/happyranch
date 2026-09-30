from __future__ import annotations

from datetime import datetime, timedelta, timezone

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import (
    DreamRecord,
    DreamStatus,
    TaskRecord,
    TaskStatus,
    ThreadInvocation,
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


def _thread_start(
    db: Database,
    *,
    seq: int,
    purpose: ThreadInvocationPurpose = ThreadInvocationPurpose.REPLY,
    session_id: str | None = None,
    executor: str | None = "codex",
    model: str | None = "gpt-5",
    started_at: datetime,
) -> ThreadInvocation:
    invocation = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="dev_agent",
        triggering_seq=seq,
        purpose=purpose,
    )
    db.stamp_invocation_started(
        invocation.invocation_token,
        session_id=session_id or f"thread-session-{seq}",
        executor=executor,
        model=model,
    )
    with db._lock:
        db._conn.execute(
            "UPDATE thread_invocations SET started_at=? WHERE invocation_token=?",
            (started_at.isoformat(), invocation.invocation_token),
        )
        db._conn.commit()
    return invocation


def _thread_usage(
    db: Database, *, session_id: str, when: datetime, value: int = 10,
) -> None:
    db.insert_session_token_usage(
        task_id=None,
        agent="dev_agent",
        session_id=session_id,
        executor="codex",
        token_usage=_usage(value),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
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


def test_workload_pairs_single_segments_and_keeps_trailing_start_missing(db: Database) -> None:
    _task_start(db, task_id="TASK-SEG", session_id="first", when=NOW - timedelta(hours=4))
    _task_end(db, task_id="TASK-SEG", when=NOW - timedelta(hours=3), duration=3600)
    _task_start(db, task_id="TASK-SEG", session_id="trailing", when=NOW - timedelta(hours=2))

    row = read_workload(db, now=NOW, timezone_name="UTC")["agents"][0]["current"]

    assert row["task_runs"] == 2
    assert row["recorded_runtime"] == {"seconds": 3600, "known": 1, "total": 2}


def test_reply_is_timed_by_consumed_at_even_when_wake_started_before_windows(db: Database) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="usage"))
    invocation = _thread_start(
        db,
        seq=1,
        started_at=NOW - timedelta(days=15),
    )
    assert db.consume_invocation(invocation.invocation_token)
    with db._lock:
        db._conn.execute(
            "UPDATE thread_invocations SET consumed_at=? WHERE invocation_token=?",
            ((NOW - timedelta(hours=1)).isoformat(), invocation.invocation_token),
        )
        db._conn.commit()

    row = read_workload(db, now=NOW, timezone_name="UTC")["agents"][0]["current"]

    assert row["thread_wakes"] == 0
    assert row["replies"] == 1


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
    _task_start(
        db,
        task_id="TASK-PREVIOUS-ONLY",
        session_id="previous-only",
        when=NOW - timedelta(days=8),
        executor="claude",
        model="sonnet",
    )

    options = read_efficiency(db, now=NOW, timezone_name="UTC")
    assert options["cohorts"] == [
        {"executor": "codex", "model": None, "model_unpinned": True, "current_runs": 1, "previous_runs": 0},
        {"executor": "codex", "model": "gpt-5", "model_unpinned": False, "current_runs": 2, "previous_runs": 0},
    ]

    compared_options = read_efficiency(db, now=NOW, timezone_name="UTC", compare=True)
    assert compared_options["cohorts"][0] == {
        "executor": "claude", "model": "sonnet", "model_unpinned": False,
        "current_runs": 0, "previous_runs": 1,
    }

    selected = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    worker = next(row for row in selected["rows"] if row["run_type"] == "worker_task")
    assert worker["current"]["runs"] == 2
    assert worker["current"]["usage_coverage"] == {"known": 1, "total": 2, "ratio": 0.5}

    # An inner join would erase TASK-MISSING and falsely report 1/1 = 100%.
    assert worker["current"]["usage_coverage"]["known"] == 1
    assert worker["current"]["usage_coverage"]["total"] == 2


def test_all_run_types_use_lifecycle_cohorts_not_usage_or_current_config(db: Database) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="usage"))
    _task_start(
        db, task_id="TASK-WORKER", session_id="worker", when=NOW - timedelta(hours=6),
    )
    _task_start(
        db, task_id="TASK-MANAGER", session_id="manager", when=NOW - timedelta(hours=5),
        purpose="manager_decision",
    )
    reply = _thread_start(
        db, seq=1, session_id="reply", started_at=NOW - timedelta(hours=4),
    )
    followup = _thread_start(
        db, seq=2, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
        session_id="followup", started_at=NOW - timedelta(hours=3),
    )
    dream_at = NOW - timedelta(hours=2)
    db.insert_dream(DreamRecord(
        id="DREAM-ALL", agent_name="dev_agent", local_date="2026-03-10",
        scheduled_for=dream_at, window_end=dream_at, started_at=dream_at,
        status=DreamStatus.COMPLETED, session_id=None, created_at=dream_at,
    ))
    AuditLogger(db).log_dream_started(
        "DREAM-ALL", "dev_agent", executor="codex", model="gpt-5",
    )
    _move_latest_audit(db, dream_at)
    # Usage/provider metadata deliberately says something else. Cohort identity
    # remains the lifecycle tuple captured before any later config change.
    for task_id, session_id in (("TASK-WORKER", "worker"), ("TASK-MANAGER", "manager")):
        _task_usage(db, task_id, session_id, NOW - timedelta(hours=1))
    _thread_usage(db, session_id="reply", when=NOW - timedelta(hours=1))
    _thread_usage(db, session_id="followup", when=NOW - timedelta(hours=1))
    assert reply.invocation_token != followup.invocation_token

    result = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )

    assert {row["run_type"]: row["current"]["runs"] for row in result["rows"]} == {
        "worker_task": 1,
        "manager_decision": 1,
        "thread_reply": 1,
        "thread_followup": 1,
        "dream": 1,
    }


def test_reported_zero_and_absent_classes_remain_distinct(db: Database) -> None:
    for ordinal in (1, 2):
        task_id = f"TASK-CLASS-{ordinal}"
        session_id = f"class-{ordinal}"
        _task_start(
            db, task_id=task_id, session_id=session_id,
            when=NOW - timedelta(hours=ordinal),
        )
        _task_usage(db, task_id, session_id, NOW - timedelta(minutes=ordinal))
    with db._lock:
        db._conn.execute(
            "UPDATE session_token_usage SET cache_creation_tokens=NULL, "
            "cache_read_tokens=NULL WHERE session_id='class-2'",
        )
        db._conn.commit()

    result = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    worker = next(row for row in result["rows"] if row["run_type"] == "worker_task")

    assert worker["current"]["usage_coverage"] == {"known": 2, "total": 2, "ratio": 1.0}
    assert worker["current"]["fresh_input"] == {
        "value": 10, "n_reported": 1, "partial_count": 1,
    }
    assert worker["current"]["reread"] == {
        "value": 10, "n_reported": 1, "partial_count": 0,
    }


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


def test_comparison_new_from_zero_even_median_and_unattributed_suppression(db: Database) -> None:
    for ordinal, value in enumerate((10, 20), start=1):
        task_id = f"TASK-CURRENT-{ordinal}"
        session_id = f"current-{ordinal}"
        _task_start(
            db, task_id=task_id, session_id=session_id,
            when=NOW - timedelta(hours=ordinal),
        )
        _task_usage(db, task_id, session_id, NOW - timedelta(minutes=ordinal))
        with db._lock:
            db._conn.execute(
                "UPDATE session_token_usage SET input_tokens=?, output_tokens=? "
                "WHERE id=(SELECT MAX(id) FROM session_token_usage)",
                (value, value),
            )
            db._conn.commit()

    result = read_efficiency(
        db, now=NOW, timezone_name="UTC", compare=True,
        executor="codex", model="gpt-5",
    )
    worker = next(row for row in result["rows"] if row["run_type"] == "worker_task")
    assert worker["current"]["fresh_input"] == {
        "value": 15.0, "n_reported": 2, "partial_count": 0,
    }
    assert worker["deltas"]["runs"] == {
        "kind": "new_from_zero", "value": 2, "withheld_reason": None,
    }
    assert worker["deltas"]["fresh_input"]["withheld_reason"] == "invalid_baseline"

    _task_start(
        db, task_id="TASK-LEGACY", session_id="legacy",
        when=NOW - timedelta(days=8), executor=None,
    )
    result = read_efficiency(
        db, now=NOW, timezone_name="UTC", compare=True,
        executor="codex", model="gpt-5",
    )
    worker = next(row for row in result["rows"] if row["run_type"] == "worker_task")
    assert worker["deltas"]["runs"]["withheld_reason"] == "unattributed_lifecycle_runs"


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


def test_rolling_window_edges_are_half_open_for_both_periods(db: Database) -> None:
    for task_id, session_id, when in (
        ("TASK-PREV-START", "prev-start", NOW - timedelta(days=14)),
        ("TASK-CURRENT-START", "current-start", NOW - timedelta(days=7)),
        ("TASK-END", "end", NOW),
        ("TASK-BEFORE", "before", NOW - timedelta(days=14, microseconds=1)),
    ):
        _task_start(db, task_id=task_id, session_id=session_id, when=when)

    row = read_workload(
        db, now=NOW, timezone_name="America/New_York", compare=True,
    )["agents"][0]

    assert row["current"]["task_runs"] == 1
    assert row["previous"]["task_runs"] == 1


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


def test_every_non_delivery_kind_is_excluded(db: Database) -> None:
    cases = (
        ("manager-decision", "manager_decision", TaskStatus.COMPLETED, "completed"),
        ("child-callback", "unattributed", TaskStatus.COMPLETED, "completed"),
        ("followup", "unattributed", TaskStatus.IN_PROGRESS, "completed"),
        ("decline", "worker_execution", TaskStatus.COMPLETED, "failed"),
        ("retry-failure", "worker_execution", TaskStatus.FAILED, "completed"),
        ("blocked", "worker_execution", TaskStatus.IN_PROGRESS, "completed"),
        ("cancelled", "worker_execution", TaskStatus.CANCELLED, "completed"),
    )
    for label, purpose, task_status, result_status in cases:
        task_id = f"TASK-EXCLUDED-{label.upper()}"
        session_id = f"excluded-{label}"
        _completed_task(db, task_id, task_status=task_status)
        _task_start(
            db, task_id=task_id, session_id=session_id,
            when=NOW - timedelta(hours=3), purpose=purpose,
        )
        db.insert_task_result(
            task_id=task_id, agent="dev_agent", session_id=session_id,
            output_summary="not a delivery", confidence_score=0, status=result_status,
        )
        _move_latest_result(db, NOW - timedelta(hours=2))

    row = read_workload(db, now=NOW, timezone_name="UTC")["agents"][0]["current"]
    assert row["deliveries"] == 0


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
    for seq, reason in enumerate(
        ("participant_removed", "agent_terminated", "agent_unavailable"), start=2,
    ):
        system = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="dev_agent", triggering_seq=seq,
            purpose=ThreadInvocationPurpose.REPLY,
        )
        db.stamp_invocation_started(
            system.invocation_token, session_id=f"runtime-system-{seq}",
            executor="codex", model="gpt-5",
        )
        db.mark_invocation_declined(system.invocation_token, decline_reason=reason)
    missing_decline = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent", triggering_seq=5,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(
        missing_decline.invocation_token, session_id="runtime-missing", executor="codex", model="gpt-5",
    )
    db.mark_invocation_declined(missing_decline.invocation_token, decline_reason="not relevant")
    system_failures = (
        (6, ThreadInvocationStatus.FAILED, "daemon_restart"),
        (7, ThreadInvocationStatus.FAILED, "coalesced_cutover"),
        (8, ThreadInvocationStatus.FAILED, "archive_started"),
        (9, ThreadInvocationStatus.FAILED, "founder_aborted"),
        (10, ThreadInvocationStatus.TIMEOUT, "timeout"),
    )
    for seq, status, reason in system_failures:
        failed = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="dev_agent", triggering_seq=seq,
            purpose=ThreadInvocationPurpose.REPLY,
        )
        db.stamp_invocation_started(
            failed.invocation_token, session_id=f"runtime-{seq}", executor="codex", model="gpt-5",
        )
        db.fail_invocation(failed.invocation_token, status=status, decline_reason=reason)
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
    assert row["current"]["runs"] == 10
    assert row["current"]["usage_coverage"] == {"known": 1, "total": 10, "ratio": 0.1}
    assert row["current"]["decline_waste"]["declined"] == 2
    assert row["current"]["decline_waste"]["usage_known"] == 1
    assert row["current"]["decline_waste"]["fresh_input"] == {"value": 7, "n_reported": 1}


def test_thread_runtime_replies_normal_join_and_no_declines(db: Database) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="usage"))
    replied = _thread_start(
        db, seq=1, session_id="normal-runtime", started_at=NOW - timedelta(hours=4),
    )
    assert db.consume_invocation(replied.invocation_token)
    declined = _thread_start(db, seq=2, started_at=NOW - timedelta(hours=3))
    assert db.mark_invocation_declined(declined.invocation_token, decline_reason="no thanks")
    failed = _thread_start(db, seq=3, started_at=NOW - timedelta(hours=2))
    assert db.fail_invocation(
        failed.invocation_token,
        status=ThreadInvocationStatus.FAILED,
        decline_reason="provider failure",
    )
    with db._lock:
        db._conn.execute(
            "UPDATE thread_invocations SET consumed_at=? WHERE invocation_token=?",
            ((NOW - timedelta(hours=1)).isoformat(), replied.invocation_token),
        )
        db._conn.commit()
    _thread_usage(db, session_id="normal-runtime", when=NOW - timedelta(minutes=30), value=9)

    workload = read_workload(db, now=NOW, timezone_name="UTC")["agents"][0]["current"]
    efficiency = read_efficiency(
        db, now=NOW, timezone_name="UTC", compare=True,
        executor="codex", model="gpt-5",
    )
    reply = next(row for row in efficiency["rows"] if row["run_type"] == "thread_reply")

    assert workload["thread_wakes"] == 3
    assert workload["replies"] == 1
    assert workload["recorded_runtime"]["known"] == 3
    assert reply["current"]["usage_coverage"] == {"known": 1, "total": 3, "ratio": 1 / 3}
    assert reply["current"]["decline_waste"]["declined"] == 1

    followup = _thread_start(
        db, seq=4, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
        started_at=NOW - timedelta(minutes=20),
    )
    assert db.consume_invocation(followup.invocation_token)
    efficiency = read_efficiency(
        db, now=NOW, timezone_name="UTC", executor="codex", model="gpt-5",
    )
    followup_row = next(
        row for row in efficiency["rows"] if row["run_type"] == "thread_followup"
    )
    assert followup_row["current"]["decline_waste"]["state"] == "no_declines"


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
        db, now=NOW, timezone_name="UTC", compare=True,
        executor="codex", model="gpt-5",
    )
    assert workload["agents"][0]["current"]["task_runs"] == 1
    assert efficiency["unattributed"]["current"]["recovery"] == 1
    assert all(row["current"]["runs"] == 0 for row in efficiency["rows"])
    assert all(row["deltas"]["runs"]["kind"] == "no_change" for row in efficiency["rows"])
