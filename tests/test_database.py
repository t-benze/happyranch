import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.database import (
    Database,
    LineageTooDeep,
    WorkspaceCleanupReclamationSelection,
)
from runtime.models import BlockKind, TaskRecord, TaskStatus


_CLEANUP_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"


def _cleanup_task(
    db: Database,
    task_id: str,
    *,
    created_at: datetime,
    status: TaskStatus,
    count: int = 0,
    completed_at: datetime | None = None,
    parent_task_id: str | None = None,
    revisit_of_task_id: str | None = None,
) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief=f"{_CLEANUP_MARKER}\nfixture",
        assigned_agent="dev_agent",
        status=status,
        orchestration_step_count=count,
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
        current_session_id=f"session-{task_id}",
        parent_task_id=parent_task_id,
        revisit_of_task_id=revisit_of_task_id,
    ))


def _selection_fixture(db: Database, *, targets: int = 1) -> tuple[datetime, list[TaskRecord | None]]:
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    for ordinal in (1, 2):
        _cleanup_task(
            db, f"TASK-OLDER-{ordinal}", created_at=now - timedelta(days=ordinal),
            status=TaskStatus.PENDING,
        )
    _cleanup_task(
        db, "TASK-OWNER", created_at=now, status=TaskStatus.IN_PROGRESS, count=1,
    )
    db.insert_audit_log(
        task_id="TASK-OWNER", agent="dev_agent", action="workspace_cleanup_triggered",
        payload={"run_number": 3, "brief_kind": "cleanup"},
    )
    target_rows = []
    for ordinal in range(targets):
        target_id = f"TASK-{1000 + ordinal}"
        completed_at = now - timedelta(days=10 + ordinal)
        db.insert_task(TaskRecord(
            id=target_id, brief="ordinary completed task", assigned_agent="dev_agent",
            status=TaskStatus.COMPLETED, created_at=completed_at - timedelta(hours=1),
            updated_at=completed_at - timedelta(hours=1), completed_at=completed_at,
            current_session_id=f"session-{target_id}",
        ))
        db.insert_task_result(
            task_id=target_id, agent="dev_agent", session_id=f"session-{target_id}",
            output_summary="done", confidence_score=90, status="completed",
        )
        target_rows.append(db.get_task(target_id))
    return now, target_rows


def _select(db: Database, tmp_path, admissions=None, *, stale_count: int = 0, claimed_count: int = 1):
    workspace = tmp_path / "runtime" / "workspaces" / "dev_agent"
    return db.select_workspace_cleanup_reclamation_candidates(
        owner_task_id="TASK-OWNER", agent="dev_agent",
        stale_orchestration_step_count=stale_count, claimed_next_step_count=claimed_count,
        canonical_workspace=workspace, authoritative_workspace=workspace,
        admit_observation=admissions,
    )


def test_workspace_cleanup_selection_is_bounded_and_exposes_each_real_read(db, tmp_path) -> None:
    _selection_fixture(db)
    statements: list[str] = []
    admissions: list[str] = []
    db._conn.set_trace_callback(statements.append)
    selection = _select(db, tmp_path, lambda name: admissions.append(name) is None or True)
    db._conn.set_trace_callback(None)

    assert isinstance(selection, WorkspaceCleanupReclamationSelection), admissions
    assert [candidate.task_id for candidate in selection.candidates] == ["TASK-1000"]
    assert selection.candidates[0].scratch_path == (
        tmp_path / "runtime" / "workspaces" / "dev_agent" / ".happyranch" / "task-tmp" / "TASK-1000"
    )
    assert selection.read_observations == (
        "owner", "marker", "history", "newer_owner", "candidates", "graph_tasks", "graph_edges", "result:TASK-1000",
    )
    assert len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) == 8


def test_workspace_cleanup_selection_refuses_before_later_reads_on_bad_owner_or_marker(db, tmp_path) -> None:
    _selection_fixture(db)
    admitted: list[str] = []
    assert _select(
        db, tmp_path, lambda name: admitted.append(name) is None or True, stale_count=1,
    ) is None
    assert admitted == ["owner"]

    db.update_task("TASK-OWNER", orchestration_step_count=2)
    admitted.clear()
    assert _select(db, tmp_path, lambda name: admitted.append(name) is None or True) is None
    assert admitted == ["owner"]

    db.update_task("TASK-OWNER", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-OWNER", agent="foreign", action="workspace_cleanup_triggered",
        payload={"run_number": 3, "brief_kind": "cleanup"},
    )
    admitted.clear()
    assert _select(db, tmp_path, lambda name: admitted.append(name) is None or True) is None
    assert admitted == ["owner", "marker"]


def test_workspace_cleanup_selection_refuses_sixth_raw_before_age_filter_without_refill(db, tmp_path) -> None:
    now, _targets = _selection_fixture(db, targets=5)
    db.insert_task(TaskRecord(
        id="TASK-NEWER", brief="ordinary completed task", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, created_at=now + timedelta(minutes=1),
        updated_at=now + timedelta(minutes=1), completed_at=now + timedelta(minutes=1),
        current_session_id="session-TASK-NEWER",
    ))
    # The newer row is raw candidate number six.  It cannot be filtered away
    # to make the five older roots actionable.
    value, selects = _selection_sql(db, tmp_path)
    assert value is None
    assert len(selects) == 5
    assert "LIMIT 6" in selects[-1]


def test_workspace_cleanup_selection_refuses_newer_marker_and_relevant_foreign_live_graph(db, tmp_path) -> None:
    now, targets = _selection_fixture(db)
    _cleanup_task(
        db, "TASK-LATER", created_at=now + timedelta(minutes=1),
        status=TaskStatus.IN_PROGRESS, count=1,
    )
    db.insert_audit_log(
        task_id="TASK-LATER", agent="dev_agent", action="workspace_cleanup_triggered",
        payload={"run_number": 4, "brief_kind": "cleanup"},
    )
    assert _select(db, tmp_path) is None

    db.execute("DELETE FROM audit_log WHERE task_id='TASK-LATER'")
    db.execute("DELETE FROM tasks WHERE id='TASK-LATER'")
    target = targets[0]
    assert target is not None
    db.insert_task(TaskRecord(
        id="TASK-FOREIGN-LIVE", brief="ordinary", assigned_agent="foreign",
        status=TaskStatus.PENDING, created_at=now - timedelta(days=11),
        updated_at=now - timedelta(days=11),
    ))
    db.execute(
        "UPDATE tasks SET parent_task_id='TASK-FOREIGN-LIVE' WHERE id=?",
        (target.id,),
    )
    assert _select(db, tmp_path) is None


def test_workspace_cleanup_selection_admission_and_missing_result_stop_later_reads(db, tmp_path) -> None:
    _selection_fixture(db, targets=2)
    # Candidate order is oldest completed first, so target 1 is checked first.
    db.execute("DELETE FROM task_results WHERE task_id='TASK-1001'")
    admitted: list[str] = []
    assert _select(db, tmp_path, lambda name: admitted.append(name) is None or True) is None
    assert admitted[-1] == "result:TASK-1001"
    assert "result:TASK-1000" not in admitted

    admitted.clear()
    def stop_at_candidates(name: str) -> bool:
        admitted.append(name)
        return name != "candidates"

    assert _select(db, tmp_path, stop_at_candidates) is None
    # The denied observation is not performed and no later admission is made.
    assert admitted == ["owner", "marker", "history", "newer_owner", "candidates"]


def test_workspace_cleanup_selection_refuses_malformed_and_noncanonical_observations(db, tmp_path) -> None:
    now, _ = _selection_fixture(db)
    db.execute("UPDATE tasks SET created_at='!invalid' WHERE id='TASK-OWNER'")
    assert _select(db, tmp_path) is None

    db.close()
    db = Database(tmp_path / "selection.sqlite")
    now, _ = _selection_fixture(db)
    db.execute("UPDATE tasks SET id='/tmp/escape' WHERE id='TASK-1000'")
    assert _select(db, tmp_path) is None


def test_workspace_cleanup_selection_includes_terminal_cleanup_rows_in_raw_six(db, tmp_path) -> None:
    now, _ = _selection_fixture(db, targets=4)
    for task_id in ("TASK-3000", "TASK-3001"):
        _cleanup_task(db, task_id, created_at=now - timedelta(days=20),
                      completed_at=now - timedelta(days=20), status=TaskStatus.COMPLETED)
        db.insert_task_result(task_id=task_id, agent="dev_agent", session_id=f"session-{task_id}",
                              output_summary="done", confidence_score=90, status="completed")
    # Owner plus two original older cleanup rows and these two terminal cleanup
    # rows is a complete ordinal-five history.  Only then can this regression
    # reach the sixth raw candidate observation it names.
    db.execute(
        "UPDATE audit_log SET payload=? WHERE task_id='TASK-OWNER'",
        ('{"run_number": 5, "brief_kind": "cleanup"}',),
    )
    admissions: list[str] = []
    assert _select(db, tmp_path, lambda name: admissions.append(name) is None or True) is None
    assert admissions == ["owner", "marker", "history", "newer_owner", "candidates"]


def _selection_sql(db: Database, tmp_path, **kwargs):
    """Run the helper with literal SQLite observation accounting."""
    statements: list[str] = []
    db._conn.set_trace_callback(statements.append)
    try:
        value = _select(db, tmp_path, **kwargs)
    finally:
        db._conn.set_trace_callback(None)
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    return value, selects


@pytest.mark.parametrize(
    ("stop", "expected_reads"),
    [
        ("owner", 0), ("marker", 1), ("history", 2), ("newer_owner", 3),
        ("candidates", 4), ("graph_tasks", 5), ("graph_edges", 6),
        ("result:TASK-1000", 7),
    ],
)
def test_workspace_cleanup_selection_sql_errors_refuse_without_later_reads(
    db, tmp_path, stop, expected_reads,
) -> None:
    _selection_fixture(db)
    admissions: list[str] = []

    def admit(name: str) -> bool:
        admissions.append(name)
        if name == stop:
            db._conn.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        return True

    try:
        value, selects = _selection_sql(db, tmp_path, admissions=admit)
    finally:
        db._conn.set_authorizer(None)
    assert value is None
    assert admissions[-1] == stop
    assert len(selects) == expected_reads


@pytest.mark.parametrize("result_index", range(5))
def test_workspace_cleanup_selection_each_selected_result_sql_error_stops_sql(
    db, tmp_path, result_index,
) -> None:
    _selection_fixture(db, targets=5)
    result_names = [f"result:TASK-{1004 - ordinal}" for ordinal in range(5)]
    stop = result_names[result_index]
    admissions: list[str] = []

    def admit(name: str) -> bool:
        admissions.append(name)
        if name == stop:
            db._conn.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        return True

    try:
        value, selects = _selection_sql(db, tmp_path, admissions=admit)
    finally:
        db._conn.set_authorizer(None)
    assert value is None
    assert admissions[-1] == stop
    assert len(selects) == 7 + result_index


