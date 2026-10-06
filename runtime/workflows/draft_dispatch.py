"""Workflow-owned initial drafting intent and canonical lifecycle events."""
from __future__ import annotations

import hashlib
import json
import asyncio
import threading
import uuid
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE, _DRAFT_PROJECTION, validate_workflow_schema

if TYPE_CHECKING:
    import sqlite3
    from runtime.daemon.org_state import OrgState
    from runtime.platform.session_backend import RunningHandle
    from runtime.workflows.authority import AuthorityAdmissionCapture


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def append_event_uncommitted(conn: sqlite3.Connection, *, org_slug: str, intent_id: str, kind: str, before: dict[str, Any] | None,
                             terminal_evidence: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> None:
    """Join the caller's writer transaction; never commit or acquire a lease."""
    intent = dict(conn.execute(
        "SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (intent_id,),
    ).fetchone())
    previous = conn.execute(
        "SELECT event_seq,event_digest FROM workflow_draft_dispatch_events "
        "WHERE intent_id=? ORDER BY event_seq DESC LIMIT 1", (intent_id,),
    ).fetchone()
    sequence = 1 if previous is None else previous["event_seq"] + 1
    timestamp = intent["created_at"] if previous is None else datetime.now(timezone.utc).isoformat()
    identity = dict(id=f"{intent_id}:{sequence}", event_seq=sequence,
                    event_kind=kind, created_at=timestamp)
    after = {key: intent[key] for key in _DRAFT_PROJECTION}
    immutable = {key: value.hex() if isinstance(value, bytes) else value
                 for key, value in intent.items() if key not in _DRAFT_MUTABLE}
    payload = canonical_bytes(dict(
        format="workflow-draft-event@1", org_slug=org_slug, event=identity,
        previous_digest=None if previous is None else previous["event_digest"],
        intent=immutable, before=before, after=after,
        terminal_evidence=terminal_evidence, result=result,
    ))
    conn.execute(
        "INSERT INTO workflow_draft_dispatch_events "
        "(id,intent_id,event_seq,event_kind,state_before,state_after,event_bytes,event_digest,"
        "session_id,result_id,result_digest,callback_accepted,disposition,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (identity["id"], intent_id, sequence, kind, None if before is None else before["state"],
         after["state"], payload, digest(payload), after["session_id"],
         result["id"] if kind in {"callback_recorded", "callback_rejected"} else None,
         result["digest"] if kind in {"callback_recorded", "callback_rejected"} else None,
         result["accepted"] if kind in {"callback_recorded", "callback_rejected"} else None,
         result["disposition"] if kind in {"callback_recorded", "callback_rejected"} else None,
         timestamp),
    )
    conn.execute("UPDATE workflow_draft_dispatch_intents SET updated_at=? WHERE id=?", (timestamp, intent_id))


class DraftOwnershipError(ValueError):
    """A safe, bounded refusal; never carries private context or host tokens."""


