import subprocess
import sys
import threading
from pathlib import Path

import pytest

from runtime.daemon.cleanup_activity import CleanupActivityInput, CleanupMeasurement
from runtime.infrastructure.database import Database


def _receipt() -> CleanupActivityInput:
    unavailable = CleanupMeasurement(False, None, None, "not_measured")
    return CleanupActivityInput(1, "report_only", "completed", unavailable, unavailable, 0, 0, 0, None, None, "ledger_unavailable")


def _trigger(db: Database, *, agent: str = "dev_agent") -> dict:
    db.insert_audit_log("TASK-001", agent, "workspace_cleanup_triggered", {"brief_kind": "report_only", "run_number": 1})
    context = db.get_cleanup_trigger_context("TASK-001", agent)
    assert context is not None
    return context


def _write(db: Database, context: dict, *, session: str = "sess-cleanup", summary: str = "receipt") -> bool:
    return db.insert_cleanup_completion(task_id="TASK-001", agent="dev_agent", session_id=session, output_summary=summary, confidence_score=80, cleanup_activity=_receipt(), trigger_context=context)


def _completed(db: Database) -> list[dict]:
    return [row for row in db.get_audit_logs("TASK-001") if row["action"] == "workspace_cleanup_completed"]


def _assert_pair(db: Database, summary: str, session: str) -> None:
    results, completed = db.get_task_results("TASK-001"), _completed(db)
    assert len(results) == len(completed) == 1
    assert results[0]["output_summary"] == summary and results[0]["session_id"] == session
    payload = completed[0]["payload"]
    assert payload["task_result_id"] == results[0]["id"]
    assert (payload["task_id"], payload["agent"], payload["session_id"]) == ("TASK-001", "dev_agent", session)
    assert (payload["trigger_audit_id"] > 0, payload["run_number"], payload["mode"], payload["outcome"]) == (True, 1, "report_only", "completed")


class _ConnectionWrapper:
    def __init__(self, connection, *, on_begin=None, fail_commit: bool = False) -> None:
        self._connection, self._on_begin, self._fail_commit = connection, on_begin, fail_commit

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def execute(self, sql, *args, **kwargs):
        if sql == "BEGIN IMMEDIATE" and self._on_begin is not None:
            self._on_begin()
        return self._connection.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        if self._fail_commit:
            raise RuntimeError("injected commit failure")
        self._connection.commit()


class _ObservedLock:
    def __init__(self, lock, attempted: threading.Event) -> None:
        self._lock, self._attempted = lock, attempted

    def acquire(self, *args, **kwargs):
        self._attempted.set()
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()


def test_cleanup_completion_commits_result_and_audit_together(db) -> None:
    assert _write(db, _trigger(db)) is True
    _assert_pair(Database(db.db_path), "receipt", "sess-cleanup")


def test_legacy_insert_task_result_still_commits_from_independent_connection(db) -> None:
    db.insert_task_result(task_id="TASK-001", agent="dev_agent", session_id="sess-ordinary", output_summary="ordinary", confidence_score=80)
    assert [r["output_summary"] for r in Database(db.db_path).get_task_results("TASK-001")] == ["ordinary"]


@pytest.mark.parametrize("writer", ["result", "audit"])
def test_actual_write_fault_rolls_back_reopens_and_retries(db, monkeypatch, writer) -> None:
    context = _trigger(db)
    method = "_insert_task_result_uncommitted" if writer == "result" else "insert_audit_log_uncommitted"
    original = getattr(db, method)

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError(f"injected {writer} write failure")

    monkeypatch.setattr(db, method, write_then_fail)
    with pytest.raises(RuntimeError, match=f"injected {writer} write failure"):
        _write(db, context)
    reopened = Database(db.db_path)
    assert reopened.get_task_results("TASK-001") == [] and _completed(reopened) == []
    assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent")) is True
    _assert_pair(Database(db.db_path), "receipt", "sess-cleanup")


def test_actual_commit_fault_rolls_back_reopens_and_retries(db) -> None:
    context, original = _trigger(db), db._conn
    db._conn = _ConnectionWrapper(original, fail_commit=True)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        _write(db, context)
    db._conn = original
    reopened = Database(db.db_path)
    assert reopened.get_task_results("TASK-001") == [] and _completed(reopened) == []
    assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent")) is True
    _assert_pair(Database(db.db_path), "receipt", "sess-cleanup")