def test_workspace_cleanup_selection_all_twelve_pre_admission_denials_stop_sql(db, tmp_path) -> None:
    _selection_fixture(db, targets=5)
    denied_index = []
    for stop in range(12):
        admissions: list[str] = []

        def admit(name: str, *, index=stop) -> bool:
            admissions.append(name)
            return len(admissions) - 1 != index

        value, selects = _selection_sql(db, tmp_path, admissions=admit)
        assert value is None
        assert len(admissions) == stop + 1
        assert len(selects) == stop
        denied_index.append(admissions[-1])
    assert denied_index[:7] == [
        "owner", "marker", "history", "newer_owner", "candidates", "graph_tasks", "graph_edges",
    ]
    assert all(name.startswith("result:TASK-") for name in denied_index[7:])


@pytest.mark.parametrize(
    "malformed_created_at",
    [
        "2025-99-99T00:00:00+00:00",
        "0000-01-01T00:00:00+00:00",
        "2025-01-01T00:00:00",
    ],
)
def test_workspace_cleanup_selection_refuses_malformed_marker_ordering_before_candidates(
    db, tmp_path, malformed_created_at,
) -> None:
    now, _ = _selection_fixture(db)
    db.insert_task(TaskRecord(
        id="TASK-MALFORMED-MARKER", brief="ordinary", assigned_agent="dev_agent",
        status=TaskStatus.PENDING, created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    ))
    db.execute(
        "UPDATE tasks SET created_at=? WHERE id='TASK-MALFORMED-MARKER'",
        (malformed_created_at,),
    )
    db.insert_audit_log(
        task_id="TASK-MALFORMED-MARKER", agent="dev_agent",
        action="workspace_cleanup_triggered",
        payload={"run_number": 4, "brief_kind": "cleanup"},
    )
    value, selects = _selection_sql(db, tmp_path)
    assert value is None
    assert len(selects) == 4
    assert not any("status IN ('completed'" in sql for sql in selects)


def test_workspace_cleanup_selection_deep_acyclic_component_is_iterative(db, tmp_path) -> None:
    """A real 10,000-row graph must not rely on the Python recursion limit."""
    now, targets = _selection_fixture(db, targets=3)
    assert all(target is not None for target in targets)
    chain_rows = [
        (
            f"TASK-DEEP-{ordinal}", "ordinary", "pending", "dev_agent",
            now.isoformat(), now.isoformat(),
            f"TASK-DEEP-{ordinal + 1}" if ordinal < 9993 else None,
        )
        for ordinal in range(9994)
    ]
    db._conn.executemany(
        "INSERT INTO tasks (id,brief,status,assigned_agent,created_at,updated_at,parent_task_id) "
        "VALUES (?,?,?,?,?,?,?)",
        chain_rows,
    )
    db._conn.commit()
    db.execute("UPDATE tasks SET parent_task_id='TASK-DEEP-0' WHERE id='TASK-1000'")
    value, selects = _selection_sql(db, tmp_path)
    assert value is not None
    assert len(value.candidates) == 3
    assert len(selects) == 10


def test_workspace_cleanup_selection_five_raw_includes_terminal_cleanup_and_no_refill(db, tmp_path) -> None:
    now, _ = _selection_fixture(db, targets=3)
    for task_id in ("TASK-3000", "TASK-3001"):
        _cleanup_task(
            db, task_id, created_at=now - timedelta(days=20),
            completed_at=now - timedelta(days=20), status=TaskStatus.COMPLETED,
        )
        db.insert_task_result(
            task_id=task_id, agent="dev_agent", session_id=f"session-{task_id}",
            output_summary="done", confidence_score=90, status="completed",
        )
    db.execute(
        "UPDATE audit_log SET payload=? WHERE task_id='TASK-OWNER'",
        ('{"run_number": 5, "brief_kind": "cleanup"}',),
    )
    value, selects = _selection_sql(db, tmp_path)
    assert value is not None
    assert len(value.candidates) == 5
    assert len(selects) == 12
    # A sixth raw row refuses even when it would be age-rejected; the helper
    # cannot refill filtered slots with a later page.
    db.insert_task(TaskRecord(
        id="TASK-TOO-NEW", brief="ordinary", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, created_at=now + timedelta(days=1),
        updated_at=now + timedelta(days=1), completed_at=now + timedelta(days=1),
        current_session_id="session-TASK-TOO-NEW",
    ))
    refused, refused_selects = _selection_sql(db, tmp_path)
    assert refused is None
    assert len(refused_selects) == 5


@pytest.mark.parametrize("count", [1000, 1001])
def test_workspace_cleanup_selection_history_boundary_is_complete(db, tmp_path, count) -> None:
    now, _ = _selection_fixture(db)
    for ordinal in range(3, count):
        _cleanup_task(
            db, f"TASK-HISTORY-{ordinal}", created_at=now - timedelta(days=ordinal),
            status=TaskStatus.PENDING,
        )
    db.execute(
        "UPDATE audit_log SET payload=? WHERE task_id='TASK-OWNER'",
        (f'{{"run_number": {count}, "brief_kind": "cleanup"}}',),
    )
    value, selects = _selection_sql(db, tmp_path)
    assert (value is not None) is (count == 1000)
    if count == 1001:
        assert len(selects) == 3


@pytest.mark.parametrize("count", [10000, 10001])
def test_workspace_cleanup_selection_graph_task_boundary(db, tmp_path, count) -> None:
    now, _ = _selection_fixture(db)
    rows = [
        (f"TASK-UNRELATED-{ordinal}", "ordinary", "broken-unrelated", "foreign", now.isoformat(), now.isoformat())
        for ordinal in range(count - 4)
    ]
    db._conn.executemany(
        "INSERT INTO tasks (id,brief,status,assigned_agent,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        rows,
    )
    db._conn.commit()
    value, selects = _selection_sql(db, tmp_path)
    assert (value is not None) is (count == 10000)
    if count == 10001:
        assert len(selects) == 6


@pytest.mark.parametrize("count", [20000, 20001])
def test_workspace_cleanup_selection_edge_boundary_is_explicit_cursor_unit(db, tmp_path, count) -> None:
    """Synthetic cursor boundary only; real SQL cannot form a valid 20k-edge graph."""
    now, _ = _selection_fixture(db)
    db.insert_task(TaskRecord(
        id="TASK-UNRELATED-EDGE", brief="ordinary", assigned_agent="foreign",
        status=TaskStatus.PENDING, created_at=now, updated_at=now,
        parent_task_id="TASK-UNRELATED-EDGE", revisit_of_task_id="TASK-UNRELATED-EDGE",
    ))
    original = db._conn
    actual_sizes: list[int] = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __getattr__(self, key):
            return getattr(original, key)

        def execute(self, sql, *args):
            cursor = original.execute(sql, *args)
            if "SELECT child_id, relative_id FROM (" in sql:
                rows = cursor.fetchall()
                actual_sizes.append(len(rows))
                return Cursor([rows[0]] * count)
            return cursor

    db._conn = Connection()
    try:
        value, selects = _selection_sql(db, tmp_path)
    finally:
        db._conn = original
    assert actual_sizes == [2]
    assert (value is not None) is (count == 20000)
    assert len(selects) == (8 if count == 20000 else 7)


# The following finite matrix is repository-resident regression coverage for
# the dormant helper.  It deliberately models the future hook's post-selection
# config/owner reads without importing or implementing that hook.
@pytest.fixture
def cleanup_selection_matrix(tmp_path):
    database = Database(tmp_path / "cleanup-selection-matrix.sqlite")
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)

    def insert(task_id, *, cleanup=False, agent="dev_agent", status="completed", age=10):
        when = now - timedelta(days=age)
        database.insert_task(TaskRecord(
            id=task_id, brief=_CLEANUP_MARKER if cleanup else "ordinary",
            assigned_agent=agent, status=TaskStatus(status), created_at=when,
            updated_at=when, completed_at=when if status == "completed" else None,
            orchestration_step_count=1 if task_id == "TASK-100" else 0,
            current_session_id=f"session-{task_id}",
        ))
        if status == "completed":
            database.insert_task_result(
                task_id=task_id, agent=agent, session_id=f"session-{task_id}",
                output_summary="done", confidence_score=90, status="completed",
            )

    insert("TASK-1", cleanup=True, age=20)
    insert("TASK-2", cleanup=True, age=19)
    insert("TASK-100", cleanup=True, status="in_progress", age=0)
    database.insert_audit_log(
        task_id="TASK-100", agent="dev_agent", action="workspace_cleanup_triggered",
        payload={"run_number": 3, "brief_kind": "cleanup"},
    )
    insert("TASK-10")
    queries: list[str] = []

    def select(**kwargs):
        queries.clear()
        database._conn.set_trace_callback(queries.append)
        try:
            options = dict(
                owner_task_id="TASK-100", agent="dev_agent",
                stale_orchestration_step_count=0, claimed_next_step_count=1,
                canonical_workspace=tmp_path / "workspaces" / "dev_agent",
                authoritative_workspace=tmp_path / "workspaces" / "dev_agent",
            )
            options.update(kwargs)
            return database.select_workspace_cleanup_reclamation_candidates(**options)
        finally:
            database._conn.set_trace_callback(None)

    yield database, insert, select, queries, now
    database.close()


@pytest.mark.parametrize("value", [
    "2025-02-29T00:00:00+00:00", "2025-02-30T00:00:00+00:00",
    "2025-04-31T00:00:00+00:00", "2025-01-01T24:00:00+00:00",
])
def test_workspace_cleanup_selection_newer_marker_uses_parser_for_invalid_calendar(
    cleanup_selection_matrix, value,
) -> None:
    database, insert, select, queries, _ = cleanup_selection_matrix
    insert("TASK-200", status="pending")
    database.execute("UPDATE tasks SET created_at=? WHERE id='TASK-200'", (value,))
    database.insert_audit_log(task_id="TASK-200", agent="dev_agent",
        action="workspace_cleanup_triggered", payload={"run_number": 4, "brief_kind": "cleanup"})
    assert select() is None
    assert len(queries) == 4


@pytest.mark.parametrize("value", [
    "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00.123456+00:00",
    "2025-01-01T00:00:00.123Z", "2025-01-01 00:00:00+00:00",
    "2025-01-01T00:00:00+23:00", "20250101T000000+0000",
    "2025-01-01T00:00:00+00:00:01",
])
def test_workspace_cleanup_selection_newer_marker_accepts_all_parser_valid_forms(
    cleanup_selection_matrix, value,
) -> None:
    database, _, select, queries, _ = cleanup_selection_matrix
    database.execute("UPDATE tasks SET created_at=? WHERE id='TASK-1'", (value,))
    database.insert_audit_log(task_id="TASK-1", agent="dev_agent",
        action="workspace_cleanup_triggered", payload={"run_number": 1, "brief_kind": "report_only"})
    selection = select()
    assert selection is not None and len(selection.candidates) == 3
    assert len(queries) == 10


