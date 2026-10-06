from __future__ import annotations

# Database facade domain map:
# - db/dreams.py: dream records and dream KB candidates, except the _now keeper
# - db/knowledge.py: KB stats, skill validation reads, and org settings
# - db/jobs.py: job records, transitions, and recovery
# - db/attachments.py: thread-scoped and task attachment reads/deletes
# - db/audit.py: generic audit-log writes and reads
# - db/sessions.py: token usage, aggregation, and thread session state
# - db/workspace_cleanup.py: cleanup selection and stale-pending observation
# - db/threads.py: thread core, participants, messages, and invocations
# - db/reply_delivery.py: reply delivery, recovery, breaker, and settlement
# - db/reply_exchange.py: strict reply exchange lifecycle and projections
# - db/schema.py: schema bootstrap, migrations, and their module-level closure
# - db/authority_v1.py: v1 authority claims, fences, and continue envelopes
# - db/authority_policy.py: authority policy release, activation, selector, and session binding
# - db/authority_v2_attempts.py: v2 attempts, candidates, refusal, and finalization
# - db/authority_v2_continuation.py: v2 continuation, settlement, publication, generation, spend, decision dispatch, and zombie consumption
# - db/tasks.py: task core CRUD, queries, severity, lineage, recall, and retry/delegation, claim/supersession, chain advance, state queries, recovery-ledger lifecycle, and completion-result readers/projection, atomic task/attachment admission, and task-followup replacement, and agent termination cleanup
# - facade: callback admission, result writers, escalation, and cross-domain writers
# - database.py: remaining domains and patched-global write keepers

import hashlib
import json
import logging
import sqlite3
import threading
import time as _time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from runtime.infrastructure.db._shared import _parse_dt, _synchronized
from runtime.infrastructure.db.audit import AuditMixin
from runtime.infrastructure.db.attachments import AttachmentsMixin
from runtime.infrastructure.db.authority_v1 import (
    AuthorityV1Mixin,
    _AUTHORITY_APPROVED_VERDICTS,
    _AUTHORITY_TERMINAL_STATUSES,
    _authority_claim_key,
    _parse_authority_fence_results,
    _serialize_authority_audit_payload,
    _serialize_authority_fence_results,
    _validate_authority_class,
)
from runtime.infrastructure.db.authority_policy import (
    AuthorityPolicyMixin,
    _AUTHORITY_POLICY_SELECTOR_SESSION_BINDING_ACTION,
    _AUTHORITY_POLICY_SESSION_BINDING_ACTION,
)
from runtime.infrastructure.db.authority_v2_attempts import (
    AuthorityV2AttemptsMixin,
    _AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING,
)
from runtime.infrastructure.db.authority_v2_continuation import (
    AuthorityV2ContinuationMixin,
    _V2_ADMISSION_RESULT_STAGE_KEYS,
    _V2_ALL_RESULT_STAGE_KEY_SETS,
    _V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS,
    _V2_ATTEMPT_RESULT_STAGE_KEYS,
    _V2_CONTINUED_RESULT_STAGE_KEYS,
    _V2_DECISION_RESULT_STAGE_KEYS,
    _V2_INVALIDATION_RESULT_STAGE_KEYS,
    _V2_MALFORMED_DECISION,
    _V2_PUBLICATION_RESULT_STAGE_KEYS,
    _V2_REFUSAL_RESULT_STAGE_KEYS,
    _V2_REFUSAL_RESULT_STAGE_KEYS_WITH_CANDIDATE,
    _V2_SPEND_OTHER_RESULT_STAGE_KEY_SETS,
    _V2_SPEND_RESULT_STAGE_KEYS,
    _canonical_completion_json,
)
from runtime.infrastructure.db.dreams import DreamsMixin
from runtime.infrastructure.db.jobs import JobsMixin
from runtime.infrastructure.db.knowledge import KnowledgeMixin
from runtime.infrastructure.db.sessions import SessionsMixin
from runtime.infrastructure.db.tasks import (
    LineageTooDeep,
    TasksMixin,
    VerifiedRetry,
    InvalidLineage,
    RetryClaim,
    Committed,
    LostClaim,
    SpawnOutcome,
    PendingRetry,
    _RetryEvidenceRefusal,
)
from runtime.infrastructure.db.threads import ThreadsMixin
from runtime.infrastructure.db.reply_delivery import ReplyDeliveryMixin
from runtime.infrastructure.db.reply_exchange import (
    EXCHANGE_GRACE_SECONDS,
    MAX_PRIORITY_WAIT_SECONDS,
    ReplyExchangeMixin,
)
from runtime.infrastructure.db.schema import (
    AuthorityAuditMigrationRefusal,
    SchemaMixin,
    _AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL,
    _AUTHORITY_POLICY_ACTIVATIONS_VALIDATE_INSERT_SQL,
    _AUTHORITY_POLICY_V2_CONTROL_SCHEMA_SQL,
    _rebuild_indexes_for,
)
from runtime.infrastructure.db.workspace_cleanup import (
    WorkspaceCleanupMarkerHistorySummary,
    WorkspaceCleanupMixin,
    WorkspaceCleanupReclamationCandidate,
    WorkspaceCleanupReclamationSelection,
    _CLEANUP_HISTORY_PAGE_SIZE,
    _STALE_PENDING_JOBS_SCAN_SQL,
    _WORKSPACE_CLEANUP_BRIEF_MARKER,
    _WORKSPACE_CLEANUP_TERMINAL_STATUSES,
    _has_raw_iso_hour_24,
    _is_aware_datetime,
    _iso_datetime_separator_index,
    _scan_stale_pending_jobs_direct_wal,
    scan_stale_pending_jobs_readonly,
)
from runtime.models import (
    AuthorityAuditEvent,
    AuthorityAuditEventType,
    AuthorityAuditPayload,
    AuthorityCandidate,
    AuthorityCandidatePolicyPin,
    AuthorityEvaluation,
    AuthorityPolicyV2Attempt,
    AuthorityPolicyV2Candidate,
    AuthorityPolicyV2CandidateAudit,
    AuthorityPolicyV2ContinueEnvelope,
    AuthorityPolicyV2CompletionDispatchContext,
    AuthorityPolicyV2DecisionAckOutcome,
    AuthorityPolicyV2DecisionClaimOutcome,
    AuthorityPolicyV2DecisionRefusalOutcome,
    AuthorityPolicyV2EnqueueDispatchClassification,
    AuthorityPolicyV2Evaluation,
    AuthorityPolicyV2FinalizationOutcome,
    AuthorityPolicyV2GenerationClaimOutcome,
    AuthorityPolicyV2AdmissionSettlementOutcome,
    AuthorityPolicyV2HousekeepingOutcome,
    AuthorityPolicyV2HousekeepingTarget,
    AuthorityPolicyV2Pin,
    AuthorityPolicyV2PublicationAckOutcome,
    AuthorityPolicyV2PublicationClaimOutcome,
    AuthorityPolicyV2PublicationFailureOutcome,
    AuthorityPolicyV2PublicationTarget,
    AuthorityPolicyV2InvalidationOutcome,
    AuthorityPolicyV2RecoveryNotification,
    AuthorityPolicyV2RootDispatch,
    AuthorityPolicyV2SessionBinding,
    AuthorityPolicyV2SettlementOutcome,
    AuthorityPolicyV2SpendOutcome,
    AuthorityPolicyV2StageOutcome,
    AuthorityFenceResult,
    AuthorityRedactionClass,
    AuthorityRetentionClass,
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_STATES,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_TRANSITIONS,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGES,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_TRANSITIONS,
    AUTHORITY_POLICY_V2_ESCALATION_DECISION_ACTION,
    AUTHORITY_POLICY_V2_FINAL_HOOK_AUDIT_ACTION,
    AUTHORITY_POLICY_V2_FINAL_TASK_AUDIT_ACTION,
    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
    AUTHORITY_POLICY_V2_HOUSEKEEPING_PENDING_CODE,
    AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES,
    AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
    AUTHORITY_POLICY_V2_PUBLICATION_LEASE_SECONDS,
    AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION,
    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
    AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_INVALIDATED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED,
    AUTHORITY_POLICY_V2_RESULT_STAGE_SPENT,
    authority_policy_v2_attempt_id,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_candidate_claim_preimage,
    authority_policy_v2_causal_result_digest,
    authority_policy_v2_contract_digest,
    authority_policy_v2_envelope_id,
    authority_policy_v2_notification_id,
    authority_policy_v2_sha256,
    BlockKind,
    DreamStatus,
    LocalCiEvidence,
    NextStep,
    ScheduleStatus,
    TaskAttachmentRecord,
    TaskRecord,
    TaskStatus,
    ThreadAttachment,
    ThreadInvocation,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    ThreadReplyArrival,
    ThreadReplyClaim,
    ThreadReplyDeliveryState,
    ThreadReplyBreakerEpisode,
    ThreadReplyExchangeProjection,
    ThreadReplyRecoveryEntry,
    ThreadReplySettlement,
    ReplyDeliveryProjection,
    ThreadScopedAttachment,
    ThreadStatus,
    TokenUsage,
    WorkHourStatus,
    validate_authority_version,
)
from runtime.reply_delivery import reply_failure_category
from runtime.infrastructure.work_hours_store import WorkHoursStore
from runtime.infrastructure.schedule_store import ScheduleStore
from runtime.infrastructure.thread_mentions import (
    parse_mentions,
    resolve_wake_set,
    valid_mentions,
)

