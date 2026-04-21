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


def test_list_tasks_empty_returns_empty_list(db):
    assert db.list_tasks() == []


def test_list_tasks_returns_most_recent_first(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.BUG_FIX, brief="Fix it"))
    db.insert_task(TaskRecord(id="TASK-002", type=TaskType.IMPLEMENT_FEATURE, brief="Build it"))
    tasks = db.list_tasks()
    assert len(tasks) == 2
    assert tasks[0].id == "TASK-002"


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


def test_get_latest_task_result_filters_by_session_id(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="early", confidence_score=70,
    )
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-B",
        output_summary="newer", confidence_score=90,
    )
    a = db.get_latest_task_result("TASK-001", "dev_agent", "sess-A")
    assert a is not None
    assert a["output_summary"] == "early"
    b = db.get_latest_task_result("TASK-001", "dev_agent", "sess-B")
    assert b is not None
    assert b["output_summary"] == "newer"


def test_get_latest_task_result_returns_none_when_missing(db) -> None:
    assert db.get_latest_task_result("TASK-X", "dev_agent", "sess-Z") is None


def test_get_latest_task_result_picks_most_recent_in_session(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="first", confidence_score=70,
    )
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="retry", confidence_score=85,
    )
    latest = db.get_latest_task_result("TASK-001", "dev_agent", "sess-A")
    assert latest["output_summary"] == "retry"


def _seed_audit(db) -> None:
    db.insert_audit_log("TASK-001", "dev_agent", "session_start", {"workspace": "/tmp/a"})
    db.insert_audit_log("TASK-001", "dev_agent", "session_end", {"duration_seconds": 30})
    db.insert_audit_log("TASK-002", "engineering_head", "session_start", None)
    db.insert_audit_log("TASK-002", "engineering_head", "escalation", {"reason": "budget"})


def test_query_audit_logs_no_filters_returns_all_ascending(db) -> None:
    _seed_audit(db)
    rows = db.query_audit_logs()
    assert [r["id"] for r in rows] == [1, 2, 3, 4]


def test_query_audit_logs_filters_by_task_id(db) -> None:
    _seed_audit(db)
    rows = db.query_audit_logs(task_id="TASK-001")
    assert {r["task_id"] for r in rows} == {"TASK-001"}
    assert len(rows) == 2


def test_query_audit_logs_filters_by_agent_and_action(db) -> None:
    _seed_audit(db)
    rows = db.query_audit_logs(agent="engineering_head", action="escalation")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"reason": "budget"}


def test_query_audit_logs_limit_returns_most_recent_chronological(db) -> None:
    _seed_audit(db)
    rows = db.query_audit_logs(limit=2)
    # limit caps to most recent N but preserves chronological (ascending) order
    assert [r["id"] for r in rows] == [3, 4]


def test_query_audit_logs_since_filters_by_timestamp(db) -> None:
    _seed_audit(db)
    all_rows = db.query_audit_logs()
    cutoff = all_rows[2]["timestamp"]  # keep rows #3 and #4
    rows = db.query_audit_logs(since=cutoff)
    assert {r["id"] for r in rows} == {3, 4}


def test_query_audit_logs_parses_payload_json(db) -> None:
    _seed_audit(db)
    rows = db.query_audit_logs(task_id="TASK-001", action="session_end")
    assert rows[0]["payload"] == {"duration_seconds": 30}


def test_insert_enrollment(db):
    db.insert_enrollment(
        name="content_writer",
        description="Writes destination guides",
        system_prompt="You are the Content Writer...",
        repos={"web-content": "https://github.com/t-benze/web-content.git"},
        executor="codex",
    )
    e = db.get_enrollment("content_writer")
    assert e is not None
    assert e["name"] == "content_writer"
    assert e["description"] == "Writes destination guides"
    assert e["status"] == "pending"
    assert e["repos"] == '{"web-content": "https://github.com/t-benze/web-content.git"}'
    assert e["executor"] == "codex"


def test_insert_enrollment_defaults_executor_to_claude(db):
    db.insert_enrollment("x", "desc", "prompt")
    e = db.get_enrollment("x")
    assert e["executor"] == "claude"


def test_get_enrollment_missing(db):
    assert db.get_enrollment("ghost") is None


def test_list_enrollments_by_status(db):
    db.insert_enrollment("a", "desc a", "prompt a")
    db.insert_enrollment("b", "desc b", "prompt b")
    db.update_enrollment_status("a", "approved")
    pending = db.list_enrollments(status="pending")
    assert len(pending) == 1
    assert pending[0]["name"] == "b"
    approved = db.list_enrollments(status="approved")
    assert len(approved) == 1
    assert approved[0]["name"] == "a"
    all_e = db.list_enrollments()
    assert len(all_e) == 2


def test_update_enrollment_status(db):
    db.insert_enrollment("x", "desc", "prompt")
    db.update_enrollment_status("x", "approved")
    assert db.get_enrollment("x")["status"] == "approved"


