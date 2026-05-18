import threading

import pytest

from src.infrastructure.database import Database, LineageTooDeep
from src.models import TaskRecord, TaskStatus


def test_init_creates_tables(db):
    tables = db.list_tables()
    assert "tasks" in tables
    assert "audit_log" in tables
    assert "scorecards" in tables
    assert "task_results" in tables


def test_insert_and_get_task(db):
    task = TaskRecord(
        id="TASK-001",
        brief="Add Alipay support",
    )
    db.insert_task(task)
    retrieved = db.get_task("TASK-001")
    assert retrieved is not None
    assert retrieved.id == "TASK-001"
    assert retrieved.brief == "Add Alipay support"
    assert retrieved.status == TaskStatus.PENDING


def test_get_nonexistent_task_returns_none(db):
    assert db.get_task("TASK-999") is None


def test_list_tasks_empty_returns_empty_list(db):
    assert db.list_tasks() == []


def test_list_tasks_returns_most_recent_first(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="Fix it"))
    db.insert_task(TaskRecord(id="TASK-002", brief="Build it"))
    tasks = db.list_tasks()
    assert len(tasks) == 2
    assert tasks[0].id == "TASK-002"


def test_update_task_status(db):
    task = TaskRecord(
        id="TASK-002",
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
    task = TaskRecord(id="TASK-001", brief="test")
    db.insert_task(task)
    assert db.next_task_id() == "TASK-002"


def test_next_task_id_skips_gaps(db):
    # Reproduces the production incident: a gap in the TASK-NNN sequence
    # (caused by transient out-of-band rows that were later deleted) must not
    # cause next_task_id to return an id that already exists.
    db.insert_task(TaskRecord(id="TASK-001", brief="t1"))
    db.insert_task(TaskRecord(id="TASK-003", brief="t3"))
    assert db.next_task_id() == "TASK-004"


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


def test_list_approved_agent_names(db):
    db.insert_enrollment("alpha", "desc", "prompt")
    db.insert_enrollment("beta", "desc", "prompt")
    db.update_enrollment_status("beta", "approved")
    result = db.list_approved_agent_names()
    assert result == ["beta"]


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
    parent = TaskRecord(id="TASK-001", brief="root")
    child = TaskRecord(
        id="TASK-002", brief="child", parent_task_id="TASK-001"
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


def test_insert_task_result_persists_decision_json(db):
    """EH decisions ride on task_results.decision_json as an opaque JSON
    string. The column is nullable (workers omit it) and round-trips
    byte-for-byte so the orchestrator can re-parse it downstream."""
    import json as _json

    payload = _json.dumps({
        "action": "delegate", "agent": "dev_agent", "prompt": "Do X",
    })
    db.insert_task_result(
        task_id="TASK-001", agent="engineering_head", session_id="eh1",
        output_summary="Triaged and delegated.", confidence_score=90,
        decision_json=payload,
    )
    row = db.get_latest_task_result("TASK-001", "engineering_head", "eh1")
    assert row["decision_json"] == payload


def test_insert_task_result_decision_json_optional(db):
    db.insert_task_result(
        task_id="TASK-003", agent="dev_agent", session_id="s3",
        output_summary="done", confidence_score=80,
    )
    row = db.get_latest_task_result("TASK-003", "dev_agent", "s3")
    assert row["decision_json"] is None


def test_update_task_sets_final_summary_and_artifact(db):
    db.insert_task(TaskRecord(id="TASK-010", brief="b"))
    db.update_task(
        "TASK-010",
        note="Produced Q1 report",
        final_artifact_dir="artifacts/TASK-010",
    )
    got = db.get_task("TASK-010")
    assert got.note == "Produced Q1 report"
    assert got.final_artifact_dir == "artifacts/TASK-010"


def test_final_fields_default_to_none(db):
    db.insert_task(TaskRecord(id="TASK-011", brief="b"))
    got = db.get_task("TASK-011")
    assert got.note is None
    assert got.final_artifact_dir is None


def test_get_children_returns_direct_children_only(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="c1", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="c2", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-004", brief="grandchild", parent_task_id="TASK-002"
    ))
    assert db.get_children("TASK-001") == ["TASK-002", "TASK-003"]
    assert db.get_children("TASK-002") == ["TASK-004"]
    assert db.get_children("TASK-003") == []


def test_get_recall_payload_returns_task_with_children(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="child", parent_task_id="TASK-001"
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
    from src.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "grassland.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
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
    from src.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "grassland.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="x")
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS,
                   block_kind=None, note=None)
    t = db.get_task("TASK-001")
    assert t.block_kind is None
    assert t.note is None


def test_get_nonterminal_task_ids_includes_blocked(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "grassland.db")
    for tid, status, bk in [
        ("T-PEN", TaskStatus.PENDING, None),
        ("T-INP", TaskStatus.IN_PROGRESS, None),
        ("T-BKD", TaskStatus.BLOCKED, BlockKind.DELEGATED),
        ("T-BKE", TaskStatus.BLOCKED, BlockKind.ESCALATED),
        ("T-CMP", TaskStatus.COMPLETED, None),
        ("T-FAI", TaskStatus.FAILED, None),
    ]:
        db.insert_task(TaskRecord(id=tid, brief="x"))
        db.update_task(tid, status=status, block_kind=bk)

    ids = set(db.get_nonterminal_task_ids())
    assert ids == {"T-PEN", "T-INP", "T-BKD", "T-BKE"}


def test_list_blocked_with_kind(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "grassland.db")
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.insert_task(TaskRecord(id="T-2", brief="y"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    db.update_task("T-2", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED)

    ids = set(db.list_blocked_with_kind(BlockKind.DELEGATED))
    assert ids == {"T-1"}
    ids = set(db.list_blocked_with_kind(BlockKind.ESCALATED))
    assert ids == {"T-2"}


def test_walk_ancestors_leaf_to_root_returns_chain(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="mid", parent_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="leaf", parent_task_id="TASK-002",
    ))
    chain = db.walk_ancestors("TASK-003")
    assert [t.id for t in chain] == ["TASK-003", "TASK-002", "TASK-001"]


def test_walk_ancestors_root_returns_single_element(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="root"))
    chain = db.walk_ancestors("TASK-001")
    assert [t.id for t in chain] == ["TASK-001"]


def test_walk_ancestors_raises_when_over_limit(db):
    db.insert_task(TaskRecord(id="TASK-000", brief="root"))
    prev = "TASK-000"
    for i in range(1, 25):  # 24 descendants + root = 25 hops
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, brief=f"t{i}", parent_task_id=prev,
        ))
        prev = tid
    with pytest.raises(LineageTooDeep):
        db.walk_ancestors(prev, max_hops=20)


