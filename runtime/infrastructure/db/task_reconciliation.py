"""The closed THR-299 remedy; no generic failed-root authority is introduced.

Callers own the registry/session lease and sorted root gates before entering
the Database RLock. Historical validation never depends on a live session.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from runtime.infrastructure.task_pause_controls import validate_pause_schema

ACTION = "failed_continuation_reconciled"
CAP = 4096
SCOPE = {
    "org": "happyranch", "team": "engineering", "manager": "engineering_manager",
    "owner": "TASK-10725", "owner_origin": "THR-299", "lineage_thread": "THR-293",
    "predecessor": "TASK-10343", "successor": "TASK-10554", "current": "TASK-10565",
    "older": "TASK-10319", "dev_child": "TASK-10450", "qa_child": "TASK-10329",
}
ROOT_SEQS = {"TASK-10319": 15, "TASK-10343": 23, "TASK-10554": 54,
             "TASK-10565": 62, "TASK-10725": 5}


class ReconciliationError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReconcileFailedContinuationBody(ClosedModel):
    version: StrictInt = Field(ge=1, le=1)
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
    version: Literal[1]
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
    version: Literal[1]
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
    require(org == SCOPE["org"] and predecessor == SCOPE["predecessor"]
            and body["existing_successor_id"] == SCOPE["successor"]
            and body["current_descendant_id"] == SCOPE["current"]
            and body["thread_id"] == SCOPE["lineage_thread"]
            and body["operator_task_id"] == SCOPE["owner"], "operation_scope", 403)


class TaskReconciliation:
    """Synchronous domain validator over the caller's ONE owned transaction."""

    def __init__(self, db: Any) -> None:
        self.db, self.conn = db, db._conn

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
                and row["assigned_agent"] == SCOPE["manager"] and row["team"] == SCOPE["team"]
                and row["dispatched_from_thread_id"] == thread and row["revisit_of_task_id"] is None)
        return row

    def _session_payload(self, row: dict) -> dict:
        payload = strict_json(row["payload"], "original_session_evidence", allow_floats=True)
        require(type(row["id"]) is int and row["id"] > 0
                and row["task_id"] == SCOPE["owner"] and row["action"] == "session_start"
                and row["agent"] == SCOPE["manager"], "original_session_evidence")
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
                         (SCOPE["owner"],))
        matches = []
        for row in rows:
            payload = self._session_payload(row)
            if payload.get("session_id") == sid:
                matches.append((row, payload))
        require(len(matches) == 1, "original_session_evidence")
        row, payload = matches[0]
        require(payload.get("invocation_purpose") == "manager_decision", "ordinary_purpose_required", 403)
        return {"task_id": SCOPE["owner"], "agent": SCOPE["manager"],
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
            manager = org.teams.manager_for_team(SCOPE["team"])
            executable = org.teams.executable_manager_for_team(SCOPE["team"])
        except (KeyError, ValueError) as exc:
            raise ReconciliationError("manager_mismatch", 403) from exc
        registry = {"team": SCOPE["team"], "manager_kind": manager.kind,
                    "manager_principal": manager.principal, "executable_manager": executable}
        require(manager.kind == "agent" and manager.principal == SCOPE["manager"]
                and executable == SCOPE["manager"], "manager_mismatch", 403)
        owner = self.root(SCOPE["owner"], SCOPE["owner_origin"])
        require(owner["status"] == "in_progress" and owner["block_kind"] is None
                and owner["cancelled_at"] is None, "operator_inactive")
        sessions = org.sessions
        require(sessions is not None, "control_unavailable", 503)
        require(owner["current_session_id"] == sid
                and sessions.get_active(SCOPE["owner"], SCOPE["manager"]) == sid
                and sessions.get_context_by_session(sid) == (org.slug, SCOPE["owner"], SCOPE["manager"]),
                "session_mismatch")
        require(not sessions.is_recovery_session(SCOPE["owner"], SCOPE["manager"], sid),
                "recovery_purpose_forbidden", 403)
        require(self.conn.execute("SELECT 1 FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                                  (SCOPE["owner"], SCOPE["manager"], sid)).fetchone() is None,
                "operator_inactive")
        return owner, self.original_session(sid), registry

    def dispatch(self, root: dict) -> dict:
        thread = root["dispatched_from_thread_id"]
        rows = self.rows("SELECT * FROM thread_invocations WHERE dispatched_task_id=? ORDER BY id", (root["id"],))
        require(len(rows) == 1, "invocation_binding")
        inv = rows[0]
        require(inv["thread_id"] == thread and inv["agent_name"] == SCOPE["manager"]
                and inv["purpose"] in ("reply", "bootstrap")
                and inv["status"] in ("pending", "consumed", "declined", "timeout", "failed"), "invocation_binding")
        trigger = self.conn.execute("SELECT * FROM thread_messages WHERE thread_id=? AND seq=?",
                                    (thread, inv["triggering_seq"])).fetchone()
        require(trigger is not None and self.conn.execute("SELECT 1 FROM threads WHERE id=?", (thread,)).fetchone() is not None,
                "invocation_binding")
        expected = {"task_id": root["id"], "dispatcher": SCOPE["manager"],
                    "target_agent": SCOPE["manager"], "team": SCOPE["team"]}
        audits = []
        for row in self.rows("SELECT * FROM audit_log WHERE task_id=? AND action='thread_dispatch' ORDER BY id", (thread,)):
            payload = strict_json(row["payload"])
            if payload.get("task_id") == root["id"]:
                require(row["agent"] == SCOPE["manager"] and all(payload.get(k) == v for k, v in expected.items()),
                        "invocation_binding")
                audits.append({"id": row["id"], "row_digest": digest(row)})
        messages = []
        for row in self.rows("SELECT * FROM thread_messages WHERE thread_id=? AND kind='system' ORDER BY seq", (thread,)):
            if row["system_payload_json"] is None:
                continue
            payload = strict_json(row["system_payload_json"])
            if payload.get("task_id") == root["id"] and payload.get("kind_tag") == "task_dispatched":
                require(row["speaker"] == SCOPE["manager"] and row["seq"] > inv["triggering_seq"]
                        and all(payload.get(k) == v for k, v in expected.items()), "invocation_binding")
                messages.append({"seq": row["seq"], "row_digest": digest({k: row[k] for k in
                                 ("thread_id", "seq", "speaker", "kind", "system_payload_json")})})
        require(len(audits) == len(messages) == 1 and messages[0]["seq"] == ROOT_SEQS[root["id"]], "invocation_binding")
        return {"root_id": root["id"], "thread_id": thread, "invocation_id": inv["id"],
                "invocation_digest": digest({k: inv[k] for k in
                     ("id", "thread_id", "agent_name", "dispatched_task_id", "triggering_seq", "purpose")}),
                "triggering_seq": inv["triggering_seq"], "trigger_message_digest": digest(dict(trigger)),
                "dispatch_audits": audits, "dispatch_messages": messages}

    def operations(self) -> list[tuple[dict, dict]]:
        result = []
        for row in self.rows("SELECT * FROM audit_log WHERE action=? ORDER BY id", (ACTION,)):
            payload = strict_json(row["payload"])
            keys(payload, "version operation_key canonical_command command_sha256 state_digest operator_binding operator_claim dispatch_bindings old_edge_bindings snapshot")
            result.append((row, payload))
        return result

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
        validate_pause_schema(self.conn, org_slug=org.slug)
        roots = [self.root(task_id, SCOPE["owner_origin"] if task_id == SCOPE["owner"] else SCOPE["lineage_thread"])
                 for task_id in ROOT_SEQS]
        pred, succ, current, older = (self.task(SCOPE[k]) for k in ("predecessor", "successor", "current", "older"))
        require(pred["status"] == "failed" and succ["status"] == "superseded")
        require(current["status"] == "in_progress" and current["cancelled_at"] is None, "stale_state")
        children = [self.task(SCOPE["dev_child"]), self.task(SCOPE["qa_child"])]
        for row, parent, agent, revisit in ((children[0], pred, "dev_agent", "TASK-10409"),
                                            (children[1], older, "qa_engineer", None)):
            require(row["status"] == "failed" and row["assigned_agent"] == agent
                    and row["parent_task_id"] == parent["id"] and row["revisit_of_task_id"] == revisit)
        origin = self.conn.execute("SELECT * FROM threads WHERE id=?", (SCOPE["owner_origin"],)).fetchone()
        require(origin is not None and origin["composed_by"] == SCOPE["manager"]
                and origin["composed_from_task_id"] == SCOPE["current"], "invocation_binding")
        dispatches = sorted((self.dispatch(root) for root in roots), key=lambda d: d["root_id"])
        edges = [self.old_edge(SCOPE["older"], SCOPE["predecessor"]),
                 self.old_edge(SCOPE["successor"], SCOPE["current"])]
        require(not self.edge_candidates(outgoing=SCOPE["predecessor"])
                and not self.edge_candidates(incoming=SCOPE["successor"]), "pair_owned")
        # Scan the entire bounded incoming lineage and outgoing owner at each
        # vertex. The proposed gap consumes one edge; there can be no branch.
        cursor, visited = current["id"], []
        while True:
            require(cursor not in visited, "lineage_cycle")
            require(len(visited) < 20, "lineage_record_limit_20")
            visited.append(cursor)
            self.root(cursor, SCOPE["lineage_thread"])
            if cursor == SCOPE["successor"]:
                cursor = SCOPE["predecessor"]
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
        for root_id in sorted((SCOPE["owner"], SCOPE["current"])):
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
        snapshot = {"scope": {**SCOPE, "operation_key": body["operation_key"]}, "registry": registry,
                "tasks": [self.task_projection(tasks[k]) for k in sorted(tasks)], "operator_binding": binding,
                "operator_result_present": False,
                "origin": {"thread_id": origin["id"], "composed_by": origin["composed_by"],
                           "composed_from_task_id": origin["composed_from_task_id"],
                           "owner_dispatch": next(d for d in dispatches if d["root_id"] == SCOPE["owner"])},
                "holds": holds, "dispatch_bindings": dispatches,
                "edge_bindings": sorted(edges, key=lambda e: (e["producer"], e["predecessor_id"], e["successor_id"], e["records"][0]["id"])),
                "local_retry": {"parent_id": current["id"], "agents": local}, "operation_ownership": []}
        # Preflight must not release a digest for a malformed frozen claim;
        # run the same closed historical-data checks before either admission.
        self.validate_frozen(snapshot, self.claim(owner))
        return snapshot

    @staticmethod
    def claim(owner: dict) -> dict:
        return {"task_id": owner["id"], "assigned_agent": owner["assigned_agent"], "team": owner["team"],
                "current_session_id_sha256": text_digest(owner["current_session_id"]), "status": owner["status"],
                "block_kind": owner["block_kind"], "cancelled_at": owner["cancelled_at"],
                "step": owner["orchestration_step_count"], "revision": owner["revision_count"],
                **{key + "_sha256": text_digest(owner[key]) for key in
                   ("active_chain", "active_fanout", "note", "blocked_on_job_ids")}, "result_row_id": None}

    def validate_receipt(self, row: dict, payload: dict) -> dict:
        # OrgState binds the database's existing control store to its selected
        # org. A command's literal org string alone is not database ownership.
        store = getattr(self.db, "_task_pause_store", None)
        require(store is not None, "control_unavailable", 503)
        require(store.org_slug == SCOPE["org"], "operation_scope", 403)
        require(type(row["id"]) is int and row["id"] > 0 and row["task_id"] == SCOPE["predecessor"]
                and row["agent"] == SCOPE["manager"] and row["action"] == ACTION)
        aware_timestamp(row["timestamp"])
        keys(payload, "version operation_key canonical_command command_sha256 state_digest operator_binding operator_claim dispatch_bindings old_edge_bindings snapshot")
        cmd = strict_json(payload["canonical_command"])
        keys(cmd, "version operation_key org predecessor_id existing_successor_id current_descendant_id thread_id operator_task_id expected_state_digest")
        # Reuse the closed request parser for exact scalar types, without ever
        # treating the stored command as today's transport authentication.
        request_body(canonical({k: v for k, v in cmd.items() if k not in ("org", "predecessor_id")} | {"session_id": "historical"}))
        exact_scope(cmd["org"], cmd["predecessor_id"], cmd)
        require(type(payload["version"]) is int and payload["version"] == 1
                and payload["operation_key"] == cmd["operation_key"]
                and canonical(cmd) == payload["canonical_command"] and digest(cmd) == payload["command_sha256"]
                and cmd["expected_state_digest"] == payload["state_digest"])
        snapshot = payload["snapshot"]
        keys(snapshot, "scope registry tasks operator_binding operator_result_present origin holds dispatch_bindings edge_bindings local_retry operation_ownership")
        require(snapshot["scope"] == {**SCOPE, "operation_key": cmd["operation_key"]}
                and digest(snapshot) == payload["state_digest"]
                and snapshot["operator_binding"] == payload["operator_binding"]
                and snapshot["dispatch_bindings"] == payload["dispatch_bindings"]
                and snapshot["edge_bindings"] == payload["old_edge_bindings"]
                and snapshot["operation_ownership"] == [] and snapshot["operator_result_present"] is False)
        self.historical_session(payload["operator_binding"])
        for task_id in ROOT_SEQS:
            self.root(task_id, SCOPE["owner_origin"] if task_id == SCOPE["owner"] else SCOPE["lineage_thread"])
        origin = self.conn.execute("SELECT * FROM threads WHERE id=?", (SCOPE["owner_origin"],)).fetchone()
        require(origin is not None and snapshot["origin"] == {
            "thread_id": origin["id"], "composed_by": SCOPE["manager"], "composed_from_task_id": SCOPE["current"],
            "owner_dispatch": next((d for d in payload["dispatch_bindings"] if d["root_id"] == SCOPE["owner"]), None)})
        require(origin["composed_by"] == SCOPE["manager"] and origin["composed_from_task_id"] == SCOPE["current"])
        require(canonical(payload["dispatch_bindings"]) == canonical(sorted(
            (self.dispatch(self.task(t)) for t in ROOT_SEQS), key=lambda d: d["root_id"])))
        edges = payload["old_edge_bindings"]
        require(isinstance(edges, list) and 2 <= len(edges) <= 18)
        for edge in edges:
            require(canonical(edge) == canonical(self.old_edge(edge["predecessor_id"], edge["successor_id"])))
        self.validate_frozen(snapshot, payload["operator_claim"])
        return {"audit_id": row["id"], "timestamp": row["timestamp"], "task_id": row["task_id"],
                "agent": row["agent"], "action": row["action"],
                **{k: payload[k] for k in ("version", "operation_key", "canonical_command", "command_sha256", "state_digest", "operator_binding")},
                "payload_sha256": digest(payload)}

    def validate_frozen(self, snapshot: dict, claim: dict) -> None:
        """Closed historical observations, separate from current lifecycle."""
        require(snapshot["registry"] == {"team": SCOPE["team"], "manager_kind": "agent",
                "manager_principal": SCOPE["manager"], "executable_manager": SCOPE["manager"]})
        tasks = snapshot["tasks"]
        require(isinstance(tasks, list) and len(tasks) <= 23)
        ids = [t.get("id") for t in tasks]
        require(ids == sorted(set(ids)) and set(ROOT_SEQS) | {SCOPE["dev_child"], SCOPE["qa_child"]} <= set(ids))
        names = "id status task_type parent_task_id revisit_of_task_id assigned_agent team dispatched_from_thread_id block_kind cancelled_at revision_count orchestration_step_count current_session_id_sha256 active_chain_sha256 active_fanout_sha256 blocked_on_job_ids_sha256 note_sha256"
        for task in tasks:
            keys(task, names)
            require(type(task["revision_count"]) is int and task["revision_count"] >= 0
                    and type(task["orchestration_step_count"]) is int and task["orchestration_step_count"] >= 0)
            for key, value in task.items():
                if key.endswith("_sha256"):
                    require(value is None or (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None))
            current = self.task(task["id"])
            for key in ("id", "task_type", "parent_task_id", "revisit_of_task_id", "assigned_agent", "team", "dispatched_from_thread_id"):
                require(task[key] == current[key])
            if task["id"] not in (SCOPE["dev_child"], SCOPE["qa_child"]):
                require(task["parent_task_id"] is None and task["task_type"] == "task"
                        and task["assigned_agent"] == SCOPE["manager"] and task["team"] == SCOPE["team"])
            if task["id"] in (SCOPE["predecessor"], SCOPE["successor"], SCOPE["older"], SCOPE["dev_child"], SCOPE["qa_child"]):
                require(task["status"] == current["status"])
        frozen = {t["id"]: t for t in tasks}
        require(frozen[SCOPE["predecessor"]]["status"] == "failed"
                and frozen[SCOPE["successor"]]["status"] == "superseded"
                and frozen[SCOPE["older"]]["status"] == "superseded")
        owner = frozen[SCOPE["owner"]]
        require(owner["status"] == "in_progress" and owner["block_kind"] is None and owner["cancelled_at"] is None
                and owner["current_session_id_sha256"] == snapshot["operator_binding"]["session_id_sha256"])
        expected_claim = {"task_id": owner["id"], "assigned_agent": owner["assigned_agent"], "team": owner["team"],
                          "current_session_id_sha256": owner["current_session_id_sha256"], "status": owner["status"],
                          "block_kind": owner["block_kind"], "cancelled_at": owner["cancelled_at"],
                          "step": owner["orchestration_step_count"], "revision": owner["revision_count"],
                          **{k + "_sha256": owner[k + "_sha256"] for k in
                             ("active_chain", "active_fanout", "note", "blocked_on_job_ids")}, "result_row_id": None}
        require(canonical(claim) == canonical(expected_claim))
        require(frozen[SCOPE["current"]]["status"] == "in_progress" and frozen[SCOPE["current"]]["cancelled_at"] is None)
        for task_id, parent, agent, revisit in ((SCOPE["dev_child"], SCOPE["predecessor"], "dev_agent", "TASK-10409"),
                                              (SCOPE["qa_child"], SCOPE["older"], "qa_engineer", None)):
            child = frozen[task_id]
            require(child["status"] == "failed" and child["parent_task_id"] == parent
                    and child["assigned_agent"] == agent and child["revisit_of_task_id"] == revisit)
        require(isinstance(snapshot["holds"], list) and len(snapshot["holds"]) == 2)
        for hold, root_id in zip(snapshot["holds"], sorted((SCOPE["owner"], SCOPE["current"]))):
            keys(hold, "root_task_id control_present generation held")
            require(hold["root_task_id"] == root_id and type(hold["control_present"]) is bool
                    and type(hold["generation"]) is int and 0 <= hold["generation"] <= 2**63 - 1
                    and hold["generation"] % 2 == 0 and hold["held"] is False
                    and (hold["control_present"] or hold["generation"] == 0))
        local = snapshot["local_retry"]
        keys(local, "parent_id agents")
        require(local["parent_id"] == SCOPE["current"] and isinstance(local["agents"], list) and len(local["agents"]) == 2)
        for item, agent in zip(local["agents"], ("dev_agent", "qa_engineer")):
            keys(item, "target_agent failed_children retries")
            require(item["target_agent"] == agent and isinstance(item["failed_children"], list) and isinstance(item["retries"], list))
            for name in ("failed_children", "retries"):
                rows = item[name]
                require(len(rows) <= CAP and [r["id"] for r in rows] == sorted({r["id"] for r in rows}))
            for child in item["failed_children"]:
                keys(child, "id assigned_agent status parent_task_id revisit_of_task_id")
                require(child["assigned_agent"] == agent and child["status"] == "failed" and child["parent_task_id"] == SCOPE["current"])
                original = self.task(child["id"])
                require(canonical(child) == canonical({k: original[k] for k in child}))
            for retry in item["retries"]:
                keys(retry, "id revisit_of_task_id")
                require(type(retry["id"]) is str and type(retry["revisit_of_task_id"]) is str)
                original = self.task(retry["id"])
                require(original["parent_task_id"] == SCOPE["current"] and original["revisit_of_task_id"] == retry["revisit_of_task_id"])
        # Reconstruct the full frozen path, including the new gap, rather than
        # trusting an array of independently valid but unrelated old edges.
        incoming = {SCOPE["successor"]: SCOPE["predecessor"]}
        outgoing = {SCOPE["predecessor"]: SCOPE["successor"]}
        for edge in snapshot["edge_bindings"]:
            pred, succ = edge["predecessor_id"], edge["successor_id"]
            require(succ not in incoming and pred not in outgoing)
            incoming[succ], outgoing[pred] = pred, succ
        path, cursor = [], SCOPE["current"]
        while True:
            require(cursor not in path and len(path) < 20, "lineage_cycle" if cursor in path else "lineage_record_limit_20")
            path.append(cursor)
            if cursor not in incoming:
                break
            cursor = incoming[cursor]
        require(set(path) == set(ids) - {SCOPE["owner"], SCOPE["dev_child"], SCOPE["qa_child"]}
                and set(outgoing) == set(path[1:]) and SCOPE["older"] in path)

    def receipt(self, operation_key: str) -> dict:
        matches = [(r, p) for r, p in self.operations() if p["operation_key"] == operation_key]
        require(bool(matches), "receipt_not_found", 404)
        require(len(matches) == 1, "operation_conflict")
        row, payload = matches[0]
        receipt = self.validate_receipt(row, payload)
        require(len(self.edge_candidates(incoming=SCOPE["successor"])) == 1
                and len(self.edge_candidates(outgoing=SCOPE["predecessor"])) == 1, "operation_conflict")
        return receipt

    def preflight(self, org: Any, predecessor: str, body: dict) -> dict:
        exact_scope(org.slug, predecessor, body)
        owner, binding, registry = self.live_operator(org, body["session_id"])
        operations = self.operations()
        require(not any(p["operation_key"] == body["operation_key"] for _, p in operations), "operation_conflict")
        require(not operations, "pair_owned")
        snapshot = self.snapshot(org, body, owner, binding, registry)
        return {"version": 1, "mode": "preflight", "org": org.slug, "predecessor_id": predecessor,
                "operation_key": body["operation_key"], "eligible": True,
                "target": {"team": SCOPE["team"], "manager": SCOPE["manager"],
                           **{k: body[k] for k in ("existing_successor_id", "current_descendant_id", "thread_id", "operator_task_id")}},
                "operator_binding": binding, "expected_state_digest": digest(snapshot), "advisory": True}

    def reconcile(self, org: Any, predecessor: str, body: dict) -> dict:
        # Caller holds Database RLock, lease and both root gates.
        require(not self.conn.in_transaction, "control_unavailable", 503)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
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
                self.conn.rollback()
                return {"version": 1, "org": org.slug, "receipt": receipt, "receipt_sha256": digest(receipt), "replayed": True}
            require(not operations, "pair_owned")
            snapshot = self.snapshot(org, body, owner, binding, registry)
            require(digest(snapshot) == body["expected_state_digest"], "stale_state")
            payload = {"version": 1, "operation_key": body["operation_key"], "canonical_command": canonical(semantic),
                       "command_sha256": digest(semantic), "state_digest": digest(snapshot), "operator_binding": binding,
                       "operator_claim": self.claim(owner), "dispatch_bindings": snapshot["dispatch_bindings"],
                       "old_edge_bindings": snapshot["edge_bindings"], "snapshot": snapshot}
            audit_id = self.db.insert_audit_log_uncommitted(predecessor, SCOPE["manager"], ACTION, payload)
            row = dict(self.conn.execute("SELECT * FROM audit_log WHERE id=?", (audit_id,)).fetchone())
            receipt = self.receipt(body["operation_key"])
            require(receipt["audit_id"] == row["id"])
            self.conn.commit()
            return {"version": 1, "org": org.slug, "receipt": receipt, "receipt_sha256": digest(receipt), "replayed": False}
        except BaseException:
            self.conn.rollback()
            raise