class WorkflowDraftDispatcher:
    """The real initial task's exclusive claim, callback and terminal owner.

    In-memory identities establish only this boot's affirmative host evidence.
    A cold process cannot recover them from a PID or a session registration.
    The durable possible-launch fence therefore survives missing acknowledgments.
    """

    def __init__(self, org: OrgState) -> None:
        self.org = org
        self.db = org.db
        self._live_lock = threading.RLock()
        self._live: dict[str, tuple[str, str, object | None]] = {}

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        with self.db._lock:
            conn = self.db._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _intent(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        rows = conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=?", (task_id,)).fetchall()
        if len(rows) != 1:
            raise DraftOwnershipError("workflow_activation_storage_corrupt")
        intent = dict(rows[0])
        self.org.workflow_activations._closure(conn, intent["activation_id"], actor=intent["admission_principal"])
        if conn.execute("SELECT 1 FROM workflow_request_task_bridges WHERE task_id=?", (task_id,)).fetchone():
            raise DraftOwnershipError("workflow_activation_storage_corrupt")
        return intent

    def _event(self, conn: sqlite3.Connection, intent: dict[str, Any], kind: str, *, changes: dict[str, Any] | None = None, terminal_evidence: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> None:
        before = {key: intent[key] for key in _DRAFT_PROJECTION}
        if changes:
            if not set(changes) <= _DRAFT_MUTABLE:
                raise DraftOwnershipError("workflow_activation_storage_corrupt")
            conn.execute("UPDATE workflow_draft_dispatch_intents SET "
                         + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                         (*changes.values(), intent["id"]))
        append_event_uncommitted(conn, org_slug=self.org.slug, intent_id=intent["id"],
                                 kind=kind, before=before, terminal_evidence=terminal_evidence, result=result)

    def _eligible(self, conn: sqlite3.Connection, intent: dict[str, Any], capture: AuthorityAdmissionCapture, active_sessions: tuple[tuple[str, str, str], ...]) -> None:
        pointer = conn.execute("SELECT * FROM workflow_active_activations WHERE instance_id=?", (intent["instance_id"],)).fetchone()
        cutover = conn.execute("SELECT state FROM workflow_cutover_state WHERE singleton=1").fetchone()
        if (not intent["is_current"] or intent["cancellation_requested"] or pointer is None
                or pointer["activation_id"] != intent["activation_id"]
                or pointer["activation_revision"] != intent["activation_revision"]
                or cutover is None or cutover[0] != "enabled"
                or capture.ready.generation != intent["authority_generation"]
                or capture.ready.snapshot_digest != intent["authority_digest"]):
            raise DraftOwnershipError("workflow_activation_authority_stale")
        from runtime.workflows.activation import author_capacity_blocked, parse_request
        if author_capacity_blocked(conn, author=intent["assigned_principal"], task_id=intent["task_id"],
                                   active_sessions=active_sessions):
            raise DraftOwnershipError("workflow_activation_author_pending")
        request = parse_request(json.loads(intent["request_bytes"]))
        self.org.workflow_activations._roles(request, json.loads(capture.ready.snapshot_bytes))

    async def claim(self, task_id: str) -> str | None:
        # Discovery and effective-profile reads precede all durable ownership.
        capture = self.org.workflow_authority.capture_admission()
        active_sessions = tuple(self.org.sessions.iter_active())
        async with self.org.workflow_authority._async_writer_lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    intent = self._intent(conn, task_id)
                    if intent["state"] != "queued":
                        return None
                    self._eligible(conn, intent, capture, active_sessions)
                    claim = uuid.uuid4().hex
                    self._event(conn, intent, "claimed", changes={"state": "claimed", "claim_token": claim,
                                                                  "claim_owner": "workflow_dispatcher"})
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute("UPDATE tasks SET status='in_progress',updated_at=? WHERE id=?",
                                 (now, task_id))
                    validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                    return claim

    def _on_loop(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        loop = self.org.orchestrator._main_loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise DraftOwnershipError("workflow_host_unavailable")
        # The worker owns no lease or SQLite transaction across this wait.
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def dispatch(self, task_id: str) -> None:
        orch = self.org.orchestrator
        if orch._host_supervisor is None or orch._main_loop is None:
            return
        try:
            claim = self._on_loop(self.claim(task_id))
            if claim is None:
                return
            task = self.db.get_task(task_id)
            with self._live_lock:
                self._live[task_id] = (claim, "", None)
            try:
                # A bounded author has no generic manager decision consumer.
                orch._run_agent(task_id, task.assigned_agent, task.brief)
            finally:
                with self._live_lock:
                    live = self._live.get(task_id)
                if live is not None:
                    # A returned/raised setup path with no reservation is
                    # affirmative no-launch. Possible launch remains uncertain.
                    self.terminal(task_id, task.assigned_agent, live[1], None)
                with self._live_lock:
                    self._live.pop(task_id, None)
                self.reconcile(task_id)
        except DraftOwnershipError:
            # Ineligibility is a durable queued intent, not a fabricated run.
            return

    def notify_queued(self, task_id: str, queue: Any) -> None:
        """Rediscover eligible durable work on the periodic daemon sweep.

        A notification grants no claim or launch authority. Revalidate the
        authenticated queued intent and captured authority under the existing
        admission fences, then deduplicate notification after every lease exits.
        Ineligible work waits for a later tick, never an immediate retry loop.
        """
        from runtime.workflows.activation import WorkflowActivationError
        from runtime.workflows.authority import WorkflowAuthorityError
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError

        with self._live_lock:
            if task_id in self._live:
                return
        try:
            with self.db._lock:
                if self._intent(self.db._conn, task_id)["state"] != "queued":
                    return
            capture = self.org.workflow_authority.capture_admission()
            active_sessions = tuple(self.org.sessions.iter_active())
            with self.org.workflow_authority.admission_writer(capture) as conn:
                intent = self._intent(conn, task_id)
                if (intent["state"] != "queued" or intent["host_launch_started"]
                        or intent["session_id"] is not None
                        or intent["host_execution_id"] is not None
                        or intent["final_result_id"] is not None):
                    return
                self._eligible(conn, intent, capture, active_sessions)
        except (DraftOwnershipError, WorkflowActivationError,
                WorkflowAuthorityError, ProfileCoordinatorError):
            return
        queue.enqueue_if_absent(self.org.slug, task_id)

    def bind_session(self, task_id: str, agent: str, session_id: str) -> None:
        with self._live_lock:
            live = self._live.get(task_id)
            if live is None:
                raise DraftOwnershipError("workflow_claim_unavailable")
            with self._writer() as conn:
                intent = self._intent(conn, task_id)
                if (intent["claim_token"] != live[0] or intent["state"] != "claimed"
                        or intent["cancellation_requested"] or intent["assigned_principal"] != agent
                        or intent["host_launch_started"]):
                    raise DraftOwnershipError("workflow_claim_stale")
                # Session registration is not an observed launch and does not
                # put a session identity in the queued/prelaunch intent.
                conn.execute("UPDATE tasks SET current_session_id=? WHERE id=?", (session_id, task_id))
            self._live[task_id] = (live[0], session_id, None)

    async def _reserve_launch(self, task_id: str, agent: str, session_id: str) -> None:
        with self._live_lock:
            live = self._live.get(task_id)
        capture = self.org.workflow_authority.capture_admission()
        active_sessions = tuple(self.org.sessions.iter_active())
        refusal = None
        async with self.org.workflow_authority._async_writer_lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    intent = self._intent(conn, task_id)
                    if (live is None or live[:2] != (intent["claim_token"], session_id)
                            or intent["assigned_principal"] != agent or intent["state"] != "claimed"
                            or intent["host_launch_started"]):
                        raise DraftOwnershipError("workflow_claim_stale")
                    try:
                        self._eligible(conn, intent, capture, active_sessions)
                    except DraftOwnershipError as exc:
                        if str(exc) != "workflow_activation_author_pending" or intent["cancellation_requested"]:
                            raise
                        # Capacity changed after claim/session registration,
                        # before the actual launch reservation. Keep this same
                        # attempt pending. Commit the no-launch requeue before
                        # the supervisor receives its prelaunch refusal; the
                        # retired claim cannot subsequently terminalize it.
                        self._event(conn, intent, "requeued", changes={"state": "queued", "claim_token": None, "claim_owner": None})
                        conn.execute("UPDATE tasks SET status='pending',current_session_id=NULL WHERE id=?", (task_id,))
                        refusal = exc
                    if refusal is None:
                        self._event(conn, intent, "launch_reserved", changes={"host_launch_started": 1, "session_id": session_id})
                    validate_workflow_schema(conn, expected_org_slug=self.org.slug)
        if refusal is not None:
            raise refusal

    def reserve_launch(self, task_id: str, agent: str, session_id: str) -> None:
        return self._on_loop(self._reserve_launch(task_id, agent, session_id))

    def host_request_key(self, task_id: str, agent: str, session_id: str) -> str:
        with self.db._lock:
            intent = self._intent(self.db._conn, task_id)
            task = self.db._conn.execute("SELECT assigned_agent,current_session_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None or task["assigned_agent"] != agent or task["current_session_id"] != session_id:
                raise DraftOwnershipError("workflow_claim_stale")
            return intent["host_execution_key"]

    def observed_handle(self, task_id: str, agent: str, session_id: str, running: RunningHandle) -> None:
        with self._live_lock:
            live = self._live.get(task_id)
            with self._writer() as conn:
                intent = self._intent(conn, task_id)
                if (live is None or live[:2] != (intent["claim_token"], session_id)
                        or intent["assigned_principal"] != agent or intent["session_id"] != session_id
                        or intent["state"] != "claimed" or not intent["host_launch_started"]):
                    raise DraftOwnershipError("workflow_claim_stale")
                # Passthrough's placeholder handle has no actual launched
                # process. It can never manufacture running/terminal proof.
                host_bound = (running.process is not None and bool(running.token)
                              and running.request_id == intent["host_execution_key"])
                if not host_bound:
                    self._event(conn, intent, "uncertain", changes={"state": "uncertain", "last_error": "workflow_host_evidence_unavailable"})
                else:
                    # A one-way identity digest is not a cancellation capability;
                    # only the supervisor/tracker keeps and invokes host controls.
                    identity = digest(canonical_bytes({"backend": running.backend, "token": running.token,
                                                        "request_id": running.request_id}))
                    self._event(conn, intent, "running", changes={"state": "running", "host_execution_id": identity})
            if not host_bound:
                raise DraftOwnershipError("workflow_host_evidence_unavailable")
            self._live[task_id] = (live[0], session_id, running)

    def _accepted_result(self, conn: sqlite3.Connection, intent: dict[str, Any]) -> dict[str, Any] | None:
        rows = conn.execute("SELECT event_bytes FROM workflow_draft_dispatch_events "
                            "WHERE intent_id=? AND event_kind='callback_recorded'", (intent["id"],)).fetchall()
        if len(rows) != 1:
            return None
        return json.loads(rows[0][0])["result"]

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str, payload: dict[str, Any], v2_admission: dict[str, Any] | None = None) -> bool:
        """Consumed only inside Database's real INTEGER result admission writer."""
        from runtime.infrastructure.database import completion_result_payload_matches
        conn = self.db._conn
        intent = self._intent(conn, task_id)
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        existing = conn.execute("SELECT * FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                                (task_id, agent, session_id)).fetchall()
        if len(existing) > 1:
            return False
        if existing:
            accepted = self._accepted_result(conn, intent)
            return bool(accepted is not None and intent["final_result_id"] == existing[0]["id"]
                        and intent["session_id"] == session_id and intent["assigned_principal"] == agent
                        and accepted["record"] == dict(existing[0])
                        and completion_result_payload_matches(existing[0], **payload))
        if (intent["state"] not in {"claimed", "running"} or not intent["host_launch_started"]
                or intent["session_id"] != session_id or intent["assigned_principal"] != agent
                or intent["cancellation_requested"] or not intent["is_current"]
                or intent["final_result_id"] is not None or task["current_session_id"] != session_id
                or task["status"] != "in_progress" or task["cancelled_at"] is not None
                or (payload["decision_json"] is not None and set(json.loads(payload["decision_json"])) - {"_manager_self_evaluation"})
                or payload["waiting_on_job_ids"] is not None):
            return False
        self.db._insert_task_result(task_id=task_id, agent=agent, session_id=session_id, **payload)
        result_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        record = dict(conn.execute("SELECT * FROM task_results WHERE id=?", (result_id,)).fetchone())
        result = dict(record=record, id=result_id, digest=digest(canonical_bytes(record)), accepted=1,
                      disposition="accepted" if intent["state"] == "running" else "pending_host_acknowledgment")
        self._event(conn, intent, "callback_recorded", changes={"final_result_id": result_id}, result=result)
        return True

    def callback_replay(self, *, task_id: str, agent: str, session_id: str, payload: dict[str, Any]) -> bool:
        """Authenticate the whole stored receipt without changing current work."""
        from runtime.infrastructure.database import completion_result_payload_matches
        with self.db._lock:
            intent = self._intent(self.db._conn, task_id)
            rows = self.db._conn.execute("SELECT * FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                                         (task_id, agent, session_id)).fetchall()
            accepted = self._accepted_result(self.db._conn, intent)
            return bool(len(rows) == 1 and accepted is not None
                        and intent["assigned_principal"] == agent and intent["session_id"] == session_id
                        and intent["final_result_id"] == rows[0]["id"] and accepted["record"] == dict(rows[0])
                        and completion_result_payload_matches(rows[0], **payload))

    def terminal(self, task_id: str, agent: str, session_id: str, outcome: Any, *, expected_request: Any = None) -> None:
        with self._live_lock:
            live = self._live.get(task_id)
            with self._writer() as conn:
                intent = self._intent(conn, task_id)
                if (live is None or live[:2] != (intent["claim_token"], session_id)
                        or intent["assigned_principal"] != agent
                        or intent["state"] in {"cancelled", "failed", "completed"}):
                    return
                receipt = None if outcome is None else outcome.receipt
                running = live[2]
                quiescent = (outcome is not None and expected_request is not None
                             and outcome.request is expected_request and outcome.attempt == 0
                             and expected_request.retry_attempt == 0
                             and running is not None and receipt is not None
                             and outcome.request.org == self.org.slug and outcome.request.logical_id == intent["host_execution_key"]
                             and running.request_id == intent["host_execution_key"]
                             and receipt.backend == running.backend and receipt.quiescent is True
                             and receipt.cleanup_status.value != "incomplete" and not receipt.survivors)
                no_launch = not intent["host_launch_started"]
                if not quiescent and not no_launch:
                    if intent["state"] != "uncertain":
                        self._event(conn, intent, "uncertain", changes={"state": "uncertain", "last_error": "workflow_host_quiescence_unavailable"})
                    return
                result = self._accepted_result(conn, intent)
                state = ("cancelled" if intent["cancellation_requested"] else "completed" if quiescent
                         and result is not None and result["record"]["status"] == "completed" else "failed"
                         if no_launch or (quiescent and result is not None and result["record"]["status"] == "failed") else "uncertain")
                if state == "uncertain":
                    if intent["state"] != "uncertain":
                        self._event(conn, intent, "uncertain", changes={"state": state, "last_error": "workflow_callback_incomplete"})
                    return
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE tasks SET status=?,completed_at=?,updated_at=?,block_kind=NULL WHERE id=?", (state, now, now, task_id))
                self._event(conn, intent, state, changes={"state": state},
                            terminal_evidence={"host_quiescent": True} if quiescent else None,
                            result=result if state == "completed" else None)

    def cancel(self, task_id: str) -> dict:
        # Durable cancellation wins before any opaque containment operation.
        with self._writer() as conn:
            intent = self._intent(conn, task_id)
            if intent["state"] in {"cancelled", "failed", "completed"}:
                return self.projection(intent)
            if not intent["cancellation_requested"]:
                self._event(conn, intent, "cancel_requested", changes={"cancellation_requested": 1})
                intent = dict(conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (intent["id"],)).fetchone())
            # A committed prelaunch state0, not PID absence, proves this
            # intent cannot have crossed the reserved launch boundary.
            if not intent["host_launch_started"]:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE tasks SET status='cancelled',cancelled_at=?,completed_at=?,updated_at=?,block_kind=NULL WHERE id=?",
                             (now, now, now, task_id))
                self._event(conn, intent, "cancelled", changes={"state": "cancelled"})
        for _agent, control in self.org.sessions.iter_task_cancel_controls(task_id):
            control()
        with self.db._lock:
            current = self._intent(self.db._conn, task_id)
            return self.projection(current)

    @staticmethod
    def projection(intent: dict[str, Any]) -> dict[str, Any]:
        return dict(ok=True, task_id=intent["task_id"], state=intent["state"],
                    cancellation_requested=bool(intent["cancellation_requested"]),
                    pending=intent["state"] in {"queued", "claimed", "running", "uncertain"},
                    reconciliation_required=intent["state"] == "uncertain",
                    execution_started=bool(intent["host_execution_id"]))

    def reconcile(self, task_id: str) -> None:
        with self._live_lock:
            live = task_id in self._live
        with self._writer() as conn:
            intent = self._intent(conn, task_id)
            if intent["state"] not in {"claimed", "running"}:
                return
            # A live claim must not be recovered by a competing reaper.
            if live:
                return
            if not intent["host_launch_started"]:
                self._event(conn, intent, "requeued", changes={"state": "queued", "claim_token": None, "claim_owner": None})
                conn.execute("UPDATE tasks SET status='pending',current_session_id=NULL WHERE id=?", (task_id,))
            else:
                self._event(conn, intent, "uncertain", changes={"state": "uncertain", "last_error": "workflow_host_identity_unavailable"})
