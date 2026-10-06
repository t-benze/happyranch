"""TASK-9909/S6-ARCHIVE/A1-A4/revision2 supplements; A2.1 keeps existing tests.

Complete consumer frames precede patch/helper assertions. The accepted four
authoring answers and selected-only controls belong to that unchanged record.
Only the preceding pin tests' unfiltered acquisition mechanism is reused.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import itertools
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

import runtime.infrastructure.database as facade
import runtime.infrastructure.db.audit as audit
import runtime.daemon.routes.threads as routes
from runtime.models import ThreadRecord
from tests.daemon.test_thread_pin_decomposition import _durable, _queues, _json_default


A = "2026-10-06T05:00:00+00:00"
D = "2026-10-06T05:00:01+00:00"
R = "2026-10-06T05:00:02+00:00"
H = "2026-01-02T00:00:00+00:00"
START = "2026-01-01T00:00:00+00:00"
SELECTED = "_set_thread_status_archived_uncommitted"
URL = "/api/v1/orgs/alpha/threads/THR-010/archive"
# Independently authored from the retained serialization contract, never rendered
# by the production writer to construct the expectation.
TRANSCRIPT = (
    "---\nthread_id: THR-010\nsubject: Archive leaf\n"
    "started_at: '2026-01-01T00:00:00+00:00'\n"
    "archived_at: '2026-10-06T05:00:02+00:00'\nparticipants:\n"
    "- dev_agent\n- qa_engineer\nforwarded_from_id: null\nturns_used: 0\n"
    "---\n\n# Summary\n\ndone\n\n# Transcript\n\n"
    "## Message 1 — founder · 2026-10-06T05:00:00+00:00\n"
    "> system: thread archived\n\n"
).encode()


class AuditClock(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return datetime.fromisoformat(D)


class RouteClock(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return datetime.fromisoformat(R)


def _files(root: Path, database_path: Path) -> dict:
    # SQLite's live db/WAL/SHM are acquired through the complete committed reader;
    # all other org files, directories, modes, bytes and temp residue are included.
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if str(path) in {str(database_path), str(database_path) + "-wal", str(database_path) + "-shm"}:
            files[relative] = {"mode": stat.S_IMODE(path.stat().st_mode), "sqlite_committed_reader": True}
        elif path.is_symlink():
            files[relative] = {"mode": stat.S_IMODE(path.lstat().st_mode), "link": os.readlink(path)}
        elif path.is_dir():
            files[relative] = {"mode": stat.S_IMODE(path.stat().st_mode), "directory": True}
        else:
            files[relative] = {"mode": stat.S_IMODE(path.stat().st_mode), "bytes": path.read_bytes()}
    return files


def _frame(state: object, org: object) -> dict:
    return {"durable": _durable(org.db), "in_transaction": org.db._conn.in_transaction,
            "files": _files(org.root, org.db.db_path), "queues": _queues(state, org),
            "subscribers": deepcopy(org.event_bus._subscribers)}


def _receipt(tmp_path: Path) -> Path:
    return Path(os.environ.get("S6_ARCHIVE_RECEIPTS", str(tmp_path / "frames")))


def _compare(actual: dict, expected: dict, root: Path, label: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / (label + ".json")
    path.write_text(json.dumps({"actual": actual, "expected": expected},
                              ensure_ascii=False, indent=2, default=_json_default) + "\n")
    assert actual == expected, f"complete shipping frame mismatch: {path}"


def _freeze_expected(expected: dict, root: Path, label: str) -> None:
    """Construct and retain the COMPLETE literal action oracle before the POST."""
    root.mkdir(parents=True, exist_ok=True)
    (root / (label + "-expected-before-http.json")).write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def _success(before: dict, org: object, historical: bool) -> dict:
    expected = deepcopy(before)
    tables = expected["durable"]["tables"]
    # Column coordinates are independently named; every other raw cell survives.
    for table, changes in (
        ("threads", {"status": "archived", "summary": "done",
                     "archived_at": H if historical else A,
                     "transcript_path": str(org.root / "threads/THR-010.md")}),
        ("thread_participants", {"agent_session_id": None, "last_resumed_seq": 0}),
    ):
        data = tables[table]
        columns = [c[1] for c in data["columns"]]
        offset = int(data["has_rowid"])
        identity = "id" if table == "threads" else "thread_id"
        for index, row in enumerate(data["rows"]):
            if row[columns.index(identity) + offset] == "THR-010":
                replaced = list(row)
                for column, value in changes.items():
                    replaced[columns.index(column) + offset] = value
                data["rows"][index] = tuple(replaced)
    # Nonzero unrelated rows initialize both allocators in fixture setup.
    sequence = tables["sqlite_sequence"]["rows"]
    ids = {row[1]: row[2] for row in sequence}
    n, m = ids["audit_log"], ids["thread_messages"]
    tables["sqlite_sequence"]["rows"] = [
        (row[0], row[1], row[2] + (2 if row[1] == "audit_log" else 1 if row[1] == "thread_messages" else 0))
        for row in sequence
    ]
    for i, action, payload in (
        (n + 1, "thread_session_invalidated", '{"reason": "archive", "rows": 2}'),
        (n + 2, "thread_archived", '{"turns_used": 0}'),
    ):
        tables["audit_log"]["rows"].append((i, i, "THR-010", "founder", action, payload, D))
    tables["thread_messages"]["rows"].append(
        (m + 1, m + 1, "THR-010", 1, "founder", "system", None, None, None,
         '{"kind_tag": "archived", "summary": "done"}', None, None, A)
    )
    expected["files"]["threads/THR-010.md"] = {"mode": 0o600, "bytes": TRANSCRIPT}
    return {"http": {"status": 200, "body": {"thread_id": "THR-010", "status": "archived",
                                            "transcript_path": str(org.root / "threads/THR-010.md")}},
            **expected}


@contextmanager
def _venue(state: object, org: object, app: object, headers: dict,
           patch: pytest.MonkeyPatch, historical: bool, root: Path):
    patch.setattr(facade, "_now", lambda: datetime.fromisoformat(A))
    patch.setattr(audit, "datetime", AuditClock)
    patch.setattr(routes, "datetime", RouteClock)
    org.db.insert_thread(ThreadRecord(
        id="THR-010", subject="Archive leaf", started_at=datetime.fromisoformat(START),
        archived_at=datetime.fromisoformat(H) if historical else None,
        summary="old" if historical else None, turn_cap=500, turns_used=0,
    ))
    org.db.insert_thread(ThreadRecord(id="THR-002", subject="Unrelated",
                                     started_at=datetime(2026, 1, 3, tzinfo=timezone.utc)))
    for agent, date, session, seq in (
        ("dev_agent", START, "sess-dev", 3), ("qa_engineer", H, "sess-qa", 2),
    ):
        patch.setattr(facade, "_now", lambda date=date: datetime.fromisoformat(date))
        org.db.add_thread_participant("THR-010", agent, added_by="founder")
        org.db.update_thread_session("THR-010", agent, agent_session_id=session, last_resumed_seq=seq)
    patch.setattr(facade, "_now", lambda: datetime.fromisoformat(A))
    org.db.add_thread_participant("THR-002", "dev_agent", added_by="founder")
    org.db.update_thread_session("THR-002", "dev_agent", agent_session_id="sentinel", last_resumed_seq=7)
    from runtime.models import ThreadMessageKind
    org.db.append_thread_message(thread_id="THR-002", speaker="founder",
                                 kind=ThreadMessageKind.MESSAGE, body_markdown="unrelated bytes")
    org.db.insert_audit_log("THR-002", "founder", "fixture_unrelated", {"literal": "keep"})
    sentinel = org.root / "threads/unrelated.bin"
    sentinel.write_bytes(b"\x00unchanged\xff\n")
    sentinel.chmod(0o640)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(headers)
    try:
        before = _frame(state, org)
        assert all(not before["queues"][name]["items"] for name in ("task", "thread", "dream", "wake", "schedule"))
        assert before["queues"]["workers"] == [] and before["subscribers"] == {}
        # Literal seeded raw coordinates establish B before any consumer action.
        participants = before["durable"]["tables"]["thread_participants"]["rows"]
        assert participants == [
            (1, "THR-010", "dev_agent", START, "founder", "sess-dev", 3),
            (2, "THR-010", "qa_engineer", H, "founder", "sess-qa", 2),
            (3, "THR-002", "dev_agent", A, "founder", "sentinel", 7),
        ]
        assert before["durable"]["tables"]["threads"]["rows"] == [
            (1, "THR-010", "Archive leaf", START, H if historical else None,
             "open", None, None, 500, 0, "old" if historical else None,
             None, None, 1, "founder", None, None),
            (2, "THR-002", "Unrelated", "2026-01-03T00:00:00+00:00", None,
             "open", None, None, 500, 0, None, None, None, 1, "founder", None, None),
        ]
        root.mkdir(parents=True, exist_ok=True)
        (root / ("B-" + str(historical) + ".json")).write_text(
            json.dumps(before, ensure_ascii=False, indent=2, default=_json_default) + "\n")
        yield client, before
    finally:
        client.close()


@pytest.fixture
def archive_venue(daemon_state, org_state, app, auth_headers, monkeypatch, tmp_path):
    try:
        yield lambda historical: _venue(daemon_state, org_state, app, auth_headers,
                                        monkeypatch, historical, _receipt(tmp_path))
    finally:
        for org in daemon_state.orgs.values():
            org.close()
        for store in (daemon_state.metrics_store, daemon_state.direct_connect_authority_store):
            if store is not None:
                store.close()


def _durable_reopen(db: facade.Database, expected: dict, root: Path, label: str) -> None:
    path = db.db_path
    db.close()
    reopened = facade.Database(path)
    try:
        _compare(_durable(reopened), expected["durable"], root, label)
    finally:
        reopened.close()


def _success_request(client: TestClient, before: dict, state: object, org: object,
                     historical: bool, patch: pytest.MonkeyPatch, root: Path, label: str) -> None:
    original = getattr(facade.Database, SELECTED)
    lock = org.db._lock
    calls, lock_depths, records = [], [], []
    ticks = itertools.count()

    class WholeTime:
        @staticmethod
        def monotonic() -> int:
            return next(ticks)

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    def spy(self: facade.Database, thread_id: str, *, summary: str | None = None) -> None:
        calls.append((self, thread_id, summary, self._lock))
        return original(self, thread_id, summary=summary)

    def trace(sql: str) -> None:
        if sql.startswith("UPDATE threads SET status ="):
            lock_depths.append((org.db._lock, org.db._lock._recursion_count()))

    logger = logging.getLogger("happyranch.database.lock")
    handler, old_level = Capture(), logger.level
    expected = _success(before, org, historical)
    _freeze_expected(expected, root, label)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    org.db._conn.set_trace_callback(trace)
    try:
        with patch.context() as local:
            local.setattr(facade.Database, SELECTED, spy)
            local.setattr(facade, "_time", WholeTime)
            local.setattr(org.db, "_lock_warn_threshold_seconds", 0.5)
            response = client.post(URL, json={"summary": "  done  "})
            actual = {"http": {"status": response.status_code, "body": response.json()}, **_frame(state, org)}
            _compare(actual, expected, root, label)
        assert calls == [(org.db, "THR-010", "done", lock)]
        assert lock_depths == [(lock, 2)]
        assert org.db._lock is lock and not lock._is_owned()
        selected_messages = [r.getMessage() for r in records if "Database." + SELECTED in r.getMessage()]
        assert selected_messages == [
            f"Database._lock wait 1.000s > threshold 0.500s for Database.{SELECTED} (lock convoy may stall other routes)",
            f"Database._lock hold 1.000s > threshold 0.500s for Database.{SELECTED}",
        ]
        assert getattr(facade.Database, SELECTED) is original
    finally:
        org.db._conn.set_trace_callback(None)
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    _durable_reopen(org.db, expected, root, label + "-reopen")


@pytest.mark.parametrize("history", [False, True], ids=["first_archive", "resumed_history"])
def test_archive_complete_frame(history, archive_venue, daemon_state, org_state, monkeypatch, tmp_path) -> None:
    with archive_venue(history) as (client, before):
        _success_request(client, before, daemon_state, org_state, history, monkeypatch,
                         _receipt(tmp_path), "A1.1-" + str(history))


@pytest.mark.parametrize("failure", ["reset", "audit"])
def test_archive_complete_failure_recovery_frame(failure, archive_venue, daemon_state,
                                                org_state, monkeypatch, tmp_path) -> None:
    with archive_venue(False) as (client, before):
        error_expected = {"http": {"status": 500, "body": "Internal Server Error"}, **deepcopy(before)}
        success_expected = _success(before, org_state, False)
        _freeze_expected(error_expected, _receipt(tmp_path), "A1.2-E-" + failure)
        _freeze_expected(success_expected, _receipt(tmp_path), "A1.2-F-" + failure)
        connection = org_state.db._conn
        original = org_state.db.insert_audit_log_uncommitted

        def fault(*args: object, **kwargs: object) -> None:
            if failure == "reset" or kwargs.get("action") == "thread_session_invalidated":
                raise RuntimeError("selected downstream " + failure)
            return original(*args, **kwargs)

        with monkeypatch.context() as local:
            attribute = "_reset_thread_sessions_for_thread_uncommitted" if failure == "reset" else "insert_audit_log_uncommitted"
            local.setattr(org_state.db, attribute, fault)
            response = client.post(URL, json={"summary": "  done  "})
        error_actual = {"http": {"status": response.status_code, "body": response.text}, **_frame(daemon_state, org_state)}
        # FIRST decisive action assertion is the complete committed E at HTTP500.
        _compare(error_actual, error_expected, _receipt(tmp_path), "A1.2-E-" + failure)
        assert org_state.db._conn is connection
        recovered = client.post(URL, json={"summary": "  done  "})
        actual = {"http": {"status": recovered.status_code, "body": recovered.json()}, **_frame(daemon_state, org_state)}
        _compare(actual, success_expected, _receipt(tmp_path), "A1.2-F-" + failure)
        assert org_state.db._conn is connection
        _durable_reopen(org_state.db, success_expected, _receipt(tmp_path), "A1.2-reopen-" + failure)


@pytest.mark.parametrize("order", ["facade_first", "mixin_first"])
def test_archive_fresh_import_order(order: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    program = """
import importlib, pathlib, sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root))
first='runtime.infrastructure.database' if sys.argv[2]=='facade_first' else 'runtime.infrastructure.db.threads'
importlib.import_module(first)
import runtime.infrastructure.database as facade
from runtime.infrastructure.db.threads import ThreadsMixin
from runtime.infrastructure.db._shared import _synchronized
import inspect
selected=facade.Database._set_thread_status_archived_uncommitted
owners=[c for c in facade.Database.__mro__ if '_set_thread_status_archived_uncommitted' in vars(c)]
assert owners in ([facade.Database],[ThreadsMixin])
assert selected is vars(owners[0])['_set_thread_status_archived_uncommitted']
assert facade._synchronized is _synchronized
assert str(inspect.signature(selected)) == "(self, thread_id: 'str', *, summary: 'str | None' = None) -> 'None'"
assert selected.__wrapped__.__defaults__ is None
assert selected.__wrapped__.__kwdefaults__ == {'summary':None}
assert facade.Database.__module__ == 'runtime.infrastructure.database'
for name,module in list(sys.modules.items()):
    if name.startswith('runtime.'):
        for value in vars(module).values():
            if isinstance(value,type) and value.__name__=='Database':
                assert value is facade.Database
import pytest
exit_code=pytest.main(['tests/daemon/test_thread_archive_decomposition.py::test_archive_complete_frame[first_archive]','-q','-n','0','--basetemp',sys.argv[3]])
for name,module in list(sys.modules.items()):
    if name=='runtime' or name.startswith('runtime.'):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root),(name,module.__file__)
sys.exit(exit_code)
"""
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "LC_ALL": "C.UTF-8",
           "HAPPYRANCH_DAEMON_HOME": str(tmp_path / "daemon-home")}
    if "S6_ARCHIVE_RECEIPTS" in os.environ:
        env["S6_ARCHIVE_RECEIPTS"] = str(_receipt(tmp_path) / order)
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(root), order, str(tmp_path / "child-pytest")],
                          cwd=root, env=env, capture_output=True, timeout=55)
    destination = _receipt(tmp_path) / order
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "child.stdout").write_bytes(proc.stdout)
    (destination / "child.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, proc.stdout.decode() + proc.stderr.decode()
