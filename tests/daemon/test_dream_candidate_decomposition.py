"""Accepted TASK-9959 revision2 D1-D4 forward-only shipping supplements.

D1 owns complete committed HTTP/file aftermath and omitted-slug preservation;
D2 owns real downstream clock/SQLite failures, including KB-before-DB residue;
D3 owns inherited old patches, selected held-phase visibility and whole-clock
warnings. Existing dream/Database/callback keepers retain their distinct gaps.
D4 owns both fresh import orders plus the complete facade/export/owner frame.
The four answers and selected-only controls are in the accepted case record.
No production seam. D4.2 uses the existing full app/served collector separately.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

# Reuse the unchanged SELECT-only, unfiltered collectors, not keeper assertions.
from tests.daemon.test_kb_view_decomposition import durable, kb_files, queues, sql_state

OLD = "2026-01-02T00:00:00+00:00"
NOW = "2026-10-07T00:01:02+00:00"
KB_NOW = "2026-10-07T00:03:04Z"
URL = "/api/v1/orgs/alpha/dreams/candidates/1/"
# TASK9987 / test_d4_complete_relocation_contract (both actual import orders):
# 1. Complete unfiltered facade members/MRO/exports and real shipping frames stay
#    pinned; exactly two already-approved memory APIs extend the baseline.
# 2. Old pin RED is ONLY shape_sha256 (dream-old-pin-red.* / dream-old-pin/);
#    corrected pin GREEN: dream-owner-green.* / dream-new-pin/ in output/TASK-9987.
# 3. D1-D3 do not own fresh import orders or the complete facade frame; retained
#    KB/archive import owners protect their own shipping consumers independently.
# 4. No production seam or filter. Whole587-member frame9d2ba533; diagnostic-only
#    removal of the two approved APIs restores whole585-member7c76756d EXACTLY.
_PRISTINE_SHAPE_SHA256 = "11bb9c1300af436c17bb7c92d1607149074bed81c3f7aaf2af8b38c6580dcd31"
# Six named wait/hold records and twelve clock calls observed on pristine cb7f2272.
_PRISTINE_WARNING_FRAME = {'warnings': [{'args': [2.0, 1.0, 'Database', 'list_dream_kb_candidates'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock wait 2.000s > threshold 1.000s for '
                          'Database.list_dream_kb_candidates (lock convoy may stall other routes)',
               'template': 'Database._lock wait %.3fs > threshold %.3fs for %s.%s (lock convoy may '
                           'stall other routes)'},
              {'args': [2.0, 1.0, 'Database', 'list_dream_kb_candidates'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock hold 2.000s > threshold 1.000s for '
                          'Database.list_dream_kb_candidates',
               'template': 'Database._lock hold %.3fs > threshold %.3fs for %s.%s'},
              {'args': [2.0, 1.0, 'Database', 'update_dream_kb_candidate'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock wait 2.000s > threshold 1.000s for '
                          'Database.update_dream_kb_candidate (lock convoy may stall other routes)',
               'template': 'Database._lock wait %.3fs > threshold %.3fs for %s.%s (lock convoy may '
                           'stall other routes)'},
              {'args': [2.0, 1.0, 'Database', 'update_dream_kb_candidate'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock hold 2.000s > threshold 1.000s for '
                          'Database.update_dream_kb_candidate',
               'template': 'Database._lock hold %.3fs > threshold %.3fs for %s.%s'},
              {'args': [2.0, 1.0, 'Database', 'list_dream_kb_candidates'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock wait 2.000s > threshold 1.000s for '
                          'Database.list_dream_kb_candidates (lock convoy may stall other routes)',
               'template': 'Database._lock wait %.3fs > threshold %.3fs for %s.%s (lock convoy may '
                           'stall other routes)'},
              {'args': [2.0, 1.0, 'Database', 'list_dream_kb_candidates'],
               'exception': None,
               'level': 'WARNING',
               'logger': 'happyranch.database.lock',
               'message': 'Database._lock hold 2.000s > threshold 1.000s for '
                          'Database.list_dream_kb_candidates',
               'template': 'Database._lock hold %.3fs > threshold %.3fs for %s.%s'}],
 'whole_clock_calls': [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]}

SENTINEL = (
    "---\nslug: amber-sentinel\ntitle: Amber xylophone\ntype: reference\n"
    "topic: sentinel\n---\n\nUnrelated exact bytes.\n"
).encode()
WRITTEN = (
    "---\nslug: candidate-one\ntitle: Candidate One\ntype: precedent\n"
    "topic: workflow\nauthored_by: dev_agent\nauthored_at: '2026-10-07T00:03:04Z'\n"
    "updated_by: dev_agent\nupdated_at: '2026-10-07T00:03:04Z'\n"
    "source_task: DREAM-001\n---\n\nCandidate body.\n"
).encode()
INDEX = (
    "# Knowledge Base Index\n\n## sentinel\n\n- `amber-sentinel` — Amber xylophone\n"
    "\n## workflow\n\n- `candidate-one` — Candidate One\n"
).encode()


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class _Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.rows: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rows.append({
            "logger": record.name, "level": record.levelname,
            "template": record.msg, "args": list(record.args), "message": record.getMessage(),
            "exception": None if not record.exc_info else {
                "type": record.exc_info[0].__module__ + "." + record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            },
        })


def _seed(org) -> None:
    from runtime.models import DreamKbCandidate, DreamRecord, TaskRecord, TaskStatus

    instant = datetime.fromisoformat(OLD)
    org.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-01-02",
        scheduled_for=instant, window_end=instant,
    ))
    for slug, title, body in [
        ("candidate-one", "Candidate One", "Candidate body.\n"),
        ("unrelated-two", "Unrelated Two", "Keep this exact content.\n"),
    ]:
        org.db.insert_dream_kb_candidate(DreamKbCandidate(
            dream_id="DREAM-001", agent_name="dev_agent", slug=slug, title=title,
            topic="workflow", rationale="Observed.", body_markdown=body,
            created_at=instant, updated_at=instant,
        ))
    org.db.insert_task(TaskRecord(
        id="TASK-999001", assigned_agent="dev_agent", brief="Dormant dream sentinel",
        status=TaskStatus.COMPLETED, created_at=instant, updated_at=instant,
    ))
    org.db.insert_audit_log("TASK-999001", "dev_agent", "dream_owned_sentinel",
                            {"raw": "preserve exact audit", "session_id": None})
    for directory in (org.root / "kb", org.root / "workspaces/dev_agent", org.root / "dreams"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    for path, data in [
        (org.root / "kb/amber-sentinel.md", SENTINEL),
        (org.root / "workspaces/dev_agent/sentinel.txt", b"workspace sentinel\x00\xff"),
        (org.root / "dreams/DREAM-001-transcript.txt", b"exact dormant transcript\n"),
    ]:
        path.write_bytes(data)
        path.chmod(0o640)


def _state(state, org) -> dict:
    return {
        "same_connection": sql_state(org.db._conn), "durable": durable(org.db.path),
        "in_transaction": org.db._conn.in_transaction,
        "query_only": org.db._conn.execute("PRAGMA query_only").fetchone()[0],
        "queues": queues(state, org),
        "files": {name: kb_files(org.root / name) for name in ("kb", "workspaces", "dreams")},
        "async_locks": {name: getattr(org, name).locked() for name in ("db_lock", "kb_lock")},
        "metrics": {"loops": deepcopy(state.metrics_registry._loops), "http": {
            name: {"buffer": list(hist._buf), "head": hist._head, "count": hist._count}
            for name, hist in state.metrics_registry._http.items()
        }},
    }


def _expect(before: dict, case: str) -> dict:
    expected = deepcopy(before)
    accept = case.startswith("accept")
    failure = case.endswith(("clock", "sqlite"))
    if not failure:
        for key in ("same_connection", "durable"):
            table = expected[key]["tables"]["dream_kb_candidates"]
            columns = table["selected_columns"]
            row = table["rows"][0]
            row[columns.index("status")] = "promoted" if accept else "rejected"
            row[columns.index("updated_at")] = NOW
            if accept:
                row[columns.index("promoted_kb_slug")] = "candidate-one"
    if case.endswith("sqlite"):
        # The real failed UPDATE opens a transaction; preserve this adverse state.
        expected["in_transaction"] = True
    if accept:
        expected["files"]["kb"].update({
            "candidate-one.md": {"kind": "file", "mode": 0o600, "bytes_hex": WRITTEN.hex()},
            "_index.md": {"kind": "file", "mode": 0o600, "bytes_hex": INDEX.hex()},
        })
    if failure:
        content = b"Internal Server Error"
        http = {"status": 500, "raw_hex": content.hex(),
                "headers": [["content-length", "21"], ["content-type", "text/plain; charset=utf-8"]]}
        errors = [{"type": "builtins.ValueError" if case.endswith("clock") else "sqlite3.OperationalError",
                   "message": "dream-clock-unavailable" if case.endswith("clock") else "attempt to write a readonly database"}]
        label = "POST __error__"
    else:
        body = {
            "id": 1, "dream_id": "DREAM-001", "agent_name": "dev_agent", "slug": "candidate-one",
            "title": "Candidate One", "topic": "workflow", "rationale": "Observed.",
            "body_markdown": "Candidate body.\n", "status": "promoted" if accept else "rejected",
            "promoted_kb_slug": "candidate-one" if accept else (
                "prior-exact-slug" if case == "dismiss-preserve-slug" else None),
            "created_at": OLD, "updated_at": NOW,
        }
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        http = {"status": 200, "raw_hex": content.hex(),
                "headers": [["content-length", str(len(content))], ["content-type", "application/json"]]}
        errors = []
        label = "POST /api/v1/orgs/{slug}/dreams/candidates/{candidate_id}/" + ("accept" if accept else "dismiss")
    # Literal middleware clock delta 0.25; the complete histogram ring is retained.
    for name in (label, "__all__"):
        expected["metrics"]["http"][name] = {"buffer": [0.25] + [0.0] * 1023, "head": 1, "count": 1}
    return {"before": before, "after": expected, "http": http, "errors": errors,
            "warnings": [], "whole_clock_calls": [], "patch_calls": [
                {"self": True, "connection": True, "lock": True, "id": 1,
                 "kwargs": {"status": "promoted", "promoted_kb_slug": "candidate-one"} if accept else {"status": "rejected"}},
            ]}


def _collect(case: str, state, org, app, headers, monkeypatch,
             *, held: bool = False, whole_clock: bool = False, class_patch: bool = False) -> tuple[dict, dict]:
    """Collect complete B/F/E/cleanup with real requests; no contract assertion."""
    from fastapi.testclient import TestClient
    from runtime.infrastructure import database as facade
    from runtime.infrastructure import kb_store
    from runtime.daemon import app as app_module

    with monkeypatch.context() as seed_patch:
        seed_patch.setattr(facade, "_now", lambda: datetime.fromisoformat(OLD))
        _seed(org)
    connection, lock = org.db._conn, org.db._lock
    if case == "dismiss-preserve-slug":
        connection.execute("UPDATE dream_kb_candidates SET promoted_kb_slug=? WHERE id=1", ("prior-exact-slug",))
        connection.commit()
    if case.endswith("sqlite"):
        connection.execute("PRAGMA query_only=ON")
    before = _state(state, org)
    expected = _expect(before, case)
    observed: dict = {}
    calls, errors, clock_calls = [], [], []
    lookup, proceed, entered, returned = (threading.Event() for _ in range(4))
    original = facade.Database.update_dream_kb_candidate
    original_list = org.db.list_dream_kb_candidates
    logger = logging.getLogger("happyranch.database.lock")
    warnings = _Warnings()
    client = TestClient(app, raise_server_exceptions=False)
    thread = None
    acquired = False
    responses = []
    worker_errors = []

    class FixedKBClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 7, 0, 3, 4, tzinfo=timezone.utc)

    def clock():
        if case.endswith("clock"):
            raise ValueError("dream-clock-unavailable")
        return datetime.fromisoformat(NOW)

    def patched(self, candidate_id, **kwargs):
        calls.append({"self": self is org.db, "connection": self._conn is connection,
                      "lock": self._lock is lock, "id": candidate_id, "kwargs": kwargs})
        entered.set()
        try:
            return original(self, candidate_id, **kwargs)
        except Exception as error:
            errors.append({"type": type(error).__module__ + "." + type(error).__name__, "message": str(error)})
            raise
        finally:
            returned.set()

    def listed(*args, **kwargs):
        result = original_list(*args, **kwargs)
        if not lookup.is_set():
            lookup.set()
            if not proceed.wait(2):
                raise TimeoutError("D3.2 lookup/entry barrier diagnostic")
        return result

    def whole_monotonic():
        # A replacement object, not mutation of the global time module.
        value = float(len(clock_calls) * 2)
        clock_calls.append(value)
        return value

    def request():
        try:
            responses.append(client.post(URL + ("accept" if case.startswith("accept") else "dismiss"),
                                         json={}, headers=headers))
        except BaseException as error:
            worker_errors.append(error)

    patch = monkeypatch.context()
    local = patch.__enter__()
    try:
        local.setattr(facade, "_now", clock)
        local.setattr(kb_store, "datetime", FixedKBClock)
        metric_clock = iter([10.0, 10.25])
        local.setattr(app_module, "_time", SimpleNamespace(monotonic=lambda: next(metric_clock)))
        local.setattr(facade, "_time", SimpleNamespace(monotonic=whole_monotonic if whole_clock else lambda: 0.0))
        if class_patch:
            local.setattr(facade.Database, "update_dream_kb_candidate", patched)
        else:
            local.setattr(org.db, "update_dream_kb_candidate", lambda candidate_id, **kwargs: patched(org.db, candidate_id, **kwargs))
        logger.addHandler(warnings)
        if held:
            local.setattr(org.db, "list_dream_kb_candidates", listed)
            thread = threading.Thread(target=request, name="dreamupdate-owned-http")
            thread.start()
            if not lookup.wait(2):
                raise TimeoutError("D3.2 lookup diagnostic")
            lock.acquire()
            acquired = True
            proceed.set()
            if not entered.wait(2):
                raise TimeoutError("D3.2 writer entry diagnostic")
            # False is the pristine observation; no early field/lock assertion.
            observed["held_phase"] = {"returned": returned.wait(0.2), "durable": durable(org.db.path)}
            expected["held_phase"] = {"returned": False, "durable": before["durable"]}
            lock.release()
            acquired = False
            thread.join(2)
            if thread.is_alive():
                raise TimeoutError("D3.2 HTTP join diagnostic")
        else:
            request()
        if worker_errors:
            raise worker_errors[0]
        if len(responses) != 1:
            raise RuntimeError("HTTP transport diagnostic: missing response")
        response = responses[0]
        observed.update(before=before, after=_state(state, org),
                        http={"status": response.status_code, "raw_hex": response.content.hex(),
                              "headers": [list(item) for item in response.headers.multi_items()]},
                        errors=errors, patch_calls=calls, warnings=list(warnings.rows),
                        whole_clock_calls=list(clock_calls))
        if whole_clock and _PRISTINE_WARNING_FRAME is not None:
            expected.update(_PRISTINE_WARNING_FRAME)
    finally:
        proceed.set()
        if acquired:
            lock.release()
        if thread is not None:
            thread.join(2)
            if thread.is_alive():
                raise TimeoutError("D3.2 cleanup join diagnostic")
        logger.removeHandler(warnings)
        patch.__exit__(None, None, None)
        released = []

        def probe():
            success = lock.acquire(timeout=2)
            released.append(success)
            if success:
                lock.release()

        probe_thread = threading.Thread(target=probe, name="dreamupdate-owned-release-probe")
        probe_thread.start()
        probe_thread.join(3)
        if probe_thread.is_alive():
            raise TimeoutError("finally-release probe cleanup diagnostic")
        prior_close = durable(org.db.path)
        connection.execute("PRAGMA query_only=OFF")
        client.close()
        org.close()
        after_close = durable(org.db.path)
        reopened = facade.Database(org.db.path)
        try:
            reopened_frame = {"sql": sql_state(reopened._conn), "in_transaction": reopened._conn.in_transaction}
        finally:
            reopened.close()
        asyncio.run(state.close_all())
        cleanup = {
            "lock_released": released, "http_thread_alive": bool(thread and thread.is_alive()),
            "prior_close": prior_close, "after_close": after_close, "reopened": reopened_frame,
            "state_orgs": list(state.orgs),
        }
        observed["cleanup"] = cleanup
        expected["cleanup"] = {
            "lock_released": [True], "http_thread_alive": False,
            "prior_close": expected["after"]["durable"], "after_close": expected["after"]["durable"],
            "reopened": {"sql": expected["after"]["same_connection"], "in_transaction": False},
            "state_orgs": [],
        }
    return observed, expected


def _compare(observed: dict, expected: dict, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    loaded = {}
    for name, module in list(sys.modules.items()):
        if (name == "runtime" or name.startswith("runtime.")) and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()
            # Wrong source is a provenance diagnostic, never business mutation RED.
            relative = str(path.relative_to(root))
            loaded[name] = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (destination / "provenance.json").write_bytes(_json({
        "root": str(root), "cwd": str(Path.cwd().resolve()), "interpreter": sys.executable,
        "interpreter_real": str(Path(sys.executable).resolve()), "version": sys.version,
        "interpreter_sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
        "loaded": loaded,
    }))
    for label, frame in (("observed", observed), ("expected", expected)):
        (destination / (label + ".json")).write_bytes(_json(frame))
    # FIRST assertion after the shipping request is its COMPLETE frame.
    if observed != expected:
        print("DREAMUPDATE COMPLETE SHIPPING FRAME EXPECTED=" + _json(expected).decode())
        print("DREAMUPDATE COMPLETE SHIPPING FRAME OBSERVED=" + _json(observed).decode())
    assert observed == expected, "DREAMUPDATE complete shipping consumer frame"


def _exercise(case: str, tmp_path: Path, state, org, app, headers, monkeypatch, **options) -> None:
    observed, expected = _collect(case, state, org, app, headers, monkeypatch, **options)
    destination = Path(os.environ.get("DREAMUPDATE_RECEIPTS", str(tmp_path / "frames"))) / (case + str(options))
    _compare(observed, expected, destination)


@pytest.mark.parametrize("case", ["accept", "dismiss", "dismiss-preserve-slug"])
def test_d1_complete_shipping_frame(case, tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise(case, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch)


@pytest.mark.parametrize("case", ["accept-clock", "dismiss-clock", "accept-sqlite", "dismiss-sqlite"])
def test_d2_shipping_failure_frame(case, tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise(case, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch)


@pytest.mark.parametrize("case", ["accept", "dismiss"])
def test_d3_old_clock_and_patch_frame(case, tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise(case, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, class_patch=case == "accept")


def test_d3_shared_lock_frame(tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("dismiss", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, held=True)


def test_d3_wholeclock_warning_frame(tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("dismiss", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, whole_clock=True)


def _compatibility(order: str, owned: Path) -> tuple[dict, dict]:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    importlib.import_module("runtime.infrastructure.database" if order == "database-first" else "runtime.infrastructure.db.dreams")
    facade = importlib.import_module("runtime.infrastructure.database")
    dreams = importlib.import_module("runtime.infrastructure.db.dreams")
    shared = importlib.import_module("runtime.infrastructure.db._shared")
    models = importlib.import_module("runtime.models")
    members = {}
    for name in dir(facade.Database):
        value = getattr(facade.Database, name)
        if callable(value):
            try:
                members[name] = ["method", str(inspect.signature(value))]
            except (TypeError, ValueError):
                members[name] = ["method", "builtin-no-signature"]
        elif isinstance(value, property):
            members[name] = ["property", str(inspect.signature(value.fget))]
    shape = {"members": members,
             "mro": [[value.__module__, value.__qualname__] for value in facade.Database.__mro__],
             "types": {name: [value.__module__, value.__qualname__] for name, value in vars(facade).items() if isinstance(value, type)}}
    identities = {
        "Database": facade.Database is importlib.import_module("runtime.infrastructure.database").Database,
        "DreamsMixin": facade.DreamsMixin is dreams.DreamsMixin,
        "_synchronized": facade._synchronized is dreams._synchronized is shared._synchronized,
        "models": {name: value is getattr(models, value.__name__) for name, value in vars(facade).items()
                   if isinstance(value, type) and value.__module__ == "runtime.models"},
    }
    owners = [owner.__name__ for owner in facade.Database.__mro__ if "update_dream_kb_candidate" in vars(owner)]
    # Both orders execute a real request before ANY ownership/shape assertion.
    from tests.daemon import conftest as fixtures

    patch = pytest.MonkeyPatch()
    try:
        home = fixtures.tmp_home.__wrapped__(owned, patch)
        runtime = fixtures.runtime.__wrapped__(owned)
        state = fixtures.daemon_state.__wrapped__(runtime)
        org = fixtures.org_state.__wrapped__(state)
        app = fixtures.app.__wrapped__(home, state)
        observed, expected = _collect("dismiss", state, org, app, fixtures.auth_headers.__wrapped__(), patch)
    finally:
        patch.undo()
    loaded = {name: str(Path(module.__file__).resolve().relative_to(root))
              for name, module in list(sys.modules.items())
              if (name == "runtime" or name.startswith("runtime.")) and getattr(module, "__file__", None)}
    observed["compatibility"] = {
        "shape_sha256": hashlib.sha256(_json(shape)).hexdigest(), "owners": owners, "identities": identities,
        "loaded_from_root": all((root / path).is_file() for path in loaded.values()),
    }
    expected_owner = "DreamsMixin" if "update_dream_kb_candidate" in vars(dreams.DreamsMixin) else "Database"
    expected["compatibility"] = {
        "shape_sha256": _PRISTINE_SHAPE_SHA256, "owners": [expected_owner],
        "identities": {"Database": True, "DreamsMixin": True, "_synchronized": True,
                       "models": {name: True for name in identities["models"]}}, "loaded_from_root": True,
    }
    return observed, expected


@pytest.mark.parametrize("order", ["database-first", "dreams-first"])
def test_d4_complete_relocation_contract(order: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    destination = Path(os.environ.get("DREAMUPDATE_RECEIPTS", str(tmp_path / "frames"))) / order
    program = (
        "import pathlib,sys;sys.path.insert(0,sys.argv[1]);"
        "from tests.daemon.test_dream_candidate_decomposition import _compatibility,_compare;"
        "observed,expected=_compatibility(sys.argv[2],pathlib.Path(sys.argv[3]));"
        "_compare(observed,expected,pathlib.Path(sys.argv[4]))"
    )
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    # subprocess.run timeout kills and reaps this owned child; no borrowed port.
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(root), order,
                           str(tmp_path / "fresh-owned"), str(destination)],
                          cwd=root, env=env, capture_output=True, timeout=55)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "child.stdout").write_bytes(proc.stdout)
    (destination / "child.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, proc.stdout.decode() + proc.stderr.decode()