def test_update_enrollment_fields(db):
    db.insert_enrollment("x", "old desc", "old prompt")
    db.update_enrollment_fields(
        "x",
        description="new desc",
        system_prompt="new prompt",
        repos={"r": "u"},
        executor="codex",
    )
    e = db.get_enrollment("x")
    assert e["description"] == "new desc"
    assert e["system_prompt"] == "new prompt"
    assert e["executor"] == "codex"


def test_delete_enrollment(db):
    db.insert_enrollment("x", "desc", "prompt")
    db.delete_enrollment("x")
    assert db.get_enrollment("x") is None


def test_insert_task_with_parent_round_trips(db):
    parent = TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root")
    child = TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001"
    )
    db.insert_task(parent)
    db.insert_task(child)
    got = db.get_task("TASK-002")
    assert got.parent_task_id == "TASK-001"


def test_insert_task_result_stores_artifact_dir(db):
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="s1",
        output_summary="done", confidence_score=80,
        artifact_dir="artifacts/TASK-001",
    )
    rows = db.get_task_results("TASK-001")
    assert rows[0]["artifact_dir"] == "artifacts/TASK-001"


def test_insert_task_result_artifact_optional(db):
    db.insert_task_result(
        task_id="TASK-002", agent="dev_agent", session_id="s2",
        output_summary="done", confidence_score=80,
    )
    rows = db.get_task_results("TASK-002")
    assert rows[0]["artifact_dir"] is None


def test_update_task_sets_final_summary_and_artifact(db):
    db.insert_task(TaskRecord(id="TASK-010", type=TaskType.GENERAL, brief="b"))
    db.update_task(
        "TASK-010",
        note="Produced Q1 report",
        final_artifact_dir="artifacts/TASK-010",
    )
    got = db.get_task("TASK-010")
    assert got.note == "Produced Q1 report"
    assert got.final_artifact_dir == "artifacts/TASK-010"


def test_final_fields_default_to_none(db):
    db.insert_task(TaskRecord(id="TASK-011", type=TaskType.GENERAL, brief="b"))
    got = db.get_task("TASK-011")
    assert got.note is None
    assert got.final_artifact_dir is None


def test_get_children_returns_direct_children_only(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="c1", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="c2", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-004", type=TaskType.GENERAL, brief="grandchild", parent_task_id="TASK-002"
    ))
    assert db.get_children("TASK-001") == ["TASK-002", "TASK-003"]
    assert db.get_children("TASK-002") == ["TASK-004"]
    assert db.get_children("TASK-003") == []


def test_get_recall_payload_returns_task_with_children(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001"
    ))
    db.update_task(
        "TASK-001",
        note="All done",
        final_artifact_dir="artifacts/TASK-001",
    )
    payload = db.get_recall_payload("TASK-001")
    assert payload is not None
    assert payload["task_id"] == "TASK-001"
    assert payload["parent_task_id"] is None
    assert payload["brief"] == "root"
    assert payload["output_summary"] == "All done"
    assert payload["artifact_dir"] == "artifacts/TASK-001"
    assert payload["children"] == ["TASK-002"]


def test_get_recall_payload_missing_task_returns_none(db):
    assert db.get_recall_payload("TASK-404") is None


def test_update_task_writes_block_kind_and_note(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task(
        "TASK-001",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.DELEGATED,
        note="Delegated to dev_agent",
        orchestration_step_count=2,
    )
    t = db.get_task("TASK-001")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.DELEGATED
    assert t.note == "Delegated to dev_agent"
    assert t.orchestration_step_count == 2


def test_update_task_can_clear_block_kind_to_none(tmp_path):
    """When a task unblocks, block_kind and note must be nulled — the existing
    update_task `v is not None` filter would silently drop these writes."""
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="x")
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS,
                   block_kind=None, note=None)
    t = db.get_task("TASK-001")
    assert t.block_kind is None
    assert t.note is None


def test_get_nonterminal_task_ids_includes_blocked(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    for tid, status, bk in [
        ("T-PEN", TaskStatus.PENDING, None),
        ("T-INP", TaskStatus.IN_PROGRESS, None),
        ("T-BKD", TaskStatus.BLOCKED, BlockKind.DELEGATED),
        ("T-BKE", TaskStatus.BLOCKED, BlockKind.ESCALATED),
        ("T-CMP", TaskStatus.COMPLETED, None),
        ("T-FAI", TaskStatus.FAILED, None),
    ]:
        db.insert_task(TaskRecord(id=tid, type=TaskType.GENERAL, brief="x"))
        db.update_task(tid, status=status, block_kind=bk)

    ids = set(db.get_nonterminal_task_ids())
    assert ids == {"T-PEN", "T-INP", "T-BKD", "T-BKE"}


def test_list_blocked_with_kind(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.insert_task(TaskRecord(id="T-2", type=TaskType.GENERAL, brief="y"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    db.update_task("T-2", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED)

    ids = set(db.list_blocked_with_kind(BlockKind.DELEGATED))
    assert ids == {"T-1"}
    ids = set(db.list_blocked_with_kind(BlockKind.ESCALATED))
    assert ids == {"T-2"}