def test_workspace_cleanup_selection_marker_blob_refuses_without_later_sql(cleanup_selection_matrix) -> None:
    database, _, select, queries, _ = cleanup_selection_matrix
    database.execute("UPDATE audit_log SET payload=?", (b"\x80",))
    assert select() is None
    assert len(queries) == 2
    database.execute("UPDATE audit_log SET payload=?", (b'{"run_number":3,"brief_kind":"cleanup"}',))
    assert select() is not None


@pytest.mark.parametrize("status", ["pending", "in_progress", "escalated", "completed", "failed", "cancelled", "superseded"])
def test_workspace_cleanup_selection_all_newer_statuses_refuse(cleanup_selection_matrix, status) -> None:
    database, insert, select, queries, _ = cleanup_selection_matrix
    insert("TASK-200", cleanup=True, status=status, age=-1)
    database.insert_audit_log(task_id="TASK-200", agent="dev_agent",
        action="workspace_cleanup_triggered", payload={"run_number": 4, "brief_kind": "cleanup"})
    assert select() is None and len(queries) == 4


@pytest.mark.parametrize("change", ["missing", "cancelled", "state", "block", "agent", "count", "stale", "claimed", "workspace"])
def test_workspace_cleanup_selection_owner_and_claim_mismatches_stop_at_owner(cleanup_selection_matrix, change) -> None:
    database, _, select, queries, now = cleanup_selection_matrix
    kwargs = {}
    if change == "missing":
        database.execute("DELETE FROM tasks WHERE id='TASK-100'")
    elif change == "cancelled":
        database.execute("UPDATE tasks SET cancelled_at=? WHERE id='TASK-100'", (now.isoformat(),))
    elif change == "state":
        database.execute("UPDATE tasks SET status='pending' WHERE id='TASK-100'")
    elif change == "block":
        database.execute("UPDATE tasks SET block_kind='delegated' WHERE id='TASK-100'")
    elif change == "agent":
        database.execute("UPDATE tasks SET assigned_agent='foreign' WHERE id='TASK-100'")
    elif change == "count":
        database.execute("UPDATE tasks SET orchestration_step_count=2 WHERE id='TASK-100'")
    elif change == "stale":
        kwargs["stale_orchestration_step_count"] = 1
    elif change == "claimed":
        kwargs["claimed_next_step_count"] = 2
    else:
        kwargs["authoritative_workspace"] = Path("/other")
    assert select(**kwargs) is None
    assert len(queries) == 1


@pytest.mark.parametrize("kind", ["zero", "two", "mixed", "wrong", "list", "bad_json", "bool", "kind", "first", "second"])
def test_workspace_cleanup_selection_marker_identity_and_ordinal_refuse(cleanup_selection_matrix, kind) -> None:
    database, _, select, queries, _ = cleanup_selection_matrix
    if kind == "zero":
        database.execute("DELETE FROM audit_log")
    elif kind in {"two", "mixed"}:
        database.insert_audit_log(task_id="TASK-100", agent="foreign" if kind == "mixed" else "dev_agent",
            action="workspace_cleanup_triggered", payload={"run_number": 3, "brief_kind": "cleanup"})
    elif kind == "wrong":
        database.execute("UPDATE audit_log SET agent='foreign'")
    else:
        payload = {"list":"[]", "bad_json":"not-json", "bool":'{"run_number":true,"brief_kind":"cleanup"}',
                   "kind":'{"run_number":3,"brief_kind":"report"}', "first":'{"run_number":1,"brief_kind":"cleanup"}',
                   "second":'{"run_number":2,"brief_kind":"cleanup"}'}[kind]
        database.execute("UPDATE audit_log SET payload=?", (payload,))
    assert select() is None and len(queries) == 2


def test_workspace_cleanup_selection_filtered_five_no_refill_and_future_hook_budget_model(cleanup_selection_matrix) -> None:
    database, insert, select, queries, now = cleanup_selection_matrix
    database.execute("UPDATE tasks SET status='pending', completed_at=NULL WHERE id IN ('TASK-1', 'TASK-2')")
    insert("TASK-11", age=21)
    insert("TASK-12", age=-1)
    insert("TASK-13", age=-2)
    insert("TASK-14", age=22)
    selection = select()
    assert selection is not None and [item.task_id for item in selection.candidates] == ["TASK-14", "TASK-11", "TASK-10"]
    assert len(queries) == 10
    database.execute("DELETE FROM tasks WHERE id IN ('TASK-12', 'TASK-13')")
    database.execute("UPDATE tasks SET status='completed', completed_at=created_at WHERE id IN ('TASK-1', 'TASK-2')")
    selection = select()
    assert selection is not None and len(selection.candidates) == 5 and len(queries) == 12
    reads, config_loads, calls = 12, 1, []
    for candidate in selection.candidates:
        config_loads += 1
        owner = database.get_task("TASK-100")
        reads += 1
        assert owner is not None and owner.orchestration_step_count == 1
        calls.append(candidate.task_id)
    assert reads == 17 and config_loads == 6 and len(calls) == 5
    # Model only: five helper owner rereads plus six config loads make 23;
    # prospective observation 24 performs neither a SQL read nor a load.
    assert reads + config_loads == 23
    before = (reads, config_loads)
    assert reads + config_loads >= 23
    assert before == (reads, config_loads)


@pytest.mark.parametrize("variant", ["parent", "revisit", "mixed", "self", "longer", "missing", "owner"])
def test_workspace_cleanup_selection_relevant_graph_cycles_and_relatives_refuse(cleanup_selection_matrix, variant) -> None:
    database, insert, select, queries, _ = cleanup_selection_matrix
    insert("TASK-20", status="pending")
    insert("TASK-30", status="pending")
    if variant == "parent":
        database.execute("UPDATE tasks SET parent_task_id='TASK-20' WHERE id='TASK-10'")
        database.execute("UPDATE tasks SET parent_task_id='TASK-10' WHERE id='TASK-20'")
    elif variant == "revisit":
        database.execute("UPDATE tasks SET revisit_of_task_id='TASK-20' WHERE id='TASK-10'")
        database.execute("UPDATE tasks SET revisit_of_task_id='TASK-10' WHERE id='TASK-20'")
    elif variant == "mixed":
        database.execute("UPDATE tasks SET parent_task_id='TASK-20' WHERE id='TASK-10'")
        database.execute("UPDATE tasks SET revisit_of_task_id='TASK-10' WHERE id='TASK-20'")
    elif variant == "self":
        database.execute("UPDATE tasks SET parent_task_id=id WHERE id='TASK-10'")
    elif variant == "longer":
        database.execute("UPDATE tasks SET parent_task_id='TASK-20' WHERE id='TASK-10'")
        database.execute("UPDATE tasks SET parent_task_id='TASK-30' WHERE id='TASK-20'")
        database.execute("UPDATE tasks SET parent_task_id='TASK-10' WHERE id='TASK-30'")
    elif variant == "missing":
        database.execute("UPDATE tasks SET parent_task_id='TASK-404' WHERE id='TASK-10'")
    else:
        database.execute("UPDATE tasks SET parent_task_id='TASK-100' WHERE id='TASK-10'")
    assert select() is None and len(queries) == 7


@pytest.mark.parametrize("change", ["task", "agent", "session", "terminal", "decode"])
def test_workspace_cleanup_selection_requires_exact_current_terminal_result(cleanup_selection_matrix, change) -> None:
    database, _, select, queries, _ = cleanup_selection_matrix
    column, value = {
        "task": ("task_id", "TASK-404"), "agent": ("agent", "foreign"),
        "session": ("session_id", "old-session"), "terminal": ("status", "failed"),
        "decode": ("risks_flagged", "bad-json"),
    }[change]
    database.execute(f"UPDATE task_results SET {column}=? WHERE task_id='TASK-1'", (value,))
    assert select() is None and len(queries) == 8


@pytest.mark.parametrize("column,value,late", [
    ("created_at", "!bad", False), ("completed_at", None, False),
    ("completed_at", "!bad", False), ("completed_at", "2099-bad", True),
])
def test_workspace_cleanup_selection_validates_all_raw_candidate_times_before_age_filter(
    cleanup_selection_matrix, column, value, late,
) -> None:
    database, _, select, queries, now = cleanup_selection_matrix
    if late:
        database.execute("UPDATE tasks SET completed_at=? WHERE id='TASK-10'", ((now + timedelta(days=1)).isoformat(),))
    database.execute(f"UPDATE tasks SET {column}=? WHERE id='TASK-10'", (value,))
    assert select() is None and len(queries) == 5


@pytest.mark.parametrize("kind", ["orphan", "duplicate"])
def test_workspace_cleanup_selection_orphan_and_duplicate_newer_evidence_refuse(cleanup_selection_matrix, kind) -> None:
    database, insert, select, queries, _ = cleanup_selection_matrix
    if kind == "orphan":
        database.insert_audit_log(task_id="TASK-404", agent="dev_agent",
            action="workspace_cleanup_triggered", payload={"run_number": 4, "brief_kind": "cleanup"})
    else:
        insert("TASK-200", cleanup=True, status="pending", age=-1)
        database.insert_audit_log(task_id="TASK-200", agent="dev_agent",
            action="workspace_cleanup_triggered", payload={"run_number": 4, "brief_kind": "cleanup"})
        database.insert_audit_log(task_id="TASK-200", agent="dev_agent",
            action="workspace_cleanup_triggered", payload={"run_number": 4, "brief_kind": "cleanup"})
    assert select() is None and len(queries) == 4


def test_workspace_cleanup_selection_timestamp_scalar_is_removed_after_success_and_sql_error(cleanup_selection_matrix) -> None:
    database, _, select, _, _ = cleanup_selection_matrix
    original = database._conn
    row_factory, isolation, changes = original.row_factory, original.isolation_level, original.total_changes
    assert select() is not None
    with pytest.raises(sqlite3.OperationalError):
        original.execute("SELECT _workspace_cleanup_is_aware_datetime('2026-01-01T00:00:00+00:00')").fetchone()
    original.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    try:
        assert select() is None
    finally:
        original.set_authorizer(None)
    assert database._conn is original
    assert original.row_factory is row_factory and original.isolation_level == isolation and original.total_changes == changes


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


def _recovery_task(db: Database, task_id: str = "TASK-RECOVERY") -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="recovery", status=TaskStatus.IN_PROGRESS,
        assigned_agent="dev_agent", current_session_id="origin",
    ))


def test_completion_recovery_claim_requires_current_assigned_origin_and_one_winner(db):
    """Concurrent same-origin claimers cannot mint two recovery episodes."""
    _recovery_task(db)
    gate = threading.Barrier(2, timeout=2)
    results: list[bool] = []

    def claim() -> None:
        gate.wait(timeout=2)
        results.append(db.claim_task_completion_recovery(
            task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
            recovery_session_id="recovery", provider_session_id="provider",
            claimed_at="2026-01-01T00:00:00+00:00",
            expires_at="2999-01-01T00:02:00+00:00",
        ))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert sorted(results) == [False, True]
    assert db.execute("SELECT COUNT(*) FROM task_completion_recoveries").fetchone()[0] == 1
    assert not db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="foreign",
        recovery_session_id="second", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )


