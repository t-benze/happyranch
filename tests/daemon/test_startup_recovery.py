from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _escalate_in_flight_tasks
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


def test_escalate_in_flight_tasks_marks_them_escalated(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)
    db.insert_task(TaskRecord(id="TASK-002", type=TaskType.GENERAL, brief="y"))
    db.update_task("TASK-002", status=TaskStatus.APPROVED)

    _escalate_in_flight_tasks(db)

    assert db.get_task("TASK-001").status == TaskStatus.ESCALATED
    assert db.get_task("TASK-002").status == TaskStatus.APPROVED


def test_escalate_in_flight_tasks_logs_audit(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)

    _escalate_in_flight_tasks(db)

    logs = db.get_audit_logs("TASK-001")
    assert any(
        log["action"] == "escalation"
        and "daemon restarted" in (log["payload"] or {}).get("reason", "")
        for log in logs
    )