def test_revisit_of_task_id_column_exists(db):
    """The tasks table must gain a nullable revisit_of_task_id column.
    Idempotent on restart: reopening the same DB must not error.
    """
    cols = {row[1] for row in db._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "revisit_of_task_id" in cols

    # Index exists (keeps the reverse lookup `WHERE revisit_of_task_id = ?` cheap).
    indexes = {row[1] for row in db._conn.execute(
        "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
    ).fetchall()}
    assert "idx_tasks_revisit_of" in indexes


def test_migration_idempotent_over_restart(tmp_path):
    """Opening a Database twice on the same file must not raise."""
    from src.infrastructure.database import Database
    path = tmp_path / "restart.db"
    db1 = Database(path)
    db1.close()
    # Second open is where duplicate-column / duplicate-index errors would fire
    # if the migration weren't guarded.
    db2 = Database(path)
    cols = {row[1] for row in db2._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "revisit_of_task_id" in cols
    db2.close()


def test_concurrent_access_from_multiple_threads_is_safe(db):
    """Regression test: sqlite3 raises InterfaceError when two threads use the
    same connection concurrently. The daemon exposes this shape — route
    handlers run on the event loop while `run_step` runs in a threadpool
    worker, and both touch the single shared `Database`. Without internal
    serialization, a concurrent `grassland revisit` + SSE tail hits
    `sqlite3.InterfaceError: bad parameter or other API misuse` on
    `GET /tasks/{id}/events` (observed on TASK-061, daemon.log 688-746).
    """
    for i in range(10):
        db.insert_task(TaskRecord(
            id=f"TASK-{i:03d}", brief=f"task {i}",
        ))

    errors: list[BaseException] = []
    ITERATIONS = 200

    def reader() -> None:
        try:
            for i in range(ITERATIONS):
                db.get_task(f"TASK-{i % 10:03d}")
                db.list_tasks(limit=10)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(ITERATIONS):
                db.insert_audit_log(
                    task_id=f"TASK-{i % 10:03d}",
                    agent="test_agent",
                    action="test_action",
                    payload={"i": i},
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=reader),
        threading.Thread(target=reader),
        threading.Thread(target=writer),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], (
        f"Concurrent Database access raised {len(errors)} exceptions, "
        f"first: {type(errors[0]).__name__}: {errors[0]}"
    )


def test_insert_task_round_trips_revisit_of(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="predecessor"))
    db.insert_task(TaskRecord(
        id="TASK-002",
        brief="revisit",
        revisit_of_task_id="TASK-001",
    ))
    got = db.get_task("TASK-002")
    assert got is not None
    assert got.revisit_of_task_id == "TASK-001"

    # Non-revisit tasks keep it NULL on read.
    got_pre = db.get_task("TASK-001")
    assert got_pre.revisit_of_task_id is None