def test_completion_callback_before_claim_is_the_only_winner(db):
    """A committed origin callback prevents a later recovery claim."""
    _recovery_task(db)
    assert db.admit_task_completion_callback(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="origin",
        output_summary="landed", confidence_score=90,
    )
    assert not db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )
    assert len(db.get_task_results("TASK-RECOVERY")) == 1


def test_completion_claim_before_waiting_origin_callback_rejects_origin(db):
    """A durable claim fences an origin callback that was waiting to commit."""
    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    assert not db.admit_task_completion_callback(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="origin",
        output_summary="late", confidence_score=90,
    )
    assert db.get_task_results("TASK-RECOVERY") == []


def test_completion_callback_and_claim_compete_concurrently_for_one_winner(db):
    """Real SQLite admission and claim race; neither worker is a sequential probe."""
    _recovery_task(db)
    gate = threading.Barrier(2, timeout=2)
    done = threading.Barrier(2, timeout=2)
    outcomes: list[tuple[str, bool]] = []
    errors: list[BaseException] = []

    def callback() -> None:
        try:
            gate.wait()
            outcomes.append(("callback", db.admit_task_completion_callback(
                task_id="TASK-RECOVERY", agent="dev_agent", session_id="origin",
                output_summary="raced", confidence_score=90,
            )))
            done.wait()
        except BaseException as exc:  # captured worker failures are test failures
            errors.append(exc)

    def claim() -> None:
        try:
            gate.wait()
            outcomes.append(("claim", db.claim_task_completion_recovery(
                task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
                recovery_session_id="recovery", provider_session_id="provider",
                claimed_at="2026-01-01T00:00:00+00:00", expires_at="2026-01-01T00:02:00+00:00",
            )))
            done.wait()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=callback), threading.Thread(target=claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert errors == []
    assert sorted(won for _kind, won in outcomes) == [False, True]
    # The durable rows identify the same sole winner observed by the callers.
    assert bool(db.get_task_results("TASK-RECOVERY")) != bool(
        db.execute("SELECT COUNT(*) FROM task_completion_recoveries").fetchone()[0]
    )


def test_completion_accepted_identity_survives_reopen_and_expiry_loses(db, monkeypatch):
    """Acceptance stores an immutable result id independently of settlement."""
    import runtime.infrastructure.database as database_module

    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="recovery")
    monkeypatch.setattr(database_module, "_now", lambda: database_module._parse_dt("2026-01-01T00:01:59+00:00"))
    assert db.admit_task_completion_callback(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
        output_summary="accepted", confidence_score=90,
    )
    row = db.execute(
        "SELECT accepted_result_id, state FROM task_completion_recoveries"
    ).fetchone()
    assert row["accepted_result_id"] is not None and row["state"] == "callback_accepted"
    assert not db.settle_expired_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
        settled_at="2026-01-01T00:02:01+00:00",
    )
    db.close()
    reopened = Database(db.db_path)
    try:
        preserved = reopened.execute(
            "SELECT accepted_result_id, state FROM task_completion_recoveries"
        ).fetchone()
        result = reopened.get_latest_task_result("TASK-RECOVERY", "dev_agent", "recovery")
        assert preserved["accepted_result_id"] == result["id"]
        assert preserved["state"] == "callback_accepted"
    finally:
        reopened.close()


def test_completion_callback_rechecks_injectable_clock_after_db_lock_delay(db, monkeypatch):
    """A callback queued on the DB lock cannot use its pre-wait time."""
    import runtime.infrastructure.database as database_module

    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="recovery")
    entered = threading.Event()
    result: list[bool] = []

    def callback() -> None:
        entered.set()
        result.append(db.admit_task_completion_callback(
            task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
            output_summary="late after lock", confidence_score=90,
        ))

    with db._lock:
        worker = threading.Thread(target=callback)
        worker.start()
        assert entered.wait(timeout=2)
        monkeypatch.setattr(
            database_module, "_now",
            lambda: database_module._parse_dt("2026-01-01T00:02:01+00:00"),
        )
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [False]
    assert db.get_task_results("TASK-RECOVERY") == []


def test_completion_callback_rechecks_live_monotonic_deadline_after_db_lock_delay(db, monkeypatch):
    """A live recovery deadline is checked only after the admission lock wins."""
    import runtime.infrastructure.database as database_module

    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="recovery")
    entered = threading.Event()
    result: list[bool] = []
    now = [9.0]
    monkeypatch.setattr(database_module._time, "monotonic", lambda: now[0])

    def callback() -> None:
        entered.set()
        result.append(db.admit_task_completion_callback(
            task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
            output_summary="late after lock", confidence_score=90,
            recovery_deadline_monotonic=10.0,
        ))

    with db._lock:
        worker = threading.Thread(target=callback)
        worker.start()
        assert entered.wait(timeout=2)
        now[0] = 10.0
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [False]
    assert db.get_task_results("TASK-RECOVERY") == []


def test_completion_admission_rollback_leaves_no_result_or_accepted_identity(db, monkeypatch):
    """The ledger cannot claim an accepted callback when result insertion fails."""
    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="recovery")

    real_insert = db._insert_task_result

    def fail_after_insert(**kwargs) -> None:
        real_insert(**kwargs)
        raise RuntimeError("injected after result insertion")

    monkeypatch.setattr(db, "_insert_task_result", fail_after_insert)
    with pytest.raises(RuntimeError, match="injected"):
        db.admit_task_completion_callback(
            task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
            output_summary="x", confidence_score=90,
        )
    assert db.get_task_results("TASK-RECOVERY") == []
    row = db.execute(
        "SELECT state, accepted_result_id, accepted_result_session_id FROM task_completion_recoveries"
    ).fetchone()
    assert tuple(row) == ("claimed", None, None)


def test_completion_recovery_claim_requires_in_progress_task(db):
    _recovery_task(db)
    db.update_task("TASK-RECOVERY", status=TaskStatus.PENDING)
    assert not db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )


def test_recovery_publication_cas_never_overwrites_replaced_origin(db):
    """A claimed recovery is not authority to overwrite a newer binding."""
    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="replacement")
    assert not db.publish_task_completion_recovery_binding(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery",
    )
    assert db.get_task("TASK-RECOVERY").current_session_id == "replacement"


def test_completion_admission_requires_published_durable_binding(db):
    _recovery_task(db)
    assert not db.admit_task_completion_callback(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="other",
        output_summary="late", confidence_score=90,
    )
    assert db.get_task_results("TASK-RECOVERY") == []


def test_recovery_expiry_settlement_and_acceptance_have_one_durable_winner(db):
    _recovery_task(db)
    assert db.claim_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-RECOVERY", current_session_id="recovery")
    assert db.settle_expired_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
        settled_at="2026-01-01T00:02:00+00:00",
    )
    assert not db.admit_task_completion_callback(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
        output_summary="too late", confidence_score=90,
    )
    assert not db.settle_expired_task_completion_recovery(
        task_id="TASK-RECOVERY", agent="dev_agent", session_id="recovery",
        settled_at="2026-01-01T00:03:00+00:00",
    )
    assert tuple(db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries"
    ).fetchone()) == ("expired", None)


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
    db.insert_task(TaskRecord(id="TASK-002", brief="b", status=TaskStatus.IN_PROGRESS))
    db.insert_task(TaskRecord(id="TASK-003", brief="c", status=TaskStatus.COMPLETED))
    blocked = db.list_tasks(status=TaskStatus.IN_PROGRESS)
    assert [t.id for t in blocked] == ["TASK-002"]
    # Raw string value also accepted (CLI/query-param path).
    assert [t.id for t in db.list_tasks(status="completed")] == ["TASK-003"]


def test_list_tasks_filters_by_block_kind(db):
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", status=TaskStatus.ESCALATED, block_kind=None,
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    escalated = db.list_tasks(status=TaskStatus.ESCALATED, block_kind=None)
    assert [t.id for t in escalated] == ["TASK-001"]
    delegated = db.list_tasks(block_kind="delegated")
    assert [t.id for t in delegated] == ["TASK-002"]


def test_list_tasks_status_filter_composes_with_agent(db):
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", status=TaskStatus.IN_PROGRESS, assigned_agent="qa_engineer",
    ))
    rows = db.list_tasks(status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent")
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
    rows, _ = db.query_audit_logs()
    assert [r["id"] for r in rows] == [1, 2, 3, 4]


def test_query_audit_logs_filters_by_task_id(db) -> None:
    _seed_audit(db)
    rows, _ = db.query_audit_logs(task_id="TASK-001")
    assert {r["task_id"] for r in rows} == {"TASK-001"}
    assert len(rows) == 2


def test_query_audit_logs_filters_by_agent_and_action(db) -> None:
    _seed_audit(db)
    rows, _ = db.query_audit_logs(agent="engineering_head", action="escalation")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"reason": "budget"}


def test_query_audit_logs_limit_returns_most_recent_chronological(db) -> None:
    _seed_audit(db)
    rows, next_cursor = db.query_audit_logs(limit=2)
    # limit caps to most recent N but preserves chronological (ascending) order
    assert [r["id"] for r in rows] == [3, 4]
    assert next_cursor is not None  # there are more older entries


def test_query_audit_logs_limit_zero_returns_empty_no_cursor(db) -> None:
    """limit=0 short-circuits to entries=[] with next_cursor=None."""
    _seed_audit(db)
    rows, next_cursor = db.query_audit_logs(limit=0)
    assert rows == []
    assert next_cursor is None


def test_query_audit_logs_limit_negative_returns_empty_no_cursor(db) -> None:
    """limit=-1 short-circuits to entries=[] with next_cursor=None (no IndexError)."""
    _seed_audit(db)
    rows, next_cursor = db.query_audit_logs(limit=-1)
    assert rows == []
    assert next_cursor is None


def test_query_audit_logs_since_filters_by_timestamp(db) -> None:
    _seed_audit(db)
    all_rows, _ = db.query_audit_logs()
    # Find the timestamp of entry with id=3 (the 3rd inserted)
    cutoff = next(r["timestamp"] for r in all_rows if r["id"] == 3)
    rows, _ = db.query_audit_logs(since=cutoff)
    assert {r["id"] for r in rows} == {3, 4}


def test_query_audit_logs_parses_payload_json(db) -> None:
    _seed_audit(db)
    rows, _ = db.query_audit_logs(task_id="TASK-001", action="session_end")
    assert rows[0]["payload"] == {"duration_seconds": 30}


# ── Keyset cursor pagination tests ───────────────────────────────────────────

def _seed_audit_many(db, n: int = 10) -> list[dict]:
    """Seed *n* audit entries with deterministic task_id for testing."""
    entries: list[dict] = []
    for i in range(n):
        row_id = db.insert_audit_log(
            "TASK-{:03d}".format(i), "dev_agent", "cursor_test", {"idx": i}
        )
        # Re-fetch to get the timestamp
        cur = db._conn.execute("SELECT * FROM audit_log WHERE id = ?", (row_id,))
        entries.append(dict(cur.fetchone()))
    return entries


