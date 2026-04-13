from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType


def test_init_creates_tables(db):
    tables = db.list_tables()
    assert "tasks" in tables
    assert "audit_log" in tables
    assert "scorecards" in tables
    assert "task_results" in tables


def test_insert_and_get_task(db):
    task = TaskRecord(
        id="TASK-001",
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    db.insert_task(task)
    retrieved = db.get_task("TASK-001")
    assert retrieved is not None
    assert retrieved.id == "TASK-001"
    assert retrieved.type == TaskType.IMPLEMENT_FEATURE
    assert retrieved.brief == "Add Alipay support"
    assert retrieved.status == TaskStatus.PENDING


def test_get_nonexistent_task_returns_none(db):
    assert db.get_task("TASK-999") is None


def test_update_task_status(db):
    task = TaskRecord(
        id="TASK-002",
        type=TaskType.BUG_FIX,
        brief="Fix broken links",
    )
    db.insert_task(task)
    db.update_task("TASK-002", status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent")
    retrieved = db.get_task("TASK-002")
    assert retrieved.status == TaskStatus.IN_PROGRESS
    assert retrieved.assigned_agent == "dev_agent"


def test_increment_revision_count(db):
    task = TaskRecord(
        id="TASK-003",
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Refactor auth",
    )
    db.insert_task(task)
    db.increment_revision_count("TASK-003")
    retrieved = db.get_task("TASK-003")
    assert retrieved.revision_count == 1
    db.increment_revision_count("TASK-003")
    retrieved = db.get_task("TASK-003")
    assert retrieved.revision_count == 2


def test_insert_audit_log(db):
    db.insert_audit_log(
        task_id="TASK-001",
        agent="dev_agent",
        action="session_start",
        payload={"workspace": "/tmp/dev_agent"},
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["agent"] == "dev_agent"
    assert logs[0]["action"] == "session_start"


def test_insert_task_result(db):
    db.insert_task_result(
        task_id="TASK-001",
        agent="dev_agent",
        session_id="sess-abc",
        output_summary="Implemented feature",
        confidence_score=85,
        risks_flagged=["sandbox mismatch"],
        duration_seconds=120,
        token_count=5000,
        estimated_cost=0.15,
    )
    results = db.get_task_results("TASK-001")
    assert len(results) == 1
    assert results[0]["confidence_score"] == 85
    assert results[0]["duration_seconds"] == 120


def test_insert_and_get_scorecard(db):
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.92,
        revision_rate=0.08,
        error_count=1,
        tier="green",
    )
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard is not None
    assert scorecard["acceptance_rate"] == 0.92
    assert scorecard["tier"] == "green"


def test_upsert_scorecard_updates_existing(db):
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.92,
        revision_rate=0.08,
        error_count=1,
        tier="green",
    )
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.70,
        revision_rate=0.30,
        error_count=5,
        tier="yellow",
    )
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard["acceptance_rate"] == 0.70
    assert scorecard["tier"] == "yellow"


def test_next_task_id(db):
    assert db.next_task_id() == "TASK-001"
    task = TaskRecord(id="TASK-001", type=TaskType.BUG_FIX, brief="test")
    db.insert_task(task)
    assert db.next_task_id() == "TASK-002"