def test_list_tasks_exposes_revisit_of(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="pre"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    rows = {t.id: t for t in db.list_tasks()}
    assert rows["TASK-002"].revisit_of_task_id == "TASK-001"
    assert rows["TASK-001"].revisit_of_task_id is None


def test_update_task_cannot_change_revisit_of_task_id(db):
    """The column is write-once at insert time. Guards against accidental
    mutation from other write paths."""
    db.insert_task(TaskRecord(
        id="TASK-001", brief="rv",
        revisit_of_task_id="TASK-000",
    ))
    db.update_task("TASK-001", revisit_of_task_id="TASK-999")
    got = db.get_task("TASK-001")
    assert got.revisit_of_task_id == "TASK-000"  # unchanged


def test_backfill_populates_revisit_of_task_id_from_audit_log(tmp_path):
    """Simulates a pre-feature revisit row: tasks has the column but no value,
    audit_log has the revisit_of entry. Reopening the DB must backfill."""
    from src.infrastructure.database import Database

    path = tmp_path / "backfill.db"
    db = Database(path)

    db.insert_task(TaskRecord(id="TASK-001", brief="pre"))
    db.insert_task(TaskRecord(id="TASK-002", brief="rv"))
    # Forcibly NULL the column to simulate legacy data even if Task 3 shipped first.
    db._conn.execute(
        "UPDATE tasks SET revisit_of_task_id = NULL WHERE id = 'TASK-002'"
    )
    db._conn.commit()
    db.insert_audit_log(
        task_id="TASK-002",
        agent="founder",
        action="revisit_of",
        payload={
            "predecessor_root": "TASK-001",
            "flagged": "TASK-001",
            "cascade": ["TASK-001"],
            "prior_status": "failed",
            "founder_note": None,
        },
    )
    db.close()

    # Reopen — backfill runs in _create_tables.
    db2 = Database(path)
    row = db2.get_task("TASK-002")
    assert row.revisit_of_task_id == "TASK-001"
    db2.close()


def test_backfill_does_not_overwrite_existing_value(tmp_path):
    """If revisit_of_task_id is already set, backfill must leave it alone —
    idempotent guard against audit-entry drift."""
    from src.infrastructure.database import Database
    path = tmp_path / "no-overwrite.db"
    db = Database(path)
    db.insert_task(TaskRecord(id="TASK-001", brief="pre"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    # Seed a conflicting audit entry; backfill must NOT overwrite.
    db.insert_audit_log(
        task_id="TASK-002", agent="founder", action="revisit_of",
        payload={"predecessor_root": "TASK-999", "flagged": "TASK-999",
                 "cascade": ["TASK-999"], "prior_status": "failed",
                 "founder_note": None},
    )
    db.close()

    db2 = Database(path)
    assert db2.get_task("TASK-002").revisit_of_task_id == "TASK-001"
    db2.close()


def test_backfill_is_a_noop_when_nothing_to_backfill(tmp_path):
    """Opening a DB with no revisit_of audit entries must not raise."""
    from src.infrastructure.database import Database
    path = tmp_path / "clean.db"
    db = Database(path)
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    db.close()
    Database(path).close()


def test_walk_revisit_chain_returns_task_to_original(db):
    """Stacked chain: P (original) → N (revisit of P) → N' (revisit of N).
    walk_revisit_chain(N') returns [N', N, P]."""
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="N-prime",
        revisit_of_task_id="TASK-002",
    ))
    chain = db.walk_revisit_chain("TASK-003")
    assert [t.id for t in chain] == ["TASK-003", "TASK-002", "TASK-001"]


def test_walk_revisit_chain_non_revisit_returns_single(db):
    """Plain task: returns [task] only."""
    db.insert_task(TaskRecord(id="TASK-001", brief="plain"))
    chain = db.walk_revisit_chain("TASK-001")
    assert [t.id for t in chain] == ["TASK-001"]


def test_walk_revisit_chain_missing_task_returns_empty(db):
    assert db.walk_revisit_chain("TASK-999") == []


def test_walk_revisit_chain_raises_when_over_limit(db):
    """Defensive bound matching walk_ancestors."""
    from src.infrastructure.database import LineageTooDeep
    db.insert_task(TaskRecord(id="TASK-000", brief="orig"))
    prev = "TASK-000"
    for i in range(1, 25):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, brief=f"t{i}",
            revisit_of_task_id=prev,
        ))
        prev = tid
    with pytest.raises(LineageTooDeep):
        db.walk_revisit_chain(prev, max_hops=20)