def test_query_audit_logs_cursor_first_page(db) -> None:
    """First page returns page-size rows + non-null next_cursor."""
    _seed_audit_many(db, n=6)
    entries, next_cursor = db.query_audit_logs(limit=3)
    assert len(entries) == 3
    assert next_cursor is not None
    # Entries are in chronological (ascending) order
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids)


def test_query_audit_logs_cursor_next_page_no_gap_no_overlap(db) -> None:
    """Next page via cursor returns immediately-older rows with no gap/overlap."""
    all_entries = _seed_audit_many(db, n=6)
    all_ids = sorted(e["id"] for e in all_entries)

    page1, cursor1 = db.query_audit_logs(limit=2)
    page1_ids = [e["id"] for e in page1]
    assert cursor1 is not None

    page2, cursor2 = db.query_audit_logs(limit=2, cursor=cursor1)
    page2_ids = [e["id"] for e in page2]
    assert cursor2 is not None

    # page1 is the 2 most-recent (last 2 in all_ids)
    assert page1_ids == all_ids[-2:]
    # page2 is the next 2 older
    assert page2_ids == all_ids[-4:-2]
    # No overlap
    assert set(page1_ids) & set(page2_ids) == set()


def test_query_audit_logs_cursor_exhaustion(db) -> None:
    """Final partial page returns next_cursor == null."""
    all_entries = _seed_audit_many(db, n=5)
    all_ids = sorted(e["id"] for e in all_entries)

    page1, cursor1 = db.query_audit_logs(limit=3)
    assert cursor1 is not None

    page2, cursor2 = db.query_audit_logs(limit=3, cursor=cursor1)
    # Should only get 2 remaining entries, with next_cursor=None
    assert len(page2) == 2
    assert cursor2 is None

    # All 5 ids covered across both pages
    seen = [e["id"] for e in page1] + [e["id"] for e in page2]
    assert sorted(seen) == all_ids


def test_query_audit_logs_cursor_with_task_id_filter(db) -> None:
    """Cursor AND-composes with task_id filter."""
    db.insert_audit_log("TASK-A", "dev_agent", "test", None)
    db.insert_audit_log("TASK-B", "dev_agent", "test", None)
    db.insert_audit_log("TASK-A", "dev_agent", "test", None)
    db.insert_audit_log("TASK-B", "dev_agent", "test", None)
    db.insert_audit_log("TASK-A", "dev_agent", "test", None)
    db.insert_audit_log("TASK-B", "dev_agent", "test", None)

    # Filter to TASK-A only, page size 2
    page1, cursor1 = db.query_audit_logs(task_id="TASK-A", limit=2)
    assert len(page1) == 2
    assert all(e["task_id"] == "TASK-A" for e in page1)
    assert cursor1 is not None

    page2, cursor2 = db.query_audit_logs(task_id="TASK-A", limit=2, cursor=cursor1)
    assert all(e["task_id"] == "TASK-A" for e in page2)
    # Should have the last 1 TASK-A entry
    assert len(page2) == 1
    # No overlap
    p1_ids = {e["id"] for e in page1}
    p2_ids = {e["id"] for e in page2}
    assert p1_ids & p2_ids == set()


def test_query_audit_logs_cursor_with_since_filter(db) -> None:
    """Cursor pagination stays within the since window."""
    entries = _seed_audit_many(db, n=5)
    # Use timestamp of entry 3 (0-indexed) as cutoff
    cutoff = sorted(entries, key=lambda e: e["id"])[2]["timestamp"]

    all_in_window, _ = db.query_audit_logs(since=cutoff)
    assert len(all_in_window) >= 2

    page1, cursor1 = db.query_audit_logs(since=cutoff, limit=2)
    assert all(e["timestamp"] >= cutoff for e in page1)
    if cursor1 is not None:
        page2, _ = db.query_audit_logs(since=cutoff, limit=2, cursor=cursor1)
        assert all(e["timestamp"] >= cutoff for e in page2)
        p1_ids = {e["id"] for e in page1}
        p2_ids = {e["id"] for e in page2}
        assert p1_ids & p2_ids == set()


def test_query_audit_logs_cursor_stability_under_insert(db) -> None:
    """Insert a NEWER row mid-pagination; cursor walk still returns same older rows."""
    _seed_audit_many(db, n=5)

    # Get first page
    page1, cursor1 = db.query_audit_logs(limit=3)
    page1_ids = [e["id"] for e in page1]
    assert cursor1 is not None

    # Insert a NEW entry (would be newer than all existing)
    db.insert_audit_log("TASK-NEW", "dev_agent", "new_event", None)

    # Continue pagination with same cursor
    page2, cursor2 = db.query_audit_logs(limit=3, cursor=cursor1)
    page2_ids = [e["id"] for e in page2]

    # No overlap with page1
    assert set(page1_ids) & set(page2_ids) == set()
    # page2 should not include the new entry (inserted AFTER cursor was derived)
    # The new entry would be newer, and cursor walks backward; it won't appear


def test_query_audit_logs_cursor_bad_cursor_rejected(db) -> None:
    """Malformed cursor raises a clean error."""
    _seed_audit(db)
    import pytest
    with pytest.raises(ValueError, match="Invalid cursor"):
        db.query_audit_logs(limit=2, cursor="not-a-valid-cursor!!!")


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


def test_get_recall_payload_includes_verdict_from_task_results(db):
    """get_recall_payload exposes the structured verdict from the latest task_results row."""
    db.insert_task(TaskRecord(id="TASK-V1", brief="review task"))
    db.insert_task_result(
        task_id="TASK-V1", agent="code_reviewer", session_id="sess-1",
        status="completed", output_summary="Verdict: APPROVE\n",
        confidence_score=90, verdict="APPROVE",
    )
    payload = db.get_recall_payload("TASK-V1")
    assert payload is not None
    assert payload["verdict"] == "APPROVE"


def test_get_recall_payload_verdict_durable_while_task_row_in_progress(db):
    """THR-211: the guarded-merge evidence path reads the structured verdict
    from task_results even while the tasks row still reads in_progress (the
    completion-status-lag window between the POST and session-finalization
    consumption). The recall payload must never gate the verdict on the task
    row being terminal."""
    db.insert_task(TaskRecord(id="TASK-VL", brief="review task"))
    db.update_task(
        "TASK-VL", status=TaskStatus.IN_PROGRESS,
        current_session_id="sess-lag", assigned_agent="code_reviewer",
    )
    db.insert_task_result(
        task_id="TASK-VL", agent="code_reviewer", session_id="sess-lag",
        status="completed", output_summary="approved",
        confidence_score=90, verdict="APPROVE",
    )
    payload = db.get_recall_payload("TASK-VL")
    assert payload is not None
    assert payload["status"] == "in_progress"
    assert payload["verdict"] == "APPROVE"


def test_get_recall_payload_null_verdict_when_no_task_results(db):
    """When no task_results rows exist, verdict is None in the payload."""
    db.insert_task(TaskRecord(id="TASK-V2", brief="no completion"))
    payload = db.get_recall_payload("TASK-V2")
    assert payload is not None
    assert "verdict" in payload
    assert payload["verdict"] is None


def test_get_recall_payload_null_verdict_when_result_has_no_verdict(db):
    """When the latest task_results row has no verdict column populated, verdict is None."""
    db.insert_task(TaskRecord(id="TASK-V3", brief="legacy task"))
    db.insert_task_result(
        task_id="TASK-V3", agent="old_agent", session_id="sess-2",
        status="completed", output_summary="Some work done.",
        confidence_score=85,
        # No verdict passed — simulates a legacy row before the column existed
    )
    payload = db.get_recall_payload("TASK-V3")
    assert payload is not None
    assert "verdict" in payload
    assert payload["verdict"] is None


def test_get_recall_payload_deterministic_latest_verdict(db):
    """The verdict comes from the latest task_results row (deterministic: ORDER BY created_at DESC, id DESC).

    When multiple task_results rows exist for the same task, the one with the
    most recent created_at wins.  When created_at ties, the highest id breaks the tie.
    """
    db.insert_task(TaskRecord(id="TASK-V4", brief="retried task"))
    # First result: FAIL (older created_at)
    db.insert_task_result(
        task_id="TASK-V4", agent="qa_engineer", session_id="sess-3",
        status="completed", output_summary="Verdict: FAIL\n",
        confidence_score=50, verdict="FAIL",
    )
    # Second result (newer created_at): PASS
    db.insert_task_result(
        task_id="TASK-V4", agent="qa_engineer", session_id="sess-4",
        status="completed", output_summary="Verdict: PASS\n",
        confidence_score=90, verdict="PASS",
    )
    payload = db.get_recall_payload("TASK-V4")
    assert payload is not None
    assert payload["verdict"] == "PASS", (
        f"Expected latest verdict PASS, got {payload['verdict']!r}"
    )


def test_get_recall_payload_result_recency_not_insertion_id(db):
    """The verdict selection is by created_at recency, not auto-increment id order.

    When a newer result row has a LOWER id (created_at is later but was inserted
    into a table with a gap from a prior-deleted row), created_at recency wins.
    """
    import datetime

    db.insert_task(TaskRecord(id="TASK-RECENCY", brief="recency test"))

    now = datetime.datetime.now(datetime.timezone.utc)
    older = (now - datetime.timedelta(hours=2)).isoformat()
    newer = (now - datetime.timedelta(hours=1)).isoformat()

    # Insert the first result at a lower id with a NEWER created_at.
    db.insert_task_result(
        task_id="TASK-RECENCY", agent="qa_engineer", session_id="sess-new",
        status="completed", output_summary="Verdict: PASS\n",
        confidence_score=90, verdict="PASS",
    )
    db._conn.execute(
        "UPDATE task_results SET created_at = ? WHERE task_id = ? AND verdict = 'PASS'",
        (newer, "TASK-RECENCY"),
    )

    # Insert a gap-filling dummy row to push the auto-increment counter up.
    db.insert_task_result(
        task_id="TASK-GAP-FILLER", agent="gap", session_id="gap-sess",
        status="completed", output_summary="gap",
        confidence_score=0, verdict="NONE",
    )

    # Now insert a second result at a HIGHER id with an OLDER created_at.
    db.insert_task_result(
        task_id="TASK-RECENCY", agent="qa_engineer", session_id="sess-old",
        status="completed", output_summary="Verdict: FAIL\n",
        confidence_score=50, verdict="FAIL",
    )
    db._conn.execute(
        "UPDATE task_results SET created_at = ? WHERE task_id = ? AND verdict = 'FAIL'",
        (older, "TASK-RECENCY"),
    )
    db._conn.commit()

    # Verify: the higher-id row has OLDER created_at, the lower-id row has NEWER.
    rows = db._conn.execute(
        "SELECT id, created_at, verdict FROM task_results WHERE task_id = ? ORDER BY id",
        ("TASK-RECENCY",),
    ).fetchall()
    assert len(rows) == 2
    assert rows[1]["id"] > rows[0]["id"]
    assert rows[1]["created_at"] < rows[0]["created_at"], (
        f"Expected older created_at on higher-id row, got "
        f"id={rows[1]['id']} created_at={rows[1]['created_at']} vs "
        f"id={rows[0]['id']} created_at={rows[0]['created_at']}"
    )

    payload = db.get_recall_payload("TASK-RECENCY")
    assert payload is not None
    # The NEWER created_at row should win (PASS), not the higher-id row (FAIL).
    assert payload["verdict"] == "PASS", (
        f"Expected verdict from newer created_at row (PASS), "
        f"got {payload['verdict']!r}. The ordering must be by created_at DESC, "
        f"not id DESC."
    )


