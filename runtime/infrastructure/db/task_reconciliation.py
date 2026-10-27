"""Two closed historical remedies; no generic failed-root authority is introduced.

Callers own the registry/session lease and sorted root gates before entering
the Database RLock. Historical validation never depends on a live session.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from runtime.infrastructure.task_pause_controls import validate_pause_schema

ACTION = "failed_continuation_reconciled"
CAP = 4096
SQLITE_INTEGER_MAX = 2**63 - 1
SCOPE = {
    "org": "happyranch", "team": "engineering", "manager": "engineering_manager",
    "owner": "TASK-10725", "owner_origin": "THR-299", "lineage_thread": "THR-293",
    "predecessor": "TASK-10343", "successor": "TASK-10554", "current": "TASK-10565",
    "older": "TASK-10319", "dev_child": "TASK-10450", "qa_child": "TASK-10329",
}
ROOT_SEQS = {"TASK-10319": 15, "TASK-10343": 23, "TASK-10554": 54,
             "TASK-10565": 62, "TASK-10725": 5}

# Literal v1 definitions above remain byte/meaning compatible. Selection never
# mutates them, including when a historical reader decodes another producer.
V2_SCOPE = MappingProxyType({
    "org": "happyranch", "team": "engineering", "manager": "engineering_manager",
    "owner": "TASK-13286", "owner_origin": "THR-291", "lineage_thread": "THR-291",
    "predecessor": "TASK-13112", "successor": "TASK-13203", "current": "TASK-13217",
    "qa_child": "TASK-13184",
})
V2_ROOT_SEQS = MappingProxyType({"TASK-13112": 496, "TASK-13203": 511,
                                "TASK-13217": 519, "TASK-13286": 526})
V2_DISPATCH_PINS = MappingProxyType({"TASK-13112": (495, 412325), "TASK-13203": (510, 417037),
                                    "TASK-13217": (518, 417747), "TASK-13286": (525, 422353)})


def selected_scope(version: int) -> Mapping[str, str]:
    require(type(version) is int and version in (1, 2), "invalid_request", 422)
    return MappingProxyType(SCOPE) if version == 1 else V2_SCOPE


def gated_roots(version: int) -> tuple[str, ...]:
    scope = selected_scope(version)
    return tuple(sorted({scope["owner"], scope["current"]} if version == 1 else V2_ROOT_SEQS))


class ReconciliationError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReconcileFailedContinuationBody(ClosedModel):
    version: StrictInt = Field(ge=1, le=2)
    operation_key: StrictStr = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    existing_successor_id: StrictStr
    current_descendant_id: StrictStr
    thread_id: StrictStr
    operator_task_id: StrictStr
    session_id: StrictStr
    expected_state_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class OperatorBinding(ClosedModel):
    task_id: str
    agent: str
    session_start_audit_id: int = Field(gt=0)
    session_start_row_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_purpose: Literal["manager_decision"]


class ReconciliationReceipt(ClosedModel):
    audit_id: int
    timestamp: str
    task_id: str
    agent: str
    action: Literal["failed_continuation_reconciled"]
    version: Literal[1, 2]
    operation_key: str
    canonical_command: str
    command_sha256: str
    state_digest: str
    operator_binding: OperatorBinding
    payload_sha256: str


class ReconciliationResponse(ClosedModel):
    version: Literal[1]
    org: str
    receipt: ReconciliationReceipt
    receipt_sha256: str
    replayed: bool


class RetryVerificationResponse(ClosedModel):
    version: Literal[1]
    mode: Literal["retry"]
    org: str
    parent_id: str
    target_agent: str
    failed_child_id: str | None
    valid: bool
    path: list[str]
    reason: str | None
    advisory: Literal[True]


class ReconciliationTarget(ClosedModel):
    team: str
    manager: str
    existing_successor_id: str
    current_descendant_id: str
    thread_id: str
    operator_task_id: str


class PreflightResponse(ClosedModel):
    version: Literal[1, 2]
    mode: Literal["preflight"]
    org: str
    predecessor_id: str
    operation_key: str
    eligible: Literal[True]
    target: ReconciliationTarget
    operator_binding: OperatorBinding
    expected_state_digest: str
    advisory: Literal[True]


class HistoricalReceiptResponse(ClosedModel):
    version: Literal[1]
    mode: Literal["receipt"]
    org: str
    historical: Literal[True]
    receipt: ReconciliationReceipt
    receipt_sha256: str


class RefusalDetail(ClosedModel):
    code: str


class ReconciliationRefusal(ClosedModel):
    detail: RefusalDetail


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text_digest(value: str | None) -> str | None:
    return None if value is None else hashlib.sha256(value.encode("utf-8")).hexdigest()


def strict_json(raw: str | bytes, code: str = "malformed_evidence", *, allow_floats: bool = False) -> dict:
    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_number(_: str) -> None:
        raise ValueError("noninteger number")

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        value = json.loads(raw, object_pairs_hook=pairs, parse_float=float if allow_floats else reject_number,
                           parse_constant=reject_number)
        if not isinstance(value, dict):
            raise ValueError("object required")
        canonical(value).encode("utf-8")
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReconciliationError(code, 422 if code == "invalid_request" else 409) from exc


def identifier(value: Any) -> str:
    try:
        if type(value) is not str or not 0 < len(value.encode("utf-8")) <= 256:
            raise ValueError("invalid ID")
    except (ValueError, UnicodeError) as exc:
        raise ReconciliationError("invalid_request", 422) from exc
    return value


def request_body(raw: str | bytes) -> dict:
    value = strict_json(raw, "invalid_request")
    try:
        body = ReconcileFailedContinuationBody.model_validate(value).model_dump()
    except ValidationError as exc:
        raise ReconciliationError("invalid_request", 422) from exc
    for key in ("existing_successor_id", "current_descendant_id", "thread_id", "operator_task_id", "session_id"):
        identifier(body[key])
    return body


def require(condition: bool, code: str = "malformed_evidence", status: int = 409) -> None:
    if not condition:
        raise ReconciliationError(code, status)


def keys(value: Any, names: str) -> None:
    require(isinstance(value, dict) and set(value) == set(names.split()))


def aware_timestamp(value: Any) -> None:
    try:
        require(type(value) is str and datetime.fromisoformat(value).utcoffset() is not None)
    except (ValueError, TypeError) as exc:
        raise ReconciliationError("malformed_evidence") from exc


def command(org: str, predecessor: str, body: dict) -> dict:
    return {"org": org, "predecessor_id": predecessor,
            **{k: v for k, v in body.items() if k != "session_id"}}


def exact_scope(org: str, predecessor: str, body: dict) -> None:
    scope = selected_scope(body["version"])
    require(org == scope["org"] and predecessor == scope["predecessor"]
            and body["existing_successor_id"] == scope["successor"]
            and body["current_descendant_id"] == scope["current"]
            and body["thread_id"] == scope["lineage_thread"]
            and body["operator_task_id"] == scope["owner"], "operation_scope", 403)


class TaskReconciliation:
    """Synchronous domain validator over the caller's ONE owned transaction."""

    def __init__(self, db: Any, version: int = 1) -> None:
        selected_scope(version)
        self.db, self.conn = db, db._conn
        self._version = version

    @property
    def version(self) -> int:
        return self._version

    @property
    def scope(self) -> Mapping[str, str]:
        return selected_scope(self.version)

    @property
    def root_seqs(self) -> Mapping[str, int]:
        return MappingProxyType(ROOT_SEQS) if self.version == 1 else V2_ROOT_SEQS

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        rows = [dict(r) for r in self.conn.execute(sql + " LIMIT 4097", args)]
        require(len(rows) <= CAP, "evidence_scan_limit")
        return rows

    def task(self, task_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        require(row is not None, "unknown_task", 404)
        return dict(row)

    def root(self, task_id: str, thread: str) -> dict:
        row = self.task(task_id)
        require(row["parent_task_id"] is None and row["task_type"] == "task"
                and row["assigned_agent"] == self.scope["manager"] and row["team"] == self.scope["team"]
                and row["dispatched_from_thread_id"] == thread and row["revisit_of_task_id"] is None)
        return row

    def _session_payload(self, row: dict) -> dict:
        require(type(row["payload"]) is str, "original_session_evidence")
        payload = strict_json(row["payload"], "original_session_evidence", allow_floats=True)
        require(type(row["id"]) is int and row["id"] > 0
                and row["task_id"] == self.scope["owner"] and row["action"] == "session_start"
                and row["agent"] == self.scope["manager"], "original_session_evidence")
        try:
            aware_timestamp(row["timestamp"])
            identifier(payload.get("session_id"))
            identifier(payload.get("invocation_purpose"))
        except ReconciliationError as exc:
            raise ReconciliationError("original_session_evidence") from exc
        return payload

    def original_session(self, sid: str) -> dict:
        # Validate every relevant owner row before selecting a SID: malformed
        # history or wrong-agent evidence must not vanish behind SID filtering.
        rows = self.rows("SELECT * FROM audit_log WHERE task_id=? AND action='session_start' ORDER BY id",
                         (self.scope["owner"],))
        matches = []
        for row in rows:
            payload = self._session_payload(row)
            if payload.get("session_id") == sid:
                matches.append((row, payload))
        require(len(matches) == 1, "original_session_evidence")
        row, payload = matches[0]
        require(payload.get("invocation_purpose") == "manager_decision", "ordinary_purpose_required", 403)
        return {"task_id": self.scope["owner"], "agent": self.scope["manager"],
                "session_start_audit_id": row["id"], "session_start_row_digest": digest(row),
                "session_id_sha256": text_digest(sid), "invocation_purpose": "manager_decision"}

    def historical_session(self, binding: dict) -> None:
        keys(binding, "task_id agent session_start_audit_id session_start_row_digest session_id_sha256 invocation_purpose")
        try:
            OperatorBinding.model_validate(binding)
        except ValidationError as exc:
            raise ReconciliationError("original_session_evidence") from exc
        row = self.conn.execute("SELECT * FROM audit_log WHERE id=?",
                                (binding["session_start_audit_id"],)).fetchone()
        require(row is not None, "original_session_evidence")
        payload = self._session_payload(dict(row))
        try:
            require(canonical(self.original_session(payload.get("session_id"))) == canonical(binding), "original_session_evidence")
        except ReconciliationError as exc:
            raise ReconciliationError("original_session_evidence") from exc

    def live_operator(self, org: Any, sid: str) -> tuple[dict, dict, dict]:
        try:
            manager = org.teams.manager_for_team(self.scope["team"])
            executable = org.teams.executable_manager_for_team(self.scope["team"])
        except (KeyError, ValueError) as exc:
            raise ReconciliationError("manager_mismatch", 403) from exc
        registry = {"team": self.scope["team"], "manager_kind": manager.kind,
                    "manager_principal": manager.principal, "executable_manager": executable}
        require(manager.kind == "agent" and manager.principal == self.scope["manager"]
                and executable == self.scope["manager"], "manager_mismatch", 403)
        owner = self.root(self.scope["owner"], self.scope["owner_origin"])
        require(owner["status"] == "in_progress" and owner["block_kind"] is None
                and owner["cancelled_at"] is None, "operator_inactive")
        sessions = org.sessions
        require(sessions is not None, "control_unavailable", 503)
        require(owner["current_session_id"] == sid
                and sessions.get_active(self.scope["owner"], self.scope["manager"]) == sid
                and sessions.get_context_by_session(sid) == (org.slug, self.scope["owner"], self.scope["manager"]),
                "session_mismatch")
        require(not sessions.is_recovery_session(self.scope["owner"], self.scope["manager"], sid),
                "recovery_purpose_forbidden", 403)
        sql = "SELECT 1 FROM task_results WHERE task_id=? AND session_id=?"
        args = (self.scope["owner"], sid)
        if self.version == 1:
            sql += " AND agent=?"
            args += (self.scope["manager"],)
        result = self.conn.execute(sql, args).fetchone()
        require(result is None, "operator_inactive")
        return owner, self.original_session(sid), registry

    def dispatch(self, root: dict) -> dict:
        thread = root["dispatched_from_thread_id"]
        rows = self.rows("SELECT * FROM thread_invocations WHERE dispatched_task_id=? ORDER BY id", (root["id"],))
        require(len(rows) == 1, "invocation_binding")
        inv = rows[0]
        require(inv["thread_id"] == thread and inv["agent_name"] == self.scope["manager"]
                and inv["purpose"] in ("reply", "bootstrap")
                and inv["status"] in ("pending", "consumed", "declined", "timeout", "failed"), "invocation_binding")
        trigger = self.conn.execute("SELECT * FROM thread_messages WHERE thread_id=? AND seq=?",
                                    (thread, inv["triggering_seq"])).fetchone()
        require(trigger is not None and self.conn.execute("SELECT 1 FROM threads WHERE id=?", (thread,)).fetchone() is not None,
                "invocation_binding")
        if self.version == 2:
            triggering_seq, _ = V2_DISPATCH_PINS[root["id"]]
            require(type(inv["id"]) is int and inv["id"] > 0
                    and type(inv["triggering_seq"]) is int and inv["triggering_seq"] == triggering_seq
                    and trigger["thread_id"] == thread and trigger["seq"] == triggering_seq
                    and trigger["kind"] == "message" and trigger["speaker"] == "founder", "invocation_binding")
            aware_timestamp(trigger["created_at"])
        expected = {"task_id": root["id"], "dispatcher": self.scope["manager"],
                    "target_agent": self.scope["manager"], "team": self.scope["team"]}
        audits = []
        for row in self.rows("SELECT * FROM audit_log WHERE task_id=? AND action='thread_dispatch' ORDER BY id", (thread,)):
            payload = strict_json(row["payload"])
            if payload.get("task_id") == root["id"]:
                require(row["agent"] == self.scope["manager"] and all(payload.get(k) == v for k, v in expected.items()),
                        "invocation_binding")
                if self.version == 2:
                    require(type(row["id"]) is int and row["id"] == V2_DISPATCH_PINS[root["id"]][1]
                            and type(row["payload"]) is str, "invocation_binding")
                    aware_timestamp(row["timestamp"])
                audits.append({"id": row["id"], "row_digest": digest(row)})
        messages = []
        for row in self.rows("SELECT * FROM thread_messages WHERE thread_id=? AND kind='system' ORDER BY seq", (thread,)):
            if row["system_payload_json"] is None:
                continue
            payload = strict_json(row["system_payload_json"])
            if payload.get("task_id") == root["id"] and payload.get("kind_tag") == "task_dispatched":
                require(row["speaker"] == self.scope["manager"] and row["seq"] > inv["triggering_seq"]
                        and all(payload.get(k) == v for k, v in expected.items()), "invocation_binding")
                messages.append({"seq": row["seq"], "row_digest": digest({k: row[k] for k in
                                 ("thread_id", "seq", "speaker", "kind", "system_payload_json")})})
        require(len(audits) == len(messages) == 1 and messages[0]["seq"] == self.root_seqs[root["id"]], "invocation_binding")
        result = {"root_id": root["id"], "thread_id": thread, "invocation_id": inv["id"],
                "invocation_digest": digest({k: inv[k] for k in
                     ("id", "thread_id", "agent_name", "dispatched_task_id", "triggering_seq", "purpose")}),
                "triggering_seq": inv["triggering_seq"], "trigger_message_digest": digest(dict(trigger)),
                "dispatch_audits": audits, "dispatch_messages": messages}
        if self.version == 2:
            require(type(root["brief"]) is str, "invocation_binding")
            result["original_brief_sha256"] = text_digest(root["brief"])
        return result

    @staticmethod
    def inventory_shape(payload: dict) -> None:
        """Strict saved primitives and consistency; original authority is later.

        This pass deliberately performs no DB/edge/receipt calls, so malformed
        unrelated envelopes cannot disappear and full readers cannot recurse.
        """
        version, snapshot = payload["version"], payload["snapshot"]

        def strings(record: dict, names: str, *, nullable: bool = False) -> None:
            for name in names.split():
                require((nullable and record[name] is None) or type(record[name]) is str)

        def sha256(record: dict, names: str, *, nullable: bool = False) -> None:
            for name in names.split():
                value = record[name]
                require((nullable and value is None) or
                        (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None))

        def positive_integer(value: Any) -> None:
            require(type(value) is int and 0 < value <= SQLITE_INTEGER_MAX)

        def reference(record: dict, number: str) -> None:
            keys(record, number + " row_digest")
            positive_integer(record[number])
            sha256(record, "row_digest")

        def binding(record: dict) -> None:
            keys(record, "task_id agent session_start_audit_id session_start_row_digest session_id_sha256 invocation_purpose")
            strings(record, "task_id agent invocation_purpose")
            positive_integer(record["session_start_audit_id"])
            sha256(record, "session_start_row_digest session_id_sha256")
            require(record["invocation_purpose"] == "manager_decision")

        def dispatch_shape(record: dict) -> None:
            keys(record, "root_id thread_id invocation_id invocation_digest triggering_seq trigger_message_digest dispatch_audits dispatch_messages" +
                 (" original_brief_sha256" if version == 2 else ""))
            strings(record, "root_id thread_id")
            positive_integer(record["invocation_id"])
            positive_integer(record["triggering_seq"])
            sha256(record, "invocation_digest trigger_message_digest" +
                   (" original_brief_sha256" if version == 2 else ""))
            for name, number in (("dispatch_audits", "id"), ("dispatch_messages", "seq")):
                require(type(record[name]) is list and len(record[name]) == 1)
                reference(record[name][0], number)

        def edge_shape(record: dict) -> None:
            keys(record, "producer predecessor_id successor_id records")
            strings(record, "producer predecessor_id successor_id")
            require(record["producer"] in ("manager_supersession", "escalation_superseded"))
            require(type(record["records"]) is list and len(record["records"]) == 1)
            reference(record["records"][0], "id")

        claim = payload["operator_claim"]
        keys(claim, "task_id assigned_agent team current_session_id_sha256 status block_kind cancelled_at step revision active_chain_sha256 active_fanout_sha256 note_sha256 blocked_on_job_ids_sha256 result_row_id")
        strings(claim, "task_id assigned_agent team status")
        strings(claim, "block_kind cancelled_at", nullable=True)
        sha256(claim, "current_session_id_sha256")
        sha256(claim, "active_chain_sha256 active_fanout_sha256 note_sha256 blocked_on_job_ids_sha256", nullable=True)
        for name in ("step", "revision"):
            require(type(claim[name]) is int and 0 <= claim[name] <= SQLITE_INTEGER_MAX)
        require(claim["result_row_id"] is None)
        # Validate each saved copy: dict equality alone equates bool with int.
        binding(payload["operator_binding"])
        binding(snapshot["operator_binding"])
        keys(snapshot["registry"], "team manager_kind manager_principal executable_manager")
        strings(snapshot["registry"], "team manager_kind manager_principal executable_manager")
        origin = snapshot["origin"]
        keys(origin, "thread_id owner_dispatch composed_by composed_from_task_id" if version == 1
             else "thread_id owner_dispatch source_approval recovery_trigger")
        strings(origin, "thread_id" + (" composed_by composed_from_task_id" if version == 1 else ""))
        dispatch_shape(origin["owner_dispatch"])
        for name, minimum, maximum in (("tasks", 7 if version == 1 else 5, 23 if version == 1 else 5),
                                       ("holds", 2 if version == 1 else 4, 2 if version == 1 else 4)):
            require(type(snapshot[name]) is list and minimum <= len(snapshot[name]) <= maximum)
        for task in snapshot["tasks"]:
            TaskReconciliation.projection_shape(task)
        for hold in snapshot["holds"]:
            keys(hold, "root_task_id control_present generation held")
            strings(hold, "root_task_id")
            require(type(hold["control_present"]) is bool and type(hold["generation"]) is int
                    and 0 <= hold["generation"] <= 2**63 - 1 and type(hold["held"]) is bool)
        for records in (payload["dispatch_bindings"], snapshot["dispatch_bindings"]):
            require(type(records) is list and len(records) == (5 if version == 1 else 4))
            for record in records:
                dispatch_shape(record)
        for records in (payload["old_edge_bindings"], snapshot["edge_bindings"]):
            require(type(records) is list and ((2 <= len(records) <= 18) if version == 1 else len(records) == 1))
            for record in records:
                edge_shape(record)
        local = snapshot["local_retry"]
        keys(local, "parent_id agents" + (" observations" if version == 2 else ""))
        strings(local, "parent_id")
        require(type(local["agents"]) is list and len(local["agents"]) == 2)
        for item in local["agents"]:
            keys(item, "target_agent failed_children retries")
            strings(item, "target_agent")
            for name, fields in (("failed_children", "id assigned_agent status parent_task_id revisit_of_task_id"),
                                 ("retries", "id revisit_of_task_id")):
                require(type(item[name]) is list and len(item[name]) <= CAP)
                for row in item[name]:
                    keys(row, fields)
                    if name == "failed_children":
                        strings(row, "id assigned_agent status parent_task_id")
                        strings(row, "revisit_of_task_id", nullable=True)
                    else:
                        strings(row, "id revisit_of_task_id")
        if version == 2:
            for name in ("source_approval", "recovery_trigger"):
                reference(origin[name], "seq")
            require(type(local["observations"]) is list and len(local["observations"]) == 4)
            for observation in local["observations"]:
                keys(observation, "root_id children results")
                strings(observation, "root_id")
                for name in ("children", "results"):
                    require(type(observation[name]) is list and len(observation[name]) <= CAP)
                    for row in observation[name]:
                        keys(row, "id projection" if name == "children" else "id task_id agent session_id_sha256 row_digest")
                        if name == "children":
                            strings(row, "id")
                            TaskReconciliation.projection_shape(row["projection"])
                        else:
                            positive_integer(row["id"])
                            strings(row, "task_id agent")
                            sha256(row, "session_id_sha256 row_digest")

        # Equality never supplies primitive validation (True == 1). All copies
        # above must pass independently before their saved relationships close.
        require(canonical(payload["operator_binding"]) == canonical(snapshot["operator_binding"])
                and canonical(payload["dispatch_bindings"]) == canonical(snapshot["dispatch_bindings"])
                and canonical(payload["old_edge_bindings"]) == canonical(snapshot["edge_bindings"]))
        TaskReconciliation.saved_consistency(snapshot, claim, version)

    @staticmethod
    def saved_consistency(snapshot: dict, claim: dict, version: int) -> None:
        """Pure producer relationships, after strict shapes; no original reads.

        This closes EVERY scanned envelope, including disjoint history. Original
        SQL rows, topology and fresh operator eligibility are separate authority.
        """
        scope = selected_scope(version)
        root_seqs = ROOT_SEQS if version == 1 else V2_ROOT_SEQS
        require(snapshot["registry"] == {"team": scope["team"], "manager_kind": "agent",
                "manager_principal": scope["manager"], "executable_manager": scope["manager"]})
        tasks = snapshot["tasks"]
        ids = [t["id"] for t in tasks]
        children = {scope["qa_child"]} | ({scope["dev_child"]} if version == 1 else set())
        require(ids == sorted(set(ids)) and set(root_seqs) | children <= set(ids))
        if version == 2:
            require(ids == sorted([*root_seqs, scope["qa_child"]]))
        frozen = {t["id"]: t for t in tasks}
        for task in tasks:
            if task["id"] not in children:
                require(task["parent_task_id"] is None and task["task_type"] == "task"
                        and task["revisit_of_task_id"] is None and task["assigned_agent"] == scope["manager"]
                        and task["team"] == scope["team"] and task["dispatched_from_thread_id"] ==
                        (scope["owner_origin"] if task["id"] == scope["owner"] else scope["lineage_thread"]))
        statuses = [("predecessor", "failed"), ("successor", "superseded")]
        statuses += [("older", "superseded")] if version == 1 else [("current", "failed")]
        for name, status in statuses:
            require(frozen[scope[name]]["status"] == status)
        child_contexts = [(scope["qa_child"], scope["older"], "qa_engineer", None),
                          (scope["dev_child"], scope["predecessor"], "dev_agent", "TASK-10409")] if version == 1 else [
                          (scope["qa_child"], scope["predecessor"], "qa_engineer", "TASK-13135")]
        for task_id, parent, agent, revisit in child_contexts:
            child = frozen[task_id]
            require(child["status"] == "failed" and child["parent_task_id"] == parent
                    and child["assigned_agent"] == agent and child["revisit_of_task_id"] == revisit)
            if version == 2:
                require(child["task_type"] == "subtask")
        owner, binding = frozen[scope["owner"]], snapshot["operator_binding"]
        require(binding["task_id"] == scope["owner"] and binding["agent"] == scope["manager"]
                and owner["status"] == "in_progress" and owner["block_kind"] is None and owner["cancelled_at"] is None
                and owner["current_session_id_sha256"] == binding["session_id_sha256"])
        expected_claim = {"task_id": owner["id"], "assigned_agent": owner["assigned_agent"], "team": owner["team"],
                          "current_session_id_sha256": owner["current_session_id_sha256"], "status": owner["status"],
                          "block_kind": owner["block_kind"], "cancelled_at": owner["cancelled_at"],
                          "step": owner["orchestration_step_count"], "revision": owner["revision_count"],
                          **{k + "_sha256": owner[k + "_sha256"] for k in
                             ("active_chain", "active_fanout", "note", "blocked_on_job_ids")}, "result_row_id": None}
        require(canonical(claim) == canonical(expected_claim))
        if version == 1:
            require(frozen[scope["current"]]["status"] == "in_progress"
                    and frozen[scope["current"]]["cancelled_at"] is None)
        for hold, root_id in zip(snapshot["holds"], gated_roots(version)):
            require(hold["root_task_id"] == root_id and hold["held"] is False
                    and hold["generation"] % 2 == 0 and (hold["control_present"] or hold["generation"] == 0))

        dispatches = snapshot["dispatch_bindings"]
        require([d["root_id"] for d in dispatches] == sorted(root_seqs))
        for dispatch in dispatches:
            root_id = dispatch["root_id"]
            require(dispatch["thread_id"] == frozen[root_id]["dispatched_from_thread_id"]
                    and dispatch["dispatch_messages"][0]["seq"] == root_seqs[root_id]
                    and dispatch["triggering_seq"] < dispatch["dispatch_messages"][0]["seq"])
            if version == 2:
                require(dispatch["triggering_seq"] == V2_DISPATCH_PINS[root_id][0]
                        and dispatch["dispatch_audits"][0]["id"] == V2_DISPATCH_PINS[root_id][1])
        require(len({d["invocation_id"] for d in dispatches}) == len(dispatches)
                and len({d["dispatch_audits"][0]["id"] for d in dispatches}) == len(dispatches))
        by_root = {d["root_id"]: d for d in dispatches}
        origin = snapshot["origin"]
        require(origin["thread_id"] == scope["owner_origin"]
                and canonical(origin["owner_dispatch"]) == canonical(by_root[scope["owner"]]))
        if version == 1:
            require(origin["composed_by"] == scope["manager"] and origin["composed_from_task_id"] == scope["current"])
        else:
            for name, root_id in (("source_approval", scope["current"]), ("recovery_trigger", scope["owner"])):
                require(origin[name] == {"seq": by_root[root_id]["triggering_seq"],
                                         "row_digest": by_root[root_id]["trigger_message_digest"]})

        edges = snapshot["edge_bindings"]
        require(edges == sorted(edges, key=lambda e: (e["producer"], e["predecessor_id"], e["successor_id"], e["records"][0]["id"])))
        require(len({(e["producer"], e["records"][0]["id"]) for e in edges}) == len(edges))
        audit_ids = {d["dispatch_audits"][0]["id"] for d in dispatches}
        require(binding["session_start_audit_id"] not in audit_ids)
        for edge in edges:
            if edge["producer"] == "escalation_superseded":
                require(edge["records"][0]["id"] not in audit_ids | {binding["session_start_audit_id"]})
        if version == 2:
            edge = edges[0]
            require(edge["producer"] == "escalation_superseded" and edge["predecessor_id"] == scope["successor"]
                    and edge["successor_id"] == scope["current"] and edge["records"][0]["id"] == 417748)
        else:
            # The v1 producer walks a variable legitimate incoming path. Keep
            # every such member, closing the proposed gap without DB traversal.
            incoming = {scope["successor"]: scope["predecessor"]}
            outgoing = {scope["predecessor"]: scope["successor"]}
            pairs = set()
            for edge in edges:
                pred, succ = edge["predecessor_id"], edge["successor_id"]
                require(succ not in incoming and pred not in outgoing)
                incoming[succ], outgoing[pred] = pred, succ
                pairs.add((pred, succ))
            require({(scope["older"], scope["predecessor"]), (scope["successor"], scope["current"])} <= pairs)
            path, cursor = [], scope["current"]
            while True:
                require(cursor not in path and len(path) < 20)
                path.append(cursor)
                if cursor not in incoming:
                    break
                cursor = incoming[cursor]
            require(set(path) == set(ids) - {scope["owner"], *children}
                    and set(outgoing) == set(path[1:]) and scope["older"] in path)
            for edge in edges:
                require(frozen[edge["predecessor_id"]]["status"] == "superseded")

        local = snapshot["local_retry"]
        parent = scope["current"] if version == 1 else scope["owner"]
        require(local["parent_id"] == parent)
        require(canonical(local["agents"][0]["retries"]) == canonical(local["agents"][1]["retries"]))
        failed_ids = set()
        for item, agent in zip(local["agents"], ("dev_agent", "qa_engineer")):
            require(item["target_agent"] == agent)
            for name in ("failed_children", "retries"):
                rows = item[name]
                require([r["id"] for r in rows] == sorted({r["id"] for r in rows}))
                for row in rows:
                    if name == "failed_children":
                        require(row["assigned_agent"] == agent and row["status"] == "failed"
                                and row["parent_task_id"] == parent and row["id"] not in failed_ids)
                        failed_ids.add(row["id"])
                    if version == 2 and name == "retries":
                        # Retain the selected v2 reader's identifier bound, now
                        # before relevance and with saved-evidence attribution.
                        try:
                            identifier(row["revisit_of_task_id"])
                        except ReconciliationError as exc:
                            raise ReconciliationError("malformed_evidence") from exc
                    if row["id"] in frozen:
                        require(frozen[row["id"]]["parent_task_id"] == parent
                                and all(row[k] == frozen[row["id"]][k] for k in row))
            retries = {r["id"]: r["revisit_of_task_id"] for r in item["retries"]}
            for child in item["failed_children"]:
                require(retries.get(child["id"]) == child["revisit_of_task_id"])

        if version == 2:
            observations = local["observations"]
            require([o["root_id"] for o in observations] == sorted(root_seqs))
            child_ids, result_ids = set(), set()
            for observation in observations:
                root_id = observation["root_id"]
                for name in ("children", "results"):
                    rows = observation[name]
                    require([r["id"] for r in rows] == sorted({r["id"] for r in rows}))
                    for row in rows:
                        if name == "children":
                            projection = row["projection"]
                            require(row["id"] == projection["id"] and projection["parent_task_id"] == root_id
                                    and row["id"] not in child_ids)
                            child_ids.add(row["id"])
                            if row["id"] in frozen:
                                require(canonical(projection) == canonical(frozen[row["id"]]))
                        else:
                            require(row["task_id"] == root_id and row["id"] not in result_ids)
                            result_ids.add(row["id"])
                            if root_id == scope["owner"]:
                                require(row["session_id_sha256"] != binding["session_id_sha256"])
                # The SQL child census contains the top-level QA projection and
                # both local queries. They are observations of the SAME capture.
                require({t["id"] for t in tasks if t["parent_task_id"] == root_id}
                        <= {r["id"] for r in observation["children"]})
                if root_id == parent:
                    projected = [r["projection"] for r in observation["children"]]
                    for item in local["agents"]:
                        require(item["failed_children"] == [{k: t[k] for k in
                                ("id", "assigned_agent", "status", "parent_task_id", "revisit_of_task_id")}
                                for t in projected if t["status"] == "failed" and t["assigned_agent"] == item["target_agent"]])
                        require(item["retries"] == [{"id": t["id"], "revisit_of_task_id": t["revisit_of_task_id"]}
                                for t in projected if t["revisit_of_task_id"] is not None])

    def operations(self) -> list[tuple[dict, dict]]:
        """Structural inventory FIRST; no edge traversal or recursive readers."""
        result, operation_keys = [], set()
        for row in self.rows("SELECT * FROM audit_log WHERE action=? ORDER BY id", (ACTION,)):
            require(type(row["payload"]) is str)
            payload = strict_json(row["payload"])
            keys(payload, "version operation_key canonical_command command_sha256 state_digest operator_binding operator_claim dispatch_bindings old_edge_bindings snapshot")
            require(type(payload["version"]) is int and payload["version"] in (1, 2))
            require(type(payload["canonical_command"]) is str)
            cmd = strict_json(payload["canonical_command"])
            keys(cmd, "version operation_key org predecessor_id existing_successor_id current_descendant_id thread_id operator_task_id expected_state_digest")
            try:
                request_body(canonical({k: v for k, v in cmd.items() if k not in ("org", "predecessor_id")} | {"session_id": "historical"}))
            except ReconciliationError as exc:
                raise ReconciliationError("malformed_evidence") from exc
            require(type(cmd["org"]) is str and type(cmd["predecessor_id"]) is str)
            exact_scope(cmd["org"], cmd["predecessor_id"], cmd)
            scope = selected_scope(payload["version"])
            require(type(cmd["version"]) is int and payload["version"] == cmd["version"]
                    and payload["operation_key"] == cmd["operation_key"]
                    and canonical(cmd) == payload["canonical_command"] and digest(cmd) == payload["command_sha256"]
                    and cmd["expected_state_digest"] == payload["state_digest"]
                    and type(row["id"]) is int and 0 < row["id"] <= SQLITE_INTEGER_MAX
                    and row["task_id"] == scope["predecessor"] and row["agent"] == scope["manager"])
            aware_timestamp(row["timestamp"])
            keys(payload["snapshot"], "scope registry tasks operator_binding operator_result_present origin holds dispatch_bindings edge_bindings local_retry operation_ownership")
            require(payload["snapshot"]["scope"] == {**scope, "operation_key": cmd["operation_key"]}
                    and digest(payload["snapshot"]) == payload["state_digest"]
                    and payload["snapshot"]["operator_binding"] == payload["operator_binding"]
                    and payload["snapshot"]["dispatch_bindings"] == payload["dispatch_bindings"]
                    and payload["snapshot"]["edge_bindings"] == payload["old_edge_bindings"]
                    and payload["snapshot"]["operator_result_present"] is False
                    and payload["snapshot"]["operation_ownership"] == [])
            try:
                OperatorBinding.model_validate(payload["operator_binding"])
            except ValidationError as exc:
                raise ReconciliationError("malformed_evidence") from exc
            self.inventory_shape(payload)
            require(payload["operation_key"] not in operation_keys, "operation_conflict")
            operation_keys.add(payload["operation_key"])
            result.append((row, payload))
        return result

    def unowned_pair(self) -> None:
        require(not self.edge_candidates(outgoing=self.scope["predecessor"])
                and not self.edge_candidates(incoming=self.scope["successor"]), "pair_owned")

    def edge_candidates(self, *, incoming: str | None = None, outgoing: str | None = None) -> list[dict]:
        """Bounded union; retain conflicting producer rows, never deduplicate."""
        require((incoming is None) != (outgoing is None))
        column, selected = ("successor_task_id", incoming) if incoming else ("predecessor_task_id", outgoing)
        edges = []
        for row in self.rows(f"SELECT * FROM manager_supersessions WHERE {column}=? ORDER BY id", (selected,)):
            edges.append({"producer": "manager_supersession", "predecessor_id": row["predecessor_task_id"],
                          "successor_id": row["successor_task_id"], "records": [{"id": row["id"], "row_digest": digest(row)}]})
        if incoming:
            audits = self.rows("SELECT * FROM audit_log WHERE action='escalation_superseded' AND "
                               "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.successor_root') END=? ORDER BY id", (incoming,))
        else:
            audits = self.rows("SELECT * FROM audit_log WHERE action='escalation_superseded' AND task_id=? ORDER BY id", (outgoing,))
        for row in audits:
            payload = strict_json(row["payload"])
            successor = payload.get("successor_root")
            require(row["agent"] == "founder" and type(successor) is str and bool(successor))
            edges.append({"producer": "escalation_superseded", "predecessor_id": row["task_id"],
                          "successor_id": successor, "records": [{"id": row["id"], "row_digest": digest(row)}]})
        for row, payload in self.operations():
            cmd = strict_json(payload["canonical_command"])
            pred, succ = cmd.get("predecessor_id"), cmd.get("existing_successor_id")
            if incoming == succ or outgoing == pred:
                edges.append({"producer": ACTION, "predecessor_id": pred, "successor_id": succ,
                              "records": [{"id": row["id"], "row_digest": digest(row)}]})
        return sorted(edges, key=lambda e: (e["producer"], e["predecessor_id"], e["successor_id"], e["records"][0]["id"]))

    def old_edge(self, predecessor: str, successor: str) -> dict:
        edges = self.edge_candidates(incoming=successor)
        require(len(edges) == 1 and edges[0]["predecessor_id"] == predecessor, "ambiguous_predecessor")
        edge = edges[0]
        require(self.edge_candidates(outgoing=predecessor) == edges, "competing_successors")
        pred, succ = self.task(predecessor), self.task(successor)
        require(pred["status"] == "superseded", "predecessor_not_superseded")
        # The existing validators retain their exact meanings; cap their
        # companion evidence before invoking them in the new producer.
        for task_id, action in ((predecessor, "manager_supersession"), (successor, "manager_supersession"),
                                (predecessor, "escalation_resolved"), (successor, "revisit_of"),
                                (predecessor, "revisit_spawned")):
            self.rows("SELECT id FROM audit_log WHERE task_id=? AND action=? ORDER BY id", (task_id, action))
        if edge["producer"] == "manager_supersession":
            records = self.conn.execute("SELECT * FROM manager_supersessions WHERE predecessor_task_id=? AND successor_task_id=?",
                                        (predecessor, successor)).fetchall()
            self.db._retry_manager_edge(pred, succ, records)
        elif edge["producer"] == "escalation_superseded":
            row = self.conn.execute("SELECT * FROM audit_log WHERE id=?", (edge["records"][0]["id"],)).fetchone()
            self.db._retry_escalation_edge(pred, succ, strict_json(row["payload"]))
        else:
            raise ReconciliationError("ambiguous_predecessor")
        return edge

    @staticmethod
    def task_projection(row: dict) -> dict:
        plain = ("id status task_type parent_task_id revisit_of_task_id assigned_agent team dispatched_from_thread_id "
                 "block_kind cancelled_at revision_count orchestration_step_count").split()
        result = {k: row[k] for k in plain}
        for key in ("current_session_id", "active_chain", "active_fanout", "blocked_on_job_ids", "note"):
            result[key + "_sha256"] = text_digest(row[key])
        return result

    def snapshot(self, org: Any, body: dict, owner: dict, binding: dict, registry: dict) -> dict:
        if self.version == 2:
            return self.snapshot_v2(org, body, owner, binding, registry)
        validate_pause_schema(self.conn, org_slug=org.slug)
        roots = [self.root(task_id, self.scope["owner_origin"] if task_id == self.scope["owner"] else self.scope["lineage_thread"])
                 for task_id in self.root_seqs]
        pred, succ, current, older = (self.task(self.scope[k]) for k in ("predecessor", "successor", "current", "older"))
        require(pred["status"] == "failed" and succ["status"] == "superseded")
        require(current["status"] == "in_progress" and current["cancelled_at"] is None, "stale_state")
        children = [self.task(self.scope["dev_child"]), self.task(self.scope["qa_child"])]
        for row, parent, agent, revisit in ((children[0], pred, "dev_agent", "TASK-10409"),
                                            (children[1], older, "qa_engineer", None)):
            require(row["status"] == "failed" and row["assigned_agent"] == agent
                    and row["parent_task_id"] == parent["id"] and row["revisit_of_task_id"] == revisit)
        origin = self.conn.execute("SELECT * FROM threads WHERE id=?", (self.scope["owner_origin"],)).fetchone()
        require(origin is not None and origin["composed_by"] == self.scope["manager"]
                and origin["composed_from_task_id"] == self.scope["current"], "invocation_binding")
        dispatches = sorted((self.dispatch(root) for root in roots), key=lambda d: d["root_id"])
        edges = [self.old_edge(self.scope["older"], self.scope["predecessor"]),
                 self.old_edge(self.scope["successor"], self.scope["current"])]
        require(not self.edge_candidates(outgoing=self.scope["predecessor"])
                and not self.edge_candidates(incoming=self.scope["successor"]), "pair_owned")
        # Scan the entire bounded incoming lineage and outgoing owner at each
        # vertex. The proposed gap consumes one edge; there can be no branch.
        cursor, visited = current["id"], []
        while True:
            require(cursor not in visited, "lineage_cycle")
            require(len(visited) < 20, "lineage_record_limit_20")
            visited.append(cursor)
            self.root(cursor, self.scope["lineage_thread"])
            if cursor == self.scope["successor"]:
                cursor = self.scope["predecessor"]
                continue
            incoming = self.edge_candidates(incoming=cursor)
            if not incoming:
                break
            require(len(incoming) == 1, "ambiguous_predecessor")
            edge = self.old_edge(incoming[0]["predecessor_id"], cursor)
            if edge not in edges:
                edges.append(edge)
            cursor = edge["predecessor_id"]
        store = getattr(self.db, "_task_pause_store", None)
        require(store is not None and store.org_slug == org.slug, "control_unavailable", 503)
        holds = []
        for root_id in sorted((self.scope["owner"], self.scope["current"])):
            root, row = store.held_uncommitted(root_id)
            require(root["id"] == root_id, "stale_state")
            holds.append({"root_task_id": root_id, "control_present": row is not None,
                          "generation": row["generation"] if row else 0, "held": bool(row and row["held"])})
        local = []
        for agent in ("dev_agent", "qa_engineer"):
            failed = self.rows("SELECT id,assigned_agent,status,parent_task_id,revisit_of_task_id FROM tasks "
                               "WHERE parent_task_id=? AND assigned_agent=? AND status='failed' ORDER BY id",
                               (current["id"], agent))
            retries = self.rows("SELECT id,revisit_of_task_id FROM tasks WHERE parent_task_id=? "
                                "AND revisit_of_task_id IS NOT NULL ORDER BY id", (current["id"],))
            local.append({"target_agent": agent, "failed_children": failed, "retries": retries})
        tasks = {r["id"]: r for r in roots + children}
        for task_id in visited:
            tasks[task_id] = self.task(task_id)
        snapshot = {"scope": {**self.scope, "operation_key": body["operation_key"]}, "registry": registry,
                "tasks": [self.task_projection(tasks[k]) for k in sorted(tasks)], "operator_binding": binding,
                "operator_result_present": False,
                "origin": {"thread_id": origin["id"], "composed_by": origin["composed_by"],
                           "composed_from_task_id": origin["composed_from_task_id"],
                           "owner_dispatch": next(d for d in dispatches if d["root_id"] == self.scope["owner"])},
                "holds": holds, "dispatch_bindings": dispatches,
                "edge_bindings": sorted(edges, key=lambda e: (e["producer"], e["predecessor_id"], e["successor_id"], e["records"][0]["id"])),
                "local_retry": {"parent_id": current["id"], "agents": local}, "operation_ownership": []}
        # Preflight must not release a digest for a malformed frozen claim;
        # run the same closed historical-data checks before either admission.
        self.validate_frozen(snapshot, self.claim(owner))
        return snapshot

    def origin_v2(self, dispatches: list[dict]) -> dict:
        references = []
        for seq in (518, 525):
            row = self.conn.execute("SELECT * FROM thread_messages WHERE thread_id=? AND seq=?",
                                    (self.scope["lineage_thread"], seq)).fetchone()
            require(row is not None, "invocation_binding")
            require(row["kind"] == "message" and row["speaker"] == "founder"
                    and type(row["seq"]) is int and row["seq"] == seq, "invocation_binding")
            aware_timestamp(row["created_at"])
            references.append({"seq": seq, "row_digest": digest(dict(row))})
        return {"thread_id": self.scope["lineage_thread"],
                "owner_dispatch": next(d for d in dispatches if d["root_id"] == self.scope["owner"]),
                "source_approval": references[0], "recovery_trigger": references[1]}

    def local_agents(self, parent_id: str) -> list[dict]:
        return [{"target_agent": agent,
                 "failed_children": self.rows("SELECT id,assigned_agent,status,parent_task_id,revisit_of_task_id FROM tasks "
                                              "WHERE parent_task_id=? AND assigned_agent=? AND status='failed' ORDER BY id",
                                              (parent_id, agent)),
                 "retries": self.rows("SELECT id,revisit_of_task_id FROM tasks WHERE parent_task_id=? "
                                      "AND revisit_of_task_id IS NOT NULL ORDER BY id", (parent_id,))}
                for agent in ("dev_agent", "qa_engineer")]

    @staticmethod
    def result_reference(row: dict) -> dict:
        require(type(row["id"]) is int and row["id"] > 0 and type(row["task_id"]) is str
                and type(row["agent"]) is str and type(row["session_id"]) is str)
        return {"id": row["id"], "task_id": row["task_id"], "agent": row["agent"],
                "session_id_sha256": text_digest(row["session_id"]), "row_digest": digest(row)}

    def snapshot_v2(self, org: Any, body: dict, owner: dict, binding: dict, registry: dict) -> dict:
        validate_pause_schema(self.conn, org_slug=org.slug)
        roots = [self.root(task_id, self.scope["lineage_thread"]) for task_id in sorted(self.root_seqs)]
        child = self.task(self.scope["qa_child"])
        dispatches = [self.dispatch(root) for root in roots]
        edge = self.old_edge(self.scope["successor"], self.scope["current"])
        require(edge["producer"] == "escalation_superseded"
                and edge["records"][0]["id"] == 417748, "invocation_binding")
        self.unowned_pair()
        # The source operator is independent, and the historical endpoint has
        # no successor. No prose/dispatch may create another ancestry edge.
        for task_id in (self.scope["predecessor"], self.scope["owner"]):
            require(not self.edge_candidates(incoming=task_id), "ambiguous_predecessor")
        for task_id in (self.scope["current"], self.scope["owner"]):
            require(not self.edge_candidates(outgoing=task_id), "competing_successors")
        store = getattr(self.db, "_task_pause_store", None)
        require(store is not None and store.org_slug == org.slug, "control_unavailable", 503)
        holds, observations = [], []
        for root_id in gated_roots(self.version):
            root, control = store.held_uncommitted(root_id)
            require(root["id"] == root_id, "stale_state")
            require(control is None or control["held"] == 0, "root_paused")
            holds.append({"root_task_id": root_id, "control_present": control is not None,
                          "generation": control["generation"] if control else 0, "held": False})
            observations.append({"root_id": root_id,
                "children": [{"id": row["id"], "projection": self.task_projection(row)} for row in
                             self.rows("SELECT * FROM tasks WHERE parent_task_id=? ORDER BY id", (root_id,))],
                "results": [self.result_reference(row) for row in
                            self.rows("SELECT * FROM task_results WHERE task_id=? ORDER BY id", (root_id,))]})
        snapshot = {"scope": {**self.scope, "operation_key": body["operation_key"]}, "registry": registry,
                    "tasks": [self.task_projection(row) for row in sorted(roots + [child], key=lambda row: row["id"])],
                    "operator_binding": binding, "operator_result_present": False,
                    "origin": self.origin_v2(dispatches), "holds": holds, "dispatch_bindings": dispatches,
                    "edge_bindings": [edge], "local_retry": {"parent_id": self.scope["owner"],
                    "agents": self.local_agents(self.scope["owner"]), "observations": observations},
                    "operation_ownership": []}
        self.validate_frozen(snapshot, self.claim(owner))
        return snapshot

    @staticmethod
    def projection_shape(task: dict) -> None:
        keys(task, "id status task_type parent_task_id revisit_of_task_id assigned_agent team dispatched_from_thread_id block_kind cancelled_at revision_count orchestration_step_count current_session_id_sha256 active_chain_sha256 active_fanout_sha256 blocked_on_job_ids_sha256 note_sha256")
        for name in ("id", "status", "task_type", "team"):
            require(type(task[name]) is str)
        # Generic observed children may be unassigned; SQL stores this as NULL.
        for name in ("assigned_agent", "parent_task_id", "revisit_of_task_id", "dispatched_from_thread_id", "block_kind", "cancelled_at"):
            require(task[name] is None or type(task[name]) is str)
        require(type(task["revision_count"]) is int and 0 <= task["revision_count"] <= SQLITE_INTEGER_MAX
                and type(task["orchestration_step_count"]) is int and 0 <= task["orchestration_step_count"] <= SQLITE_INTEGER_MAX)
        for name, value in task.items():
            if name.endswith("_sha256"):
                require(value is None or (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None))

    def validate_projection(self, task: dict) -> None:
        self.projection_shape(task)
        original = self.task(task["id"])
        # Later ordinary lifecycle is not a rewrite of original task identity.
        for name in ("id", "task_type", "parent_task_id", "revisit_of_task_id", "assigned_agent", "team", "dispatched_from_thread_id"):
            require(task[name] == original[name])

    def validate_frozen_v2(self, snapshot: dict, claim: dict) -> None:
        for task in snapshot["tasks"]:
            self.validate_projection(task)
        frozen = {t["id"]: t for t in snapshot["tasks"]}
        for name in ("predecessor", "successor", "current", "qa_child"):
            task = frozen[self.scope[name]]
            require(task["status"] == self.task(task["id"])["status"])
        edge = snapshot["edge_bindings"][0]
        require(edge == self.old_edge(self.scope["successor"], self.scope["current"]))
        # R1: fresh admission checks the independent operator's unowned topology;
        # a later outgoing owner supersession does not invalidate history.
        require(not self.edge_candidates(outgoing=self.scope["current"])
                and not self.edge_candidates(incoming=self.scope["owner"]), "competing_successors")
        for item in snapshot["local_retry"]["agents"]:
            for name in ("failed_children", "retries"):
                for row in item[name]:
                    original = self.task(row["id"])
                    require(original["parent_task_id"] == self.scope["owner"])
                    for key in set(row) - {"status"}:
                        require(row[key] == original[key])
        for observation in snapshot["local_retry"]["observations"]:
            for row in observation["children"]:
                self.validate_projection(row["projection"])
            for row in observation["results"]:
                original = self.conn.execute("SELECT * FROM task_results WHERE id=?", (row["id"],)).fetchone()
                require(original is not None and row == self.result_reference(dict(original)))

    @staticmethod
    def claim(owner: dict) -> dict:
        return {"task_id": owner["id"], "assigned_agent": owner["assigned_agent"], "team": owner["team"],
                "current_session_id_sha256": text_digest(owner["current_session_id"]), "status": owner["status"],
                "block_kind": owner["block_kind"], "cancelled_at": owner["cancelled_at"],
                "step": owner["orchestration_step_count"], "revision": owner["revision_count"],
                **{key + "_sha256": text_digest(owner[key]) for key in
                   ("active_chain", "active_fanout", "note", "blocked_on_job_ids")}, "result_row_id": None}

    def validate_receipt(self, row: dict, payload: dict) -> dict:
        require(type(payload.get("version")) is int and payload["version"] in (1, 2))
        if payload["version"] != self.version:
            return TaskReconciliation(self.db, payload["version"]).validate_receipt(row, payload)
        # OrgState binds the database's existing control store to its selected
        # org. A command's literal org string alone is not database ownership.
        store = getattr(self.db, "_task_pause_store", None)
        require(store is not None, "control_unavailable", 503)
        require(store.org_slug == self.scope["org"], "operation_scope", 403)
        require(type(row["id"]) is int and row["id"] > 0 and row["task_id"] == self.scope["predecessor"]
                and row["agent"] == self.scope["manager"] and row["action"] == ACTION)
        aware_timestamp(row["timestamp"])
        keys(payload, "version operation_key canonical_command command_sha256 state_digest operator_binding operator_claim dispatch_bindings old_edge_bindings snapshot")
        cmd = strict_json(payload["canonical_command"])
        keys(cmd, "version operation_key org predecessor_id existing_successor_id current_descendant_id thread_id operator_task_id expected_state_digest")
        # Reuse the closed request parser for exact scalar types, without ever
        # treating the stored command as today's transport authentication.
        request_body(canonical({k: v for k, v in cmd.items() if k not in ("org", "predecessor_id")} | {"session_id": "historical"}))
        exact_scope(cmd["org"], cmd["predecessor_id"], cmd)
        require(type(payload["version"]) is int and payload["version"] == self.version == cmd["version"]
                and payload["operation_key"] == cmd["operation_key"]
                and canonical(cmd) == payload["canonical_command"] and digest(cmd) == payload["command_sha256"]
                and cmd["expected_state_digest"] == payload["state_digest"])
        snapshot = payload["snapshot"]
        keys(snapshot, "scope registry tasks operator_binding operator_result_present origin holds dispatch_bindings edge_bindings local_retry operation_ownership")
        require(snapshot["scope"] == {**self.scope, "operation_key": cmd["operation_key"]}
                and digest(snapshot) == payload["state_digest"]
                and snapshot["operator_binding"] == payload["operator_binding"]
                and snapshot["dispatch_bindings"] == payload["dispatch_bindings"]
                and snapshot["edge_bindings"] == payload["old_edge_bindings"]
                and snapshot["operation_ownership"] == [] and snapshot["operator_result_present"] is False)
        self.inventory_shape(payload)
        self.historical_session(payload["operator_binding"])
        for task_id in self.root_seqs:
            self.root(task_id, self.scope["owner_origin"] if task_id == self.scope["owner"] else self.scope["lineage_thread"])
        if self.version == 2:
            require(snapshot["origin"] == self.origin_v2(payload["dispatch_bindings"]))
        else:
            origin = self.conn.execute("SELECT * FROM threads WHERE id=?", (self.scope["owner_origin"],)).fetchone()
            require(origin is not None and snapshot["origin"] == {
                "thread_id": origin["id"], "composed_by": self.scope["manager"], "composed_from_task_id": self.scope["current"],
                "owner_dispatch": next((d for d in payload["dispatch_bindings"] if d["root_id"] == self.scope["owner"]), None)})
            require(origin["composed_by"] == self.scope["manager"] and origin["composed_from_task_id"] == self.scope["current"])
        require(canonical(payload["dispatch_bindings"]) == canonical(sorted(
            (self.dispatch(self.task(t)) for t in self.root_seqs), key=lambda d: d["root_id"])))
        edges = payload["old_edge_bindings"]
        require(isinstance(edges, list) and ((2 <= len(edges) <= 18) if self.version == 1 else len(edges) == 1))
        for edge in edges:
            require(canonical(edge) == canonical(self.old_edge(edge["predecessor_id"], edge["successor_id"])))
        self.validate_frozen(snapshot, payload["operator_claim"])
        return {"audit_id": row["id"], "timestamp": row["timestamp"], "task_id": row["task_id"],
                "agent": row["agent"], "action": row["action"],
                **{k: payload[k] for k in ("version", "operation_key", "canonical_command", "command_sha256", "state_digest", "operator_binding")},
                "payload_sha256": digest(payload)}

    def validate_frozen(self, snapshot: dict, claim: dict) -> None:
        """Close saved consistency first, then selected original authentication."""
        self.inventory_shape({"version": self.version, "snapshot": snapshot, "operator_claim": claim,
                              "operator_binding": snapshot["operator_binding"],
                              "dispatch_bindings": snapshot["dispatch_bindings"],
                              "old_edge_bindings": snapshot["edge_bindings"]})
        if self.version == 2:
            self.validate_frozen_v2(snapshot, claim)
            return
        for task in snapshot["tasks"]:
            self.validate_projection(task)
            if task["id"] in (self.scope["predecessor"], self.scope["successor"], self.scope["older"], self.scope["dev_child"], self.scope["qa_child"]):
                require(task["status"] == self.task(task["id"])["status"])
        for item in snapshot["local_retry"]["agents"]:
            for child in item["failed_children"]:
                original = self.task(child["id"])
                require(canonical(child) == canonical({k: original[k] for k in child}))
            for retry in item["retries"]:
                original = self.task(retry["id"])
                require(original["parent_task_id"] == self.scope["current"] and original["revisit_of_task_id"] == retry["revisit_of_task_id"])

    def receipt(self, operation_key: str) -> dict:
        matches = [(r, p) for r, p in self.operations() if p["operation_key"] == operation_key]
        require(bool(matches), "receipt_not_found", 404)
        require(len(matches) == 1, "operation_conflict")
        row, payload = matches[0]
        receipt = self.validate_receipt(row, payload)
        producer = TaskReconciliation(self.db, payload["version"])
        incoming = producer.edge_candidates(incoming=producer.scope["successor"])
        outgoing = producer.edge_candidates(outgoing=producer.scope["predecessor"])
        require(len(incoming) == 1 and incoming == outgoing
                and incoming[0]["producer"] == ACTION and incoming[0]["records"][0]["id"] == row["id"], "operation_conflict")
        return receipt

    def preflight(self, org: Any, predecessor: str, body: dict) -> dict:
        require(body["version"] == self.version, "operation_scope", 403)
        exact_scope(org.slug, predecessor, body)
        owner, binding, registry = self.live_operator(org, body["session_id"])
        operations = self.operations()
        require(not any(p["operation_key"] == body["operation_key"] for _, p in operations), "operation_conflict")
        self.unowned_pair()
        snapshot = self.snapshot(org, body, owner, binding, registry)
        return {"version": self.version, "mode": "preflight", "org": org.slug, "predecessor_id": predecessor,
                "operation_key": body["operation_key"], "eligible": True,
                "target": {"team": self.scope["team"], "manager": self.scope["manager"],
                           **{k: body[k] for k in ("existing_successor_id", "current_descendant_id", "thread_id", "operator_task_id")}},
                "operator_binding": binding, "expected_state_digest": digest(snapshot), "advisory": True}

    def reconcile(self, org: Any, predecessor: str, body: dict) -> dict:
        # Caller holds Database RLock, selected lease and sorted root gates.
        sessions, store = org.sessions, getattr(self.db, "_task_pause_store", None)
        require(org.db is self.db and self.db._conn is self.conn and org.slug == self.scope["org"],
                "control_unavailable", 503)
        require(not self.conn.in_transaction, "control_unavailable", 503)
        self.conn.execute("BEGIN IMMEDIATE")
        commit_attempted = False
        try:
            require(body["version"] == self.version, "operation_scope", 403)
            exact_scope(org.slug, predecessor, body)
            owner, binding, registry = self.live_operator(org, body["session_id"])
            semantic = command(org.slug, predecessor, body)
            operations = self.operations()
            matches = [(r, p) for r, p in operations if p["operation_key"] == body["operation_key"]]
            if matches:
                require(len(matches) == 1, "operation_conflict")
                _, payload = matches[0]
                require(payload["canonical_command"] == canonical(semantic) and payload["command_sha256"] == digest(semantic),
                        "operation_conflict")
                receipt = self.receipt(body["operation_key"])
                response = {"version": 1, "org": org.slug, "receipt": receipt, "receipt_sha256": digest(receipt), "replayed": True}
                ReconciliationResponse.model_validate(response)
                self.conn.rollback()
                return response
            self.unowned_pair()
            snapshot = self.snapshot(org, body, owner, binding, registry)
            require(digest(snapshot) == body["expected_state_digest"], "stale_state")
            payload = {"version": self.version, "operation_key": body["operation_key"], "canonical_command": canonical(semantic),
                       "command_sha256": digest(semantic), "state_digest": digest(snapshot), "operator_binding": binding,
                       "operator_claim": self.claim(owner), "dispatch_bindings": snapshot["dispatch_bindings"],
                       "old_edge_bindings": snapshot["edge_bindings"], "snapshot": snapshot}
            require(org.db is self.db and self.db._conn is self.conn and org.sessions is sessions
                    and getattr(self.db, "_task_pause_store", None) is store, "control_unavailable", 503)
            audit_id = self.db.insert_audit_log_uncommitted(predecessor, self.scope["manager"], ACTION, payload)
            row = dict(self.conn.execute("SELECT * FROM audit_log WHERE id=?", (audit_id,)).fetchone())
            receipt = self.receipt(body["operation_key"])
            require(receipt["audit_id"] == row["id"])
            response = {"version": 1, "org": org.slug, "receipt": receipt, "receipt_sha256": digest(receipt), "replayed": False}
            ReconciliationResponse.model_validate(response)
            commit_attempted = True
            self.conn.commit()
            return response
        except BaseException as primary:
            try:
                self.conn.rollback()
            except BaseException as rollback:
                error = ReconciliationError("rollback_uncertain", 503)
                error.primary_error, error.rollback_error = primary, rollback
                error.add_note(f"primary={type(primary).__name__}; rollback={type(rollback).__name__}")
                raise error from primary
            if commit_attempted:
                raise ReconciliationError("commit_uncertain", 503) from primary
            raise