def test_orphan_receipt_is_not_grafted(db) -> None:
    context = _trigger(db)
    db.insert_audit_log("TASK-001", "dev_agent", "workspace_cleanup_completed", {"corrupt": True})
    with pytest.raises(RuntimeError, match="cleanup_receipt_already_present"):
        _write(db, context)
    assert db.get_task_results("TASK-001") == []


@pytest.mark.parametrize("mutation", ["changed", "missing", "duplicate", "foreign"])
def test_transaction_time_trigger_change_is_atomic_no_write(db, mutation) -> None:
    context = _trigger(db)
    if mutation == "changed":
        db._conn.execute("UPDATE audit_log SET payload = ? WHERE id = ?", ('{"brief_kind":"cleanup","run_number":3}', context["trigger_audit_id"]))
        db._conn.commit()
    elif mutation == "missing":
        db._conn.execute("DELETE FROM audit_log WHERE id = ?", (context["trigger_audit_id"],)); db._conn.commit()
    else:
        db.insert_audit_log("TASK-001", "other" if mutation == "foreign" else "dev_agent", "workspace_cleanup_triggered", {"brief_kind": "report_only", "run_number": 1})
    with pytest.raises(RuntimeError, match="cleanup_context_changed"):
        _write(db, context, session=f"sess-{mutation}")
    reopened = Database(db.db_path)
    assert reopened.get_task_results("TASK-001") == [] and _completed(reopened) == []


def test_stale_context_retry_preserves_original_winner(db) -> None:
    context = _trigger(db)
    assert _write(db, context, summary="winner") is True
    db._conn.execute("UPDATE audit_log SET payload = ? WHERE id = ?", ('{"brief_kind":"cleanup","run_number":3}', context["trigger_audit_id"])); db._conn.commit()
    with pytest.raises(RuntimeError, match="cleanup_context_changed"):
        _write(db, context, session="sess-retry", summary="replacement")
    _assert_pair(Database(db.db_path), "winner", "sess-cleanup")


def test_unrelated_transaction_stays_under_caller_control_and_independent_visibility(db) -> None:
    context, other = _trigger(db), Database(db.db_path)
    db._conn.execute("BEGIN")
    db._conn.execute("INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)", ("TASK-unrelated", "dev_agent", "unrelated", "{}", "2026-01-01T00:00:00+00:00"))
    with pytest.raises(RuntimeError, match="unrelated_transaction"):
        _write(db, context)
    assert db._conn.in_transaction and other._conn.execute("SELECT COUNT(*) FROM audit_log WHERE task_id = 'TASK-unrelated'").fetchone()[0] == 0
    db._conn.rollback()
    assert other._conn.execute("SELECT COUNT(*) FROM audit_log WHERE task_id = 'TASK-unrelated'").fetchone()[0] == 0
    db._conn.execute("BEGIN"); db._conn.execute("INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)", ("TASK-unrelated", "dev_agent", "unrelated", "{}", "2026-01-01T00:00:00+00:00")); db._conn.commit()
    assert other._conn.execute("SELECT COUNT(*) FROM audit_log WHERE task_id = 'TASK-unrelated'").fetchone()[0] == 1
    assert _write(db, context) is True


def test_rlock_excludes_contender_at_actual_lock_acquisition(db, monkeypatch) -> None:
    context, entered, release, attempted = _trigger(db), threading.Event(), threading.Event(), threading.Event()
    original = db._insert_task_result_uncommitted

    def hold_after_result(*args, **kwargs):
        result = original(*args, **kwargs); entered.set(); assert release.wait(2); return result

    monkeypatch.setattr(db, "_insert_task_result_uncommitted", hold_after_result)
    db._lock = _ObservedLock(db._lock, attempted); attempted.clear()
    outcomes: list[object] = []

    def invoke(label: str) -> None:
        try: outcomes.append((label, _write(db, context, summary=label)))
        except BaseException as exc: outcomes.append((label, exc))

    first, second = threading.Thread(target=invoke, args=("winner",)), threading.Thread(target=invoke, args=("loser",))
    first.start(); assert entered.wait(2); second.start(); assert attempted.wait(2)
    release.set(); first.join(2); second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(outcomes, key=lambda item: item[0]) == [("loser", False), ("winner", True)]
    _assert_pair(Database(db.db_path), "winner", "sess-cleanup")


