"""Accepted S2-KBVIEW supplements; existing suppression tests remain keepers.

K1.1 owns complete HTTP/durable/transaction isolation beyond count-only keepers.
K2.1 invokes the real facade clock/SQLite failure, beyond the whole-writer mock.
K3.1 owns both fresh import orders, inherited old patches and actual shared lock.
No production seam; expected frames are literal inputs plus independent B state.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import hashlib
import importlib
import inspect
import json
import logging
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading

import pytest

SLUG = "kbview-owned-entry"



OLD = "2026-01-02T00:00:00+00:00"



ENTRY = {
    "slug": SLUG, "title": "KBVIEW authored entry", "type": "reference",
    "topic": "kbview", "tags": ["owned", "durability"],
    "authored_by": "dev_agent", "authored_at": OLD,
    "updated_by": None, "updated_at": None, "source_task": None,
    "supersedes": None, "body": "# KBVIEW\n\nExact authored body.\n",
}



MARKDOWN = (
    "---\nslug: kbview-owned-entry\ntitle: KBVIEW authored entry\n"
    "type: reference\ntopic: kbview\ntags: [owned, durability]\n"
    "authored_by: dev_agent\nauthored_at: '2026-01-02T00:00:00+00:00'\n"
    "updated_by: null\nupdated_at: null\nsource_task: null\nsupersedes: null\n"
    "---\n\n# KBVIEW\n\nExact authored body.\n"
).encode()



def quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'



def sql_state(connection: sqlite3.Connection) -> dict:
    """Read every object, table/column/row/rowid, allocator and index unfiltered."""
    master = [list(row) for row in connection.execute(
        "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name"
    )]
    tables = {}
    for kind, name, _owner, _page, definition in master:
        if kind != "table":
            continue
        table = quoted(name)
        columns = [list(row) for row in connection.execute(f"PRAGMA table_xinfo({table})")]
        without_rowid = "WITHOUT ROWID" in (definition or "").upper()
        select = "*" if without_rowid else "rowid,*"
        order = ",".join(quoted(row[1]) for row in columns) if without_rowid else "rowid"
        cursor = connection.execute(f"SELECT {select} FROM {table} ORDER BY {order}")
        rows = [list(row) for row in cursor]
        for row in rows:
            for index, value in enumerate(row):
                if isinstance(value, bytes):
                    row[index] = {"sqlite_blob_hex": value.hex()}
        tables[name] = {
            "columns": columns, "selected_columns": [column[0] for column in cursor.description],
            "rows": rows,
            "foreign_keys": [list(row) for row in connection.execute(f"PRAGMA foreign_key_list({table})")],
            "index_list": [list(row) for row in connection.execute(f"PRAGMA index_list({table})")],
        }
    indexes = {name: [list(row) for row in connection.execute(f"PRAGMA index_xinfo({quoted(name)})")]
               for kind, name, *_rest in master if kind == "index"}
    return {"master": master, "tables": tables, "indexes": indexes,
            "foreign_keys_enabled": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "foreign_key_check": [list(row) for row in connection.execute("PRAGMA foreign_key_check")],
            "schema_version": connection.execute("PRAGMA schema_version").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0]}



def durable(path: Path) -> dict:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        # Foreign-key enforcement is connection-local; retain its own value.
        return sql_state(connection)
    finally:
        connection.close()



def queues(state, org) -> dict:
    task = state.queue
    sessions = org.sessions
    result = {
        "task": {"items": list(task._queue._queue), "pending_counts": dict(task._pending_counts),
                 "admission_reservations": sorted(task._admission_reservations),
                 "worker_tasks": list(task._worker_tasks), "stopping": task._stopping},
        "org": {name: {"items": list(getattr(org, name)._q._queue)} for name in
                ("thread_queue", "dream_queue", "wake_queue", "schedule_queue")},
        "sessions": {name: copy.copy(getattr(sessions, name)) for name in
                     ("_active", "_pids", "_cancel_controls", "_context_by_session",
                      "_recovery_sessions", "_recovery_deadlines", "_binding_leases")},
    }
    result["org"]["thread_queue"]["published_once_tokens"] = sorted(org.thread_queue._published_once_tokens)
    # Empty containers are observed, not substituted for synthetic owners.
    # Any nonempty callable/session ownership refuses JSON instead of erasing identity.
    for name, values in result["sessions"].items():
        if name == "_recovery_sessions":
            result["sessions"][name] = sorted(values)
    return result



def kb_files(path: Path) -> dict:
    result = {}
    for file in [path, *sorted(path.rglob("*"))]:
        relative = str(file.relative_to(path))
        info = file.lstat()
        result[relative] = {"mode": stat.S_IMODE(info.st_mode),
                            "kind": "dir" if file.is_dir() else "file"}
        if file.is_file():
            result[relative]["bytes_hex"] = file.read_bytes().hex()
    return result



class Logs(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.rows = []
        self.full = []

    def emit(self, record):
        error = None
        if record.exc_info:
            error = {"type": record.exc_info[0].__module__ + "." + record.exc_info[0].__name__,
                     "text": str(record.exc_info[1])}
        self.rows.append({"logger": record.name, "level": record.levelname,
                          "message": record.getMessage(), "exception": error})
        self.full.append(self.format(record))



# Complete facade map includes the approved read_memory_collection_evidence and
# append_memory_collection_transition APIs; all prior signatures, MRO and exports remain.
_PRISTINE_FACADE_SHAPE_SHA256 = "21f6c874dab043f064c3214a1ed1e8c409d060fdacd9164c2bc1f2d846451014"


def _prepare(org, case: str):
    from runtime.infrastructure.kb_store import KBStore
    from runtime.models import TaskRecord, TaskStatus

    store = KBStore(org.root / "kb")
    store.path_for(SLUG).write_bytes(MARKDOWN)
    store.path_for(SLUG).chmod(0o640)
    org.db.insert_task(TaskRecord(
        id="TASK-999001", brief="Owned dormant KBVIEW sentinel", status=TaskStatus.COMPLETED,
        assigned_agent="dev_agent", created_at=datetime.fromisoformat(OLD), updated_at=datetime.fromisoformat(OLD),
    ))
    org.db.insert_audit_log("TASK-999001", "dev_agent", "kbview_owned_sentinel",
                            {"raw": "do not erase causal sentinel", "session_id": None})
    connection = org.db._conn
    connection.execute("INSERT INTO kb_views (slug,view_count,last_viewed_at) VALUES (?, ?, ?)",
                       ("kbview-unrelated-sentinel", 3, OLD))
    if case == "repeat_view":
        connection.execute("INSERT INTO kb_views (slug,view_count,last_viewed_at) VALUES (?, ?, ?)",
                           (SLUG, 7, OLD))
    connection.commit()
    return store


def _state(state, org, store) -> dict:
    return {"same_connection": sql_state(org.db._conn), "durable": durable(org.db.path),
            "in_transaction": org.db._conn.in_transaction, "queues": queues(state, org),
            "kb_files": kb_files(store.root)}


def _expect(baseline: dict, case: str) -> dict:
    expected = copy.deepcopy(baseline)
    instant = "2026-10-06T15:18:01+00:00" if case == "repeat_view" else "2026-10-06T15:18:00+00:00"
    if case in ("first_view", "repeat_view"):
        for key in ("same_connection", "durable"):
            expected[key]["tables"]["kb_views"]["rows"] = [
                [1, "kbview-unrelated-sentinel", 3, OLD],
                [2, SLUG, 8 if case == "repeat_view" else 1, instant],
            ]
        warnings = []
    else:
        warnings = [{"logger": "runtime.daemon.routes.kb", "level": "WARNING",
                     "message": f"kb_views record failed for {SLUG}",
                     "exception": {"type": "builtins.RuntimeError" if case == "facade_clock_error"
                                   else "sqlite3.DatabaseError",
                                   "text": "KBVIEW owned facade clock failure" if case == "facade_clock_error"
                                   else "not authorized"}}]
    expected["http"] = {"status": 200, "raw_hex": json.dumps(
        ENTRY, ensure_ascii=False, separators=(",", ":"),
    ).encode().hex(), "json": ENTRY}
    expected["warnings"] = warnings
    return expected


def _whole_boundary(observed: dict, expected: dict) -> None:
    # This is the FIRST shipping-consumer assertion after the selected request.
    # Unfiltered diagnostics keep durable mismatch and pending transaction causal.
    if observed != expected:
        print("KBVIEW COMPLETE SHIPPING FRAME EXPECTED=" + json.dumps(expected, sort_keys=True))
        print("KBVIEW COMPLETE SHIPPING FRAME OBSERVED=" + json.dumps(observed, sort_keys=True))
    assert observed == expected, "KBVIEW complete shipping consumer frame"


def _exercise(case: str, state, org, app, headers, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from runtime.infrastructure import database as facade

    store = _prepare(org, case)
    baseline = _state(state, org, store)
    expected = _expect(baseline, case)  # Independent expectation precedes selected call.
    instant = "2026-10-06T15:18:01+00:00" if case == "repeat_view" else "2026-10-06T15:18:00+00:00"
    reached = []
    original_clock = facade._now
    log = Logs()
    logger = logging.getLogger("runtime.daemon.routes.kb")
    client = TestClient(app)
    complete = False
    try:
        if case == "facade_clock_error":
            def clock():
                reached.append("facade._now")
                raise RuntimeError("KBVIEW owned facade clock failure")
            monkeypatch.setattr(facade, "_now", clock)
        else:
            monkeypatch.setattr(facade, "_now", lambda: datetime.fromisoformat(instant))
        if case == "sqlite_insert_denied":
            def authorizer(action, arg1, arg2, database, trigger):
                reached.append([action, arg1, arg2, database, trigger])
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and arg1 == "kb_views" else sqlite3.SQLITE_OK
            org.db._conn.set_authorizer(authorizer)
        logger.addHandler(log)
        response = client.get(f"/api/v1/orgs/alpha/kb/{SLUG}",
                              headers=headers | {"X-HappyRanch-Surface": "cli"})
        observed = _state(state, org, store) | {
            "http": {"status": response.status_code, "raw_hex": response.content.hex(), "json": response.json()},
            "warnings": log.rows,
        }
        _whole_boundary(observed, expected)
        complete = True
        if case == "facade_clock_error":
            assert reached == ["facade._now"]
        elif case == "sqlite_insert_denied":
            assert any(row[0] == sqlite3.SQLITE_INSERT and row[1] == "kb_views" for row in reached)
        org.db._conn.set_authorizer(None)
        monkeypatch.setattr(facade, "_now", original_clock)
        stats = client.get("/api/v1/orgs/alpha/kb/stats", headers=headers)
        rows = [{"slug": "kbview-unrelated-sentinel", "view_count": 3, "last_viewed_at": OLD}]
        if case == "first_view":
            rows.append({"slug": SLUG, "view_count": 1, "last_viewed_at": instant})
        elif case == "repeat_view":
            rows.insert(0, {"slug": SLUG, "view_count": 8, "last_viewed_at": instant})
        assert {"status": stats.status_code, "raw_hex": stats.content.hex(), "json": stats.json()} == {
            "status": 200, "raw_hex": json.dumps({"entries": rows}, separators=(",", ":")).encode().hex(),
            "json": {"entries": rows},
        }
    finally:
        org.db._conn.set_authorizer(None)
        monkeypatch.setattr(facade, "_now", original_clock)
        logger.removeHandler(log)
        before_close = durable(org.db.path)
        client.close()
        org.close()  # Close rolls back an unfinished control; no writer runs in cleanup.
        after_close = durable(org.db.path)
        if not complete:
            print("KBVIEW ADVERSE CLEANUP DURABLE=" + json.dumps(
                {"before_close": before_close, "after_close": after_close}, sort_keys=True,
            ))
        asyncio.run(state.close_all())
    assert before_close == after_close == expected["durable"]
    reopened = facade.Database(org.db.path)
    try:
        assert {"sql": sql_state(reopened._conn), "in_transaction": reopened._conn.in_transaction} == {
            "sql": expected["same_connection"], "in_transaction": False,
        }
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["first_view", "repeat_view"])
def test_kbview_complete_committed_shipping_boundary(
    case, tmp_home, daemon_state, org_state, app, auth_headers, monkeypatch,
) -> None:
    """K1.1: exact complete consumer frame and independent durable/reopen state."""
    _exercise(case, daemon_state, org_state, app, auth_headers, monkeypatch)


@pytest.mark.parametrize("case", ["facade_clock_error", "sqlite_insert_denied"])
def test_kbview_real_fault_shipping_boundary(
    case, tmp_home, daemon_state, org_state, app, auth_headers, monkeypatch,
) -> None:
    """K2.1: real selected faults preserve complete read/state/original warning."""
    _exercise(case, daemon_state, org_state, app, auth_headers, monkeypatch)


def _compatibility(order: str, owned: Path, expected_owner: str) -> None:
    # This runs in a new interpreter BEFORE fixture imports can choose import order.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    first = "runtime.infrastructure.database" if order == "facade_first" else "runtime.infrastructure.db.knowledge"
    importlib.import_module(first)
    facade = importlib.import_module("runtime.infrastructure.database")
    knowledge = importlib.import_module("runtime.infrastructure.db.knowledge")
    shared = importlib.import_module("runtime.infrastructure.db._shared")
    models = importlib.import_module("runtime.models")
    methods = {}
    for name in dir(facade.Database):
        value = getattr(facade.Database, name)
        if callable(value):
            try:
                methods[name] = str(inspect.signature(value))
            except (TypeError, ValueError):
                methods[name] = "builtin-no-signature"
    shape = {"methods": methods,
             "mro": [[value.__module__, value.__qualname__] for value in facade.Database.__mro__],
             "type_exports": {name: [value.__module__, value.__qualname__] for name, value in vars(facade).items()
                              if isinstance(value, type)}}
    shape_sha = hashlib.sha256(json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert shape_sha == _PRISTINE_FACADE_SHAPE_SHA256, "pristine complete facade method/signature/MRO/type-export map"
    owners = [owner.__name__ for owner in facade.Database.__mro__ if "record_kb_view" in vars(owner)]
    assert owners == [expected_owner], ("KBVIEW unique selected owner", owners, [expected_owner])
    assert facade.Database is importlib.import_module("runtime.infrastructure.database").Database
    assert facade.KnowledgeMixin is knowledge.KnowledgeMixin
    assert facade._synchronized is knowledge._synchronized is shared._synchronized
    for name, value in vars(facade).items():
        if isinstance(value, type) and value.__module__ == "runtime.models":
            assert value is getattr(models, value.__name__), name
    for name, module in sorted(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if file and (name == "runtime" or name.startswith("runtime.")):
            assert Path(file).resolve().is_relative_to(root), (name, file)

    from fastapi.testclient import TestClient
    from tests.daemon import conftest as fixtures

    patch = pytest.MonkeyPatch()
    state = None
    client = None
    thread = None
    started = threading.Event()
    finished = threading.Event()
    lock_log = Logs()
    route_log = Logs()
    lock_logger = logging.getLogger("happyranch.database.lock")
    route_logger = logging.getLogger("runtime.daemon.routes.kb")
    original_method = facade.Database.record_kb_view
    original_clock = facade._now
    original_time = facade._time
    held = False
    try:
        home = fixtures.tmp_home.__wrapped__(owned, patch)
        runtime = fixtures.runtime.__wrapped__(owned)
        state = fixtures.daemon_state.__wrapped__(runtime)
        org = fixtures.org_state.__wrapped__(state)
        app = fixtures.app.__wrapped__(home, state)
        headers = fixtures.auth_headers.__wrapped__() | {"X-HappyRanch-Surface": "cli"}
        store = _prepare(org, "first_view")
        baseline = _state(state, org, store)
        expected = _expect(baseline, "first_view")
        connection = org.db._conn
        lock = org.db._lock
        calls = []
        clock_calls = []

        def old_class_patch(self, slug):
            calls.append((self is org.db, self._conn is connection, self._lock is lock, slug))
            return original_method(self, slug)

        def old_clock():
            clock_calls.append("facade._now")
            return datetime.fromisoformat("2026-10-06T15:18:00+00:00")

        class WholeTime:
            def __init__(self):
                self.values = iter([0.0, 0.2, 0.3, 0.8])
                self.calls = 0

            def monotonic(self):
                self.calls += 1
                started.set()
                return next(self.values)

        whole_time = WholeTime()
        patch.setattr(facade.Database, "record_kb_view", old_class_patch)
        patch.setattr(facade, "_now", old_clock)
        patch.setattr(facade, "_time", whole_time)
        org.db._lock_warn_threshold_seconds = 0.1
        lock_logger.addHandler(lock_log)
        route_logger.addHandler(route_log)
        client = TestClient(app)
        response_or_error = []

        def request():
            try:
                response_or_error.append(client.get(f"/api/v1/orgs/alpha/kb/{SLUG}", headers=headers))
            except BaseException as error:
                response_or_error.append(error)
            finally:
                finished.set()

        lock.acquire()
        held = True
        thread = threading.Thread(target=request, name="kbview-owned-http")
        thread.start()
        assert started.wait(5), "real shipping writer reached WHOLE old facade time before held RLock"
        assert not finished.is_set(), "real request must wait for the actual retained RLock"
        assert durable(org.db.path) == baseline["durable"]
        lock.release()
        held = False
        thread.join(10)
        assert not thread.is_alive(), "owned shipping HTTP thread joined"
        response = response_or_error[0]
        if isinstance(response, BaseException):
            raise response
        observed = _state(state, org, store) | {
            "http": {"status": response.status_code, "raw_hex": response.content.hex(), "json": response.json()},
            "warnings": route_log.rows,
        }
        _whole_boundary(observed, expected)
        assert calls == [(True, True, True, SLUG)]
        assert clock_calls == ["facade._now"] and whole_time.calls == 4
        assert lock_log.rows == [
            {"logger": "happyranch.database.lock", "level": "WARNING", "exception": None,
             "message": "Database._lock wait 0.200s > threshold 0.100s for Database.record_kb_view (lock convoy may stall other routes)"},
            {"logger": "happyranch.database.lock", "level": "WARNING", "exception": None,
             "message": "Database._lock hold 0.500s > threshold 0.100s for Database.record_kb_view"},
        ]
        patch.setattr(facade, "_time", original_time)
        org.db._lock_warn_threshold_seconds = 1.0

        # Existing qualified instance patch still controls the actual shipping call.
        def instance_failure(_slug):
            raise RuntimeError("KBVIEW old instance patch")

        org.db.record_kb_view = instance_failure
        route_log.rows.clear()
        instance_response = client.get(f"/api/v1/orgs/alpha/kb/{SLUG}", headers=headers)
        expected_instance = copy.deepcopy(expected)
        expected_instance["warnings"] = [{"logger": "runtime.daemon.routes.kb", "level": "WARNING",
            "message": f"kb_views record failed for {SLUG}",
            "exception": {"type": "builtins.RuntimeError", "text": "KBVIEW old instance patch"}}]
        _whole_boundary(_state(state, org, store) | {"warnings": route_log.rows,
            "http": {"status": instance_response.status_code, "raw_hex": instance_response.content.hex(),
                     "json": instance_response.json()}}, expected_instance)
        del org.db.record_kb_view

        # Actual selected failure exercises synchronized finally, then another
        # owned thread proves the same retained RLock is available.
        def clock_failure():
            raise RuntimeError("KBVIEW owned facade clock failure")

        patch.setattr(facade, "_now", clock_failure)
        route_log.rows.clear()
        failure_response = client.get(f"/api/v1/orgs/alpha/kb/{SLUG}", headers=headers)
        expected_failure = copy.deepcopy(expected_instance)
        expected_failure["warnings"][0]["exception"]["text"] = "KBVIEW owned facade clock failure"
        _whole_boundary(_state(state, org, store) | {"warnings": route_log.rows,
            "http": {"status": failure_response.status_code, "raw_hex": failure_response.content.hex(),
                     "json": failure_response.json()}}, expected_failure)
        acquired = []

        def check_finally():
            acquired.append(lock.acquire(timeout=5))
            if acquired[-1]:
                lock.release()

        thread = threading.Thread(target=check_finally, name="kbview-owned-finally")
        thread.start()
        thread.join(6)
        assert not thread.is_alive() and acquired == [True], "actual synchronized finally released retained RLock"
        print(json.dumps({"order": order, "owner": expected_owner, "shape_sha256": shape_sha,
                          "real_class_patch": True, "real_instance_patch": True, "whole_clocks": True,
                          "actual_held_lock": True, "logger": True, "finally_release": True}))
    finally:
        if held:
            lock.release()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive(), "owned compatibility thread cleanup"
        if state is not None and "record_kb_view" in vars(org.db):
            del org.db.record_kb_view
        patch.undo()
        lock_logger.removeHandler(lock_log)
        route_logger.removeHandler(route_log)
        if client is not None:
            client.close()
        if state is not None:
            asyncio.run(state.close_all())
        assert facade._now is original_clock and facade._time is original_time
        assert facade.Database.record_kb_view is original_method


@pytest.mark.parametrize("order", ["facade_first", "knowledge_first"])
def test_kbview_fresh_import_and_shipping_facade_compatibility(order, tmp_path) -> None:
    """K3.1: both fresh imports and real old patches/clocks/lock/logger/finally."""
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), order, str(tmp_path), "KnowledgeMixin"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    _compatibility(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