def test_walk_revisit_chain_truncates_when_asked(db):
    """Read-path opt-in: return the partial chain on overrun instead of raising.

    Revisit history grows naturally over a task's lifetime (unlike parent
    ancestry, which is bounded by the delegation depth), so read endpoints
    need a non-crashing path.
    """
    db.insert_task(TaskRecord(id="TASK-000", brief="orig"))
    prev = "TASK-000"
    for i in range(1, 25):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, brief=f"t{i}",
            revisit_of_task_id=prev,
        ))
        prev = tid
    chain = db.walk_revisit_chain(prev, max_hops=20, truncate=True)
    assert len(chain) == 20
    assert chain[0].id == prev


def test_walk_ancestors_does_not_follow_revisit_edge(db):
    """REGRESSION GUARD: cascade-fail in run_step keys on walk_ancestors. If
    walk_ancestors ever followed revisit_of_task_id, a predecessor's FAILED
    children would poison the new root via _enqueue_parent_if_waiting.
    Never let this test go green by making walk_ancestors follow the edge.
    """
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="N",
        revisit_of_task_id="TASK-001",  # NOT a parent edge.
        parent_task_id=None,             # Still a root.
    ))
    chain = db.walk_ancestors("TASK-002")
    assert [t.id for t in chain] == ["TASK-002"]  # Does NOT include TASK-001.


def test_get_direct_revisits_returns_all_direct_children(db):
    """Two revisits of the same predecessor — both appear, ordered by creation."""
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="rv1",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="rv2",
        revisit_of_task_id="TASK-001",
    ))
    assert db.get_direct_revisits("TASK-001") == ["TASK-002", "TASK-003"]


def test_get_direct_revisits_does_not_include_transitive(db):
    """In P → N → N', P.get_direct_revisits returns only [N], not [N, N']."""
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="N'",
        revisit_of_task_id="TASK-002",
    ))
    assert db.get_direct_revisits("TASK-001") == ["TASK-002"]
    assert db.get_direct_revisits("TASK-002") == ["TASK-003"]