def test_update_task_writes_block_kind_and_note(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    db.update_task(
        "TASK-001",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        note="Delegated to dev_agent",
        orchestration_step_count=2,
    )
    t = db.get_task("TASK-001")
    assert t.status == TaskStatus.IN_PROGRESS
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
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="x")
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
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.update_task("T-2", status=TaskStatus.ESCALATED, block_kind=None)

    ids = set(db.list_blocked_with_kind(BlockKind.DELEGATED))
    assert ids == {"T-1"}


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
        "VALUES ('TASK-1', 'in_progress', 'engineering', 'root', '2026-01-01', '2026-01-01')"
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
    back to escalated. Closes Codex P2 race in the escalate branch."""
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
    NULL, completed_at set) instead of escalated."""
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


def test_try_fail_over_budget_succeeds_from_in_progress_delegated(db):
    """The budget guard can also fire from an in_progress(delegated) eligible
    pre-state (a resumed parent-style row); the CAS keys on the block_kind."""
    from runtime.models import BlockKind
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    ok = db.try_fail_over_budget(
        "T-1", expected_status=TaskStatus.IN_PROGRESS,
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


def test_thr080_slice_a_migration_rewrites_resolved_superseded_to_superseded(tmp_path):
    """THR-080 Slice A: the idempotent boot migration rewrites
    status='resolved_superseded' -> 'superseded' on next startup.
    One-way, no dual-read (founder-ratified)."""
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus
    dbp = tmp_path / "happyranch.db"
    db = Database(dbp)
    db.insert_task(TaskRecord(id="T-OLD", brief="old status name"))
    # Seed the old status name via raw SQL (bypass enum write path).
    db._conn.execute(
        "UPDATE tasks SET status='resolved_superseded' WHERE id='T-OLD'"
    )
    db._conn.commit()
    # Pre-migration assert: the raw SQL value is stored as-is.
    row = db._conn.execute(
        "SELECT status FROM tasks WHERE id='T-OLD'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "resolved_superseded"
    db.close()

    # Re-open -> the startup ALTER-ladder + THR-080 UPDATE runs.
    db2 = Database(dbp)
    t = db2.get_task("T-OLD")
    assert t is not None
    assert t.status == TaskStatus.SUPERSEDED
    # Verify the raw DB value also reads the new name.
    row2 = db2._conn.execute(
        "SELECT status FROM tasks WHERE id='T-OLD'"
    ).fetchone()
    assert row2 is not None
    assert row2["status"] == "superseded"
    db2.close()


def test_try_delegate_rejects_cancelled_parent_and_inserts_no_child(db):
    """Atomic CAS: a cancelled parent must not be transitioned back to
    in_progress(delegated), AND the child must not be created. This is the
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
        status=TaskStatus.IN_PROGRESS,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    result = db.get_subtree_statuses("ROOT-1")
    assert sorted(result) == sorted(["in_progress", "completed"])


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
        status=TaskStatus.IN_PROGRESS,
    ))
    # Branch C: child -> grandchild with subtask
    db.insert_task(TaskRecord(
        id="CHILD-C", brief="cc", parent_task_id="ROOT-1",
        status=TaskStatus.ESCALATED, task_type="subtask",
    ))
    db.insert_task(TaskRecord(
        id="GRAND-C", brief="gc", parent_task_id="CHILD-C",
        status=TaskStatus.SUPERSEDED,
    ))
    result = db.get_subtree_statuses("ROOT-1")
    assert sorted(result) == sorted([
        "completed", "failed", "in_progress", "superseded", "escalated",
    ])


# ---------------------------------------------------------------------------
# get_descendant_task_ids — subtree walk for Activity replay
# ---------------------------------------------------------------------------

def test_get_descendant_task_ids_returns_empty_for_task_with_no_children(db):
    db.insert_task(TaskRecord(id="ROOT-D", brief="root"))
    result = db.get_descendant_task_ids("ROOT-D")
    assert result == []


def test_get_descendant_task_ids_returns_direct_children(db):
    db.insert_task(TaskRecord(id="ROOT-D", brief="root"))
    db.insert_task(TaskRecord(id="CHILD-1", brief="c1", parent_task_id="ROOT-D"))
    db.insert_task(TaskRecord(id="CHILD-2", brief="c2", parent_task_id="ROOT-D"))
    result = db.get_descendant_task_ids("ROOT-D")
    assert sorted(result) == ["CHILD-1", "CHILD-2"]


def test_get_descendant_task_ids_walks_grandchildren(db):
    db.insert_task(TaskRecord(id="ROOT-D", brief="root"))
    db.insert_task(TaskRecord(id="CHILD-1", brief="c1", parent_task_id="ROOT-D"))
    db.insert_task(TaskRecord(id="GRAND-1", brief="g1", parent_task_id="CHILD-1"))
    result = db.get_descendant_task_ids("ROOT-D")
    assert sorted(result) == ["CHILD-1", "GRAND-1"]


def test_get_descendant_task_ids_excludes_root(db):
    db.insert_task(TaskRecord(id="ROOT-D", brief="root"))
    db.insert_task(TaskRecord(id="CHILD-1", brief="c1", parent_task_id="ROOT-D"))
    result = db.get_descendant_task_ids("ROOT-D")
    assert "ROOT-D" not in result


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
        status=TaskStatus.ESCALATED,
    ))
    result = db.list_roots()
    assert len(result) == 1
    root = result[0]
    assert hasattr(root, '_severity_rollup')
    # escalated child → root rollup should be 'escalated' (worst severity, Path B)
    assert root._severity_rollup == 'escalated'


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


def test_list_roots_severity_rollup_escalated_wins_over_failed(db):
    """Escalated is the worst severity (Path B) — escalated > failed >
    in_progress > pending > completed > cancelled > superseded."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="ok", status=TaskStatus.COMPLETED))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.ESCALATED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.FAILED,
    ))
    result = db.list_roots()
    assert result[0]._severity_rollup == 'escalated'


def test_list_roots_severity_rollup_delegating_parent_does_not_dominate(db):
    """Path B (THR-037 §F.4): a delegating parent is stored `in_progress`
    (rank 2), so a healthy delegating subtree NO LONGER rolls up to the
    attention-grabbing worst — only a real `escalated`/`failed` descendant
    pulls it up. Here an in_progress(delegated) root with completed children
    rolls up to plain `in_progress`, not to amber/red."""
    db.insert_task(TaskRecord(
        id="ROOT-1", brief="parent", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-1", brief="c1", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    db.insert_task(TaskRecord(
        id="CHILD-2", brief="c2", parent_task_id="ROOT-1",
        status=TaskStatus.COMPLETED,
    ))
    result = db.list_roots()
    root = [r for r in result if r.id == "ROOT-1"][0]
    assert root._severity_rollup == 'in_progress'


def test_list_roots_filters_by_status(db):
    """Status filter applied to the root itself, rollup computed on full subtree."""
    db.insert_task(TaskRecord(id="ROOT-1", brief="r1", status=TaskStatus.IN_PROGRESS))
    db.insert_task(TaskRecord(id="ROOT-2", brief="r2", status=TaskStatus.COMPLETED))
    result = db.list_roots(status=TaskStatus.IN_PROGRESS)
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


# ── THR-129 lock instrumentation tests ──────────────────────────────────


def test_lock_instrument_hold_warns_on_slow_query(db, caplog):
    """When a query holds the lock longer than the threshold, a warning is logged."""
    import logging
    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    # Lower the threshold to trigger on any real query
    db._lock_warn_threshold_seconds = 0.0
    # Run a query that takes some measurable time (the grouped active-session
    # query, even on an empty DB, takes a few ms — not enough for 0.0 s
    # threshold on a fast machine, but the timing is monotonic so any hold > 0
    # will trigger). We seed enough rows to guarantee it.
    for i in range(100):
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, ?, ?, NULL)",
            (f"2026-06-01T00:00:{i:02d}", f"TASK-{i % 10}", f"agent{i % 5}",
             "session_start" if i % 2 == 0 else "session_end"),
        )
    db._conn.commit()
    db.fetch_all_readonly("SELECT COUNT(*) FROM audit_log")
    # With threshold 0.0, any positive hold time triggers a warning
    hold_warnings = [r for r in caplog.record_tuples
                     if "hold" in r[2] and "fetch_all_readonly" in r[2]]
    assert len(hold_warnings) >= 1, f"Expected hold warning, got: {caplog.record_tuples}"
    # Reset threshold
    db._lock_warn_threshold_seconds = 1.0


def test_lock_instrument_wait_detects_contention(db, caplog):
    """When two threads contend for the lock, the waiting thread logs a wait warning."""
    import logging
    import time
    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.05  # 50ms — low enough to catch contention

    hold_started = threading.Event()
    hold_done = threading.Event()
    wait_observed = threading.Event()
    wait_warnings: list = []

    def slow_holder():
        # Acquire lock and hold it for a bit
        db._lock.acquire()
        try:
            hold_started.set()
            time.sleep(0.3)  # hold long enough for the waiter to notice
        finally:
            db._lock.release()
            hold_done.set()

    def waiter():
        # Wait for holder to start, then try to acquire
        hold_started.wait()
        db.fetch_all_readonly("SELECT 1")
        wait_observed.set()

    t_holder = threading.Thread(target=slow_holder)
    t_waiter = threading.Thread(target=waiter)
    t_holder.start()
    t_waiter.start()
    t_holder.join()
    t_waiter.join()

    assert wait_observed.is_set(), "waiter never completed"
    wait_warnings = [r for r in caplog.record_tuples
                     if "wait" in r[2]]
    assert len(wait_warnings) >= 1, (
        f"Expected wait warning, got records: {caplog.record_tuples}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_lock_instrument_rlock_reentrancy_no_false_wait(db, caplog):
    """RLock reentrancy: nested acquire by the same thread shows near-zero wait."""
    import logging
    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.1

    # get_task calls fetch_one_readonly which both go through _synchronized.
    # The inner call re-acquires the RLock with no contention.
    db.insert_task(TaskRecord(id="TASK-R", brief="reentrant test"))
    result = db.get_task("TASK-R")
    assert result is not None

    # No wait warnings should fire for reentrant acquires (near-zero wait)
    wait_warnings = [r for r in caplog.record_tuples if "wait" in r[2]]
    assert len(wait_warnings) == 0, (
        f"RLock reentrancy should not log wait warnings, got: {wait_warnings}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_lock_instrument_responsiveness_regression(db, caplog):
    """Durable responsiveness regression: a lightweight read completes quickly
    even when a concurrent thread holds the lock briefly (Post-Phase 0, the
    dashboard query is fast — this proves a fast operation is not starved)."""
    import logging
    import time

    # Seed some data
    for i in range(50):
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, ?, ?, NULL)",
            (f"2026-06-01T00:00:{i:02d}", f"TASK-{i % 5}", f"agent{i % 3}",
             "session_start" if i % 2 == 0 else "session_end"),
        )
    db._conn.commit()

    results: list = []
    barrier = threading.Barrier(2, timeout=5)

    def fast_query():
        barrier.wait()
        t0 = time.monotonic()
        rows = db.fetch_all_readonly("SELECT COUNT(*) FROM audit_log")
        elapsed = time.monotonic() - t0
        results.append(("fast", elapsed, rows))

    def slow_query():
        barrier.wait()
        t0 = time.monotonic()
        # The grouped active-session query (post-Phase 0 fix)
        rows = db.fetch_all_readonly(
            "SELECT DISTINCT agent FROM audit_log "
            "WHERE action IN ('session_start', 'session_end') "
            "GROUP BY task_id, agent "
            "HAVING MAX(CASE WHEN action='session_start' THEN timestamp END) IS NOT NULL "
            "  AND (MAX(CASE WHEN action='session_end' THEN timestamp END) IS NULL "
            "       OR MAX(CASE WHEN action='session_start' THEN timestamp END) >= "
            "          MAX(CASE WHEN action='session_end' THEN timestamp END))"
        )
        elapsed = time.monotonic() - t0
        results.append(("grouped", elapsed, rows))

    t1 = threading.Thread(target=fast_query)
    t2 = threading.Thread(target=slow_query)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    # The fast count(*) query should complete in well under 1 second
    # even when contending with the grouped active-session query.
    fast_elapsed = [e for label, e, _ in results if label == "fast"][0]
    assert fast_elapsed < 1.0, (
        f"Fast query took {fast_elapsed:.3f}s — lock contention regression"
    )


