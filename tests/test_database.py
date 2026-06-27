import threading
import time

import pytest

from runtime.infrastructure.database import Database, LineageTooDeep
from runtime.models import TaskRecord, TaskStatus


def test_init_creates_tables(db):
    tables = db.list_tables()
    assert "tasks" in tables
    assert "audit_log" in tables
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


def test_list_tasks_filters_by_status(db):
    db.insert_task(TaskRecord(id="TASK-001", brief="a", status=TaskStatus.PENDING))
    db.insert_task(TaskRecord(id="TASK-002", brief="b", status=TaskStatus.BLOCKED))
    db.insert_task(TaskRecord(id="TASK-003", brief="c", status=TaskStatus.COMPLETED))
    blocked = db.list_tasks(status=TaskStatus.BLOCKED)
    assert [t.id for t in blocked] == ["TASK-002"]
    # Raw string value also accepted (CLI/query-param path).
    assert [t.id for t in db.list_tasks(status="completed")] == ["TASK-003"]


def test_list_tasks_filters_by_block_kind(db):
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.DELEGATED,
    ))
    escalated = db.list_tasks(status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED)
    assert [t.id for t in escalated] == ["TASK-001"]
    delegated = db.list_tasks(block_kind="delegated")
    assert [t.id for t in delegated] == ["TASK-002"]


def test_list_tasks_status_filter_composes_with_agent(db):
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", status=TaskStatus.BLOCKED, assigned_agent="dev_agent",
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", status=TaskStatus.BLOCKED, assigned_agent="qa_engineer",
    ))
    rows = db.list_tasks(status=TaskStatus.BLOCKED, assigned_agent="dev_agent")
    assert [t.id for t in rows] == ["TASK-001"]


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


def test_insert_task_with_parent_round_trips(db):
    parent = TaskRecord(id="TASK-001", brief="root")
    child = TaskRecord(
        id="TASK-002", brief="child", parent_task_id="TASK-001"
    )
    db.insert_task(parent)
    db.insert_task(child)
    got = db.get_task("TASK-002")
    assert got.parent_task_id == "TASK-001"


def test_insert_task_result_stores_output_dir(db):
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="s1",
        output_summary="done", confidence_score=80,
        output_dir="output/TASK-001",
    )
    rows = db.get_task_results("TASK-001")
    assert rows[0]["output_dir"] == "output/TASK-001"


def test_insert_task_result_output_dir_optional(db):
    db.insert_task_result(
        task_id="TASK-002", agent="dev_agent", session_id="s2",
        output_summary="done", confidence_score=80,
    )
    rows = db.get_task_results("TASK-002")
    assert rows[0]["output_dir"] is None


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


def test_update_task_sets_final_summary_and_output_dir(db):
    db.insert_task(TaskRecord(id="TASK-010", brief="b"))
    db.update_task(
        "TASK-010",
        note="Produced Q1 report",
        final_output_dir="output/TASK-010",
    )
    got = db.get_task("TASK-010")
    assert got.note == "Produced Q1 report"
    assert got.final_output_dir == "output/TASK-010"


def test_final_fields_default_to_none(db):
    db.insert_task(TaskRecord(id="TASK-011", brief="b"))
    got = db.get_task("TASK-011")
    assert got.note is None
    assert got.final_output_dir is None


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
        final_output_dir="output/TASK-001",
    )
    payload = db.get_recall_payload("TASK-001")
    assert payload is not None
    assert payload["task_id"] == "TASK-001"
    assert payload["parent_task_id"] is None
    assert payload["brief"] == "root"
    assert payload["output_summary"] == "All done"
    assert payload["output_dir"] == "output/TASK-001"
    assert payload["children"] == ["TASK-002"]


def test_get_recall_payload_missing_task_returns_none(db):
    assert db.get_recall_payload("TASK-404") is None


