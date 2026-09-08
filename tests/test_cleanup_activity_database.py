import pytest

from runtime.daemon.cleanup_activity import (
    CleanupActivityInput,
    CleanupMeasurement,
)


def _receipt() -> CleanupActivityInput:
    unavailable = CleanupMeasurement(False, None, None, "not_measured")
    return CleanupActivityInput(
        1, "report_only", "completed", unavailable, unavailable,
        0, 0, 0, None, None, "ledger_unavailable",
    )


def test_cleanup_completion_commits_result_and_audit_together(db) -> None:
    db.insert_audit_log(
        "TASK-001", "dev_agent", "workspace_cleanup_triggered",
        {"brief_kind": "report_only", "run_number": 1},
    )
    context = db.get_cleanup_trigger_context("TASK-001", "dev_agent")
    assert context is not None

    db.insert_cleanup_completion(
        task_id="TASK-001", agent="dev_agent", session_id="sess-cleanup",
        output_summary="receipt", confidence_score=80,
        cleanup_activity=_receipt(), trigger_context=context,
    )

    assert len(db.get_task_results("TASK-001")) == 1
    rows = db.get_audit_logs("TASK-001")
    completed = [row for row in rows if row["action"] == "workspace_cleanup_completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["task_result_id"] > 0


def test_legacy_insert_task_result_still_commits(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-ordinary",
        output_summary="ordinary", confidence_score=80,
    )
    assert len(db.get_task_results("TASK-001")) == 1


def test_cleanup_completion_rolls_back_result_when_audit_insert_fails(db, monkeypatch) -> None:
    db.insert_audit_log(
        "TASK-001", "dev_agent", "workspace_cleanup_triggered",
        {"brief_kind": "report_only", "run_number": 1},
    )
    context = db.get_cleanup_trigger_context("TASK-001", "dev_agent")
    assert context is not None

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(db, "insert_audit_log_uncommitted", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        db.insert_cleanup_completion(
            task_id="TASK-001", agent="dev_agent", session_id="sess-cleanup",
            output_summary="receipt", confidence_score=80,
            cleanup_activity=_receipt(), trigger_context=context,
        )
    assert db.get_task_results("TASK-001") == []
    assert [row for row in db.get_audit_logs("TASK-001") if row["action"] == "workspace_cleanup_completed"] == []


def test_cleanup_completion_refuses_orphan_receipt_without_grafting(db) -> None:
    db.insert_audit_log(
        "TASK-001", "dev_agent", "workspace_cleanup_triggered",
        {"brief_kind": "report_only", "run_number": 1},
    )
    db.insert_audit_log(
        "TASK-001", "dev_agent", "workspace_cleanup_completed", {"corrupt": True},
    )
    context = db.get_cleanup_trigger_context("TASK-001", "dev_agent")
    assert context is not None
    with pytest.raises(RuntimeError, match="cleanup_receipt_already_present"):
        db.insert_cleanup_completion(
            task_id="TASK-001", agent="dev_agent", session_id="sess-cleanup",
            output_summary="receipt", confidence_score=80,
            cleanup_activity=_receipt(), trigger_context=context,
        )
    assert db.get_task_results("TASK-001") == []
