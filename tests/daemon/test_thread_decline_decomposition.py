"""TASK-9977 revision2 C1-C4: selected legacy/special decline shipping frames.

The accepted four answers, keeper gaps and selected-only controls live in the
existing task case record. No production seam. All expectations below are
authored from original e2780012 source, before relocation or controls. Complete
B/E are written before the POST; F is collected independently, including cleanup.
The FIRST semantic assertion is complete F == E. Setup/profile/join failures are
diagnostics, never mutation RED. Existing keepers are unchanged.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

# Reuse unfiltered SELECT-only collectors, never their keeper assertions.
from tests.daemon.test_kb_view_decomposition import queues, sql_state

SEED = "2026-10-06T00:00:00+00:00"
ACTION = "2026-10-07T00:00:00+00:00"
COMPETING = "2026-10-06T23:59:59+00:00"
FAILED_REASON = "C1.3 competing failure"
THREAD = "THR-998001"
URL = "/api/v1/orgs/alpha/threads/" + THREAD
_MISSING = object()


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(_json(value))


def _sql(connection: sqlite3.Connection) -> dict:
    value = sql_state(connection)
    value["table_info"] = {
        name: [list(row) for row in connection.execute(
            'PRAGMA table_info("' + name.replace('"', '""') + '")')]
        for name in value["tables"]
    }
    value["index_info"] = {
        name: [list(row) for row in connection.execute(
            'PRAGMA index_info("' + name.replace('"', '""') + '")')]
        for name in value["indexes"]
    }
    return value


def _durable(path: Path) -> dict:
    """Independent query_only reader, one snapshot, all cursors/transaction closed."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    result = None
    try:
        connection.execute("PRAGMA query_only=ON").close()
        connection.execute("BEGIN").close()
        result = {"sql": _sql(connection), "query_only": 1, "in_transaction": connection.in_transaction}
        connection.rollback()
        result["transaction_closed"] = not connection.in_transaction
        return result
    finally:
        connection.close()
        if result is not None:
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                result["connection_closed"] = True
            else:
                result["connection_closed"] = False


def _files(root: Path, database: Path) -> tuple[dict, dict]:
    """Business bytes/modes/links intact; SQLite physical bytes are provenance."""
    business, physical = {}, {}
    paths = [root, *sorted(root.rglob("*"))]
    sqlite_paths = {database}
    for path in paths:
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    sqlite_paths.add(path)
    physical_paths = {Path(str(path) + suffix) for path in sqlite_paths
                      for suffix in ("", "-wal", "-shm", "-journal")}
    for path in paths:
        name = str(path.relative_to(root))
        info = path.lstat()
        row = {"mode": stat.S_IMODE(info.st_mode), "kind": "dir" if path.is_dir() else "file"}
        if path.is_symlink():
            row.update(kind="symlink", link=os.readlink(path))
        elif path.is_file():
            row["bytes_hex"] = path.read_bytes().hex()
        if path in physical_paths:
            physical[name] = row
        else:
            business[name] = row
    return business, physical


def _state(state, org) -> dict:
    queue_frame = queues(state, org)
    for name in ("thread_queue", "dream_queue", "wake_queue", "schedule_queue"):
        queue = getattr(org, name)._q
        queue_frame["org"][name].update(size=queue.qsize(), unfinished_tasks=queue._unfinished_tasks)
    files, _physical = _files(state.runtime.root, org.db.path)
    other_databases = {}
    for path in sorted(state.runtime.root.rglob("*")):
        if path.is_file() and not path.is_symlink() and path != org.db.path:
            with path.open("rb") as stream:
                is_sqlite = stream.read(16) == b"SQLite format 3\x00"
            if is_sqlite:
                other_databases[str(path.relative_to(state.runtime.root))] = _durable(path)
    return {
        "writer": _sql(org.db._conn), "durable": _durable(org.db.path),
        "in_transaction": org.db._conn.in_transaction,
        "query_only": org.db._conn.execute("PRAGMA query_only").fetchone()[0],
        "other_databases": other_databases,
        "queues": queue_frame, "files": {"runtime": files, "daemon_home": _files(
            Path(os.environ["HAPPYRANCH_DAEMON_HOME"]), org.db.path)[0]},
        "org_db_lock": org.db_lock.locked(), "bus_locked": org.event_bus._lock.locked(),
    }


class _Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.rows: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rows.append({
            "logger": record.name, "level": record.levelname, "template": record.msg,
            "args": list(record.args), "message": record.getMessage(),
            "exception": None if not record.exc_info else {
                "type": record.exc_info[0].__module__ + "." + record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            },
        })


def _seed(org, purpose: str, monkeypatch) -> str:
    from runtime.infrastructure import database as facade
    from runtime.infrastructure.db import audit as audit_module
    from runtime.models import ThreadInvocationPurpose, ThreadMessageKind, ThreadRecord

    # Only fixture creation is deterministic; real public minting still owns rows.
    values = iter([uuid.UUID(int=998001), uuid.UUID(int=998002), uuid.UUID(int=998003)])
    class SeedAuditClock(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return datetime.fromisoformat(SEED)
    with monkeypatch.context() as patch:
        patch.setattr(facade, "_now", lambda: datetime.fromisoformat(SEED))
        patch.setattr(audit_module, "datetime", SeedAuditClock)
        patch.setattr(uuid, "uuid4", lambda: next(values))
        for thread in (THREAD, "THR-998002"):
            org.db.insert_thread(ThreadRecord(id=thread, subject=thread, started_at=datetime.fromisoformat(SEED)))
            org.db.add_thread_participant(thread, "dev_agent", added_by="@founder")
            org.db.add_thread_participant(thread, "qa_engineer", added_by="@founder")
            org.db.append_thread_message(
                thread_id=thread, speaker="@founder", kind=ThreadMessageKind.MESSAGE,
                body_markdown="Pristine triggering message.\n",
            )
        if purpose == "task_followup":
            invocation, _cap = org.db.mint_followup_invocation_with_cap_extend(
                THREAD, agent_name="dev_agent", triggering_seq=1,
            )
        else:
            invocation = org.db.mint_thread_invocation(
                thread_id=THREAD, agent_name="dev_agent", triggering_seq=1,
                purpose=ThreadInvocationPurpose(purpose),
            )
        for thread, agent in ((THREAD, "qa_engineer"), ("THR-998002", "dev_agent")):
            org.db.mint_thread_invocation(
                thread_id=thread, agent_name=agent, triggering_seq=1,
                purpose=ThreadInvocationPurpose.REPLY,
            )
        org.db.insert_audit_log(THREAD, "@founder", "decline_owned_sentinel", {"raw": "keep", "session_id": None})
    for relative, data in (
        ("threads/decline-sentinel.md", b"exact transcript sentinel\n"),
        ("workspaces/dev_agent/decline-sentinel.bin", b"owned\x00\xff"),
    ):
        path = org.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o640)
    return invocation.invocation_token


def _row(sql: dict, table: str, key: str, value: object) -> list:
    data = sql["tables"][table]
    index = data["selected_columns"].index(key)
    return next(row for row in data["rows"] if row[index] == value)


def _terminal(sql: dict, token: str, status: str, reason: str | None, when: str) -> None:
    row = _row(sql, "thread_invocations", "invocation_token", token)
    columns = sql["tables"]["thread_invocations"]["selected_columns"]
    for name, value in (("status", status), ("decline_reason", reason), ("consumed_at", when)):
        row[columns.index(name)] = value


def _audit(sql: dict, reason: str | None) -> None:
    # Exact original raw JSON bytes and allocator semantics, independently authored.
    sequence = _row(sql, "sqlite_sequence", "name", "audit_log")
    columns = sql["tables"]["sqlite_sequence"]["selected_columns"]
    allocation = sequence[columns.index("seq")] + 1
    sequence[columns.index("seq")] = allocation
    payload = '{"agent_name": "dev_agent", "reason": "nothing to add"}' if reason else '{"agent_name": "dev_agent"}'
    values = {"rowid": allocation, "id": allocation, "task_id": THREAD, "agent": "dev_agent",
              "action": "thread_decline_consumed", "payload": payload, "timestamp": ACTION}
    sql["tables"]["audit_log"]["rows"].append([
        values[name] for name in sql["tables"]["audit_log"]["selected_columns"]
    ])


def _http(status: int, body: object) -> dict:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if isinstance(body, dict) else body.encode()
    return {"status": status, "raw_hex": raw.hex(), "body": body,
            "headers": [["content-length", str(len(raw))],
                        ["content-type", "application/json" if isinstance(body, dict) else "text/plain; charset=utf-8"]]}


def _response(response) -> dict:
    return {"status": response.status_code, "raw_hex": response.content.hex(),
            "body": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
            "headers": [list(item) for item in response.headers.multi_items()]}


def _readers(before: dict, purpose: str, status: str, reason: str | None, when: str | None) -> dict:
    result = deepcopy(before)
    if purpose != "bootstrap":
        for response in result.values():
            for message in response["body"]["messages"]:
                for responder in message["responder_status"]:
                    if responder["agent_name"] == "dev_agent":
                        responder.update(status=status, responded_at=when, decline_reason=reason,
                                         category="infra_fail" if status == "failed" else "declined")
            response.update(_http(200, response["body"]))
    return result


def _expected(before: dict, reader_before: dict, token: str, purpose: str,
              reason: str | None, mode: str) -> dict:
    after = deepcopy(before)
    failure = mode in ("facade-clock", "sqlite-update-denied")
    cas = mode == "cas"
    when, status, terminal_reason = (COMPETING, "failed", FAILED_REASON) if cas else (ACTION, "declined", reason)
    if not failure:
        for sql in (after["writer"], after["durable"]["sql"]):
            _terminal(sql, token, status, terminal_reason, when)
            if not cas:
                _audit(sql, reason)
    # Pristine Python3.14 SQLite rejects this UPDATE during authorization before
    # implicit BEGIN: original observation is False, preserved without rollback.
    post = _http(500, "Internal Server Error") if failure else (
        _http(409, {"detail": {"code": "invocation_token_consumed"}}) if cas else
        _http(200, {"thread_id": THREAD, "status": "declined"})
    )
    readers = deepcopy(reader_before) if failure else _readers(reader_before, purpose, status, terminal_reason, when)
    events = [] if failure or cas else [
        ["thread:" + THREAD, {"thread_id": THREAD, "seq": None, "speaker": "dev_agent", "kind": "decline_status", "preview": ""}],
        ["thread_inbox:alpha", {"thread_id": THREAD, "event_kind": "decline_status", "status": "open"}],
    ]
    error = None
    if failure:
        error = {"type": "builtins.RuntimeError" if mode == "facade-clock" else "sqlite3.DatabaseError",
                 "message": "C2-clock" if mode == "facade-clock" else "not authorized",
                 "cause": None, "context": None}
    expected = {
        "before": before, "reader_before": reader_before, "post": post,
        "readers": readers, "after": after, "second": None, "after_second": after,
        "events": events, "subscriber_events": [row[1] for row in events],
        "queue_attempts": [], "errors": [] if error is None else [error], "warnings": [],
        "calls": [{"token": token, "reason": reason, "same_db": True,
                   "result": None if failure else not cas}],
        "settlement": [None] if purpose == "reply" else [],
        "clock_calls": [], "held": None, "competing": None,
    }
    if mode == "ordinary":
        expected["second"] = _http(409, {"detail": {"code": "invocation_token_consumed", "status": "declined"}})
    if cas:
        competing = deepcopy(before)
        for sql in (competing["writer"], competing["durable"]["sql"]):
            _terminal(sql, token, "failed", FAILED_REASON, COMPETING)
        expected["competing"] = {
            "before": deepcopy(before), "after": competing, "result": True,
            "readers": _readers(reader_before, purpose, "failed", FAILED_REASON, COMPETING),
            "clock_restored": True, "same_token": token,
        }
    if mode == "wholeclock":
        expected["clock_calls"] = [0.0, 0.0, 0.0, 2.0]
        expected["warnings"] = [{
            "logger": "happyranch.database.lock", "level": "WARNING",
            "template": "Database._lock hold %.3fs > threshold %.3fs for %s.%s",
            "args": [2.0, 1.0, "Database", "mark_invocation_declined"],
            "message": "Database._lock hold 2.000s > threshold 1.000s for Database.mark_invocation_declined",
            "exception": None,
        }]
    if mode == "held":
        expected["held"] = {
            "witness": "selected-acquire-attempt", "durable": before["durable"],
            "same_lock": True, "same_connection": True, "same_token": token,
            "order": ["holder-acquired", "selected-acquire-attempt", "held-reader-closed",
                      "holder-release", "selected-commit", "selected-return"],
        }
    expected["cleanup"] = {
        "class_restored": True, "clock_restored": True, "wholeclock_restored": True,
        "profile_restored": True, "authorizer_removed": True, "native_lock_released": True,
        "holder_joined": True, "request_joined": True, "client_closed": True,
        "subscriptions": {}, "async_pending": 0, "orgs": [],
        "before_close": after["durable"], "after_close": after["durable"],
        "reopened": {"sql": after["writer"], "in_transaction": False},
        "files": after["files"],
        "other_databases": after["other_databases"],
    }
    return expected


