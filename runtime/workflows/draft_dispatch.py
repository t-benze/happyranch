"""Workflow-owned initial drafting intent and canonical lifecycle events."""
from __future__ import annotations

import hashlib
import logging
import json
import asyncio
import threading
import uuid
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from runtime.daemon.startup_recovery import document_admission_validation, require_document_admission
from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE, _DRAFT_PROJECTION, validate_workflow_schema

if TYPE_CHECKING:
    import sqlite3
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.startup_recovery import RecoveryOutcome
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
        self._startup_state = self._startup_org = self._startup_recovery = None
        self._live_lock = threading.RLock()
        self._live: dict[str, tuple[str, str, object | None]] = {}

    @contextmanager
    def _writer(self, *, validate_admission: Callable[[], None] | None = None) -> Iterator[sqlite3.Connection]:
        with self.db._lock:
            if validate_admission is not None:
                validate_admission()
            conn = self.db._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if validate_admission is not None:
                    validate_admission()
                yield conn
                validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                if validate_admission is not None:
                    validate_admission()
                conn.commit()
            except BaseException as primary:
                try:
                    conn.rollback()
                except BaseException as secondary:
                    if validate_admission is None:
                        raise
                    try:
                        # One actual private secondary survives note/format failure.
                        BaseException.__setattr__(primary, "_document_rollback_secondary",
                                                  (secondary, secondary.__traceback__))
                    except BaseException:
                        pass
                    try:
                        BaseException.add_note(primary,
                            f"selected document rollback also failed: {secondary!r}"[:1024])
                    except BaseException:
                        # Diagnostics cannot mask the original error/traceback.
                        pass
                raise

    def _intent(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Ordinary entry retains the activation owner's complete validation."""
        return self._intent_with_activation_closure(conn, task_id, self.org.workflow_activations._closure)

    def _intent_from_validated_schema(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Recovery-only composition after its complete same-snapshot read."""
        if not conn.in_transaction:
            raise DraftOwnershipError("workflow_activation_storage_corrupt")
        return self._intent_with_activation_closure(
            conn, task_id, self.org.workflow_activations._closure_from_validated_schema)

    def _intent_with_activation_closure(self, conn: sqlite3.Connection, task_id: str,
                                      closure: Callable[..., dict]) -> dict[str, Any]:
        # These two private entries select actual owner methods, never a
        # caller-supplied inventory or a Boolean claiming schema validation.
        rows = conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=?", (task_id,)).fetchall()
        if len(rows) != 1:
            raise DraftOwnershipError("workflow_activation_storage_corrupt")
        intent = dict(rows[0])
        if intent['admission_kind'] == 'revision':
            from runtime.workflows.revisions import validate_revision_record
            row = conn.execute('SELECT * FROM workflow_document_revision_operations WHERE draft_intent_id=?', (intent['id'],)).fetchone()
            if row is None:
                raise DraftOwnershipError('workflow_document_revision_storage_corrupt')
            validate_revision_record(conn, dict(row), expected_org_slug=self.org.slug)
        else:
            closure(conn, intent["activation_id"], actor=intent["admission_principal"])
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
        if intent['admission_kind'] == 'revision':
            self.org.workflow_revisions._authority(conn, intent, capture)
            return
        request = parse_request(json.loads(intent["request_bytes"]))
        template = self.org.workflow_activations._template(request)
        self.org.workflow_activations._roles(request, json.loads(capture.ready.snapshot_bytes), template)

    async def claim(self, task_id: str, *, validate_admission: Callable[[], None] | None = None) -> str | None:
        # Discovery and effective-profile reads precede all durable ownership.
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        capture = self.org.workflow_authority.capture_admission()
        validate()
        active_sessions = tuple(self.org.sessions.iter_active())
        async with self.org.workflow_authority._async_writer_lock:
            validate()
            async with self.org.db_lock:
                validate()
                with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
                    validate()
                    from runtime.infrastructure.team_move_store import validate_team_move_schema
                    from runtime.orchestrator.team_move import DeferredTeamMove
                    if validate_team_move_schema(conn, org_slug=self.org.slug):
                        raise DeferredTeamMove("team_move_unowned_workflow_producer")
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

    def dispatch(self, task_id: str, *, validate_admission: Callable[[], None] | None = None) -> None:
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        orch = self.org.orchestrator
        if orch._host_supervisor is None or orch._main_loop is None:
            return
        try:
            if getattr(self.db, "_task_pause_store", None) is not None:
                return self._dispatch_pause_owned(task_id, validate_admission=validate)
            claim = self._on_loop(self.claim(task_id, validate_admission=validate))
            if claim is None:
                return
            validate()
            task = self.db.get_task(task_id)
            validate()
            with self._live_lock:
                validate()
                self._live[task_id] = (claim, "", None)
            try:
                # A bounded author has no generic manager decision consumer.
                orch._run_agent(task_id, task.assigned_agent, task.brief)
            finally:
                import sys
                primary = sys.exception()
                try:
                    with self._live_lock:
                        live = self._live.get(task_id)
                    if live is not None:
                        # Owed original terminal settlement; stop grants no
                        # reason to fabricate failure or abandon native proof.
                        self.terminal(task_id, task.assigned_agent, live[1], None)
                        self._reconcile_core(task_id, settlement=live)
                except BaseException as exc:
                    if primary is None:
                        raise
                    primary.add_note(f"document native settlement also failed: {exc!r}")
                finally:
                    with self._live_lock:
                        self._live.pop(task_id, None)
        except DraftOwnershipError:
            # Ineligibility is a durable queued intent, not a fabricated run.
            return

    def _dispatch_pause_owned(self, task_id: str, *, validate_admission: Callable[[], None] | None = None) -> None:
        """Prepare a queued intent; final host arbitration owns every mutation."""
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        from runtime.orchestrator.task_pause import TaskInvocation, DeferredRootPause, _invocation
        from runtime.orchestrator.team_move import DeferredTeamMove
        from runtime.infrastructure.task_pause_controls import PauseControlError
        with self._live_lock:
            validate()
            if task_id in self._live:
                return
        with self.db._lock:
            validate()
            intent = self._intent(self.db._conn, task_id)
            if intent["state"] != "queued":
                return
            key = intent["host_execution_key"]
        validate()
        task = self.db.get_task(task_id)
        validate()
        invocation = TaskInvocation(self.org.orchestrator, task, task.assigned_agent,
                                    self.org.orchestrator._build_session_id(), owner="draft")
        invocation.workflow_dispatcher = self
        invocation.draft_request_key = key
        try:
            validate()
            invocation.prepare(validate_admission=validate)
        except PauseControlError as exc:
            if exc.detail["code"] == "root_paused":
                return
            raise
        except DeferredTeamMove:
            self.org.orchestrator._team_moves.defer_task(task_id, None)
            return
        except DeferredRootPause:
            return
        token = _invocation.set(invocation)
        try:
            self.org.orchestrator._run_agent(task_id, task.assigned_agent, task.brief)
        except (DeferredRootPause, DeferredTeamMove) as exc:
            if isinstance(exc, DeferredTeamMove):
                self.org.orchestrator._team_moves.defer_task(task_id, None)
        except BaseException:
            if invocation.admitted:
                invocation.phase("unknown")
            raise
        finally:
            from runtime.orchestrator.task_pause import release_live
            import sys
            failed = sys.exc_info()[0] is not None
            primary = sys.exception()
            try:
                # Keep the original invocation/current-context/live registration
                # until its narrow no-launch/uncertain reconciliation finishes.
                self._reconcile_core(task_id, settlement=invocation)
            except BaseException as exc:
                failed = True
                if primary is None:
                    raise
                primary.add_note(f"document reconciliation also failed: {exc!r}")
            finally:
                try:
                    _invocation.reset(token)
                    with self._live_lock:
                        self._live.pop(task_id, None)
                finally:
                    release_live(invocation, failed=failed)

    async def _commit_pause_launch(self, invocation: Any, ctx: Any) -> bool:
        # Capture every external read before synchronous ownership. Async locks
        # are acquired before entering the original synchronous fence interval;
        # no synchronous lock/lease crosses an event-loop wait.
        validate = document_admission_validation(self)
        capture = self.org.workflow_authority.capture_admission()
        validate()
        active_sessions = tuple(self.org.sessions.iter_active())
        with self._live_lock:
            validate()
            if invocation.task.id in self._live:
                raise DraftOwnershipError("workflow_claim_unavailable")
        async with self.org.workflow_authority._async_writer_lock:
            validate()
            async with self.org.db_lock:
                validate()
                authority = self.org.workflow_authority
                with authority._admission_ownership(capture, validate_admission=validate):
                    validate()
                    invocation.draft_capture = capture
                    invocation.draft_sessions = active_sessions
                    committed = invocation._commit_local(ctx, validate_admission=validate)
        if committed:
            with self._live_lock:
                validate()
                if invocation.task.id in self._live:
                    raise DraftOwnershipError("workflow_claim_stale")
                self._live[invocation.task.id] = (invocation.draft_claim, invocation.session_id, None)
        return committed

    def _claim_launch_uncommitted(self, invocation: Any) -> None:
        """Reuse the original intent checks/events in the final task writer."""
        require_document_admission(self)
        conn = self.db._conn
        capture = invocation.draft_capture
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        intent = self._intent(conn, invocation.task.id)
        self.db._check_team_move_workflow_uncommitted(intent["instance_id"])
        if (intent["state"] != "queued" or intent["host_launch_started"]
                or intent["assigned_principal"] != invocation.agent
                or intent["host_execution_key"] != invocation.draft_request_key):
            raise DraftOwnershipError("workflow_claim_stale")
        self._eligible(conn, intent, capture, invocation.draft_sessions)
        claim = uuid.uuid4().hex
        self._event(conn, intent, "claimed", changes={"state": "claimed", "claim_token": claim,
                                                    "claim_owner": "workflow_dispatcher"})
        intent = self._intent(conn, invocation.task.id)
        self._event(conn, intent, "launch_reserved", changes={"host_launch_started": 1,
                                                              "session_id": invocation.session_id})
        conn.execute("UPDATE tasks SET status='in_progress',current_session_id=?,updated_at=? WHERE id=?",
                     (invocation.session_id, datetime.now(timezone.utc).isoformat(), invocation.task.id))
        validate_workflow_schema(conn, expected_org_slug=self.org.slug)
        invocation.draft_claim = claim

    def notify_queued(self, task_id: str, queue: Any) -> RecoveryOutcome | None:
        """Rediscover eligible durable work on the periodic daemon sweep.

        A notification grants no claim or launch authority. Revalidate the
        authenticated queued intent and captured authority under the existing
        admission fences, then deduplicate notification after every lease exits.
        Ineligible work waits for a later tick, never an immediate retry loop.
        """
        from runtime.workflows.activation import WorkflowActivationError
        from runtime.workflows.authority import WorkflowAuthorityError
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError

        from runtime.daemon.startup_publication import current_publication
        from runtime.daemon.startup_recovery import RecoveryOutcome
        publication = current_publication(self.org.orchestrator)
        validate = document_admission_validation(self, publication=publication)
        if queue is not self._startup_state.queue:
            from runtime.daemon.startup_recovery import StartupUnavailable
            raise StartupUnavailable("startup_document_queue_changed")

        def held(code):
            if publication is not None:
                publication.count("held")
                return RecoveryOutcome("local_hold", code)
            return None

        with self._live_lock:
            validate()
            if task_id in self._live:
                return held("startup_document_live_owner")
        try:
            with self.db._lock:
                validate()
                if self._intent(self.db._conn, task_id)["state"] != "queued":
                    return held("startup_document_not_queued")
            capture = self.org.workflow_authority.capture_admission()
            validate()
            active_sessions = tuple(self.org.sessions.iter_active())
            with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
                validate()
                intent = self._intent(conn, task_id)
                if (intent["state"] != "queued" or intent["host_launch_started"]
                        or intent["session_id"] is not None
                        or intent["host_execution_id"] is not None
                        or intent["final_result_id"] is not None):
                    return held("startup_document_owner_ineligible")
                store = getattr(self.db, "_task_pause_store", None)
                if store is not None:
                    from runtime.infrastructure.task_pause_controls import PauseControlError
                    try:
                        store.held_uncommitted(task_id)
                    except PauseControlError as exc:
                        if publication is not None and exc.detail["code"] != "root_paused":
                            raise
                        return held("root_paused")
                self._eligible(conn, intent, capture, active_sessions)
        except (DraftOwnershipError, WorkflowActivationError,
                WorkflowAuthorityError, ProfileCoordinatorError) as exc:
            if publication is not None:
                from runtime.workflows.review_join import HOLDS
                code = str(exc) if isinstance(exc, DraftOwnershipError) else exc.code
                if code not in HOLDS | {"workflow_activation_author_pending"}:
                    raise
                return held(code)
            return
        validate()
        if publication is not None:
            from runtime.orchestrator.run_step import _publish_redelivery_task
            return _publish_redelivery_task(self.org.orchestrator, task_id,
                                            generation_aware=False)
        queue.enqueue_if_absent(self.org.slug, task_id)

    def bind_session(self, task_id: str, agent: str, session_id: str) -> None:
        validate = document_admission_validation(self)
        with self._live_lock:
            validate()
            live = self._live.get(task_id)
            if live is None:
                raise DraftOwnershipError("workflow_claim_unavailable")
            with self.db._lock:
                validate()
                with self._writer(validate_admission=validate) as conn:
                    validate()
                    intent = self._intent(conn, task_id)
                    if (intent["claim_token"] != live[0] or intent["state"] != "claimed"
                            or intent["cancellation_requested"] or intent["assigned_principal"] != agent
                            or intent["host_launch_started"]):
                        raise DraftOwnershipError("workflow_claim_stale")
                    # Session registration is not an observed launch and does not
                    # put a session identity in the queued/prelaunch intent.
                    conn.execute("UPDATE tasks SET current_session_id=? WHERE id=?", (session_id, task_id))
            self._live[task_id] = (live[0], session_id, None)

    async def _reserve_launch(self, task_id: str, agent: str, session_id: str, *, validate_admission: Callable[[], None] | None = None) -> None:
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        with self._live_lock:
            validate()
            live = self._live.get(task_id)
        capture = self.org.workflow_authority.capture_admission()
        validate()
        active_sessions = tuple(self.org.sessions.iter_active())
        refusal = None
        async with self.org.workflow_authority._async_writer_lock:
            validate()
            async with self.org.db_lock:
                validate()
                with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
                    validate()
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
        validate = document_admission_validation(self)
        return self._on_loop(self._reserve_launch(task_id, agent, session_id, validate_admission=validate))

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
        from runtime.orchestrator.run_step import _capture_root_worktree_transition, _RootWorktreeTransitions, _root_worktree_batch
        root_transitions = _RootWorktreeTransitions()
        staged_transitions = _RootWorktreeTransitions()
        root_tail_failed = False
        try:
            with self._live_lock:
                live = self._live.get(task_id)
                with self._writer() as conn:
                    with _capture_root_worktree_transition(self.org.orchestrator, task_id, 'draft_dispatch.terminal', staged_transitions):
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
            # The existing writer committed and released its locks.
            root_transitions.extend(staged_transitions)
        except BaseException:
            root_tail_failed = True
            raise
        finally:
            if root_transitions:
                _root_worktree_batch(self.org.orchestrator, root_transitions, teardown_incomplete=root_tail_failed)

    def cancel(self, task_id: str) -> dict:
        # Durable cancellation wins before any opaque containment operation.
        from runtime.orchestrator.run_step import _capture_root_worktree_transition, _RootWorktreeTransitions, _root_worktree_batch
        root_transitions = _RootWorktreeTransitions()
        staged_transitions = _RootWorktreeTransitions()
        root_tail_failed = False
        try:
            with self._writer() as conn:
                with _capture_root_worktree_transition(self.org.orchestrator, task_id, 'draft_dispatch.cancel', staged_transitions):
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
            # Publish only the original successfully committed transition.
            root_transitions.extend(staged_transitions)
            for _agent, control in self.org.sessions.iter_task_cancel_controls(task_id):
                control()
            with self.db._lock:
                current = self._intent(self.db._conn, task_id)
                return self.projection(current)
        except BaseException:
            root_tail_failed = True
            raise
        finally:
            if root_transitions:
                _root_worktree_batch(self.org.orchestrator, root_transitions, teardown_incomplete=root_tail_failed)

    @staticmethod
    def projection(intent: dict[str, Any]) -> dict[str, Any]:
        return dict(ok=True, task_id=intent["task_id"], state=intent["state"],
                    cancellation_requested=bool(intent["cancellation_requested"]),
                    pending=intent["state"] in {"queued", "claimed", "running", "uncertain"},
                    reconciliation_required=intent["state"] == "uncertain",
                    execution_started=bool(intent["host_execution_id"]))

    def _check_dispatch_settlement(self, task_id: str, settlement: Any) -> None:
        from runtime.daemon.startup_recovery import _document_attachment
        from runtime.orchestrator.task_pause import current_invocation, TaskInvocation
        _document_attachment(self)
        if isinstance(settlement, TaskInvocation):
            valid = (current_invocation() is settlement and settlement.orch is self.org.orchestrator
                     and settlement.workflow_dispatcher is self and settlement.task.id == task_id
                     and settlement.store is getattr(self.db, "_task_pause_store", None)
                     and settlement.store._live.get(settlement.identity) is settlement)
        else:
            valid = (getattr(self.db, "_task_pause_store", None) is None
                     and settlement is not None and self._live.get(task_id) is settlement)
        if not valid:
            raise DraftOwnershipError("workflow_native_settlement_owner_changed")

    @contextmanager
    def _completed_followup(self, task_id: str) -> Iterator[None]:
        """Only hold downstream effects of an authentic retained native completion.

        This grants no writer bypass. Ordinary callers still receive refusal;
        native terminal/tracker/capacity/finally is not made to fail merely
        because its committed receipt/graph delivery must wait for discovery.
        """
        from runtime.daemon.startup_recovery import StartupUnavailable
        from runtime.orchestrator.task_pause import current_invocation
        with self._live_lock:
            original = self._live.get(task_id)
        invocation = current_invocation()
        try:
            yield
        except StartupUnavailable as exc:
            with self._live_lock:
                with self.db._lock:
                    if original is None or self._live.get(task_id) is not original:
                        raise
                    if getattr(self.db, "_task_pause_store", None) is not None:
                        if invocation is None:
                            raise
                        self._check_dispatch_settlement(task_id, invocation)
                    intent = self._intent(self.db._conn, task_id)
                    result = self._accepted_result(self.db._conn, intent)
                    if (intent["state"] != "completed" or original[2] is None
                            or intent["session_id"] != original[1] or result is None
                            or result["record"]["status"] != "completed"):
                        raise
            logging.getLogger(__name__).warning(
                "Document native completion committed; dependent collection/publication pending: task=%s code=%s",
                task_id, exc.code)

    def reconcile(self, task_id: str, *, recovery: Any = None, startup_owner: Any = None) -> None:
        validate = document_admission_validation(self, recovery=recovery, sweep_owner=startup_owner)
        self._reconcile_core(task_id, recovery=recovery, startup_owner=startup_owner, validate_admission=validate)

    def _reconcile_core(self, task_id: str, *, recovery: Any = None,
                        startup_owner: Any = None, settlement: Any = None,
                        validate_admission: Callable[[], None] | None = None) -> None:
        validate = None
        if settlement is None:
            validate = (document_admission_validation(self, recovery=recovery, sweep_owner=startup_owner)
                        if validate_admission is None else validate_admission)
            validate()
        def check() -> None:
            if settlement is None:
                validate()
            else:
                if recovery is not None or startup_owner is not None:
                    raise DraftOwnershipError("workflow_native_settlement_owner_changed")
                self._check_dispatch_settlement(task_id, settlement)

        # Blocking locks renew before the original writer; native settlement
        # retains its actual live invocation, never a generic readiness flag.
        with self._live_lock:
            check()
            with self.db._lock:
                check()
                live = settlement is None and task_id in self._live
                with self._writer(validate_admission=validate) as conn:
                    check()
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