def test_update_task_writes_block_kind_and_note(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "happyranch.db")
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
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="x")
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS,
                   block_kind=None, note=None)
    t = db.get_task("TASK-001")
    assert t.block_kind is None
    assert t.note is None


def test_get_nonterminal_task_ids_path_b(tmp_path):
    """Path B: the restart-sweep iterator yields {pending, in_progress,
    escalated}. Parked carriers are in_progress(delegated|blocked_on_job);
    escalated is its own top-level non-terminal status. blocked is dropped
    (no live row is `blocked` after the boot migration). cancelled is terminal
    → excluded alongside completed/failed."""
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "happyranch.db")
    for tid, status, bk in [
        ("T-PEN", TaskStatus.PENDING, None),
        ("T-RUN", TaskStatus.IN_PROGRESS, None),                  # running subprocess
        ("T-DEL", TaskStatus.IN_PROGRESS, BlockKind.DELEGATED),   # parked on children
        ("T-JOB", TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB),  # parked on jobs
        ("T-ESC", TaskStatus.ESCALATED, None),                    # awaiting founder
        ("T-CMP", TaskStatus.COMPLETED, None),
        ("T-FAI", TaskStatus.FAILED, None),
        ("T-CAN", TaskStatus.CANCELLED, None),
    ]:
        db.insert_task(TaskRecord(id=tid, brief="x"))
        db.update_task(tid, status=status, block_kind=bk)

    ids = set(db.get_nonterminal_task_ids())
    assert ids == {"T-PEN", "T-RUN", "T-DEL", "T-JOB", "T-ESC"}


def test_list_blocked_with_kind(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "happyranch.db")
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
    from runtime.infrastructure.database import Database
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
    serialization, a concurrent `happyranch revisit` + SSE tail hits
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
    from runtime.infrastructure.database import Database

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
    from runtime.infrastructure.database import Database
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
    from runtime.infrastructure.database import Database
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
    from runtime.infrastructure.database import LineageTooDeep
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


def test_legacy_type_column_is_dropped_on_open(tmp_path):
    """A pre-Task-4 DB with a legacy `type TEXT NOT NULL` column: opening it
    via Database() drops the column, and inserts still work."""
    import sqlite3
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE tasks (
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
            final_output_dir TEXT
        )"""
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "type" not in cols          # legacy column dropped
    assert "task_type" in cols         # new column present

    db.insert_task(TaskRecord(id="TASK-001", brief="legacy schema test"))
    got = db.get_task("TASK-001")
    assert got is not None and got.task_type == "task"


def test_task_type_backfill_classifies_existing_children_as_subtask(tmp_path):
    """Upgrade migration: a pre-existing DB with a root + delegated child (no
    task_type column) must backfill the child (parent_task_id IS NOT NULL) to
    'subtask' and the root to 'task'. Otherwise an in-flight legacy child would
    be mis-typed 'task' and run_step would parse its completion as a decision."""
    import sqlite3
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord

    db_path = tmp_path / "upgrade.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_dir TEXT
        )"""
    )
    # A root (no parent) and a delegated child (has parent) — no task_type col.
    conn.execute(
        "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
        "VALUES ('TASK-1', 'blocked', 'engineering', 'root', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO tasks (id, status, team, brief, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-2', 'pending', 'engineering', 'child', 'TASK-1', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    assert db.get_task("TASK-1").task_type == "task"      # root
    assert db.get_task("TASK-2").task_type == "subtask"   # delegated child
    db.close()
    db.close()


def test_escalation_notifications_table_exists(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='escalation_notifications'"
    )
    assert cur.fetchone() is not None


def test_escalation_notifications_index_exists(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='escalation_notifications'"
    )
    names = {row[0] for row in cur.fetchall()}
    assert "idx_escalation_notifications_task" in names


def test_processed_event_ids_table_exists(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_event_ids'"
    )
    assert cur.fetchone() is not None


from datetime import datetime, timedelta, timezone


def test_mint_escalation_notification_writes_row(tmp_path):
    db = Database(tmp_path / "happyranch.db")
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
    db = Database(tmp_path / "happyranch.db")
    assert db.get_escalation_notification("om_missing") is None


def test_consume_escalation_notification_marks_consumed(tmp_path):
    db = Database(tmp_path / "happyranch.db")
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
    db = Database(tmp_path / "happyranch.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is True
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is False


def test_record_processed_event_first_call_returns_true(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    ) is True


def test_record_processed_event_duplicate_returns_false(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    )
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="rejected", reason="dup",
    ) is False


def test_update_processed_event_outcome(tmp_path):
    db = Database(tmp_path / "happyranch.db")
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
    db = Database(tmp_path / "happyranch.db")
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


def test_mint_escalation_notification_accepts_script_request_kind(tmp_path):
    from datetime import datetime, timedelta, timezone
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "happyranch.db")
    db.mint_escalation_notification(
        feishu_message_id="om_sr_1",
        org_slug="acme",
        task_id="SR-007",
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="job_request",
    )
    row = db.get_escalation_notification("om_sr_1")
    assert row is not None
    assert row["kind"] == "job_request"
    assert row["task_id"] == "SR-007"


