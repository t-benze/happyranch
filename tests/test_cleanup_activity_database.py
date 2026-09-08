import subprocess
import sys
import threading

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


def _trigger(db, *, agent: str = "dev_agent") -> dict:
    db.insert_audit_log(
        "TASK-001", agent, "workspace_cleanup_triggered",
        {"brief_kind": "report_only", "run_number": 1},
    )
    context = db.get_cleanup_trigger_context("TASK-001", agent)
    assert context is not None
    return context


def _write(db, context: dict, *, session: str = "sess-cleanup", summary: str = "receipt") -> bool:
    return db.insert_cleanup_completion(
        task_id="TASK-001", agent="dev_agent", session_id=session,
        output_summary=summary, confidence_score=80,
        cleanup_activity=_receipt(), trigger_context=context,
    )


def _completed(db) -> list[dict]:
    return [row for row in db.get_audit_logs("TASK-001") if row["action"] == "workspace_cleanup_completed"]


def test_cleanup_completion_commits_result_and_audit_together(db) -> None:
    context = _trigger(db)
    assert _write(db, context) is True

    # A second real connection verifies durable visibility, rather than merely
    # reading the writer's connection-local state.
    from runtime.infrastructure.database import Database
    reopened = Database(db.db_path)
    results = reopened.get_task_results("TASK-001")
    completed = _completed(reopened)
    assert len(results) == len(completed) == 1
    assert completed[0]["payload"]["task_result_id"] > 0
    assert completed[0]["payload"]["task_result_id"] == results[0]["id"]
    assert completed[0]["payload"]["session_id"] == "sess-cleanup"


def test_legacy_insert_task_result_still_commits(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-ordinary",
        output_summary="ordinary", confidence_score=80,
    )
    from runtime.infrastructure.database import Database
    assert len(Database(db.db_path).get_task_results("TASK-001")) == 1