def test_lock_instrument_execute_contention_regression(db, caplog):
    """Database.execute (now @_synchronized) fires wait/hold warnings
    under contention, proving all shared-connection paths are instrumented."""
    import logging
    import time

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.05  # 50ms

    hold_started = threading.Event()

    def slow_holder():
        db._lock.acquire()
        try:
            hold_started.set()
            time.sleep(0.3)
        finally:
            db._lock.release()

    def waiter():
        hold_started.wait()
        # db.execute() goes through @_synchronized → must log wait
        db.execute("SELECT 1")

    t_holder = threading.Thread(target=slow_holder)
    t_waiter = threading.Thread(target=waiter)
    t_holder.start()
    t_waiter.start()
    t_holder.join()
    t_waiter.join()

    wait_warnings = [r for r in caplog.record_tuples if "wait" in r[2]]
    assert len(wait_warnings) >= 1, (
        f"db.execute() must log wait warning under contention, "
        f"got: {caplog.record_tuples}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_lock_instrument_execute_reentrancy_no_false_wait(db, caplog):
    """db.execute() called from within another @_synchronized method must
    show near-zero wait time (RLock reentrancy). The inner execute must
    not produce a false-positive wait warning."""
    import logging

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.01  # very low threshold

    # Insert a task (goes through @_synchronized). get_task internally
    # calls fetch_one_readonly which goes through @_synchronized. The
    # inner call re-acquires the RLock with near-zero wait.
    db.insert_task(TaskRecord(id="TASK-REX", brief="reentrant exec test"))
    result = db.get_task("TASK-REX")
    assert result is not None

    # Also test execute() called directly from within a @_synchronized
    # method. insert_task calls self.execute() internally.
    # Direct execute() should show no wait warning when called reentrantly.
    wait_warnings = [r for r in caplog.record_tuples if "wait" in r[2]]
    assert len(wait_warnings) == 0, (
        f"RLock reentrancy should not log wait warnings for execute, "
        f"got: {wait_warnings}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_lock_instrument_no_sql_param_leakage(db, caplog):
    """Lock instrumentation log messages must NEVER contain SQL text or
    query parameters. Only duration, threshold, class name, and method
    name are safe to emit. Any SQL/param leakage is a security risk."""
    import logging
    import time

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.0  # trigger on any positive hold
    hold_started = threading.Event()
    hold_done = threading.Event()

    def slow_holder():
        db._lock.acquire()
        try:
            hold_started.set()
            time.sleep(0.3)
        finally:
            db._lock.release()
            hold_done.set()

    def waiter():
        hold_started.wait()
        # This query has a distinctive SQL pattern and parameter
        db.fetch_all_readonly(
            "SELECT COUNT(*) AS n FROM tasks WHERE id = ?",
            ("TASK-SECRET-LEAK-TEST",),
        )

    t_holder = threading.Thread(target=slow_holder)
    t_waiter = threading.Thread(target=waiter)
    t_holder.start()
    t_waiter.start()
    t_holder.join()
    t_waiter.join()

    # Collect all lock warning messages
    lock_warnings = [
        r[2] for r in caplog.record_tuples
        if "Database._lock" in r[2]
    ]
    assert len(lock_warnings) >= 1, (
        "Expected at least one lock warning, got none"
    )

    # Verify no SQL or parameter leakage
    for msg in lock_warnings:
        # SQL keywords
        assert "SELECT" not in msg.upper(), (
            f"Lock warning leaks SQL: {msg[:100]}"
        )
        assert "INSERT" not in msg.upper(), (
            f"Lock warning leaks SQL: {msg[:100]}"
        )
        assert "FROM" not in msg, (
            f"Lock warning leaks SQL: {msg[:100]}"
        )
        assert "WHERE" not in msg, (
            f"Lock warning leaks SQL: {msg[:100]}"
        )
        # Task IDs / parameters
        assert "TASK-SECRET-LEAK-TEST" not in msg, (
            f"Lock warning leaks query parameter: {msg[:100]}"
        )
        # Verify safe fields are present
        assert "Database._lock" in msg, (
            f"Lock warning missing class info: {msg[:100]}"
        )

    db._lock_warn_threshold_seconds = 1.0


# ── THR-129 fix-forward round 3: execute hold/wait + nested reentrancy ──

def test_execute_hold_warns_at_threshold_boundary(db, caplog):
    """Database.execute itself records hold warnings when execution time
    exceeds the threshold. Proves the @_synchronized instrumentation covers
    the execute() path, not just the higher-level query methods."""
    import logging

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.0  # trigger on any positive hold

    # A query that takes measurable time by doing multiple UNION ALL scans
    db.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
        "UNION ALL SELECT COUNT(*) FROM sqlite_master "
    )
    hold_warnings = [r for r in caplog.record_tuples
                     if "hold" in r[2] and "execute" in r[2]]
    assert len(hold_warnings) >= 1, (
        f"Expected hold warning for execute(), got: {caplog.record_tuples}"
    )
    # Verify no SQL leakage in warnings
    for _, _, msg in hold_warnings:
        assert "SELECT" not in msg.upper(), f"Warning leaks SQL: {msg[:100]}"
        assert "sqlite_master" not in msg, f"Warning leaks table name: {msg[:100]}"

    db._lock_warn_threshold_seconds = 1.0


def test_execute_wait_warns_at_threshold_boundary(db, caplog):
    """Database.execute itself records wait warnings under contention,
    proving all shared-connection paths are instrumented."""
    import logging
    import time

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.05  # 50ms

    hold_started = threading.Event()

    def slow_holder():
        db._lock.acquire()
        try:
            hold_started.set()
            time.sleep(0.3)
        finally:
            db._lock.release()

    def waiter():
        hold_started.wait()
        # Direct execute() call via @_synchronized — must log wait
        result = db.execute("SELECT 1")
        list(result)  # consume iterator

    t_holder = threading.Thread(target=slow_holder)
    t_waiter = threading.Thread(target=waiter)
    t_holder.start()
    t_waiter.start()
    t_holder.join()
    t_waiter.join()

    wait_warnings = [r for r in caplog.record_tuples
                     if "wait" in r[2] and "execute" in r[2]]
    assert len(wait_warnings) >= 1, (
        f"db.execute() must log wait warning under contention, "
        f"got: {caplog.record_tuples}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_execute_nested_reentrant_no_false_wait(db, caplog):
    """Genuinely nested Database.execute call within another
    @_synchronized method: the inner execute re-acquires the RLock with
    near-zero wait and must not produce false-positive wait warnings.
    This proves RLock reentrancy for execute-within-locked-context,
    exercising the exact execute→execute nesting path."""
    import logging

    caplog.set_level(logging.WARNING, logger="happyranch.database.lock")
    db._lock_warn_threshold_seconds = 0.01  # very low threshold

    # Acquire the lock explicitly, then call execute() multiple times from
    # the same thread. Each inner execute() is @_synchronized and will
    # re-acquire the RLock — this is the genuinely nested execute path.
    def nested_executes():
        db._lock.acquire()
        try:
            # First execute (outer lock held by us)
            r1 = db.execute("SELECT 1")
            list(r1)
            # Second execute — also re-acquires RLock
            r2 = db.execute("SELECT 2")
            list(r2)
            # Third execute
            r3 = db.execute("SELECT 3")
            list(r3)
            # insert_task calls self.execute() internally via @_synchronized
            db.insert_task(TaskRecord(id="TASK-NESTX", brief="nested exec"))
        finally:
            db._lock.release()

    t = threading.Thread(target=nested_executes)
    t.start()
    t.join()

    # Zero wait warnings — all acquires were reentrant (same thread)
    wait_warnings = [r for r in caplog.record_tuples if "wait" in r[2]]
    assert len(wait_warnings) == 0, (
        f"Nested/reentrant execute calls must not log wait warnings, "
        f"got: {wait_warnings}"
    )
    db._lock_warn_threshold_seconds = 1.0


def test_local_ci_migration_from_legacy_schema(tmp_path):
    """Prove additive nullable migration: seed a task_results row in the old
    table shape (no local_ci column), open through real Database startup
    migration, assert local_ci column was added nullable, the legacy row
    survives, its existing field values are intact, its local_ci is NULL,
    and a new row can persist the exact valid JSON."""
    import sqlite3
    from runtime.infrastructure.database import Database

    db_path = tmp_path / "legacy_lc.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE tasks (
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
        final_output_dir TEXT,
        task_type TEXT NOT NULL DEFAULT 'task',
        orchestration_step_count INTEGER NOT NULL DEFAULT 0,
        revisit_of_task_id TEXT,
        dispatched_from_thread_id TEXT,
        block_kind TEXT,
        blocked_on_job_ids TEXT,
        active_chain TEXT,
        note TEXT,
        active_fanout TEXT
    )""")
    conn.execute("""CREATE TABLE task_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'completed',
        output_summary TEXT NOT NULL DEFAULT '',
        confidence_score INTEGER NOT NULL DEFAULT 80,
        learnings TEXT,
        risks_flagged TEXT,
        duration_seconds INTEGER,
        token_count INTEGER,
        estimated_cost REAL,
        output_dir TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.commit()

    conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary,
            confidence_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("TASK-LEGACY", "dev_agent", "sess-legacy",
         "completed", "legacy row", 90, "2025-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    db = Database(db_path)

    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(task_results)").fetchall()}
    assert "local_ci" in cols, f"Columns after migration: {cols}"

    rows = db.get_task_results("TASK-LEGACY")
    assert len(rows) == 1
    legacy = rows[0]
    assert legacy["task_id"] == "TASK-LEGACY"
    assert legacy["agent"] == "dev_agent"
    assert legacy["session_id"] == "sess-legacy"
    assert legacy["status"] == "completed"
    assert legacy["output_summary"] == "legacy row"
    assert legacy["confidence_score"] == 90
    assert legacy["created_at"] == "2025-01-01T00:00:00+00:00"
    assert legacy.get("local_ci") is None, f"Expected NULL, got {legacy.get('local_ci')!r}"

    db.insert_task_result(
        task_id="TASK-LEGACY",
        agent="dev_agent",
        session_id="sess-new",
        status="completed",
        output_summary="with local_ci",
        confidence_score=95,
        local_ci_json='{"command":"scripts/local_ci.sh all","exit_code":0}',
    )
    rows = db.get_task_results("TASK-LEGACY")
    assert len(rows) == 2
    new_row = [r for r in rows if r["session_id"] == "sess-new"][0]
    assert new_row["local_ci"] == '{"command":"scripts/local_ci.sh all","exit_code":0}'

    db.close()


# ── get_latest_completion_report local_ci reconstruction ─────────────────

def test_get_latest_completion_report_reconstructs_local_ci(db):
    """A task_result row with a valid local_ci JSON reconstructs into
    the returned CompletionReport.local_ci field with exact equality."""
    db.insert_task_result(
        task_id="TASK-LC",
        agent="dev_agent",
        session_id="sess-lc",
        status="completed",
        output_summary="with local_ci",
        confidence_score=95,
        local_ci_json='{"command":"scripts/local_ci.sh all","exit_code":0}',
    )
    report = db.get_latest_completion_report("TASK-LC")
    assert report is not None
    assert report.local_ci is not None
    assert report.local_ci.command == "scripts/local_ci.sh all"
    assert report.local_ci.exit_code == 0


def test_get_latest_completion_report_local_ci_null_returns_none(db):
    """A row without local_ci_json (stored NULL) returns local_ci=None."""
    db.insert_task_result(
        task_id="TASK-NULL",
        agent="dev_agent",
        session_id="sess-null",
        status="completed",
        output_summary="no local_ci",
        confidence_score=95,
    )
    report = db.get_latest_completion_report("TASK-NULL")
    assert report is not None
    assert report.local_ci is None


def test_get_latest_completion_report_local_ci_malformed_returns_none(db):
    """Malformed JSON in local_ci degrades to None and never crashes."""
    from datetime import datetime, timezone
    db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary,
            confidence_score, local_ci, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TASK-MAL", "dev_agent", "sess-mal", "completed", "malformed",
         95, "NOT JSON", datetime.now(timezone.utc).isoformat()),
    )
    db._conn.commit()
    report = db.get_latest_completion_report("TASK-MAL")
    assert report is not None
    assert report.local_ci is None