def test_get_latest_notification_for_sr_returns_most_recent(tmp_path):
    from datetime import datetime, timedelta, timezone
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "happyranch.db")
    now = datetime.now(timezone.utc)
    db.mint_escalation_notification(
        feishu_message_id="om_old", org_slug="acme", task_id="SR-007",
        chat_id="oc_xyz", expires_at=now + timedelta(hours=72),
        kind="job_request",
    )
    time.sleep(0.001)
    db.mint_escalation_notification(
        feishu_message_id="om_new", org_slug="acme", task_id="SR-007",
        chat_id="oc_xyz", expires_at=now + timedelta(hours=72),
        kind="job_request",
    )
    found = db.get_latest_notification_for_sr("SR-007", kind="job_request")
    assert found is not None
    assert found["feishu_message_id"] == "om_new"


def test_get_latest_notification_for_sr_returns_none_when_missing(tmp_path):
    from runtime.infrastructure.database import Database
    db = Database(tmp_path / "happyranch.db")
    assert db.get_latest_notification_for_sr("SR-999", kind="job_request") is None


def test_get_latest_notification_for_sr_finds_consumed_rows(tmp_path):
    """The terminal-result follow-up needs the parent message_id even after
    the original APPROVE consumed the row."""
    from datetime import datetime, timedelta, timezone
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "happyranch.db")
    db.mint_escalation_notification(
        feishu_message_id="om_x", org_slug="acme", task_id="SR-008",
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="job_request",
    )
    db.consume_escalation_notification("om_x", consumed_by="feishu-reply")
    found = db.get_latest_notification_for_sr("SR-008", kind="job_request")
    assert found is not None  # consumed rows still returned for follow-up lookups
    assert found["feishu_message_id"] == "om_x"


# ---- Cancel-race Guard C: SQL-level atomic CAS ----
# See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.
# Codex review of PR #34 surfaced that the Python-level _is_already_terminal
# check is non-atomic with the subsequent db.update_task / db.insert_task,
# leaving a microsecond-window race. These methods close it at the SQL layer.

def test_try_escalate_succeeds_on_pending_task(db):
    """CAS happy path (Path B): PENDING task transitions to ESCALATED
    (top-level status, block_kind cleared)."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    ok = db.try_escalate("T-1", reason="needs founder")
    assert ok is True
    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert t.block_kind is None
    assert t.note == "needs founder"


def test_try_escalate_rejects_cancelled_task(db):
    """Atomic CAS: a task with cancelled_at set must not be transitioned
    back to BLOCKED(ESCALATED). Closes Codex P2 race in the escalate branch."""
    from datetime import datetime, timezone
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task(
        "T-1", status=TaskStatus.FAILED, cancelled_at=now, completed_at=now,
        note="cancelled by founder",
    )
    ok = db.try_escalate("T-1", reason="bogus")
    assert ok is False
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED  # unchanged
    assert t.note == "cancelled by founder"  # unchanged
    assert t.cancelled_at is not None


def test_try_escalate_rejects_terminal_task(db):
    """A COMPLETED or FAILED task must not be re-escalated even without cancel."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.COMPLETED, note="done")
    ok = db.try_escalate("T-1", reason="bogus")
    assert ok is False
    t = db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.note == "done"


