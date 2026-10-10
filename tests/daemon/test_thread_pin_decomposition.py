"""Accepted S6-PIN supplements; P1.2/P3.1 remain existing keeper owners.

The independently accepted four answers and mutation map are in
TASK-9867/S6-PIN/P1-P4/revision2. No production seam is added here.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import itertools
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

import runtime.infrastructure.database as facade
import runtime.infrastructure.db.audit as audit
from runtime.models import ThreadMessageKind, ThreadRecord


PIN_TIME = "2026-10-06T05:00:00+00:00"
AUDIT_TIME = "2026-10-06T05:00:01+00:00"
OLD_PIN = "2026-01-02T00:00:00+00:00"
# Complete 563-method inventory: all 561 prior signatures are unchanged;
# approved memory collection evidence/transition APIs add two signatures.
METHOD_SIGNATURE_SHA256 = "cbf5ef739eccdcb423eedfbd33a9a2b9b4a67fc7f4ff34d25b8488619de09fcc"
# Actual protocol4 bytes frozen on pristine61319854 before relocation.
PRISTINE_PICKLES = (
    "gASVMAAAAAAAAACMH3J1bnRpbWUuaW5mcmFzdHJ1Y3R1cmUuZGF0YWJhc2WUjAhEYXRhYmFzZZSTlC4=",
    "gASVTgAAAAAAAACMH3J1bnRpbWUuaW5mcmFzdHJ1Y3R1cmUuZGF0YWJhc2WUjCZEYXRhYmFzZS5zZXRfdGhyZWFkX3Bpbm5lZF91bmNvbW1pdHRlZJSTlC4=",
    "gASVIwAAAAAAAACMDnJ1bnRpbWUubW9kZWxzlIwMVGhyZWFkUmVjb3JklJOULg==",
)
MRO_MODULES = (
    "runtime.infrastructure.database.Database",
    *(f"runtime.infrastructure.db.{module}.{name}" for module, name in (
        ("tasks", "TasksMixin"), ("dreams", "DreamsMixin"),
        ("knowledge", "KnowledgeMixin"), ("jobs", "JobsMixin"),
        ("attachments", "AttachmentsMixin"), ("audit", "AuditMixin"),
        ("sessions", "SessionsMixin"), ("workspace_cleanup", "WorkspaceCleanupMixin"),
        ("threads", "ThreadsMixin"), ("reply_delivery", "ReplyDeliveryMixin"),
        ("reply_exchange", "ReplyExchangeMixin"), ("schema", "SchemaMixin"),
        ("authority_v1", "AuthorityV1Mixin"), ("authority_policy", "AuthorityPolicyMixin"),
        ("authority_v2_attempts", "AuthorityV2AttemptsMixin"),
        ("authority_v2_continuation", "AuthorityV2ContinuationMixin"),
    )),
    "builtins.object",
)


class AuditClock:
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return datetime.fromisoformat(AUDIT_TIME)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _durable(db: facade.Database) -> dict:
    """Separate reader: every schema object, column and raw row, without filters."""
    reader = sqlite3.connect(f"file:{db.db_path}?mode=ro", uri=True)
    try:
        objects = reader.execute(
            "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        tables = {}
        for kind, name, _, _, sql in objects:
            if kind != "table":
                continue
            quoted = _quoted(name)
            columns = reader.execute(f"PRAGMA table_xinfo({quoted})").fetchall()
            indexes = reader.execute(f"PRAGMA index_list({quoted})").fetchall()
            # Include rowid where SQLite supplies it; WITHOUT ROWID uses complete PK order.
            without_rowid = "WITHOUT ROWID" in (sql or "").upper()
            order = ",".join(_quoted(c[1]) for c in sorted(columns, key=lambda c: c[5]) if c[5])
            query = f"SELECT * FROM {quoted} ORDER BY {order}" if without_rowid else f"SELECT rowid,* FROM {quoted} ORDER BY rowid"
            tables[name] = {
                "columns": columns, "indexes": indexes,
                "index_columns": {i[1]: reader.execute(f"PRAGMA index_xinfo({_quoted(i[1])})").fetchall() for i in indexes},
                "foreign_keys": reader.execute(f"PRAGMA foreign_key_list({quoted})").fetchall(),
                "rows": reader.execute(query).fetchall(), "has_rowid": not without_rowid,
            }
        return {"objects": objects, "tables": tables}
    finally:
        reader.close()


def _async_queue(queue: object) -> dict:
    return {
        "items": list(queue._queue), "getters": list(queue._getters),
        "putters": list(queue._putters), "unfinished": queue._unfinished_tasks,
        "finished": queue._finished.is_set(), "loop": queue._loop,
        "maxsize": queue._maxsize,
    }


def _queues(state: object, org: object) -> dict:
    task = state.queue
    with task._admission_lock:
        frame = {
            "task": _async_queue(task._queue), "pending": dict(task._pending_counts),
            "reservations": sorted(task._admission_reservations),
            "workers": list(task._worker_tasks), "stopping": task._stopping,
            "metrics_registry_identity": id(task._metrics_registry),
            "admission_lock_identity": id(task._admission_lock),
        }
    for name in ("thread", "dream", "wake", "schedule"):
        frame[name] = _async_queue(getattr(org, name + "_queue")._q)
    frame["published_once_tokens"] = sorted(org.thread_queue._published_once_tokens)
    return frame


def _state_frame(state: object, org: object) -> dict:
    return {"durable": _durable(org.db), "in_transaction": org.db._conn.in_transaction,
            "queues": _queues(state, org)}


def _expected_transition(before: dict, pinned: bool) -> dict:
    """Independent literal pin/audit delta; all other bytes and fields survive."""
    expected = deepcopy(before)
    tables = expected["durable"]["tables"]
    threads = tables["threads"]
    columns = [c[1] for c in threads["columns"]]
    offset = int(threads["has_rowid"])
    for i, row in enumerate(threads["rows"]):
        if row[columns.index("id") + offset] == "THR-010":
            replacement = list(row)
            replacement[columns.index("pinned_at") + offset] = PIN_TIME if pinned else None
            threads["rows"][i] = tuple(replacement)
    sequence = tables["sqlite_sequence"]
    sequence_row = next(row for row in sequence["rows"] if row[1] == "audit_log")
    next_id = sequence_row[2] + 1
    sequence["rows"] = [
        (row[0], row[1], next_id) if row[1] == "audit_log" else row
        for row in sequence["rows"]
    ]
    raw_audit = (next_id, "THR-010", "founder",
                 "thread_pinned" if pinned else "thread_unpinned",
                 '{"pinned": true}' if pinned else '{"pinned": false}', AUDIT_TIME)
    tables["audit_log"]["rows"].append((next_id, *raw_audit))
    return expected


def _json_default(value: object) -> dict:
    if isinstance(value, bytes):
        return {"sqlite_blob_base64": base64.b64encode(value).decode()}
    raise TypeError(type(value).__name__)


def _compare(actual: dict, expected: dict, root: Path, label: str, response: object = None) -> None:
    # Both COMPLETE frames exist before the single decisive assertion.
    receipt = {"actual": actual, "expected": expected}
    if response is not None:
        receipt["observed_http_body"] = response.text
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{label}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    )
    assert actual == expected, f"complete shipping frame mismatch; raw actual/expected: {root / (label + '.json')}"


@contextmanager
def _venue(state: object, org: object, app: object, headers: dict,
           monkeypatch: pytest.MonkeyPatch, pinned: bool):
    monkeypatch.setattr(facade, "_now", lambda: datetime.fromisoformat(PIN_TIME))
    monkeypatch.setattr(audit, "datetime", AuditClock)
    org.db.insert_thread(ThreadRecord(
        id="THR-010", subject="Selected pin", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        pinned_at=datetime.fromisoformat(OLD_PIN) if pinned else None,
    ))
    org.db.insert_thread(ThreadRecord(
        id="THR-002", subject="Unrelated", started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    ))
    org.db.add_thread_participant("THR-010", "dev_agent", added_by="founder")
    org.db.append_thread_message(thread_id="THR-010", speaker="founder",
                                 kind=ThreadMessageKind.MESSAGE, body_markdown="Frozen message")
    org.db.insert_audit_log("THR-002", "founder", "fixture_unrelated", {"literal": "keep"})
    client = TestClient(app, raise_server_exceptions=False)
    try:
        before = _state_frame(state, org)
        # Setup diagnostics, never accepted mutation RED: actual queues are seeded empty.
        assert all(not before["queues"][name]["items"] for name in ("task", "thread", "dream", "wake", "schedule"))
        assert before["queues"]["workers"] == []
        yield client, before
    finally:
        client.close()


@pytest.fixture
def pin_venue(daemon_state, org_state, app, auth_headers, monkeypatch):
    try:
        yield lambda pinned: _venue(daemon_state, org_state, app, auth_headers, monkeypatch, pinned)
    finally:
        for org in daemon_state.orgs.values():
            org.close()
        for store in (daemon_state.metrics_store, daemon_state.direct_connect_authority_store):
            if store is not None:
                store.close()


def _receipt_root(tmp_path: Path) -> Path:
    return Path(os.environ.get("S6_PIN_RECEIPTS", str(tmp_path / "frames")))


@pytest.mark.parametrize("pinned", [True, False], ids=["pin", "unpin"])
def test_pin_complete_committed_frame(pinned, pin_venue, daemon_state, org_state,
                                      auth_headers, tmp_path) -> None:
    with pin_venue(not pinned) as (client, before):
        expected = {"http": {"status": 200, "body": {"thread_id": "THR-010", "pinned": pinned}},
                    **_expected_transition(before, pinned)}
        response = client.post("/api/v1/orgs/alpha/threads/THR-010/pin",
                               json={"pinned": pinned}, headers=auth_headers)
        actual = {"http": {"status": response.status_code, "body": response.json()},
                  **_state_frame(daemon_state, org_state)}
        _compare(actual, expected, _receipt_root(tmp_path), f"P1.1-{pinned}", response)


@pytest.mark.parametrize("pinned", [True, False], ids=["pin", "unpin"])
def test_pin_complete_failure_recovery_frame(pinned, pin_venue, daemon_state, org_state,
                                            auth_headers, monkeypatch, tmp_path) -> None:
    with pin_venue(not pinned) as (client, before):
        failure_expected = {"http": {"status": 500}, **deepcopy(before)}
        recovery_expected = {"http": {"status": 200, "body": {"thread_id": "THR-010", "pinned": pinned}},
                             **_expected_transition(before, pinned)}

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("audit insertion failed")

        with monkeypatch.context() as fault:
            fault.setattr(org_state.db, "insert_audit_log_uncommitted", fail_audit)
            response = client.post("/api/v1/orgs/alpha/threads/THR-010/pin",
                                   json={"pinned": pinned}, headers=auth_headers)
        failure_actual = {"http": {"status": response.status_code}, **_state_frame(daemon_state, org_state)}
        # Error text is retained diagnostically; accepted P2 owns status + complete durable frame.
        _compare(failure_actual, failure_expected, _receipt_root(tmp_path), f"P2.1-failure-{pinned}", response)
        recovered = client.post("/api/v1/orgs/alpha/threads/THR-010/pin",
                                json={"pinned": pinned}, headers=auth_headers)
        recovery_actual = {"http": {"status": recovered.status_code, "body": recovered.json()},
                           **_state_frame(daemon_state, org_state)}
        _compare(recovery_actual, recovery_expected, _receipt_root(tmp_path), f"P2.1-recovery-{pinned}", recovered)


def test_pin_old_owner_patch_and_shared_lock(pin_venue, daemon_state, org_state,
                                            auth_headers, monkeypatch, caplog, tmp_path) -> None:
    with pin_venue(False) as (client, before):
        original = facade.Database.set_thread_pinned_uncommitted
        lock = org_state.db._lock
        calls = []

        def spy(self: facade.Database, thread_id: str, *, pinned: bool) -> None:
            calls.append((self, thread_id, pinned))
            return original(self, thread_id, pinned=pinned)

        ticks = itertools.count()

        class WholeTime:
            @staticmethod
            def monotonic() -> int:
                return next(ticks)

        with monkeypatch.context() as patches:
            patches.setattr(facade.Database, "set_thread_pinned_uncommitted", spy)
            patches.setattr(facade, "_time", WholeTime)
            patches.setattr(org_state.db, "_lock_warn_threshold_seconds", 0.5)
            caplog.set_level("WARNING", logger="happyranch.database.lock")
            expected = {"http": {"status": 200, "body": {"thread_id": "THR-010", "pinned": True}},
                        **_expected_transition(before, True)}
            response = client.post("/api/v1/orgs/alpha/threads/THR-010/pin",
                                   json={"pinned": True}, headers=auth_headers)
            actual = {"http": {"status": response.status_code, "body": response.json()},
                      **_state_frame(daemon_state, org_state)}
            _compare(actual, expected, _receipt_root(tmp_path), "P4.1", response)
        assert calls == [(org_state.db, "THR-010", True)]
        assert org_state.db._lock is lock
        expected_warnings = []
        for method, wait, hold in (
            ("get_thread", True, True), ("set_thread_pinned_with_audit", True, False),
            ("set_thread_pinned_uncommitted", True, True), ("insert_audit_log_uncommitted", True, True),
        ):
            if wait:
                expected_warnings.append(f"Database._lock wait 1.000s > threshold 0.500s for Database.{method} (lock convoy may stall other routes)")
            if hold:
                expected_warnings.append(f"Database._lock hold 1.000s > threshold 0.500s for Database.{method}")
        expected_warnings.append("Database._lock hold 9.000s > threshold 0.500s for Database.set_thread_pinned_with_audit")
        assert [r.getMessage() for r in caplog.records if r.name == "happyranch.database.lock"] == expected_warnings
        assert facade.Database.set_thread_pinned_uncommitted is original
        assert not org_state.db._conn.in_transaction


def _fresh_child(order: str, root: Path, data_root: Path) -> None:
    """Called only in a fresh foreground child, before this test module import."""
    from runtime.config import Settings
    from runtime.daemon import paths
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.infrastructure.db._shared import _synchronized
    from runtime.infrastructure.db.threads import ThreadsMixin
    from runtime.runtime import RuntimeDir
    methods = {name: str(inspect.signature(value)) for name, value in inspect.getmembers(facade.Database, callable) if not name.startswith("__")}
    assert hashlib.sha256(json.dumps(methods, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == METHOD_SIGNATURE_SHA256
    assert tuple(c.__module__ + "." + c.__qualname__ for c in facade.Database.__mro__) == MRO_MODULES
    assert facade._synchronized is _synchronized
    owners = [cls for cls in facade.Database.__mro__ if "set_thread_pinned_uncommitted" in vars(cls)]
    # Pristine pre-move GREEN and inherited post-move GREEN; never two owners.
    assert owners in ([facade.Database], [ThreadsMixin])
    selected = facade.Database.set_thread_pinned_uncommitted
    assert selected is vars(owners[0])["set_thread_pinned_uncommitted"]
    for encoded, expected in zip(PRISTINE_PICKLES, (facade.Database, selected, ThreadRecord), strict=True):
        assert pickle.loads(base64.b64decode(encoded)) is expected
    paths.ensure_daemon_home()
    paths.ensure_token()
    runtime = RuntimeDir.init(data_root / "runtime")
    org_root = runtime.orgs_dir / "alpha"
    (org_root / "org").mkdir(parents=True)
    (org_root / "org/teams.yaml").write_text("teams: {}\n")
    state = DaemonState.from_runtime(runtime, Settings())
    org = state.orgs["alpha"]
    try:
        with pytest.MonkeyPatch.context() as patch:
            with _venue(state, org, create_app(state), {}, patch, False) as (client, before):
                expected = {"http": {"status": 200, "body": {"thread_id": "THR-010", "pinned": True}}, **_expected_transition(before, True)}
                response = client.post("/api/v1/orgs/alpha/threads/THR-010/pin", json={"pinned": True},
                                       headers={"Authorization": f"Bearer {paths.read_token()}"})
                actual = {"http": {"status": response.status_code, "body": response.json()}, **_state_frame(state, org)}
                _compare(actual, expected, _receipt_root(data_root), "P4.2-" + order, response)
        for name, module in sys.modules.items():
            if name == "runtime" or name.startswith("runtime."):
                assert Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
    finally:
        for org in state.orgs.values():
            org.close()
        for store in (state.metrics_store, state.direct_connect_authority_store):
            if store is not None:
                store.close()


@pytest.mark.parametrize("order", ["facade-first", "mixin-first"])
def test_pin_fresh_import_order(order: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    program = """
import importlib, pathlib, sys
root=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(root))
order=sys.argv[2]
first='runtime.infrastructure.database' if order=='facade-first' else 'runtime.infrastructure.db.threads'
second='runtime.infrastructure.db.threads' if order=='facade-first' else 'runtime.infrastructure.database'
importlib.import_module(first); importlib.import_module(second)
from tests.daemon.test_thread_pin_decomposition import _fresh_child
_fresh_child(order,root,pathlib.Path(sys.argv[3]))
"""
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "LC_ALL": "C.UTF-8",
           "HAPPYRANCH_DAEMON_HOME": str(tmp_path / "daemon-home")}
    if "S6_PIN_RECEIPTS" in os.environ:
        env["S6_PIN_RECEIPTS"] = os.environ["S6_PIN_RECEIPTS"]
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(root), order, str(tmp_path)],
                          cwd=tmp_path, env=env, capture_output=True, timeout=55)
    (tmp_path / "child.stdout").write_bytes(proc.stdout)
    (tmp_path / "child.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, proc.stderr.decode()