def test_cleanup_completion_rolls_back_result_when_audit_insert_fails(db, monkeypatch) -> None:
    context = _trigger(db)

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(db, "insert_audit_log_uncommitted", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        _write(db, context)
    assert db.get_task_results("TASK-001") == []
    assert _completed(db) == []


def test_cleanup_completion_rolls_back_when_real_commit_call_fails(db) -> None:
    context = _trigger(db)
    real_connection = db._conn

    class CommitFailureConnection:
        def __getattr__(self, name):
            return getattr(real_connection, name)

        def commit(self) -> None:
            raise RuntimeError("injected commit failure")

    db._conn = CommitFailureConnection()
    with pytest.raises(RuntimeError, match="injected commit failure"):
        _write(db, context)
    db._conn = real_connection
    from runtime.infrastructure.database import Database
    reopened = Database(db.db_path)
    assert reopened.get_task_results("TASK-001") == []
    assert _completed(reopened) == []
    assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent")) is True


@pytest.mark.parametrize("stage", ["after_result", "after_audit", "before_commit"])
def test_cleanup_completion_rolls_back_every_precommit_stage_after_reopen(db, stage) -> None:
    context = _trigger(db)

    def fail(current: str) -> None:
        if current == stage:
            raise RuntimeError(stage)

    db._cleanup_completion_stage_hook = fail
    with pytest.raises(RuntimeError, match=stage):
        _write(db, context)
    del db._cleanup_completion_stage_hook
    from runtime.infrastructure.database import Database
    reopened = Database(db.db_path)
    assert reopened.get_task_results("TASK-001") == []
    assert _completed(reopened) == []
    assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent")) is True


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


def test_cleanup_completion_rejects_unrelated_transaction_without_touching_it(db) -> None:
    context = _trigger(db)
    db._conn.execute("BEGIN")
    db._conn.execute("INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                     ("TASK-unrelated", "dev_agent", "unrelated", "{}", "2026-01-01T00:00:00+00:00"))
    with pytest.raises(RuntimeError, match="unrelated_transaction"):
        _write(db, context)
    assert db._conn.in_transaction
    assert db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE task_id = 'TASK-unrelated'").fetchone()[0] == 1
    db._conn.commit()
    assert _write(db, context) is True


def test_cleanup_completion_rlock_excludes_competing_thread(db) -> None:
    context = _trigger(db)
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    first_error: list[BaseException] = []

    def hold(stage: str) -> None:
        if stage == "after_result":
            entered.set()
            assert release.wait(2)

    db._cleanup_completion_stage_hook = hold

    def first() -> None:
        try:
            _write(db, context)
        except BaseException as exc:  # pragma: no cover - asserted below
            first_error.append(exc)

    def second() -> None:
        assert _write(db, context) is False
        second_done.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start()
    assert entered.wait(2)
    two.start()
    assert not second_done.wait(0.1)
    release.set()
    one.join(2)
    two.join(2)
    del db._cleanup_completion_stage_hook
    assert not first_error and second_done.is_set()
    assert [row["output_summary"] for row in db.get_task_results("TASK-001")] == ["receipt"]
    assert len(_completed(db)) == 1


def test_cleanup_completion_two_connections_first_wins_without_receipt_graft(db) -> None:
    context = _trigger(db)
    from runtime.infrastructure.database import Database
    other = Database(db.db_path)
    assert _write(db, context, summary="winner") is True
    assert _write(other, other.get_cleanup_trigger_context("TASK-001", "dev_agent"), summary="loser") is False
    assert [row["output_summary"] for row in other.get_task_results("TASK-001")] == ["winner"]
    assert len(_completed(other)) == 1


def test_cleanup_completion_never_grafts_receipt_onto_existing_result(db) -> None:
    context = _trigger(db)
    db.insert_task_result("TASK-001", "dev_agent", "sess-existing", "ordinary", 80)
    assert _write(db, context, session="sess-existing", summary="replacement") is False
    assert [row["output_summary"] for row in db.get_task_results("TASK-001")] == ["ordinary"]
    assert _completed(db) == []


@pytest.mark.parametrize("stage, expected_rows", [
    ("after_result", 0), ("after_audit", 0), ("after_commit", 1),
])
def test_cleanup_completion_subprocess_crash_has_exact_reopen_visibility(db, stage, expected_rows) -> None:
    _trigger(db)
    script = "\n".join([
        "import os, sys",
        "from pathlib import Path",
        "from runtime.daemon.cleanup_activity import CleanupActivityInput, CleanupMeasurement",
        "from runtime.infrastructure.database import Database",
        "db = Database(Path(sys.argv[1]))",
        "measurement = CleanupMeasurement(False, None, None, 'not_measured')",
        "receipt = CleanupActivityInput(1, 'report_only', 'completed', measurement, measurement, 0, 0, 0, None, None, 'ledger_unavailable')",
        "context = db.get_cleanup_trigger_context('TASK-001', 'dev_agent')",
        "def crash(current):",
        "    if current == sys.argv[2]: os._exit(23)",
        "db._cleanup_completion_stage_hook = crash",
        "db.insert_cleanup_completion(task_id='TASK-001', agent='dev_agent', session_id='sess-crash', output_summary='crash', confidence_score=80, cleanup_activity=receipt, trigger_context=context)",
    ])
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db.db_path), stage],
        cwd=str(__import__("pathlib").Path(__file__).parents[1]), check=False,
    )
    assert completed.returncode == 23
    from runtime.infrastructure.database import Database
    reopened = Database(db.db_path)
    assert len(reopened.get_task_results("TASK-001")) == expected_rows
    assert len(_completed(reopened)) == expected_rows
    context = reopened.get_cleanup_trigger_context("TASK-001", "dev_agent")
    assert context is not None
    assert _write(reopened, context, session="sess-crash") is (expected_rows == 0)


@pytest.mark.parametrize("payload", [
    {"brief_kind": "report_only", "run_number": False},
    {"brief_kind": "report_only", "run_number": 3},
    {"brief_kind": "cleanup", "run_number": 2},
    {"brief_kind": [], "run_number": 1},
])
def test_cleanup_trigger_context_rejects_malformed_payload(db, payload) -> None:
    db.insert_audit_log("TASK-001", "dev_agent", "workspace_cleanup_triggered", payload)
    assert db.get_cleanup_trigger_context("TASK-001", "dev_agent") is None


def test_cleanup_trigger_context_rejects_foreign_duplicate(db) -> None:
    db.insert_audit_log("TASK-001", "dev_agent", "workspace_cleanup_triggered", {"brief_kind": "report_only", "run_number": 1})
    db.insert_audit_log("TASK-001", "other", "workspace_cleanup_triggered", {"brief_kind": "report_only", "run_number": 1})
    assert db.get_cleanup_trigger_context("TASK-001", "dev_agent") is None