def test_try_escalate_rejects_missing_task(db):
    assert db.try_escalate("T-NOPE", reason="x") is False


def test_try_escalate_over_budget_succeeds_from_expected_state(db):
    """CAS happy path (Path B): an eligible PENDING task at the step cap
    escalates to the top-level ESCALATED status (block_kind cleared)."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    ok = db.try_escalate_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        reason="max steps (3) exceeded",
    )
    assert ok is True
    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert t.block_kind is None
    assert t.note == "max steps (3) exceeded"


def test_try_escalate_over_budget_is_idempotent_under_duplicate_delivery(db):
    """Two duplicate deliveries read the same eligible at-cap row; only the
    first writer wins. The second sees the row already moved out of PENDING so
    its conditional UPDATE matches zero rows → returns False. Guarantees the
    thread `task_escalated` message + TASK_FOLLOWUP invocation fire exactly once
    on the pre-CAS max-steps path."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    first = db.try_escalate_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        reason="max steps (3) exceeded",
    )
    second = db.try_escalate_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        reason="max steps (3) exceeded",
    )
    assert first is True
    assert second is False


def test_try_escalate_over_budget_rejects_cancelled_task(db):
    """A /cancel landing between the step-1 read and the budget guard moves the
    row to FAILED; the CAS pre-state no longer matches → no escalation."""
    from datetime import datetime, timezone
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, cancelled_at=now,
                   completed_at=now, note="cancelled by founder")
    ok = db.try_escalate_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        reason="bogus",
    )
    assert ok is False
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder"