def test_two_connections_contend_at_sqlite_and_first_wins_without_graft(db, monkeypatch) -> None:
    context, other = _trigger(db), Database(db.db_path)
    entered, release, second_begin = threading.Event(), threading.Event(), threading.Event()
    original = db._insert_task_result_uncommitted

    def hold_after_result(*args, **kwargs):
        result = original(*args, **kwargs); entered.set(); assert release.wait(2); return result

    monkeypatch.setattr(db, "_insert_task_result_uncommitted", hold_after_result)
    other._conn = _ConnectionWrapper(other._conn, on_begin=second_begin.set)
    outcomes: list[object] = []

    def invoke(target: Database, current: dict, label: str) -> None:
        try: outcomes.append((label, _write(target, current, summary=label)))
        except BaseException as exc: outcomes.append((label, exc))

    first = threading.Thread(target=invoke, args=(db, context, "winner")); first.start(); assert entered.wait(2)
    second = threading.Thread(target=invoke, args=(other, other.get_cleanup_trigger_context("TASK-001", "dev_agent"), "loser")); second.start(); assert second_begin.wait(2)
    release.set(); first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(outcomes, key=lambda item: item[0]) == [("loser", False), ("winner", True)]
    _assert_pair(Database(db.db_path), "winner", "sess-cleanup")


@pytest.mark.parametrize("stage, expected_rows", [("result", 0), ("audit", 0), ("commit", 1)])
def test_subprocess_crash_is_bounded_and_reopen_retry_is_exact(db, stage, expected_rows) -> None:
    _trigger(db)
    script = "\n".join(["import os, sys", "from pathlib import Path", "from runtime.daemon.cleanup_activity import CleanupActivityInput, CleanupMeasurement", "from runtime.infrastructure.database import Database", "db = Database(Path(sys.argv[1]))", "stage = sys.argv[2]", "m = CleanupMeasurement(False, None, None, 'not_measured')", "receipt = CleanupActivityInput(1, 'report_only', 'completed', m, m, 0, 0, 0, None, None, 'ledger_unavailable')", "context = db.get_cleanup_trigger_context('TASK-001', 'dev_agent')", "if stage == 'result':", "  original = db._insert_task_result_uncommitted", "  def crash_result(*a, **k):", "    original(*a, **k); os._exit(23)", "  db._insert_task_result_uncommitted = crash_result", "elif stage == 'audit':", "  original = db.insert_audit_log_uncommitted", "  def crash_audit(*a, **k):", "    original(*a, **k); os._exit(23)", "  db.insert_audit_log_uncommitted = crash_audit", "else:", "  original = db._conn", "  class CrashConnection:", "    def __getattr__(self, n): return getattr(original, n)", "    def commit(self): original.commit(); os._exit(23)", "  db._conn = CrashConnection()", "db.insert_cleanup_completion(task_id='TASK-001', agent='dev_agent', session_id='sess-crash', output_summary='crash', confidence_score=80, cleanup_activity=receipt, trigger_context=context)"])
    completed = subprocess.run([sys.executable, "-c", script, str(db.db_path), stage], cwd=str(Path(__file__).parents[1]), check=False, timeout=5, capture_output=True, text=True)
    assert completed.returncode == 23
    reopened = Database(db.db_path)
    if expected_rows:
        _assert_pair(reopened, "crash", "sess-crash")
        assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent"), session="sess-crash") is False
    else:
        assert reopened.get_task_results("TASK-001") == [] and _completed(reopened) == []
        assert _write(reopened, reopened.get_cleanup_trigger_context("TASK-001", "dev_agent"), session="sess-crash", summary="retry") is True
        _assert_pair(Database(db.db_path), "retry", "sess-crash")


@pytest.mark.parametrize("payload", [{"brief_kind": "report_only", "run_number": False}, {"brief_kind": "report_only", "run_number": 3}, {"brief_kind": "cleanup", "run_number": 2}, {"brief_kind": [], "run_number": 1}])
def test_cleanup_trigger_context_rejects_malformed_payload(db, payload) -> None:
    db.insert_audit_log("TASK-001", "dev_agent", "workspace_cleanup_triggered", payload)
    assert db.get_cleanup_trigger_context("TASK-001", "dev_agent") is None