def _collect(case: str, state, org, app, headers: dict, monkeypatch, destination: Path,
             *, mode: str = "ordinary") -> tuple[dict, dict]:
    from httpx import ASGITransport, AsyncClient
    from runtime.infrastructure import database as facade
    from runtime.infrastructure.db import audit as audit_module
    from runtime.models import ThreadInvocationStatus

    purpose = "reply" if case.startswith("legacy-reply") else "bootstrap" if case.startswith("bootstrap") else "task_followup"
    reason_input = None if case.endswith("null") else "   " if case.endswith("whitespace") else " nothing to add "
    reason = None if reason_input is None else "" if case.endswith("whitespace") else "nothing to add"
    selected = facade.Database.mark_invocation_declined
    body = inspect.unwrap(selected)
    prior_class = vars(facade.Database).get("mark_invocation_declined", _MISSING)
    prior_clock, prior_wholeclock = facade._now, facade._time
    prior_threshold = org.db._lock_warn_threshold_seconds
    token = _seed(org, purpose, monkeypatch)
    request = {"thread_id": THREAD, "invocation_token": token, "speaker": "dev_agent",
               "reason": reason_input, "in_response_to_seq": 1}
    observed: dict = {}
    events, queue_attempts, errors, calls, settlement, clock_calls = [], [], [], [], [], []
    profile_events, raw_profile = [], []
    acquired, release, witness = threading.Event(), threading.Event(), threading.Event()
    deadline = time.monotonic() + 30
    holder = None
    request_thread = None
    worker_errors = []
    clients = []
    first_witness: list[str] = []
    profile_restored: list[bool] = []
    selected_commit: list[object] = []
    selected_executed: list[object] = []
    owned_actors: list[threading.Thread] = []
    lock, connection = org.db._lock, org.db._conn
    warnings = _Warnings()
    logger = logging.getLogger("happyranch.database.lock")
    physical: dict = {}

    def bounded_wait(gate: threading.Event, label: str) -> None:
        if not gate.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError(label + " diagnostic; no contract verdict")

    def holder_run() -> None:
        try:
            with lock:
                profile_events.append("holder-acquired")
                raw_profile.append({"phase": "holder-acquired", "thread": threading.get_native_id(),
                                    "lock": id(lock), "connection": id(connection), "owned": lock._is_owned()})
                acquired.set()
                bounded_wait(release, "holder release")
                profile_events.append("holder-release")
        except BaseException as error:
            worker_errors.append(error)
            acquired.set()
            witness.set()

    def delegate(self, invoked_token: str, **kwargs) -> bool:
        nonlocal holder
        frame = sys._getframe()
        saved_profile = sys.getprofile()
        call = {"token": invoked_token, "reason": kwargs.get("decline_reason"), "same_db": self is org.db, "result": None}
        calls.append(call)

        def profile(current, event: str, arg) -> None:
            # Pure observation. Exact wrapper/free method/self/token/parent;
            # prechecks, observer entry and later AuditMixin commits cannot match.
            selected_body = current.f_code is body.__code__ and current.f_locals.get("self") is org.db and current.f_locals.get("token") == token
            wrapper = (current.f_code is facade.Database.fail_invocation.__code__ and
                       current.f_locals.get("method") is body and current.f_locals.get("self") is org.db and
                       current.f_locals.get("args") == (token,) and current.f_locals.get("kwargs") == kwargs and
                       current.f_back is frame)
            parent = current.f_back
            body_owned = selected_body and (parent is frame or (parent is not None and
                parent.f_code is facade.Database.fail_invocation.__code__ and parent.f_locals.get("method") is body and parent.f_back is frame))
            label = None
            if event == "c_call" and wrapper and getattr(arg, "__self__", None) is lock and getattr(arg, "__name__", None) == "acquire":
                label = "selected-acquire-attempt"
            elif event == "c_return" and body_owned and getattr(arg, "__self__", None) is connection and getattr(arg, "__name__", None) == "execute":
                selected_executed.append(current)
            elif event == "c_return" and body_owned and current in selected_executed and getattr(arg, "__self__", None) is connection and getattr(arg, "__name__", None) == "commit":
                selected_commit.append(current)
                label = "selected-commit"
            elif event == "return" and body_owned and current in selected_commit and isinstance(arg, bool):
                label = "selected-return"
            if label:
                profile_events.append(label)
                raw_profile.append({"phase": label, "event": event, "frame": id(current), "parent": id(current.f_back),
                                    "code": id(current.f_code), "source": current.f_code.co_filename,
                                    "self": id(org.db), "token": token, "lock": id(lock), "connection": id(connection),
                                    "thread": threading.get_native_id(), "result": arg if event == "return" else None})
                if not first_witness and label in ("selected-acquire-attempt", "selected-return"):
                    first_witness.append("selected-acquire-attempt" if label == "selected-acquire-attempt" else "selected-return-commit")
                    witness.set()

        if mode == "held":
            holder = threading.Thread(target=holder_run, name="decline-owned-native-holder")
            holder.start()
            bounded_wait(acquired, "holder acquisition")
            sys.setprofile(profile)
        try:
            if mode == "cas":
                paused_clock = facade._now
                # The old-class delegate pauses BEFORE the selected descriptor.
                # A distinct owned competitor completes retained public failure,
                # commit, clock restoration and both readers before this resumes.
                actor_frames = []
                def actor_run() -> None:
                    try:
                        actor_before = _state(state, org)
                        try:
                            facade._now = lambda: datetime.fromisoformat(COMPETING)
                            result = org.db.fail_invocation(token, status=ThreadInvocationStatus.FAILED, decline_reason=FAILED_REASON)
                        finally:
                            facade._now = paused_clock
                        actor_frames.append({"before": actor_before, "after": _state(state, org), "result": result,
                            "readers": asyncio.run(readers()), "clock_restored": facade._now is paused_clock, "same_token": token})
                    except BaseException as error:
                        worker_errors.append(error)
                actor = threading.Thread(target=actor_run, name="decline-owned-competing-failure")
                owned_actors.append(actor)
                actor.start()
                actor.join(max(0, deadline - time.monotonic()))
                if actor.is_alive() or worker_errors or len(actor_frames) != 1:
                    raise RuntimeError("competing reader join diagnostic")
                observed["competing"] = actor_frames[0]
            if mode == "facade-clock":
                def broken_clock() -> datetime:
                    raise RuntimeError("C2-clock")
                facade._now = broken_clock
            if mode == "sqlite-update-denied":
                connection.set_authorizer(lambda operation, table, column, database, origin:
                    sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_UPDATE and table == "thread_invocations" else sqlite3.SQLITE_OK)
            if mode == "wholeclock":
                ticks = iter([0.0, 0.0, 0.0, 2.0])
                def tick() -> float:
                    value = next(ticks)
                    clock_calls.append(value)
                    return value
                facade._time = SimpleNamespace(monotonic=tick)
                org.db._lock_warn_threshold_seconds = 1.0
            call["result"] = selected(self, invoked_token, **kwargs)
            return call["result"]
        except Exception as error:
            errors.append({"type": type(error).__module__ + "." + type(error).__name__, "message": str(error),
                           "cause": None if error.__cause__ is None else str(error.__cause__),
                           "context": None if error.__context__ is None else str(error.__context__)})
            raise
        finally:
            facade._now = action_clock
            facade._time = prior_wholeclock
            org.db._lock_warn_threshold_seconds = prior_threshold
            connection.set_authorizer(None)
            if mode == "held":
                sys.setprofile(saved_profile)
                profile_restored.append(sys.getprofile() is saved_profile)

    async def readers() -> dict:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://decline-owned", headers=headers) as client:
            clients.append(client)
            return {"detail": _response(await client.get(URL)), "messages": _response(await client.get(URL + "/messages"))}

    original_publish, original_put, original_once = org.event_bus.publish, org.thread_queue.put, org.thread_queue.put_once
    async def publish(topic: str, event: dict) -> None:
        events.append([topic, deepcopy(event)])
        await original_publish(topic, event)
    async def put(job) -> None:
        queue_attempts.append({"method": "put", "org_slug": job.org_slug, "token": job.invocation_token})
        await original_put(job)
    async def put_once(job) -> None:
        queue_attempts.append({"method": "put_once", "org_slug": job.org_slug, "token": job.invocation_token})
        await original_once(job)
    original_settle = org.db.settle_conversational_reply_with_exchange
    def settle(**kwargs):
        result = original_settle(**kwargs)
        settlement.append(None if result[0] is None else "actual-modern-settlement")
        return result

    class AuditClock(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return datetime.fromisoformat(ACTION)

    action_clock = lambda: datetime.fromisoformat(ACTION)
    patch = monkeypatch.context()
    local = patch.__enter__()
    local.setattr(facade, "_now", action_clock)
    local.setattr(audit_module, "datetime", AuditClock)
    local.setattr(facade.Database, "mark_invocation_declined", delegate)
    local.setattr(org.event_bus, "publish", publish)
    local.setattr(org.thread_queue, "put", put)
    local.setattr(org.thread_queue, "put_once", put_once)
    local.setattr(org.db, "settle_conversational_reply_with_exchange", settle)
    logger.addHandler(warnings)
    loop_cleanup: dict = {}

    async def run_request() -> None:
        generators = [org.event_bus.subscribe("thread:" + THREAD), org.event_bus.subscribe("thread_inbox:alpha")]
        tasks = [asyncio.create_task(anext(generator)) for generator in generators]
        # Yield only to establish original subscriptions; no timing-based phase proof.
        await asyncio.sleep(0)
        try:
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://decline-owned", headers=headers) as client:
                clients.append(client)
                observed["post"] = _response(await client.post(URL + "/decline", json=request))
                observed["readers"] = await readers()
                observed["after"] = _state(state, org)
                observed["second"] = None
                if mode == "ordinary":
                    observed["second"] = _response(await client.post(URL + "/decline", json=request))
                observed["after_second"] = _state(state, org)
                await asyncio.sleep(0)
                observed["subscriber_events"] = [task.result() for task in tasks if task.done() and not task.cancelled()]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for generator in generators:
                await generator.aclose()
            loop_cleanup.update(client_closed=all(client.is_closed for client in clients), subscriptions={key: len(value) for key, value in org.event_bus._subscribers.items()},
                                async_pending=len([task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]))

    def request_run() -> None:
        try:
            asyncio.run(run_request())
        except BaseException as error:
            worker_errors.append(error)
            witness.set()

    try:
        reader_before = asyncio.run(readers())
        before = _state(state, org)
        # Snapshot is outside route's async db_lock; expected actor frame explicitly owns it.
        expected = _expected(before, reader_before, token, purpose, reason, mode)
        if mode == "cas":
            expected["competing"]["before"]["org_db_lock"] = True
            expected["competing"]["after"]["org_db_lock"] = True
        _write(destination / "before.json", before)
        _write(destination / "expected.json", expected)
        _write(destination / "input.json", request)
        physical["before"] = _files(state.runtime.root, org.db.path)[1]
        observed.update(before=before, reader_before=reader_before, held=None, competing=None)
        if mode == "held":
            request_thread = threading.Thread(target=request_run, name="decline-owned-http")
            request_thread.start()
            bounded_wait(witness, "actual selected native phase witness")
            if worker_errors or not first_witness:
                raise RuntimeError("missing native selected witness diagnostic")
            try:
                held_read = _durable(org.db.path)
                profile_events.append("held-reader-closed")
                observed["held"] = {"witness": first_witness[0], "durable": held_read,
                    "same_lock": lock is org.db._lock, "same_connection": connection is org.db._conn,
                    "same_token": token, "order": profile_events}
            finally:
                release.set()
            request_thread.join(max(0, deadline - time.monotonic()))
            if request_thread.is_alive():
                raise TimeoutError("HTTP join diagnostic")
        else:
            request_run()
        if worker_errors:
            raise worker_errors[0]
        observed.update(events=events, queue_attempts=queue_attempts, errors=errors, calls=calls,
                        settlement=settlement, clock_calls=clock_calls, warnings=warnings.rows)
        physical["completed"] = _files(state.runtime.root, org.db.path)[1]
    finally:
        release.set()
        cleanup_deadline = time.monotonic() + 10
        for thread in (holder, request_thread, *owned_actors):
            if thread is not None:
                thread.join(max(0, cleanup_deadline - time.monotonic()))
                if thread.is_alive():
                    raise RuntimeError("owned thread cleanup diagnostic")
        logger.removeHandler(warnings)
        connection.set_authorizer(None)
        patch.__exit__(None, None, None)
        released: list[bool] = []
        def release_probe() -> None:
            success = lock.acquire(timeout=3)
            released.append(success)
            if success:
                lock.release()
        probe = threading.Thread(target=release_probe, name="decline-owned-finally-probe")
        probe.start()
        probe.join(5)
        if probe.is_alive() or released != [True]:
            raise RuntimeError("native lock release diagnostic")
        before_close = _durable(org.db.path)
        physical["before_close"] = _files(state.runtime.root, org.db.path)[1]
        org.close()
        after_close = _durable(org.db.path)
        physical["after_close"] = _files(state.runtime.root, org.db.path)[1]
        reopened = facade.Database(org.db.path)
        try:
            reopened_frame = {"sql": _sql(reopened._conn), "in_transaction": reopened._conn.in_transaction}
        finally:
            reopened.close()
        asyncio.run(state.close_all())
        final_files, physical["after_reopen"] = _files(state.runtime.root, org.db.path)
        final_other_databases = {name: _durable(state.runtime.root / name)
                                for name in before["other_databases"]}
        observed["cleanup"] = {
            "class_restored": vars(facade.Database).get("mark_invocation_declined", _MISSING) is prior_class,
            "clock_restored": facade._now is prior_clock, "wholeclock_restored": facade._time is prior_wholeclock,
            "profile_restored": all(profile_restored), "authorizer_removed": True,
            "native_lock_released": released == [True], "holder_joined": holder is None or not holder.is_alive(),
            "request_joined": request_thread is None or not request_thread.is_alive(),
            **loop_cleanup, "orgs": list(state.orgs),
            "before_close": before_close, "after_close": after_close, "reopened": reopened_frame,
            "files": {"runtime": final_files, "daemon_home": _files(
                Path(os.environ["HAPPYRANCH_DAEMON_HOME"]), org.db.path)[0]},
            "other_databases": final_other_databases,
        }
        _write(destination / "native-provenance.json", raw_profile)
        _write(destination / "sqlite-physical-provenance.json", physical)
    return observed, expected


def _compare(observed: dict, expected: dict, destination: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = {}
    for name, module in list(sys.modules.items()):
        if (name == "runtime" or name.startswith("runtime.")) and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(root / "runtime"):
                raise RuntimeError("loaded-source provenance diagnostic: " + name + " " + str(path))
            loaded[name] = {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    _write(destination / "provenance.json", {
        "root": str(root), "cwd": str(Path.cwd().resolve()), "interpreter": sys.executable,
        "interpreter_real": str(Path(sys.executable).resolve()), "version": sys.version,
        "interpreter_sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
        "loaded": loaded, "argv": sys.argv,
    })
    _write(destination / "observed.json", observed)
    if observed != expected:
        print("DECLINE COMPLETE SHIPPING EXPECTED=" + _json(expected).decode())
        print("DECLINE COMPLETE SHIPPING OBSERVED=" + _json(observed).decode())
    assert observed == expected, "DECLINE complete shipping consumer frame"


def _exercise(case: str, tmp_path: Path, state, org, app, headers: dict, monkeypatch,
              *, mode: str = "ordinary") -> None:
    destination = Path(os.environ.get("DECLINE_RECEIPTS", str(tmp_path / "frames"))) / (case + "-" + mode)
    observed, expected = _collect(case, state, org, app, headers, monkeypatch, destination, mode=mode)
    _compare(observed, expected, destination)


@pytest.mark.parametrize("case", [
    "legacy-reply-text", "legacy-reply-null", "bootstrap-text", "bootstrap-null",
    "task-followup-text", "task-followup-null", "task-followup-whitespace", "task-followup-cas-miss",
])
def test_c1_complete_shipping_frame(case, tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise(case, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch,
              mode="cas" if case.endswith("cas-miss") else "ordinary")


@pytest.mark.parametrize("case", ["facade-clock", "sqlite-update-denied"])
def test_c2_shipping_failure_frame(case, tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("task-followup-text", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, mode=case)


def test_c3_old_clock_and_patch_frame(tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("task-followup-text", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, mode="oldpatch")


def test_c3_shared_lock_frame(tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("task-followup-text", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, mode="held")


def test_c3_wholeclock_warning_frame(tmp_home, tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch) -> None:
    _exercise("task-followup-text", tmp_path, daemon_state, org_state, app, auth_headers, monkeypatch, mode="wholeclock")


def _facade() -> dict:
    facade = importlib.import_module("runtime.infrastructure.database")
    threads = importlib.import_module("runtime.infrastructure.db.threads")
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
    return {
        "members": members,
        "mro": [[value.__module__, value.__qualname__] for value in facade.Database.__mro__],
        "types": {name: [value.__module__, value.__qualname__] for name, value in vars(facade).items() if isinstance(value, type)},
        "owners": {name: [[value.__module__, value.__qualname__] for value in facade.Database.__mro__ if name in vars(value)] for name in members},
        "identities": {
            "Database": facade.Database is importlib.import_module("runtime.infrastructure.database").Database,
            "ThreadsMixin": facade.ThreadsMixin is threads.ThreadsMixin,
            "_synchronized": facade._synchronized is threads._synchronized is shared._synchronized,
            "models": {name: value is getattr(models, value.__name__) for name, value in vars(facade).items()
                       if isinstance(value, type) and value.__module__ == "runtime.models"},
        },
    }


def _fresh(order: str, owned: Path, destination: Path) -> None:
    # The caller imports facade/threads in the chosen order BEFORE this module.
    from tests.daemon import conftest as fixtures

    patch = pytest.MonkeyPatch()
    try:
        home = fixtures.tmp_home.__wrapped__(owned, patch)
        runtime = fixtures.runtime.__wrapped__(owned)
        state = fixtures.daemon_state.__wrapped__(runtime)
        org = fixtures.org_state.__wrapped__(state)
        app = fixtures.app.__wrapped__(home, state)
        observed, expected = _collect("task-followup-text", state, org, app,
                                      fixtures.auth_headers.__wrapped__(), patch, destination)
    finally:
        patch.undo()
    _compare(observed, expected, destination)
    # Structural checks follow the FIRST complete actual shipping assertion.
    actual = _facade()
    expected_facade = deepcopy(_PRISTINE_FACADE)
    owner = os.environ.get("DECLINE_EXPECT_OWNER", "ThreadsMixin")
    if owner not in ("Database", "ThreadsMixin"):
        raise RuntimeError("invalid source-stage diagnostic")
    expected_facade["owners"]["mark_invocation_declined"] = [[
        "runtime.infrastructure.database" if owner == "Database" else "runtime.infrastructure.db.threads", owner,
    ]]
    _write(destination / "facade-observed.json", actual)
    _write(destination / "facade-expected.json", expected_facade)
    assert actual == expected_facade


@pytest.mark.parametrize("order", ["facade-first", "threads-first"])
def test_c4_fresh_import_shipping_frame(order: str, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    destination = Path(os.environ.get("DECLINE_RECEIPTS", str(tmp_path / "frames"))) / order
    first = "runtime.infrastructure.database" if order == "facade-first" else "runtime.infrastructure.db.threads"
    program = (
        "import importlib,pathlib,sys;sys.path.insert(0,sys.argv[1]);"
        "importlib.import_module(sys.argv[2]);"
        "from tests.daemon.test_thread_decline_decomposition import _fresh;"
        "_fresh(sys.argv[3],pathlib.Path(sys.argv[4]),pathlib.Path(sys.argv[5]))"
    )
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    argv = [sys.executable, "-I", "-B", "-c", program, str(root), first, order,
            str(tmp_path / "fresh-owned"), str(destination)]
    # run() kills and reaps the exact owned child on timeout; no borrowed server.
    try:
        proc = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired as error:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "child.stdout").write_bytes(error.stdout or b"")
        (destination / "child.stderr").write_bytes(error.stderr or b"")
        raise RuntimeError("fresh child timeout diagnostic; no contract verdict") from error
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "child.stdout").write_bytes(proc.stdout)
    (destination / "child.stderr").write_bytes(proc.stderr)
    _write(destination / "child-command.json", {"argv": argv, "cwd": str(root), "exit": proc.returncode, "reaped": True})
    assert proc.returncode == 0, proc.stdout.decode() + proc.stderr.decode()


# Complete pristine facade literal captured before controls/candidate; no digest surrogate.
# Include the two approved additive memory APIs in the complete facade baseline.
_PRISTINE_FACADE = {'identities': {'Database': True,
                'ThreadsMixin': True,
                '_synchronized': True,
                'models': {'AuthorityAuditEvent': True,
                           'AuthorityAuditEventType': True,
                           'AuthorityAuditPayload': True,
                           'AuthorityCandidate': True,
                           'AuthorityCandidatePolicyPin': True,
                           'AuthorityEvaluation': True,
                           'AuthorityFenceResult': True,
                           'AuthorityPolicyV2AdmissionSettlementOutcome': True,
                           'AuthorityPolicyV2Attempt': True,
                           'AuthorityPolicyV2Candidate': True,
                           'AuthorityPolicyV2CandidateAudit': True,
                           'AuthorityPolicyV2CompletionDispatchContext': True,
                           'AuthorityPolicyV2ContinueEnvelope': True,
                           'AuthorityPolicyV2DecisionAckOutcome': True,
                           'AuthorityPolicyV2DecisionClaimOutcome': True,
                           'AuthorityPolicyV2DecisionRefusalOutcome': True,
                           'AuthorityPolicyV2EnqueueDispatchClassification': True,
                           'AuthorityPolicyV2Evaluation': True,
                           'AuthorityPolicyV2FinalizationOutcome': True,
                           'AuthorityPolicyV2GenerationClaimOutcome': True,
                           'AuthorityPolicyV2HousekeepingOutcome': True,
                           'AuthorityPolicyV2HousekeepingTarget': True,
                           'AuthorityPolicyV2InvalidationOutcome': True,
                           'AuthorityPolicyV2Pin': True,
                           'AuthorityPolicyV2PublicationAckOutcome': True,
                           'AuthorityPolicyV2PublicationClaimOutcome': True,
                           'AuthorityPolicyV2PublicationFailureOutcome': True,
                           'AuthorityPolicyV2PublicationTarget': True,
                           'AuthorityPolicyV2RecoveryNotification': True,
                           'AuthorityPolicyV2RootDispatch': True,
                           'AuthorityPolicyV2SessionBinding': True,
                           'AuthorityPolicyV2SettlementOutcome': True,
                           'AuthorityPolicyV2SpendOutcome': True,
                           'AuthorityPolicyV2StageOutcome': True,
                           'AuthorityRedactionClass': True,
                           'AuthorityRetentionClass': True,
                           'BlockKind': True,
                           'DreamStatus': True,
                           'LocalCiEvidence': True,
                           'NextStep': True,
                           'ReplyDeliveryProjection': True,
                           'ScheduleStatus': True,
                           'TaskAttachmentRecord': True,
                           'TaskRecord': True,
                           'TaskStatus': True,
                           'ThreadAttachment': True,
                           'ThreadInvocation': True,
                           'ThreadInvocationPurpose': True,
                           'ThreadInvocationStatus': True,
                           'ThreadMessage': True,
                           'ThreadMessageKind': True,
                           'ThreadParticipant': True,
                           'ThreadRecord': True,
                           'ThreadReplyArrival': True,
                           'ThreadReplyBreakerEpisode': True,
                           'ThreadReplyClaim': True,
                           'ThreadReplyDeliveryState': True,
                           'ThreadReplyExchangeProjection': True,
                           'ThreadReplyRecoveryEntry': True,
                           'ThreadReplySettlement': True,
                           'ThreadScopedAttachment': True,
                           'ThreadStatus': True,
                           'TokenUsage': True,
                           'WorkHourStatus': True}},
 'members': {'append_memory_collection_transition': ['method', "(self, *, result_row_id: 'int') -> 'dict'"],
             'read_memory_collection_evidence': ['method', "(self) -> 'dict'"],
             '__class__': ['method', 'builtin-no-signature'],
             '__delattr__': ['method', '(self, name, /)'],
             '__dir__': ['method', '(self, /)'],
             '__eq__': ['method', '(self, value, /)'],
             '__format__': ['method', '(self, format_spec, /)'],
             '__ge__': ['method', '(self, value, /)'],
             '__getattribute__': ['method', '(self, name, /)'],
             '__getstate__': ['method', '(self, /)'],
             '__gt__': ['method', '(self, value, /)'],
             '__hash__': ['method', '(self, /)'],
             '__init__': ['method', "(self, db_path: 'Path') -> 'None'"],
             '__init_subclass__': ['method', '()'],
             '__le__': ['method', '(self, value, /)'],
             '__lt__': ['method', '(self, value, /)'],
             '__ne__': ['method', '(self, value, /)'],
             '__new__': ['method', '(*args, **kwargs)'],
             '__reduce__': ['method', '(self, /)'],
             '__reduce_ex__': ['method', '(self, protocol, /)'],
             '__repr__': ['method', '(self, /)'],
             '__setattr__': ['method', '(self, name, value, /)'],
             '__sizeof__': ['method', '(self, /)'],
             '__str__': ['method', '(self, /)'],
             '__subclasshook__': ['method', '(object, /)'],
             '_ack_v2_notification_publication_uncommitted': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "manager_session_id: 'str', "
                                                              "result_id: 'int', "
                                                              "publication_attempt: 'int', "
                                                              "publisher_boot_id: 'str', now_dt: "
                                                              "'datetime') -> "
                                                              "'AuthorityPolicyV2PublicationAckOutcome'"],
             '_acknowledge_v2_decision_dispatch_uncommitted': ['method',
                                                               "(self, *, root_task_id: 'str', "
                                                               "manager_agent: 'str', result_id: "
                                                               "'int') -> "
                                                               "'AuthorityPolicyV2DecisionAckOutcome'"],
             '_add_thread_participant_uncommitted': ['method',
                                                     "(self, thread_id: 'str', agent_name: 'str', "
                                                     "*, added_by: 'str') -> 'bool'"],
             '_advance_v2_attempt_stage_uncommitted': ['method',
                                                       '(self, attempt: '
                                                       "'AuthorityPolicyV2Attempt', new_stage: "
                                                       "'str') -> 'None'"],
             '_advance_v2_candidate_lifecycle_uncommitted': ['method',
                                                             '(self, candidate: '
                                                             "'AuthorityPolicyV2Candidate', "
                                                             "new_stage: 'str') -> 'None'"],
             '_append_authority_policy_activation_uncommitted': ['method',
                                                                 "(self, *, team: 'str', release: "
                                                                 "'AuthorityPolicyRelease', "
                                                                 "expected_previous_epoch: 'int', "
                                                                 "action: 'str', request_id: "
                                                                 "'str', request_digest: 'str') -> "
                                                                 "'AuthorityPolicyActivation'"],
             '_append_thread_message_uncommitted': ['method',
                                                    "(self, *, thread_id: 'str', speaker: 'str', "
                                                    "kind: 'ThreadMessageKind', body_markdown: "
                                                    "'str | None' = None, decline_reason: 'str | "
                                                    "None' = None, system_payload: 'dict | None' = "
                                                    "None, attachments: 'list[ThreadAttachment] | "
                                                    "None' = None, sent_from_task_id: 'str | None' "
                                                    "= None, mentions: 'list[str] | None' = None) "
                                                    "-> 'int'"],
             '_apply_arrival_uncommitted': ['method',
                                            "(self, thread_id: 'str', agent_name: 'str', seq: "
                                            "'int') -> 'ThreadReplyArrival'"],
             '_attachments_for_messages': ['method',
                                           "(self, thread_id: 'str', seqs: 'list[int]') -> "
                                           "'dict[int, list[ThreadAttachment]]'"],
             '_audit_authority_policy_v2_candidate_claim_uncommitted': ['method',
                                                                        '(self, *, root_task_id: '
                                                                        "'str', manager_agent: "
                                                                        "'str', "
                                                                        'manager_session_id: '
                                                                        "'str', result_id: 'int', "
                                                                        "origin_boot_id: 'str', "
                                                                        "owner_attempt_id: 'str', "
                                                                        "now: 'str', "
                                                                        "max_revise_rounds: 'int' "
                                                                        '= 0) -> '
                                                                        "'AuthorityPolicyV2StageOutcome'"],
             '_audit_consumption_uncommitted': ['method',
                                                "(self, *, root_task_id: 'str', manager_agent: "
                                                "'str', manager_session_id: 'str', result_id: "
                                                "'int', origin_boot_id: 'str', owner_attempt_id: "
                                                "'str', now: 'str', max_revise_rounds: 'int' = 0) "
                                                "-> 'AuthorityPolicyV2StageOutcome'"],
             '_audit_evaluation_uncommitted': ['method',
                                               "(self, *, root_task_id: 'str', manager_agent: "
                                               "'str', manager_session_id: 'str', result_id: "
                                               "'int', origin_boot_id: 'str', owner_attempt_id: "
                                               "'str', now: 'str', max_revise_rounds: 'int' = 0) "
                                               "-> 'AuthorityPolicyV2StageOutcome'"],
             '_authenticate_authority_legacy_control_receipt': ['method',
                                                                '(self, receipt: '
                                                                "'AuthorityPolicyLegacyControlReceipt', "
                                                                "team: 'str', selectors: "
                                                                "'list[AuthorityPolicySelector]') "
                                                                "-> 'AuthorityPolicySelector'"],
             '_authenticate_authority_selector_control_audit': ['method',
                                                                "(self, team: 'str', selectors: "
                                                                "'list[AuthorityPolicySelector]', "
                                                                "*, bounded: 'bool' = False) -> "
                                                                "'None'"],
             '_authenticate_authority_v2_control_receipt': ['method',
                                                            '(self, receipt: '
                                                            "'AuthorityPolicyV2ControlReceipt', "
                                                            "team: 'str', selectors: "
                                                            "'list[AuthorityPolicySelector]') -> "
                                                            "'AuthorityPolicySelector'"],
             '_authenticate_exact_v2_zombie_binding_uncommitted': ['method',
                                                                   "(self, *, task_id: 'str', "
                                                                   "agent: 'str', session_id: "
                                                                   "'str') -> "
                                                                   "'AuthorityPolicyV2SessionBinding "
                                                                   "| None'"],
             '_authenticate_v2_admission_event_uncommitted': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', attempt_id: "
                                                              "'str', expected: 'dict') -> 'bool'"],
             '_authenticate_v2_admission_ready_uncommitted': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "manager_session_id: 'str', "
                                                              "result_id: 'int') -> 'tuple[str | "
                                                              "None, dict | None]'"],
             '_authenticate_v2_attempt_admission_audit_uncommitted': ['method',
                                                                      "(self, attempt_row: 'dict') "
                                                                      "-> 'bool'"],
             '_authenticate_v2_attempt_admission_uncommitted': ['method',
                                                                "(self, *, task_id: 'str', agent: "
                                                                "'str', session_id: 'str', "
                                                                "admission: 'dict') -> 'bool'"],
             '_authenticate_v2_attempt_stage_audit_uncommitted': ['method',
                                                                  "(self, attempt_row: 'dict', "
                                                                  "stage: 'str', *, "
                                                                  "require_unfinalized: 'bool' = "
                                                                  "False) -> 'bool'"],
             '_authenticate_v2_candidate_audit_uncommitted': ['method',
                                                              '(self, candidate: '
                                                              "'AuthorityPolicyV2Candidate', "
                                                              "event: 'str') -> 'bool'"],
             '_authenticate_v2_candidate_evidence_uncommitted': ['method',
                                                                 "(self, *, root_task_id: 'str', "
                                                                 "manager_agent: 'str', "
                                                                 "manager_session_id: 'str', "
                                                                 "result_id: 'int', "
                                                                 "origin_boot_id: 'str', "
                                                                 "owner_attempt_id: 'str', "
                                                                 "max_revise_rounds: 'int' = 0) -> "
                                                                 "'tuple[str | None, dict | "
                                                                 "None]'"],
             '_authenticate_v2_candidate_pin_joins_uncommitted': ['method',
                                                                  '(self, *, attempt: '
                                                                  "'AuthorityPolicyV2Attempt', "
                                                                  'candidate: '
                                                                  "'AuthorityPolicyV2Candidate', "
                                                                  "pin: 'AuthorityPolicyV2Pin', "
                                                                  "binding, release) -> 'bool'"],
             '_authenticate_v2_claim_evidence_uncommitted': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "manager_session_id: 'str', "
                                                             "result_id: 'int', origin_boot_id: "
                                                             "'str', owner_attempt_id: 'str', "
                                                             "max_revise_rounds: 'int' = 0) -> "
                                                             "'tuple[str | None, dict | None]'"],
             '_authenticate_v2_decision_event_uncommitted': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "expected_events: 'dict[str, dict]') "
                                                             "-> 'bool'"],
             '_authenticate_v2_evaluation_evidence_uncommitted': ['method',
                                                                  '(self, *, candidate: '
                                                                  "'AuthorityPolicyV2Candidate', "
                                                                  'attempt: '
                                                                  "'AuthorityPolicyV2Attempt', "
                                                                  "binding, release) -> 'tuple[str "
                                                                  "| None, dict | None]'"],
             '_authenticate_v2_final_hook_audit_uncommitted': ['method',
                                                               '(self, attempt_row, candidate_id: '
                                                               "'str', envelope_id: 'str', "
                                                               "notification_id: 'str', "
                                                               "generation_id: 'str') -> 'bool'"],
             '_authenticate_v2_final_result_stage_uncommitted': ['method',
                                                                 '(self, attempt_row, '
                                                                 "candidate_id: 'str', "
                                                                 "envelope_id: 'str', "
                                                                 "notification_id: 'str', "
                                                                 "generation_id: 'str') -> 'bool'"],
             '_authenticate_v2_final_rows_uncommitted': ['method',
                                                         '(self, *, attempt: '
                                                         "'AuthorityPolicyV2Attempt', attempt_row, "
                                                         "candidate: 'AuthorityPolicyV2Candidate', "
                                                         'evaluation: '
                                                         "'AuthorityPolicyV2Evaluation', "
                                                         "require_dispatch_generation: 'bool' = "
                                                         "True) -> 'tuple[str | None, dict | "
                                                         "None]'"],
             '_authenticate_v2_final_task_audit_uncommitted': ['method',
                                                               '(self, attempt_row, candidate_id: '
                                                               "'str', envelope_id: 'str', "
                                                               "notification_id: 'str', "
                                                               "generation_id: 'str') -> 'bool'"],
             '_authenticate_v2_obligation_uncommitted': ['method',
                                                         "(self, attempt_row: 'dict') -> 'str | "
                                                         "None'"],
             '_authenticate_v2_ordinary_completion_evidence_uncommitted': ['method',
                                                                           '(self, *, '
                                                                           "root_task_id: 'str', "
                                                                           "manager_agent: 'str', "
                                                                           "result_row) -> 'bool'"],
             '_authenticate_v2_post_final_evidence_uncommitted': ['method',
                                                                  "(self, *, root_task_id: 'str', "
                                                                  "manager_agent: 'str', "
                                                                  "manager_session_id: 'str', "
                                                                  "result_id: 'int', "
                                                                  'require_dispatch_generation: '
                                                                  "'bool' = True) -> 'tuple[str | "
                                                                  "None, dict | None]'"],
             '_authenticate_v2_post_final_task_uncommitted': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "manager_session_id: 'str') -> "
                                                              "'bool'"],
             '_authenticate_v2_prior_result_stages_uncommitted': ['method',
                                                                  "(self, attempt_row: 'dict', "
                                                                  "stages: 'tuple[str, ...]', "
                                                                  "candidate_id: 'str') -> 'bool'"],
             '_authenticate_v2_publication_event_uncommitted': ['method',
                                                                "(self, *, root_task_id: 'str', "
                                                                "manager_agent: 'str', attempt_id: "
                                                                "'str', expected: 'dict') -> "
                                                                "'bool'"],
             '_authenticate_v2_publication_final_uncommitted': ['method',
                                                                "(self, *, root_task_id: 'str', "
                                                                "manager_agent: 'str', "
                                                                "manager_session_id: 'str', "
                                                                "result_id: 'int', "
                                                                "allow_post_admission: 'bool' = "
                                                                "False) -> 'tuple[str | None, dict "
                                                                "| None]'"],
             '_authenticate_v2_publication_settlement_proof_uncommitted': ['method',
                                                                           '(self, *, '
                                                                           "root_task_id: 'str', "
                                                                           "manager_agent: 'str', "
                                                                           'manager_session_id: '
                                                                           "'str', result_id: "
                                                                           "'int', attempt, "
                                                                           'candidate, envelope, '
                                                                           'notification, '
                                                                           "result_row) -> 'bool'"],
             '_authenticate_v2_publication_stage_evidence_uncommitted': ['method',
                                                                         '(self, *, root_task_id: '
                                                                         "'str', manager_agent: "
                                                                         "'str', attempt_id: "
                                                                         "'str', candidate_id: "
                                                                         "'str', result_id: 'int', "
                                                                         "envelope_id: 'str', "
                                                                         "notification_id: 'str', "
                                                                         "generation_id: 'str', "
                                                                         'publication_attempt: '
                                                                         "'int', "
                                                                         'publisher_boot_id: '
                                                                         "'str', stage: 'str') -> "
                                                                         "'bool'"],
             '_authenticate_v2_refusal_completion_uncommitted': ['method',
                                                                 "(self, attempt_row: 'dict', *, "
                                                                 "refusal_code: 'str') -> "
                                                                 "'tuple[bool, bool]'"],
             '_authenticate_v2_refusal_escalation_uncommitted': ['method',
                                                                 "(self, attempt_row: 'dict', *, "
                                                                 "refusal_code: 'str') -> 'str | "
                                                                 "None'"],
             '_authenticate_v2_refusal_result_stage_uncommitted': ['method',
                                                                   "(self, attempt_row: 'dict', *, "
                                                                   "candidate_id: 'str | None', "
                                                                   "refusal_code: 'str', "
                                                                   "finalization_state: 'str') -> "
                                                                   "'bool'"],
             '_authenticate_v2_result_body_uncommitted': ['method',
                                                          '(self, result_row, attempt: '
                                                          "'AuthorityPolicyV2Attempt') -> 'bool'"],
             '_authenticate_v2_result_stage_audit_uncommitted': ['method',
                                                                 "(self, attempt_row: 'dict', "
                                                                 "stage: 'str', *, candidate_id: "
                                                                 "'str | None' = None, "
                                                                 "require_finalization: 'bool' = "
                                                                 "False) -> 'bool'"],
             '_authenticate_v2_retained_claim_boot_uncommitted': ['method',
                                                                  "(self, *, root_task_id: 'str', "
                                                                  "manager_agent: 'str', "
                                                                  "attempt_id: 'str', "
                                                                  "candidate_id: 'str', result_id: "
                                                                  "'int', envelope_id: 'str', "
                                                                  "notification_id: 'str', "
                                                                  "generation_id: 'str', "
                                                                  "publication_attempt: 'int', "
                                                                  "expected_boot: 'str | None') -> "
                                                                  "'str | None'"],
             '_authenticate_v2_retained_publication_evidence_uncommitted': ['method',
                                                                            '(self, *, '
                                                                            "root_task_id: 'str', "
                                                                            "manager_agent: 'str', "
                                                                            "attempt_id: 'str', "
                                                                            "candidate_id: 'str', "
                                                                            "result_id: 'int', "
                                                                            "envelope_id: 'str', "
                                                                            'notification, '
                                                                            "generation_id: 'str', "
                                                                            'allowed_states: '
                                                                            "'tuple[str, ...]') -> "
                                                                            "'bool'"],
             '_authenticate_v2_session_binding_uncommitted': ['method',
                                                              '(self, binding: '
                                                              "'AuthorityPolicyV2SessionBinding') "
                                                              "-> 'None'"],
             '_authenticate_v2_settlement_evidence_uncommitted': ['method',
                                                                  "(self, *, root_task_id: 'str', "
                                                                  "manager_agent: 'str', "
                                                                  "result_id: 'int', "
                                                                  "manager_session_id: 'str', "
                                                                  'result_row, attempt, candidate, '
                                                                  'envelope, notification) -> '
                                                                  "'bool'"],
             '_authenticate_v2_settlement_final_uncommitted': ['method',
                                                               "(self, *, root_task_id: 'str', "
                                                               "manager_agent: 'str', "
                                                               "manager_session_id: 'str', "
                                                               "result_id: 'int') -> 'tuple[str | "
                                                               "None, dict | None]'"],
             '_authenticate_v2_settlement_pre_state_uncommitted': ['method',
                                                                   "(self, *, root_task_id: 'str', "
                                                                   "manager_agent: 'str', "
                                                                   "result_id: 'int', "
                                                                   "manager_session_id: 'str', "
                                                                   'attempt, candidate, envelope, '
                                                                   "notification) -> 'bool'"],
             '_authenticate_v2_spend_event_uncommitted': ['method',
                                                          "(self, *, root_task_id: 'str', "
                                                          "manager_agent: 'str', expected: 'dict') "
                                                          "-> 'bool'"],
             '_authenticate_v2_spending_result_uncommitted': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "next_session_id: 'str', "
                                                              'spending_result_id, '
                                                              "causal_result_id: 'int')"],
             '_authenticate_v2_spent_decision_receipt_uncommitted': ['method',
                                                                     '(self, *, root_task_id: '
                                                                     "'str', manager_agent: 'str', "
                                                                     "result_id: 'int') -> "
                                                                     "'tuple[str | None, dict | "
                                                                     "None]'"],
             '_authenticate_v2_zombie_consumption_receipt_uncommitted': ['method',
                                                                         '(self, *, task_id: '
                                                                         "'str', agent: 'str', "
                                                                         "session_id: 'str', "
                                                                         "result_id: 'int', "
                                                                         "task_row) -> 'bool'"],
             '_authority_begin': ['method', "(self) -> 'bool'"],
             '_authority_candidate_from_row': ['method', "(self, row) -> 'AuthorityCandidate'"],
             '_authority_commit': ['method', "(self, nested: 'bool') -> 'None'"],
             '_authority_policy_activation_from_row': ['method',
                                                       '(self, row) -> '
                                                       "'AuthorityPolicyActivation'"],
             '_authority_policy_legacy_receipt_from_audit_row': ['method',
                                                                 '(row) -> '
                                                                 "'AuthorityPolicyLegacyControlReceipt'"],
             '_authority_policy_release_from_row': ['method',
                                                    "(self, row) -> 'AuthorityPolicyRelease'"],
             '_authority_policy_selector_from_row': ['method',
                                                     "(self, row) -> 'AuthorityPolicySelector'"],
             '_authority_policy_v2_activation_from_row': ['method',
                                                          '(self, row) -> '
                                                          "'AuthorityPolicyV2Activation'"],
             '_authority_policy_v2_attempt_from_row': ['method',
                                                       "(self, row) -> 'AuthorityPolicyV2Attempt'"],
             '_authority_policy_v2_attempt_id_for_identity': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "manager_session_id: 'str', "
                                                              "result_id: 'int') -> 'str'"],
             '_authority_policy_v2_candidate_from_row': ['method',
                                                         '(self, row) -> '
                                                         "'AuthorityPolicyV2Candidate'"],
             '_authority_policy_v2_envelope_from_row': ['method',
                                                        '(self, row) -> '
                                                        "'AuthorityPolicyV2ContinueEnvelope'"],
             '_authority_policy_v2_evaluation_from_row': ['method',
                                                          '(self, row) -> '
                                                          "'AuthorityPolicyV2Evaluation'"],
             '_authority_policy_v2_notification_from_row': ['method',
                                                            '(self, row) -> '
                                                            "'AuthorityPolicyV2RecoveryNotification'"],
             '_authority_policy_v2_pin_from_row': ['method',
                                                   "(self, row) -> 'AuthorityPolicyV2Pin'"],
             '_authority_policy_v2_receipt_from_audit_row': ['method',
                                                             '(row) -> '
                                                             "'AuthorityPolicyV2ControlReceipt'"],
             '_authority_policy_v2_release_from_row': ['method',
                                                       "(self, row) -> 'AuthorityPolicyV2Release'"],
             '_authority_policy_v2_root_dispatch_from_row': ['method',
                                                             '(self, row) -> '
                                                             "'AuthorityPolicyV2RootDispatch'"],
             '_authority_policy_v2_session_binding_from_row': ['method',
                                                               '(self, row) -> '
                                                               "'AuthorityPolicyV2SessionBinding'"],
             '_authority_rollback': ['method', "(self, nested: 'bool') -> 'None'"],
             '_authority_write_transaction': ['method', '(self)'],
             '_backfill_revisit_of_task_id': ['method', "(self) -> 'None'"],
             '_claim_authority_policy_v2_candidate_uncommitted': ['method',
                                                                  "(self, *, root_task_id: 'str', "
                                                                  "manager_agent: 'str', "
                                                                  "manager_session_id: 'str', "
                                                                  "result_id: 'int', "
                                                                  "origin_boot_id: 'str', "
                                                                  "owner_attempt_id: 'str', "
                                                                  "max_revise_rounds: 'int', now: "
                                                                  "'str', schema_observation) -> "
                                                                  "'AuthorityPolicyV2StageOutcome'"],
             '_claim_v2_decision_dispatch_uncommitted': ['method',
                                                         "(self, *, root_task_id: 'str', "
                                                         "manager_agent: 'str', result_id: 'int') "
                                                         '-> '
                                                         "'AuthorityPolicyV2DecisionClaimOutcome'"],
             '_claim_v2_notification_publication_uncommitted': ['method',
                                                                "(self, *, root_task_id: 'str', "
                                                                "manager_agent: 'str', "
                                                                "manager_session_id: 'str', "
                                                                "result_id: 'int', "
                                                                "publisher_boot_id: 'str', now_dt: "
                                                                "'datetime') -> "
                                                                "'AuthorityPolicyV2PublicationClaimOutcome'"],
             '_clear_catchup_pending_uncommitted': ['method',
                                                    "(self, thread_id: 'str', agent_name: 'str') "
                                                    "-> 'int'"],
             '_clear_v2_refusal_failure_authority': ['method',
                                                     "(self, attempt_id: 'str', owner_attempt_id: "
                                                     "'str | None' = None) -> 'None'"],
             '_close_reply_exchange_uncommitted': ['method',
                                                   "(self, thread_id: 'str', exchange_id: 'int', "
                                                   "*, reason: 'str') -> "
                                                   "'list[ThreadReplyArrival]'"],
             '_consume_authority_policy_v2_candidate_uncommitted': ['method',
                                                                    '(self, *, root_task_id: '
                                                                    "'str', manager_agent: 'str', "
                                                                    "manager_session_id: 'str', "
                                                                    "result_id: 'int', "
                                                                    "origin_boot_id: 'str', "
                                                                    "owner_attempt_id: 'str', "
                                                                    "max_revise_rounds: 'int' = 0) "
                                                                    '-> '
                                                                    "'AuthorityPolicyV2StageOutcome'"],
             '_consume_v2_continue_envelope_uncommitted': ['method',
                                                           '(self, envelope: '
                                                           "'AuthorityPolicyV2ContinueEnvelope', "
                                                           "spending_result_id: 'int') -> 'None'"],
             '_create_authority_tables': ['method', "(self) -> 'None'"],
             '_create_tables': ['method', "(self) -> 'None'"],
             '_current_failed_contributions': ['method',
                                               "(self, desc: 'list[TaskRecord]', by_id: 'dict[str, "
                                               "TaskRecord]', succ: 'dict[str, list[str]]') -> "
                                               "'set[str]'"],
             '_current_severity_rollup': ['method', "(self, root: 'TaskRecord') -> 'str'"],
             '_derive_conversational_mentions': ['method',
                                                 "(self, thread_id: 'str', speaker: 'str', kind: "
                                                 "'ThreadMessageKind', body_markdown: 'str | "
                                                 "None') -> 'list[str] | None'"],
             '_dream_candidate_row_to_model': ['method', "(self, row) -> 'DreamKbCandidate'"],
             '_dream_row_to_model': ['method', "(self, row) -> 'DreamRecord'"],
             '_emit_reply_wake_audit': ['method',
                                        "(self, *, thread_id: 'str', agent_name: 'str', action: "
                                        "'str', payload: 'dict') -> 'None'"],
             '_ensure_task_attachments_storage_key_unique': ['method', "(self) -> 'None'"],
             '_evaluate_authority_policy_v2_candidate_uncommitted': ['method',
                                                                     '(self, *, root_task_id: '
                                                                     "'str', manager_agent: 'str', "
                                                                     "manager_session_id: 'str', "
                                                                     "result_id: 'int', "
                                                                     "origin_boot_id: 'str', "
                                                                     "owner_attempt_id: 'str', "
                                                                     "now: 'str', "
                                                                     "max_revise_rounds: 'int' = "
                                                                     '0) -> '
                                                                     "'AuthorityPolicyV2StageOutcome'"],
             '_evaluate_exchange_closure': ['method',
                                            "(self, thread_id: 'str') -> "
                                            "'list[ThreadReplyArrival]'"],
             '_exchange_cohort_uncommitted': ['method', "(self, exchange_row) -> 'list[str]'"],
             '_exchange_has_live_cohort_wake': ['method',
                                                "(self, thread_id: 'str', open_seq: 'int', cohort: "
                                                "'list[str]') -> 'bool'"],
             '_extend_reply_exchange_uncommitted': ['method',
                                                    "(self, thread_id: 'str', seq: 'int') -> "
                                                    "'None'"],
             '_finalize_v2_continuation_replay_uncommitted': ['method',
                                                              '(self, attempt: '
                                                              "'AuthorityPolicyV2Attempt', "
                                                              'attempt_row) -> '
                                                              "'AuthorityPolicyV2FinalizationOutcome'"],
             '_finalize_v2_continuation_uncommitted': ['method',
                                                       "(self, *, root_task_id: 'str', "
                                                       "manager_agent: 'str', manager_session_id: "
                                                       "'str', result_id: 'int', origin_boot_id: "
                                                       "'str', owner_attempt_id: 'str', "
                                                       "max_revise_rounds: 'int', now: 'str') -> "
                                                       "'AuthorityPolicyV2FinalizationOutcome'"],
             '_forget_v2_live_owner': ['method',
                                       "(self, attempt_id: 'str', owner_attempt_id: 'str') -> "
                                       "'None'"],
             '_get_authority_selector_uncommitted': ['method',
                                                     "(self, team: 'str') -> "
                                                     "'AuthorityPolicySelector | None'"],
             '_get_open_exchange_uncommitted': ['method', "(self, thread_id: 'str')"],
             '_get_subtree_tasks': ['method', "(self, root_task_id: 'str') -> 'list[TaskRecord]'"],
             '_has_live_manager_supersession_family_work_uncommitted': ['method',
                                                                        "(self, task_id: 'str') -> "
                                                                        "'bool'"],
             '_increment_thread_turns_used_uncommitted': ['method',
                                                          "(self, thread_id: 'str', *, by: 'int' = "
                                                          "1) -> 'None'"],
             '_insert_authority_policy_activation_audit_uncommitted': ['method',
                                                                       "(self, *, team: 'str', "
                                                                       'release: '
                                                                       "'AuthorityPolicyRelease', "
                                                                       'activation: '
                                                                       "'AuthorityPolicyActivation', "
                                                                       "request_digest: 'str') -> "
                                                                       "'None'"],
             '_insert_authority_policy_selector_history_uncommitted': ['method',
                                                                       '(self, selector: '
                                                                       "'AuthorityPolicySelector') "
                                                                       "-> 'None'"],
             '_insert_authority_policy_v2_attempt_uncommitted': ['method',
                                                                 "(self, *, task_id: 'str', agent: "
                                                                 "'str', session_id: 'str', "
                                                                 "result_id: 'int', admission: "
                                                                 "'dict', now: 'str') -> "
                                                                 "'AuthorityPolicyV2Attempt'"],
             '_insert_authority_policy_v2_control_audit_uncommitted': ['method',
                                                                       "(self, *, team: 'str', "
                                                                       "request_id: 'str | None', "
                                                                       "request_digest: 'str | "
                                                                       "None', kind: 'str', "
                                                                       "release_id: 'str | None', "
                                                                       "activation_id: 'str | "
                                                                       "None', selector_id: 'str | "
                                                                       "None', action: 'str | "
                                                                       "None', payload_json: "
                                                                       "'str', created_at: 'str') "
                                                                       "-> 'None'"],
             '_insert_task_attachments_txn': ['method',
                                              "(self, task_id: 'str', attachments: 'list[dict]', "
                                              "uploaded_by: 'str') -> 'None'"],
             '_insert_task_result': ['method',
                                     "(self, task_id: 'str', agent: 'str', session_id: 'str', "
                                     "output_summary: 'str', confidence_score: 'int', status: "
                                     "'str' = 'completed', risks_flagged: 'list[str] | None' = "
                                     "None, learnings: 'str | None' = None, duration_seconds: 'int "
                                     "| None' = None, token_count: 'int | None' = None, "
                                     "estimated_cost: 'float | None' = None, output_dir: 'str | "
                                     "None' = None, decision_json: 'str | None' = None, "
                                     "waiting_on_job_ids: 'list[str] | None' = None, verdict: 'str "
                                     "| None' = None, local_ci_json: 'str | None' = None) -> "
                                     "'None'"],
             '_insert_task_uncommitted': ['method', "(self, task: 'TaskRecord') -> 'None'"],
             '_insert_thread_uncommitted': ['method', "(self, t: 'ThreadRecord') -> 'None'"],
             '_insert_v2_candidate_and_pin_uncommitted': ['method',
                                                          '(self, *, candidate: '
                                                          "'AuthorityPolicyV2Candidate', attempt: "
                                                          "'AuthorityPolicyV2Attempt', binding: "
                                                          "'AuthorityPolicyV2SessionBinding', "
                                                          "owner_attempt_id: 'str', "
                                                          "origin_boot_id: 'str', now: 'str') -> "
                                                          "'AuthorityPolicyV2StageOutcome'"],
             '_insert_v2_candidate_audit_uncommitted': ['method',
                                                        '(self, candidate: '
                                                        "'AuthorityPolicyV2Candidate', event: "
                                                        "'str', *, owner_attempt_id: 'str', "
                                                        "origin_boot_id: 'str', now: 'str') -> "
                                                        "'None'"],
             '_insert_v2_continue_envelope_uncommitted': ['method',
                                                          '(self, envelope: '
                                                          "'AuthorityPolicyV2ContinueEnvelope') -> "
                                                          "'None'"],
             '_insert_v2_recovery_notification_uncommitted': ['method',
                                                              '(self, notification: '
                                                              "'AuthorityPolicyV2RecoveryNotification') "
                                                              "-> 'None'"],
             '_invalidate_v2_notification_generation_uncommitted': ['method',
                                                                    '(self, *, root_task_id: '
                                                                    "'str', manager_agent: 'str', "
                                                                    "manager_session_id: 'str', "
                                                                    "result_id: 'int', now_dt: "
                                                                    "'datetime') -> "
                                                                    "'AuthorityPolicyV2InvalidationOutcome'"],
             '_legacy_session_binding_rows_uncommitted': ['method',
                                                          "(self, task_id: 'str', agent_name: "
                                                          "'str', session_id: 'str') -> "
                                                          "'list[dict]'"],
             '_load_authority_selector_history_chain': ['method',
                                                        "(self, team: 'str', *, up_to_selector_id: "
                                                        "'str | None' = None) -> "
                                                        "'list[AuthorityPolicySelector]'"],
             '_mark_catchup_pending_uncommitted': ['method',
                                                   "(self, thread_id: 'str', exchange_id: 'int', "
                                                   "agent_name: 'str') -> 'bool'"],
             '_mark_v2_refusal_failure_authority': ['method',
                                                    "(self, attempt_id: 'str', owner_attempt_id: "
                                                    "'str') -> 'None'"],
             '_migrate_dark_authority_activation_seal_if_needed': ['method', "(self) -> 'None'"],
             '_migrate_drop_talk_surface_if_needed': ['method', "(self) -> 'None'"],
             '_migrate_jobs_table_if_needed': ['method', "(self) -> 'None'"],
             '_migrate_remote_job_schema': ['method', "(self) -> 'None'"],
             '_migrate_session_token_usage_scope_columns': ['method', "(self) -> 'None'"],
             '_migrate_thread_invocation_attribution_columns': ['method', "(self) -> 'None'"],
             '_migrate_thread_invocation_reply_message_link': ['method', "(self) -> 'None'"],
             '_mint_reply_invocation_uncommitted': ['method',
                                                    "(self, thread_id: 'str', agent_name: 'str', "
                                                    "triggering_seq: 'int') -> 'str'"],
             '_open_reply_exchange_uncommitted': ['method',
                                                  "(self, *, thread_id: 'str', open_seq: 'int', "
                                                  "speaker: 'str', mentions: 'list[str]', "
                                                  "recipients: 'list[str]') -> 'int'"],
             '_pair_catchup_pending_uncommitted': ['method',
                                                   "(self, thread_id: 'str', agent_name: 'str') -> "
                                                   "'bool'"],
             '_pair_held_by_open_exchange': ['method',
                                             "(self, thread_id: 'str', agent_name: 'str') -> "
                                             "'bool'"],
             '_raise_required_uncommitted': ['method',
                                             "(self, thread_id: 'str', agent_name: 'str', seq: "
                                             "'int') -> 'None'"],
             '_record_v2_failed_stage_obligation': ['method',
                                                    "(self, *, attempt_id: 'str', "
                                                    "owner_attempt_id: 'str', code: 'str') -> "
                                                    "'None'"],
             '_record_v2_notification_publication_failure_uncommitted': ['method',
                                                                         '(self, *, root_task_id: '
                                                                         "'str', manager_agent: "
                                                                         "'str', "
                                                                         'manager_session_id: '
                                                                         "'str', result_id: 'int', "
                                                                         'publication_attempt: '
                                                                         "'int', "
                                                                         'publisher_boot_id: '
                                                                         "'str', now_dt: "
                                                                         "'datetime') -> "
                                                                         "'AuthorityPolicyV2PublicationFailureOutcome'"],
             '_refuse_v2_decision_dispatch_uncommitted': ['method',
                                                          "(self, *, root_task_id: 'str', "
                                                          "manager_agent: 'str', result_id: 'int', "
                                                          "now_dt: 'datetime') -> "
                                                          "'AuthorityPolicyV2DecisionRefusalOutcome'"],
             '_refuse_v2_stage': ['method',
                                  "(self, *, attempt_id: 'str', owner_attempt_id: 'str', code: "
                                  "'str', origin_boot_id: 'str | None' = None, candidate_id: 'str "
                                  "| None' = None, claim_key: 'str | None' = None, stage: 'str | "
                                  "None' = None, poison: 'bool | None' = None) -> "
                                  "'AuthorityPolicyV2StageOutcome'"],
             '_require_authority_selector_cas': ['method',
                                                 "(selector: 'AuthorityPolicySelector', "
                                                 "expected_selector_id: 'str | None') -> 'None'"],
             '_reset_thread_sessions_for_agent_uncommitted': ['method',
                                                              "(self, agent_name: 'str') -> 'int'"],
             '_reset_thread_sessions_for_thread_uncommitted': ['method',
                                                               "(self, thread_id: 'str') -> 'int'"],
             '_resolve_exchange_wake_set': ['method',
                                            "(self, *, thread_id: 'str', mentions: 'list[str]', "
                                            "recipients: 'list[str]', open_exchange) -> "
                                            "'list[str]'"],
             '_retained_v2_mechanical_eligibility_uncommitted': ['method',
                                                                 '(self, task, *, '
                                                                 "max_revise_rounds: 'int') -> "
                                                                 "'str | None'"],
             '_retire_skill_lifecycle_if_present': ['method', "(self) -> 'None'"],
             '_retire_v2_root_dispatch_uncommitted': ['method',
                                                      '(self, dispatch: '
                                                      "'AuthorityPolicyV2RootDispatch', now_dt: "
                                                      "'datetime') -> 'None'"],
             '_retrofit_authority_audit_fk_if_needed': ['method', "(self) -> 'None'"],
             '_retrofit_authority_lifecycle_trigger_if_needed': ['method', "(self) -> 'None'"],
             '_retrofit_authority_policy_activation_trigger_if_needed': ['method',
                                                                         "(self) -> 'None'"],
             '_retry_audits': ['method',
                               "(self, task_id: 'str', action: 'str') -> 'list[tuple[str, dict]]'"],
             '_retry_claim_matches': ['method',
                                      "(self, claim: 'RetryClaim', *, pending: 'bool' = False) -> "
                                      "'bool'"],
             '_retry_dispatch_edge': ['method',
                                      "(self, predecessor, successor, evidence: 'dict') -> 'None'"],
             '_retry_escalation_edge': ['method',
                                        "(self, predecessor, successor, evidence: 'dict') -> "
                                        "'None'"],
             '_retry_manager_edge': ['method',
                                     "(self, predecessor, successor, records: 'list') -> 'None'"],
             '_retry_object': ['method', "(raw: 'str') -> 'dict'"],
             '_retry_require_audit': ['method',
                                      "(self, task_id: 'str', action: 'str', agent: 'str', "
                                      "expected: 'dict', *, decision: 'str | None' = None) -> "
                                      "'None'"],
             '_retry_spawn_check': ['method',
                                    "(self, parent_id: 'str', children: 'list', claim: "
                                    "'RetryClaim', revision_delta: 'int' = 0, revision_cap: 'int' "
                                    "= 0, *, ordinary: 'bool' = False) -> 'InvalidLineage | "
                                    "LostClaim | None'"],
             '_row_to_completion_report': ['method',
                                           "(self, task_id: 'str', row) -> "
                                           '"\'CompletionReport\'"'],
             '_row_to_invocation': ['method', "(self, row) -> 'ThreadInvocation'"],
             '_row_to_job': ['method', '(row) -> "\'JobRecord\'"'],
             '_row_to_reply_breaker_episode': ['method',
                                               "(self, row) -> 'ThreadReplyBreakerEpisode'"],
             '_row_to_reply_delivery_state': ['method',
                                              "(self, row) -> 'ThreadReplyDeliveryState'"],
             '_row_to_thread': ['method', "(self, row) -> 'ThreadRecord'"],
             '_running_recovery_fail_reason': ['method',
                                               "(self, inv, *, same_pair: 'bool', right_purpose: "
                                               "'bool', pending: 'bool', started: 'bool', "
                                               "range_ok: 'bool') -> 'str'"],
             '_selector_session_binding_rows_uncommitted': ['method',
                                                            "(self, task_id: 'str', agent_name: "
                                                            "'str', session_id: 'str') -> "
                                                            "'list[dict]'"],
             '_session_token_usage_filters': ['method',
                                              "(self, *, since: 'str | None' = None, task_id: 'str "
                                              "| None' = None, agent: 'str | None' = None, "
                                              "scope_type: 'str | None' = None, scope_id: 'str | "
                                              "None' = None, thread_id: 'str | None' = None, "
                                              "purpose: 'str | None' = None) -> 'tuple[list[str], "
                                              "list[object]]'"],
             '_set_thread_status_archived_uncommitted': ['method',
                                                         "(self, thread_id: 'str', *, summary: "
                                                         "'str | None' = None) -> 'None'"],
             '_set_v2_decision_state_uncommitted': ['method',
                                                    '(self, envelope: '
                                                    "'AuthorityPolicyV2ContinueEnvelope', "
                                                    "new_state: 'str') -> 'None'"],
             '_settle_reply_uncommitted': ['method',
                                           "(self, token: 'str', *, outcome: 'str', "
                                           "decline_reason: 'str | None' = None, "
                                           "reply_message_seq: 'int | None' = None, "
                                           "reply_thread_id: 'str | None' = None, "
                                           "reply_agent_name: 'str | None' = None) -> "
                                           "'ThreadReplySettlement | None'"],
             '_settle_v2_continuation_generation_uncommitted': ['method',
                                                                "(self, *, root_task_id: 'str', "
                                                                "manager_agent: 'str', "
                                                                "manager_session_id: 'str', "
                                                                "result_id: 'int', generation_id: "
                                                                "'str | None', next_session_id: "
                                                                "'str', now_dt: 'datetime') -> "
                                                                "'AuthorityPolicyV2AdmissionSettlementOutcome'"],
             '_settle_v2_exact_receipt_uncommitted': ['method',
                                                      "(self, receipt_row, *, root_task_id: 'str', "
                                                      "manager_agent: 'str', manager_session_id: "
                                                      "'str', result_id: 'int', now: 'str') -> "
                                                      "'bool'"],
             '_spend_v2_continuation_envelope_uncommitted': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "manager_session_id: 'str', "
                                                             "result_id: 'int', generation_id: "
                                                             "'str | None', next_session_id: 'str "
                                                             "| None', spending_result_id, now_dt: "
                                                             "'datetime') -> "
                                                             "'AuthorityPolicyV2SpendOutcome'"],
             '_suppress_open_exchanges_uncommitted': ['method',
                                                      "(self, thread_id: 'str', *, reason: 'str') "
                                                      "-> 'None'"],
             '_sweep_corrupt_exchanges_uncommitted': ['method',
                                                      "(self) -> 'list[ThreadReplyArrival]'"],
             '_thread_tail_seq': ['method', "(self, thread_id: 'str') -> 'int'"],
             '_token_usage_rollup_select': ['method',
                                            "(group_expr: 'str', group_alias: 'str', *, "
                                            "include_model_classification: 'bool' = False) -> "
                                            "'str'"],
             '_try_claim_v2_continuation_generation_uncommitted': ['method',
                                                                   "(self, *, root_task_id: 'str', "
                                                                   "manager_agent: 'str', "
                                                                   "manager_session_id: 'str', "
                                                                   "result_id: 'int', "
                                                                   "generation_id: 'str | None', "
                                                                   "next_session_id: 'str', "
                                                                   "now_dt: 'datetime') -> "
                                                                   "'AuthorityPolicyV2GenerationClaimOutcome'"],
             '_update_v2_attempt_finalization_uncommitted': ['method',
                                                             '(self, attempt: '
                                                             "'AuthorityPolicyV2Attempt', "
                                                             "finalization_state: 'str', "
                                                             "refusal_code: 'str | None') -> "
                                                             "'None'"],
             '_upsert_v2_root_dispatch_pending_uncommitted': ['method',
                                                              '(self, dispatch: '
                                                              "'AuthorityPolicyV2RootDispatch', *, "
                                                              "prior_generation_id: 'str | None') "
                                                              "-> 'None'"],
             '_v2_admission_event_payload': ['method',
                                             "(self, *, stage: 'str', attempt_id: 'str', "
                                             "candidate_id: 'str', result_id: 'int', envelope_id: "
                                             "'str', notification_id: 'str', generation_id: 'str', "
                                             "next_session_id: 'str') -> 'dict'"],
             '_v2_authenticate_publication_claim_history_uncommitted': ['method',
                                                                        '(self, *, root_task_id: '
                                                                        "'str', manager_agent: "
                                                                        "'str', attempt_id: 'str', "
                                                                        "candidate_id: 'str', "
                                                                        "result_id: 'int', "
                                                                        "envelope_id: 'str', "
                                                                        "notification_id: 'str', "
                                                                        "generation_id: 'str', "
                                                                        'publication_attempt: '
                                                                        "'int') -> 'dict[int, "
                                                                        "dict] | None'"],
             '_v2_authenticated_publication_events_by_attempt_uncommitted': ['method',
                                                                             '(self, *, '
                                                                             "root_task_id: 'str', "
                                                                             'manager_agent: '
                                                                             "'str', attempt_id: "
                                                                             "'str', stage: 'str', "
                                                                             "candidate_id: 'str', "
                                                                             "result_id: 'int', "
                                                                             "envelope_id: 'str', "
                                                                             'notification_id: '
                                                                             "'str', "
                                                                             'generation_id: '
                                                                             "'str', "
                                                                             'reference_attempt: '
                                                                             "'int') -> 'dict[int, "
                                                                             "dict] | None'"],
             '_v2_candidate_from_claim_key': ['method',
                                              "(self, claim_key: 'str', *, attempt: "
                                              "'AuthorityPolicyV2Attempt', binding: "
                                              "'AuthorityPolicyV2SessionBinding', origin_boot_id: "
                                              "'str', owner_attempt_id: 'str', schema_observation, "
                                              "permission_surface_digest: 'str') -> "
                                              "'AuthorityPolicyV2Candidate'"],
             '_v2_claim_task_owner_current_uncommitted': ['method',
                                                          "(self, *, root_task_id: 'str', "
                                                          "manager_agent: 'str', next_session_id: "
                                                          "'str') -> 'bool'"],
             '_v2_closed_audit_matches': ['method', "(self, rows, expected: 'dict') -> 'bool'"],
             '_v2_completion_material_projection_from_report': ['method',
                                                                "(report) -> 'dict | None'"],
             '_v2_completion_material_projection_from_row': ['method', "(row) -> 'dict | None'"],
             '_v2_completion_row_is_ordinary_related': ['method',
                                                        "(payload, *, result_id: 'int', "
                                                        "manager_session_id: 'str') -> 'bool'"],
             '_v2_completion_row_is_recovery_related': ['method',
                                                        "(payload, *, result_id: 'int', "
                                                        "manager_session_id: 'str') -> 'bool'"],
             '_v2_contender_is_authentic_owner': ['method',
                                                  "(self, *, attempt_id: 'str', owner_attempt_id: "
                                                  "'str', origin_boot_id: 'str') -> 'bool'"],
             '_v2_decision_event_payload': ['method',
                                            "(self, *, stage: 'str', attempt_id: 'str', "
                                            "candidate_id: 'str', result_id: 'int', envelope_id: "
                                            "'str', notification_id: 'str', generation_id: 'str', "
                                            "next_session_id: 'str', spending_result_id: 'int', "
                                            "report_digest: 'str') -> 'dict'"],
             '_v2_decision_events_absent_uncommitted': ['method', "(self, **kwargs) -> 'bool'"],
             '_v2_final_hook_payload': ['method',
                                        "(self, attempt_row, candidate_id: 'str', envelope_id: "
                                        "'str', notification_id: 'str', generation_id: 'str') -> "
                                        "'dict'"],
             '_v2_final_result_stage_payload': ['method',
                                                "(self, attempt_row, candidate_id: 'str', "
                                                "envelope_id: 'str', notification_id: 'str', "
                                                "generation_id: 'str') -> 'dict'"],
             '_v2_final_task_payload': ['method',
                                        "(self, attempt_row, candidate_id: 'str', envelope_id: "
                                        "'str', notification_id: 'str', generation_id: 'str') -> "
                                        "'dict'"],
             '_v2_finalization_reason_for': ['method', "(self, code: 'str | None') -> 'str'"],
             '_v2_housekeeping_target_from_row': ['method',
                                                  "(self, row, *, obligation_code: 'str | None') "
                                                  "-> 'AuthorityPolicyV2HousekeepingTarget'"],
             '_v2_identity_observation': ['method',
                                          "(payload, field: 'str', expected, kind: 'str') -> "
                                          "'str'"],
             '_v2_identity_scoped_audits': ['method',
                                            "(self, root_task_id: 'str', manager_agent: 'str', "
                                            "action: 'str', *, attempt_id: 'str | None' = None, "
                                            "include_opaque: 'bool' = False) -> 'list[dict] | "
                                            "None'"],
             '_v2_is_int': ['method', "(value) -> 'bool'"],
             '_v2_json_type_sensitive_equal': ['method', "(left, right) -> 'bool'"],
             '_v2_later_result_provenance_uncommitted': ['method',
                                                         "(self, *, root_task_id: 'str', row, "
                                                         "envelopes) -> 'bool'"],
             '_v2_ordinary_completion_payload': ['method',
                                                 "(self, *, root_task_id: 'str', manager_agent: "
                                                 "'str', result_row) -> 'dict | None'"],
             '_v2_parse_material_decision': ['method', '(raw)'],
             '_v2_parse_material_list': ['method', '(raw)'],
             '_v2_parse_material_local_ci': ['method', '(raw)'],
             '_v2_publication_audit_payload': ['method',
                                               "(self, *, stage: 'str', attempt_id: 'str', "
                                               "candidate_id: 'str', result_id: 'int', "
                                               "envelope_id: 'str', notification_id: 'str', "
                                               "generation_id: 'str', publication_attempt: 'int | "
                                               "None' = None, publisher_boot_id: 'str | None' = "
                                               "None) -> 'dict'"],
             '_v2_publication_event_identity_keys': ['method',
                                                     "(self, stage: 'str') -> 'set[str]'"],
             '_v2_receipt_blocks_ordinary': ['method',
                                             "(self, receipt, *, result_id: 'int', "
                                             "manager_session_id: 'str') -> 'bool'"],
             '_v2_receipt_identity_of': ['method', "(row) -> 'dict'"],
             '_v2_recovery_completion_rows': ['method',
                                              "(self, rows, *, result_id: 'int', "
                                              "manager_session_id: 'str') -> 'list[dict]'"],
             '_v2_refusal_completion_payload': ['method',
                                                "(self, *, attempt_row: 'dict', refusal_code: "
                                                "'str', recovery_session_id: 'str | None') -> "
                                                "'dict'"],
             '_v2_refusal_failure_authority': ['method',
                                               "(self, *, attempt_id: 'str', owner_attempt_id: "
                                               "'str | None') -> 'bool'"],
             '_v2_refusal_stage_payload': ['method',
                                           "(self, attempt_row: 'dict', candidate_id: 'str | "
                                           "None', refusal_code: 'str', finalization_state: 'str') "
                                           "-> 'dict'"],
             '_v2_related_decision_events_uncommitted': ['method',
                                                         "(self, *, root_task_id: 'str', "
                                                         "manager_agent: 'str', attempt_id: 'str', "
                                                         "candidate_id: 'str', result_id: 'int', "
                                                         "envelope_id: 'str', notification_id: "
                                                         "'str', generation_id: 'str', "
                                                         "next_session_id: 'str', "
                                                         "spending_result_id: 'int') -> 'dict[str, "
                                                         "list[dict]] | None'"],
             '_v2_related_publication_events_uncommitted': ['method',
                                                            "(self, *, root_task_id: 'str', "
                                                            "manager_agent: 'str', attempt_id: "
                                                            "'str', stage: 'str', candidate_id: "
                                                            "'str', result_id: 'int', envelope_id: "
                                                            "'str', notification_id: 'str', "
                                                            "generation_id: 'str') -> 'list[dict] "
                                                            "| None'"],
             '_v2_related_result_stage_events_absent_uncommitted': ['method',
                                                                    '(self, *, root_task_id: '
                                                                    "'str', manager_agent: 'str', "
                                                                    "attempt_id: 'str', stage: "
                                                                    "'str', candidate_id: 'str', "
                                                                    "result_id: 'int', "
                                                                    "envelope_id: 'str', "
                                                                    "notification_id: 'str', "
                                                                    "generation_id: 'str') -> "
                                                                    "'bool'"],
             '_v2_related_spend_events_uncommitted': ['method',
                                                      "(self, *, root_task_id: 'str', "
                                                      "manager_agent: 'str', attempt_id: 'str', "
                                                      "candidate_id: 'str', result_id: 'int', "
                                                      "envelope_id: 'str', notification_id: 'str', "
                                                      "generation_id: 'str', next_session_id: "
                                                      "'str', spending_result_id: 'int') -> "
                                                      "'list[dict] | None'"],
             '_v2_result_reference_state': ['method',
                                            "(payload, fields, result_id: 'int') -> 'str'"],
             '_v2_result_stage_shape_is_closed': ['method', "(stage, payload) -> 'bool'"],
             '_v2_root_lineage_live_uncommitted': ['method',
                                                   '(self, *, root_task_id, dispatch_row, '
                                                   "envelopes, attempts) -> 'bool'"],
             '_v2_session_reference_related': ['method',
                                               "(payload, fields, session_id: 'str') -> 'bool'"],
             '_v2_settled_rows': ['method',
                                  '(self, rows, *, attempt, candidate, envelope, notification) -> '
                                  "'list[dict]'"],
             '_v2_settlement_completion_payload': ['method',
                                                   "(self, *, root_task_id: 'str', manager_agent: "
                                                   "'str', result_row, result_id: 'int', "
                                                   "manager_session_id: 'str') -> 'dict'"],
             '_v2_settlement_settled_payload': ['method',
                                                '(self, *, attempt, candidate, envelope, '
                                                "notification) -> 'dict'"],
             '_v2_spend_event_absent_uncommitted': ['method', "(self, **kwargs) -> 'bool'"],
             '_v2_spend_event_identity_keys': ['method', "(self) -> 'set[str]'"],
             '_v2_spend_event_payload': ['method',
                                         "(self, *, attempt_id: 'str', candidate_id: 'str', "
                                         "result_id: 'int', envelope_id: 'str', notification_id: "
                                         "'str', generation_id: 'str', next_session_id: 'str', "
                                         "spending_result_id: 'int', report_digest: 'str') -> "
                                         "'dict'"],
             '_v2_spend_other_stage_shape_is_closed': ['method', "(stage, payload) -> 'bool'"],
             '_v2_spending_report_digest': ['method', "(row) -> 'str | None'"],
             '_v2_spending_report_identity': ['method', "(row) -> 'dict'"],
             '_v2_terminal_decision_receipt_authenticated_uncommitted': ['method',
                                                                         '(self, *, root_task_id: '
                                                                         "'str', envelope) -> "
                                                                         "'bool'"],
             '_validate_authority_activation_history': ['method',
                                                        '(self, receipt: '
                                                        "'AuthorityPolicyActivation') -> 'None'"],
             '_validate_authority_selector_team': ['method', "(team: 'str') -> 'str'"],
             '_worst_subtree_status': ['method',
                                       "(self, root_status: 'str', child_statuses: 'list[str]') -> "
                                       "'str'"],
             '_write_authority_policy_active_selector_uncommitted': ['method',
                                                                     '(self, selector: '
                                                                     "'AuthorityPolicySelector') "
                                                                     "-> 'None'"],
             '_write_authority_policy_v2_activation_uncommitted': ['method',
                                                                   '(self, activation: '
                                                                   "'AuthorityPolicyV2Activation') "
                                                                   "-> 'None'"],
             '_write_authority_policy_v2_release_uncommitted': ['method',
                                                                '(self, release: '
                                                                "'AuthorityPolicyV2Release', *, "
                                                                "created_at: 'str') -> 'None'"],
             '_zombie_marker_value': ['method', "(value) -> 'str | None'"],
             'acknowledge_authority_policy_v2_decision_dispatch': ['method',
                                                                   "(self, *, root_task_id: 'str', "
                                                                   "manager_agent: 'str', "
                                                                   "result_id: 'int') -> "
                                                                   "'AuthorityPolicyV2DecisionAckOutcome'"],
             'acknowledge_authority_policy_v2_notification_publication': ['method',
                                                                          '(self, *, root_task_id: '
                                                                          "'str', manager_agent: "
                                                                          "'str', "
                                                                          'manager_session_id: '
                                                                          "'str', result_id: "
                                                                          "'int', "
                                                                          'publication_attempt: '
                                                                          "'int', "
                                                                          'publisher_boot_id: '
                                                                          "'str') -> "
                                                                          "'AuthorityPolicyV2PublicationAckOutcome'"],
             'acquire_thread_reply_breaker_probe': ['method',
                                                    "(self, *, thread_id: 'str', agent_name: "
                                                    "'str', executor_key: 'str', lease_id: 'str', "
                                                    "now: 'datetime | None' = None) -> "
                                                    "'ThreadReplyBreakerEpisode | None'"],
             'activate_authority_policy': ['method',
                                           "(self, activation: 'AuthorityPolicyActivation') -> "
                                           "'AuthorityPolicyActivation'"],
             'activate_authority_policy_legacy': ['method',
                                                  '(self, request: '
                                                  "'AuthorityPolicyLegacyActivationRequest | "
                                                  "dict') -> "
                                                  "'AuthorityPolicyLegacyControlReceipt'"],
             'activate_authority_policy_v2': ['method',
                                              '(self, request: '
                                              "'AuthorityPolicyV2ActivationControlRequest | dict') "
                                              "-> 'AuthorityPolicyV2ControlReceipt'"],
             'activate_authority_policy_with_audit': ['method',
                                                      "(self, *, team: 'str', release_id: 'str', "
                                                      "expected_previous_epoch: 'int', action: "
                                                      "'str', request_id: 'str', request_digest: "
                                                      "'str') -> 'AuthorityPolicyActivation'"],
             'add_thread_participant': ['method',
                                        "(self, thread_id: 'str', agent_name: 'str', *, added_by: "
                                        "'str') -> 'bool'"],
             'admit_retry_feedback': ['method',
                                      "(self, pending: 'PendingRetry', enqueue) -> 'str | "
                                      "LostClaim'"],
             'admit_task_completion_callback': ['method',
                                                "(self, *, task_id: 'str', agent: 'str', "
                                                "session_id: 'str', output_summary: 'str', "
                                                "confidence_score: 'int', status: 'str' = "
                                                "'completed', risks_flagged: 'list[str] | None' = "
                                                "None, output_dir: 'str | None' = None, "
                                                "decision_json: 'str | None' = None, "
                                                "waiting_on_job_ids: 'list[str] | None' = None, "
                                                "verdict: 'str | None' = None, local_ci_json: 'str "
                                                "| None' = None, recovery_deadline_monotonic: "
                                                "'float | None' = None, v2_admission: 'dict | "
                                                "None' = None) -> 'bool'"],
             'aggregate_session_token_usage_by_agent': ['method',
                                                        "(self, since: 'str | None' = None, "
                                                        "task_id: 'str | None' = None, agent: 'str "
                                                        "| None' = None, scope_type: 'str | None' "
                                                        "= None, scope_id: 'str | None' = None, "
                                                        "thread_id: 'str | None' = None, purpose: "
                                                        "'str | None' = None) -> 'list[dict]'"],
             'aggregate_session_token_usage_by_failed_task': ['method',
                                                              "(self, since: 'str | None' = None, "
                                                              "agent: 'str | None' = None, "
                                                              "task_id: 'str | None' = None, "
                                                              "scope_type: 'str | None' = None, "
                                                              "scope_id: 'str | None' = None, "
                                                              "thread_id: 'str | None' = None, "
                                                              "purpose: 'str | None' = None) -> "
                                                              "'list[dict]'"],
             'aggregate_session_token_usage_by_model': ['method',
                                                        "(self, since: 'str | None' = None, "
                                                        "task_id: 'str | None' = None, agent: 'str "
                                                        "| None' = None, scope_type: 'str | None' "
                                                        "= None, scope_id: 'str | None' = None, "
                                                        "thread_id: 'str | None' = None, purpose: "
                                                        "'str | None' = None) -> 'list[dict]'"],
             'aggregate_session_token_usage_by_purpose': ['method',
                                                          "(self, since: 'str | None' = None, "
                                                          "task_id: 'str | None' = None, agent: "
                                                          "'str | None' = None, scope_type: 'str | "
                                                          "None' = None, scope_id: 'str | None' = "
                                                          "None, thread_id: 'str | None' = None, "
                                                          "purpose: 'str | None' = None) -> "
                                                          "'list[dict]'"],
             'aggregate_session_token_usage_by_scope': ['method',
                                                        "(self, since: 'str | None' = None, "
                                                        "task_id: 'str | None' = None, agent: 'str "
                                                        "| None' = None, scope_type: 'str | None' "
                                                        "= None, scope_id: 'str | None' = None, "
                                                        "thread_id: 'str | None' = None, purpose: "
                                                        "'str | None' = None) -> 'list[dict]'"],
             'aggregate_session_token_usage_by_task': ['method',
                                                       "(self, since: 'str | None' = None, agent: "
                                                       "'str | None' = None, task_id: 'str | None' "
                                                       "= None, scope_type: 'str | None' = None, "
                                                       "scope_id: 'str | None' = None, thread_id: "
                                                       "'str | None' = None, purpose: 'str | None' "
                                                       "= None) -> 'list[dict]'"],
             'aggregate_session_token_usage_by_thread': ['method',
                                                         "(self, since: 'str | None' = None, "
                                                         "task_id: 'str | None' = None, agent: "
                                                         "'str | None' = None, scope_type: 'str | "
                                                         "None' = None, scope_id: 'str | None' = "
                                                         "None, thread_id: 'str | None' = None, "
                                                         "purpose: 'str | None' = None) -> "
                                                         "'list[dict]'"],
             'append_thread_message': ['method',
                                       "(self, *, thread_id: 'str', speaker: 'str', kind: "
                                       "'ThreadMessageKind', body_markdown: 'str | None' = None, "
                                       "decline_reason: 'str | None' = None, system_payload: 'dict "
                                       "| None' = None, attachments: 'list[ThreadAttachment] | "
                                       "None' = None, sent_from_task_id: 'str | None' = None) -> "
                                       "'int'"],
             'archive_thread_and_reset_sessions': ['method',
                                                   "(self, thread_id: 'str', *, summary: 'str', "
                                                   "audit_scope_id: 'str', audit_agent: 'str') -> "
                                                   "'None'"],
             'audit_authority_policy_v2_candidate_claim': ['method',
                                                           "(self, *, root_task_id: 'str', "
                                                           "manager_agent: 'str', "
                                                           "manager_session_id: 'str', result_id: "
                                                           "'int', origin_boot_id: 'str', "
                                                           "owner_attempt_id: 'str', "
                                                           "max_revise_rounds: 'int' = 0) -> "
                                                           "'AuthorityPolicyV2StageOutcome'"],
             'audit_authority_policy_v2_candidate_consumption': ['method',
                                                                 "(self, *, root_task_id: 'str', "
                                                                 "manager_agent: 'str', "
                                                                 "manager_session_id: 'str', "
                                                                 "result_id: 'int', "
                                                                 "origin_boot_id: 'str', "
                                                                 "owner_attempt_id: 'str', "
                                                                 "max_revise_rounds: 'int' = 0) -> "
                                                                 "'AuthorityPolicyV2StageOutcome'"],
             'audit_authority_policy_v2_candidate_evaluation': ['method',
                                                                "(self, *, root_task_id: 'str', "
                                                                "manager_agent: 'str', "
                                                                "manager_session_id: 'str', "
                                                                "result_id: 'int', origin_boot_id: "
                                                                "'str', owner_attempt_id: 'str', "
                                                                "max_revise_rounds: 'int' = 0) -> "
                                                                "'AuthorityPolicyV2StageOutcome'"],
             'authority_policy_v2_completion_dispatch_context': ['method',
                                                                 "(self, *, root_task_id: 'str', "
                                                                 'result_row_id) -> '
                                                                 "'AuthorityPolicyV2CompletionDispatchContext'"],
             'authority_policy_v2_decision_result_report_binds': ['method',
                                                                  "(self, *, root_task_id: 'str', "
                                                                  'spending_result_id, report) -> '
                                                                  "'bool'"],
             'backstop_consumed_task_completion_recovery_jobs': ['method',
                                                                 "(self, *, task_id: 'str', agent: "
                                                                 "'str', result_row_id: 'int', "
                                                                 "finished_at: 'str') -> 'int'"],
             'backstop_terminated_task_jobs': ['method',
                                               "(self, task_id: 'str', *, finished_at: 'str') -> "
                                               "'int'"],
             'batch_get_direct_revisits': ['method',
                                           "(self, task_ids: 'list[str]') -> 'dict[str, "
                                           "list[str]]'"],
             'bind_authority_policy_legacy_session': ['method',
                                                      "(self, *, task_id: 'str', agent_name: "
                                                      "'str', session_id: 'str', legacy_payload: "
                                                      "'dict', selector_payload: 'dict | None') -> "
                                                      "'None'"],
             'bind_authority_policy_v2_permission_surface_reader': ['method',
                                                                    "(self, reader) -> 'None'"],
             'bind_authority_policy_v2_process_boot_id': ['method',
                                                          "(self, boot_id: 'str | None') -> "
                                                          "'None'"],
             'bind_authority_policy_v2_session': ['method',
                                                  '(self, binding: '
                                                  "'AuthorityPolicyV2SessionBinding | dict') -> "
                                                  "'AuthorityPolicyV2SessionBinding'"],
             'bump_thread_turn_cap': ['method',
                                      "(self, thread_id: 'str', *, delta: 'int' = 1) -> 'int'"],
             'cancel_zombie_without_fingerprint': ['method',
                                                   "(self, *, task_id: 'str', expected_agent: "
                                                   "'str', expected_session_id: 'str', "
                                                   'expected_zombie_flagged_at, cancelled_at: '
                                                   "'str') -> 'bool'"],
             'claim_authority_candidate': ['method',
                                           "(self, *, root_task_id: 'str', team: 'str', "
                                           "manager_agent: 'str', manager_session_id: 'str', "
                                           "causal_event_id: 'str', causal_event_digest: 'str', "
                                           "causal_result_id: 'str | None', policy_id: 'str', "
                                           "policy_version: 'str', policy_digest: 'str', "
                                           "prompt_id: 'str', prompt_version: 'str', "
                                           "prompt_digest: 'str', model_id: 'str', model_version: "
                                           "'str', model_digest: 'str', snapshot_digest: 'str', "
                                           "snapshot_retention_class: 'str' = 'digest_only', "
                                           "snapshot_redaction_class: 'str' = 'redacted', "
                                           "fence_results: 'dict | None' = None, _commit: 'bool' = "
                                           "True) -> 'tuple[str, bool]'"],
             'claim_authority_candidate_with_policy_pin': ['method',
                                                           "(self, *, release_id: 'str', "
                                                           "activation_id: 'str', "
                                                           "activation_epoch: 'int', provider_id: "
                                                           "'str', executor_kind: 'str', "
                                                           '**candidate_kwargs) -> '
                                                           "'tuple[AuthorityCandidate, "
                                                           "AuthorityCandidatePolicyPin]'"],
             'claim_authority_policy_v2_candidate': ['method',
                                                     "(self, *, root_task_id: 'str', "
                                                     "manager_agent: 'str', manager_session_id: "
                                                     "'str', result_id: 'int', origin_boot_id: "
                                                     "'str', owner_attempt_id: 'str', "
                                                     "max_revise_rounds: 'int' = 0) -> "
                                                     "'AuthorityPolicyV2StageOutcome'"],
             'claim_authority_policy_v2_decision_dispatch': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', result_id: "
                                                             "'int') -> "
                                                             "'AuthorityPolicyV2DecisionClaimOutcome'"],
             'claim_authority_policy_v2_notification_publication': ['method',
                                                                    '(self, *, root_task_id: '
                                                                    "'str', manager_agent: 'str', "
                                                                    "manager_session_id: 'str', "
                                                                    "result_id: 'int') -> "
                                                                    "'AuthorityPolicyV2PublicationClaimOutcome'"],
             'claim_conversational_reply': ['method',
                                            "(self, token: 'str', *, executor: 'str | None' = "
                                            "None, model: 'str | None' = None) -> "
                                            "'ThreadReplyClaim | None'"],
             'claim_task_completion_recovery': ['method',
                                                "(self, *, task_id: 'str', agent: 'str', "
                                                "origin_session_id: 'str', recovery_session_id: "
                                                "'str', provider_session_id: 'str', claimed_at: "
                                                "'str', expires_at: 'str') -> 'bool'"],
             'classify_authority_policy_v2_root_dispatch_for_enqueue': ['method',
                                                                        '(self, root_task_id: '
                                                                        "'str') -> "
                                                                        "'AuthorityPolicyV2EnqueueDispatchClassification'"],
             'close': ['method', "(self) -> 'None'"],
             'close_thread_reply_breaker': ['method',
                                            "(self, *, thread_id: 'str', agent_name: 'str', "
                                            "executor_key: 'str', now: 'datetime | None' = None) "
                                            "-> 'bool'"],
             'close_thread_reply_breakers_except': ['method',
                                                    "(self, *, thread_id: 'str', agent_name: "
                                                    "'str', executor_key: 'str', now: 'datetime | "
                                                    "None' = None) -> 'int'"],
             'coherent_read_view': ['method', '(self)'],
             'commit': ['method', "(self) -> 'None'"],
             'commit_authority_continue_same_root': ['method',
                                                     "(self, *, task_id: 'str', candidate_id: "
                                                     "'str', expected_manager_agent: 'str', "
                                                     "expected_session: 'str', expected_team: "
                                                     "'str', expected_policy_id: 'str', "
                                                     "expected_policy_version: 'str', "
                                                     "expected_policy_digest: 'str', "
                                                     "expected_prompt_id: 'str', "
                                                     "expected_prompt_version: 'str', "
                                                     "expected_prompt_digest: 'str', "
                                                     "expected_model_id: 'str', "
                                                     "expected_model_version: 'str', "
                                                     "expected_model_digest: 'str', "
                                                     "expected_input_digest: 'str', "
                                                     "expected_causal_event_id: 'str', "
                                                     "expected_max_revise_rounds: 'int', "
                                                     "expected_status: 'TaskStatus', "
                                                     "expected_block_kind: 'BlockKind | None', "
                                                     "note: 'str', audit_agent: 'str', "
                                                     "authority_continue_payload: 'dict', "
                                                     "hook_outcome_payload: 'dict', "
                                                     "envelope_clause_id: 'str', envelope_action: "
                                                     "'str', envelope_causal_event_digest: 'str') "
                                                     "-> 'bool'"],
             'complete_task_if_current_recovery_owner': ['method',
                                                         "(self, *, task_id: 'str', agent: 'str', "
                                                         "session_id: 'str', note: 'str', "
                                                         "output_dir: 'str | None', completed_at: "
                                                         "'str', result_row_id: 'int') -> 'bool'"],
             'completion_recovery_callback_allowed': ['method',
                                                      "(self, *, task_id: 'str', agent: 'str', "
                                                      "session_id: 'str', now: 'str') -> 'bool'"],
             'consume_accepted_blocked_task_completion_recovery': ['method',
                                                                   "(self, *, task_id: 'str', "
                                                                   "agent: 'str', session_id: "
                                                                   "'str', result_row_id: 'int', "
                                                                   'blocked_on_job_ids: '
                                                                   "'list[str]', note: 'str', "
                                                                   "completion_payload: 'dict', "
                                                                   "settled_at: 'str') -> 'bool'"],
             'consume_accepted_completed_task_completion_recovery': ['method',
                                                                     "(self, *, task_id: 'str', "
                                                                     "agent: 'str', session_id: "
                                                                     "'str', result_row_id: 'int', "
                                                                     "note: 'str', output_dir: "
                                                                     "'str | None', "
                                                                     "completion_payload: 'dict', "
                                                                     "settled_at: 'str', reviewer: "
                                                                     "'str | None', verdict: 'str "
                                                                     "| None') -> 'bool'"],
             'consume_accepted_nonroot_escalation_recovery': ['method',
                                                              "(self, *, task_id: 'str', agent: "
                                                              "'str', session_id: 'str', "
                                                              "result_row_id: 'int', note: 'str', "
                                                              "completion_payload: 'dict', "
                                                              "settled_at: 'str') -> 'bool'"],
             'consume_authority_candidate': ['method', "(self, candidate_id: 'str') -> 'bool'"],
             'consume_authority_continue_envelope': ['method',
                                                     "(self, *, envelope_id: 'str', root_task_id: "
                                                     "'str', decision_family: 'str', "
                                                     "expected_manager_agent: 'str', "
                                                     "expected_session_id: 'str', "
                                                     "expected_causal_event_id: 'str', "
                                                     "expected_causal_event_digest: 'str', "
                                                     "expected_policy_id: 'str', "
                                                     "expected_policy_version: 'str', "
                                                     "expected_policy_digest: 'str', "
                                                     "expected_clause_id: 'str', expected_action: "
                                                     "'str', audit_agent: 'str', error: 'str | "
                                                     "None' = None, violation: 'bool' = False) -> "
                                                     "'str'"],
             'consume_authority_policy_v2_candidate': ['method',
                                                       "(self, *, root_task_id: 'str', "
                                                       "manager_agent: 'str', manager_session_id: "
                                                       "'str', result_id: 'int', origin_boot_id: "
                                                       "'str', owner_attempt_id: 'str', "
                                                       "max_revise_rounds: 'int' = 0) -> "
                                                       "'AuthorityPolicyV2StageOutcome'"],
             'consume_escalation_notification': ['method',
                                                 "(self, feishu_message_id: 'str', consumed_by: "
                                                 "'str') -> 'bool'"],
             'consume_invocation': ['method', "(self, token: 'str') -> 'bool'"],
             'consume_v2_fingerprint_and_clear_zombie': ['method',
                                                         "(self, *, task_id: 'str', "
                                                         "expected_agent: 'str', "
                                                         "expected_session_id: 'str', result_id: "
                                                         "'int', expected_zombie_flagged_at) -> "
                                                         "'bool'"],
             'consumed_task_completion_recovery_owner_is_current': ['method',
                                                                    "(self, *, task_id: 'str', "
                                                                    "agent: 'str', "
                                                                    "recovery_session_id: 'str', "
                                                                    "result_row_id: 'int', "
                                                                    "terminal_status: 'str') -> "
                                                                    "'bool'"],
             'count_pending_turn_obligations': ['method', "(self, thread_id: 'str') -> 'int'"],
             'count_task_attachments': ['method', "(self, task_id: 'str') -> 'int'"],
             'create_and_activate_authority_policy_v2': ['method',
                                                         '(self, request: '
                                                         "'AuthorityPolicyV2PairedControlRequest | "
                                                         "dict') -> "
                                                         "'AuthorityPolicyV2ControlReceipt'"],
             'create_authority_policy_release': ['method',
                                                 "(self, release: 'AuthorityPolicyRelease') -> "
                                                 "'AuthorityPolicyRelease'"],
             'create_authority_policy_release_with_audit': ['method',
                                                            '(self, release: '
                                                            "'AuthorityPolicyRelease', *, "
                                                            "request_id: 'str', request_digest: "
                                                            "'str') -> 'AuthorityPolicyRelease'"],
             'cutover_thread_reply_delivery_state': ['method',
                                                     "(self, thread_id: 'str') -> "
                                                     "'list[ThreadReplyDeliveryState]'"],
             'decline_pending_invocations_for_agent': ['method',
                                                       "(self, thread_id: 'str', agent_name: "
                                                       "'str', *, decline_reason: 'str | None' = "
                                                       "None) -> 'int'"],
             'decline_unstarted_invocations_for_agent': ['method',
                                                         "(self, agent_name: 'str', *, "
                                                         "decline_reason: 'str') -> 'int'"],
             'delete_task_attachment': ['method',
                                        "(self, task_id: 'str', storage_key: 'str') -> 'bool'"],
             'delete_thread_scoped_attachment': ['method',
                                                 "(self, thread_id: 'str', attachment_id: 'str') "
                                                 "-> 'bool'"],
             'discard_reply_delivery': ['method',
                                        "(self, thread_id: 'str', *, agent_name: 'str | None' = "
                                        "None, decline_reason: 'str', status: "
                                        "'ThreadInvocationStatus' = "
                                        "<ThreadInvocationStatus.FAILED: 'failed'>) -> 'int'"],
             'dispatch_task_followup_replacement': ['method',
                                                    "(self, *, token: 'str', thread_id: 'str', "
                                                    "dispatcher: 'str', task: 'TaskRecord', team: "
                                                    "'str') -> 'dict'"],
             'ensure_authority_selector': ['method',
                                           "(self, team: 'str') -> 'AuthorityPolicySelector'"],
             'evaluate_authority_policy_v2_candidate': ['method',
                                                        "(self, *, root_task_id: 'str', "
                                                        "manager_agent: 'str', manager_session_id: "
                                                        "'str', result_id: 'int', origin_boot_id: "
                                                        "'str', owner_attempt_id: 'str', "
                                                        "max_revise_rounds: 'int' = 0) -> "
                                                        "'AuthorityPolicyV2StageOutcome'"],
             'execute': ['method', "(self, sql: 'str', parameters=())"],
             'fail_invocation': ['method',
                                 "(self, token: 'str', *, status: 'ThreadInvocationStatus', "
                                 "decline_reason: 'str') -> 'bool'"],
             'fetch_all_readonly': ['method',
                                    "(self, sql: 'str', params: 'tuple' = ()) -> "
                                    '"\'list[sqlite3.Row]\'"'],
             'fetch_one_readonly': ['method',
                                    "(self, sql: 'str', params: 'tuple' = ()) -> "
                                    '"\'sqlite3.Row | None\'"'],
             'finalize_authority_policy_v2_attempt_refusal': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', "
                                                              "manager_session_id: 'str', "
                                                              "result_id: 'int', refusal_code: "
                                                              "'str', owner_attempt_id: 'str | "
                                                              "None' = None, recovery_session_id: "
                                                              "'str | None' = None) -> "
                                                              "'AuthorityPolicyV2HousekeepingOutcome'"],
             'finalize_authority_policy_v2_continuation': ['method',
                                                           "(self, *, root_task_id: 'str', "
                                                           "manager_agent: 'str', "
                                                           "manager_session_id: 'str', result_id: "
                                                           "'int', origin_boot_id: 'str', "
                                                           "owner_attempt_id: 'str', "
                                                           "max_revise_rounds: 'int' = 0) -> "
                                                           "'AuthorityPolicyV2FinalizationOutcome'"],
             'get_accepted_task_completion_recovery_result': ['method',
                                                              "(self, *, task_id: 'str', agent: "
                                                              "'str') -> 'dict | None'"],
             'get_accepted_task_completion_recovery_task_ids': ['method', "(self) -> 'list[str]'"],
             'get_active_authority_continue_envelope': ['method', "(self, root_task_id: 'str')"],
             'get_agent_task_results': ['method',
                                        "(self, agent: 'str', since: 'str | None' = None) -> "
                                        "'list[dict]'"],
             'get_all_org_settings': ['method', "(self) -> 'dict[str, str]'"],
             'get_audit_logs': ['method', "(self, task_id: 'str') -> 'list[dict]'"],
             'get_audit_logs_by_action': ['method',
                                          "(self, action: 'str', since: 'str | None' = None) -> "
                                          "'list[dict]'"],
             'get_audit_logs_for_agent_since': ['method',
                                                "(self, agent: 'str', since: 'str', *, limit: "
                                                "'int' = 200) -> 'list[dict]'"],
             'get_authority_candidate': ['method',
                                         "(self, candidate_id: 'str') -> 'AuthorityCandidate | "
                                         "None'"],
             'get_authority_candidate_by_claim': ['method',
                                                  "(self, claim_key: 'str') -> 'AuthorityCandidate "
                                                  "| None'"],
             'get_authority_candidate_policy_pin': ['method',
                                                    "(self, candidate_id: 'str') -> "
                                                    "'AuthorityCandidatePolicyPin | None'"],
             'get_authority_continue_envelope': ['method', "(self, envelope_id: 'str')"],
             'get_authority_evaluation': ['method',
                                          "(self, candidate_id: 'str') -> 'AuthorityEvaluation | "
                                          "None'"],
             'get_authority_policy_activation': ['method',
                                                 "(self, activation_id: 'str') -> "
                                                 "'AuthorityPolicyActivation | None'"],
             'get_authority_policy_activation_for_release': ['method',
                                                             "(self, team: 'str', release_id: "
                                                             "'str') -> 'AuthorityPolicyActivation "
                                                             "| None'"],
             'get_authority_policy_history_snapshot': ['method',
                                                       "(self, team: 'str') -> 'tuple[int, int]'"],
             'get_authority_policy_outcomes_snapshot': ['method',
                                                        "(self, team: 'str') -> 'tuple[str, str] | "
                                                        "None'"],
             'get_authority_policy_release': ['method',
                                              "(self, release_id: 'str') -> "
                                              "'AuthorityPolicyRelease | None'"],
             'get_authority_policy_selector_by_id': ['method',
                                                     "(self, team: 'str', selector_id: 'str') -> "
                                                     "'AuthorityPolicySelector | None'"],
             'get_authority_policy_v2_activation': ['method',
                                                    "(self, activation_id: 'str') -> "
                                                    "'AuthorityPolicyV2Activation | None'"],
             'get_authority_policy_v2_attempt': ['method',
                                                 "(self, *, root_task_id: 'str', manager_agent: "
                                                 "'str', manager_session_id: 'str', result_id: "
                                                 "'int') -> 'AuthorityPolicyV2Attempt | None'"],
             'get_authority_policy_v2_attempt_for_result': ['method',
                                                            "(self, result_id: 'int') -> "
                                                            "'AuthorityPolicyV2Attempt | None'"],
             'get_authority_policy_v2_candidate': ['method',
                                                   "(self, candidate_id: 'str') -> "
                                                   "'AuthorityPolicyV2Candidate | None'"],
             'get_authority_policy_v2_candidate_audit': ['method',
                                                         "(self, candidate_id: 'str', event: "
                                                         "'str') -> 'dict | None'"],
             'get_authority_policy_v2_candidate_for_result': ['method',
                                                              "(self, result_id: 'int') -> "
                                                              "'AuthorityPolicyV2Candidate | "
                                                              "None'"],
             'get_authority_policy_v2_continue_envelope': ['method',
                                                           "(self, envelope_id: 'str') -> "
                                                           "'AuthorityPolicyV2ContinueEnvelope | "
                                                           "None'"],
             'get_authority_policy_v2_continue_envelope_for_candidate': ['method',
                                                                         '(self, candidate_id: '
                                                                         "'str') -> "
                                                                         "'AuthorityPolicyV2ContinueEnvelope "
                                                                         "| None'"],
             'get_authority_policy_v2_continue_envelope_for_root': ['method',
                                                                    "(self, root_task_id: 'str') "
                                                                    '-> '
                                                                    "'AuthorityPolicyV2ContinueEnvelope "
                                                                    "| None'"],
             'get_authority_policy_v2_decision_receipt_for_result': ['method',
                                                                     '(self, *, root_task_id: '
                                                                     "'str', spending_result_id) "
                                                                     "-> 'dict | None'"],
             'get_authority_policy_v2_evaluation': ['method',
                                                    "(self, candidate_id: 'str') -> "
                                                    "'AuthorityPolicyV2Evaluation | None'"],
             'get_authority_policy_v2_evaluation_for_result': ['method',
                                                               "(self, result_id: 'int') -> "
                                                               "'AuthorityPolicyV2Evaluation | "
                                                               "None'"],
             'get_authority_policy_v2_history_snapshot': ['method',
                                                          "(self, team: 'str') -> 'tuple[int, "
                                                          "int]'"],
             'get_authority_policy_v2_housekeeping_target': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "manager_session_id: 'str', "
                                                             "result_id: 'int') -> "
                                                             "'AuthorityPolicyV2HousekeepingTarget "
                                                             "| None'"],
             'get_authority_policy_v2_pin': ['method',
                                             "(self, candidate_id: 'str') -> 'AuthorityPolicyV2Pin "
                                             "| None'"],
             'get_authority_policy_v2_recovery_notification': ['method',
                                                               "(self, notification_id: 'str') -> "
                                                               "'AuthorityPolicyV2RecoveryNotification "
                                                               "| None'"],
             'get_authority_policy_v2_recovery_notification_for_envelope': ['method',
                                                                            '(self, envelope_id: '
                                                                            "'str') -> "
                                                                            "'AuthorityPolicyV2RecoveryNotification "
                                                                            "| None'"],
             'get_authority_policy_v2_release': ['method',
                                                 "(self, release_id: 'str') -> "
                                                 "'AuthorityPolicyV2Release | None'"],
             'get_authority_policy_v2_root_dispatch': ['method',
                                                       "(self, root_task_id: 'str') -> "
                                                       "'AuthorityPolicyV2RootDispatch | None'"],
             'get_authority_policy_v2_session_binding': ['method',
                                                         "(self, *, root_task_id: 'str', "
                                                         "manager_agent: 'str', "
                                                         "manager_session_id: 'str') -> "
                                                         "'AuthorityPolicyV2SessionBinding | "
                                                         "None'"],
             'get_authority_policy_v2_settlement_receipt_identity': ['method',
                                                                     '(self, *, root_task_id: '
                                                                     "'str', manager_agent: 'str', "
                                                                     "manager_session_id: 'str | "
                                                                     "None' = None, result_id: "
                                                                     "'int | None' = None) -> "
                                                                     "'dict | None'"],
             'get_authority_selector': ['method',
                                        "(self, team: 'str') -> 'AuthorityPolicySelector | None'"],
             'get_children': ['method', "(self, parent_task_id: 'str') -> 'list[str]'"],
             'get_claimed_task_completion_recovery': ['method',
                                                      "(self, *, task_id: 'str', agent: 'str') -> "
                                                      "'dict | None'"],
             'get_consumed_completed_task_completion_recovery_task_ids': ['method',
                                                                          "(self) -> 'list[str]'"],
             'get_consumed_nonroot_escalation_recovery_task_ids': ['method',
                                                                   "(self) -> 'list[str]'"],
             'get_consumed_task_completion_recovery_owners': ['method', "(self) -> 'list[dict]'"],
             'get_current_authority_policy_activation': ['method',
                                                         "(self, team: 'str') -> "
                                                         "'AuthorityPolicyActivation | None'"],
             'get_descendant_task_ids': ['method', "(self, root_task_id: 'str') -> 'list[str]'"],
             'get_direct_revisits': ['method', "(self, task_id: 'str') -> 'list[str]'"],
             'get_dream': ['method', "(self, dream_id: 'str') -> 'DreamRecord | None'"],
             'get_dream_for_agent_date': ['method',
                                          "(self, agent_name: 'str', local_date: 'str') -> "
                                          "'DreamRecord | None'"],
             'get_escalation_episode_audit_tail': ['method',
                                                   "(self, task_id: 'str', *, limit: 'int' = 256) "
                                                   "-> 'list[dict]'"],
             'get_escalation_notification': ['method',
                                             "(self, feishu_message_id: 'str') -> 'dict | None'"],
             'get_invocation_any_status': ['method',
                                           "(self, token: 'str') -> 'ThreadInvocation | None'"],
             'get_job': ['method', '(self, job_id: \'str\') -> "\'JobRecord | None\'"'],
             'get_job_owner_task_id': ['method', "(self, job_id: 'str') -> 'str | None'"],
             'get_job_status': ['method', "(self, job_id: 'str') -> 'str | None'"],
             'get_last_successful_dream': ['method',
                                           "(self, agent_name: 'str') -> 'DreamRecord | None'"],
             'get_latest_completion_report': ['method',
                                              "(self, task_id: 'str', agent: 'str | None' = None, "
                                              "session_id: 'str | None' = None)"],
             'get_latest_notification_for_sr': ['method',
                                                "(self, job_id: 'str', *, kind: 'str') -> 'dict | "
                                                "None'"],
             'get_latest_skill_materialization': ['method',
                                                  "(self, skill_id: 'str', agent: 'str') -> 'dict "
                                                  "| None'"],
             'get_latest_skill_validation': ['method',
                                             "(self, skill_id: 'str', version: 'str | None' = "
                                             "None) -> 'dict | None'"],
             'get_latest_task_result': ['method',
                                        "(self, task_id: 'str', agent: 'str', session_id: 'str') "
                                        "-> 'dict | None'"],
             'get_next_authority_policy_release_version': ['method',
                                                           "(self, team: 'str', policy_id: 'str') "
                                                           "-> 'int'"],
             'get_nonterminal_task_ids': ['method', "(self) -> 'list[str]'"],
             'get_org_setting': ['method', "(self, section: 'str') -> 'str | None'"],
             'get_pending_invocation': ['method',
                                        "(self, token: 'str') -> 'ThreadInvocation | None'"],
             'get_recall_payload': ['method', "(self, task_id: 'str') -> 'dict | None'"],
             'get_reply_delivery_state': ['method',
                                          "(self, thread_id: 'str', agent_name: 'str') -> "
                                          "'ThreadReplyDeliveryState | None'"],
             'get_running_job_task_ids': ['method', "(self) -> 'dict[str, str]'"],
             'get_subtree_statuses': ['method', "(self, root_task_id: 'str') -> 'list[str]'"],
             'get_task': ['method', "(self, task_id: 'str') -> 'TaskRecord | None'"],
             'get_task_attachment': ['method',
                                     "(self, task_id: 'str', storage_key: 'str') -> "
                                     "'TaskAttachmentRecord | None'"],
             'get_task_attachment_by_storage_key': ['method',
                                                    "(self, storage_key: 'str') -> "
                                                    "'TaskAttachmentRecord | None'"],
             'get_task_results': ['method', "(self, task_id: 'str') -> 'list[dict]'"],
             'get_thread': ['method', "(self, thread_id: 'str') -> 'ThreadRecord | None'"],
             'get_thread_max_message_seq': ['method', "(self, thread_id: 'str') -> 'int'"],
             'get_thread_message_by_seq': ['method',
                                           "(self, thread_id: 'str', seq: 'int') -> 'ThreadMessage "
                                           "| None'"],
             'get_thread_reply_breaker': ['method',
                                          "(self, thread_id: 'str', agent_name: 'str', "
                                          "executor_key: 'str') -> 'ThreadReplyBreakerEpisode | "
                                          "None'"],
             'get_thread_scoped_attachment': ['method',
                                              "(self, thread_id: 'str', attachment_id: 'str') -> "
                                              "'ThreadScopedAttachment | None'"],
             'get_thread_session': ['method',
                                    "(self, thread_id: 'str', agent_name: 'str') -> 'tuple[str | "
                                    "None, int]'"],
             'handoff_consumed_task_completion_recovery_parent_effect': ['method',
                                                                         '(self, *, task_id: '
                                                                         "'str', agent: 'str', "
                                                                         'recovery_session_id: '
                                                                         "'str', result_row_id: "
                                                                         "'int', terminal_status: "
                                                                         "'str', effect: "
                                                                         "'Callable[[], None]') -> "
                                                                         "'bool'"],
             'has_orchestration_step_audit': ['method',
                                              "(self, *, task_id: 'str', step_number: 'int') -> "
                                              "'bool'"],
             'has_task_completion_report_audit': ['method',
                                                  "(self, *, task_id: 'str', agent: 'str', "
                                                  "session_id: 'str', result_row_id: 'int') -> "
                                                  "'bool'"],
             'increment_revision_count': ['method', "(self, task_id: 'str') -> 'None'"],
             'increment_thread_turns_used': ['method',
                                             "(self, thread_id: 'str', *, by: 'int' = 1) -> "
                                             "'None'"],
             'insert_audit_log': ['method',
                                  "(self, task_id: 'str', agent: 'str', action: 'str', payload: "
                                  "'dict | None' = None) -> 'int'"],
             'insert_audit_log_uncommitted': ['method',
                                              "(self, task_id: 'str', agent: 'str', action: 'str', "
                                              "payload: 'dict | None' = None) -> 'int'"],
             'insert_cleanup_report_thread_and_task': ['method',
                                                       "(self, *, thread_id: 'str', subject: "
                                                       "'str', composer: 'str', opening_body: "
                                                       "'str', initial_recipients: 'list[str]', "
                                                       "turn_cap: 'int', task: 'TaskRecord') -> "
                                                       "'str'"],
             'insert_dream': ['method', "(self, dream: 'DreamRecord') -> 'None'"],
             'insert_dream_kb_candidate': ['method',
                                           "(self, candidate: 'DreamKbCandidate') -> 'None'"],
             'insert_job': ['method', '(self, r: "\'JobRecord\'") -> \'None\''],
             'insert_session_token_usage': ['method',
                                            "(self, task_id: 'str | None', agent: 'str', "
                                            "session_id: 'str', executor: 'str', token_usage: "
                                            "'TokenUsage', scope_type: 'str' = 'task', scope_id: "
                                            "'str | None' = None, thread_id: 'str | None' = None, "
                                            "invocation_purpose: 'str | None' = None) -> 'None'"],
             'insert_skill_validation_event': ['method',
                                               "(self, *, skill_id: 'str', slug: 'str', agent: "
                                               "'str | None' = None, source: 'str' = "
                                               "'user_authored', severity: 'str' = 'info', ok: "
                                               "'bool' = True, version: 'str | None' = None, "
                                               "findings: 'list[str] | None' = None, reason_codes: "
                                               "'list[str] | None' = None) -> 'int'"],
             'insert_task': ['method', "(self, task: 'TaskRecord') -> 'None'"],
             'insert_task_attachment': ['method',
                                        "(self, *, task_id: 'str', ordinal: 'int', storage_key: "
                                        "'str', display_name: 'str', size_bytes: 'int | None', "
                                        "content_type: 'str | None', uploaded_by: 'str') -> "
                                        "'None'"],
             'insert_task_result': ['method',
                                    "(self, task_id: 'str', agent: 'str', session_id: 'str', "
                                    "output_summary: 'str', confidence_score: 'int', status: 'str' "
                                    "= 'completed', risks_flagged: 'list[str] | None' = None, "
                                    "learnings: 'str | None' = None, duration_seconds: 'int | "
                                    "None' = None, token_count: 'int | None' = None, "
                                    "estimated_cost: 'float | None' = None, output_dir: 'str | "
                                    "None' = None, decision_json: 'str | None' = None, "
                                    "waiting_on_job_ids: 'list[str] | None' = None, verdict: 'str "
                                    "| None' = None, local_ci_json: 'str | None' = None) -> "
                                    "'None'"],
             'insert_task_with_attachments': ['method',
                                              '(self, task: "\'TaskRecord\'", attachments: '
                                              "'list[dict]', uploaded_by: 'str') -> 'None'"],
             'insert_thread': ['method', "(self, t: 'ThreadRecord') -> 'None'"],
             'insert_thread_scoped_attachment': ['method',
                                                 "(self, *, attachment_id: 'str', thread_id: "
                                                 "'str', display_name: 'str', size_bytes: 'int | "
                                                 "None', content_type: 'str | None', uploaded_by: "
                                                 "'str') -> 'None'"],
             'invalidate_authority_policy_v2_notification_generation': ['method',
                                                                        '(self, *, root_task_id: '
                                                                        "'str', manager_agent: "
                                                                        "'str', "
                                                                        'manager_session_id: '
                                                                        "'str', result_id: 'int') "
                                                                        '-> '
                                                                        "'AuthorityPolicyV2InvalidationOutcome'"],
             'invalidate_thread_session_evicted': ['method',
                                                   "(self, thread_id: 'str', agent_name: 'str', *, "
                                                   "stale_session_id: 'str', error: 'str', "
                                                   "executor: 'str' = 'claude') -> 'None'"],
             'is_thread_participant': ['method',
                                       "(self, thread_id: 'str', agent_name: 'str') -> 'bool'"],
             'kb_view_stats': ['method', "(self) -> 'list[dict]'"],
             'list_agent_tasks': ['method',
                                  "(self, agent: 'str', limit: 'int' = 50) -> 'list[TaskRecord]'"],
             'list_authority_audit': ['method',
                                      "(self, candidate_id: 'str') -> 'list[AuthorityAuditEvent]'"],
             'list_authority_candidates_for_root': ['method',
                                                    "(self, root_task_id: 'str') -> "
                                                    "'list[AuthorityCandidate]'"],
             'list_authority_policy_history': ['method',
                                               "(self, team: 'str', *, snapshot_version: 'int', "
                                               "snapshot_epoch: 'int', after_version: 'int | "
                                               "None', after_epoch: 'int | None', "
                                               "after_release_id: 'str | None', "
                                               "after_activation_id: 'str | None', limit: 'int') "
                                               "-> 'list[dict]'"],
             'list_authority_policy_outcomes': ['method',
                                                "(self, team: 'str', *, snapshot_created_at: "
                                                "'str', snapshot_id: 'str', after_created_at: 'str "
                                                "| None', after_id: 'str | None', limit: 'int') -> "
                                                "'list[dict]'"],
             'list_authority_policy_selector_history': ['method',
                                                        "(self, team: 'str') -> "
                                                        "'list[AuthorityPolicySelector]'"],
             'list_authority_policy_v2_candidate_audits': ['method',
                                                           "(self, candidate_id: 'str') -> "
                                                           "'list[dict]'"],
             'list_authority_policy_v2_control_audit': ['method',
                                                        "(self, team: 'str') -> 'list[dict]'"],
             'list_authority_policy_v2_evaluations': ['method',
                                                      "(self, *, root_task_id: 'str', "
                                                      "manager_agent: 'str') -> "
                                                      "'list[AuthorityPolicyV2Evaluation]'"],
             'list_authority_policy_v2_history': ['method',
                                                  "(self, team: 'str', *, snapshot_version: 'int', "
                                                  "snapshot_epoch: 'int', after_version: 'int | "
                                                  "None', after_release_id: 'str | None', limit: "
                                                  "'int') -> 'list[dict]'"],
             'list_authority_policy_v2_publication_targets': ['method',
                                                              '(self) -> '
                                                              "'list[AuthorityPolicyV2PublicationTarget]'"],
             'list_authority_policy_v2_result_stage_audits': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str') -> "
                                                              "'list[dict]'"],
             'list_authority_policy_v2_unfinalized_attempts': ['method',
                                                               '(self) -> '
                                                               "'list[AuthorityPolicyV2HousekeepingTarget]'"],
             'list_blocked_with_kind': ['method', "(self, kind) -> 'list[str]'"],
             'list_dream_ids_by_status': ['method', "(self, statuses: 'set[str]') -> 'list[str]'"],
             'list_dream_kb_candidates': ['method',
                                          "(self, *, dream_id: 'str | None' = None, agent: 'str | "
                                          "None' = None, candidate_id: 'int | None' = None) -> "
                                          "'list[DreamKbCandidate]'"],
             'list_dreams': ['method',
                             "(self, *, agent: 'str | None' = None, limit: 'int' = 50) -> "
                             "'list[DreamRecord]'"],
             'list_invocations_for_thread_grouped_by_seq': ['method',
                                                            "(self, thread_id: 'str') -> "
                                                            "'dict[int, list[dict[str, object]]]'"],
             'list_job_ids_by_status': ['method', "(self, statuses: 'set[str]') -> 'list[str]'"],
             'list_jobs_db': ['method',
                              "(self, *, status: 'str | list[str] | None' = None, agent: 'str | "
                              "None' = None, task_id: 'str | None' = None, review_required: 'bool "
                              "| None' = None, persistent: 'bool | None' = None, limit: 'int' = "
                              '50) -> "list[\'JobRecord\']"'],
             'list_open_notifications_for_task': ['method',
                                                  "(self, task_id: 'str') -> 'list[dict]'"],
             'list_open_thread_ids': ['method', "(self) -> 'list[str]'"],
             'list_pending_thread_invocations': ['method', "(self) -> 'list[ThreadInvocation]'"],
             'list_reply_delivery_projections': ['method',
                                                 "(self, thread_id: 'str') -> "
                                                 "'list[ReplyDeliveryProjection]'"],
             'list_reply_delivery_states': ['method', "(self) -> 'list[ThreadReplyDeliveryState]'"],
             'list_reply_exchange_projections': ['method',
                                                 "(self, thread_id: 'str') -> "
                                                 "'list[ThreadReplyExchangeProjection]'"],
             'list_roots': ['method',
                            "(self, limit: 'int' = 20, assigned_agent: 'str | None' = None, "
                            "before_task_id: 'str | None' = None, status: 'TaskStatus | str | "
                            "None' = None, block_kind: 'BlockKind | str | None' = None) -> "
                            "'list[TaskRecord]'"],
             'list_session_token_usage': ['method',
                                          "(self, task_id: 'str | None' = None, agent: 'str | "
                                          "None' = None, since: 'str | None' = None, limit: 'int | "
                                          "None' = None, scope_type: 'str | None' = None, "
                                          "scope_id: 'str | None' = None, thread_id: 'str | None' "
                                          "= None, purpose: 'str | None' = None) -> 'list[dict]'"],
             'list_skill_validation_events': ['method',
                                              "(self, *, skill_id: 'str | None' = None, agent: "
                                              "'str | None' = None, source: 'str | None' = None, "
                                              "since: 'str | None' = None, severity: 'str | None' "
                                              "= None, limit: 'int' = 100) -> 'list[dict]'"],
             'list_stale_pending_jobs': ['method', "(self, cutoff_iso: 'str') -> 'list[dict]'"],
             'list_started_invocations_for_agent': ['method',
                                                    "(self, agent_name: 'str') -> 'list[tuple[str, "
                                                    "str]]'"],
             'list_tables': ['method', "(self) -> 'list[str]'"],
             'list_task_attachments': ['method',
                                       "(self, task_id: 'str') -> 'list[TaskAttachmentRecord]'"],
             'list_tasks': ['method',
                            "(self, limit: 'int' = 20, assigned_agent: 'str | None' = None, "
                            "before_task_id: 'str | None' = None, status: 'TaskStatus | str | "
                            "None' = None, block_kind: 'BlockKind | str | None' = None, "
                            "blocked_on_job_id: 'str | None' = None) -> 'list[TaskRecord]'"],
             'list_tasks_blocked_on_jobs': ['method', "(self) -> 'list[str]'"],
             'list_tasks_by_brief_prefix': ['method',
                                            "(self, brief_prefix: 'str', *, assigned_agent: 'str | "
                                            "None' = None, limit: 'int' = 200) -> "
                                            "'list[TaskRecord]'"],
             'list_tasks_by_thread': ['method', "(self, thread_id: 'str') -> 'list[dict]'"],
             'list_thread_invocations': ['method',
                                         "(self, thread_id: 'str', *, status: "
                                         "'ThreadInvocationStatus | None' = None) -> "
                                         "'list[ThreadInvocation]'"],
             'list_thread_messages': ['method',
                                      "(self, thread_id: 'str', *, since_seq: 'int' = 0, limit: "
                                      "'int | None' = 1000) -> 'list[ThreadMessage]'"],
             'list_thread_participant_names_for_threads': ['method',
                                                           "(self, thread_ids: 'list[str]') -> "
                                                           "'dict[str, list[str]]'"],
             'list_thread_participants': ['method',
                                          "(self, thread_id: 'str') -> 'list[ThreadParticipant]'"],
             'list_thread_scoped_attachments': ['method',
                                                "(self, thread_id: 'str') -> "
                                                "'list[ThreadScopedAttachment]'"],
             'list_threads': ['method',
                              "(self, *, status: 'str | None' = None, limit: 'int' = 50) -> "
                              "'list[ThreadRecord]'"],
             'list_threads_page': ['method',
                                   "(self, *, org: 'str', status: 'str | None' = None, page_size: 'int' = 50, cursor: 'str | None' = None) -> 'dict'"],
             'list_threads_by_composed_from_task_id': ['method',
                                                       "(self, task_id: 'str') -> "
                                                       "'list[ThreadRecord]'"],
             'list_workspace_cleanup_activity': ['method',
                                                 "(self, agent: 'str', limit: 'int' = 5) -> "
                                                 "'list[dict]'"],
             'mark_invocation_declined': ['method',
                                          "(self, token: 'str', *, decline_reason: 'str | None' = "
                                          "None) -> 'bool'"],
             'mark_task_completion_recovery_callback_consumed': ['method',
                                                                 "(self, *, task_id: 'str', agent: "
                                                                 "'str', session_id: 'str', "
                                                                 "result_row_id: 'int', "
                                                                 "settled_at: 'str') -> 'bool'"],
             'mint_due_thread_reply_breaker_probes': ['method',
                                                      "(self, *, now: 'datetime | None' = None, "
                                                      "no_episode_executor_keys: 'dict[tuple[str, "
                                                      "str], str] | None' = None, "
                                                      "cooldown_seconds: 'int' = 900) -> "
                                                      "'list[ThreadReplyRecoveryEntry]'"],
             'mint_escalation_notification': ['method',
                                              "(self, feishu_message_id: 'str', org_slug: 'str', "
                                              "task_id: 'str', chat_id: 'str', expires_at: "
                                              "'datetime', kind: 'str' = 'escalation') -> 'None'"],
             'mint_followup_invocation_with_cap_extend': ['method',
                                                          "(self, thread_id: 'str', *, agent_name: "
                                                          "'str', triggering_seq: 'int', "
                                                          "cap_delta_if_over: 'int' = 1) -> "
                                                          '"\'tuple[ThreadInvocation, int | '
                                                          'None]\'"'],
             'mint_thread_invocation': ['method',
                                        "(self, *, thread_id: 'str', agent_name: 'str', "
                                        "triggering_seq: 'int', purpose: "
                                        "'ThreadInvocationPurpose') -> 'ThreadInvocation'"],
             'next_dream_id': ['method', "(self) -> 'str'"],
             'next_job_id': ['method', "(self) -> 'str'"],
             'next_task_attachment_id': ['method', "(self) -> 'str'"],
             'next_task_id': ['method', "(self) -> 'str'"],
             'next_thread_attachment_id': ['method', "(self) -> 'str'"],
             'next_thread_id': ['method', "(self) -> 'str'"],
             'path': ['property', "(self) -> 'Path'"],
             'publish_task_completion_recovery_binding': ['method',
                                                          "(self, *, task_id: 'str', agent: 'str', "
                                                          "origin_session_id: 'str', "
                                                          "recovery_session_id: 'str') -> 'bool'"],
             'query_audit_logs': ['method',
                                  "(self, task_id: 'str | None' = None, agent: 'str | None' = "
                                  "None, action: 'str | None' = None, since: 'str | None' = None, "
                                  "limit: 'int | None' = None, cursor: 'str | None' = None) -> "
                                  "'tuple[list[dict], str | None]'"],
             'query_usage_lifecycle_snapshot': ['method',
                                                "(self, *, start_utc: 'str', end_utc: 'str') -> "
                                                "'dict[str, list[dict]]'"],
             'reactivate_authority_policy_legacy': ['method',
                                                    '(self, request: '
                                                    "'AuthorityPolicyLegacyReactivationRequest | "
                                                    "dict') -> "
                                                    "'AuthorityPolicyLegacyControlReceipt'"],
             'reap_pending_invocations': ['method',
                                          "(self, thread_id: 'str', *, purposes: "
                                          "'list[ThreadInvocationPurpose] | None' = None, "
                                          "decline_reason: 'str') -> 'int'"],
             'reaper_sweep_reply_exchanges': ['method', "(self) -> 'list[ThreadReplyArrival]'"],
             'reconcile_accepted_recovery_continued_same_root': ['method',
                                                                 "(self, *, task_id: 'str', agent: "
                                                                 "'str', session_id: 'str', "
                                                                 "result_row_id: 'int', "
                                                                 "completion_payload: 'dict', "
                                                                 "settled_at: 'str') -> 'bool'"],
             'reconcile_reply_exchanges': ['method', "(self) -> 'list[ThreadReplyArrival]'"],
             'record_authority_audit': ['method',
                                        "(self, *, candidate_id: 'str', event_type: 'str', "
                                        "payload: 'dict | None' = None) -> 'int'"],
             'record_authority_evaluation': ['method',
                                             "(self, *, candidate_id: 'str', disposition: 'str', "
                                             "disposition_code: 'str', response_digest: 'str', "
                                             "response_retention_class: 'str' = 'digest_only', "
                                             "response_redaction_class: 'str' = 'redacted', "
                                             "fence_results: 'dict | None' = None) -> 'int'"],
             'record_authority_policy_v2_notification_publication_failure': ['method',
                                                                             '(self, *, '
                                                                             "root_task_id: 'str', "
                                                                             'manager_agent: '
                                                                             "'str', "
                                                                             'manager_session_id: '
                                                                             "'str', result_id: "
                                                                             "'int', "
                                                                             'publication_attempt: '
                                                                             "'int', "
                                                                             'publisher_boot_id: '
                                                                             "'str') -> "
                                                                             "'AuthorityPolicyV2PublicationFailureOutcome'"],
             'record_conversational_arrival': ['method',
                                               "(self, *, thread_id: 'str', speaker: 'str', kind: "
                                               "'ThreadMessageKind', body_markdown: 'str | None' = "
                                               "None, attachments: 'list[ThreadAttachment] | None' "
                                               "= None, sent_from_task_id: 'str | None' = None, "
                                               "recipients: 'list[str]') -> 'tuple[int, "
                                               "list[ThreadReplyArrival]]'"],
             'record_dispatch_on_invocation': ['method',
                                               "(self, token: 'str', *, task_id: 'str') -> 'bool'"],
             'record_kb_view': ['method', "(self, slug: 'str') -> 'None'"],
             'record_processed_event': ['method',
                                        "(self, org_slug: 'str', feishu_event_id: 'str', outcome: "
                                        "'str', reason: 'str | None') -> 'bool'"],
             'record_thread_reply_breaker_failure': ['method',
                                                     "(self, *, thread_id: 'str', agent_name: "
                                                     "'str', executor_key: 'str', "
                                                     "invocation_token: 'str', failure_category: "
                                                     "'str', threshold: 'int', cooldown_seconds: "
                                                     "'int', now: 'datetime | None' = None) -> "
                                                     "'ThreadReplyBreakerEpisode'"],
             'recover_orphaned_running_jobs': ['method',
                                               "(self, *, now_iso: 'str') -> 'list[str]'"],
             'recover_reply_delivery_state': ['method',
                                              "(self) -> 'list[ThreadReplyRecoveryEntry]'"],
             'refuse_authority_policy_v2_decision_dispatch': ['method',
                                                              "(self, *, root_task_id: 'str', "
                                                              "manager_agent: 'str', result_id: "
                                                              "'int') -> "
                                                              "'AuthorityPolicyV2DecisionRefusalOutcome'"],
             'remove_thread_participant': ['method',
                                           "(self, thread_id: 'str', agent_name: 'str') -> 'bool'"],
             'rename_thread_with_audit': ['method',
                                          "(self, thread_id: 'str', *, subject: 'str', actor: "
                                          "'str' = 'founder') -> 'bool'"],
             'reply_conversational': ['method',
                                      "(self, *, thread_id: 'str', speaker: 'str', body_markdown: "
                                      "'str | None', attachments: 'list[ThreadAttachment] | None', "
                                      "token: 'str', token_purpose: 'ThreadInvocationPurpose') -> "
                                      "'tuple[int, ThreadReplySettlement | None, "
                                      "list[ThreadReplyArrival]]'"],
             'reset_thread_session': ['method',
                                      "(self, thread_id: 'str', agent_name: 'str') -> 'None'"],
             'reset_thread_sessions_for_agent': ['method',
                                                 "(self, agent_name: 'str', *, audit_scope_id: "
                                                 "'str | None' = None, audit_agent: 'str | None' = "
                                                 "None, audit_reason: 'str | None' = None) -> "
                                                 "'int'"],
             'reset_thread_sessions_for_thread': ['method',
                                                  "(self, thread_id: 'str', *, audit_scope_id: "
                                                  "'str | None' = None, audit_agent: 'str | None' "
                                                  "= None, audit_reason: 'str | None' = None) -> "
                                                  "'int'"],
             'resolve_ancestor_attachments': ['method',
                                              "(self, task_id: 'str', max_hops: 'int' = 20) -> "
                                              "'list[TaskAttachmentRecord]'"],
             'rollback': ['method', "(self) -> 'None'"],
             'select_workspace_cleanup_reclamation_candidates': ['method',
                                                                 "(self, *, owner_task_id: 'str', "
                                                                 "agent: 'str', "
                                                                 'stale_orchestration_step_count: '
                                                                 "'int', claimed_next_step_count: "
                                                                 "'int', canonical_workspace: "
                                                                 "'Path', authoritative_workspace: "
                                                                 "'Path', admit_observation: "
                                                                 "'Callable[[str], bool] | None' = "
                                                                 'None) -> '
                                                                 "'WorkspaceCleanupReclamationSelection "
                                                                 "| None'"],
             'set_task_executor_pid_if_current': ['method',
                                                  "(self, *, task_id: 'str', agent: 'str', "
                                                  "session_id: 'str', pid: 'int') -> 'bool'"],
             'set_thread_pinned': ['method',
                                   "(self, thread_id: 'str', *, pinned: 'bool') -> 'None'"],
             'set_thread_pinned_uncommitted': ['method',
                                               "(self, thread_id: 'str', *, pinned: 'bool') -> "
                                               "'None'"],
             'set_thread_pinned_with_audit': ['method',
                                              "(self, thread_id: 'str', *, pinned: 'bool', actor: "
                                              "'str' = 'founder') -> 'bool'"],
             'set_thread_status': ['method',
                                   "(self, thread_id: 'str', *, status: 'ThreadStatus', summary: "
                                   "'str | None' = None) -> 'None'"],
             'set_thread_subject': ['method',
                                    "(self, thread_id: 'str', *, subject: 'str') -> 'None'"],
             'set_thread_subject_uncommitted': ['method',
                                                "(self, thread_id: 'str', *, subject: 'str') -> "
                                                "'None'"],
             'set_thread_transcript_path': ['method',
                                            "(self, thread_id: 'str', transcript_path: 'str') -> "
                                            "'None'"],
             'set_thread_turn_cap': ['method',
                                     "(self, thread_id: 'str', *, new_cap: 'int') -> 'None'"],
             'settle_authority_policy_v2_continuation_receipt': ['method',
                                                                 "(self, *, root_task_id: 'str', "
                                                                 "manager_agent: 'str', "
                                                                 "manager_session_id: 'str', "
                                                                 "result_id: 'int', "
                                                                 "recovery_session_id: 'str | "
                                                                 "None' = None, "
                                                                 "accepted_result_id: 'int | None' "
                                                                 '= None, '
                                                                 "accepted_result_session_id: 'str "
                                                                 "| None' = None) -> "
                                                                 "'AuthorityPolicyV2SettlementOutcome'"],
             'settle_consumed_task_completion_recovery_jobs': ['method',
                                                               "(self, *, task_id: 'str', agent: "
                                                               "'str', recovery_session_id: 'str', "
                                                               "result_row_id: 'int', "
                                                               "terminal_status: 'str', "
                                                               "finished_at: 'str') -> 'tuple[str, "
                                                               "...] | None'"],
             'settle_conversational_reply': ['method',
                                             "(self, *, token: 'str', outcome: 'str', "
                                             "decline_reason: 'str | None' = None) -> "
                                             "'ThreadReplySettlement | None'"],
             'settle_conversational_reply_with_breaker_failure': ['method',
                                                                  "(self, *, token: 'str', "
                                                                  "outcome: 'str', decline_reason: "
                                                                  "'str', thread_id: 'str', "
                                                                  "agent_name: 'str', "
                                                                  "executor_key: 'str', "
                                                                  "failure_category: 'str', "
                                                                  "threshold: 'int', "
                                                                  "cooldown_seconds: 'int', now: "
                                                                  "'datetime | None' = None) -> "
                                                                  "'tuple[ThreadReplySettlement | "
                                                                  'None, ThreadReplyBreakerEpisode '
                                                                  "| None]'"],
             'settle_conversational_reply_with_exchange': ['method',
                                                           "(self, *, token: 'str', outcome: "
                                                           "'str', decline_reason: 'str | None' = "
                                                           "None) -> 'tuple[ThreadReplySettlement "
                                                           "| None, list[ThreadReplyArrival]]'"],
             'settle_expired_task_completion_recovery': ['method',
                                                         "(self, *, task_id: 'str', agent: 'str', "
                                                         "session_id: 'str', settled_at: 'str') -> "
                                                         "'bool'"],
             'settle_interrupted_task_completion_recovery': ['method',
                                                             "(self, *, task_id: 'str', agent: "
                                                             "'str', settled_at: 'str', note: "
                                                             "'str') -> 'bool'"],
             'settle_thread_reply_breaker_success': ['method',
                                                     "(self, *, thread_id: 'str', agent_name: "
                                                     "'str', executor_key: 'str', "
                                                     "invocation_token: 'str', episode_id: 'str', "
                                                     "probe_lease_id: 'str | None' = None, now: "
                                                     "'datetime | None' = None) -> 'bool'"],
             'settle_v2_continuation_generation_admission': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "manager_session_id: 'str', "
                                                             "result_id: 'int', generation_id: "
                                                             "'str | None', next_session_id: "
                                                             "'str') -> "
                                                             "'AuthorityPolicyV2AdmissionSettlementOutcome'"],
             'spend_authority_continue_envelope_if_active': ['method',
                                                             "(self, root_task_id: 'str', *, "
                                                             "audit_agent: 'str', error: 'str') -> "
                                                             "'bool'"],
             'spend_authority_policy_v2_continue_envelope': ['method',
                                                             "(self, *, root_task_id: 'str', "
                                                             "manager_agent: 'str', "
                                                             "manager_session_id: 'str', "
                                                             "result_id: 'int', generation_id: "
                                                             "'str | None', next_session_id: 'str "
                                                             "| None', spending_result_id: 'int') "
                                                             "-> 'AuthorityPolicyV2SpendOutcome'"],
             'stamp_invocation_started': ['method',
                                          "(self, token: 'str', *, session_id: 'str | None', "
                                          "executor: 'str | None' = None, model: 'str | None' = "
                                          "None) -> 'None'"],
             'summarize_workspace_cleanup_marker_history': ['method',
                                                            "(self, brief_prefix: 'str', *, "
                                                            "assigned_agent: 'str', page_size: "
                                                            "'int' = 1000) -> "
                                                            "'WorkspaceCleanupMarkerHistorySummary'"],
             'task_completion_recovery_launch_allowed': ['method',
                                                         "(self, *, task_id: 'str', agent: 'str', "
                                                         "recovery_session_id: 'str') -> 'bool'"],
             'terminate_agent_cleanups': ['method',
                                          "(self, agent_name: 'str', *, audit_scope_id: 'str | "
                                          "None' = None, audit_agent: 'str | None' = None) -> "
                                          "'None'"],
             'transition_job_to_rejected': ['method',
                                            "(self, job_id: 'str', *, reviewer: 'str', reason: "
                                            "'str', reviewed_at: 'str') -> 'None'"],
             'transition_job_to_running': ['method',
                                           "(self, job_id: 'str', *, reviewer: 'str', reviewed_at: "
                                           "'str', started_at: 'str', cwd_resolved: 'str', "
                                           "max_runtime_seconds: 'int | None', stdout_path: 'str', "
                                           "stderr_path: 'str') -> 'None'"],
             'transition_job_to_terminal': ['method',
                                            '(self, job_id: \'str\', *, status: "\'JobStatus\'", '
                                            "exit_code: 'int | None', finished_at: 'str', "
                                            "duration_ms: 'int', stdout_head: 'str | None', "
                                            "stderr_head: 'str | None', reason: 'str | None' = "
                                            "None) -> 'None'"],
             'try_advance_chain': ['method',
                                   "(self, parent_id: 'str', active_chain_json: 'str', next_child: "
                                   '"\'TaskRecord\'", *, attachments: \'list[dict] | None\' = '
                                   "None, uploaded_by: 'str' = 'orchestrator') -> 'bool'"],
             'try_claim_for_step': ['method',
                                    "(self, task_id: 'str', expected_status: 'TaskStatus', "
                                    "expected_block_kind: 'BlockKind | None', new_count: 'int') -> "
                                    "'bool'"],
             'try_claim_v2_continuation_generation': ['method',
                                                      "(self, *, root_task_id: 'str', "
                                                      "manager_agent: 'str', manager_session_id: "
                                                      "'str', result_id: 'int', generation_id: "
                                                      "'str | None', next_session_id: 'str') -> "
                                                      "'AuthorityPolicyV2GenerationClaimOutcome'"],
             'try_delegate': ['method',
                              "(self, parent_id: 'str', child: 'TaskRecord', *, parent_note: "
                              "'str', attachments: 'list[dict] | None' = None, active_chain_json: "
                              "'str | None' = None, uploaded_by: 'str' = 'orchestrator', "
                              "expected_claim: 'RetryClaim', revision_delta: 'int' = 0, "
                              "revision_cap: 'int' = 0) -> 'SpawnOutcome'"],
             'try_delegate_many': ['method',
                                   "(self, parent_id: 'str', children: 'list', *, parent_note: "
                                   "'str', active_fanout_json: 'str | None' = None, "
                                   "children_attachments: 'list[list[dict] | None] | None' = None, "
                                   "carrier_chains: 'list[dict] | None' = None, uploaded_by: 'str' "
                                   "= 'orchestrator', expected_claim: 'RetryClaim') -> "
                                   "'SpawnOutcome'"],
             'try_escalate': ['method',
                              "(self, task_id: 'str', *, reason: 'str', recovery_owner: "
                              "'tuple[str, str] | None' = None, recovery_result_id: 'int | None' = "
                              "None, recovery_completion_payload: 'dict | None' = None, "
                              "recovery_settled_at: 'str | None' = None, recovery_fault_hook=None) "
                              "-> 'bool'"],
             'try_escalate_over_budget': ['method',
                                          "(self, task_id: 'str', *, expected_status: "
                                          "'TaskStatus', expected_block_kind: 'BlockKind | None', "
                                          "reason: 'str') -> 'bool'"],
             'try_escalate_runtime': ['method',
                                      "(self, task_id: 'str', *, reason: 'str', agent: 'str', "
                                      "reason_code: 'str', expected_status: 'TaskStatus | None' = "
                                      "None, expected_block_kind: 'BlockKind | None' = None, "
                                      "match_expected_state: 'bool' = False, clear_active_fanout: "
                                      "'bool' = False) -> 'bool'"],
             'try_fail_nonroot_manager_supersede': ['method',
                                                    "(self, task_id: 'str', *, actor_agent: 'str', "
                                                    "actor_session_id: 'str', expected_team: "
                                                    "'str', note: 'str') -> 'bool'"],
             'try_fail_over_budget': ['method',
                                      "(self, task_id: 'str', *, expected_status: 'TaskStatus', "
                                      "expected_block_kind: 'BlockKind | None', note: 'str') -> "
                                      "'bool'"],
             'try_manager_supersede': ['method',
                                       "(self, task_id: 'str', *, actor_agent: 'str', "
                                       "actor_session_id: 'str', expected_team: 'str', "
                                       "successor_brief: 'str', rationale: 'str', attestation: "
                                       "'dict[str, object]') -> 'str | None'"],
             'try_reject_thread_origin_manager_supersede': ['method',
                                                            "(self, task_id: 'str', *, "
                                                            "actor_agent: 'str', actor_session_id: "
                                                            "'str', expected_team: 'str', reason: "
                                                            "'str', reason_code: 'str') -> 'bool'"],
             'try_retry_feedback': ['method',
                                    "(self, expected_claim: 'RetryClaim', feedback: 'str') -> "
                                    "'PendingRetry | LostClaim'"],
             'update_dream': ['method', "(self, dream_id: 'str', **fields: 'object') -> 'None'"],
             'update_dream_kb_candidate': ['method',
                                           "(self, candidate_id: 'int', *, status: 'str', "
                                           "promoted_kb_slug: 'str | None' = None) -> 'None'"],
             'update_dream_status_if': ['method',
                                        "(self, dream_id: 'str', expected_status: 'DreamStatus', "
                                        "new_status: 'DreamStatus', **fields: 'object') -> 'bool'"],
             'update_processed_event_outcome': ['method',
                                                "(self, org_slug: 'str', feishu_event_id: 'str', "
                                                "outcome: 'str', reason: 'str | None' = None) -> "
                                                "'None'"],
             'update_task': ['method', "(self, task_id: 'str', **fields: 'object') -> 'None'"],
             'update_task_active_chain': ['method',
                                          "(self, task_id: 'str', active_chain: 'str | None') -> "
                                          "'None'"],
             'update_task_active_fanout': ['method',
                                           "(self, task_id: 'str', active_fanout: 'str | None') -> "
                                           "'None'"],
             'update_thread_session': ['method',
                                       "(self, thread_id: 'str', agent_name: 'str', *, "
                                       "agent_session_id: 'str | None', last_resumed_seq: 'int') "
                                       "-> 'None'"],
             'upsert_org_setting': ['method',
                                    "(self, section: 'str', value_json: 'str', *, before: 'dict | "
                                    "None' = None, after: 'dict | None' = None, actor: 'str' = "
                                    "'founder') -> 'None'"],
             'verify_retry_link': ['method',
                                   "(self, parent_id: 'str', target_agent: 'str', failed_id: 'str "
                                   "| None') -> 'VerifiedRetry | InvalidLineage'"],
             'walk_ancestors': ['method',
                                "(self, task_id: 'str', max_hops: 'int' = 20) -> "
                                "'list[TaskRecord]'"],
             'walk_revisit_chain': ['method',
                                    "(self, task_id: 'str', max_hops: 'int' = 20, truncate: 'bool' "
                                    "= False) -> 'list[TaskRecord]'"],
             'workflow_schema_transaction': ['method', '(self)']},
 'mro': [['runtime.infrastructure.database', 'Database'],
         ['runtime.infrastructure.db.tasks', 'TasksMixin'],
         ['runtime.infrastructure.db.dreams', 'DreamsMixin'],
         ['runtime.infrastructure.db.knowledge', 'KnowledgeMixin'],
         ['runtime.infrastructure.db.jobs', 'JobsMixin'],
         ['runtime.infrastructure.db.attachments', 'AttachmentsMixin'],
         ['runtime.infrastructure.db.audit', 'AuditMixin'],
         ['runtime.infrastructure.db.sessions', 'SessionsMixin'],
         ['runtime.infrastructure.db.workspace_cleanup', 'WorkspaceCleanupMixin'],
         ['runtime.infrastructure.db.threads', 'ThreadsMixin'],
         ['runtime.infrastructure.db.reply_delivery', 'ReplyDeliveryMixin'],
         ['runtime.infrastructure.db.reply_exchange', 'ReplyExchangeMixin'],
         ['runtime.infrastructure.db.schema', 'SchemaMixin'],
         ['runtime.infrastructure.db.authority_v1', 'AuthorityV1Mixin'],
         ['runtime.infrastructure.db.authority_policy', 'AuthorityPolicyMixin'],
         ['runtime.infrastructure.db.authority_v2_attempts', 'AuthorityV2AttemptsMixin'],
         ['runtime.infrastructure.db.authority_v2_continuation', 'AuthorityV2ContinuationMixin'],
         ['builtins', 'object']],
 'owners': {'append_memory_collection_transition': [['runtime.infrastructure.database', 'Database']],
            'read_memory_collection_evidence': [['runtime.infrastructure.database', 'Database']],
            '__class__': [['builtins', 'object']],
            '__delattr__': [['builtins', 'object']],
            '__dir__': [['builtins', 'object']],
            '__eq__': [['builtins', 'object']],
            '__format__': [['builtins', 'object']],
            '__ge__': [['builtins', 'object']],
            '__getattribute__': [['builtins', 'object']],
            '__getstate__': [['builtins', 'object']],
            '__gt__': [['builtins', 'object']],
            '__hash__': [['builtins', 'object']],
            '__init__': [['runtime.infrastructure.database', 'Database'], ['builtins', 'object']],
            '__init_subclass__': [['builtins', 'object']],
            '__le__': [['builtins', 'object']],
            '__lt__': [['builtins', 'object']],
            '__ne__': [['builtins', 'object']],
            '__new__': [['builtins', 'object']],
            '__reduce__': [['builtins', 'object']],
            '__reduce_ex__': [['builtins', 'object']],
            '__repr__': [['builtins', 'object']],
            '__setattr__': [['builtins', 'object']],
            '__sizeof__': [['builtins', 'object']],
            '__str__': [['builtins', 'object']],
            '__subclasshook__': [['builtins', 'object']],
            '_ack_v2_notification_publication_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_acknowledge_v2_decision_dispatch_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                               'AuthorityV2ContinuationMixin']],
            '_add_thread_participant_uncommitted': [['runtime.infrastructure.database',
                                                     'Database']],
            '_advance_v2_attempt_stage_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                       'AuthorityV2AttemptsMixin']],
            '_advance_v2_candidate_lifecycle_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                             'AuthorityV2AttemptsMixin']],
            '_append_authority_policy_activation_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                 'AuthorityPolicyMixin']],
            '_append_thread_message_uncommitted': [['runtime.infrastructure.database', 'Database']],
            '_apply_arrival_uncommitted': [['runtime.infrastructure.db.reply_delivery',
                                            'ReplyDeliveryMixin']],
            '_attachments_for_messages': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            '_audit_authority_policy_v2_candidate_claim_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                        'AuthorityV2AttemptsMixin']],
            '_audit_consumption_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                'AuthorityV2AttemptsMixin']],
            '_audit_evaluation_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                               'AuthorityV2AttemptsMixin']],
            '_authenticate_authority_legacy_control_receipt': [['runtime.infrastructure.db.authority_policy',
                                                                'AuthorityPolicyMixin']],
            '_authenticate_authority_selector_control_audit': [['runtime.infrastructure.db.authority_policy',
                                                                'AuthorityPolicyMixin']],
            '_authenticate_authority_v2_control_receipt': [['runtime.infrastructure.db.authority_policy',
                                                            'AuthorityPolicyMixin']],
            '_authenticate_exact_v2_zombie_binding_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                   'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_admission_event_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_admission_ready_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_attempt_admission_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                      'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_attempt_admission_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_attempt_stage_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                  'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_candidate_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                              'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_candidate_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_candidate_pin_joins_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                  'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_claim_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                             'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_decision_event_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_evaluation_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                  'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_final_hook_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                               'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_final_result_stage_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                 'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_final_rows_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                         'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_final_task_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                               'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_obligation_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                         'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_ordinary_completion_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                           'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_post_final_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                  'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_post_final_task_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_prior_result_stages_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                  'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_publication_event_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_publication_final_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_publication_settlement_proof_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                           'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_publication_stage_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                         'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_refusal_completion_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_refusal_escalation_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_refusal_result_stage_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                   'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_result_body_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                          'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_result_stage_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_authenticate_v2_retained_claim_boot_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                  'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_retained_publication_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                            'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_session_binding_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                              'AuthorityPolicyMixin']],
            '_authenticate_v2_settlement_evidence_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                  'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_settlement_final_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                               'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_settlement_pre_state_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                   'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_spend_event_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                          'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_spending_result_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_spent_decision_receipt_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                     'AuthorityV2ContinuationMixin']],
            '_authenticate_v2_zombie_consumption_receipt_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                         'AuthorityV2ContinuationMixin']],
            '_authority_begin': [['runtime.infrastructure.db.authority_policy',
                                  'AuthorityPolicyMixin']],
            '_authority_candidate_from_row': [['runtime.infrastructure.db.authority_v1',
                                               'AuthorityV1Mixin']],
            '_authority_commit': [['runtime.infrastructure.db.authority_policy',
                                   'AuthorityPolicyMixin']],
            '_authority_policy_activation_from_row': [['runtime.infrastructure.db.authority_policy',
                                                       'AuthorityPolicyMixin']],
            '_authority_policy_legacy_receipt_from_audit_row': [['runtime.infrastructure.db.authority_policy',
                                                                 'AuthorityPolicyMixin']],
            '_authority_policy_release_from_row': [['runtime.infrastructure.db.authority_policy',
                                                    'AuthorityPolicyMixin']],
            '_authority_policy_selector_from_row': [['runtime.infrastructure.db.authority_policy',
                                                     'AuthorityPolicyMixin']],
            '_authority_policy_v2_activation_from_row': [['runtime.infrastructure.db.authority_policy',
                                                          'AuthorityPolicyMixin']],
            '_authority_policy_v2_attempt_from_row': [['runtime.infrastructure.db.authority_v2_attempts',
                                                       'AuthorityV2AttemptsMixin']],
            '_authority_policy_v2_attempt_id_for_identity': [['runtime.infrastructure.db.authority_v2_attempts',
                                                              'AuthorityV2AttemptsMixin']],
            '_authority_policy_v2_candidate_from_row': [['runtime.infrastructure.db.authority_v2_attempts',
                                                         'AuthorityV2AttemptsMixin']],
            '_authority_policy_v2_envelope_from_row': [['runtime.infrastructure.db.authority_v2_continuation',
                                                        'AuthorityV2ContinuationMixin']],
            '_authority_policy_v2_evaluation_from_row': [['runtime.infrastructure.db.authority_v2_attempts',
                                                          'AuthorityV2AttemptsMixin']],
            '_authority_policy_v2_notification_from_row': [['runtime.infrastructure.db.authority_v2_continuation',
                                                            'AuthorityV2ContinuationMixin']],
            '_authority_policy_v2_pin_from_row': [['runtime.infrastructure.db.authority_v2_attempts',
                                                   'AuthorityV2AttemptsMixin']],
            '_authority_policy_v2_receipt_from_audit_row': [['runtime.infrastructure.db.authority_policy',
                                                             'AuthorityPolicyMixin']],
            '_authority_policy_v2_release_from_row': [['runtime.infrastructure.db.authority_policy',
                                                       'AuthorityPolicyMixin']],
            '_authority_policy_v2_root_dispatch_from_row': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            '_authority_policy_v2_session_binding_from_row': [['runtime.infrastructure.db.authority_policy',
                                                               'AuthorityPolicyMixin']],
            '_authority_rollback': [['runtime.infrastructure.db.authority_policy',
                                     'AuthorityPolicyMixin']],
            '_authority_write_transaction': [['runtime.infrastructure.db.authority_policy',
                                              'AuthorityPolicyMixin']],
            '_backfill_revisit_of_task_id': [['runtime.infrastructure.db.schema', 'SchemaMixin']],
            '_claim_authority_policy_v2_candidate_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                  'AuthorityV2AttemptsMixin']],
            '_claim_v2_decision_dispatch_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                         'AuthorityV2ContinuationMixin']],
            '_claim_v2_notification_publication_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                'AuthorityV2ContinuationMixin']],
            '_clear_catchup_pending_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                    'ReplyExchangeMixin']],
            '_clear_v2_refusal_failure_authority': [['runtime.infrastructure.db.authority_v2_attempts',
                                                     'AuthorityV2AttemptsMixin']],
            '_close_reply_exchange_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                   'ReplyExchangeMixin']],
            '_consume_authority_policy_v2_candidate_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                    'AuthorityV2AttemptsMixin']],
            '_consume_v2_continue_envelope_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                           'AuthorityV2ContinuationMixin']],
            '_create_authority_tables': [['runtime.infrastructure.db.schema', 'SchemaMixin']],
            '_create_tables': [['runtime.infrastructure.db.schema', 'SchemaMixin']],
            '_current_failed_contributions': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_current_severity_rollup': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_derive_conversational_mentions': [['runtime.infrastructure.db.reply_delivery',
                                                 'ReplyDeliveryMixin']],
            '_dream_candidate_row_to_model': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            '_dream_row_to_model': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            '_emit_reply_wake_audit': [['runtime.infrastructure.database', 'Database']],
            '_ensure_task_attachments_storage_key_unique': [['runtime.infrastructure.db.schema',
                                                             'SchemaMixin']],
            '_evaluate_authority_policy_v2_candidate_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                     'AuthorityV2AttemptsMixin']],
            '_evaluate_exchange_closure': [['runtime.infrastructure.db.reply_exchange',
                                            'ReplyExchangeMixin']],
            '_exchange_cohort_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                              'ReplyExchangeMixin']],
            '_exchange_has_live_cohort_wake': [['runtime.infrastructure.db.reply_exchange',
                                                'ReplyExchangeMixin']],
            '_extend_reply_exchange_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                    'ReplyExchangeMixin']],
            '_finalize_v2_continuation_replay_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_finalize_v2_continuation_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                       'AuthorityV2ContinuationMixin']],
            '_forget_v2_live_owner': [['runtime.infrastructure.db.authority_v2_attempts',
                                       'AuthorityV2AttemptsMixin']],
            '_get_authority_selector_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                     'AuthorityPolicyMixin']],
            '_get_open_exchange_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                'ReplyExchangeMixin']],
            '_get_subtree_tasks': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_has_live_manager_supersession_family_work_uncommitted': [['runtime.infrastructure.db.tasks',
                                                                        'TasksMixin']],
            '_increment_thread_turns_used_uncommitted': [['runtime.infrastructure.db.threads',
                                                          'ThreadsMixin']],
            '_insert_authority_policy_activation_audit_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                       'AuthorityPolicyMixin']],
            '_insert_authority_policy_selector_history_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                       'AuthorityPolicyMixin']],
            '_insert_authority_policy_v2_attempt_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_insert_authority_policy_v2_control_audit_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                       'AuthorityPolicyMixin']],
            '_insert_task_attachments_txn': [['runtime.infrastructure.database', 'Database']],
            '_insert_task_result': [['runtime.infrastructure.database', 'Database']],
            '_insert_task_uncommitted': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_insert_thread_uncommitted': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            '_insert_v2_candidate_and_pin_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                          'AuthorityV2AttemptsMixin']],
            '_insert_v2_candidate_audit_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                        'AuthorityV2AttemptsMixin']],
            '_insert_v2_continue_envelope_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                          'AuthorityV2ContinuationMixin']],
            '_insert_v2_recovery_notification_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_invalidate_v2_notification_generation_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                    'AuthorityV2ContinuationMixin']],
            '_legacy_session_binding_rows_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                          'AuthorityPolicyMixin']],
            '_load_authority_selector_history_chain': [['runtime.infrastructure.db.authority_policy',
                                                        'AuthorityPolicyMixin']],
            '_mark_catchup_pending_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                   'ReplyExchangeMixin']],
            '_mark_v2_refusal_failure_authority': [['runtime.infrastructure.db.authority_v2_attempts',
                                                    'AuthorityV2AttemptsMixin']],
            '_migrate_dark_authority_activation_seal_if_needed': [['runtime.infrastructure.db.schema',
                                                                   'SchemaMixin']],
            '_migrate_drop_talk_surface_if_needed': [['runtime.infrastructure.db.schema',
                                                      'SchemaMixin']],
            '_migrate_jobs_table_if_needed': [['runtime.infrastructure.db.schema', 'SchemaMixin']],
            '_migrate_remote_job_schema': [['runtime.infrastructure.db.schema', 'SchemaMixin']],
            '_migrate_session_token_usage_scope_columns': [['runtime.infrastructure.db.schema',
                                                            'SchemaMixin']],
            '_migrate_thread_invocation_attribution_columns': [['runtime.infrastructure.db.schema',
                                                                'SchemaMixin']],
            '_migrate_thread_invocation_reply_message_link': [['runtime.infrastructure.db.schema',
                                                               'SchemaMixin']],
            '_mint_reply_invocation_uncommitted': [['runtime.infrastructure.db.reply_delivery',
                                                    'ReplyDeliveryMixin']],
            '_open_reply_exchange_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                  'ReplyExchangeMixin']],
            '_pair_catchup_pending_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                   'ReplyExchangeMixin']],
            '_pair_held_by_open_exchange': [['runtime.infrastructure.db.reply_exchange',
                                             'ReplyExchangeMixin']],
            '_raise_required_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                             'ReplyExchangeMixin']],
            '_record_v2_failed_stage_obligation': [['runtime.infrastructure.db.authority_v2_attempts',
                                                    'AuthorityV2AttemptsMixin']],
            '_record_v2_notification_publication_failure_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                         'AuthorityV2ContinuationMixin']],
            '_refuse_v2_decision_dispatch_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                          'AuthorityV2ContinuationMixin']],
            '_refuse_v2_stage': [['runtime.infrastructure.db.authority_v2_attempts',
                                  'AuthorityV2AttemptsMixin']],
            '_require_authority_selector_cas': [['runtime.infrastructure.db.authority_policy',
                                                 'AuthorityPolicyMixin']],
            '_reset_thread_sessions_for_agent_uncommitted': [['runtime.infrastructure.db.sessions',
                                                              'SessionsMixin']],
            '_reset_thread_sessions_for_thread_uncommitted': [['runtime.infrastructure.db.sessions',
                                                               'SessionsMixin']],
            '_resolve_exchange_wake_set': [['runtime.infrastructure.db.reply_exchange',
                                            'ReplyExchangeMixin']],
            '_retained_v2_mechanical_eligibility_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            '_retire_skill_lifecycle_if_present': [['runtime.infrastructure.db.schema',
                                                    'SchemaMixin']],
            '_retire_v2_root_dispatch_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                      'AuthorityV2ContinuationMixin']],
            '_retrofit_authority_audit_fk_if_needed': [['runtime.infrastructure.db.schema',
                                                        'SchemaMixin']],
            '_retrofit_authority_lifecycle_trigger_if_needed': [['runtime.infrastructure.db.schema',
                                                                 'SchemaMixin']],
            '_retrofit_authority_policy_activation_trigger_if_needed': [['runtime.infrastructure.db.schema',
                                                                         'SchemaMixin']],
            '_retry_audits': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_claim_matches': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_dispatch_edge': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_escalation_edge': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_manager_edge': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_object': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_require_audit': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_retry_spawn_check': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_row_to_completion_report': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_row_to_invocation': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            '_row_to_job': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            '_row_to_reply_breaker_episode': [['runtime.infrastructure.db.reply_delivery',
                                               'ReplyDeliveryMixin']],
            '_row_to_reply_delivery_state': [['runtime.infrastructure.db.reply_delivery',
                                              'ReplyDeliveryMixin']],
            '_row_to_thread': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            '_running_recovery_fail_reason': [['runtime.infrastructure.db.reply_delivery',
                                               'ReplyDeliveryMixin']],
            '_selector_session_binding_rows_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                            'AuthorityPolicyMixin']],
            '_session_token_usage_filters': [['runtime.infrastructure.db.sessions',
                                              'SessionsMixin']],
            '_set_thread_status_archived_uncommitted': [['runtime.infrastructure.db.threads',
                                                         'ThreadsMixin']],
            '_set_v2_decision_state_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                    'AuthorityV2ContinuationMixin']],
            '_settle_reply_uncommitted': [['runtime.infrastructure.db.reply_delivery',
                                           'ReplyDeliveryMixin']],
            '_settle_v2_continuation_generation_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                'AuthorityV2ContinuationMixin']],
            '_settle_v2_exact_receipt_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                      'AuthorityV2AttemptsMixin']],
            '_spend_v2_continuation_envelope_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            '_suppress_open_exchanges_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                      'ReplyExchangeMixin']],
            '_sweep_corrupt_exchanges_uncommitted': [['runtime.infrastructure.db.reply_exchange',
                                                      'ReplyExchangeMixin']],
            '_thread_tail_seq': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            '_token_usage_rollup_select': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            '_try_claim_v2_continuation_generation_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                   'AuthorityV2ContinuationMixin']],
            '_update_v2_attempt_finalization_uncommitted': [['runtime.infrastructure.db.authority_v2_attempts',
                                                             'AuthorityV2AttemptsMixin']],
            '_upsert_v2_root_dispatch_pending_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            '_v2_admission_event_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                             'AuthorityV2ContinuationMixin']],
            '_v2_authenticate_publication_claim_history_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                        'AuthorityV2ContinuationMixin']],
            '_v2_authenticated_publication_events_by_attempt_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                             'AuthorityV2ContinuationMixin']],
            '_v2_candidate_from_claim_key': [['runtime.infrastructure.db.authority_v2_attempts',
                                              'AuthorityV2AttemptsMixin']],
            '_v2_claim_task_owner_current_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                          'AuthorityV2ContinuationMixin']],
            '_v2_closed_audit_matches': [['runtime.infrastructure.db.authority_v2_continuation',
                                          'AuthorityV2ContinuationMixin']],
            '_v2_completion_material_projection_from_report': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                'AuthorityV2ContinuationMixin']],
            '_v2_completion_material_projection_from_row': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            '_v2_completion_row_is_ordinary_related': [['runtime.infrastructure.db.authority_v2_continuation',
                                                        'AuthorityV2ContinuationMixin']],
            '_v2_completion_row_is_recovery_related': [['runtime.infrastructure.db.authority_v2_continuation',
                                                        'AuthorityV2ContinuationMixin']],
            '_v2_contender_is_authentic_owner': [['runtime.infrastructure.db.authority_v2_attempts',
                                                  'AuthorityV2AttemptsMixin']],
            '_v2_decision_event_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                            'AuthorityV2ContinuationMixin']],
            '_v2_decision_events_absent_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                        'AuthorityV2ContinuationMixin']],
            '_v2_final_hook_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                        'AuthorityV2ContinuationMixin']],
            '_v2_final_result_stage_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                                'AuthorityV2ContinuationMixin']],
            '_v2_final_task_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                        'AuthorityV2ContinuationMixin']],
            '_v2_finalization_reason_for': [['runtime.infrastructure.db.authority_v2_continuation',
                                             'AuthorityV2ContinuationMixin']],
            '_v2_housekeeping_target_from_row': [['runtime.infrastructure.db.authority_v2_attempts',
                                                  'AuthorityV2AttemptsMixin']],
            '_v2_identity_observation': [['runtime.infrastructure.db.authority_v2_continuation',
                                          'AuthorityV2ContinuationMixin']],
            '_v2_identity_scoped_audits': [['runtime.infrastructure.db.authority_v2_continuation',
                                            'AuthorityV2ContinuationMixin']],
            '_v2_is_int': [['runtime.infrastructure.db.authority_v2_continuation',
                            'AuthorityV2ContinuationMixin']],
            '_v2_json_type_sensitive_equal': [['runtime.infrastructure.db.authority_v2_continuation',
                                               'AuthorityV2ContinuationMixin']],
            '_v2_later_result_provenance_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                         'AuthorityV2ContinuationMixin']],
            '_v2_ordinary_completion_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                                 'AuthorityV2ContinuationMixin']],
            '_v2_parse_material_decision': [['runtime.infrastructure.db.authority_v2_continuation',
                                             'AuthorityV2ContinuationMixin']],
            '_v2_parse_material_list': [['runtime.infrastructure.db.authority_v2_continuation',
                                         'AuthorityV2ContinuationMixin']],
            '_v2_parse_material_local_ci': [['runtime.infrastructure.db.authority_v2_continuation',
                                             'AuthorityV2ContinuationMixin']],
            '_v2_publication_audit_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                               'AuthorityV2ContinuationMixin']],
            '_v2_publication_event_identity_keys': [['runtime.infrastructure.db.authority_v2_continuation',
                                                     'AuthorityV2ContinuationMixin']],
            '_v2_receipt_blocks_ordinary': [['runtime.infrastructure.db.authority_v2_continuation',
                                             'AuthorityV2ContinuationMixin']],
            '_v2_receipt_identity_of': [['runtime.infrastructure.db.authority_v2_continuation',
                                         'AuthorityV2ContinuationMixin']],
            '_v2_recovery_completion_rows': [['runtime.infrastructure.db.authority_v2_continuation',
                                              'AuthorityV2ContinuationMixin']],
            '_v2_refusal_completion_payload': [['runtime.infrastructure.db.authority_v2_attempts',
                                                'AuthorityV2AttemptsMixin']],
            '_v2_refusal_failure_authority': [['runtime.infrastructure.db.authority_v2_attempts',
                                               'AuthorityV2AttemptsMixin']],
            '_v2_refusal_stage_payload': [['runtime.infrastructure.db.authority_v2_attempts',
                                           'AuthorityV2AttemptsMixin']],
            '_v2_related_decision_events_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                         'AuthorityV2ContinuationMixin']],
            '_v2_related_publication_events_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                            'AuthorityV2ContinuationMixin']],
            '_v2_related_result_stage_events_absent_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                    'AuthorityV2ContinuationMixin']],
            '_v2_related_spend_events_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                      'AuthorityV2ContinuationMixin']],
            '_v2_result_reference_state': [['runtime.infrastructure.db.authority_v2_continuation',
                                            'AuthorityV2ContinuationMixin']],
            '_v2_result_stage_shape_is_closed': [['runtime.infrastructure.db.authority_v2_continuation',
                                                  'AuthorityV2ContinuationMixin']],
            '_v2_root_lineage_live_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                   'AuthorityV2ContinuationMixin']],
            '_v2_session_reference_related': [['runtime.infrastructure.db.authority_v2_continuation',
                                               'AuthorityV2ContinuationMixin']],
            '_v2_settled_rows': [['runtime.infrastructure.db.authority_v2_continuation',
                                  'AuthorityV2ContinuationMixin']],
            '_v2_settlement_completion_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                                   'AuthorityV2ContinuationMixin']],
            '_v2_settlement_settled_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                                'AuthorityV2ContinuationMixin']],
            '_v2_spend_event_absent_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                    'AuthorityV2ContinuationMixin']],
            '_v2_spend_event_identity_keys': [['runtime.infrastructure.db.authority_v2_continuation',
                                               'AuthorityV2ContinuationMixin']],
            '_v2_spend_event_payload': [['runtime.infrastructure.db.authority_v2_continuation',
                                         'AuthorityV2ContinuationMixin']],
            '_v2_spend_other_stage_shape_is_closed': [['runtime.infrastructure.db.authority_v2_continuation',
                                                       'AuthorityV2ContinuationMixin']],
            '_v2_spending_report_digest': [['runtime.infrastructure.db.authority_v2_continuation',
                                            'AuthorityV2ContinuationMixin']],
            '_v2_spending_report_identity': [['runtime.infrastructure.db.authority_v2_continuation',
                                              'AuthorityV2ContinuationMixin']],
            '_v2_terminal_decision_receipt_authenticated_uncommitted': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                         'AuthorityV2ContinuationMixin']],
            '_validate_authority_activation_history': [['runtime.infrastructure.db.authority_policy',
                                                        'AuthorityPolicyMixin']],
            '_validate_authority_selector_team': [['runtime.infrastructure.db.authority_policy',
                                                   'AuthorityPolicyMixin']],
            '_worst_subtree_status': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            '_write_authority_policy_active_selector_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                     'AuthorityPolicyMixin']],
            '_write_authority_policy_v2_activation_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                   'AuthorityPolicyMixin']],
            '_write_authority_policy_v2_release_uncommitted': [['runtime.infrastructure.db.authority_policy',
                                                                'AuthorityPolicyMixin']],
            '_zombie_marker_value': [['runtime.infrastructure.db.authority_v2_continuation',
                                      'AuthorityV2ContinuationMixin']],
            'acknowledge_authority_policy_v2_decision_dispatch': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                   'AuthorityV2ContinuationMixin']],
            'acknowledge_authority_policy_v2_notification_publication': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                          'AuthorityV2ContinuationMixin']],
            'acquire_thread_reply_breaker_probe': [['runtime.infrastructure.db.reply_delivery',
                                                    'ReplyDeliveryMixin']],
            'activate_authority_policy': [['runtime.infrastructure.db.authority_policy',
                                           'AuthorityPolicyMixin']],
            'activate_authority_policy_legacy': [['runtime.infrastructure.db.authority_policy',
                                                  'AuthorityPolicyMixin']],
            'activate_authority_policy_v2': [['runtime.infrastructure.db.authority_policy',
                                              'AuthorityPolicyMixin']],
            'activate_authority_policy_with_audit': [['runtime.infrastructure.db.authority_policy',
                                                      'AuthorityPolicyMixin']],
            'add_thread_participant': [['runtime.infrastructure.database', 'Database']],
            'admit_retry_feedback': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'admit_task_completion_callback': [['runtime.infrastructure.database', 'Database']],
            'aggregate_session_token_usage_by_agent': [['runtime.infrastructure.db.sessions',
                                                        'SessionsMixin']],
            'aggregate_session_token_usage_by_failed_task': [['runtime.infrastructure.db.sessions',
                                                              'SessionsMixin']],
            'aggregate_session_token_usage_by_model': [['runtime.infrastructure.db.sessions',
                                                        'SessionsMixin']],
            'aggregate_session_token_usage_by_purpose': [['runtime.infrastructure.db.sessions',
                                                          'SessionsMixin']],
            'aggregate_session_token_usage_by_scope': [['runtime.infrastructure.db.sessions',
                                                        'SessionsMixin']],
            'aggregate_session_token_usage_by_task': [['runtime.infrastructure.db.sessions',
                                                       'SessionsMixin']],
            'aggregate_session_token_usage_by_thread': [['runtime.infrastructure.db.sessions',
                                                         'SessionsMixin']],
            'append_thread_message': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'archive_thread_and_reset_sessions': [['runtime.infrastructure.db.threads',
                                                   'ThreadsMixin']],
            'audit_authority_policy_v2_candidate_claim': [['runtime.infrastructure.db.authority_v2_attempts',
                                                           'AuthorityV2AttemptsMixin']],
            'audit_authority_policy_v2_candidate_consumption': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                 'AuthorityV2AttemptsMixin']],
            'audit_authority_policy_v2_candidate_evaluation': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                'AuthorityV2AttemptsMixin']],
            'authority_policy_v2_completion_dispatch_context': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                 'AuthorityV2ContinuationMixin']],
            'authority_policy_v2_decision_result_report_binds': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                  'AuthorityV2ContinuationMixin']],
            'backstop_consumed_task_completion_recovery_jobs': [['runtime.infrastructure.db.jobs',
                                                                 'JobsMixin']],
            'backstop_terminated_task_jobs': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'batch_get_direct_revisits': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'bind_authority_policy_legacy_session': [['runtime.infrastructure.db.authority_policy',
                                                      'AuthorityPolicyMixin']],
            'bind_authority_policy_v2_permission_surface_reader': [['runtime.infrastructure.db.authority_v2_attempts',
                                                                    'AuthorityV2AttemptsMixin']],
            'bind_authority_policy_v2_process_boot_id': [['runtime.infrastructure.db.authority_v2_attempts',
                                                          'AuthorityV2AttemptsMixin']],
            'bind_authority_policy_v2_session': [['runtime.infrastructure.db.authority_policy',
                                                  'AuthorityPolicyMixin']],
            'bump_thread_turn_cap': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'cancel_zombie_without_fingerprint': [['runtime.infrastructure.db.authority_v2_continuation',
                                                   'AuthorityV2ContinuationMixin']],
            'claim_authority_candidate': [['runtime.infrastructure.db.authority_v1',
                                           'AuthorityV1Mixin']],
            'claim_authority_candidate_with_policy_pin': [['runtime.infrastructure.db.authority_v1',
                                                           'AuthorityV1Mixin']],
            'claim_authority_policy_v2_candidate': [['runtime.infrastructure.db.authority_v2_attempts',
                                                     'AuthorityV2AttemptsMixin']],
            'claim_authority_policy_v2_decision_dispatch': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            'claim_authority_policy_v2_notification_publication': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                    'AuthorityV2ContinuationMixin']],
            'claim_conversational_reply': [['runtime.infrastructure.db.reply_delivery',
                                            'ReplyDeliveryMixin']],
            'claim_task_completion_recovery': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'classify_authority_policy_v2_root_dispatch_for_enqueue': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                        'AuthorityV2ContinuationMixin']],
            'close': [['runtime.infrastructure.database', 'Database']],
            'close_thread_reply_breaker': [['runtime.infrastructure.db.reply_delivery',
                                            'ReplyDeliveryMixin']],
            'close_thread_reply_breakers_except': [['runtime.infrastructure.db.reply_delivery',
                                                    'ReplyDeliveryMixin']],
            'coherent_read_view': [['runtime.infrastructure.database', 'Database']],
            'commit': [['runtime.infrastructure.database', 'Database']],
            'commit_authority_continue_same_root': [['runtime.infrastructure.db.authority_v1',
                                                     'AuthorityV1Mixin']],
            'complete_task_if_current_recovery_owner': [['runtime.infrastructure.db.tasks',
                                                         'TasksMixin']],
            'completion_recovery_callback_allowed': [['runtime.infrastructure.db.tasks',
                                                      'TasksMixin']],
            'consume_accepted_blocked_task_completion_recovery': [['runtime.infrastructure.db.tasks',
                                                                   'TasksMixin']],
            'consume_accepted_completed_task_completion_recovery': [['runtime.infrastructure.db.tasks',
                                                                     'TasksMixin']],
            'consume_accepted_nonroot_escalation_recovery': [['runtime.infrastructure.db.tasks',
                                                              'TasksMixin']],
            'consume_authority_candidate': [['runtime.infrastructure.db.authority_v1',
                                             'AuthorityV1Mixin']],
            'consume_authority_continue_envelope': [['runtime.infrastructure.db.authority_v1',
                                                     'AuthorityV1Mixin']],
            'consume_authority_policy_v2_candidate': [['runtime.infrastructure.db.authority_v2_attempts',
                                                       'AuthorityV2AttemptsMixin']],
            'consume_escalation_notification': [['runtime.infrastructure.database', 'Database']],
            'consume_invocation': [['runtime.infrastructure.database', 'Database']],
            'consume_v2_fingerprint_and_clear_zombie': [['runtime.infrastructure.db.authority_v2_continuation',
                                                         'AuthorityV2ContinuationMixin']],
            'consumed_task_completion_recovery_owner_is_current': [['runtime.infrastructure.db.tasks',
                                                                    'TasksMixin']],
            'count_pending_turn_obligations': [['runtime.infrastructure.database', 'Database']],
            'count_task_attachments': [['runtime.infrastructure.db.attachments',
                                        'AttachmentsMixin']],
            'create_and_activate_authority_policy_v2': [['runtime.infrastructure.db.authority_policy',
                                                         'AuthorityPolicyMixin']],
            'create_authority_policy_release': [['runtime.infrastructure.db.authority_policy',
                                                 'AuthorityPolicyMixin']],
            'create_authority_policy_release_with_audit': [['runtime.infrastructure.db.authority_policy',
                                                            'AuthorityPolicyMixin']],
            'cutover_thread_reply_delivery_state': [['runtime.infrastructure.db.reply_delivery',
                                                     'ReplyDeliveryMixin']],
            'decline_pending_invocations_for_agent': [['runtime.infrastructure.database',
                                                       'Database']],
            'decline_unstarted_invocations_for_agent': [['runtime.infrastructure.database',
                                                         'Database']],
            'delete_task_attachment': [['runtime.infrastructure.db.attachments',
                                        'AttachmentsMixin']],
            'delete_thread_scoped_attachment': [['runtime.infrastructure.db.attachments',
                                                 'AttachmentsMixin']],
            'discard_reply_delivery': [['runtime.infrastructure.db.reply_delivery',
                                        'ReplyDeliveryMixin']],
            'dispatch_task_followup_replacement': [['runtime.infrastructure.db.tasks',
                                                    'TasksMixin']],
            'ensure_authority_selector': [['runtime.infrastructure.db.authority_policy',
                                           'AuthorityPolicyMixin']],
            'evaluate_authority_policy_v2_candidate': [['runtime.infrastructure.db.authority_v2_attempts',
                                                        'AuthorityV2AttemptsMixin']],
            'execute': [['runtime.infrastructure.database', 'Database']],
            'fail_invocation': [['runtime.infrastructure.database', 'Database']],
            'fetch_all_readonly': [['runtime.infrastructure.database', 'Database']],
            'fetch_one_readonly': [['runtime.infrastructure.database', 'Database']],
            'finalize_authority_policy_v2_attempt_refusal': [['runtime.infrastructure.db.authority_v2_attempts',
                                                              'AuthorityV2AttemptsMixin']],
            'finalize_authority_policy_v2_continuation': [['runtime.infrastructure.db.authority_v2_continuation',
                                                           'AuthorityV2ContinuationMixin']],
            'get_accepted_task_completion_recovery_result': [['runtime.infrastructure.db.tasks',
                                                              'TasksMixin']],
            'get_accepted_task_completion_recovery_task_ids': [['runtime.infrastructure.db.tasks',
                                                                'TasksMixin']],
            'get_active_authority_continue_envelope': [['runtime.infrastructure.db.authority_v1',
                                                        'AuthorityV1Mixin']],
            'get_agent_task_results': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_all_org_settings': [['runtime.infrastructure.db.knowledge', 'KnowledgeMixin']],
            'get_audit_logs': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'get_audit_logs_by_action': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'get_audit_logs_for_agent_since': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'get_authority_candidate': [['runtime.infrastructure.db.authority_v1',
                                         'AuthorityV1Mixin']],
            'get_authority_candidate_by_claim': [['runtime.infrastructure.db.authority_v1',
                                                  'AuthorityV1Mixin']],
            'get_authority_candidate_policy_pin': [['runtime.infrastructure.db.authority_v1',
                                                    'AuthorityV1Mixin']],
            'get_authority_continue_envelope': [['runtime.infrastructure.db.authority_v1',
                                                 'AuthorityV1Mixin']],
            'get_authority_evaluation': [['runtime.infrastructure.db.authority_v1',
                                          'AuthorityV1Mixin']],
            'get_authority_policy_activation': [['runtime.infrastructure.db.authority_policy',
                                                 'AuthorityPolicyMixin']],
            'get_authority_policy_activation_for_release': [['runtime.infrastructure.db.authority_policy',
                                                             'AuthorityPolicyMixin']],
            'get_authority_policy_history_snapshot': [['runtime.infrastructure.db.authority_policy',
                                                       'AuthorityPolicyMixin']],
            'get_authority_policy_outcomes_snapshot': [['runtime.infrastructure.db.authority_policy',
                                                        'AuthorityPolicyMixin']],
            'get_authority_policy_release': [['runtime.infrastructure.db.authority_policy',
                                              'AuthorityPolicyMixin']],
            'get_authority_policy_selector_by_id': [['runtime.infrastructure.db.authority_policy',
                                                     'AuthorityPolicyMixin']],
            'get_authority_policy_v2_activation': [['runtime.infrastructure.db.authority_policy',
                                                    'AuthorityPolicyMixin']],
            'get_authority_policy_v2_attempt': [['runtime.infrastructure.db.authority_v2_attempts',
                                                 'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_attempt_for_result': [['runtime.infrastructure.db.authority_v2_attempts',
                                                            'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_candidate': [['runtime.infrastructure.db.authority_v2_attempts',
                                                   'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_candidate_audit': [['runtime.infrastructure.db.authority_v2_attempts',
                                                         'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_candidate_for_result': [['runtime.infrastructure.db.authority_v2_attempts',
                                                              'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_continue_envelope': [['runtime.infrastructure.db.authority_v2_continuation',
                                                           'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_continue_envelope_for_candidate': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                         'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_continue_envelope_for_root': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                    'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_decision_receipt_for_result': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                     'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_evaluation': [['runtime.infrastructure.db.authority_v2_attempts',
                                                    'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_evaluation_for_result': [['runtime.infrastructure.db.authority_v2_attempts',
                                                               'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_history_snapshot': [['runtime.infrastructure.db.authority_policy',
                                                          'AuthorityPolicyMixin']],
            'get_authority_policy_v2_housekeeping_target': [['runtime.infrastructure.db.authority_v2_attempts',
                                                             'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_pin': [['runtime.infrastructure.db.authority_v2_attempts',
                                             'AuthorityV2AttemptsMixin']],
            'get_authority_policy_v2_recovery_notification': [['runtime.infrastructure.db.authority_v2_continuation',
                                                               'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_recovery_notification_for_envelope': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                            'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_release': [['runtime.infrastructure.db.authority_policy',
                                                 'AuthorityPolicyMixin']],
            'get_authority_policy_v2_root_dispatch': [['runtime.infrastructure.db.authority_v2_continuation',
                                                       'AuthorityV2ContinuationMixin']],
            'get_authority_policy_v2_session_binding': [['runtime.infrastructure.db.authority_policy',
                                                         'AuthorityPolicyMixin']],
            'get_authority_policy_v2_settlement_receipt_identity': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                     'AuthorityV2ContinuationMixin']],
            'get_authority_selector': [['runtime.infrastructure.db.authority_policy',
                                        'AuthorityPolicyMixin']],
            'get_children': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_claimed_task_completion_recovery': [['runtime.infrastructure.db.tasks',
                                                      'TasksMixin']],
            'get_consumed_completed_task_completion_recovery_task_ids': [['runtime.infrastructure.db.tasks',
                                                                          'TasksMixin']],
            'get_consumed_nonroot_escalation_recovery_task_ids': [['runtime.infrastructure.db.tasks',
                                                                   'TasksMixin']],
            'get_consumed_task_completion_recovery_owners': [['runtime.infrastructure.db.tasks',
                                                              'TasksMixin']],
            'get_current_authority_policy_activation': [['runtime.infrastructure.db.authority_policy',
                                                         'AuthorityPolicyMixin']],
            'get_descendant_task_ids': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_direct_revisits': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_dream': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'get_dream_for_agent_date': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'get_escalation_episode_audit_tail': [['runtime.infrastructure.db.audit',
                                                   'AuditMixin']],
            'get_escalation_notification': [['runtime.infrastructure.database', 'Database']],
            'get_invocation_any_status': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'get_job': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'get_job_owner_task_id': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'get_job_status': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'get_last_successful_dream': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'get_latest_completion_report': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_latest_notification_for_sr': [['runtime.infrastructure.database', 'Database']],
            'get_latest_skill_materialization': [['runtime.infrastructure.db.knowledge',
                                                  'KnowledgeMixin']],
            'get_latest_skill_validation': [['runtime.infrastructure.db.knowledge',
                                             'KnowledgeMixin']],
            'get_latest_task_result': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_next_authority_policy_release_version': [['runtime.infrastructure.db.authority_policy',
                                                           'AuthorityPolicyMixin']],
            'get_nonterminal_task_ids': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_org_setting': [['runtime.infrastructure.db.knowledge', 'KnowledgeMixin']],
            'get_pending_invocation': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'get_recall_payload': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_reply_delivery_state': [['runtime.infrastructure.db.reply_delivery',
                                          'ReplyDeliveryMixin']],
            'get_running_job_task_ids': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'get_subtree_statuses': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_task': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_task_attachment': [['runtime.infrastructure.db.attachments', 'AttachmentsMixin']],
            'get_task_attachment_by_storage_key': [['runtime.infrastructure.db.attachments',
                                                    'AttachmentsMixin']],
            'get_task_results': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'get_thread': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'get_thread_max_message_seq': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'get_thread_message_by_seq': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'get_thread_reply_breaker': [['runtime.infrastructure.db.reply_delivery',
                                          'ReplyDeliveryMixin']],
            'get_thread_scoped_attachment': [['runtime.infrastructure.db.attachments',
                                              'AttachmentsMixin']],
            'get_thread_session': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            'handoff_consumed_task_completion_recovery_parent_effect': [['runtime.infrastructure.db.tasks',
                                                                         'TasksMixin']],
            'has_orchestration_step_audit': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'has_task_completion_report_audit': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'increment_revision_count': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'increment_thread_turns_used': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'insert_audit_log': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'insert_audit_log_uncommitted': [['runtime.infrastructure.db.audit', 'AuditMixin']],
            'insert_cleanup_report_thread_and_task': [['runtime.infrastructure.database',
                                                       'Database']],
            'insert_dream': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'insert_dream_kb_candidate': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'insert_job': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'insert_session_token_usage': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            'insert_skill_validation_event': [['runtime.infrastructure.database', 'Database']],
            'insert_task': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'insert_task_attachment': [['runtime.infrastructure.database', 'Database']],
            'insert_task_result': [['runtime.infrastructure.database', 'Database']],
            'insert_task_with_attachments': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'insert_thread': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'insert_thread_scoped_attachment': [['runtime.infrastructure.database', 'Database']],
            'invalidate_authority_policy_v2_notification_generation': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                        'AuthorityV2ContinuationMixin']],
            'invalidate_thread_session_evicted': [['runtime.infrastructure.db.sessions',
                                                   'SessionsMixin']],
            'is_thread_participant': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'kb_view_stats': [['runtime.infrastructure.db.knowledge', 'KnowledgeMixin']],
            'list_agent_tasks': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_authority_audit': [['runtime.infrastructure.db.authority_v1',
                                      'AuthorityV1Mixin']],
            'list_authority_candidates_for_root': [['runtime.infrastructure.db.authority_v1',
                                                    'AuthorityV1Mixin']],
            'list_authority_policy_history': [['runtime.infrastructure.db.authority_policy',
                                               'AuthorityPolicyMixin']],
            'list_authority_policy_outcomes': [['runtime.infrastructure.db.authority_policy',
                                                'AuthorityPolicyMixin']],
            'list_authority_policy_selector_history': [['runtime.infrastructure.db.authority_policy',
                                                        'AuthorityPolicyMixin']],
            'list_authority_policy_v2_candidate_audits': [['runtime.infrastructure.db.authority_v2_attempts',
                                                           'AuthorityV2AttemptsMixin']],
            'list_authority_policy_v2_control_audit': [['runtime.infrastructure.db.authority_policy',
                                                        'AuthorityPolicyMixin']],
            'list_authority_policy_v2_evaluations': [['runtime.infrastructure.db.authority_v2_attempts',
                                                      'AuthorityV2AttemptsMixin']],
            'list_authority_policy_v2_history': [['runtime.infrastructure.db.authority_policy',
                                                  'AuthorityPolicyMixin']],
            'list_authority_policy_v2_publication_targets': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            'list_authority_policy_v2_result_stage_audits': [['runtime.infrastructure.db.authority_v2_attempts',
                                                              'AuthorityV2AttemptsMixin']],
            'list_authority_policy_v2_unfinalized_attempts': [['runtime.infrastructure.db.authority_v2_attempts',
                                                               'AuthorityV2AttemptsMixin']],
            'list_blocked_with_kind': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_dream_ids_by_status': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'list_dream_kb_candidates': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'list_dreams': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'list_invocations_for_thread_grouped_by_seq': [['runtime.infrastructure.db.threads',
                                                            'ThreadsMixin']],
            'list_job_ids_by_status': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'list_jobs_db': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'list_open_notifications_for_task': [['runtime.infrastructure.database', 'Database']],
            'list_open_thread_ids': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_pending_thread_invocations': [['runtime.infrastructure.db.threads',
                                                 'ThreadsMixin']],
            'list_reply_delivery_projections': [['runtime.infrastructure.db.reply_delivery',
                                                 'ReplyDeliveryMixin']],
            'list_reply_delivery_states': [['runtime.infrastructure.db.reply_delivery',
                                            'ReplyDeliveryMixin']],
            'list_reply_exchange_projections': [['runtime.infrastructure.db.reply_exchange',
                                                 'ReplyExchangeMixin']],
            'list_roots': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_session_token_usage': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            'list_skill_validation_events': [['runtime.infrastructure.db.knowledge',
                                              'KnowledgeMixin']],
            'list_stale_pending_jobs': [['runtime.infrastructure.database', 'Database']],
            'list_started_invocations_for_agent': [['runtime.infrastructure.db.threads',
                                                    'ThreadsMixin']],
            'list_tables': [['runtime.infrastructure.database', 'Database']],
            'list_task_attachments': [['runtime.infrastructure.db.attachments',
                                       'AttachmentsMixin']],
            'list_tasks': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_tasks_blocked_on_jobs': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_tasks_by_brief_prefix': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_tasks_by_thread': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'list_thread_invocations': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_thread_messages': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_thread_participant_names_for_threads': [['runtime.infrastructure.db.threads',
                                                           'ThreadsMixin']],
            'list_thread_participants': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_thread_scoped_attachments': [['runtime.infrastructure.db.attachments',
                                                'AttachmentsMixin']],
            'list_threads': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_threads_page': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'list_threads_by_composed_from_task_id': [['runtime.infrastructure.db.threads',
                                                       'ThreadsMixin']],
            'list_workspace_cleanup_activity': [['runtime.infrastructure.db.workspace_cleanup',
                                                 'WorkspaceCleanupMixin']],
            'mark_invocation_declined': [['runtime.infrastructure.database', 'Database']],
            'mark_task_completion_recovery_callback_consumed': [['runtime.infrastructure.db.tasks',
                                                                 'TasksMixin']],
            'mint_due_thread_reply_breaker_probes': [['runtime.infrastructure.db.reply_delivery',
                                                      'ReplyDeliveryMixin']],
            'mint_escalation_notification': [['runtime.infrastructure.database', 'Database']],
            'mint_followup_invocation_with_cap_extend': [['runtime.infrastructure.db.threads',
                                                          'ThreadsMixin']],
            'mint_thread_invocation': [['runtime.infrastructure.database', 'Database']],
            'next_dream_id': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'next_job_id': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'next_task_attachment_id': [['runtime.infrastructure.db.attachments',
                                         'AttachmentsMixin']],
            'next_task_id': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'next_thread_attachment_id': [['runtime.infrastructure.db.attachments',
                                           'AttachmentsMixin']],
            'next_thread_id': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'path': [['runtime.infrastructure.database', 'Database']],
            'publish_task_completion_recovery_binding': [['runtime.infrastructure.db.tasks',
                                                          'TasksMixin']],
            'query_audit_logs': [['runtime.infrastructure.database', 'Database']],
            'query_usage_lifecycle_snapshot': [['runtime.infrastructure.db.sessions',
                                                'SessionsMixin']],
            'reactivate_authority_policy_legacy': [['runtime.infrastructure.db.authority_policy',
                                                    'AuthorityPolicyMixin']],
            'reap_pending_invocations': [['runtime.infrastructure.database', 'Database']],
            'reaper_sweep_reply_exchanges': [['runtime.infrastructure.db.reply_exchange',
                                              'ReplyExchangeMixin']],
            'reconcile_accepted_recovery_continued_same_root': [['runtime.infrastructure.db.tasks',
                                                                 'TasksMixin']],
            'reconcile_reply_exchanges': [['runtime.infrastructure.db.reply_exchange',
                                           'ReplyExchangeMixin']],
            'record_authority_audit': [['runtime.infrastructure.db.authority_v1',
                                        'AuthorityV1Mixin']],
            'record_authority_evaluation': [['runtime.infrastructure.db.authority_v1',
                                             'AuthorityV1Mixin']],
            'record_authority_policy_v2_notification_publication_failure': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                             'AuthorityV2ContinuationMixin']],
            'record_conversational_arrival': [['runtime.infrastructure.db.reply_delivery',
                                               'ReplyDeliveryMixin']],
            'record_dispatch_on_invocation': [['runtime.infrastructure.db.threads',
                                               'ThreadsMixin']],
            'record_kb_view': [['runtime.infrastructure.db.knowledge', 'KnowledgeMixin']],
            'record_processed_event': [['runtime.infrastructure.database', 'Database']],
            'record_thread_reply_breaker_failure': [['runtime.infrastructure.db.reply_delivery',
                                                     'ReplyDeliveryMixin']],
            'recover_orphaned_running_jobs': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'recover_reply_delivery_state': [['runtime.infrastructure.db.reply_delivery',
                                              'ReplyDeliveryMixin']],
            'refuse_authority_policy_v2_decision_dispatch': [['runtime.infrastructure.db.authority_v2_continuation',
                                                              'AuthorityV2ContinuationMixin']],
            'remove_thread_participant': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'rename_thread_with_audit': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'reply_conversational': [['runtime.infrastructure.db.reply_delivery',
                                      'ReplyDeliveryMixin']],
            'reset_thread_session': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            'reset_thread_sessions_for_agent': [['runtime.infrastructure.db.sessions',
                                                 'SessionsMixin']],
            'reset_thread_sessions_for_thread': [['runtime.infrastructure.db.sessions',
                                                  'SessionsMixin']],
            'resolve_ancestor_attachments': [['runtime.infrastructure.db.attachments',
                                              'AttachmentsMixin']],
            'rollback': [['runtime.infrastructure.database', 'Database']],
            'select_workspace_cleanup_reclamation_candidates': [['runtime.infrastructure.db.workspace_cleanup',
                                                                 'WorkspaceCleanupMixin']],
            'set_task_executor_pid_if_current': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'set_thread_pinned': [['runtime.infrastructure.database', 'Database']],
            'set_thread_pinned_uncommitted': [['runtime.infrastructure.db.threads',
                                               'ThreadsMixin']],
            'set_thread_pinned_with_audit': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'set_thread_status': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'set_thread_subject': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'set_thread_subject_uncommitted': [['runtime.infrastructure.db.threads',
                                                'ThreadsMixin']],
            'set_thread_transcript_path': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'set_thread_turn_cap': [['runtime.infrastructure.db.threads', 'ThreadsMixin']],
            'settle_authority_policy_v2_continuation_receipt': [['runtime.infrastructure.db.authority_v2_continuation',
                                                                 'AuthorityV2ContinuationMixin']],
            'settle_consumed_task_completion_recovery_jobs': [['runtime.infrastructure.db.jobs',
                                                               'JobsMixin']],
            'settle_conversational_reply': [['runtime.infrastructure.db.reply_delivery',
                                             'ReplyDeliveryMixin']],
            'settle_conversational_reply_with_breaker_failure': [['runtime.infrastructure.db.reply_delivery',
                                                                  'ReplyDeliveryMixin']],
            'settle_conversational_reply_with_exchange': [['runtime.infrastructure.db.reply_delivery',
                                                           'ReplyDeliveryMixin']],
            'settle_expired_task_completion_recovery': [['runtime.infrastructure.db.tasks',
                                                         'TasksMixin']],
            'settle_interrupted_task_completion_recovery': [['runtime.infrastructure.db.tasks',
                                                             'TasksMixin']],
            'settle_thread_reply_breaker_success': [['runtime.infrastructure.db.reply_delivery',
                                                     'ReplyDeliveryMixin']],
            'settle_v2_continuation_generation_admission': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            'spend_authority_continue_envelope_if_active': [['runtime.infrastructure.db.authority_v1',
                                                             'AuthorityV1Mixin']],
            'spend_authority_policy_v2_continue_envelope': [['runtime.infrastructure.db.authority_v2_continuation',
                                                             'AuthorityV2ContinuationMixin']],
            'stamp_invocation_started': [['runtime.infrastructure.database', 'Database']],
            'summarize_workspace_cleanup_marker_history': [['runtime.infrastructure.db.workspace_cleanup',
                                                            'WorkspaceCleanupMixin']],
            'task_completion_recovery_launch_allowed': [['runtime.infrastructure.db.tasks',
                                                         'TasksMixin']],
            'terminate_agent_cleanups': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'transition_job_to_rejected': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'transition_job_to_running': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'transition_job_to_terminal': [['runtime.infrastructure.db.jobs', 'JobsMixin']],
            'try_advance_chain': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_claim_for_step': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_claim_v2_continuation_generation': [['runtime.infrastructure.db.authority_v2_continuation',
                                                      'AuthorityV2ContinuationMixin']],
            'try_delegate': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_delegate_many': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_escalate': [['runtime.infrastructure.database', 'Database']],
            'try_escalate_over_budget': [['runtime.infrastructure.database', 'Database']],
            'try_escalate_runtime': [['runtime.infrastructure.database', 'Database']],
            'try_fail_nonroot_manager_supersede': [['runtime.infrastructure.db.tasks',
                                                    'TasksMixin']],
            'try_fail_over_budget': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_manager_supersede': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'try_reject_thread_origin_manager_supersede': [['runtime.infrastructure.db.tasks',
                                                            'TasksMixin']],
            'try_retry_feedback': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'update_dream': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'update_dream_kb_candidate': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'update_dream_status_if': [['runtime.infrastructure.db.dreams', 'DreamsMixin']],
            'update_processed_event_outcome': [['runtime.infrastructure.database', 'Database']],
            'update_task': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'update_task_active_chain': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'update_task_active_fanout': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'update_thread_session': [['runtime.infrastructure.db.sessions', 'SessionsMixin']],
            'upsert_org_setting': [['runtime.infrastructure.db.knowledge', 'KnowledgeMixin']],
            'verify_retry_link': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'walk_ancestors': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'walk_revisit_chain': [['runtime.infrastructure.db.tasks', 'TasksMixin']],
            'workflow_schema_transaction': [['runtime.infrastructure.database', 'Database']]},
 'types': {'AttachmentsMixin': ['runtime.infrastructure.db.attachments', 'AttachmentsMixin'],
           'AuditMixin': ['runtime.infrastructure.db.audit', 'AuditMixin'],
           'AuthorityAuditEvent': ['runtime.models', 'AuthorityAuditEvent'],
           'AuthorityAuditEventType': ['runtime.models', 'AuthorityAuditEventType'],
           'AuthorityAuditMigrationRefusal': ['runtime.infrastructure.db.schema',
                                              'AuthorityAuditMigrationRefusal'],
           'AuthorityAuditPayload': ['runtime.models', 'AuthorityAuditPayload'],
           'AuthorityCandidate': ['runtime.models', 'AuthorityCandidate'],
           'AuthorityCandidatePolicyPin': ['runtime.models', 'AuthorityCandidatePolicyPin'],
           'AuthorityEvaluation': ['runtime.models', 'AuthorityEvaluation'],
           'AuthorityFenceResult': ['runtime.models', 'AuthorityFenceResult'],
           'AuthorityPolicyMixin': ['runtime.infrastructure.db.authority_policy',
                                    'AuthorityPolicyMixin'],
           'AuthorityPolicyV2AdmissionSettlementOutcome': ['runtime.models',
                                                           'AuthorityPolicyV2AdmissionSettlementOutcome'],
           'AuthorityPolicyV2Attempt': ['runtime.models', 'AuthorityPolicyV2Attempt'],
           'AuthorityPolicyV2Candidate': ['runtime.models', 'AuthorityPolicyV2Candidate'],
           'AuthorityPolicyV2CandidateAudit': ['runtime.models', 'AuthorityPolicyV2CandidateAudit'],
           'AuthorityPolicyV2CompletionDispatchContext': ['runtime.models',
                                                          'AuthorityPolicyV2CompletionDispatchContext'],
           'AuthorityPolicyV2ContinueEnvelope': ['runtime.models',
                                                 'AuthorityPolicyV2ContinueEnvelope'],
           'AuthorityPolicyV2DecisionAckOutcome': ['runtime.models',
                                                   'AuthorityPolicyV2DecisionAckOutcome'],
           'AuthorityPolicyV2DecisionClaimOutcome': ['runtime.models',
                                                     'AuthorityPolicyV2DecisionClaimOutcome'],
           'AuthorityPolicyV2DecisionRefusalOutcome': ['runtime.models',
                                                       'AuthorityPolicyV2DecisionRefusalOutcome'],
           'AuthorityPolicyV2EnqueueDispatchClassification': ['runtime.models',
                                                              'AuthorityPolicyV2EnqueueDispatchClassification'],
           'AuthorityPolicyV2Evaluation': ['runtime.models', 'AuthorityPolicyV2Evaluation'],
           'AuthorityPolicyV2FinalizationOutcome': ['runtime.models',
                                                    'AuthorityPolicyV2FinalizationOutcome'],
           'AuthorityPolicyV2GenerationClaimOutcome': ['runtime.models',
                                                       'AuthorityPolicyV2GenerationClaimOutcome'],
           'AuthorityPolicyV2HousekeepingOutcome': ['runtime.models',
                                                    'AuthorityPolicyV2HousekeepingOutcome'],
           'AuthorityPolicyV2HousekeepingTarget': ['runtime.models',
                                                   'AuthorityPolicyV2HousekeepingTarget'],
           'AuthorityPolicyV2InvalidationOutcome': ['runtime.models',
                                                    'AuthorityPolicyV2InvalidationOutcome'],
           'AuthorityPolicyV2Pin': ['runtime.models', 'AuthorityPolicyV2Pin'],
           'AuthorityPolicyV2PublicationAckOutcome': ['runtime.models',
                                                      'AuthorityPolicyV2PublicationAckOutcome'],
           'AuthorityPolicyV2PublicationClaimOutcome': ['runtime.models',
                                                        'AuthorityPolicyV2PublicationClaimOutcome'],
           'AuthorityPolicyV2PublicationFailureOutcome': ['runtime.models',
                                                          'AuthorityPolicyV2PublicationFailureOutcome'],
           'AuthorityPolicyV2PublicationTarget': ['runtime.models',
                                                  'AuthorityPolicyV2PublicationTarget'],
           'AuthorityPolicyV2RecoveryNotification': ['runtime.models',
                                                     'AuthorityPolicyV2RecoveryNotification'],
           'AuthorityPolicyV2RootDispatch': ['runtime.models', 'AuthorityPolicyV2RootDispatch'],
           'AuthorityPolicyV2SessionBinding': ['runtime.models', 'AuthorityPolicyV2SessionBinding'],
           'AuthorityPolicyV2SettlementOutcome': ['runtime.models',
                                                  'AuthorityPolicyV2SettlementOutcome'],
           'AuthorityPolicyV2SpendOutcome': ['runtime.models', 'AuthorityPolicyV2SpendOutcome'],
           'AuthorityPolicyV2StageOutcome': ['runtime.models', 'AuthorityPolicyV2StageOutcome'],
           'AuthorityRedactionClass': ['runtime.models', 'AuthorityRedactionClass'],
           'AuthorityRetentionClass': ['runtime.models', 'AuthorityRetentionClass'],
           'AuthorityV1Mixin': ['runtime.infrastructure.db.authority_v1', 'AuthorityV1Mixin'],
           'AuthorityV2AttemptsMixin': ['runtime.infrastructure.db.authority_v2_attempts',
                                        'AuthorityV2AttemptsMixin'],
           'AuthorityV2ContinuationMixin': ['runtime.infrastructure.db.authority_v2_continuation',
                                            'AuthorityV2ContinuationMixin'],
           'BlockKind': ['runtime.models', 'BlockKind'],
           'Committed': ['runtime.infrastructure.db.tasks', 'Committed'],
           'Database': ['runtime.infrastructure.database', 'Database'],
           'DreamStatus': ['runtime.models', 'DreamStatus'],
           'DreamsMixin': ['runtime.infrastructure.db.dreams', 'DreamsMixin'],
           'InvalidLineage': ['runtime.infrastructure.db.tasks', 'InvalidLineage'],
           'JobsMixin': ['runtime.infrastructure.db.jobs', 'JobsMixin'],
           'KnowledgeMixin': ['runtime.infrastructure.db.knowledge', 'KnowledgeMixin'],
           'LineageTooDeep': ['runtime.infrastructure.db.tasks', 'LineageTooDeep'],
           'LocalCiEvidence': ['runtime.models', 'LocalCiEvidence'],
           'LostClaim': ['runtime.infrastructure.db.tasks', 'LostClaim'],
           'NextStep': ['runtime.models', 'NextStep'],
           'Path': ['pathlib', 'Path'],
           'PendingRetry': ['runtime.infrastructure.db.tasks', 'PendingRetry'],
           'ReplyDeliveryMixin': ['runtime.infrastructure.db.reply_delivery', 'ReplyDeliveryMixin'],
           'ReplyDeliveryProjection': ['runtime.models', 'ReplyDeliveryProjection'],
           'ReplyExchangeMixin': ['runtime.infrastructure.db.reply_exchange', 'ReplyExchangeMixin'],
           'RetryClaim': ['runtime.infrastructure.db.tasks', 'RetryClaim'],
           'ScheduleStatus': ['runtime.models', 'ScheduleStatus'],
           'ScheduleStore': ['runtime.infrastructure.schedule_store', 'ScheduleStore'],
           'SchemaMixin': ['runtime.infrastructure.db.schema', 'SchemaMixin'],
           'SessionsMixin': ['runtime.infrastructure.db.sessions', 'SessionsMixin'],
           'TaskAttachmentRecord': ['runtime.models', 'TaskAttachmentRecord'],
           'TaskRecord': ['runtime.models', 'TaskRecord'],
           'TaskStatus': ['runtime.models', 'TaskStatus'],
           'TasksMixin': ['runtime.infrastructure.db.tasks', 'TasksMixin'],
           'ThreadAttachment': ['runtime.models', 'ThreadAttachment'],
           'ThreadInvocation': ['runtime.models', 'ThreadInvocation'],
           'ThreadInvocationPurpose': ['runtime.models', 'ThreadInvocationPurpose'],
           'ThreadInvocationStatus': ['runtime.models', 'ThreadInvocationStatus'],
           'ThreadMessage': ['runtime.models', 'ThreadMessage'],
           'ThreadMessageKind': ['runtime.models', 'ThreadMessageKind'],
           'ThreadParticipant': ['runtime.models', 'ThreadParticipant'],
           'ThreadRecord': ['runtime.models', 'ThreadRecord'],
           'ThreadReplyArrival': ['runtime.models', 'ThreadReplyArrival'],
           'ThreadReplyBreakerEpisode': ['runtime.models', 'ThreadReplyBreakerEpisode'],
           'ThreadReplyClaim': ['runtime.models', 'ThreadReplyClaim'],
           'ThreadReplyDeliveryState': ['runtime.models', 'ThreadReplyDeliveryState'],
           'ThreadReplyExchangeProjection': ['runtime.models', 'ThreadReplyExchangeProjection'],
           'ThreadReplyRecoveryEntry': ['runtime.models', 'ThreadReplyRecoveryEntry'],
           'ThreadReplySettlement': ['runtime.models', 'ThreadReplySettlement'],
           'ThreadScopedAttachment': ['runtime.models', 'ThreadScopedAttachment'],
           'ThreadStatus': ['runtime.models', 'ThreadStatus'],
           'ThreadsMixin': ['runtime.infrastructure.db.threads', 'ThreadsMixin'],
           'TokenUsage': ['runtime.models', 'TokenUsage'],
           'ValidationError': ['pydantic_core._pydantic_core', 'ValidationError'],
           'VerifiedRetry': ['runtime.infrastructure.db.tasks', 'VerifiedRetry'],
           'WorkHourStatus': ['runtime.models', 'WorkHourStatus'],
           'WorkHoursStore': ['runtime.infrastructure.work_hours_store', 'WorkHoursStore'],
           'WorkspaceCleanupMarkerHistorySummary': ['runtime.infrastructure.db.workspace_cleanup',
                                                    'WorkspaceCleanupMarkerHistorySummary'],
           'WorkspaceCleanupMixin': ['runtime.infrastructure.db.workspace_cleanup',
                                     'WorkspaceCleanupMixin'],
           'WorkspaceCleanupReclamationCandidate': ['runtime.infrastructure.db.workspace_cleanup',
                                                    'WorkspaceCleanupReclamationCandidate'],
           'WorkspaceCleanupReclamationSelection': ['runtime.infrastructure.db.workspace_cleanup',
                                                    'WorkspaceCleanupReclamationSelection'],
           '_RetryEvidenceRefusal': ['runtime.infrastructure.db.tasks', '_RetryEvidenceRefusal'],
           'datetime': ['datetime', 'datetime'],
           'timedelta': ['datetime', 'timedelta'],
           'timezone': ['datetime', 'timezone']}}