def test_get_direct_revisits_none(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    assert db.get_direct_revisits("TASK-001") == []


def test_insert_task_succeeds_on_legacy_schema_with_type_column(tmp_path):
    """Simulate an upgraded DB that still has the legacy `type NOT NULL` column.
    insert_task must supply a sentinel value so the NOT NULL constraint is satisfied."""
    import sqlite3
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db_path = tmp_path / "legacy.db"

    # Manually create the legacy schema (pre-Task-4) with type TEXT NOT NULL.
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_summary TEXT,
            final_artifact_dir TEXT,
            block_kind TEXT,
            note TEXT,
            orchestration_step_count INTEGER DEFAULT 0,
            cancelled_at TEXT,
            revisit_of_task_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scorecards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL UNIQUE,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            acceptance_rate REAL NOT NULL,
            revision_rate REAL NOT NULL,
            error_count INTEGER NOT NULL,
            tier TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            output_summary TEXT,
            decision_json TEXT,
            confidence_score INTEGER,
            learnings TEXT,
            risks_flagged TEXT,
            duration_seconds INTEGER,
            token_count INTEGER,
            estimated_cost REAL,
            artifact_dir TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_enrollments (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            repos TEXT NOT NULL DEFAULT '{}',
            executor TEXT NOT NULL DEFAULT 'claude',
            allow_rules TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS talks (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            topic_list_json TEXT,
            new_learnings_count INTEGER NOT NULL DEFAULT 0,
            new_kb_slugs_json TEXT,
            transcript_path TEXT
        );
    """)
    conn.commit()
    conn.close()

    # Database() should detect the legacy column and handle inserts gracefully.
    db = Database(db_path)
    assert db._tasks_has_legacy_type_column is True

    record = TaskRecord(id="TASK-001", brief="legacy schema test", team="engineering")
    db.insert_task(record)  # Must NOT raise IntegrityError

    # Round-trip read should work.
    got = db.get_task("TASK-001")
    assert got is not None
    assert got.id == "TASK-001"
    assert got.brief == "legacy schema test"


def test_fresh_db_has_no_legacy_type_column(tmp_path):
    """Fresh DBs must not have the legacy type column — flag stays False."""
    from src.infrastructure.database import Database

    db = Database(tmp_path / "fresh.db")
    assert db._tasks_has_legacy_type_column is False
    db.close()


def test_task_round_trips_dispatched_from_talk_id(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "grassland.db")
    task = TaskRecord(
        id="TASK-001",
        brief="dispatched task",
        team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_talk_id="TALK-007",
    )
    db.insert_task(task)
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id == "TALK-007"


def test_task_round_trips_dispatched_from_talk_id_when_null(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "grassland.db")
    task = TaskRecord(id="TASK-001", brief="normal task", team="engineering")
    db.insert_task(task)
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id is None


def test_idempotent_dispatched_from_talk_id_migration(tmp_path):
    from src.infrastructure.database import Database

    db_path = tmp_path / "grassland.db"
    Database(db_path)            # first init creates the column
    Database(db_path)            # second init must NOT raise


def test_dispatched_from_talk_id_index_queryable(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "grassland.db")
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", team="engineering",
        assigned_agent="dev_agent", dispatched_from_talk_id="TALK-007",
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", team="engineering",
        assigned_agent="dev_agent",
    ))
    cur = db._conn.execute(
        "SELECT id FROM tasks WHERE dispatched_from_talk_id = ?", ("TALK-007",),
    )
    rows = [r["id"] for r in cur.fetchall()]
    assert rows == ["TASK-001"]


def test_escalation_notifications_table_exists(tmp_path):
    db = Database(tmp_path / "grassland.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='escalation_notifications'"
    )
    assert cur.fetchone() is not None


def test_escalation_notifications_index_exists(tmp_path):
    db = Database(tmp_path / "grassland.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='escalation_notifications'"
    )
    names = {row[0] for row in cur.fetchall()}
    assert "idx_escalation_notifications_task" in names


def test_processed_event_ids_table_exists(tmp_path):
    db = Database(tmp_path / "grassland.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_event_ids'"
    )
    assert cur.fetchone() is not None


from datetime import datetime, timedelta, timezone


def test_mint_escalation_notification_writes_row(tmp_path):
    db = Database(tmp_path / "grassland.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_xyz",
        org_slug="hk-macau-tourism",
        task_id="TASK-001",
        chat_id="oc_abc",
        expires_at=expires,
    )
    row = db.get_escalation_notification("om_xyz")
    assert row is not None
    assert row["org_slug"] == "hk-macau-tourism"
    assert row["task_id"] == "TASK-001"
    assert row["chat_id"] == "oc_abc"
    assert row["consumed_at"] is None


def test_get_escalation_notification_missing_returns_none(tmp_path):
    db = Database(tmp_path / "grassland.db")
    assert db.get_escalation_notification("om_missing") is None


def test_consume_escalation_notification_marks_consumed(tmp_path):
    db = Database(tmp_path / "grassland.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    assert db.consume_escalation_notification("om_1", consumed_by="cli-fallback") is True
    row = db.get_escalation_notification("om_1")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


def test_consume_escalation_notification_twice_returns_false(tmp_path):
    db = Database(tmp_path / "grassland.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is True
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is False


def test_record_processed_event_first_call_returns_true(tmp_path):
    db = Database(tmp_path / "grassland.db")
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    ) is True


def test_record_processed_event_duplicate_returns_false(tmp_path):
    db = Database(tmp_path / "grassland.db")
    db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    )
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="rejected", reason="dup",
    ) is False


def test_update_processed_event_outcome(tmp_path):
    db = Database(tmp_path / "grassland.db")
    db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="pending", reason=None,
    )
    db.update_processed_event_outcome(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    )
    cur = db._conn.execute(
        "SELECT outcome, reason FROM processed_event_ids "
        "WHERE org_slug = ? AND feishu_event_id = ?",
        ("o", "evt_1"),
    )
    row = cur.fetchone()
    assert row["outcome"] == "consumed"
    assert row["reason"] is None


def test_list_open_notifications_for_task(tmp_path):
    db = Database(tmp_path / "grassland.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    db.mint_escalation_notification(
        feishu_message_id="om_2", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    db.consume_escalation_notification("om_1", consumed_by="x")
    rows = db.list_open_notifications_for_task("T1")
    ids = [r["feishu_message_id"] for r in rows]
    assert ids == ["om_2"]  # only the unconsumed one