logger = logging.getLogger(__name__)
































def _now() -> datetime:
    return datetime.now(timezone.utc)








def completion_result_payload_matches(
    stored_result: dict | sqlite3.Row, *,
    output_summary: str, confidence_score: int, status: str,
    risks_flagged: list[str] | None, output_dir: str | None,
    decision_json: str | None, waiting_on_job_ids: list[str] | None,
    verdict: str | None, local_ci_json: str | None,
) -> bool:
    """Compare the complete normalized persisted completion projection.

    This is derived from the exact projection ``_insert_task_result`` persists
    (the same values the callback route builds and passes to
    ``admit_task_completion_callback``), so an identity-exact transport retry
    matches structurally while a changed summary/status/confidence/verdict/
    output path, decision, risks, wait IDs or local-CI evidence refuses.  The
    callback's server-owned ``created_at``/boot identity are intentionally
    excluded: they are not client payload and are not part of the admitted
    exactness contract.

    JSON-valued fields (decision, risks, wait IDs, local-CI evidence) compare
    with semantic JSON equality; scalar fields compare by value.
    """
    row = dict(stored_result) if not isinstance(stored_result, dict) else stored_result
    return (
        row.get("output_summary") == output_summary
        and row.get("confidence_score") == confidence_score
        and row.get("status") == status
        and row.get("output_dir") == output_dir
        and row.get("verdict") == verdict
        and _canonical_completion_json(row.get("risks_flagged"))
        == _canonical_completion_json(risks_flagged)
        and _canonical_completion_json(row.get("decision_json"))
        == _canonical_completion_json(decision_json)
        and _canonical_completion_json(row.get("waiting_on_job_ids"))
        == _canonical_completion_json(waiting_on_job_ids)
        and _canonical_completion_json(row.get("local_ci"))
        == _canonical_completion_json(local_ci_json)
    )




# ── Keyset cursor helpers for audit-log pagination ────────────────────────

import base64 as _base64


def _encode_cursor(timestamp: str, row_id: int) -> str:
    """Encode (timestamp, id) into an opaque base64 cursor string."""
    raw = f"{timestamp}|{row_id}"
    return _base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    """Decode an opaque cursor string back to (timestamp, id).

    Raises ``ValueError`` on malformed cursors so callers can reject them
    cleanly (422 at the HTTP layer).
    """
    try:
        raw = _base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, id_str = raw.rsplit("|", 1)
        return ts, int(id_str)
    except Exception:
        raise ValueError(f"Invalid cursor: {cursor!r}")






