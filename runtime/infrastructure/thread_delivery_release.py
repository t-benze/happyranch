"""Dormant C1a delivery dispositions; never a cleanup/retirement certificate.

The native mutator is UNMOUNTED. C1b must supply a real serving lifetime and
current-control owner before native_authority can admit anything. In particular
there is no caller callback, proof Boolean, ambient credential or local registry
that enables mutation. Raw ThreadLifecycle and safety census remain unchanged.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Annotated, Any, Iterator, Literal

from pydantic import Field, ValidationError

from runtime.infrastructure.thread_admission import Closed, ThreadAdmissionStore

ACTION = "thread_delivery_release"
RISKS = (
    "late_producer_and_session_writes",
    "overlapping_native_and_workspace_work",
    "repeated_external_effects",
    "missing_whole_owner_process_effect_closure",
)
SafeID = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Integer = Annotated[int, Field(gt=0)]
SequenceNumber = Annotated[int, Field(ge=0)]
Mode = Literal["proof_backed", "human_risk_override"]


class ReleaseUnavailable(ValueError):
    """Category only; never return native payloads or invocation credentials."""

    def __init__(self, code: str, *, committed: bool | None = False,
                 settings_restored: bool = True) -> None:
        self.code, self.committed = code, committed
        self.settings_restored = settings_restored
        super().__init__("thread_delivery_release_" + code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseUnavailable("duplicate_field")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ReleaseUnavailable("nonfinite_json")


def decode(model: Any, raw: str) -> Any:
    if type(raw) is not str or len(raw.encode("utf8")) > 16384:
        raise ReleaseUnavailable("payload_bound")
    try:
        json.loads(raw, object_pairs_hook=_unique,
                   parse_constant=_constant)
        return model.model_validate_json(raw, strict=True)
    except (ValueError, TypeError, ValidationError) as from_exc:
        raise ReleaseUnavailable("invalid_evidence") from from_exc


def typed(values: Any) -> bytes:
    """Lossless length-delimited SQLite values, including NULL and exact UTF8."""
    result = bytearray()
    for value in values:
        if value is None:
            tag, raw = b"N", b""
        elif type(value) is int:
            tag, raw = b"I", str(value).encode("ascii")
        elif type(value) is str:
            tag, raw = b"T", value.encode("utf8")
        else:
            raise ReleaseUnavailable("native_type")
        result.extend(tag + len(raw).to_bytes(8, "big") + raw)
    return bytes(result)


def fingerprint(rows: list[dict]) -> str:
    return digest(b"".join(typed((len(row),)) + b"".join(
        typed((key, value)) for key, value in row.items()) for row in rows))


class Selection(Closed):
    org_slug: SafeID
    thread_id: SafeID
    invocation_id: Integer
    agent_id: SafeID
    session_id: SafeID
    preparation_id: SafeID
    token_digest: Digest


class PreviewRequest(Closed):
    version: Literal[1]
    operation_key: Key
    mode: Mode
    selection: Selection


class Prefix(Closed):
    count: Annotated[int, Field(gt=0, le=1024)]
    last_id: Integer
    digest: Digest


class DeliveryRange(Closed):
    acknowledged: SequenceNumber
    required: SequenceNumber
    running_from: SequenceNumber | None
    running_through: SequenceNumber | None


class Confirmation(Closed):
    confirmed: Literal[True]
    preview_digest: Digest
    accepted_risks: tuple[Literal[
        "late_producer_and_session_writes", "overlapping_native_and_workspace_work",
        "repeated_external_effects", "missing_whole_owner_process_effect_closure",
    ], ...]


class ReleaseCommand(PreviewRequest):
    prefix: Prefix
    native_digest: Digest
    range: DeliveryRange
    risk_digest: Digest
    reason: Literal["selected_thread_delivery_obstruction"]
    confirmation: Confirmation


class Companion(Closed):
    version: Literal[1]
    org_slug: SafeID
    operation_key: Key
    command: Annotated[str, Field(max_length=16384)]
    command_digest: Digest
    principal: Literal["founder"]
    admission: Literal["native_serving_human_owner"]
    source_history_digest: Digest
    source_post_digest: Digest
    disposition: Literal["running_replaced", "historical_only"]
    replacement_id: Integer | None
    replacement_token_digest: Digest | None
    replacement_initial_digest: Digest | None
    poststate_digest: Digest
    wake_audit_id: Integer | None
    marker_audit_id: Integer | None


# Fixed native columns, not caller-selected SQL or a schema-filtering baseline.
_COLUMNS = {
    "audit_log": "id task_id agent action payload",
    "threads": "id status started_at archived_at turn_cap turns_used",
    "thread_participants": "thread_id agent_name added_at added_by agent_session_id last_resumed_seq",
    "thread_invocations": (
        "id thread_id agent_name invocation_token triggering_seq purpose status enqueued_at "
        "started_at consumed_at session_id executor model reply_message_seq dispatched_task_id decline_reason"
    ),
    "thread_reply_delivery_state": (
        "thread_id agent_name acknowledged_through_seq required_through_seq queued_invocation_token "
        "running_invocation_token running_from_seq running_through_seq last_terminal_reason last_terminal_at updated_at"
    ),
    "thread_reply_breaker_episodes": (
        "thread_id agent_name executor_key episode_id state consecutive_failures opened_at "
        "cooldown_until probe_lease_id last_failure_category updated_at"
    ),
    "thread_reply_breaker_receipts": "invocation_token episode_id outcome failure_category recorded_at",
    "thread_reply_exchange": (
        "thread_id exchange_id state open_seq close_seq opened_at last_activity_at closed_at close_reason deferred_count"
    ),
    "thread_exchange_deferrals": (
        "thread_id exchange_id agent_name state created_at released_at mint_token_prefix catchup_pending"
    ),
    "thread_messages": "id thread_id seq mentions_json",
}


@dataclass
class Budget:
    deadline: float = field(default_factory=lambda: time.monotonic() + 3)
    rows: int = 0
    raw_bytes: int = 0
    statements: int = 0
    vm_steps: int = 0

    def check(self) -> None:
        if (time.monotonic() >= self.deadline or self.rows > 1024
                or self.raw_bytes > 4 * 1024 * 1024
                or self.statements > 256 or self.vm_steps > 5_000_000):
            raise ReleaseUnavailable("budget_unavailable")

    def statement(self) -> None:
        self.statements += 1
        self.vm_steps += 1000  # conservative charge below progress granularity
        self.check()


class NativeRead:
    """One shared budget/settings owner; caller already owns Database RLock.

    Permits composition inside the SOLE native write transaction. Never calls
    B's transaction-refusing diagnostic reader. Restore failure poisons this
    seam and carries an explicit native-close obligation to the future owner.
    """

    def __init__(self, db: Any, budget: Budget) -> None:
        self.db, self.budget = db, budget

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        self.budget.statement()
        return self.db._conn.execute(sql, params)

    def change(self, sql: str, params: tuple) -> int:
        cur = self.execute(sql, params)
        try:
            return cur.rowcount
        finally:
            cur.close()

    def rows(self, table: str, where: str, params: tuple, order: str) -> list[dict]:
        columns = _COLUMNS[table].split()
        # SQLite checks every value BEFORE it is copied into a Python object.
        oversized = " OR ".join(
            f"(typeof({c}) NOT IN ('null','integer','text') OR coalesce(length(CAST({c} AS BLOB)),0)>16384)"
            for c in columns)
        projections = ",".join(
            f"CASE WHEN ({oversized}) THEN NULL ELSE {c} END AS {c}" for c in columns)
        cur = self.execute(
            f"SELECT ({oversized}) AS invalid,{projections} FROM {table} WHERE {where} ORDER BY {order}",
            params)
        result: list[dict] = []
        try:
            while True:
                # At most 16 per page; even the completion sentinel is charged.
                self.budget.check()
                page = cur.fetchmany(16)
                self.budget.statements += 1
                self.budget.check()
                for row in page:
                    self.budget.rows += 1
                    self.budget.raw_bytes += len(typed(tuple(row)))
                    self.budget.check()
                    if row["invalid"] != 0:
                        raise ReleaseUnavailable("value_bound")
                    result.append({c: row[c] for c in columns})
                if len(page) < 16:
                    self.budget.rows += 1  # charged completion sentinel
                    self.budget.check()
                    return result
        finally:
            cur.close()

    def one(self, table: str, where: str, params: tuple, order: str) -> dict | None:
        rows = self.rows(table, where, params, order)
        if len(rows) > 1:
            raise ReleaseUnavailable("ambiguous_native_owner")
        return rows[0] if rows else None


@contextmanager
def native_read(db: Any, budget: Budget) -> Iterator[NativeRead]:
    # A native caller under DB may reenter this lock; no DB->move acquisition.
    if not db._lock.acquire(timeout=min(.025, max(0, budget.deadline - time.monotonic()))):
        raise ReleaseUnavailable("database_busy")
    changed = False
    old_timeout = old_length = old_factory = None
    failure = None

    def progress() -> int:
        nonlocal failure
        budget.vm_steps += 1000
        try:
            budget.check()
            return 0
        except ReleaseUnavailable as exc:
            failure = exc
            return 1

    try:
        budget.check()
        if (getattr(db, "_thread_release_settings_unsafe", False)
                or getattr(db, "_cleanup_diagnostic_settings_unsafe", False)):
            raise ReleaseUnavailable("settings_unrestored", settings_restored=False)
        if (getattr(db, "_collection_read_deadline", None) is not None
                or getattr(db, "_cleanup_diagnostic_read_active", False)
                or getattr(db, "_thread_release_read_active", False)):
            raise ReleaseUnavailable("database_owned")
        # Reserve unconditional restoration/transaction-finalization work
        # before changing connection settings; restoration never gets skipped
        # because the evidence budget was exhausted.
        budget.statements += 4
        budget.vm_steps += 4000
        budget.check()
        old_factory = db._conn.row_factory
        if old_factory not in (None, sqlite3.Row):
            raise ReleaseUnavailable("row_factory")
        changed = True
        db._thread_release_read_active = True
        db._conn.row_factory = sqlite3.Row
        old_length = db._conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,
            min(db._conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH), 1024 * 1024))
        db._conn.set_progress_handler(progress, 1000)
        reader = NativeRead(db, budget)
        cur = reader.execute("PRAGMA busy_timeout")
        try:
            budget.rows += 1
            budget.raw_bytes += 8
            budget.check()
            old_timeout = cur.fetchone()[0]
        finally:
            cur.close()
        reader.execute("PRAGMA busy_timeout=25").close()
        yield reader
    except sqlite3.Error:
        if failure is not None:
            raise failure from None
        raise ReleaseUnavailable("native_unavailable", committed=None if db._conn.in_transaction else False) from None
    finally:
        restored = True
        if changed:
            for restore in (
                lambda: db._conn.set_progress_handler(None, 0),
                lambda: setattr(db._conn, "row_factory", old_factory),
                lambda: db._conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, old_length) if old_length is not None else None,
                lambda: db._conn.execute(f"PRAGMA busy_timeout={int(old_timeout)}").close() if old_timeout is not None else None,
            ):
                try:
                    restore()
                except BaseException:
                    restored = False
            if restored:
                db._thread_release_read_active = False
            else:
                db._thread_release_settings_unsafe = True
        db._lock.release()
        if not restored:
            raise ReleaseUnavailable("settings_unrestored", committed=None, settings_restored=False)


@dataclass
class ThreadEvidence:
    audits: list[dict]
    invocations: list[dict]
    histories: dict[int, tuple]
    native: list[dict]
    delivery: list[dict]
    participants: list[dict]
    breakers: list[dict]
    exchanges: list[dict]
    deferrals: list[dict]


class ReleaseReader:
    """Strict complete org-family inventory and independent native cross-links."""

    def __init__(self, reader: NativeRead, org_slug: str) -> None:
        self.reader, self.org_slug = reader, org_slug
        self.threads: dict[str, ThreadEvidence] = {}
        self.records: dict[str, tuple[dict, Companion, ReleaseCommand]] = {}
        self.current: dict[int, bool] = {}

    def thread(self, thread_id: str) -> ThreadEvidence:
        if thread_id in self.threads:
            return self.threads[thread_id]
        r = self.reader
        audits = r.rows("audit_log", "task_id=?", (thread_id,), "id")
        invs = r.rows("thread_invocations", "thread_id=?", (thread_id,), "id")
        thread = r.rows("threads", "id=?", (thread_id,), "id")
        participants = r.rows("thread_participants", "thread_id=?", (thread_id,), "agent_name")
        delivery = r.rows("thread_reply_delivery_state", "thread_id=?", (thread_id,), "agent_name")
        breakers = r.rows("thread_reply_breaker_episodes", "thread_id=?", (thread_id,), "agent_name,executor_key")
        exchanges = r.rows("thread_reply_exchange", "thread_id=?", (thread_id,), "exchange_id")
        deferrals = r.rows("thread_exchange_deferrals", "thread_id=?", (thread_id,), "exchange_id,agent_name")
        receipts = r.rows("thread_reply_breaker_receipts",
            "invocation_token IN (SELECT invocation_token FROM thread_invocations WHERE thread_id=?)",
            (thread_id,), "invocation_token")
        openers = []
        for exchange in exchanges:
            openers.extend(r.rows("thread_messages", "thread_id=? AND seq=?",
                (thread_id, exchange["open_seq"]), "id"))
        if len(thread) != 1:
            raise ReleaseUnavailable("thread_missing")
        for inv in invs:
            if (type(inv["invocation_token"]) is not str or not inv["invocation_token"]
                    or type(inv["triggering_seq"]) is not int or inv["triggering_seq"] < 0
                    or type(inv["id"]) is not int or inv["id"] <= 0
                    or inv["thread_id"] != thread_id or not isinstance(inv["agent_name"], str)
                    or inv["status"] not in {"pending", "consumed", "declined", "failed", "timeout"}
                    or inv["purpose"] not in {"reply", "bootstrap", "task_followup"}):
                raise ReleaseUnavailable("native_invocation")
        for d in delivery:
            ack, req = d["acknowledged_through_seq"], d["required_through_seq"]
            if (type(ack) is not int or type(req) is not int or not 0 <= ack <= req
                    or (d["queued_invocation_token"] is not None and d["running_invocation_token"] is not None)):
                raise ReleaseUnavailable("native_delivery_range")
            for slot in ("queued_invocation_token", "running_invocation_token"):
                token = d[slot]
                if token is not None and not any(i["invocation_token"] == token
                        and i["agent_name"] == d["agent_name"] and i["purpose"] == "reply" for i in invs):
                    raise ReleaseUnavailable("native_delivery_owner")
            if d["running_invocation_token"] is not None:
                if (type(d["running_from_seq"]) is not int or type(d["running_through_seq"]) is not int
                        or not ack <= d["running_from_seq"] <= d["running_through_seq"] <= req):
                    raise ReleaseUnavailable("native_delivery_range")
            elif d["running_from_seq"] is not None or d["running_through_seq"] is not None:
                raise ReleaseUnavailable("native_delivery_range")
        for p in participants:
            if type(p["last_resumed_seq"]) is not int or p["last_resumed_seq"] < 0:
                raise ReleaseUnavailable("participant_range")
        for b in breakers:
            if (b["state"] not in {"closed", "open", "probe"}
                    or type(b["consecutive_failures"]) is not int or b["consecutive_failures"] < 0
                    or ((b["state"] == "probe") != (b["probe_lease_id"] is not None))):
                raise ReleaseUnavailable("native_breaker")
        for d in deferrals:
            if (d["state"] not in {"held", "released", "suppressed"}
                    or type(d["catchup_pending"]) is not int or d["catchup_pending"] not in (0, 1)
                    or not any(x["exchange_id"] == d["exchange_id"] for x in exchanges)):
                raise ReleaseUnavailable("native_deferral")
        lifecycle = [row for row in audits if row["action"] == "thread_move_lifecycle"]
        # Every lifecycle row has a real native owner; foreign/orphan rows refuse.
        from runtime.infrastructure.thread_admission import ThreadLifecycle
        ids = {inv["id"]: inv for inv in invs}
        state_ids = set()
        for row in lifecycle:
            r.budget.check()
            state = decode(ThreadLifecycle, row["payload"])
            if (type(state.invocation_id) is not int or state.invocation_id not in ids
                    or ids[state.invocation_id]["agent_name"] != state.agent_id):
                raise ReleaseUnavailable("orphan_lifecycle")
            state_ids.add(state.invocation_id)
        store = ThreadAdmissionStore(r.db, self.org_slug)
        histories = {}
        for inv in invs:
            native_inv = SimpleNamespace(id=inv["id"], thread_id=thread_id,
                agent_name=inv["agent_name"], session_id=inv["session_id"])
            # Empty history is a legacy fact; selected releases still require it.
            if inv["id"] in state_ids:
                histories[inv["id"]] = store._replay_bounded(
                    native_inv, lifecycle, check=r.budget.check)
        evidence = ThreadEvidence(audits, invs, histories,
            thread + participants + invs + delivery + breakers + receipts + exchanges + deferrals + openers,
            delivery, participants, breakers, exchanges, deferrals)
        self.threads[thread_id] = evidence
        return evidence

    def inventory(self, family: list[dict] | None = None) -> None:
        if family is None:
            family = self.reader.rows("audit_log", "action=?", (ACTION,), "id")
        owners: set[tuple] = set()
        for row in family:
            self.reader.budget.check()
            c = decode(Companion, row["payload"])
            cmd = decode(ReleaseCommand, c.command)
            s = cmd.selection
            if (type(c.version) is not int or type(cmd.version) is not int
                    or c.org_slug != self.org_slug or s.org_slug != self.org_slug
                    or row["task_id"] != s.thread_id or row["agent"] != s.agent_id
                    or c.operation_key != cmd.operation_key
                    or c.command.encode() != canonical(cmd.model_dump(mode="json"))
                    or digest(c.command.encode()) != c.command_digest
                    or cmd.mode != "human_risk_override"):
                raise ReleaseUnavailable("companion_owner")
            validate_confirmation(cmd)
            identity = (s.thread_id, s.invocation_id, s.agent_id, s.session_id, s.preparation_id)
            if cmd.operation_key in self.records or identity in owners:
                raise ReleaseUnavailable("duplicate_disposition")
            owners.add(identity)
            e = self.thread(s.thread_id)
            inv, history = selected(e, s)
            prefix = [r for r in e.audits if r["id"] <= cmd.prefix.last_id]
            if (len(prefix) != cmd.prefix.count or not prefix
                    or prefix[-1]["id"] != cmd.prefix.last_id
                    or fingerprint(prefix) != cmd.prefix.digest or row["id"] <= cmd.prefix.last_id):
                raise ReleaseUnavailable("saved_prefix_changed")
            source_history = [(i, state) for i, state in history if i <= cmd.prefix.last_id]
            if (not source_history or source_history[-1][1].preparation_id != s.preparation_id
                    or not (source_history[-1][1].unknown or not source_history[-1][1].producer_settled)
                    or history_digest(source_history) != c.source_history_digest):
                raise ReleaseUnavailable("source_history")
            if fingerprint([inv]) != c.source_post_digest:
                raise ReleaseUnavailable("source_poststate")
            if c.disposition == "running_replaced":
                if (cmd.range.running_from is None or cmd.range.running_through is None
                        or not cmd.range.acknowledged <= cmd.range.running_from <= cmd.range.running_through <= cmd.range.required
                        or cmd.range.required <= cmd.range.acknowledged
                        or inv["status"] != "failed" or inv["decline_reason"] != "human_delivery_release"
                        or inv["consumed_at"] is None or c.replacement_id is None
                        or c.replacement_token_digest is None or c.replacement_initial_digest is None
                        or c.wake_audit_id is None):
                    raise ReleaseUnavailable("replacement_link")
                replacement = next((v for v in e.invocations if v["id"] == c.replacement_id), None)
                if (replacement is None or replacement["id"] <= inv["id"]
                        or (replacement["thread_id"], replacement["agent_name"], replacement["purpose"]) !=
                        (s.thread_id, s.agent_id, "reply")
                        or digest(replacement["invocation_token"].encode()) != c.replacement_token_digest
                        or replacement["triggering_seq"] != cmd.range.acknowledged + 1):
                    raise ReleaseUnavailable("replacement_owner")
                initial = dict(replacement)
                initial.update(status="pending", started_at=None, consumed_at=None, session_id=None,
                    executor=None, model=None, reply_message_seq=None, dispatched_task_id=None, decline_reason=None)
                if fingerprint([initial]) != c.replacement_initial_digest:
                    raise ReleaseUnavailable("replacement_initial")
                wake = next((a for a in e.audits if a["id"] == c.wake_audit_id), None)
                if (wake is None or not cmd.prefix.last_id < wake["id"] < row["id"]
                        or wake["agent"] != s.agent_id or wake["action"] != "thread_reply_wake_recovered"
                        or _wake_payload(wake["payload"]) != {
                            "agent_name": s.agent_id, "kind": "human_delivery_release",
                            "from_seq": cmd.range.acknowledged + 1, "through_seq": cmd.range.required,
                            "token_prefix": replacement["invocation_token"][:8]}):
                    raise ReleaseUnavailable("wake_link")
            elif (inv["status"] == "pending" or c.replacement_id is not None
                    or c.replacement_token_digest is not None or c.replacement_initial_digest is not None
                    or c.wake_audit_id is not None or c.marker_audit_id is not None):
                raise ReleaseUnavailable("historical_disposition")
            if c.disposition == "running_replaced":
                # Reconstruct the original committed delivery snapshot without
                # demanding that later legitimate claims/arrivals stay frozen.
                d = dict(thread_id=s.thread_id, agent_name=s.agent_id,
                    acknowledged_through_seq=cmd.range.acknowledged, required_through_seq=cmd.range.required,
                    queued_invocation_token=replacement["invocation_token"], running_invocation_token=None,
                    running_from_seq=None, running_through_seq=None, last_terminal_reason="human_delivery_release",
                    last_terminal_at=inv["consumed_at"], updated_at=inv["consumed_at"])
                if fingerprint([inv, initial, d]) != c.poststate_digest:
                    raise ReleaseUnavailable("committed_poststate")
            elif c.poststate_digest != c.source_post_digest:
                raise ReleaseUnavailable("committed_poststate")
            if c.marker_audit_id is not None:
                marker = next((a for a in e.audits if a["id"] == c.marker_audit_id), None)
                if (marker is None or not cmd.prefix.last_id < marker["id"] < row["id"]
                        or marker["agent"] != s.agent_id
                        or marker["action"] != "thread_deferral_catchup_minted"
                        or _wake_payload(marker["payload"]) != {
                            "agent_name": s.agent_id, "kind": "human_delivery_release",
                            "from_seq": cmd.range.acknowledged + 1, "through_seq": cmd.range.required,
                            "replacement_id": c.replacement_id}):
                    raise ReleaseUnavailable("marker_link")
            self.records[cmd.operation_key] = (row, c, cmd)
            # A later ORIGINAL lifecycle revokes current delivery permission.
            # Its own audit suffix and legitimate replacement history do not.
            self.current[s.invocation_id] = history[-1][0] <= cmd.prefix.last_id
        self.reader.budget.check()

    def released(self, invocation_id: int) -> bool:
        return self.current.get(invocation_id, False)

    def show(self, operation_key: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_key):
            raise ReleaseUnavailable("key")
        record = self.records.get(operation_key)
        if record is None:
            return None
        row, c, cmd = record
        return dict(version=1, operation_key=operation_key, audit_id=row["id"],
            command_digest=c.command_digest, committed=True, disposition=c.disposition,
            original_id=cmd.selection.invocation_id, replacement_id=c.replacement_id,
            replacement_token_digest=c.replacement_token_digest,
            current_delivery_permission=self.released(cmd.selection.invocation_id),
            current_native_eligibility=False, serving_admission_available=False,
            incomplete_reasons=["serving_owner_unavailable"], notification="unknown")


def _wake_payload(raw: str) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, TypeError):
        raise ReleaseUnavailable("wake_payload") from None
    if type(value) is not dict:
        raise ReleaseUnavailable("wake_payload")
    return value


def selected(e: ThreadEvidence, selection: Selection) -> tuple[dict, tuple]:
    inv = next((v for v in e.invocations if v["id"] == selection.invocation_id), None)
    history = e.histories.get(selection.invocation_id)
    if (inv is None or history is None or inv["purpose"] != "reply" or inv["started_at"] is None
            or (inv["thread_id"], inv["agent_name"], inv["session_id"]) !=
            (selection.thread_id, selection.agent_id, selection.session_id)
            or digest(inv["invocation_token"].encode()) != selection.token_digest
            or history[-1][1].preparation_id != selection.preparation_id
            or history[-1][1].session_id != selection.session_id):
        raise ReleaseUnavailable("selection")
    return inv, history


def history_digest(history: tuple | list) -> str:
    return digest(canonical([(i, state.model_dump(mode="json")) for i, state in history]))


def confirmation_inputs(cmd: ReleaseCommand) -> dict:
    return cmd.model_dump(mode="json", exclude={"confirmation"})


def validate_confirmation(cmd: ReleaseCommand) -> None:
    if (type(cmd.version) is not int or type(cmd.selection.invocation_id) is not int
            or cmd.confirmation.confirmed is not True
            or cmd.confirmation.accepted_risks != RISKS
            or digest(canonical(confirmation_inputs(cmd))) != cmd.confirmation.preview_digest
            or cmd.risk_digest != digest(canonical(RISKS))):
        raise ReleaseUnavailable("confirmation")


def preview(reader: ReleaseReader, request: PreviewRequest) -> dict:
    if type(request.version) is not int or request.selection.org_slug != reader.org_slug:
        raise ReleaseUnavailable("selection")
    if request.operation_key in reader.records:
        raise ReleaseUnavailable("key_collision")
    if any(cmd.selection.invocation_id == request.selection.invocation_id
           for _, _, cmd in reader.records.values()):
        raise ReleaseUnavailable("already_disposed")
    e = reader.thread(request.selection.thread_id)
    inv, history = selected(e, request.selection)
    if not e.audits or inv["started_at"] is None:
        raise ReleaseUnavailable("unstarted")
    state = history[-1][1]
    if not (state.unknown or not state.producer_settled):
        raise ReleaseUnavailable("no_obstruction")
    s = request.selection
    pair = [row for row in e.delivery if row["agent_name"] == s.agent_id]
    if len(pair) != 1:
        raise ReleaseUnavailable("delivery_missing")
    d = pair[0]
    numbers = [d[k] for k in ("acknowledged_through_seq", "required_through_seq")]
    if any(type(v) is not int or v < 0 for v in numbers) or numbers[0] > numbers[1]:
        raise ReleaseUnavailable("range")
    pending = inv["status"] == "pending"
    if d["queued_invocation_token"] is not None and d["running_invocation_token"] is not None:
        raise ReleaseUnavailable("both_slots")
    if pending and next(row for row in e.native if "status" in row and "id" in row and row["id"] == s.thread_id)["status"] != "open":
        raise ReleaseUnavailable("thread_closed")
    if pending:
        if (d["running_invocation_token"] != inv["invocation_token"]
                or d["queued_invocation_token"] is not None
                or any(type(d[k]) is not int for k in ("running_from_seq", "running_through_seq"))
                or not numbers[0] <= d["running_from_seq"] <= d["running_through_seq"] <= numbers[1]
                or numbers[1] <= numbers[0]):
            raise ReleaseUnavailable("running_owner")
    if len([p for p in e.participants if p["agent_name"] == s.agent_id]) != 1:
        raise ReleaseUnavailable("participant_missing")
    for other in e.invocations:
        if other["id"] == inv["id"] or other["agent_name"] != s.agent_id:
            continue
        h = e.histories.get(other["id"])
        safely_queued = (not pending and other["status"] == "pending" and other["started_at"] is None
            and d["queued_invocation_token"] == other["invocation_token"]
            and d["running_invocation_token"] is None
            and (not h or (h[-1][1].deferred and h[-1][1].producer_settled and not h[-1][1].unknown)))
        if ((other["status"] == "pending" and not safely_queued)
                or (h and (h[-1][1].unknown or not h[-1][1].producer_settled)
                    and not reader.released(other["id"]))):
            raise ReleaseUnavailable("unrelated_owner")
    # Actual cohort membership is reconstructed from its native opening signal.
    for x in e.exchanges:
        if x["state"] == "open":
            opener = reader.reader.one("thread_messages", "thread_id=? AND seq=?",
                (s.thread_id, x["open_seq"]), "id")
            try:
                cohort = json.loads(opener["mentions_json"], object_pairs_hook=_unique) if opener else None
            except (ValueError, TypeError):
                cohort = None
            if (type(cohort) is not list or not cohort or any(type(v) is not str for v in cohort)
                    or len(set(cohort)) != len(cohort) or s.agent_id not in cohort):
                raise ReleaseUnavailable("exchange_unsafe")
    if any(x["agent_name"] == s.agent_id and
           (x["state"] == "held" or x["state"] == "suppressed"
            or type(x["catchup_pending"]) is not int or x["catchup_pending"] not in (0, 1)
            or (x["catchup_pending"] and x["state"] != "released")) for x in e.deferrals):
        raise ReleaseUnavailable("deferral_unsafe")
    active_breakers = [b for b in e.breakers if b["agent_name"] == s.agent_id and b["state"] != "closed"]
    if any(b["state"] != "probe" or not b["probe_lease_id"] or not pending for b in active_breakers) or len(active_breakers) > 1:
        raise ReleaseUnavailable("breaker_unsafe")
    inputs = request.model_dump(mode="json")
    inputs.update(prefix=Prefix(count=len(e.audits), last_id=e.audits[-1]["id"],
        digest=fingerprint(e.audits)).model_dump(),
        native_digest=fingerprint(e.native),
        range=DeliveryRange(acknowledged=numbers[0], required=numbers[1],
            running_from=d["running_from_seq"], running_through=d["running_through_seq"]).model_dump(),
        risk_digest=digest(canonical(RISKS)), reason="selected_thread_delivery_obstruction")
    reader.reader.budget.check()
    return dict(inputs=inputs, preview_digest=digest(canonical(inputs)),
        accepted_risks=list(RISKS), proof_backed_available=False,
        serving_admission_available=False, disposition="running_replaced" if pending else "historical_only")


@contextmanager
def native_authority(db: Any, selection: Selection) -> Iterator[Any]:
    """C1b dependency: actual serving owner/control/lifetime, never attestation.

    This fixed refusal deliberately precedes lock/settings/transaction changes.
    Future integration must hold move -> DB -> short revoke/COMMIT leaf, renew
    actual owner/host/tracker controls with the shared native reader/budget,
    then use only a local admission/revocation check in the COMMIT leaf. Retain
    DB lifetime through uncertain rollback/settings restoration. No active
    caller exists in C1a.
    """
    raise ReleaseUnavailable("serving_owner_unavailable")
    yield


def delivery_view(db: Any, thread_id: str | None = None) -> ReleaseReader | None:
    """Caller holds DB. No-companion path keeps literal legacy behavior."""
    # Even the no-companion action-family census is bounded. An exhausted
    # scan is never absence. No nested B/collection settings owner is stolen.
    move = getattr(db, "_team_move_coordinator", None)
    with native_read(db, Budget()) as native:
        own_snapshot = not db._conn.in_transaction
        if own_snapshot:
            native.execute("BEGIN").close()
        try:
            family = native.rows("audit_log", "action=?", (ACTION,), "id")
            if not family:
                return None
            if move is None:
                raise ReleaseUnavailable("org_owner_unavailable")
            view = ReleaseReader(native, move.store.org_slug)
            view.inventory(family)
            if thread_id is not None:
                view.thread(thread_id)
            return view
        finally:
            if own_snapshot:
                try:
                    db._conn.rollback()  # SELECT-only snapshot; never outer TX
                except BaseException:
                    db._thread_release_settings_unsafe = True
                    raise ReleaseUnavailable("read_snapshot_unrestored", committed=None,
                        settings_restored=False) from None


def pair_permission(view: ReleaseReader | None, thread_id: str, agent_id: str) -> bool:
    """Only release-linked pairs change; every other raw obstruction survives."""
    if view is None:
        return True
    evidence = view.threads[thread_id]
    if not any(cmd.selection.thread_id == thread_id and cmd.selection.agent_id == agent_id
               for _, _, cmd in view.records.values()):
        return True
    for inv in evidence.invocations:
        if inv["agent_name"] != agent_id:
            continue
        history = evidence.histories.get(inv["id"])
        if not history:
            continue
        state = history[-1][1]
        if view.released(inv["id"]):
            continue
        if state.unknown:
            # Native revalidation runs while THIS genuine current attempt is
            # open. Its own possible-launch row is not historical lost-owner
            # evidence. Never waive failed_cleanup or an earlier uncertain
            # attempt, and never infer ownership from a supplied DTO.
            move = getattr(view.reader.db, "_team_move_coordinator", None)
            owner = move.threads.live.get(inv["id"]) if move is not None else None
            ongoing = bool(inv["status"] == "pending" and owner is not None
                and owner.owner_live and owner.attempt_open and owner.state == state
                and not state.failed_cleanup and state.attempts
                and state.attempts[-1].possible_launch and not state.attempts[-1].settled
                and all(not a.possible_launch or (a.settled and a.quiescent) for a in state.attempts[:-1]))
            if not ongoing:
                return False
        elif inv["status"] != "pending" and not state.producer_settled:
            return False
    return True