def test_try_fail_over_budget_succeeds_from_expected_state(db):
    """THR-033 Change A: the non-root variant of the budget-guard CAS. An
    eligible PENDING task at the step cap transitions to FAILED (block_kind
    NULL, completed_at set) instead of BLOCKED(ESCALATED)."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    ok = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        note="max steps (3) exceeded",
    )
    assert ok is True
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.block_kind is None
    assert t.note == "max steps (3) exceeded"
    assert t.completed_at is not None


def test_try_fail_over_budget_is_idempotent_under_duplicate_delivery(db):
    """The CAS makes the non-root over-budget fail fire exactly once: two
    duplicate deliveries read the same eligible at-cap row, only the first
    writer wins, so the parent enqueue + thread followup fire once."""
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    first = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        note="max steps (3) exceeded",
    )
    second = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        note="max steps (3) exceeded",
    )
    assert first is True
    assert second is False
    assert db.get_task("T-1").status == TaskStatus.FAILED


def test_try_fail_over_budget_rejects_cancelled_task(db):
    """A /cancel landing between the step-1 read and the budget guard moves the
    row out of the expected pre-state; the CAS rejects the fail for free."""
    from datetime import datetime, timezone
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, cancelled_at=now,
                   completed_at=now, note="cancelled by founder")
    ok = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.PENDING, expected_block_kind=None,
        note="bogus",
    )
    assert ok is False
    t = db.get_task("T-1")
    assert t.note == "cancelled by founder"  # unchanged


def test_try_fail_over_budget_succeeds_from_blocked_delegated(db):
    """The budget guard can also fire from a BLOCKED(DELEGATED) eligible
    pre-state (a resumed parent-style row); the CAS keys on the block_kind."""
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    ok = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.BLOCKED,
        expected_block_kind=BlockKind.DELEGATED, note="max steps (3) exceeded",
    )
    assert ok is True
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.block_kind is None


def test_try_delegate_succeeds_on_pending_parent(db):
    """CAS happy path (Path B): parent transitions to IN_PROGRESS(DELEGATED)
    — a parent waiting on its own children is in progress, with the waiting
    reason kept in block_kind — AND child is inserted in one atomic RLock
    acquisition."""
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head"))
    child = TaskRecord(
        id="T-CHILD", brief="child work",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    )
    ok = db.try_delegate("T-PAR", child, parent_note="Delegated to dev_agent (child=T-CHILD)")
    assert ok is True

    par = db.get_task("T-PAR")
    assert par.status == TaskStatus.IN_PROGRESS
    assert par.block_kind == BlockKind.DELEGATED
    assert par.note == "Delegated to dev_agent (child=T-CHILD)"
    ch = db.get_task("T-CHILD")
    assert ch is not None
    assert ch.parent_task_id == "T-PAR"


def test_try_claim_for_step_parked_delegated_clears_discriminant(db):
    """Path B §C.2: try_claim_for_step is representation-agnostic — claiming a
    parked in_progress(delegated) task (the pickup after all children terminal)
    transitions it to in_progress(block_kind NULL) via the SAME (status,
    block_kind) CAS, with NO SQL change. Exactly one claim wins; a stale
    duplicate delivery carrying the old expected pair loses."""
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED)
    ok = db.try_claim_for_step(
        "T-1", expected_status=TaskStatus.IN_PROGRESS,
        expected_block_kind=BlockKind.DELEGATED, new_count=3,
    )
    assert ok is True
    t = db.get_task("T-1")
    assert t.status == TaskStatus.IN_PROGRESS
    assert t.block_kind is None
    assert t.orchestration_step_count == 3
    # Duplicate delivery with the now-stale expected pair matches zero rows.
    assert db.try_claim_for_step(
        "T-1", expected_status=TaskStatus.IN_PROGRESS,
        expected_block_kind=BlockKind.DELEGATED, new_count=4,
    ) is False
    assert db.get_task("T-1").orchestration_step_count == 3


def test_path_b_migration_flips_live_blocked_rows(tmp_path):
    """Path B §D.3: the idempotent boot migration flips LIVE blocked(...) rows
    into the stored model on the next startup. Historical terminal rows
    (failed + cancelled_at) are LEFT AS-IS — only new cancels write
    status='cancelled'."""
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    dbp = tmp_path / "happyranch.db"
    db = Database(dbp)
    for tid in ("T-DEL", "T-ESC", "T-JOB", "T-CAN"):
        db.insert_task(TaskRecord(id=tid, brief="x"))
    # Seed pre-migration shapes via raw SQL (bypass the enum write path).
    db._conn.execute("UPDATE tasks SET status='blocked', block_kind='delegated' WHERE id='T-DEL'")
    db._conn.execute("UPDATE tasks SET status='blocked', block_kind='escalated' WHERE id='T-ESC'")
    db._conn.execute("UPDATE tasks SET status='blocked', block_kind='blocked_on_job' WHERE id='T-JOB'")
    db._conn.execute("UPDATE tasks SET status='failed', cancelled_at='2026-01-01T00:00:00Z' WHERE id='T-CAN'")
    db._conn.commit()
    db.close()

    # Re-open → the startup ALTER-ladder + Path-B UPDATEs run over the rows.
    db2 = Database(dbp)
    assert db2.get_task("T-DEL").status == TaskStatus.IN_PROGRESS
    assert db2.get_task("T-DEL").block_kind == BlockKind.DELEGATED
    assert db2.get_task("T-ESC").status == TaskStatus.ESCALATED
    assert db2.get_task("T-ESC").block_kind is None
    assert db2.get_task("T-JOB").status == TaskStatus.IN_PROGRESS
    assert db2.get_task("T-JOB").block_kind == BlockKind.BLOCKED_ON_JOB
    # Historical terminal cancellation LEFT AS-IS (still failed + cancelled_at).
    can = db2.get_task("T-CAN")
    assert can.status == TaskStatus.FAILED
    assert can.cancelled_at is not None
    db2.close()


def test_try_delegate_rejects_cancelled_parent_and_inserts_no_child(db):
    """Atomic CAS: a cancelled parent must not be transitioned back to
    BLOCKED(DELEGATED), AND the child must not be created. This is the
    spawn-new-work race from Codex P1 — the most important variant.
    """
    from datetime import datetime, timezone
    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task(
        "T-PAR", status=TaskStatus.FAILED, cancelled_at=now, completed_at=now,
        note="cancelled by founder",
    )

    child = TaskRecord(
        id="T-CHILD", brief="child work",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    )
    ok = db.try_delegate("T-PAR", child, parent_note="Delegated to dev_agent")
    assert ok is False

    par = db.get_task("T-PAR")
    assert par.status == TaskStatus.FAILED  # unchanged
    assert par.note == "cancelled by founder"  # unchanged
    # CRITICAL: the child must NOT exist. This is the TASK-497 bug shape.
    assert db.get_task("T-CHILD") is None
    assert db.get_children("T-PAR") == []


def test_try_delegate_rejects_terminal_parent(db):
    """COMPLETED parent must not get a new child either."""
    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.COMPLETED, note="done")
    child = TaskRecord(id="T-CHILD", brief="x", parent_task_id="T-PAR")
    ok = db.try_delegate("T-PAR", child, parent_note="late delegate")
    assert ok is False
    assert db.get_task("T-CHILD") is None


def test_try_delegate_rejects_missing_parent(db):
    child = TaskRecord(id="T-CHILD", brief="x", parent_task_id="T-NOPE")
    ok = db.try_delegate("T-NOPE", child, parent_note="x")
    assert ok is False
    assert db.get_task("T-CHILD") is None


def test_tasks_active_chain_column_exists_and_defaults_null(db):
    task = TaskRecord(
        id="TASK-1",
        team="engineering",
        brief="x",
        parent_task_id=None,
    )
    db.insert_task(task)
    retrieved = db.get_task("TASK-1")
    assert retrieved is not None
    assert retrieved.active_chain is None  # column exists, NULL by default

    cursor = db._conn.execute("PRAGMA table_info(tasks)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "active_chain" in cols


def test_update_task_active_chain_sets_and_clears(db):
    db.insert_task(TaskRecord(id="TASK-1", team="engineering", brief="x", parent_task_id=None))

    db.update_task_active_chain("TASK-1", '{"step_index":0,"legs":[],"step_audit_id":1}')
    assert db.get_task("TASK-1").active_chain == '{"step_index":0,"legs":[],"step_audit_id":1}'

    db.update_task_active_chain("TASK-1", None)
    assert db.get_task("TASK-1").active_chain is None


def test_task_type_defaults_to_task():
    from runtime.models import TaskRecord
    t = TaskRecord(id="TASK-001", brief="x")
    assert t.task_type == "task"


def test_task_type_accepts_subtask():
    from runtime.models import TaskRecord
    t = TaskRecord(id="TASK-002", brief="x", task_type="subtask")
    assert t.task_type == "subtask"


def test_legacy_artifact_columns_renamed_and_path_strings_rewritten(tmp_path):
    """2026-06-02 rename: an un-migrated runtime with the OLD column names
    (`final_artifact_dir`, `artifact_dir`) and OLD `artifacts/...` path
    strings must come out of `Database()` init with the NEW columns and
    rewritten paths. This is the daemon-startup-self-migration path that
    replaces the external migration script's column-rename + path rewrite.
    """
    import sqlite3

    db_path = tmp_path / "legacy_artifacts.db"

    # Build the pre-rename schema. Only the columns that the rename touches
    # are reproduced verbatim — Database() init will add the rest via its
    # idempotent ALTER list.
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
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
            final_artifact_dir TEXT
        );
        CREATE TABLE task_results (
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
    """)
    conn.execute(
        "INSERT INTO tasks (id, status, brief, created_at, updated_at, final_artifact_dir) "
        "VALUES (?, 'completed', 'b', '2026-06-02T00:00:00Z', '2026-06-02T00:00:00Z', 'artifacts/TASK-1')",
        ("TASK-1",),
    )
    conn.execute(
        "INSERT INTO task_results (task_id, agent, session_id, artifact_dir, created_at) "
        "VALUES ('TASK-1', 'dev_agent', 'sess-1', 'artifacts/TASK-1', '2026-06-02T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)

    task_cols = {row[1] for row in db._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    result_cols = {row[1] for row in db._conn.execute("PRAGMA table_info(task_results)").fetchall()}

    assert "final_output_dir" in task_cols
    assert "final_artifact_dir" not in task_cols
    assert "output_dir" in result_cols
    assert "artifact_dir" not in result_cols

    final_dir = db._conn.execute("SELECT final_output_dir FROM tasks WHERE id='TASK-1'").fetchone()[0]
    output_dir = db._conn.execute("SELECT output_dir FROM task_results WHERE task_id='TASK-1'").fetchone()[0]
    assert final_dir == "output/TASK-1"
    assert output_dir == "output/TASK-1"

    db.close()

    # Second init must be a no-op (paths already rewritten, columns already renamed).
    db2 = Database(db_path)
    final_dir2 = db2._conn.execute("SELECT final_output_dir FROM tasks WHERE id='TASK-1'").fetchone()[0]
    assert final_dir2 == "output/TASK-1"
    db2.close()


def test_task_type_round_trips(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord
    db = Database(tmp_path / "rt.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="root", task_type="task"))
    db.insert_task(TaskRecord(id="TASK-002", brief="child", task_type="subtask"))
    assert db.get_task("TASK-001").task_type == "task"
    assert db.get_task("TASK-002").task_type == "subtask"
    db.close()


# ---------------------------------------------------------------------------
# get_subtree_statuses — severity rollup derive for Tasks list
# ---------------------------------------------------------------------------

def test_get_subtree_statuses_root_with_no_subtree_returns_empty(db):
    """A root task with zero children returns an empty status list."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="alone"))
    assert db.get_subtree_statuses("ROOT-1") == []


def test_get_subtree_statuses_returns_direct_child_statuses(db):
    """Direct children statuses are collected."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="root"))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.BLOCKED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    result = db.get_subtree_statuses("ROOT-1")
    assert sorted(result) == sorted(["blocked", "completed"])


def test_get_subtree_statuses_walks_deeply_nested_subtree(db):
    """Statuses from grandchild and great-grandchild levels are collected."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="root"))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.PENDING,
    ))
    db.insert_task(TaskRecord(
        id="GRAND-1", brief="gc1", parent_task_id="CHILD-1",
        status=TaskStatus.FAILED,
    ))
    db.insert_task(TaskRecord(
        id="GREAT-1", brief="ggc1", parent_task_id="GRAND-1",
        status=TaskStatus.IN_PROGRESS,
    ))
    result = db.get_subtree_statuses("ROOT-1")
    # Should collect all three descendant statuses.
    assert sorted(result) == sorted(["pending", "failed", "in_progress"])


def test_get_subtree_statuses_multiple_branches(db):
    """Multiple child branches are all traversed."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="root"))
    # Branch A: child -> grandchild
    db.insert_task(TaskRecord(
        id="CHILD-A", brief="ca", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    db.insert_task(TaskRecord(
        id="GRAND-A", brief="ga", parent_task_id="CHILD-A",
        status=TaskStatus.FAILED,
    ))
    # Branch B: child only
    db.insert_task(TaskRecord(
        id="CHILD-B", brief="cb", parent_task_id="ROOT-1",
        status=TaskStatus.BLOCKED,
    ))
    # Branch C: child -> grandchild with subtask
    db.insert_task(TaskRecord(
        id="CHILD-C", brief="cc", parent_task_id="ROOT-1",
        status=TaskStatus.IN_PROGRESS, task_type="subtask",
    ))
    db.insert_task(TaskRecord(
        id="GRAND-C", brief="gc", parent_task_id="CHILD-C",
        status=TaskStatus.RESOLVED_SUPERSEDED,
    ))
    result = db.get_subtree_statuses("ROOT-1")
    assert sorted(result) == sorted([
        "completed", "failed", "blocked", "in_progress", "resolved_superseded",
    ])


# ---------------------------------------------------------------------------
# list_roots — roots-only list with severity rollup
# ---------------------------------------------------------------------------

def test_list_roots_returns_only_root_tasks(db):
    """Tasks with a parent_task_id are excluded."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="r1"))
    db.insert_task(TaskRecord(id="ROOT-2", brief="r2"))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1"
    ))
    result = db.list_roots()
    ids = [t.id for t in result]
    assert "ROOT-1" in ids
    assert "ROOT-2" in ids
    assert "CHILD-1" not in ids


def test_list_roots_includes_severity_rollup(db):
    """Each root carries a _severity_rollup string reflecting worst child status."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="ok", status=TaskStatus.COMPLETED))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.BLOCKED,
    ))
    result = db.list_roots()
    assert len(result) == 1
    root = result[0]
    assert hasattr(root, '_severity_rollup')
    # blocked child → root rollup should be 'blocked'
    assert root._severity_rollup == 'blocked'


def test_list_roots_severity_rollup_root_without_subtree_is_own_status(db):
    """A root without any child tasks reflects its own status as rollup."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="alone", status=TaskStatus.IN_PROGRESS))
    result = db.list_roots()
    assert result[0]._severity_rollup == 'in_progress'


def test_list_roots_severity_rollup_failed_wins_over_completed(db):
    """Failed is worse than completed in the rollup."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="ok", status=TaskStatus.COMPLETED))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.FAILED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    result = db.list_roots()
    assert result[0]._severity_rollup == 'failed'


def test_list_roots_severity_rollup_blocked_wins_over_failed(db):
    """Blocked is the worst severity — blocked > failed > in_progress > pending > completed."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="ok", status=TaskStatus.COMPLETED))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.BLOCKED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.FAILED,
    ))
    result = db.list_roots()
    assert result[0]._severity_rollup == 'blocked'


def test_list_roots_filters_by_status(db):
    """Status filter applied to the root itself, rollup computed on full subtree."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="r1", status=TaskStatus.BLOCKED))
    db.insert_task(TaskRecord(id="ROOT-2", brief="r2", status=TaskStatus.COMPLETED))
    result = db.list_roots(status=TaskStatus.BLOCKED)
    assert len(result) == 1
    assert result[0].id == "ROOT-1"


def test_list_roots_filters_by_agent(db):
    """Assigned agent filter on roots."""
    db.insert_task(TaskRecord(
        id="ROOT-1", brief="r1", assigned_agent="dev_agent"
    ))
    db.insert_task(TaskRecord(
        id="ROOT-2", brief="r2", assigned_agent="qa_engineer"
    ))
    result = db.list_roots(assigned_agent="dev_agent")
    assert [t.id for t in result] == ["ROOT-1"]


def test_list_roots_severity_rollup_ignores_revisit_chain(db):
    """The rollup is ONLY on the parent_task_id subtree, not revisit predecessors."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="r1", status=TaskStatus.COMPLETED))
    # This task revisits ROOT-1 (it's a successor, not a child)
    db.insert_task(TaskRecord(
        id="ROOT-2", brief="r2", status=TaskStatus.FAILED,
        revisit_of_task_id="ROOT-1",
    ))
    result = db.list_roots()
    # ROOT-1's rollup should be its own status (COMPLETED), not FAILED from the revisit.
    root1 = [r for r in result if r.id == "ROOT-1"][0]
    assert root1._severity_rollup == 'completed'
