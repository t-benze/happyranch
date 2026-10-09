"""Org-only, cooperative Founder-request cutover over the shipped v1 layout.

The store owns short SQLite transactions, never dispatch/cancellation or host
work. Hashes detect incoherent supported history, not hostile same-UID forgery.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import (
    _CUTOVER_POLICY,
    _CUTOVER_STATES,
    _cutover_event_digest,
    _validate_cutover_data,
    _validate_installed,
    draft_migration_guidance,
    _validate_draft_data,
    _validate_submission_data,
)


class WorkflowCutoverError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WorkflowCutoverStore:
    def __init__(self, database: Database, *, org_slug: str) -> None:
        if not isinstance(org_slug, str) or not org_slug:
            raise WorkflowCutoverError("cutover_storage_corrupt")
        self._database = database
        self._org_slug = org_slug

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        with self._database._lock:
            conn = self._database._conn
            if conn.in_transaction:
                raise WorkflowCutoverError("cutover_operation_failed")
            try:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
                if write:
                    conn.commit()
                else:
                    conn.rollback()
            except BaseException:
                conn.rollback()
                raise

    def _read(self, conn: sqlite3.Connection) -> tuple[dict, list[dict]]:
        try:
            _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False)
            return _validate_cutover_data(conn, expected_org_slug=self._org_slug)
        except (ValueError, sqlite3.DatabaseError) as exc:
            raise WorkflowCutoverError("cutover_storage_corrupt") from exc

    def _advance(self, conn: sqlite3.Connection, marker: dict, events: list[dict], *, key: str) -> None:
        seq = marker["generation"] + 1
        event = {
            "id": f"cutover-event-{seq}", "event_seq": seq,
            "state_before": marker["state"], "state_after": _CUTOVER_STATES[seq - 1],
            "operation_key": key, "created_at": datetime.now(timezone.utc).isoformat(),
        }
        digest = _cutover_event_digest(
            event, org_slug=self._org_slug, previous_digest=events[-1]["event_digest"],
        )
        conn.execute(
            "INSERT INTO workflow_cutover_events VALUES (?,?,?,?,?,?,?)",
            (event["id"], seq, event["state_before"], event["state_after"], key,
             digest, event["created_at"]),
        )
        conn.execute(
            "UPDATE workflow_cutover_state SET state=?,generation=?,operation_key=?,"
            "disable_reason=?,updated_at=? WHERE singleton=1 AND generation=?",
            (event["state_after"], seq, key,
             "founder_disable_requested" if seq >= 5 else None,
             event["created_at"], marker["generation"]),
        )
        self._read(conn)

    def request(self, *, action: str, operation_key: str, expected_generation: int) -> dict:
        if (action not in ("enable", "disable") or not isinstance(operation_key, str)
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", operation_key) is None
                or type(expected_generation) is not int or expected_generation < 1):
            raise WorkflowCutoverError("cutover_invalid_request")
        try:
            with self._transaction(write=True) as conn:
                marker, events = self._read(conn)
                layout = _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False)
                original = next((e for e in events if e["event_seq"] in (2, 5)
                                 and e["operation_key"] == operation_key), None)
                if layout in ("E", "G") and self._draft_readiness_blockers(conn):
                    raise WorkflowCutoverError("cutover_storage_corrupt")
                replayed = original is not None
                if original is not None:
                    original_action = "enable" if original["event_seq"] == 2 else "disable"
                    if action != original_action or expected_generation != original["event_seq"] - 1:
                        raise WorkflowCutoverError("cutover_operation_conflict")
                else:
                    if expected_generation != marker["generation"]:
                        raise WorkflowCutoverError("cutover_generation_stale")
                    allowed = "enable" if marker["state"] == "installed_legacy_only" else (
                        "disable" if marker["state"] == "enabled" else None
                    )
                    if action != allowed:
                        raise WorkflowCutoverError("cutover_transition_not_allowed")
                    if action == "enable" and layout == "F":
                        raise WorkflowCutoverError("draft_schema_migration_required")
                    self._advance(conn, marker, events, key=operation_key)
                    original = {"id": f"cutover-event-{marker['generation'] + 1}",
                                "event_seq": marker["generation"] + 1}
            # The authentic request/fence is durable before reconciliation.
            projection = self.recover_authorized()
            return {
                **projection, "request_event_id": original["id"],
                "request_generation": original["event_seq"], "request_action": action,
                "replayed": replayed,
            }
        except sqlite3.DatabaseError as exc:
            raise WorkflowCutoverError("cutover_operation_failed") from exc

    def recover_authorized(self) -> dict:
        """Advance only a validated committed request, at most three commits.

        Every boundary rechecks the authoritative layout/history and the real
        verification/drain snapshot under a fresh writer reservation.
        """
        try:
            for _ in range(3):
                with self._transaction(write=True) as conn:
                    marker, events = self._read(conn)
                    state = marker["state"]
                    if self._draft_readiness_blockers(conn):
                        break
                    if state in ("enable_requested", "compatibility_verified"):
                        if self._compatibility_blockers(conn):
                            break
                    elif state == "draining":
                        if self._drain_blockers(conn):
                            break
                    elif state != "disable_requested":
                        break
                    self._advance(conn, marker, events, key=marker["operation_key"])
        except sqlite3.DatabaseError:
            # The request remains durable; projection must report its pending
            # stage rather than turn a failed reconciliation into a rollback.
            projection = self.get()
            projection["blockers"].append(self._blocker(
                "cutover_recovery_unavailable", owner="workflow_cutover_reconciler",
                required_action="retry_same_request_or_cold_reopen",
            ))
            projection["reconciliation_required"] = True
            return projection
        return self.get()

    def get(self) -> dict:
        try:
            with self._transaction(write=False) as conn:
                marker, events = self._read(conn)
                return self._projection(conn, marker, events)
        except sqlite3.DatabaseError as exc:
            raise WorkflowCutoverError("cutover_operation_failed") from exc

    def _projection(self, conn: sqlite3.Connection, marker: dict, events: list[dict]) -> dict:
        blockers = self._draft_readiness_blockers(conn)
        if marker["state"] in ("enable_requested", "compatibility_verified"):
            blockers = self._compatibility_blockers(conn)
        elif marker["state"] in ("disable_requested", "draining", "drained"):
            blockers = self._drain_blockers(conn)
        return {
            "org_slug": self._org_slug, **marker, "events": events,
            "allowed_actions": (["enable"] if not blockers else []) if marker["generation"] == 1 else (
                ["disable"] if marker["state"] == "enabled" else []
            ),
            "blockers": blockers, "reconciliation_required": bool(blockers),
            "verification": None if len(events) < 3 else {
                "event_id": events[2]["id"], "policy": _CUTOVER_POLICY,
            },
        }

    @staticmethod
    def _blocker(code: str, *, owner: str, required_action: str, **facts: str) -> dict:
        return {"code": code, "owner": owner, "required_action": required_action, **facts}

    @staticmethod
    def _workflow_tables(conn: sqlite3.Connection) -> list[str]:
        return [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name LIKE 'workflow\\_%' ESCAPE '\\' ORDER BY name"
        )]

    def _integrity_blockers(self, conn: sqlite3.Connection) -> list[dict]:
        if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            return [self._blocker("cutover_sqlite_integrity", owner="workflow_cutover_reconciler",
                                  required_action="reconcile_storage")]
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return [self._blocker("cutover_foreign_key_integrity", owner="workflow_cutover_reconciler",
                                  required_action="reconcile_storage")]
        return []

    def _draft_readiness_blockers(self, conn: sqlite3.Connection) -> list[dict]:
        if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) in ("E", "G"):
            try:
                _validate_draft_data(conn, expected_org_slug=self._org_slug)
                if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) == "G":
                    _validate_submission_data(conn, expected_org_slug=self._org_slug)
            except (ValueError, sqlite3.DatabaseError):
                return [self._blocker("cutover_draft_closure", owner="workflow_recovery",
                                     required_action="reconcile_draft_closure", deferred_to="U2D/U5")]
            return []
        path = self._database.path
        runtime_root = str(path.parent.parent.parent) if (
            path.name == "happyranch.db" and path.parent.name == self._org_slug
            and path.parent.parent.name == "orgs"
        ) else "<absolute-root>"
        return [self._blocker("draft_schema_migration_required", owner="operator",
                             required_action=draft_migration_guidance(org_slug=self._org_slug, runtime_root=runtime_root))]

    def _compatibility_blockers(self, conn: sqlite3.Connection) -> list[dict]:
        blockers = self._draft_readiness_blockers(conn) + self._integrity_blockers(conn)
        # Inert template and coordinated authority/profile foundations are
        # permitted. Every other workflow work relation must still be empty.
        for table in self._workflow_tables(conn):
            if table in ("workflow_adapter_versions", "workflow_cutover_state", "workflow_cutover_events", "workflow_draft_adapter_versions", "workflow_submission_schema_versions"):
                continue
            if table.startswith(("workflow_template_", "workflow_authority_",
                                 "workflow_publication_", "workflow_profile_")):
                continue
            row = conn.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
            if row is not None:
                facts = dict(row)
                blockers.append(self._blocker(
                    "cutover_pre_enable_work", record_id=str(facts.get("id", facts.get("record_id", table))),
                    owner=str(facts.get("recovery_owner", "workflow_recovery")),
                    required_action="resolve_pre_enable_work", deferred_to="U2D/U5",
                ))
        return blockers

    def _drain_blockers(self, conn: sqlite3.Connection) -> list[dict]:
        blockers = self._draft_readiness_blockers(conn) + self._integrity_blockers(conn)
        outboxes = [dict(row) for row in conn.execute("SELECT * FROM workflow_dispatch_outbox ORDER BY id")]
        by_operation = {o["operation_id"]: o for o in outboxes}
        for table in ("workflow_dispatch_operations", "workflow_request_task_bridges"):
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                data = dict(row)
                op_id = data.get("operation_id", data.get("id"))
                outbox = by_operation.get(op_id)
                expected_state = None if outbox is None else (
                    (outbox["state"] if outbox["state"] in ("cancelled", "completed") else "admitted")
                    if table == "workflow_dispatch_operations" else outbox["state"]
                )
                if ((outbox is None and data["state"] not in ("cancelled", "completed"))
                        or (outbox is not None and data["state"] != expected_state)):
                    blockers.append(self._blocker(
                        "cutover_dispatch_closure", record_id=str(op_id), state=data["state"],
                        owner="workflow_recovery", required_action="reconcile_dispatch_closure", deferred_to="U2D/U5",
                    ))
        for outbox in outboxes:
            if outbox["state"] in ("cancelled", "completed"):
                continue
            closure = conn.execute(
                "SELECT op.org_slug,op.instance_id,op.round_id,op.request_id,op.state AS op_state,"
                "r.round_id AS request_round,r.principal,r.assignment_generation,r.status AS request_status,"
                "b.operation_id AS bridge_operation,b.instance_id AS bridge_instance,b.request_id AS bridge_request,"
                "b.assigned_principal,b.assignment_generation AS bridge_generation,b.state AS bridge_state,"
                "b.task_id,b.session_id,t.assigned_agent,"
                "rd.instance_id AS round_instance,rd.current_revision,sub.instance_id AS submission_instance,"
                "sub.revision, tv.namespace "
                "FROM workflow_dispatch_operations op "
                "JOIN workflow_review_requests r ON r.id=op.request_id "
                "JOIN workflow_request_task_bridges b ON b.operation_id=op.id "
                "JOIN workflow_rounds rd ON rd.id=op.round_id "
                "JOIN workflow_submissions sub ON sub.id=rd.submission_id "
                "JOIN workflow_instances i ON i.id=op.instance_id "
                "JOIN workflow_binding_snapshots bs ON bs.id=i.binding_snapshot_id "
                "JOIN workflow_template_versions tv ON tv.id=bs.template_version_id "
                "JOIN tasks t ON t.id=b.task_id WHERE op.id=?",
                (outbox["operation_id"],),
            ).fetchone()
            data = dict(closure) if closure is not None else {}
            coherent = (
                data.get("org_slug") == self._org_slug
                and data.get("op_state") == "admitted"
                and data.get("request_id") == outbox["request_id"] == data.get("bridge_request")
                and data.get("round_id") == data.get("request_round")
                and data.get("instance_id") == data.get("bridge_instance") == data.get("round_instance") == data.get("submission_instance")
                and data.get("principal") == data.get("assigned_principal") == data.get("assigned_agent")
                and data.get("assignment_generation") == data.get("bridge_generation")
                and data.get("current_revision") == data.get("revision") == outbox["artifact_revision"]
                and data.get("namespace") == outbox["authority_namespace"]
                and data.get("bridge_state") == outbox["state"]
                and data.get("request_status") == "pending"
                and bool(outbox["recovery_owner"])
            )
            owner = outbox["claim_owner"] or outbox["recovery_owner"] or "workflow_recovery"
            if not coherent:
                code, action, deferred = "cutover_dispatch_closure", "reconcile_dispatch_closure", "U2D/U5"
            elif outbox["state"] in ("queued", "claimed") and not outbox["host_launch_started"]:
                code, action, deferred = "cutover_prelaunch_work", "cancel_prelaunch_work", "U2D/U4"
            elif outbox["state"] == "uncertain" or (outbox["state"] == "claimed" and outbox["host_launch_started"]):
                code, action, deferred = "cutover_uncertain_work", "reconcile_host_execution", "U5"
            else:
                code, action, deferred = "cutover_running_work", "await_callback_or_cancel", "U4/U5"
            blockers.append(self._blocker(code, record_id=outbox["id"], state=outbox["state"],
                                          owner=owner, required_action=action, deferred_to=deferred))
        for row in conn.execute("SELECT record_id,recovery_owner,state FROM workflow_recovery_claims ORDER BY record_id"):
            if row[2] == "claimed":
                blockers.append(self._blocker(
                    "cutover_recovery_owned_work", record_id=row[0], state=row[2], owner=row[1],
                    required_action="reconcile_owned_work", deferred_to="U5",
                ))
        if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) in ("E", "G"):
            for row in conn.execute("SELECT * FROM workflow_draft_dispatch_intents ORDER BY id"):
                intent = dict(row)
                if intent["state"] in ("cancelled", "failed", "completed"):
                    continue
                if intent["state"] in ("queued", "claimed") and not intent["host_launch_started"]:
                    code, action = "cutover_prelaunch_work", "cancel_prelaunch_work"
                elif intent["state"] == "uncertain" or (intent["state"] == "claimed" and intent["host_launch_started"]):
                    code, action = "cutover_uncertain_work", "reconcile_host_execution"
                else:
                    code, action = "cutover_running_work", "await_callback_or_cancel"
                blockers.append(self._blocker(code, record_id=intent["id"], state=intent["state"],
                                              owner=intent["claim_owner"] or intent["recovery_owner"],
                                              required_action=action, deferred_to="U2D/U4/U5"))
        if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) == "G":
            for row in conn.execute("SELECT o.submission_id FROM workflow_submission_operations o "
                                    "LEFT JOIN workflow_submission_result_links l ON l.submission_id=o.submission_id "
                                    "LEFT JOIN task_results r ON r.id=l.task_result_id "
                                    "LEFT JOIN workflow_draft_dispatch_intents d ON d.instance_id=o.instance_id "
                                    "AND d.task_id=o.source_task_id AND d.session_id=o.source_session_id "
                                    "WHERE l.submission_id IS NULL OR r.status!='completed' OR d.state!='completed'"):
                blockers.append(self._blocker("cutover_submission_pending", record_id=row[0],
                                              owner="workflow_recovery", required_action="reconcile_submission_source"))
            for table, predicate in (("workflow_rounds", "state='reviewing'"),
                                     ("workflow_review_requests", "status='pending'")):
                for row in conn.execute(f"SELECT id FROM {table} WHERE {predicate}"):
                    blockers.append(self._blocker("cutover_review_pending", record_id=row[0],
                                                  owner="workflow_recovery", required_action="resolve_pending_review"))
        return blockers

    def downgrade_preflight(self) -> dict:
        try:
            with self._transaction(write=False) as conn:
                marker, events = self._read(conn)
                blockers = self._integrity_blockers(conn)
                from runtime.identities.schema import validate_names
                if validate_names(conn, org_slug=self._org_slug):
                    blockers.append(self._blocker("naming_schema_requires_compatible_reader", owner="operator",
                                                  required_action="retain_compatible_runtime"))
                if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) in ("E", "G"):
                    layout = _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False)
                    blockers.append(self._blocker("submission_schema_requires_compatible_reader" if layout == "G" else "draft_schema_requires_compatible_reader", owner="operator",
                                                  required_action="retain_compatible_runtime"))
                if marker["generation"] != 1:
                    blockers.append(self._blocker(
                        "cutover_history_prevents_downgrade", owner="workflow_cutover_reconciler",
                        required_action="retain_current_runtime",
                    ))
                for table in self._workflow_tables(conn):
                    if table in ("workflow_adapter_versions", "workflow_cutover_state", "workflow_cutover_events", "workflow_draft_adapter_versions", "workflow_submission_schema_versions"):
                        continue
                    if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None:
                        blockers.append(self._blocker(
                            "cutover_data_prevents_downgrade", record_id=table, owner="workflow_cutover_reconciler",
                            required_action="retain_current_runtime",
                        ))
                return {"eligible": not blockers, "blockers": blockers,
                        "projection": self._projection(conn, marker, events)}
        except sqlite3.DatabaseError as exc:
            raise WorkflowCutoverError("cutover_operation_failed") from exc