class Database(
    TasksMixin,
    DreamsMixin,
    KnowledgeMixin,
    JobsMixin,
    AttachmentsMixin,
    AuditMixin,
    SessionsMixin,
    WorkspaceCleanupMixin,
    ThreadsMixin,
    ReplyDeliveryMixin,
    ReplyExchangeMixin,
    SchemaMixin,
    AuthorityV1Mixin,
    AuthorityPolicyMixin,
    AuthorityV2AttemptsMixin,
    AuthorityV2ContinuationMixin,
):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # See `_synchronized` for the threading model. RLock (not Lock) because
        # e.g. `walk_ancestors` → `get_task` and `get_recall_payload` → `get_task`
        # both re-enter public methods while already holding the lock.
        self._lock = threading.RLock()
        # THR-229 checkpoint C3b: process-local proof of an uninterrupted live
        # attempt owner.  The admission transaction registers the winning
        # ``(attempt_id, owner_attempt_id)`` here; the claim/claim-audit stages
        # require that exact live token, so a reopened Database instance, a new
        # daemon boot, or a caller that merely possesses the persisted UUID
        # strings cannot advance the attempt.  This is an ownership marker, not
        # a credential, and it is never persisted.
        self._v2_live_attempt_owners: dict[str, str] = {}
        # THR-229 C3d1 correction: after the authentic uninterrupted owner
        # selected refusal and the refusal transaction failed, the live-owner
        # token is poisoned and this process-local marker records the failure
        # ownership.  Claim/evaluate/consume/audit advancement is prohibited
        # (the live token is gone), while safely attributable housekeeping may
        # still retry even when persisting the durable obligation failed.  The
        # marker is derived from the in-memory token, never reconstructed from
        # durable UUIDs, and it is never a credential.
        self._v2_refusal_failed_owners: dict[str, str] = {}
        # THR-229 C3b correction: the narrowly scoped server-side permission
        # reader.  It is bound by the orchestration seam (the store the
        # orchestrator constructs) and called inside the server process as
        # ``reader(agent)``; it is never a caller-supplied allow/deny boolean or
        # a precomputed digest.  When unbound the claim refuses (fail closed).
        self._v2_permission_surface_reader = None
        # THR-229 C3d1: the trusted current daemon-process identity (the same
        # ``authority_v2_origin_boot_id`` recorded on admitted attempts).  It is
        # bound by the orchestration seam, never supplied as an allow boolean,
        # and it is the only evidence that an attempt belongs to an old boot.
        self._v2_process_boot_id: str | None = None
        # THR-129 lock instrumentation: configurable warning threshold for
        # lock wait/hold times (seconds). Test seam — tests set this to a low
        # value to verify instrumentation fires.
        self._lock_warn_threshold_seconds = 1.0
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            from runtime.infrastructure.remote_job_schema import (
                migrate_identity_enrollment_schema,
                validate_identity_enrollment_schema_preflight,
            )

            # TASK-6611: this fail-closed guard and six-stage convergence are the
            # first database-open schema/data operation.  In particular they run
            # before WAL selection and every legacy/jobs/S2/open-path mutator.
            validate_identity_enrollment_schema_preflight(self._conn)
            migrate_identity_enrollment_schema(
                self._conn,
                stage_hook=getattr(self, "_remote_identity_schema_stage_hook", None),
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate_jobs_table_if_needed()
            self._migrate_drop_talk_surface_if_needed()
            self._retire_skill_lifecycle_if_present()
            self._create_tables()
            self._migrate_remote_job_schema()
            self._migrate_dark_authority_activation_seal_if_needed()
            self._create_authority_tables()
            self._retrofit_authority_policy_activation_trigger_if_needed()
            self._retrofit_authority_audit_fk_if_needed()
            self._retrofit_authority_lifecycle_trigger_if_needed()
            self._ensure_task_attachments_storage_key_unique()
            # Working-hours CRUD lives in its own module but shares THIS connection
            # and lock so the single-connection serialization invariant (see
            # `_synchronized`) is preserved across both surfaces.
            self.work_hours = WorkHoursStore(self._conn, self._lock)
            self.schedules = ScheduleStore(self._conn, self._lock)
        except BaseException:
            # Database owns the connection as soon as connect() succeeds.  A
            # fail-closed schema check or interrupted migration can raise before
            # callers receive an instance, so close here rather than depending
            # on exception-frame collection to release SQLite descriptors.
            self._conn.close()
            raise


    @_synchronized
    def execute(self, sql: str, parameters=()):
        """Passthrough to the underlying sqlite3 connection's execute().

        Enables lifecycle stores that accept either raw connections (tests)
        or Database wrappers (production) to call ``db.execute()`` uniformly.

        Lock acquisition is centralized through ``_synchronized`` (same as all
        other public methods) so lock wait/hold instrumentation covers every
        shared-connection path uniformly. RLock reentrancy is preserved —
        ``execute`` called from within another ``_synchronized`` method
        re-acquires with near-zero wait.
        """
        return self._conn.execute(sql, parameters)

    @property
    def path(self) -> Path:
        """Alias for ``db_path``. Convenience for callers that prefer ``.path``."""
        return self.db_path

    @contextmanager
    def coherent_read_view(self):
        """Yield the shared connection inside ONE coherent, synchronized read view.

        THR-229 C3a: a multi-query validation must not read its schema
        inventory, integrity results and frozen raw digest through separate
        unprotected operations. This view closes both gaps:

        * **Shared-connection synchronization.** ``self._lock`` (the same
          ``threading.RLock`` every ``_synchronized`` method uses) is held for
          the whole view, so no other ``Database``-mediated operation can
          interleave statements on the single shared connection.
        * **One SQLite read snapshot.** A deferred read transaction is pinned,
          so a commit made on an independent connection cannot split the read
          into a mixture of old schema/data and a new digest. WAL readers keep
          their snapshot until the read transaction ends, so an independent
          writer is never blocked or turned into a hang by this view.

        Transaction ownership: when the caller already owns a transaction the
        view joins it and performs no ``BEGIN``/``COMMIT``/``ROLLBACK``, so
        caller work is never committed or rolled back; otherwise a deferred
        read transaction is opened and always ended with ``ROLLBACK``, which
        publishes no write and changes neither foreign-key enforcement nor the
        connection's isolation level. Read-only: callers must not mutate
        through the yielded connection.
        """
        self._lock.acquire(blocking=True)
        started = False
        try:
            if not self._conn.in_transaction:
                self._conn.execute("BEGIN")
                started = True
            yield self._conn
        finally:
            try:
                if started:
                    self._conn.rollback()
            finally:
                self._lock.release()

    @contextmanager
    def workflow_schema_transaction(self):
        """Yield the shared connection for the one org-load schema unit.

        U1A installs and validates the workflow-owned layout only through
        ``OrgState.load``.  The installer needs the same connection and RLock
        discipline as every other ``Database`` operation, with one
        ``BEGIN IMMEDIATE`` covering its first schema observation through its
        final marker/event write.  Filesystem, network, and host work are not
        permitted inside this context.

        A caller-owned transaction is rejected rather than joined: the
        workflow layout is a complete atomic unit and must never be committed
        or rolled back as an accidental side effect of another owner.
        """
        self._lock.acquire(blocking=True)
        try:
            if self._conn.in_transaction:
                raise ValueError("workflow_schema_caller_transaction_not_allowed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
        finally:
            self._lock.release()


    @_synchronized
    def list_tables(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row["name"] for row in cursor.fetchall()]

    # --- Tasks ---





































    @_synchronized
    def try_escalate(
        self, task_id: str, *, reason: str,
        recovery_owner: tuple[str, str] | None = None,
        recovery_result_id: int | None = None,
        recovery_completion_payload: dict | None = None,
        recovery_settled_at: str | None = None,
        recovery_fault_hook=None,
    ) -> bool:
        """Atomic CAS: transition task to ESCALATED (Path B top-level status,
        block_kind cleared) only if it isn't cancelled or already terminal.

        Closes the post-_is_already_terminal race in the escalate decision
        branch — the Python-level check + UPDATE pair was non-atomic with the
        cancel route's UPDATE. By gating the transition with a SQL `WHERE
        cancelled_at IS NULL AND status NOT IN (...)` predicate under the
        Database RLock (same lock the cancel route's update_task uses), the
        operation serializes against cancel: either cancel ran first and we
        see cancelled_at != NULL → bail, or we ran first and cancel observes
        escalated → transitions cleanly to FAILED on its own.

        Returns True iff the row transitioned.

        See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3
        (Codex review of PR #34 surfaced the residual race).
        """
        row = self._conn.execute(
            "SELECT parent_task_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row is not None and row["parent_task_id"] is not None:
            logger.error("refused non-root escalation for task %s", task_id)
            return False
        now = datetime.now(timezone.utc).isoformat()
        if recovery_owner is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ?
                     AND cancelled_at IS NULL
                     AND parent_task_id IS NULL
                     AND status NOT IN ('completed', 'failed', 'superseded', 'cancelled')""",
                (TaskStatus.ESCALATED.value, reason, now, task_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

        # Recovery owns an immutable accepted result, not merely a task status.
        # Keep its final root escalation, both durable receipts, and consumption
        # marker in one transaction so a replacement immediately before this
        # shipping CAS cannot escalate (or notify for) the newer owner.
        if recovery_result_id is None or recovery_completion_payload is None:
            raise ValueError("recovery escalation requires its accepted result receipt")
        agent, session_id = recovery_owner
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if recovery_fault_hook is not None:
                recovery_fault_hook("before_effect")
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND assigned_agent = ? AND current_session_id = ?
                     AND parent_task_id IS NULL
                     AND cancelled_at IS NULL AND status = ?
                     AND EXISTS (
                       SELECT 1 FROM task_completion_recoveries
                       WHERE task_id = ? AND agent = ? AND recovery_session_id = ?
                         AND accepted_result_id = ? AND state = 'callback_accepted'
                     )""",
                (TaskStatus.ESCALATED.value, reason, now, task_id, agent, session_id,
                 TaskStatus.IN_PROGRESS.value, task_id, agent, session_id,
                 recovery_result_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            if recovery_fault_hook is not None:
                recovery_fault_hook("after_effect_before_ledger")
            self.insert_audit_log_uncommitted(
                task_id, agent, "completion_report", recovery_completion_payload,
            )
            self.insert_audit_log_uncommitted(
                task_id, agent, "escalation", {"reason": reason},
            )
            marker = self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_consumed', accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (session_id, recovery_settled_at or now, task_id, agent, session_id,
                 recovery_result_id),
            )
            if marker.rowcount != 1:
                self._conn.rollback()
                return False
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def try_escalate_runtime(
        self,
        task_id: str,
        *,
        reason: str,
        agent: str,
        reason_code: str,
        expected_status: TaskStatus | None = None,
        expected_block_kind: BlockKind | None = None,
        match_expected_state: bool = False,
        clear_active_fanout: bool = False,
    ) -> bool:
        """Atomically commit a runtime-raised escalation and its audit pair.

        Runtime mechanical fences are not authority decisions, so they never
        invoke the evaluator or create candidate/evaluation rows. Track A
        nevertheless requires one explicit ``authority_hook:not_applicable``
        denominator row linked to the committed ``escalation`` row.

        The task transition and both audit rows share one transaction. A lost
        CAS changes nothing; any audit append failure rolls the transition and
        both rows back. Re-entry after a committed escalation loses because an
        already-escalated task is excluded from the update predicate.
        """
        row = self._conn.execute(
            "SELECT parent_task_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row is not None and row["parent_task_id"] is not None:
            logger.error("refused non-root escalation for task %s", task_id)
            return False
        now = datetime.now(timezone.utc).isoformat()
        set_active_fanout = ", active_fanout = NULL" if clear_active_fanout else ""
        if match_expected_state:
            if expected_status is None:
                raise ValueError("expected_status is required for an expected-state CAS")
            if expected_block_kind is None:
                state_predicate = "status = ? AND block_kind IS NULL"
                state_args: tuple = (expected_status.value,)
            else:
                state_predicate = "status = ? AND block_kind = ?"
                state_args = (expected_status.value, expected_block_kind.value)
        else:
            state_predicate = (
                "cancelled_at IS NULL AND status NOT IN "
                "('completed', 'failed', 'superseded', 'cancelled', 'escalated')"
            )
            state_args = ()

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                f"""UPDATE tasks
                    SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                        {set_active_fanout}
                    WHERE id = ? AND parent_task_id IS NULL AND {state_predicate}""",
                (TaskStatus.ESCALATED.value, reason, now, task_id, *state_args),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            escalation_audit_id = self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=agent,
                action="escalation",
                payload={"reason": reason},
            )
            self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=agent,
                action="authority_hook",
                payload={
                    "outcome": "not_applicable",
                    "reason_code": reason_code,
                    "reason": "runtime-raised escalation is not an authority decision",
                    "causal_escalation_audit_id": escalation_audit_id,
                },
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def try_escalate_over_budget(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        reason: str,
    ) -> bool:
        """Atomic CAS for the run_step max-steps budget guard.

        Transitions the row to ESCALATED (Path B top-level status, block_kind
        cleared) with note=reason, but ONLY if it still matches
        (expected_status, expected_block_kind) — the eligible pre-state observed
        at run_step step 1. Returns True iff it transitioned.

        Why this exists: the budget guard runs BEFORE try_claim_for_step, so it
        has no upstream CAS. Two duplicate queue deliveries can both read the
        same stale at-cap eligible row and both escalate, double-posting the
        thread `task_escalated` message + TASK_FOLLOWUP invocation. The
        conditional WHERE makes only the first writer win; the loser matches
        zero rows and bails. A /cancel landing in the window also moves the row
        out of the expected pre-state, so the CAS rejects it for free.
        """
        row = self._conn.execute(
            "SELECT parent_task_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row is not None and row["parent_task_id"] is not None:
            logger.error("refused non-root escalation for task %s", task_id)
            return False
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND parent_task_id IS NULL
                     AND status = ? AND block_kind IS NULL""",
                (TaskStatus.ESCALATED.value, reason, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND parent_task_id IS NULL
                     AND status = ? AND block_kind = ?""",
                (TaskStatus.ESCALATED.value, reason, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1













    # --- Audit Log ---

    @_synchronized
    def commit(self) -> None:
        """Commit the current transaction — public companion to insert_audit_log_uncommitted."""
        self._conn.commit()

    def _emit_reply_wake_audit(
        self,
        *,
        thread_id: str,
        agent_name: str,
        action: str,
        payload: dict,
    ) -> None:
        """Emit one reply-delivery lifecycle audit row INSIDE the open store
        transaction (caller commits). GH-688 Phase 1 Slice C.

        The six approved actions — thread_reply_wake_created / _coalesced /
        _claimed / _settled / _cancelled / _recovered — are written at the
        exact store transitions that already know the durable outcome, so
        duplicate queue notifications (stale claim CAS no-ops) and idempotent
        recovery can never fabricate false events. The existing
        ``audit_log.task_id = THR-*`` scope-prefix convention is unchanged;
        ``agent`` is the wake owner so /audit?agent= filters naturally.
        Payloads carry only truthfully observed fields (agent, inclusive
        range, 8-char token prefix, outcome/reason/follow-on result) and
        never expose full single-use invocation tokens.
        """
        self.insert_audit_log_uncommitted(
            task_id=thread_id,
            agent=agent_name,
            action=action,
            payload=payload,
        )

    @_synchronized
    def rollback(self) -> None:
        """Roll back the current transaction — companion to insert_audit_log_uncommitted."""
        self._conn.rollback()

    @_synchronized
    def fetch_one_readonly(
        self, sql: str, params: tuple = ()
    ) -> "sqlite3.Row | None":
        """Run a read-only SELECT and return the first row or None.

        For use by modules outside ``Database`` (e.g. ``dashboard_summary``)
        that need to issue read aggregations without bypassing ``_lock``.
        Holds the same ``RLock`` as every other public Database method.
        """
        return self._conn.execute(sql, params).fetchone()

    @_synchronized
    def fetch_all_readonly(
        self, sql: str, params: tuple = ()
    ) -> "list[sqlite3.Row]":
        """Run a read-only SELECT and return all rows.

        See ``fetch_one_readonly`` for the threading rationale.
        """
        return self._conn.execute(sql, params).fetchall()

    @_synchronized
    def query_audit_logs(
        self,
        task_id: str | None = None,
        agent: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Filtered audit-log query used by the /audit route.

        All filters are optional and AND-composed. ``limit`` returns the most
        recent N rows (ORDER BY timestamp DESC, id DESC) but the result is
        re-sorted ascending so callers still see chronological order.

        Supports KEYSET cursor pagination: pass the ``cursor`` returned by a
        prior call to get the next older page.  The cursor is an opaque string
        encoding the (timestamp, id) of the last row in the prior page.
        ``next_cursor`` is ``None`` exactly when the result set is exhausted.
        """
        import base64

        clauses: list[str] = []
        params: list[object] = []

        # Decode cursor into a keyset filter (rows BEFORE the cursor anchor)
        if cursor is not None:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            clauses.append(
                "(timestamp < ? OR (timestamp = ? AND id < ?))"
            )
            params.extend([cursor_ts, cursor_ts, cursor_id])

        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        # Defensive guard: non-positive limit short-circuits to empty result.
        # Without this, limit=0 returns next_cursor anchored to a row that was
        # never returned, and limit<0 produces an IndexError on result[limit-1].
        if limit is not None and limit <= 0:
            return [], None

        if limit is not None:
            # Fetch limit+1 to detect whether another page exists
            sql = (
                f"SELECT * FROM audit_log {where} "
                f"ORDER BY timestamp DESC, id DESC LIMIT ?"
            )
            params.append(limit + 1)
        else:
            sql = f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC, id DESC"

        db_cursor = self._conn.execute(sql, tuple(params))
        rows = db_cursor.fetchall()

        result: list[dict] = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)

        next_cursor: str | None = None
        if limit is not None and len(result) > limit:
            # The extra row tells us there is a next page.
            # Encode the (timestamp, id) of the last actual-page row as next_cursor.
            last_of_page = result[limit - 1]
            next_cursor = _encode_cursor(
                last_of_page["timestamp"], last_of_page["id"]
            )
            # Trim to exactly the requested page size
            result = result[:limit]

        # Re-sort ascending so callers see chronological (oldest-first) order.
        result.sort(key=lambda d: d["id"])

        return result, next_cursor

    # --- Task Results ---





















    @_synchronized
    def insert_task_result(
        self,
        task_id: str,
        agent: str,
        session_id: str,
        output_summary: str,
        confidence_score: int,
        status: str = "completed",
        risks_flagged: list[str] | None = None,
        learnings: str | None = None,
        duration_seconds: int | None = None,
        token_count: int | None = None,
        estimated_cost: float | None = None,
        output_dir: str | None = None,
        decision_json: str | None = None,
        waiting_on_job_ids: list[str] | None = None,
        verdict: str | None = None,
        local_ci_json: str | None = None,
    ) -> None:
        self._insert_task_result(
            task_id=task_id, agent=agent, session_id=session_id,
            output_summary=output_summary, confidence_score=confidence_score,
            status=status, risks_flagged=risks_flagged, learnings=learnings,
            duration_seconds=duration_seconds, token_count=token_count,
            estimated_cost=estimated_cost, output_dir=output_dir,
            decision_json=decision_json, waiting_on_job_ids=waiting_on_job_ids,
            verdict=verdict, local_ci_json=local_ci_json,
        )
        self._conn.commit()

    def _insert_task_result(
        self,
        task_id: str, agent: str, session_id: str, output_summary: str,
        confidence_score: int, status: str = "completed",
        risks_flagged: list[str] | None = None, learnings: str | None = None,
        duration_seconds: int | None = None, token_count: int | None = None,
        estimated_cost: float | None = None, output_dir: str | None = None,
        decision_json: str | None = None,
        waiting_on_job_ids: list[str] | None = None,
        verdict: str | None = None, local_ci_json: str | None = None,
    ) -> None:
        """Insert a result without committing; caller owns any transaction."""
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, status, output_summary, decision_json,
                confidence_score, learnings, risks_flagged, duration_seconds,
                token_count, estimated_cost, output_dir, waiting_on_job_ids,
                verdict, local_ci, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent,
                session_id,
                status,
                output_summary,
                decision_json,
                confidence_score,
                learnings,
                json.dumps(risks_flagged) if risks_flagged is not None else None,
                duration_seconds,
                token_count,
                estimated_cost,
                output_dir,
                json.dumps(waiting_on_job_ids) if waiting_on_job_ids is not None else None,
                verdict,
                local_ci_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    @_synchronized
    def admit_task_completion_callback(
        self, *, task_id: str, agent: str, session_id: str,
        output_summary: str, confidence_score: int, status: str = "completed",
        risks_flagged: list[str] | None = None, output_dir: str | None = None,
        decision_json: str | None = None, waiting_on_job_ids: list[str] | None = None,
        verdict: str | None = None, local_ci_json: str | None = None,
        recovery_deadline_monotonic: float | None = None,
        v2_admission: dict | None = None,
    ) -> bool:
        """Atomically admit, persist, and ledger-accept one completion callback.

        ``v2_admission`` (THR-229 checkpoint C2) carries the authenticated
        versioned evidence for a v2-bound manager session: the exact launch
        binding identity, the v2 contract/family references, the sanitized
        assessment digest and the owning daemon-process boot UUID.  When
        supplied, the immutable result, the admitted attempt journal row and
        the ``authority_policy_v2_result_stage`` admission audit are inserted in
        the SAME transaction as the existing result/receipt writes, so any
        failure rolls back every newly admitted result/attempt/audit/receipt
        change.  An exact transport retry compares the admitted assessment
        digest and never allocates a second result/attempt/audit; a changed
        assessment digest refuses.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            from runtime.workflows.recovery import classify_task
            drafts = getattr(self, "_workflow_drafts", None)
            ownership = classify_task(self, task_id, org_slug=drafts.org.slug if drafts is not None else None)
            if ownership.kind != "legacy":
                if ownership.kind != "draft" or drafts is None:
                    self._conn.rollback()
                    return False
                if v2_admission is not None and not self._authenticate_v2_attempt_admission_uncommitted(
                    task_id=task_id, agent=agent, session_id=session_id, admission=v2_admission,
                ):
                    self._conn.rollback()
                    return False
                payload = dict(output_summary=output_summary, confidence_score=confidence_score, status=status,
                               risks_flagged=risks_flagged, output_dir=output_dir, decision_json=decision_json,
                               waiting_on_job_ids=waiting_on_job_ids, verdict=verdict, local_ci_json=local_ci_json)
                prior = self._conn.execute("SELECT * FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                                           (task_id, agent, session_id)).fetchone()
                if not drafts.callback_uncommitted(task_id=task_id, agent=agent, session_id=session_id, payload=payload):
                    self._conn.rollback()
                    return False
                admitted_attempt = None
                if prior is not None:
                    if v2_admission is not None:
                        admitted = self.get_authority_policy_v2_attempt_for_result(prior["id"])
                        if admitted is None or any(getattr(admitted, key) != v2_admission[key] for key in (
                            "assessment_digest", "binding_id", "release_id", "activation_id", "activation_epoch", "selector_id", "contract_digest",
                        )):
                            self._conn.rollback()
                            return False
                    self._conn.rollback()
                    return True
                if v2_admission is not None:
                    # Event insertion also allocates rowids. Resolve the exact
                    # accepted INTEGER identity from its owning intent.
                    result_id = self._conn.execute("SELECT final_result_id FROM workflow_draft_dispatch_intents WHERE id=?",
                                                   (ownership.intent_id,)).fetchone()[0]
                    admitted_attempt = self._insert_authority_policy_v2_attempt_uncommitted(
                        task_id=task_id, agent=agent, session_id=session_id, result_id=result_id,
                        admission=v2_admission, now=_now().isoformat(),
                    )
                from runtime.infrastructure.workflow_schema import validate_workflow_schema
                validate_workflow_schema(self._conn, expected_org_slug=drafts.org.slug)
                self._conn.commit()
                if admitted_attempt is not None:
                    self._v2_live_attempt_owners[admitted_attempt.attempt_id] = admitted_attempt.owner_attempt_id
                return True
            task = self._conn.execute(
                "SELECT status, cancelled_at, assigned_agent, current_session_id "
                "FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or task["cancelled_at"] is not None or task["status"] in {
                "completed", "failed", "cancelled", "superseded",
            }:
                self._conn.rollback()
                return False
            # ``assigned_agent`` is selected when a task is submitted, before
            # any runtime invocation exists.  ``current_session_id`` is the
            # shipping publication seam: _run_agent writes it immediately
            # before SessionTracker.set_active().  Retain the established
            # tracker-only ordinary callback path until that session binding
            # exists; once it does, require the complete durable identity.
            if task["current_session_id"] is not None and (
                task["assigned_agent"] != agent or task["current_session_id"] != session_id
            ):
                self._conn.rollback()
                return False
            # Monotonic time is meaningful only in this process and is passed
            # from the server-owned active recovery binding.  Check it here,
            # after BEGIN IMMEDIATE, so DB-lock delay cannot extend admission.
            if recovery_deadline_monotonic is not None and _time.monotonic() >= recovery_deadline_monotonic:
                self._conn.rollback()
                return False
            now = _now().isoformat()
            if not self.completion_recovery_callback_allowed(
                task_id=task_id, agent=agent, session_id=session_id, now=now,
            ):
                # A recovery claim fences its origin and its own replacement,
                # not an ordinary generation that has since atomically taken
                # the task's durable binding.  Retire the displaced recovery
                # in this same callback transaction before accepting the
                # newer exact owner, so its stale launch cannot later run and
                # it cannot leave the newer callback permanently inadmissible.
                recovery = self._conn.execute(
                    """SELECT origin_session_id, recovery_session_id
                       FROM task_completion_recoveries
                       WHERE task_id=? AND agent=? AND state='claimed'""",
                    (task_id, agent),
                ).fetchone()
                if (
                    recovery is None
                    or session_id in {
                        recovery["origin_session_id"],
                        recovery["recovery_session_id"],
                    }
                ):
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    """UPDATE task_completion_recoveries
                       SET state='superseded', settled_at=?
                       WHERE task_id=? AND agent=? AND state='claimed'""",
                    (now, task_id, agent),
                )
            if v2_admission is not None:
                # Authenticate the supplied evidence against the session's
                # immutable launch binding before any write.  A missing,
                # corrupt, or mismatched binding refuses with zero writes.
                if not self._authenticate_v2_attempt_admission_uncommitted(
                    task_id=task_id, agent=agent, session_id=session_id,
                    admission=v2_admission,
                ):
                    self._conn.rollback()
                    return False
            existing = self._conn.execute(
                "SELECT * FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                (task_id, agent, session_id),
            ).fetchone()
            if existing is not None:
                if v2_admission is None:
                    # Unchanged legacy idempotency: any persisted same-session
                    # result short-circuits an exact retry.
                    self._conn.rollback()
                    return True
                # Narrowed v2 retry seam: an existing result is only an exact
                # replay when the authenticated admitted evidence and the
                # complete normalized completion payload both match.  The
                # retry re-reads the raw attempt columns, so it must re-run the
                # same canonical column/preimage and unique admission-audit
                # authentication as the typed readers; a missing, mutated,
                # duplicated or mismatched attempt/audit, or any changed
                # summary/decision/status/confidence/verdict/risks/output
                # path/wait-ID/local-CI field, refuses rather than silently
                # acknowledging a changed completion result.
                admitted_row = self._conn.execute(
                    """SELECT * FROM authority_policy_v2_attempts
                       WHERE root_task_id=? AND manager_agent=?
                         AND manager_session_id=? AND result_id=?""",
                    (task_id, agent, session_id, existing["id"]),
                ).fetchone()
                if admitted_row is None:
                    self._conn.rollback()
                    return False
                try:
                    admitted = self._authority_policy_v2_attempt_from_row(admitted_row)
                except ValueError:
                    self._conn.rollback()
                    return False
                if not self._authenticate_v2_attempt_admission_audit_uncommitted(
                    dict(admitted_row)
                ):
                    self._conn.rollback()
                    return False
                if (
                    admitted.assessment_digest != v2_admission["assessment_digest"]
                    or admitted.binding_id != v2_admission["binding_id"]
                    or admitted.release_id != v2_admission["release_id"]
                    or admitted.activation_id != v2_admission["activation_id"]
                    or admitted.activation_epoch != v2_admission["activation_epoch"]
                    or admitted.selector_id != v2_admission["selector_id"]
                    or admitted.contract_digest != v2_admission["contract_digest"]
                ):
                    self._conn.rollback()
                    return False
                if not completion_result_payload_matches(
                    existing,
                    output_summary=output_summary,
                    confidence_score=confidence_score,
                    status=status,
                    risks_flagged=risks_flagged,
                    output_dir=output_dir,
                    decision_json=decision_json,
                    waiting_on_job_ids=waiting_on_job_ids,
                    verdict=verdict,
                    local_ci_json=local_ci_json,
                ):
                    self._conn.rollback()
                    return False
                self._conn.rollback()
                return True
            self._insert_task_result(
                task_id=task_id, agent=agent, session_id=session_id,
                output_summary=output_summary, confidence_score=confidence_score,
                status=status, risks_flagged=risks_flagged, output_dir=output_dir,
                decision_json=decision_json, waiting_on_job_ids=waiting_on_job_ids,
                verdict=verdict, local_ci_json=local_ci_json,
            )
            accepted_result_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_accepted', accepted_result_id=?,
                       accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=? AND state='claimed'""",
                (accepted_result_id, session_id, now, task_id, agent, session_id),
            )
            admitted_attempt = None
            if v2_admission is not None:
                admitted_attempt = self._insert_authority_policy_v2_attempt_uncommitted(
                    task_id=task_id, agent=agent, session_id=session_id,
                    result_id=accepted_result_id, admission=v2_admission, now=now,
                )
            self._conn.commit()
            if admitted_attempt is not None:
                # Register the live-owner proof only AFTER the durable commit,
                # so a rolled-back admission can never leave a claimable token.
                self._v2_live_attempt_owners[admitted_attempt.attempt_id] = (
                    admitted_attempt.owner_attempt_id
                )
            return True
        except Exception:
            self._conn.rollback()
            raise










    # --- Session Token Usage ---

    # --- KB views ---


    # --- Skill validation events ---

    @_synchronized
    def insert_skill_validation_event(
        self,
        *,
        skill_id: str,
        slug: str,
        agent: str | None = None,
        source: str = "user_authored",
        severity: str = "info",
        ok: bool = True,
        version: str | None = None,
        findings: list[str] | None = None,
        reason_codes: list[str] | None = None,
    ) -> int:
        """Insert a skill validation event and return the row id."""
        now = _now().isoformat()
        findings_json = json.dumps(findings or [])
        codes_json = json.dumps(reason_codes or [])
        cursor = self._conn.execute(
            """INSERT INTO skill_validation_events
               (skill_id, slug, agent, source, severity, ok, version,
                findings, reason_codes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_id,
                slug,
                agent,
                source,
                severity,
                1 if ok else 0,
                version,
                findings_json,
                codes_json,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    # --- Thread IDs ---



    @_synchronized
    def list_stale_pending_jobs(self, cutoff_iso: str) -> list[dict]:
        """Read-only scan for never-started pending jobs older than ``cutoff_iso``.

        Predicate: ``status='pending' AND started_at IS NULL AND created_at <=
        cutoff`` — a row that was submitted but never dispatched (no
        ``transition_job_to_running`` ever stamped ``started_at``) and has
        reached the observation threshold. Purely observational: callers
        must NOT use this as a reaper/retry/cancel mechanism. Returns a
        lightweight dict per row (id/task_id/agent_name/title/review_required/
        created_at) — not full JobRecords — because this is a diagnostic scan.
        """
        rows = self._conn.execute(
            _STALE_PENDING_JOBS_SCAN_SQL,
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]






    @_synchronized
    def add_thread_participant(
        self, thread_id: str, agent_name: str, *, added_by: str
    ) -> bool:
        """Insert a participant. Returns True if inserted, False if duplicate."""
        try:
            self._conn.execute(
                "INSERT INTO thread_participants (thread_id, agent_name, added_at, added_by) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, agent_name, _now().isoformat(), added_by),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False






    def _append_thread_message_uncommitted(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
        attachments: list[ThreadAttachment] | None = None,
        sent_from_task_id: str | None = None,
        mentions: list[str] | None = None,
    ) -> int:
        """Allocate seq + insert a message (and its attachments) WITHOUT
        opening or committing a transaction. Callers must own the transaction
        (``BEGIN IMMEDIATE`` / ``BEGIN``) and commit/rollback themselves.

        Returns the allocated seq.
        """
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
            "FROM thread_messages WHERE thread_id = ?",
            (thread_id,),
        )
        next_seq = cursor.fetchone()["next_seq"]
        self._conn.execute(
            "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
            "body_markdown, decline_reason, system_payload_json, "
            "sent_from_task_id, mentions_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                next_seq,
                speaker,
                kind.value,
                body_markdown,
                decline_reason,
                json.dumps(system_payload) if system_payload else None,
                sent_from_task_id,
                json.dumps(mentions) if mentions is not None else None,
                _now().isoformat(),
            ),
        )
        for ordinal, attachment in enumerate(attachments or []):
            self._conn.execute(
                "INSERT INTO thread_message_attachments ("
                "thread_id, message_seq, ordinal, artifact_name, display_name, "
                "size_bytes, content_type, uploaded_by, created_at, "
                "thread_attachment_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    next_seq,
                    ordinal,
                    attachment.artifact_name,
                    attachment.display_name,
                    attachment.size_bytes,
                    attachment.content_type,
                    attachment.uploaded_by,
                    _now().isoformat(),
                    attachment.thread_attachment_id,
                ),
            )
        return next_seq






    # --- Thread-scoped attachments (TASK-1616) ---

    @_synchronized
    def insert_thread_scoped_attachment(
        self,
        *,
        attachment_id: str,
        thread_id: str,
        display_name: str,
        size_bytes: int | None,
        content_type: str | None,
        uploaded_by: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO thread_scoped_attachments "
            "(attachment_id, thread_id, display_name, size_bytes, "
            "content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                attachment_id,
                thread_id,
                display_name,
                size_bytes,
                content_type,
                uploaded_by,
                _now().isoformat(),
            ),
        )
        self._conn.commit()

    # --- Task attachments (THR-109) ---

    @_synchronized
    def insert_task_attachment(
        self,
        *,
        task_id: str,
        ordinal: int,
        storage_key: str,
        display_name: str,
        size_bytes: int | None,
        content_type: str | None,
        uploaded_by: str,
    ) -> None:
        # Reject if storage_key is already claimed — including by legacy
        # duplicate rows that were excluded from the partial unique index.
        existing = self.get_task_attachment_by_storage_key(storage_key)
        if existing is not None:
            raise sqlite3.IntegrityError(
                f"UNIQUE constraint failed: task_attachments.storage_key: "
                f"{storage_key}"
            )
        self._conn.execute(
            "INSERT INTO task_attachments "
            "(task_id, ordinal, storage_key, display_name, size_bytes, "
            "content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                ordinal,
                storage_key,
                display_name,
                size_bytes,
                content_type,
                uploaded_by,
                _now().isoformat(),
            ),
        )
        self._conn.commit()


    def _insert_task_attachments_txn(
        self, task_id: str, attachments: list[dict], uploaded_by: str,
    ) -> None:
        """Insert attachment links + audit rows within an existing transaction.

        Caller MUST have already started a transaction (BEGIN IMMEDIATE) and
        MUST be holding the @_synchronized lock. Does NOT commit — the caller
        owns the transaction lifecycle.

        Raises sqlite3.IntegrityError on duplicate storage_key.
        """
        now = _now().isoformat()
        for att in attachments:
            existing = self._conn.execute(
                "SELECT 1 FROM task_attachments WHERE storage_key = ?",
                (att["storage_key"],),
            ).fetchone()
            if existing is not None:
                raise sqlite3.IntegrityError(
                    "UNIQUE constraint failed: "
                    f"task_attachments.storage_key: {att['storage_key']}"
                )
            self._conn.execute(
                "INSERT INTO task_attachments "
                "(task_id, ordinal, storage_key, display_name, size_bytes, "
                "content_type, uploaded_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    att["ordinal"],
                    att["storage_key"],
                    att["display_name"],
                    att["size_bytes"],
                    att["content_type"],
                    uploaded_by,
                    now,
                ),
            )
            audit_ts = _now().isoformat()
            audit_payload = json.dumps({
                "storage_key": att["storage_key"],
                "display_name": att["display_name"],
                "content_type": att["content_type"],
                "uploaded_by": uploaded_by,
            })
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, uploaded_by, "task_attachment_added", audit_payload, audit_ts),
            )

    # --- Cleanup-report thread + task atomic producer (THR-195) ----------
    #
    # TASK-6046 finding 1: the daemon's cleanup trigger previously created the
    # report thread (committing thread/participant/message/turn/audit rows)
    # BEFORE inserting the task, so an insert failure left an OPEN thread
    # claiming a nonexistent task. The fix is one atomic producer operation:
    # thread + task commit or roll back together (KB
    # ``atomic-multi-table-persistence`` — composite method, single
    # BEGIN IMMEDIATE/COMMIT with rollback), so any failure leaves ZERO
    # durable residue and a later retry succeeds exactly once.


    def _add_thread_participant_uncommitted(
        self, thread_id: str, agent_name: str, *, added_by: str,
    ) -> bool:
        """Insert a thread participant WITHOUT committing (caller owns the
        transaction). Returns True if inserted, False if duplicate."""
        try:
            self._conn.execute(
                "INSERT INTO thread_participants (thread_id, agent_name, added_at, added_by) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, agent_name, _now().isoformat(), added_by),
            )
            return True
        except sqlite3.IntegrityError:
            return False


    @_synchronized
    def insert_cleanup_report_thread_and_task(
        self,
        *,
        thread_id: str,
        subject: str,
        composer: str,
        opening_body: str,
        initial_recipients: list[str],
        turn_cap: int,
        task: TaskRecord,
    ) -> str:
        """Atomically create a cleanup-report thread AND insert the task it was
        composed from, in ONE BEGIN IMMEDIATE / COMMIT transaction.

        The daemon's per-agent founder-report thread (row, composer
        participant, opening message, turn accounting, and the canonical
        ``thread_started`` / ``thread_message_sent`` audit rows) and the task
        row commit or roll back together (KB ``atomic-multi-table-persistence``;
        same compound pattern as ``insert_task_with_attachments`` /
        ``try_delegate_many``). On ANY exception the whole batch rolls back —
        zero durable residue in every affected table — nothing is enqueued by
        the caller, and a later retry succeeds exactly once (TASK-6046 finding
        1). A report thread can never survive claiming a task that was not
        inserted, and a task can never dangle without its thread.

        Thread semantics mirror ``_create_agent_thread_locked`` for the
        founder-only report thread: the owning agent is the composer and
        participant; the opening message is appended as seq 1 with the mention
        signal derived exactly the same way (``_derive_conversational_mentions``
        — the daemon's opening carries no @mentions, so the wake set is empty);
        one turn is counted; the two audit payloads match ``AuditLogger``
        shapes. @founder is NOT a participant row (spec §3.3) and no agent
        recipients exist, so no reply wake is derived. The inserted task is a
        clean root (no parent, no thread dispatch). ``thread_id`` and ``task.id``
        must be caller-allocated under ``org.db_lock``.

        Returns the thread id. Raises on failure (nothing persisted).
        """
        thread = ThreadRecord(
            id=thread_id,
            subject=subject,
            turn_cap=turn_cap,
            composed_by=composer,
            composed_from_task_id=task.id,
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._insert_thread_uncommitted(thread)
            self._add_thread_participant_uncommitted(
                thread_id, composer, added_by=composer,
            )
            mentions = self._derive_conversational_mentions(
                thread_id, composer, ThreadMessageKind.MESSAGE, opening_body,
            )
            seq = self._append_thread_message_uncommitted(
                thread_id=thread_id,
                speaker=composer,
                kind=ThreadMessageKind.MESSAGE,
                body_markdown=opening_body,
                mentions=mentions,
            )
            self._increment_thread_turns_used_uncommitted(thread_id, by=1)
            self.insert_audit_log_uncommitted(
                task_id=thread_id,
                agent=composer,
                action="thread_started",
                payload={
                    "subject": subject,
                    "initial_recipients": initial_recipients,
                    "forwarded_from_id": None,
                    "composed_by": composer,
                    "composed_from_task_id": task.id,
                    "composed_from_dream_id": None,
                },
            )
            self.insert_audit_log_uncommitted(
                task_id=thread_id,
                agent=composer,
                action="thread_message_sent",
                payload={"seq": seq, "kind": "message"},
            )
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
                   current_session_id, zombie_flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.status.value,
                    task.assigned_agent,
                    task.team,
                    task.brief,
                    task.revision_count,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.parent_task_id,
                    task.revisit_of_task_id,
                    task.dispatched_from_thread_id,
                    task.block_kind.value if task.block_kind else None,
                    task.note,
                    task.orchestration_step_count,
                    task.session_timeout_seconds,
                    task.task_type,
                    task.active_fanout,
                    task.current_session_id,
                    task.zombie_flagged_at.isoformat() if task.zombie_flagged_at else None,
                ),
            )
            self._conn.commit()
            return thread_id
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def mint_thread_invocation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        triggering_seq: int,
        purpose: ThreadInvocationPurpose,
    ) -> ThreadInvocation:
        import uuid as _uuid
        token = _uuid.uuid4().hex
        now = _now().isoformat()
        cursor = self._conn.execute(
            "INSERT INTO thread_invocations (thread_id, agent_name, "
            "invocation_token, triggering_seq, purpose, status, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (thread_id, agent_name, token, triggering_seq, purpose.value, now),
        )
        self._conn.commit()
        return ThreadInvocation(
            id=cursor.lastrowid,
            thread_id=thread_id,
            agent_name=agent_name,
            invocation_token=token,
            triggering_seq=triggering_seq,
            purpose=purpose,
            status=ThreadInvocationStatus.PENDING,
            enqueued_at=datetime.fromisoformat(now),
        )




    @_synchronized
    def consume_invocation(self, token: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'consumed', "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def mark_invocation_declined(
        self, token: str, *, decline_reason: str | None = None
    ) -> bool:
        """Set invocation status to 'declined' with an optional reason.

        Returns True if the row was updated (was pending), False otherwise.
        """
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'declined', "
            "consumed_at = ?, decline_reason = ? "
            "WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), decline_reason, token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def decline_pending_invocations_for_agent(
        self, thread_id: str, agent_name: str,
        *, decline_reason: str | None = None,
    ) -> int:
        """Bulk-decline all pending invocations for (thread_id, agent_name).

        Returns the count of rows updated.
        """
        now = _now().isoformat()
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'declined', "
            "consumed_at = ?, decline_reason = ? "
            "WHERE thread_id = ? AND agent_name = ? AND status = 'pending'",
            (now, decline_reason, thread_id, agent_name),
        )
        self._conn.commit()
        return cursor.rowcount



    @_synchronized
    def fail_invocation(
        self, token: str, *, status: ThreadInvocationStatus, decline_reason: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (status.value, decline_reason, _now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def stamp_invocation_started(
        self,
        token: str,
        *,
        session_id: str | None,
        executor: str | None = None,
        model: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE thread_invocations SET started_at = ?, session_id = ?, "
            "executor = ?, model = ? "
            "WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), session_id, executor, model, token),
        )
        self._conn.commit()




    @_synchronized
    def decline_unstarted_invocations_for_agent(
        self, agent_name: str, *, decline_reason: str,
    ) -> int:
        """Decline all pending, not-yet-started invocations for ``agent_name``.

        Returns the number of rows updated.
        """
        now = _now().isoformat()
        cursor = self._conn.execute(
            "UPDATE thread_invocations "
            "SET status = ?, decline_reason = ?, consumed_at = ? "
            "WHERE agent_name = ? AND status = 'pending' AND started_at IS NULL",
            (ThreadInvocationStatus.DECLINED.value, decline_reason, now, agent_name),
        )
        self._conn.commit()
        return cursor.rowcount


    @_synchronized
    def count_pending_turn_obligations(self, thread_id: str) -> int:
        """Count pending invocations that represent future turn obligations.

        REPLY, BOOTSTRAP, TASK_FOLLOWUP count.

        No current callers in production routes — kept as a documented API.
        After the broadcast-only routing change (spec §7, "invite is free"),
        the /invite projection was dropped entirely; /send and /compose use
        a simpler turns_used + 1 projection; the task-followup auto-extend
        path (mint_followup_invocation_with_cap_extend) inlines its own
        pending-count SQL. Unit tests exercise this helper directly.
        """
        counted = (
            ThreadInvocationPurpose.REPLY.value,
            ThreadInvocationPurpose.BOOTSTRAP.value,
            ThreadInvocationPurpose.TASK_FOLLOWUP.value,
        )
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM thread_invocations "
            "WHERE thread_id = ? AND status = ? AND purpose IN ({})".format(
                ",".join("?" * len(counted))
            ),
            (thread_id, ThreadInvocationStatus.PENDING.value, *counted),
        ).fetchone()
        return int(row["n"])

    @_synchronized
    def reap_pending_invocations(
        self,
        thread_id: str,
        *,
        purposes: list[ThreadInvocationPurpose] | None = None,
        decline_reason: str,
    ) -> int:
        now = _now().isoformat()
        if purposes is None:
            cursor = self._conn.execute(
                "UPDATE thread_invocations SET status = 'failed', "
                "decline_reason = ?, consumed_at = ? "
                "WHERE thread_id = ? AND status = 'pending'",
                (decline_reason, now, thread_id),
            )
        else:
            placeholders = ",".join("?" * len(purposes))
            values = [decline_reason, now, thread_id] + [p.value for p in purposes]
            cursor = self._conn.execute(
                f"UPDATE thread_invocations SET status = 'failed', "
                f"decline_reason = ?, consumed_at = ? "
                f"WHERE thread_id = ? AND status = 'pending' "
                f"AND purpose IN ({placeholders})",
                values,
            )
        self._conn.commit()
        return cursor.rowcount




    @_synchronized
    def set_thread_pinned(self, thread_id: str, *, pinned: bool) -> None:
        """Set/clear founder-workspace pin state (THR-209).

        Pin state is presentation-only: this write touches ``pinned_at`` and
        nothing else — no message, notification, participant, unread, or
        activity-timestamp effect. The caller is responsible for the
        ``thread_pinned``/``thread_unpinned`` audit row.
        """
        if pinned:
            self._conn.execute(
                "UPDATE threads SET pinned_at = ? WHERE id = ?",
                (_now().isoformat(), thread_id),
            )
        else:
            self._conn.execute(
                "UPDATE threads SET pinned_at = NULL WHERE id = ?",
                (thread_id,),
            )
        self._conn.commit()











    # --- Escalation Notifications ---

    @_synchronized
    def mint_escalation_notification(
        self,
        feishu_message_id: str,
        org_slug: str,
        task_id: str,
        chat_id: str,
        expires_at: datetime,
        kind: str = "escalation",
    ) -> None:
        if kind not in ("escalation", "failure", "job_request"):
            raise ValueError(
                f"kind must be 'escalation', 'failure', or 'job_request', got {kind!r}"
            )
        expires_at_str = expires_at.astimezone(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO escalation_notifications
               (feishu_message_id, org_slug, task_id, chat_id,
                created_at, expires_at, consumed_at, consumed_by, kind)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                feishu_message_id, org_slug, task_id, chat_id,
                datetime.now(timezone.utc).isoformat(),
                expires_at_str,
                kind,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_escalation_notification(self, feishu_message_id: str) -> dict | None:
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications WHERE feishu_message_id = ?""",
            (feishu_message_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    @_synchronized
    def get_latest_notification_for_sr(
        self, job_id: str, *, kind: str,
    ) -> dict | None:
        """Look up the most-recent escalation_notifications row for an SR.

        Used by the terminal-result follow-up: when a Feishu-initiated script run
        finishes, we post a threaded reply to the original push's message_id.
        Returns consumed rows too — the APPROVE reply consumes the row, but the
        parent message_id is still needed to thread the result post.
        """
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications
               WHERE task_id = ? AND kind = ?
               ORDER BY created_at DESC LIMIT 1""",
            (job_id, kind),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    @_synchronized
    def consume_escalation_notification(
        self, feishu_message_id: str, consumed_by: str,
    ) -> bool:
        """Atomically mark a notification consumed. Returns True on first
        consume, False if already consumed or missing."""
        cur = self._conn.execute(
            """UPDATE escalation_notifications
               SET consumed_at = ?, consumed_by = ?
               WHERE feishu_message_id = ? AND consumed_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), consumed_by, feishu_message_id),
        )
        self._conn.commit()
        return cur.rowcount == 1

    # --- Processed Event Dedup ---

    @_synchronized
    def record_processed_event(
        self,
        org_slug: str,
        feishu_event_id: str,
        outcome: str,
        reason: str | None,
    ) -> bool:
        """INSERT OR IGNORE into the dedup table. Returns True on first insert,
        False on duplicate."""
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO processed_event_ids
               (org_slug, feishu_event_id, processed_at, outcome, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                org_slug, feishu_event_id,
                datetime.now(timezone.utc).isoformat(),
                outcome, reason,
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1

    @_synchronized
    def update_processed_event_outcome(
        self,
        org_slug: str,
        feishu_event_id: str,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        """Update the outcome on an existing processed_event_ids row. Used when
        the listener has decided how the event was disposed (consumed/rejected/ignored)."""
        self._conn.execute(
            """UPDATE processed_event_ids
               SET outcome = ?, reason = ?
               WHERE org_slug = ? AND feishu_event_id = ?""",
            (outcome, reason, org_slug, feishu_event_id),
        )
        self._conn.commit()

    @_synchronized
    def list_open_notifications_for_task(self, task_id: str) -> list[dict]:
        """Return un-consumed notification rows for a task. Used by CLI
        resolve-escalation to mark the matching Feishu row consumed."""
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications
               WHERE task_id = ? AND consumed_at IS NULL""",
            (task_id,),
        )
        return [dict(row) for row in cur.fetchall()]





    @_synchronized
    def close(self) -> None:
        self._conn.close()