def test_get_latest_completion_report_local_ci_wrong_shape_returns_none(db):
    """JSON that parses but is the wrong shape (not a dict) returns None."""
    from datetime import datetime, timezone
    db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary,
            confidence_score, local_ci, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TASK-SHAPE", "dev_agent", "sess-shape", "completed", "wrong shape",
         95, '[1,2,3]', datetime.now(timezone.utc).isoformat()),
    )
    db._conn.commit()
    report = db.get_latest_completion_report("TASK-SHAPE")
    assert report is not None
    assert report.local_ci is None


def test_get_latest_completion_report_local_ci_invalid_values_returns_none(db):
    """Valid JSON dict with values failing the strict LocalCiEvidence contract
    (non-zero exit_code) degrades to None."""
    from datetime import datetime, timezone
    db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary,
            confidence_score, local_ci, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TASK-INV", "dev_agent", "sess-inv", "completed", "invalid values",
         95, '{"command":"bad","exit_code":1}',
         datetime.now(timezone.utc).isoformat()),
    )
    db._conn.commit()
    report = db.get_latest_completion_report("TASK-INV")
    assert report is not None
    assert report.local_ci is None


def test_get_latest_completion_report_local_ci_extra_key_returns_none(db):
    """Valid JSON dict with an extra key (forbidden by extra='forbid')
    degrades to None."""
    from datetime import datetime, timezone
    db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary,
            confidence_score, local_ci, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TASK-EXTRA", "dev_agent", "sess-extra", "completed", "extra key",
         95,
         '{"command":"scripts/local_ci.sh all","exit_code":0,"extra":true}',
         datetime.now(timezone.utc).isoformat()),
    )
    db._conn.commit()
    report = db.get_latest_completion_report("TASK-EXTRA")
    assert report is not None
    assert report.local_ci is None


def test_get_latest_completion_report_returns_latest_row_local_ci(db):
    """When multiple task_results exist, the latest row's local_ci is used."""
    db.insert_task_result(
        task_id="TASK-MULTI",
        agent="dev_agent",
        session_id="sess-first",
        status="completed",
        output_summary="first",
        confidence_score=95,
    )
    db.insert_task_result(
        task_id="TASK-MULTI",
        agent="dev_agent",
        session_id="sess-second",
        status="completed",
        output_summary="second with local_ci",
        confidence_score=95,
        local_ci_json='{"command":"scripts/local_ci.sh all","exit_code":0}',
    )
    report = db.get_latest_completion_report("TASK-MULTI")
    assert report is not None
    assert report.local_ci is not None
    assert report.output_summary == "second with local_ci"


def test_get_latest_completion_report_scoped_by_agent_session(db):
    """THR-211: the (agent, session_id) scoped lookup returns the exact
    fingerprint row even when a newer unrelated row exists — the authority the
    chain gate relies on. The unscoped lookup keeps the newest-row contract
    for the non-chain readers, and an unknown fingerprint fails closed."""
    db.insert_task_result(
        task_id="TASK-MIX", agent="dev_agent", session_id="sess-auth",
        status="completed", output_summary="auth ok", confidence_score=90,
        verdict="APPROVE",
    )
    db.insert_task_result(
        task_id="TASK-MIX", agent="other_agent", session_id="sess-other",
        status="completed", output_summary="intruder", confidence_score=10,
        verdict="REQUEST_CHANGES",
    )
    scoped = db.get_latest_completion_report("TASK-MIX", "dev_agent", "sess-auth")
    assert scoped is not None
    assert scoped.verdict == "APPROVE"
    assert scoped.output_summary == "auth ok"
    # Unscoped keeps the newest-row contract (used by non-chain readers).
    unscoped = db.get_latest_completion_report("TASK-MIX")
    assert unscoped is not None
    assert unscoped.verdict == "REQUEST_CHANGES"
    # Unknown fingerprint fails closed.
    assert db.get_latest_completion_report("TASK-MIX", "dev_agent", "nope") is None
    # A newer row within the SAME fingerprint still wins (retry semantics).
    db.insert_task_result(
        task_id="TASK-MIX", agent="dev_agent", session_id="sess-auth",
        status="completed", output_summary="auth retry", confidence_score=95,
        verdict="APPROVE",
    )
    scoped2 = db.get_latest_completion_report("TASK-MIX", "dev_agent", "sess-auth")
    assert scoped2 is not None
    assert scoped2.output_summary == "auth retry"
    assert scoped2.verdict == "APPROVE"


# ── TASK-5823: exact-scoped reader fail-closes on structural malformation ──

def _insert_malformed_exact_result(db, *, column: str, raw) -> None:
    db.insert_task_result(
        task_id="TASK-MAL", agent="code_reviewer", session_id="sess-cur",
        status="completed", output_summary="exact completed row",
        confidence_score=90, verdict="APPROVE",
    )
    db._conn.execute(
        f"UPDATE task_results SET {column} = ? "
        "WHERE task_id = ? AND session_id = ?",
        (raw, "TASK-MAL", "sess-cur"),
    )
    db._conn.commit()


def test_get_latest_completion_report_scoped_malformed_risks_json_returns_none(db):
    """TASK-5823: a modern exact-fingerprint row whose persisted risks_flagged
    is invalid JSON has NO acceptable authenticated report — the scoped read
    returns None (fail-closed) instead of raising."""
    _insert_malformed_exact_result(db, column="risks_flagged", raw="not-json{{[")
    assert db.get_latest_completion_report("TASK-MAL", "code_reviewer", "sess-cur") is None


def test_get_latest_completion_report_scoped_wrong_shape_returns_none(db):
    """Valid JSON of the wrong container shape (dict instead of list) fails the
    strict CompletionReport contract and fail-closes to None on the scoped read."""
    _insert_malformed_exact_result(db, column="risks_flagged", raw='{"not": "a list"}')
    assert db.get_latest_completion_report("TASK-MAL", "code_reviewer", "sess-cur") is None


def test_get_latest_completion_report_scoped_non_string_elements_returns_none(db):
    """A list with invalid element types (list[int] instead of list[str])
    fail-closes to None on the scoped read."""
    _insert_malformed_exact_result(db, column="risks_flagged", raw="[1, 2, 3]")
    assert db.get_latest_completion_report("TASK-MAL", "code_reviewer", "sess-cur") is None


def test_get_latest_completion_report_scoped_confidence_out_of_range_returns_none(db):
    """confidence_score outside the strict CompletionReport range (0..100) is a
    structural validation failure — fail-closes to None on the scoped read."""
    _insert_malformed_exact_result(db, column="confidence_score", raw=150)
    assert db.get_latest_completion_report("TASK-MAL", "code_reviewer", "sess-cur") is None


def test_get_latest_completion_report_unscoped_malformed_still_raises(db):
    """Legacy unscoped read keeps its prior behavior: a structurally malformed
    newest row still surfaces as JSONDecodeError — the TASK-5823 fail-closed
    conversion applies ONLY to the modern exact-fingerprint scope."""
    _insert_malformed_exact_result(db, column="risks_flagged", raw="not-json{{[")
    with pytest.raises(ValueError):
        db.get_latest_completion_report("TASK-MAL")


def test_get_latest_completion_report_scoped_valid_row_round_trips_structured_fields(db):
    """Positive control at the reader seam: a VALID exact row round-trips its
    structured fields through the strict contract — the fail-closed conversion
    must never degrade a well-formed authenticated report."""
    db.insert_task_result(
        task_id="TASK-OK", agent="code_reviewer", session_id="sess-cur",
        status="completed", output_summary="approved", confidence_score=90,
        verdict="APPROVE", risks_flagged=["risk one", "risk two"],
        waiting_on_job_ids=["JOB-1"],
    )
    report = db.get_latest_completion_report("TASK-OK", "code_reviewer", "sess-cur")
    assert report is not None
    assert report.risks_flagged == ["risk one", "risk two"]
    assert report.waiting_on_job_ids == ["JOB-1"]
    assert report.verdict == "APPROVE"
