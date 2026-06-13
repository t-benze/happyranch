from __future__ import annotations

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import TokenUsage


def test_audit_methods_write_workhour_scope_id(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite")
    audit = AuditLogger(db)

    audit.log_work_hour_scheduled(
        "WORKHOUR-001", "dev_agent", local_date="2026-06-11", slot="09:00", mode="windowed",
    )
    audit.log_work_hour_started("WORKHOUR-001", "dev_agent")
    audit.log_work_hour_spawned("WORKHOUR-001", "dev_agent", task_ids=["TASK-301", "TASK-302"])
    audit.log_work_hour_completed(
        "WORKHOUR-001", "dev_agent", spawned_task_count=2, routine_count=2,
    )

    scheduled = db.get_audit_logs_by_action("work_hour_scheduled")
    assert len(scheduled) == 1
    # The established generic-scope-id overload: task_id column stores WORKHOUR-NNN.
    assert scheduled[0]["task_id"] == "WORKHOUR-001"
    assert scheduled[0]["payload"] == {
        "local_date": "2026-06-11", "slot": "09:00", "mode": "windowed",
    }

    spawned = db.get_audit_logs_by_action("work_hour_spawned")
    assert spawned[0]["payload"]["task_ids"] == ["TASK-301", "TASK-302"]
    assert spawned[0]["payload"]["spawned_task_count"] == 2


def test_failed_and_timeout_audit_actions_are_distinct(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite")
    audit = AuditLogger(db)
    audit.log_work_hour_failed("WORKHOUR-002", "dev_agent", reason="no_callback")
    audit.log_work_hour_timeout("WORKHOUR-003", "dev_agent", reason="timed out")

    assert db.get_audit_logs_by_action("work_hour_failed")[0]["task_id"] == "WORKHOUR-002"
    assert db.get_audit_logs_by_action("work_hour_timeout")[0]["task_id"] == "WORKHOUR-003"


def test_token_usage_uses_work_hour_scope_without_overloading_task_id(tmp_path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.insert_session_token_usage(
        task_id=None,
        agent="dev_agent",
        session_id="sess-wh-1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=100, output_tokens=40, model="claude-opus"),
        scope_type="work_hour",
        scope_id="WORKHOUR-001",
    )

    rows = db.list_session_token_usage(scope_type="work_hour")
    assert len(rows) == 1
    assert rows[0]["scope_type"] == "work_hour"
    assert rows[0]["scope_id"] == "WORKHOUR-001"
    # task_id is NOT overloaded for the work-hour scope.
    assert rows[0]["task_id"] is None

    rollup = db.aggregate_session_token_usage_by_scope()
    wh = [r for r in rollup if r["scope_type"] == "work_hour"]
    assert len(wh) == 1
    assert wh[0]["scope_id"] == "WORKHOUR-001"
    assert wh[0]["total_tokens"] == 140
