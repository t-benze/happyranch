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
from runtime.infrastructure.db.dreams import DreamsMixin
from runtime.infrastructure.db.jobs import JobsMixin
from runtime.infrastructure.db.knowledge import KnowledgeMixin
from runtime.infrastructure.db.sessions import SessionsMixin
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


# Closed translation between the precise pre-final stage vocabulary and the
# terminal refusal-housekeeping vocabulary.  Both the stage writer (which must
# persist the obligation before dropping its live owner) and the hook consumer
# use this one table.
_AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING = {
    "owner_lost": "owner_lost",
    "cancelled": "cancelled",
    "claim_failed": "claim_failed",
    "claim_audit_missing": "claim_audit_missing",
    "evaluation_failed": "evaluation_failed",
    "evaluation_audit_missing": "evaluation_audit_missing",
    "evaluation_missing": "evaluation_audit_missing",
    "consume_failed": "consume_failed",
    "consume_audit_missing": "consume_audit_missing",
    "final_commit_failed": "final_commit_failed",
    "identity_mismatch": "identity_mismatch",
    "decision_dispatch_interrupted": "decision_dispatch_interrupted",
    "transaction_owned": "identity_mismatch",
    "evidence_drift": "identity_mismatch",
    "schema_drift": "identity_mismatch",
    "already_claimed": "interrupted_pre_final",
    "already_audited": "interrupted_pre_final",
    "already_evaluated": "interrupted_pre_final",
    "already_consumed": "interrupted_pre_final",
}








@dataclass(frozen=True)
class VerifiedRetry:
    """Read-only provenance result; final spawn must revalidate under its writer lock."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class InvalidLineage:
    reason: str


@dataclass(frozen=True)
class RetryClaim:
    """Original report ownership, captured before preflight or preparation."""

    task_id: str
    assigned_agent: str | None
    team: str
    current_session_id: str | None
    step: int
    active_chain: str | None
    active_fanout: str | None
    revision: int
    note: str | None
    status: TaskStatus
    block_kind: BlockKind | None
    cancelled_at: str | None
    result_row_id: int | None = None

    @classmethod
    def from_task(cls, task: TaskRecord, *, result_row_id: int | None = None):
        return cls(task.id, task.assigned_agent, task.team, task.current_session_id,
                   task.orchestration_step_count, task.active_chain, task.active_fanout,
                   task.revision_count, task.note, task.status, task.block_kind,
                   task.cancelled_at, result_row_id)


@dataclass(frozen=True)
class Committed:
    child_ids: tuple[str, ...]


@dataclass(frozen=True)
class LostClaim:
    """No durable mutation or enqueue is permitted for this stale input."""


SpawnOutcome = Committed | InvalidLineage | LostClaim


@dataclass(frozen=True)
class PendingRetry:
    claim: RetryClaim
    result_id: int
    feedback: str


class _RetryEvidenceRefusal(Exception):
    """Internal control flow for invalid recorded evidence, never SQLite errors."""


class LineageTooDeep(Exception):
    """Ancestor walk exceeded the safety bound; indicates data corruption."""






def _now() -> datetime:
    return datetime.now(timezone.utc)




_V2_MALFORMED_DECISION = object()


def _canonical_completion_json(value):
    """Return one canonical structural form for a persisted JSON column.

    ``value`` is either the persisted TEXT column (a JSON document string or
    ``None``) or the in-flight client value the callback route projects (a
    ``dict``/``list``, a pre-serialized JSON string, or ``None``).  Parsing both
    sides before re-dumping gives semantic JSON equality without depending on
    key order or whitespace.  Unparseable/ unrepresentable values become a
    distinct sentinel tuple so they never accidentally compare equal.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return ("__unparseable__", str(value))
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ("__unserializable__", repr(value))


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




# Closed key sets of every result-stage event that is NOT the ``spent`` receipt.
# The spend classifier may only treat a non-``spent`` row as independently
# legitimate prior history when the row's payload carries the exact closed key
# set of the recognized other stage.  An arbitrary/absent/malformed
# discriminator, or a recognized name with a non-authentic shape, is never
# proof of unrelatedness and is classified by identity instead.  These shapes
# mirror the in-file payload builders: the attempt-stage audits
# (``admitted``/``*_audited``/``refused``), the final ``continued`` result-stage
# event, and the publication/admission event payloads.
_V2_ATTEMPT_RESULT_STAGE_KEYS = frozenset({
    "stage", "attempt_id", "result_id", "binding_id", "contract_id",
    "contract_version", "contract_digest", "release_id", "activation_id",
    "activation_epoch", "selector_id", "assessment_digest", "owner_attempt_id",
    "origin_boot_id", "finalization_state",
})
_V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS = _V2_ATTEMPT_RESULT_STAGE_KEYS | {
    "candidate_id",
}
_V2_REFUSAL_RESULT_STAGE_KEYS = _V2_ATTEMPT_RESULT_STAGE_KEYS | {"refusal_code"}
_V2_REFUSAL_RESULT_STAGE_KEYS_WITH_CANDIDATE = _V2_REFUSAL_RESULT_STAGE_KEYS | {
    "candidate_id",
}
_V2_CONTINUED_RESULT_STAGE_KEYS = _V2_ATTEMPT_RESULT_STAGE_KEYS | {
    "candidate_id", "envelope_id", "notification_id", "generation_id",
}
_V2_ADMISSION_RESULT_STAGE_KEYS = frozenset({
    "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
    "notification_id", "generation_id", "next_session_id",
})
_V2_PUBLICATION_RESULT_STAGE_KEYS = frozenset({
    "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
    "notification_id", "generation_id", "publication_attempt",
    "publisher_boot_id",
})
_V2_INVALIDATION_RESULT_STAGE_KEYS = frozenset({
    "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
    "notification_id", "generation_id",
})
# THR-229 checkpoint C3d3c2: the closed result-keyed decision-dispatch event
# shape.  Each decision event binds the exact causal attempt/candidate/result/
# envelope/notification/generation PLUS the reserved next session, the immutable
# spending result and its bound ``report_digest`` -- the same exact receipt
# identity the ``spent`` audit binds.  ``spent`` carries exactly this shape too.
_V2_DECISION_RESULT_STAGE_KEYS = frozenset({
    "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
    "notification_id", "generation_id", "next_session_id",
    "spending_result_id", "report_digest",
})
_V2_SPEND_RESULT_STAGE_KEYS = _V2_DECISION_RESULT_STAGE_KEYS
_V2_SPEND_OTHER_RESULT_STAGE_KEY_SETS = {
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED: (
        _V2_ATTEMPT_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED: (
        _V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED: (
        _V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED: (
        _V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED: (
        _V2_REFUSAL_RESULT_STAGE_KEYS,
        _V2_REFUSAL_RESULT_STAGE_KEYS_WITH_CANDIDATE,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED: (
        _V2_CONTINUED_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED: (
        _V2_PUBLICATION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED: (
        _V2_PUBLICATION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED: (
        _V2_PUBLICATION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED: (
        _V2_PUBLICATION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_INVALIDATED: (
        _V2_INVALIDATION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED: (
        _V2_ADMISSION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED: (
        _V2_ADMISSION_RESULT_STAGE_KEYS,
    ),
    # The decision-dispatch family is independently legitimate OTHER-stage
    # history for the ``spent`` classifier, so a committed claim/applied/
    # interruption never makes an exact ``already_spent_exact`` replay fail.
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED: (
        _V2_DECISION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED: (
        _V2_DECISION_RESULT_STAGE_KEYS,
    ),
    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED: (
        _V2_DECISION_RESULT_STAGE_KEYS,
    ),
}
# Every recognized closed result-stage shape, used by the decision-dispatch
# classifier to skip only genuinely authentic OTHER-stage history.
_V2_ALL_RESULT_STAGE_KEY_SETS = dict(_V2_SPEND_OTHER_RESULT_STAGE_KEY_SETS)
_V2_ALL_RESULT_STAGE_KEY_SETS[AUTHORITY_POLICY_V2_RESULT_STAGE_SPENT] = (
    _V2_SPEND_RESULT_STAGE_KEYS,
)


class Database(
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
    def insert_task(self, task: TaskRecord) -> None:
        params = (
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
        )
        self._conn.execute(
            """INSERT INTO tasks (id, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id,
               revisit_of_task_id, dispatched_from_thread_id,
               block_kind, note,
               orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
               current_session_id, zombie_flagged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        self._conn.commit()

    @_synchronized
    def get_task(self, task_id: str) -> TaskRecord | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            team=row["team"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
            revisit_of_task_id=row["revisit_of_task_id"],
            dispatched_from_thread_id=row["dispatched_from_thread_id"],
            block_kind=row["block_kind"],
            blocked_on_job_ids=row["blocked_on_job_ids"],
            active_chain=row["active_chain"],
            active_fanout=row["active_fanout"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_output_dir=row["final_output_dir"],
            cancelled_at=row["cancelled_at"],
            last_heartbeat=row["last_heartbeat"],
            session_timeout_seconds=row["session_timeout_seconds"],
            task_type=row["task_type"],
            executor_pid=row["executor_pid"],
            current_session_id=row["current_session_id"],
            zombie_flagged_at=row["zombie_flagged_at"],
        )

    @_synchronized
    def list_tasks(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
        blocked_on_job_id: str | None = None,
    ) -> list[TaskRecord]:
        # Cursor pagination: callers pass the last task_id of the previous page
        # as `before_task_id`; we resolve its created_at and emit the next page
        # using (created_at, id) DESC for a stable tiebreak. `status` and
        # `block_kind` are optional equality filters (read-only backlog queries).
        # `blocked_on_job_id` is a DERIVE filter for the Jobs "if-approved"
        # cascade — finds tasks blocked on a specific job id.
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        # Assemble the WHERE clause dynamically: with four optional filter
        # dimensions (agent, status, block_kind, cursor) an if/elif tree would
        # be 2**4 branches. StrEnum members stringify to their value, so str()
        # accepts both the enum and a raw query-param string.
        conditions: list[str] = []
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if blocked_on_job_id is not None:
            # Mirror jobs_runner.py canonic pred: status + block_kind + LIKE.
            # Without the status/block_kind guard a task that was once
            # blocked on JOB-X but is now done/running leaks into the
            # "if approved" cascade. Path B changed the parked carrier
            # from blocked(blocked_on_job) to in_progress(blocked_on_job).
            conditions.append(
                "status = ? AND block_kind = ? AND blocked_on_job_ids LIKE ?"
            )
            params.extend([
                TaskStatus.IN_PROGRESS.value,
                BlockKind.BLOCKED_ON_JOB.value,
                f'%"{blocked_on_job_id}"%',
            ])
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def get_children(self, parent_task_id: str) -> list[str]:
        """Return direct children of a task, ordered by creation time."""
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at",
            (parent_task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def get_descendant_task_ids(self, root_task_id: str) -> list[str]:
        """Return all descendant task IDs in the parent_task_id subtree
        (direct children, grandchildren, etc.). Excludes the root itself.

        Uses the same iterative get_children() walk as get_subtree_statuses().
        """
        ids: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            ids.append(child_id)
            stack.extend(self.get_children(child_id))
        return ids

    # Severity ranking for subtree rollup: lower = worse.
    # escalated is the attention-grabbing worst (genuine founder attention);
    # superseded is the calmest. Under the Path-B stored model
    # (THR-037 Change B) a delegating/parked parent is in_progress (rank 2),
    # so a healthy delegating parent NO LONGER dominates its subtree to amber —
    # only a real escalated (0) or failed (1) descendant pulls the rollup up.
    # cancelled is a deliberate terminal stop with no pending work, so it ranks
    # calmer than completed. The deprecated 'blocked' value is intentionally
    # absent: any lingering blocked row falls to the default rank (99, calmest).
    _SEVERITY_RANK: dict[str, int] = {
        "escalated": 0,
        "failed": 1,
        "in_progress": 2,
        "pending": 3,
        "completed": 4,
        "cancelled": 5,
        "superseded": 6,
    }

    @_synchronized
    def get_subtree_statuses(self, root_task_id: str) -> list[str]:
        """Return the status values of all descendant tasks in the
        parent_task_id subtree (direct children, grandchildren, etc.).

        Walks the tree recursively via get_children(). Excludes the root
        task itself; only descendants are collected. An empty list means the
        root has no children (rollup = the root's own status).

        This is a DERIVE — no schema change; uses existing parent_task_id
        and get_children().
        """
        statuses: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            child = self.get_task(child_id)
            if child is not None:
                statuses.append(child.status.value)
                stack.extend(self.get_children(child_id))
        return statuses

    def _worst_subtree_status(self, root_status: str, child_statuses: list[str]) -> str:
        """Return the worst status among a root's own status and its
        descendants' statuses.

        The rollup of a singleton subtree is the root's own status (P1: no
        guessed severity). Uses _SEVERITY_RANK — lowest rank wins.
        """
        worst = root_status
        worst_rank = self._SEVERITY_RANK.get(worst, 99)
        for s in child_statuses:
            rank = self._SEVERITY_RANK.get(s, 99)
            if rank < worst_rank:
                worst = s
                worst_rank = rank
        return worst

    def _get_subtree_tasks(self, root_task_id: str) -> list[TaskRecord]:
        """Cycle-safe, iterative parent_task_id descendant snapshot.

        Exact bounds: each reachable descendant id enters ``seen`` once, so the
        walk makes <= D get_task() calls and <= D + 1 get_children() calls and
        holds <= D TaskRecords, where D = reachable descendant count. A
        malformed parent_task_id cycle supplied directly to this private helper
        terminates on ``seen``; it cannot be reached from a NULL-parent
        list_roots root. The iterative walk also avoids recursion limits for
        ordinary deep trees.

        Private helper: left undecorated and called only under an existing
        synchronized public entry (``list_roots``). If ever exposed as a public
        ``Database`` method it must gain ``@_synchronized``.
        """
        seen = {root_task_id}
        out: list[TaskRecord] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            tid = stack.pop()
            if tid in seen:
                continue
            seen.add(tid)
            task = self.get_task(tid)
            if task is None:
                continue
            out.append(task)
            stack.extend(self.get_children(tid))
        return out

    def _current_failed_contributions(
        self,
        desc: list[TaskRecord],
        by_id: dict[str, TaskRecord],
        succ: dict[str, list[str]],
    ) -> set[str]:
        """IDs of FAILED descendants that still contribute ``failed``.

        Mirrors ``run_step._current_unresolved_failed_leaves`` /
        ``_collect_leaf`` on the finite descendant snapshot without importing
        orchestration policy. ``_collect_leaf`` returns a node only at its
        no-successor base case, so across all FAILED seeds the contributing set
        is exactly the reachable terminal FAILED nodes: a FAILED descendant
        with a forward same-parent successor has no unresolved failed leaf of
        its own. A cycle (malformed lineage) is ambiguous, so it fails
        conservative and is KEPT, never silently retired.

        Exact bounds: one iterative colored DFS over the descendant nodes/edges
        is O(D + E) time and O(D) space; every node is colored once and every
        edge examined once. No recursion, so no RecursionError.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        color = dict.fromkeys(by_id, WHITE)
        reaches_cycle: set[str] = set()
        for start in by_id:
            if color[start] != WHITE:
                continue
            color[start] = GREY
            stack = [(start, iter(succ.get(start, ())))]
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if color[nxt] == GREY:              # back edge -> cycle
                        reaches_cycle.add(nxt)
                    elif color[nxt] == WHITE:
                        color[nxt] = GREY
                        stack.append((nxt, iter(succ.get(nxt, ()))))
                        advanced = True
                        break
                if advanced:
                    continue
                color[node] = BLACK
                if node in reaches_cycle or any(
                    n in reaches_cycle for n in succ.get(node, ())
                ):
                    reaches_cycle.add(node)             # ancestor of a cycle
                stack.pop()

        return {
            nid
            for nid, task in by_id.items()
            if task.status == TaskStatus.FAILED
            and (not succ.get(nid) or nid in reaches_cycle)
        }

    def _current_severity_rollup(self, root: TaskRecord) -> str:
        """Worst CURRENT status over the root's own status and its
        parent_task_id descendants. Only stale FAILED-descendant contributions
        are removed; every other status (escalated, active, cancelled,
        completed, superseded, legacy) contributes exactly as before.

        Forward links are only ``revisit_of_task_id`` where both endpoints are
        descendants inside this root and the successor shares the
        predecessor's parent. No timestamp or agent is ever consulted (no
        latest-wins). Cancellation removes a predecessor's stale ``failed``
        only because the lineage then has no unresolved failed leaf — never
        because cancellation is successful retirement.

        Private helper: called only under ``list_roots`` (see
        ``_get_subtree_tasks``).
        """
        desc = self._get_subtree_tasks(root.id)
        by_id = {d.id: d for d in desc}
        succ: dict[str, list[str]] = {}
        for d in desc:
            pred = d.revisit_of_task_id
            if (
                pred is not None
                and pred in by_id                              # predecessor is a descendant
                and by_id[pred].parent_task_id == d.parent_task_id   # same parent
            ):
                succ.setdefault(pred, []).append(d.id)
        failed_now = self._current_failed_contributions(desc, by_id, succ)
        current = [
            d.status.value
            for d in desc
            if d.status != TaskStatus.FAILED or d.id in failed_now
        ]
        return self._worst_subtree_status(root.status.value, current)

    @_synchronized
    def list_roots(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
    ) -> list[TaskRecord]:
        """Return root tasks (parent_task_id IS NULL) with cursor pagination,
        same filter parameters as list_tasks(), plus a per-root _severity_rollup.

        The _severity_rollup attribute (str) is the worst CURRENT status among
        the root's own status and its parent_task_id subtree. Only a historical
        FAILED descendant contribution is curated: a FAILED descendant whose
        forward same-parent revisit lineage leaves no unresolved FAILED leaf
        (a COMPLETED/SUPERSEDED/active/cancelled successor, or a malformed
        cycle handled conservatively) no longer dominates. Every other status,
        the root's own severity, and all escalations are preserved; a root
        without children shows its own status. Set as a dynamic attribute on
        the TaskRecord (not a model field — DERIVE, no schema). See
        ``_current_severity_rollup``.
        """
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        conditions = ["parent_task_id IS NULL"]
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} "
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        results: list[TaskRecord] = []
        for row in cursor.fetchall():
            task = TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            object.__setattr__(
                task, '_severity_rollup',
                self._current_severity_rollup(task),
            )
            results.append(task)
        return results

    @_synchronized
    def list_tasks_by_brief_prefix(
        self,
        brief_prefix: str,
        *,
        assigned_agent: str | None = None,
        limit: int = 200,
    ) -> list[TaskRecord]:
        """Tasks whose brief starts with ``brief_prefix``, newest-first.

        Authoritative SQL-side marker filter (THR-195 / TASK-6043): unlike a
        bounded scan of recent ordinary tasks, an SQL ``brief LIKE`` filter
        can never be exhausted by newer non-matching rows, so older
        marker-matched rows cannot be hidden. ``%`` and ``_`` in the prefix
        are escaped (treated literally). ``assigned_agent`` narrows to one
        agent's rows when given.
        """
        escaped = (
            brief_prefix.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        conditions = ["brief LIKE ? ESCAPE '\\'"]
        params: list = [escaped + "%"]
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(conditions)} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def list_tasks_by_thread(
        self, thread_id: str,
    ) -> list[dict]:
        """Return tasks dispatched from a thread, newest-first.

        Uses the existing idx_tasks_dispatched_from_thread_id partial index.
        Returns lightweight summary dicts with the fields the frontend needs:
        id, status, brief, assigned_agent, created_at, parent_task_id.
        """
        cursor = self._conn.execute(
            "SELECT id, status, brief, assigned_agent, created_at, parent_task_id "
            "FROM tasks WHERE dispatched_from_thread_id = ? "
            "ORDER BY created_at DESC",
            (thread_id,),
        )
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "brief": row["brief"],
                "assigned_agent": row["assigned_agent"],
                "created_at": row["created_at"],
                "parent_task_id": row["parent_task_id"],
            }
            for row in cursor.fetchall()
        ]

    @_synchronized
    def get_direct_revisits(self, task_id: str) -> list[str]:
        """Return IDs of tasks whose revisit_of_task_id points at this task,
        ordered by creation. Uses idx_tasks_revisit_of.
        """
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE revisit_of_task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def batch_get_direct_revisits(
        self, task_ids: list[str],
    ) -> dict[str, list[str]]:
        """Return direct revisits for multiple task_ids in a single query.

        Avoids the N+1 pattern when a list route needs direct_revisits for
        every returned item. Uses idx_tasks_revisit_of.
        """
        if not task_ids:
            return {}
        placeholders = ','.join(['?'] * len(task_ids))
        cursor = self._conn.execute(
            f"SELECT revisit_of_task_id, id FROM tasks"
            f" WHERE revisit_of_task_id IN ({placeholders})"
            f" ORDER BY created_at",
            tuple(task_ids),
        )
        result: dict[str, list[str]] = {tid: [] for tid in task_ids}
        for row in cursor.fetchall():
            root_id = row["revisit_of_task_id"]
            result.setdefault(root_id, []).append(row["id"])
        return result

    @_synchronized
    def walk_ancestors(self, task_id: str, max_hops: int = 20) -> list[TaskRecord]:
        """Return [task, parent, ..., root] by following parent_task_id.

        Raises LineageTooDeep if the walk exceeds max_hops (defensive bound;
        real lineages are 2-4 deep). A missing intermediate task truncates the
        walk silently — callers see the chain they could reconstruct.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.parent_task_id
        if current_id is not None:
            raise LineageTooDeep(f"walk from {task_id} exceeded {max_hops} hops")
        return chain

    @_synchronized
    def walk_revisit_chain(
        self, task_id: str, max_hops: int = 20, truncate: bool = False,
    ) -> list[TaskRecord]:
        """Return [task, predecessor, ..., original] by following revisit_of_task_id.

        Sideways edge — does NOT cross into parent_task_id ancestor space.
        Non-revisit tasks return [task]. Missing task returns []. Overruns
        raise LineageTooDeep by default (same pattern as walk_ancestors); pass
        truncate=True to return the first max_hops entries instead — read
        paths use this because revisit history grows naturally over a task's
        lifetime and must not 500 once it exceeds the defensive bound.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.revisit_of_task_id
        if current_id is not None and not truncate:
            raise LineageTooDeep(
                f"revisit chain from {task_id} exceeded {max_hops} hops"
            )
        return chain

    @_synchronized
    def get_recall_payload(self, task_id: str) -> dict | None:
        """Return a flat dict suitable for the /recall endpoint, or None.

        ``children`` is the list of direct child task ids — the route layer
        promotes them to full payloads when ``tree=true``.

        ``verdict`` is the structured verdict from the latest persisted
        ``task_results`` row (deterministic: ``ORDER BY created_at DESC, id DESC LIMIT 1``
        — result-recency first with a stable id tie-breaker).
        Absent / ``None`` when no result row exists or the latest row has no verdict.
        """
        task = self.get_task(task_id)
        if task is None:
            return None
        created_at = (
            task.created_at.isoformat()
            if hasattr(task.created_at, "isoformat")
            else task.created_at
        )
        completed_at = (
            task.completed_at.isoformat()
            if hasattr(task.completed_at, "isoformat")
            else task.completed_at
        )
        # Latest structured verdict from persisted task_results, if any.
        verdict: str | None = None
        latest = self._conn.execute(
            "SELECT verdict FROM task_results WHERE task_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if latest is not None:
            verdict = latest["verdict"] if "verdict" in latest.keys() else None
        return {
            "task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "revisit_of_task_id": task.revisit_of_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": created_at,
            "completed_at": completed_at,
            "output_summary": task.note,
            "output_dir": task.final_output_dir,
            "verdict": verdict,
            "children": self.get_children(task.id),
        }

    @_synchronized
    def list_agent_tasks(self, agent: str, limit: int = 50) -> list[TaskRecord]:
        """Return tasks assigned to an agent, newest-first.

        Orders by the latest available timestamp (completed_at > updated_at >
        created_at) as a lexicographic string compare — our ISO-8601 values
        include microseconds and +00:00 which SQLite's ``datetime()`` parser
        rejects, but they sort correctly as raw strings.
        """
        cursor = self._conn.execute(
            """SELECT * FROM tasks WHERE assigned_agent = ?
               ORDER BY COALESCE(completed_at, updated_at, created_at) DESC
               LIMIT ?""",
            (agent, limit),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "blocked_on_job_ids", "note", "orchestration_step_count",
            "final_output_dir", "cancelled_at", "last_heartbeat",
            "executor_pid", "current_session_id", "zombie_flagged_at",
        }
        # NOTE: filter on membership, not on None-ness — block_kind must be
        # resettable to NULL when a task unblocks.
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if hasattr(v, "value"):
                updates[k] = v.value
            else:
                updates[k] = v
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    @_synchronized
    def update_task_active_chain(self, task_id: str, active_chain: str | None) -> None:
        """Set or clear tasks.active_chain. Pass None to clear (chain finished,
        aborted, or never declared)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_chain = ?, updated_at = ? WHERE id = ?",
            (active_chain, now, task_id),
        )
        self._conn.commit()

    @_synchronized
    def update_task_active_fanout(self, task_id: str, active_fanout: str | None) -> None:
        """Set or clear tasks.active_fanout. Pass None to clear (fan-out join
        claimed or parent terminal)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_fanout = ?, updated_at = ? WHERE id = ?",
            (active_fanout, now, task_id),
        )
        self._conn.commit()

    @staticmethod
    def _retry_object(raw: str) -> dict:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise _RetryEvidenceRefusal("malformed_evidence") from exc
        if not isinstance(value, dict):
            raise _RetryEvidenceRefusal("malformed_evidence")
        return value

    def _retry_audits(self, task_id: str, action: str) -> list[tuple[str, dict]]:
        # These are the actual org-local records, without a CLI pagination cap.
        return [
            (row["agent"], self._retry_object(row["payload"]))
            for row in self._conn.execute(
                "SELECT agent, payload FROM audit_log WHERE task_id=? AND action=?",
                (task_id, action),
            )
        ]

    def _retry_require_audit(
        self, task_id: str, action: str, agent: str, expected: dict, *,
        decision: str | None = None,
    ) -> None:
        observations = self._retry_audits(task_id, action)
        if decision is not None:
            # Earlier manual continues belong to this root's history, but do
            # not describe its eventual supersession. Malformed JSON still
            # fails in _retry_audits, rather than silently losing evidence.
            observations = [(actor, payload) for actor, payload in observations
                            if payload.get("decision") != "continue"]
        if not observations or any(
            actual_agent != agent or any(payload.get(k) != v for k, v in expected.items())
            for actual_agent, payload in observations
        ):
            raise _RetryEvidenceRefusal("malformed_evidence")

    def _retry_manager_edge(self, predecessor, successor, records: list) -> None:
        normalized = {tuple(row) for row in records}
        if len(normalized) != 1:
            raise _RetryEvidenceRefusal("ambiguous_predecessor")
        row = records[0]
        actor = successor["assigned_agent"]
        evidence = self._retry_object(row["attestation_evidence"])
        attestation = evidence.get("attestation")
        fields = (
            "policy_product_intent_unchanged", "no_budget_or_external_commitment",
            "no_permission_or_cross_team_change",
            "no_schema_auth_security_privacy_or_data_access_change",
            "no_unresolved_founder_gate",
        )
        if (
            row["actor_agent"] != actor
            or row["original_root_task_id"] != predecessor["id"]
            or not isinstance(row["actor_session_id"], str) or not row["actor_session_id"]
            or evidence.get("rule_version") != "manager_supersession_attestation.v1"
            or evidence.get("actor_agent") != actor
            or evidence.get("actor_session_id") != row["actor_session_id"]
            or not isinstance(attestation, dict)
            or not isinstance(attestation.get("recovery_reason"), str)
            or not attestation["recovery_reason"].strip()
            or any(attestation.get(field) is not True for field in fields)
        ):
            raise _RetryEvidenceRefusal("malformed_evidence")
        for name in ("predecessor_brief", "successor_brief"):
            if (not isinstance(row[name], str)
                or hashlib.sha256(row[name].encode()).hexdigest() != row[name + "_sha256"]):
                raise _RetryEvidenceRefusal("malformed_evidence")
        payload = {key: row[key] for key in (
            "original_root_task_id", "actor_session_id", "rationale",
            "predecessor_brief_sha256", "successor_brief_sha256",
        )}
        payload["attestation_evidence"] = evidence
        for task, other in ((predecessor, successor), (successor, predecessor)):
            self._retry_require_audit(task["id"], "manager_supersession", actor, {
                **payload, "counterpart_task_id": other["id"],
            })

    def _retry_dispatch_edge(self, predecessor, successor, evidence: dict) -> None:
        thread_id = evidence.get("thread_id")
        actor = successor["assigned_agent"]
        if (
            not isinstance(thread_id, str) or not thread_id
            or successor["dispatched_from_thread_id"] != thread_id
            or successor["revisit_of_task_id"] is not None
            or evidence.get("founder_note") != f"thread {thread_id} dispatch by {actor}"
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        bindings = self._conn.execute(
            "SELECT invocation_token FROM thread_invocations "
            "WHERE dispatched_task_id=? AND thread_id=? AND agent_name=?",
            (successor["id"], thread_id, actor),
        ).fetchall()
        if len(bindings) != 1:
            raise _RetryEvidenceRefusal("invocation_binding")
        try:
            inv = self.get_invocation_any_status(bindings[0]["invocation_token"])
        except ValueError as exc:
            raise _RetryEvidenceRefusal("invocation_binding") from exc
        if (
            inv is None or inv.dispatched_task_id != successor["id"]
            or inv.thread_id != thread_id or inv.agent_name != actor
            or inv.purpose not in (ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP)
            or inv.status not in (
                ThreadInvocationStatus.PENDING, ThreadInvocationStatus.CONSUMED,
                ThreadInvocationStatus.DECLINED, ThreadInvocationStatus.TIMEOUT,
                ThreadInvocationStatus.FAILED,
            )
            or self._conn.execute(
                "SELECT 1 FROM thread_messages WHERE thread_id=? AND seq=?",
                (thread_id, inv.triggering_seq),
            ).fetchone() is None
            or self._conn.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone() is None
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        expected = {"task_id": successor["id"], "dispatcher": actor,
                    "target_agent": actor, "team": successor["team"]}
        # Other dispatches in this thread are unrelated to this edge.
        audits = self._conn.execute(
            "SELECT agent, payload FROM audit_log WHERE task_id=? AND action='thread_dispatch' "
            "AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.task_id') END=?",
            (thread_id, successor["id"]),
        ).fetchall()
        if not audits or any(
            row["agent"] != actor or any(self._retry_object(row["payload"]).get(k) != v
                                          for k, v in expected.items()) for row in audits
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        messages = self._conn.execute(
            "SELECT speaker, kind, seq, system_payload_json FROM thread_messages "
            "WHERE thread_id=? AND CASE WHEN json_valid(system_payload_json) "
            "THEN json_extract(system_payload_json,'$.task_id') END=?",
            (thread_id, successor["id"]),
        ).fetchall()
        messages = [row for row in messages if self._retry_object(row["system_payload_json"]).get("kind_tag") == "task_dispatched"]
        if not messages or any(
            row["speaker"] != actor or row["kind"] != "system" or row["seq"] <= inv.triggering_seq
            or any(self._retry_object(row["system_payload_json"]).get(k) != v for k, v in expected.items())
            for row in messages
        ):
            raise _RetryEvidenceRefusal("invocation_binding")

    def _retry_escalation_edge(self, predecessor, successor, evidence: dict) -> None:
        actor = evidence.get("actor")
        prior = evidence.get("prior_block_kind")
        if prior not in ("escalated", "delegated"):
            raise _RetryEvidenceRefusal("malformed_evidence")
        if actor == "thread-dispatch":
            self._retry_dispatch_edge(predecessor, successor, evidence)
        elif actor == "cli":
            if successor["revisit_of_task_id"] != predecessor["id"]:
                raise _RetryEvidenceRefusal("malformed_evidence")
            audits = self._retry_audits(successor["id"], "revisit_of")
            if not audits:
                raise _RetryEvidenceRefusal("malformed_evidence")
            for audit_actor, payload in audits:
                cascade = payload.get("cascade")
                if (
                    audit_actor != "founder" or payload.get("actor") != "cli"
                    or payload.get("predecessor_root") != predecessor["id"]
                    or payload.get("prior_status") != "blocked-" + prior
                    or not isinstance(cascade, list) or not cascade
                    or not all(isinstance(item, str) for item in cascade)
                    or len(set(cascade)) != len(cascade)
                    or cascade[0] != predecessor["id"] or cascade[-1] != payload.get("flagged")
                ):
                    raise _RetryEvidenceRefusal("malformed_evidence")
                for parent_id, child_id in zip(cascade, cascade[1:]):
                    child = self._conn.execute("SELECT parent_task_id FROM tasks WHERE id=?", (child_id,)).fetchone()
                    if child is None or child[0] != parent_id:
                        raise _RetryEvidenceRefusal("malformed_evidence")
            self._retry_require_audit(predecessor["id"], "revisit_spawned", "founder", {"new_root": successor["id"]})
        elif actor in ("founder", successor["assigned_agent"]):
            thread_id = evidence.get("thread_id")
            expected = {"decision": "supersede", "resolution_path": "manual_break_glass"}
            if actor != "founder":
                if (
                    not isinstance(thread_id, str) or not thread_id
                    or successor["dispatched_from_thread_id"] != thread_id
                    or predecessor["dispatched_from_thread_id"] != thread_id
                    or self._conn.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone() is None
                ):
                    raise _RetryEvidenceRefusal("malformed_evidence")
                # Thread manual resolution has no stored token-to-successor join.
                # The actual producer's resolution and supersession rows are authority.
                expected.update(resolution_path="thread_manual_supersede", thread_id=thread_id)
            if prior != "escalated" or successor["revisit_of_task_id"] is not None:
                raise _RetryEvidenceRefusal("malformed_evidence")
            self._retry_require_audit(
                predecessor["id"], "escalation_resolved", actor, expected, decision="supersede",
            )
        else:
            raise _RetryEvidenceRefusal("malformed_evidence")

    @_synchronized
    def verify_retry_link(self, parent_id: str, target_agent: str, failed_id: str | None) -> VerifiedRetry | InvalidLineage:
        """Verify an explicit retry through recorded supersessions in this org DB.

        Inclusive limit: twenty root task records, at most nineteen edges.
        This read-only check is advisory until repeated inside the spawn transaction.
        Brief text, task numbering and arbitrary revisit links confer no authority.
        """
        try:
            local_failed = {row[0] for row in self._conn.execute(
                "SELECT id FROM tasks WHERE parent_task_id=? AND assigned_agent=? AND status='failed'",
                (parent_id, target_agent),
            )}
            if failed_id is None:
                if local_failed:
                    raise _RetryEvidenceRefusal("retry_link_required")
                return VerifiedRetry((parent_id,))
            failed = self._conn.execute("SELECT * FROM tasks WHERE id=?", (failed_id,)).fetchone()
            if failed is None:
                raise _RetryEvidenceRefusal("retry_not_found")
            if failed["status"] != "failed":
                raise _RetryEvidenceRefusal("retry_not_failed")
            if failed["assigned_agent"] != target_agent:
                raise _RetryEvidenceRefusal("retry_agent_mismatch")
            if failed["parent_task_id"] == parent_id:
                return VerifiedRetry((parent_id,))
            # A remote historical link cannot retire an unresolved local retry.
            retired = {row[0] for row in self._conn.execute(
                "SELECT revisit_of_task_id FROM tasks WHERE parent_task_id=? AND revisit_of_task_id IS NOT NULL",
                (parent_id,),
            )}
            if local_failed - retired:
                raise _RetryEvidenceRefusal("retry_link_required")
            current = self._conn.execute("SELECT * FROM tasks WHERE id=?", (parent_id,)).fetchone()
            if current is None:
                raise _RetryEvidenceRefusal("no_verified_supersession")
            team, manager = current["team"], current["assigned_agent"]
            path: list[str] = []
            while True:
                if current["id"] in path:
                    raise _RetryEvidenceRefusal("lineage_cycle")
                if len(path) == 20:
                    raise _RetryEvidenceRefusal("lineage_record_limit_20")
                if current["parent_task_id"] is not None or current["task_type"] != "task":
                    raise _RetryEvidenceRefusal("no_verified_supersession")
                if current["team"] != team:
                    raise _RetryEvidenceRefusal("team_mismatch")
                if current["assigned_agent"] != manager:
                    raise _RetryEvidenceRefusal("manager_mismatch")
                path.append(current["id"])
                if current["id"] == failed["parent_task_id"]:
                    return VerifiedRetry(tuple(path))
                if len(path) == 20:
                    raise _RetryEvidenceRefusal("lineage_record_limit_20")
                manager_rows = self._conn.execute(
                    "SELECT * FROM manager_supersessions WHERE successor_task_id=?", (current["id"],),
                ).fetchall()
                escalation_rows = self._conn.execute(
                    "SELECT task_id,agent,payload FROM audit_log WHERE action='escalation_superseded' "
                    "AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.successor_root') END=?",
                    (current["id"],),
                ).fetchall()
                predecessors = {r["predecessor_task_id"] for r in manager_rows} | {r["task_id"] for r in escalation_rows}
                if not predecessors:
                    raise _RetryEvidenceRefusal("no_verified_supersession")
                if len(predecessors) != 1:
                    raise _RetryEvidenceRefusal("ambiguous_predecessor")
                predecessor_id = predecessors.pop()
                if predecessor_id in path:
                    raise _RetryEvidenceRefusal("lineage_cycle")
                predecessor = self._conn.execute("SELECT * FROM tasks WHERE id=?", (predecessor_id,)).fetchone()
                if predecessor is None or predecessor["status"] != "superseded":
                    raise _RetryEvidenceRefusal("predecessor_not_superseded")
                outgoing = {r[0] for r in self._conn.execute(
                    "SELECT successor_task_id FROM manager_supersessions WHERE predecessor_task_id=?", (predecessor_id,),
                )}
                outgoing_audits = self._retry_audits(predecessor_id, "escalation_superseded")
                for audit_actor, payload in outgoing_audits:
                    successor_id = payload.get("successor_root")
                    if audit_actor != "founder" or not isinstance(successor_id, str) or not successor_id:
                        raise _RetryEvidenceRefusal("malformed_evidence")
                    outgoing.add(successor_id)
                if len(outgoing) > 1:
                    raise _RetryEvidenceRefusal("competing_successors")
                if manager_rows and escalation_rows:
                    raise _RetryEvidenceRefusal("ambiguous_predecessor")
                if manager_rows:
                    self._retry_manager_edge(predecessor, current, manager_rows)
                else:
                    normalized = {json.dumps(payload, sort_keys=True) for _, payload in outgoing_audits}
                    if len(normalized) != 1:
                        raise _RetryEvidenceRefusal("ambiguous_predecessor")
                    self._retry_escalation_edge(predecessor, current, outgoing_audits[0][1])
                current = predecessor
        except _RetryEvidenceRefusal as exc:
            return InvalidLineage(str(exc))

    def _retry_claim_matches(self, claim: RetryClaim, *, pending: bool = False) -> bool:
        if claim.status != TaskStatus.IN_PROGRESS or claim.block_kind is not None or claim.cancelled_at is not None:
            return False
        task = self.get_task(claim.task_id)
        if (task is None or task.status != (TaskStatus.PENDING if pending else TaskStatus.IN_PROGRESS)
                or task.block_kind is not None or task.cancelled_at is not None
                or not claim.current_session_id or not claim.assigned_agent
                or replace(RetryClaim.from_task(task, result_row_id=claim.result_row_id),
                           status=TaskStatus.IN_PROGRESS if pending else task.status) != claim):
            return False
        if claim.result_row_id is not None:
            row = self._conn.execute(
                "SELECT task_id, agent, session_id FROM task_results WHERE id = ?",
                (claim.result_row_id,),
            ).fetchone()
            if row is None or tuple(row) != (claim.task_id, claim.assigned_agent, claim.current_session_id):
                return False
        return True

    def _retry_spawn_check(self, parent_id: str, children: list, claim: RetryClaim,
                           revision_delta: int = 0, revision_cap: int = 0,
                           *, ordinary: bool = False) -> InvalidLineage | LostClaim | None:
        if parent_id != claim.task_id or not self._retry_claim_matches(claim):
            return LostClaim()
        for child in children:
            if child.parent_task_id != parent_id:
                return InvalidLineage("child_parent_mismatch")
            outcome = self.verify_retry_link(parent_id, child.assigned_agent, child.revisit_of_task_id)
            if isinstance(outcome, InvalidLineage):
                return outcome
        delta = 0
        if ordinary:
            row = self._conn.execute(
                "SELECT assigned_agent FROM tasks WHERE parent_task_id = ? "
                "AND status IN ('completed', 'failed') ORDER BY created_at, id LIMIT 1",
                (parent_id,),
            ).fetchone()
            delta = int(row is not None and row[0] == children[0].assigned_agent
                        and row[0] != claim.assigned_agent)
        if delta != revision_delta or (delta and revision_cap > 0 and claim.revision >= revision_cap):
            return LostClaim()
        return None

    @_synchronized
    def try_retry_feedback(self, expected_claim: RetryClaim, feedback: str) -> PendingRetry | LostClaim:
        """A: commit existing feedback result/audit and PENDING as one owned write."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if not self._retry_claim_matches(expected_claim):
                self._conn.rollback()
                return LostClaim()
            self._insert_task_result(
                task_id=expected_claim.task_id, agent=expected_claim.assigned_agent,
                session_id="", status="completed", confidence_score=0,
                output_summary=feedback, risks_flagged=[],
            )
            result_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.insert_audit_log_uncommitted(
                task_id=expected_claim.task_id, agent="orchestrator", action="orchestration_step",
                payload={"step_number": expected_claim.step,
                         "decision": {"action": "feedback", "reason": feedback}},
            )
            self._conn.execute(
                "UPDATE tasks SET status = 'pending', block_kind = NULL, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), expected_claim.task_id),
            )
            self._conn.commit()
            return PendingRetry(expected_claim, result_id, feedback)
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def admit_retry_feedback(self, pending: PendingRetry, enqueue) -> str | LostClaim:
        """B: no-write admission; hold the writer reservation through synchronous enqueue.

        Queue insertion is not a claim and is not crash-atomic with SQLite.
        Startup may enqueue PENDING again after either side of this boundary.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if not self._retry_claim_matches(pending.claim, pending=True):
                return LostClaim()
            row = self._conn.execute(
                "SELECT task_id, agent, session_id, status, output_summary FROM task_results WHERE id = ?",
                (pending.result_id,),
            ).fetchone()
            if row is None or tuple(row) != (pending.claim.task_id, pending.claim.assigned_agent,
                                             "", "completed", pending.feedback):
                return LostClaim()
            if enqueue is None:
                return "not_configured"
            enqueue()
            return "enqueued"
        finally:
            self._conn.rollback()

    @_synchronized
    def try_delegate_many(
        self, parent_id: str, children: list, *, parent_note: str,
        active_fanout_json: str | None = None,
        children_attachments: list[list[dict] | None] | None = None,
        carrier_chains: list[dict] | None = None,
        uploaded_by: str = "orchestrator",
        expected_claim: RetryClaim,
    ) -> SpawnOutcome:
        """Atomic CAS: insert N child tasks + transition parent to
        IN_PROGRESS(DELEGATED) under a single explicit SQL transaction.

        All child inserts, parent status/block_kind/active_fanout update,
        note write, attachment links/audit rows, and pipeline carrier chain
        materialization (active_chain + first leg insert) happen in one
        transaction. On any exception the transaction rolls back — no partial
        children, no orphan rows, no orphan attachment links, no orphan
        carrier state.

        Same cancel-race semantics as try_delegate (single-child): if the
        parent is cancelled or already terminal at the time of the guarded
        SELECT, no children are inserted and the parent is not overwritten.

        When ``children_attachments`` is provided, it must be a list of the
        same length as ``children``; each element is either a list of
        attachment param dicts for that child, or None/empty.

        When ``carrier_chains`` is provided, it must be a list of dicts with
        keys ``child_index`` (int), ``active_chain_json`` (str), and
        ``first_leg`` (dict with keys: id, team, brief, assigned_agent,
        status, session_timeout_seconds, task_type). The first leg is
        inserted as a child of the carrier within the same transaction.

        Committed carries direct child/carrier IDs. InvalidLineage and
        LostClaim carry no writes; the original claim is checked before
        lineage. BEGIN IMMEDIATE precedes both authoritative rereads.

        Children must already have their IDs allocated (caller calls
        next_task_id() N times before invoking this method). Carrier first
        leg IDs must also be pre-allocated.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            refusal = self._retry_spawn_check(
                parent_id, children, expected_claim,

            )
            if refusal is not None:
                self._conn.rollback()
                return refusal
            now = now_ts = datetime.now(timezone.utc).isoformat()
            for i, child in enumerate(children):
                self._conn.execute(
                    """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                       revision_count, created_at, updated_at, completed_at, parent_task_id,
                       revisit_of_task_id, dispatched_from_thread_id,
                       block_kind, note,
                       orchestration_step_count, session_timeout_seconds, task_type, active_fanout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        child.id,
                        child.status.value,
                        child.assigned_agent,
                        child.team,
                        child.brief,
                        child.revision_count,
                        child.created_at.isoformat(),
                        child.updated_at.isoformat(),
                        child.completed_at.isoformat() if child.completed_at else None,
                        child.parent_task_id,
                        child.revisit_of_task_id,
                        child.dispatched_from_thread_id,
                        child.block_kind.value if child.block_kind else None,
                        child.note,
                        child.orchestration_step_count,
                        child.session_timeout_seconds,
                        child.task_type,
                        child.active_fanout,
                    ),
                )
                # Insert attachment links for this child if present.
                child_atts = None
                if children_attachments and i < len(children_attachments):
                    child_atts = children_attachments[i]
                if child_atts:
                    self._insert_task_attachments_txn(
                        child.id, child_atts, uploaded_by,
                    )
            # Materialize pipeline carrier chains within the same transaction.
            # active_chain on the carrier + first leg insert as child of carrier
            # are atomic with the fanout spawn — no partial carrier state.
            if carrier_chains:
                for cc in carrier_chains:
                    ci = cc["child_index"]
                    cid = children[ci].id
                    self._conn.execute(
                        "UPDATE tasks SET active_chain = ? WHERE id = ?",
                        (cc["active_chain_json"], cid),
                    )
                    fl = cc["first_leg"]
                    self._conn.execute(
                        """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                           revision_count, created_at, updated_at, completed_at,
                           parent_task_id, revisit_of_task_id, dispatched_from_thread_id,
                           block_kind, note,
                           orchestration_step_count, session_timeout_seconds, task_type,
                           active_fanout)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fl["id"],
                            fl["status"].value if isinstance(fl["status"], TaskStatus) else fl["status"],
                            fl["assigned_agent"],
                            fl["team"],
                            fl["brief"],
                            fl.get("revision_count", 0),
                            fl.get("created_at", now),
                            fl.get("updated_at", now),
                            None,
                            cid,
                            None,
                            None,
                            None,
                            None,
                            fl.get("orchestration_step_count", 0),
                            fl.get("session_timeout_seconds", 0),
                            fl.get("task_type", "subtask"),
                            None,
                        ),
                    )
            self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = ?, note = ?, active_fanout = ?, updated_at = ?
                   WHERE id = ?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
                 active_fanout_json, now, parent_id),
            )
            self._conn.commit()
            return Committed(tuple(child.id for child in children))
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def try_claim_for_step(
        self,
        task_id: str,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        new_count: int,
    ) -> bool:
        """Atomic compare-and-swap for the run_step entry transition.

        Transitions the row to status=in_progress, clears block_kind/note, and
        sets orchestration_step_count=new_count, but ONLY if the row currently
        matches (expected_status, expected_block_kind). Returns True iff the
        transition occurred.

        Why this exists: two workers can pop the same task_id (e.g. a multi-
        child fan-in double-enqueued the parent). Without this CAS, both pass
        the check-then-update at run_step steps 1→3 and both spawn an agent
        subprocess. The conditional WHERE ensures only the first writer wins.

        THR-229 C3d3b mandatory fallback fence: this ordinary claim is NOT
        generation admission.  In the SAME ``BEGIN IMMEDIATE`` that would claim
        the task, a root whose durable ``authority_policy_v2_root_dispatch``
        pointer is ``pending`` (a live v2 continuation generation) refuses --
        the task cannot win on status/block_kind alone.  An untagged or stale
        queue item therefore can never adopt today's generation: the tagged
        ``try_claim_v2_continuation_generation`` path is the only admission and
        it authenticates the exact metadata token G.  Roots with no pending
        pointer keep their unchanged ordinary behavior.
        """
        now = datetime.now(timezone.utc).isoformat()
        began = False
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
            began = True
        try:
            pending_generation = self._conn.execute(
                """SELECT 1 FROM authority_policy_v2_root_dispatch
                    WHERE root_task_id=? AND state='pending'""",
                (task_id,),
            ).fetchone()
            if pending_generation is not None:
                if began:
                    self._conn.rollback()
                return False
            if expected_block_kind is None:
                cursor = self._conn.execute(
                    """UPDATE tasks
                       SET status = ?, block_kind = NULL, note = NULL,
                           orchestration_step_count = ?, updated_at = ?
                       WHERE id = ? AND status = ? AND block_kind IS NULL""",
                    (TaskStatus.IN_PROGRESS.value, new_count, now,
                     task_id, expected_status.value),
                )
            else:
                cursor = self._conn.execute(
                    """UPDATE tasks
                       SET status = ?, block_kind = NULL, note = NULL,
                           orchestration_step_count = ?, updated_at = ?
                       WHERE id = ? AND status = ? AND block_kind = ?""",
                    (TaskStatus.IN_PROGRESS.value, new_count, now,
                     task_id, expected_status.value, expected_block_kind.value),
                )
            self._conn.commit()
            return cursor.rowcount == 1
        except Exception:
            if began:
                self._conn.rollback()
            raise

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

    @_synchronized
    def try_fail_over_budget(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        note: str,
    ) -> bool:
        """Atomic CAS for the run_step max-steps budget guard — non-root variant.

        Mirror of ``try_escalate_over_budget`` (the root variant), but transitions
        the row to FAILED (block_kind NULL, completed_at set — FAILED is terminal,
        unlike the ESCALATED template) ONLY if it still matches
        (expected_status, expected_block_kind). Returns True iff it transitioned.

        Per THR-033 Change A a NON-root task that hits the step budget must not
        escalate directly to the founder — it fails and hands back to its parent
        (bounded failure-recovery carries it up). The CAS is required for the same
        reason as ``try_escalate_over_budget``: the budget guard runs BEFORE
        try_claim_for_step, so it has no upstream CAS. Two duplicate queue
        deliveries can both read the same stale at-cap eligible row; the
        conditional WHERE makes only the first writer win, so the parent enqueue +
        thread followup fire exactly once. A /cancel landing in the window moves
        the row out of the expected pre-state and the CAS rejects it for free.
        """
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind IS NULL""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind = ?""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1

    def _has_live_manager_supersession_family_work_uncommitted(
        self, task_id: str,
    ) -> bool:
        """Return whether a supersession family retains live task/job work."""
        return self._conn.execute(
            """WITH RECURSIVE family(id) AS (
                   SELECT ?
                   UNION ALL
                   SELECT t.id FROM tasks t JOIN family f ON t.parent_task_id = f.id
               )
               SELECT 1 FROM tasks
                WHERE id IN family AND id != ?
                  AND status NOT IN ('completed', 'failed', 'cancelled', 'superseded')
               UNION ALL
               SELECT 1 FROM jobs
                WHERE task_id IN family AND status IN ('pending', 'running')
               LIMIT 1""",
            (task_id, task_id),
        ).fetchone() is not None

    @_synchronized
    def try_fail_nonroot_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        note: str,
    ) -> bool:
        """Fail one still-current non-root supersede claim atomically.

        Non-root identity is an explicit predicate, not an interpretation of
        ``try_manager_supersede`` returning ``None``.  A cancelled, replaced,
        blocked, terminal, root, or otherwise non-current row is untouched.
        The caller owns the ordinary FAILED terminal tail and parent handoff
        only after this transition succeeds.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """UPDATE tasks
                  SET status = ?, block_kind = NULL, note = ?, completed_at = ?,
                      updated_at = ?, active_chain = NULL, active_fanout = NULL
                WHERE id = ? AND parent_task_id IS NOT NULL
                  AND status = ? AND block_kind IS NULL
                  AND cancelled_at IS NULL AND task_type = 'task'
                  AND assigned_agent = ? AND team = ?
                  AND current_session_id = ?""",
            (
                TaskStatus.FAILED.value,
                note,
                now,
                now,
                task_id,
                TaskStatus.IN_PROGRESS.value,
                actor_agent,
                expected_team,
                actor_session_id,
            ),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def try_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        successor_brief: str,
        rationale: str,
        attestation: dict[str, object],
    ) -> str | None:
        """Atomically replace one eligible claimed root with a pending successor.

        This intentionally has no generic target/actor override: callers supply
        only the server-derived current claim and the two decision fields.  A
        false return has no write side effects; exceptions roll the entire
        operation back.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None or (
                row["status"] != TaskStatus.IN_PROGRESS.value
                or row["block_kind"] is not None
                or row["cancelled_at"] is not None
                or row["task_type"] != "task"
                or row["parent_task_id"] is not None
                or row["assigned_agent"] != actor_agent
                or row["team"] != expected_team
                or row["current_session_id"] != actor_session_id
                or row["active_chain"] is not None
                or row["active_fanout"] is not None
                or row["blocked_on_job_ids"] is not None
                or row["dispatched_from_thread_id"] not in (None, "")
            ):
                self._conn.rollback()
                return None
            if self._has_live_manager_supersession_family_work_uncommitted(task_id):
                self._conn.rollback()
                return None
            predecessor = row["id"]
            prior = self._conn.execute(
                "SELECT original_root_task_id FROM manager_supersessions WHERE successor_task_id = ?",
                (predecessor,),
            ).fetchone()
            original_root = prior["original_root_task_id"] if prior else predecessor
            if self._conn.execute(
                "SELECT 1 FROM manager_supersessions WHERE original_root_task_id = ? LIMIT 1",
                (original_root,),
            ).fetchone() is not None:
                self._conn.rollback()
                return None
            successor_id = self.next_task_id()
            now = datetime.now(timezone.utc).isoformat()
            predecessor_brief = row["brief"]
            predecessor_hash = hashlib.sha256(predecessor_brief.encode()).hexdigest()
            successor_hash = hashlib.sha256(successor_brief.encode()).hexdigest()
            attestation_evidence = {
                "rule_version": "manager_supersession_attestation.v1",
                "actor_agent": actor_agent,
                "actor_session_id": actor_session_id,
                "attestation": attestation,
            }
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief, task_type,
                       revision_count, created_at, updated_at, parent_task_id,
                       orchestration_step_count, session_timeout_seconds)
                   VALUES (?, 'pending', ?, ?, ?, 'task', 0, ?, ?, NULL, 0, ?)""",
                (successor_id, actor_agent, row["team"], successor_brief, now, now,
                 row["session_timeout_seconds"]),
            )
            self._conn.execute(
                """INSERT INTO manager_supersessions
                   (predecessor_task_id, successor_task_id, original_root_task_id,
                    actor_agent, actor_session_id, rationale, attestation_evidence, predecessor_brief,
                    successor_brief, predecessor_brief_sha256, successor_brief_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (predecessor, successor_id, original_root, actor_agent, actor_session_id,
                 rationale, json.dumps(attestation_evidence, sort_keys=True), predecessor_brief, successor_brief, predecessor_hash,
                 successor_hash, now),
            )
            self._conn.execute(
                """UPDATE tasks SET status = 'superseded', block_kind = NULL,
                       blocked_on_job_ids = NULL, active_chain = NULL, active_fanout = NULL,
                       note = ?, completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'in_progress' AND block_kind IS NULL
                     AND current_session_id = ?""",
                (f"manager-superseded by {successor_id}", now, now, predecessor,
                 actor_session_id),
            )
            if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("supersession claim became stale")
            payload = {
                "original_root_task_id": original_root,
                "actor_session_id": actor_session_id,
                "rationale": rationale,
                "attestation_evidence": attestation_evidence,
                "predecessor_brief_sha256": predecessor_hash,
                "successor_brief_sha256": successor_hash,
            }
            for audit_task_id, counterpart_task_id in (
                (predecessor, successor_id), (successor_id, predecessor),
            ):
                audit_payload = {**payload, "counterpart_task_id": counterpart_task_id}
                self._conn.execute(
                    "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (audit_task_id, actor_agent, "manager_supersession", json.dumps(audit_payload), now),
                )
            self._conn.commit()
            return successor_id
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def try_reject_thread_origin_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        reason: str,
        reason_code: str,
    ) -> bool:
        """Atomically escalate one currently claimed, thread-origin manager root.

        This is the ineligible counterpart to ``try_manager_supersede``.  Its
        complete claim predicate is evaluated under the same transaction as
        the state and audit writes, so a competing continuation, block, or
        cancellation wins without any rejection side effects.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if self._has_live_manager_supersession_family_work_uncommitted(task_id):
                self._conn.rollback()
                return False
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ?
                     AND status = 'in_progress'
                     AND block_kind IS NULL
                     AND cancelled_at IS NULL
                     AND task_type = 'task'
                     AND parent_task_id IS NULL
                     AND assigned_agent = ?
                     AND team = ?
                     AND current_session_id = ?
                     AND active_chain IS NULL
                     AND active_fanout IS NULL
                     AND blocked_on_job_ids IS NULL
                     AND dispatched_from_thread_id IS NOT NULL
                     AND dispatched_from_thread_id != ''""",
                (
                    TaskStatus.ESCALATED.value, reason, now, task_id,
                    actor_agent, expected_team, actor_session_id,
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            escalation_audit_id = self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=actor_agent,
                action="escalation",
                payload={"reason": reason},
            )
            self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=actor_agent,
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
    def try_delegate(
        self, parent_id: str, child: TaskRecord, *, parent_note: str,
        attachments: list[dict] | None = None,
        active_chain_json: str | None = None,
        uploaded_by: str = "orchestrator",
        expected_claim: RetryClaim,
        revision_delta: int = 0,
        revision_cap: int = 0,
    ) -> SpawnOutcome:
        """Commit the child, attachments, chain and ordinary revision atomically.

        BEGIN IMMEDIATE under RLock precedes original claim, callback binding,
        lineage and worker-of-record rereads. LostClaim takes precedence over
        InvalidLineage. Neither refusal writes; Committed alone permits enqueue.
        All actual write/serialization errors roll back and propagate, preserving
        prior callback results, decisions and parent state.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            refusal = self._retry_spawn_check(
                parent_id, [child], expected_claim,
                revision_delta, revision_cap, ordinary=True,
            )
            if refusal is not None:
                self._conn.rollback()
                return refusal
            now = now_ts = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
                   current_session_id, zombie_flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    child.id,
                    child.status.value,
                    child.assigned_agent,
                    child.team,
                    child.brief,
                    child.revision_count,
                    child.created_at.isoformat(),
                    child.updated_at.isoformat(),
                    child.completed_at.isoformat() if child.completed_at else None,
                    child.parent_task_id,
                    child.revisit_of_task_id,
                    child.dispatched_from_thread_id,
                    child.block_kind.value if child.block_kind else None,
                    child.note,
                    child.orchestration_step_count,
                    child.session_timeout_seconds,
                    child.task_type,
                    child.active_fanout,
                    child.current_session_id,
                    child.zombie_flagged_at.isoformat() if child.zombie_flagged_at else None,
                ),
            )
            if attachments:
                self._insert_task_attachments_txn(
                    child.id, attachments, uploaded_by,
                )
            # Write active_chain within the same transaction so a crash or
            # write failure rolls back child + parent + chain atomically.
            if active_chain_json is not None:
                self._conn.execute(
                    "UPDATE tasks SET active_chain = ? WHERE id = ?",
                    (active_chain_json, parent_id),
                )
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = ?, note = ?, updated_at = ?, revision_count = revision_count + ?
                   WHERE id = ?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
                 now, revision_delta, parent_id),
            )
            self._conn.commit()
            return Committed((child.id,))
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def try_advance_chain(
        self,
        parent_id: str,
        active_chain_json: str,
        next_child: "TaskRecord",
        *,
        attachments: list[dict] | None = None,
        uploaded_by: str = "orchestrator",
    ) -> bool:
        """Atomically update parent active_chain + insert next child +
        attachment links/audit in a single explicit transaction.

        Replaces the prior two-step pattern (update_task_active_chain +
        insert_task_with_attachments) with a single transaction so a crash
        or child/link/audit write failure rolls back the chain advance.

        Returns True on success (parent chain advanced, child exists,
        attachments linked). Returns False and rolls back on any failure.
        """
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "UPDATE tasks SET active_chain = ? WHERE id = ?",
                (active_chain_json, parent_id),
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
                    next_child.id,
                    next_child.status.value,
                    next_child.assigned_agent,
                    next_child.team,
                    next_child.brief,
                    next_child.revision_count,
                    next_child.created_at.isoformat(),
                    next_child.updated_at.isoformat(),
                    next_child.completed_at.isoformat() if next_child.completed_at else None,
                    next_child.parent_task_id,
                    next_child.revisit_of_task_id,
                    next_child.dispatched_from_thread_id,
                    next_child.block_kind.value if next_child.block_kind else None,
                    next_child.note,
                    next_child.orchestration_step_count,
                    next_child.session_timeout_seconds,
                    next_child.task_type,
                    next_child.active_fanout,
                    next_child.current_session_id,
                    next_child.zombie_flagged_at.isoformat() if next_child.zombie_flagged_at else None,
                ),
            )
            if attachments:
                self._insert_task_attachments_txn(
                    next_child.id, attachments, uploaded_by,
                )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    @_synchronized
    def increment_revision_count(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET revision_count = revision_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()

    @_synchronized
    def next_task_id(self) -> str:
        # MAX(numeric_suffix) over TASK-NNN-shaped rows. Robust to gaps and
        # foreign-shape rows that a COUNT(*)-based allocator would mis-count
        # and then collide with on the next insert.
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 6) AS INTEGER)) AS m "
            "FROM tasks WHERE id GLOB 'TASK-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"TASK-{n:03d}"

    @_synchronized
    def get_nonterminal_task_ids(self) -> list[str]:
        # Path B: blocked dropped (no live row is `blocked` after the boot
        # migration); escalated added so the restart sweep visits escalated
        # rows to leave them alone (§B Branch 5). cancelled is terminal →
        # excluded.
        nonterminal = (
            TaskStatus.PENDING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.ESCALATED.value,
        )
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_blocked_with_kind(self, kind) -> list[str]:
        """Return IDs of parked tasks with the given block_kind.

        Queries by in_progress + block_kind — the stored Path-B representation.
        """
        kind_value = kind.value if hasattr(kind, "value") else kind
        cursor = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = 'in_progress' AND block_kind = ?",
            (kind_value,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_tasks_blocked_on_jobs(self) -> list[str]:
        """Return ids of tasks currently parked waiting on jobs (BLOCKED_ON_JOB).

        Used by startup recovery (spec §5.7) to re-evaluate the predicate after
        `recover_orphaned_running_jobs` force-fails any leftovers.
        """
        rows = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = ? AND block_kind = ?",
            (TaskStatus.IN_PROGRESS.value, BlockKind.BLOCKED_ON_JOB.value),
        ).fetchall()
        return [row["id"] for row in rows]

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
    def claim_task_completion_recovery(
        self, *, task_id: str, agent: str, origin_session_id: str,
        recovery_session_id: str, provider_session_id: str,
        claimed_at: str, expires_at: str,
    ) -> bool:
        """Durably spend the sole THR-247 recovery opportunity.

        This is intentionally a database transaction rather than a tracker
        lock: completion callbacks arrive on the event loop while the task
        runner executes on a worker thread.  A pre-existing exact origin
        result wins; after this claim an origin callback is no longer
        admissible and only the fresh recovery binding may report.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            task = self._conn.execute(
                "SELECT status, cancelled_at, assigned_agent, current_session_id "
                "FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or task["cancelled_at"] is not None or task["status"] != TaskStatus.IN_PROGRESS.value \
                    or task["assigned_agent"] != agent or task["current_session_id"] != origin_session_id:
                self._conn.rollback()
                return False
            existing = self._conn.execute(
                "SELECT 1 FROM task_results WHERE task_id = ? AND agent = ? AND session_id = ?",
                (task_id, agent, origin_session_id),
            ).fetchone()
            if existing is not None:
                self._conn.rollback()
                return False
            prior = self._conn.execute(
                "SELECT 1 FROM task_completion_recoveries "
                "WHERE task_id = ? AND agent = ? AND origin_session_id = ?",
                (task_id, agent, origin_session_id),
            ).fetchone()
            live_episode = self._conn.execute(
                "SELECT 1 FROM task_completion_recoveries "
                "WHERE task_id = ? AND agent = ? AND state = 'claimed'",
                (task_id, agent),
            ).fetchone()
            if prior is not None or live_episode is not None:
                self._conn.rollback()
                return False
            self._conn.execute(
                """INSERT INTO task_completion_recoveries
                   (task_id, agent, origin_session_id, recovery_session_id,
                    provider_session_id, claimed_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (task_id, agent, origin_session_id, recovery_session_id,
                 provider_session_id, claimed_at, expires_at),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def publish_task_completion_recovery_binding(
        self, *, task_id: str, agent: str, origin_session_id: str,
        recovery_session_id: str,
    ) -> bool:
        """Publish a claimed recovery only while its origin still owns the task."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                """UPDATE tasks SET assigned_agent = ?, current_session_id = ?
                   WHERE id = ? AND status = ? AND cancelled_at IS NULL
                     AND assigned_agent = ? AND current_session_id = ?
                     AND EXISTS (
                       SELECT 1 FROM task_completion_recoveries
                       WHERE task_id = ? AND agent = ?
                         AND origin_session_id = ?
                         AND recovery_session_id = ? AND state = 'claimed'
                     )""",
                (agent, recovery_session_id, task_id, TaskStatus.IN_PROGRESS.value,
                 agent, origin_session_id, task_id, agent, origin_session_id,
                 recovery_session_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def task_completion_recovery_launch_allowed(
        self, *, task_id: str, agent: str, recovery_session_id: str,
    ) -> bool:
        """Whether a claimed recovery still owns the durable launch binding.

        This is deliberately a fresh, synchronous launch-time check rather
        than an earlier claim/publication observation.  It is called from the
        executor's per-attempt pre-launch seam (and the supervisor's matching
        pre-prepare seam), after any preparation/admission wait.  Cancellation
        and ordinary newer-generation publication therefore make a stale
        recovery fail closed before it can create a new subprocess.
        """
        row = self._conn.execute(
            """SELECT 1
               FROM tasks AS t
               JOIN task_completion_recoveries AS r
                 ON r.task_id = t.id AND r.agent = ?
               WHERE t.id = ? AND t.assigned_agent = ?
                 AND t.current_session_id = ?
                 AND t.status = ? AND t.cancelled_at IS NULL
                 AND r.recovery_session_id = ? AND r.state = 'claimed'""",
            (agent, task_id, agent, recovery_session_id,
             TaskStatus.IN_PROGRESS.value, recovery_session_id),
        ).fetchone()
        return row is not None

    @_synchronized
    def set_task_executor_pid_if_current(
        self, *, task_id: str, agent: str, session_id: str, pid: int,
    ) -> bool:
        """Publish a PID only for the still-current invocation generation."""
        cursor = self._conn.execute(
            """UPDATE tasks SET executor_pid = ?
               WHERE id = ? AND assigned_agent = ? AND current_session_id = ?
                 AND status = ? AND cancelled_at IS NULL""",
            (pid, task_id, agent, session_id, TaskStatus.IN_PROGRESS.value),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def completion_recovery_callback_allowed(
        self, *, task_id: str, agent: str, session_id: str, now: str,
    ) -> bool:
        """Return whether a callback session remains the atomic winner.

        The caller holds the org route lock while it validates the payload and
        immediately persists it.  The durable ledger prevents an old origin
        callback from overtaking a spent recovery claim and closes admission at
        the persisted absolute deadline.
        """
        rows = self._conn.execute(
            """SELECT origin_session_id, recovery_session_id, expires_at, state
               FROM task_completion_recoveries
               WHERE task_id = ? AND agent = ?""",
            (task_id, agent),
        ).fetchall()
        if not rows:
            return True
        # Every historical origin/recovery binding remains fenced.  A fresh
        # ordinary generation is allowed once no episode is still claimed.
        for row in rows:
            if session_id == row["origin_session_id"]:
                return False
            if session_id == row["recovery_session_id"]:
                return row["state"] == "claimed" and now < row["expires_at"]
        return not any(row["state"] == "claimed" for row in rows)

    @_synchronized
    def mark_task_completion_recovery_callback_consumed(
        self, *, task_id: str, agent: str, session_id: str, result_row_id: int,
        settled_at: str,
    ) -> bool:
        """Spend only the still-current exact manager recovery receipt."""
        cursor = self._conn.execute(
            """UPDATE task_completion_recoveries
               SET state = 'callback_consumed', accepted_result_session_id = ?,
                   settled_at = ?
               WHERE task_id = ? AND agent = ? AND recovery_session_id = ?
                 AND accepted_result_id=? AND state = 'callback_accepted'
                 AND EXISTS (
                     SELECT 1 FROM tasks WHERE id=? AND assigned_agent=?
                       AND current_session_id=? AND cancelled_at IS NULL AND status=?
                 )""",
            (session_id, settled_at, task_id, agent, session_id, result_row_id,
             task_id, agent, session_id, TaskStatus.COMPLETED.value),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def reconcile_accepted_recovery_continued_same_root(
        self, *, task_id: str, agent: str, session_id: str, result_row_id: int,
        completion_payload: dict, settled_at: str,
    ) -> bool:
        """Settle a recovery receipt after its exact authority continuation.

        The authority continuation is already a committed, fenced transaction.
        This narrow recovery consumer only recognizes its immutable causal
        result/candidate/envelope tuple; it never evaluates policy or infers a
        continuation from ``pending`` alone.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            current = self._conn.execute(
                """SELECT status, assigned_agent, current_session_id, cancelled_at
                   FROM tasks WHERE id=?""", (task_id,),
            ).fetchone()
            continuation = self._conn.execute(
                """SELECT 1 FROM authority_candidates c
                   JOIN authority_continue_envelopes e ON e.candidate_id=c.id
                   WHERE c.root_task_id=? AND c.manager_session_id=?
                     AND c.causal_event_id=? AND c.lifecycle_state='consumed'
                     AND e.root_task_id=? AND e.manager_agent=?
                     AND e.manager_session_id=?""",
                (task_id, session_id, f"result:{result_row_id}", task_id, agent, session_id),
            ).fetchone()
            if not (
                current is not None and current["status"] == TaskStatus.PENDING.value
                and current["assigned_agent"] == agent
                and current["current_session_id"] == session_id
                and current["cancelled_at"] is None and continuation is not None
            ):
                self._conn.rollback()
                return False
            receipt = self._conn.execute(
                """SELECT 1 FROM audit_log WHERE task_id=? AND agent=?
                   AND action='completion_report'
                     AND json_extract(payload, '$._recovery_session_id')=?
                     AND json_extract(payload, '$._result_row_id')=?""",
                (task_id, agent, session_id, result_row_id),
            ).fetchone()
            if receipt is None:
                self.insert_audit_log_uncommitted(
                    task_id, agent, "completion_report", completion_payload,
                )
            marker = self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_consumed', accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (session_id, settled_at, task_id, agent, session_id, result_row_id),
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
    def complete_task_if_current_recovery_owner(
        self, *, task_id: str, agent: str, session_id: str, note: str,
        output_dir: str | None, completed_at: str, result_row_id: int,
    ) -> bool:
        """Complete only the still-current accepted recovery generation.

        This is deliberately a final effect CAS, rather than an entry check:
        another generation may replace the recovery owner while ordinary
        manager parsing/auditing is in progress.
        """
        cursor = self._conn.execute(
            """UPDATE tasks SET status=?, block_kind=NULL, note=?,
               final_output_dir=?, completed_at=?
               WHERE id=? AND assigned_agent=? AND current_session_id=?
                 AND cancelled_at IS NULL AND status=?
                 AND EXISTS (
                     SELECT 1 FROM task_completion_recoveries
                     WHERE task_id=? AND agent=? AND recovery_session_id=?
                       AND accepted_result_id=? AND state='callback_accepted'
                 )""",
            (TaskStatus.COMPLETED.value, note, output_dir, completed_at,
             task_id, agent, session_id, TaskStatus.IN_PROGRESS.value,
             task_id, agent, session_id, result_row_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def consume_accepted_blocked_task_completion_recovery(
        self, *, task_id: str, agent: str, session_id: str, result_row_id: int,
        blocked_on_job_ids: list[str], note: str, completion_payload: dict,
        settled_at: str,
    ) -> bool:
        """Atomically apply the accepted blocked-recovery durable effects.

        ``AuditLogger`` and ``update_task`` normally commit independently.
        That is correct for ordinary reports, but an accepted recovery callback
        needs one durable receipt boundary: completion audit, parked carrier,
        blocked audit, and ledger consumption either all persist or all roll
        back.  Queue delivery remains deliberately outside this transaction;
        startup can reconstruct it from the parked carrier.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            recovery = self._conn.execute(
                """SELECT 1 FROM task_completion_recoveries
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (task_id, agent, session_id, result_row_id),
            ).fetchone()
            task = self._conn.execute(
                """SELECT 1 FROM tasks WHERE id=? AND assigned_agent=?
                   AND current_session_id=? AND cancelled_at IS NULL
                   AND status=?""",
                (task_id, agent, session_id, TaskStatus.IN_PROGRESS.value),
            ).fetchone()
            if recovery is None or task is None:
                self._conn.rollback()
                return False
            self.insert_audit_log_uncommitted(
                task_id, agent, "completion_report", completion_payload,
            )
            self._conn.execute(
                """UPDATE tasks SET status=?, block_kind=?, blocked_on_job_ids=?, note=?
                   WHERE id=?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.BLOCKED_ON_JOB.value,
                 json.dumps(blocked_on_job_ids), note, task_id),
            )
            self.insert_audit_log_uncommitted(
                task_id, agent, "task_blocked_on_jobs",
                {
                    "agent": agent,
                    "blocking_job_ids": blocked_on_job_ids,
                    "output_summary_excerpt": (note or "")[:200],
                },
            )
            self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_consumed', accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (session_id, settled_at, task_id, agent, session_id, result_row_id),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def consume_accepted_completed_task_completion_recovery(
        self, *, task_id: str, agent: str, session_id: str, result_row_id: int,
        note: str, output_dir: str | None, completion_payload: dict, settled_at: str,
        reviewer: str | None, verdict: str | None,
    ) -> bool:
        """Atomically apply the durable completed-recovery receipt.

        This is intentionally the small recovery-only counterpart of the
        blocked carrier transaction: the completion audit, delegated verdict,
        terminal task row, and exact accepted ledger marker must never be
        independently durable.  ``verdict`` is derived from this immutable
        accepted completion result, never a task-wide latest-result lookup.
        Post-commit in-memory delivery/cleanup remains reconstructible by the
        ordinary startup machinery.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            recovery = self._conn.execute(
                """SELECT 1 FROM task_completion_recoveries
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (task_id, agent, session_id, result_row_id),
            ).fetchone()
            task = self._conn.execute(
                """SELECT 1 FROM tasks WHERE id=? AND assigned_agent=?
                   AND current_session_id=? AND cancelled_at IS NULL
                   AND status=?""",
                (task_id, agent, session_id, TaskStatus.IN_PROGRESS.value),
            ).fetchone()
            if recovery is None or task is None:
                self._conn.rollback()
                return False
            self.insert_audit_log_uncommitted(task_id, agent, "completion_report", completion_payload)
            if reviewer is not None and verdict is not None:
                self.insert_audit_log_uncommitted(
                    task_id, reviewer, "review_verdict", {
                        "verdict": verdict,
                        "feedback": note,
                        "reviewed_agent": agent,
                    },
                )
            self._conn.execute(
                """UPDATE tasks SET status=?, block_kind=NULL, note=?, final_output_dir=?,
                   completed_at=? WHERE id=?""",
                (TaskStatus.COMPLETED.value, note, output_dir, settled_at, task_id),
            )
            self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_consumed', accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (session_id, settled_at, task_id, agent, session_id, result_row_id),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def consume_accepted_nonroot_escalation_recovery(
        self, *, task_id: str, agent: str, session_id: str, result_row_id: int,
        note: str, completion_payload: dict, settled_at: str,
    ) -> bool:
        """Fail a non-root manager escalation with its exact recovery receipt."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            recovery = self._conn.execute(
                """SELECT 1 FROM task_completion_recoveries
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (task_id, agent, session_id, result_row_id),
            ).fetchone()
            task = self._conn.execute(
                """SELECT 1 FROM tasks WHERE id=? AND assigned_agent=?
                   AND current_session_id=? AND cancelled_at IS NULL
                   AND status=? AND parent_task_id IS NOT NULL""",
                (task_id, agent, session_id, TaskStatus.IN_PROGRESS.value),
            ).fetchone()
            if recovery is None or task is None:
                self._conn.rollback()
                return False
            self.insert_audit_log_uncommitted(task_id, agent, "completion_report", completion_payload)
            self._conn.execute(
                """UPDATE tasks SET status=?, block_kind=NULL, note=?, completed_at=?
                   WHERE id=?""",
                (TaskStatus.FAILED.value, note, settled_at, task_id),
            )
            marker = self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='callback_consumed', accepted_result_session_id=?, settled_at=?
                   WHERE task_id=? AND agent=? AND recovery_session_id=?
                     AND accepted_result_id=? AND state='callback_accepted'""",
                (session_id, settled_at, task_id, agent, session_id, result_row_id),
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
    def get_consumed_completed_task_completion_recovery_task_ids(self) -> list[str]:
        """Return only exact-current-owner completed recovery receipts.

        The startup cleanup consumer acts on a terminal task's jobs.  A
        historical consumed receipt is therefore not enough: its immutable
        accepted result, recovery generation, and current task owner must all
        still agree.  Otherwise a cancelled or reassigned task could cause an
        old recovery receipt to touch a newer winner's jobs.
        """
        rows = self._conn.execute(
            """SELECT r.task_id
               FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id = r.accepted_result_id
               JOIN tasks AS t ON t.id = r.task_id
               WHERE r.state='callback_consumed'
                 AND t.status=? AND t.task_type IN ('subtask', 'task')
                 AND t.cancelled_at IS NULL
                 AND tr.task_id = r.task_id AND tr.agent = r.agent
                 AND tr.session_id = r.recovery_session_id
                 AND t.assigned_agent = r.agent
                 AND t.current_session_id = r.recovery_session_id
               ORDER BY r.id""",
            (TaskStatus.COMPLETED.value,),
        ).fetchall()
        return [row["task_id"] for row in rows]

    @_synchronized
    def get_consumed_nonroot_escalation_recovery_task_ids(self) -> list[str]:
        """Return exact-current-owner non-root manager recovery receipts.

        These rows are terminal only after the recovery transaction commits.
        Startup reconstructs their owned-job cleanup and parent wake; it does
        not re-run the ordinary manager authority path.
        """
        rows = self._conn.execute(
            """SELECT r.task_id
               FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id = r.accepted_result_id
               JOIN tasks AS t ON t.id = r.task_id
               WHERE r.state='callback_consumed'
                 AND t.status=? AND t.task_type='task'
                 AND t.parent_task_id IS NOT NULL AND t.cancelled_at IS NULL
                 AND tr.task_id=r.task_id AND tr.agent=r.agent
                 AND tr.session_id=r.recovery_session_id
                 AND t.assigned_agent=r.agent
                 AND t.current_session_id=r.recovery_session_id
               ORDER BY r.id""",
            (TaskStatus.FAILED.value,),
        ).fetchall()
        return [row["task_id"] for row in rows]

    @_synchronized
    def get_consumed_task_completion_recovery_owners(self) -> list[dict]:
        """Snapshot terminal recovery owners for startup-owned effects.

        IDs alone are insufficient once the selector releases its lock: a
        newer generation can replace the task binding before startup reaches
        cleanup.  Keep the immutable result and recovery-session fingerprint
        so every post-selection effect can revalidate it independently.
        """
        rows = self._conn.execute(
            """SELECT r.task_id, r.agent, r.recovery_session_id,
                      r.accepted_result_id, t.status, t.parent_task_id
               FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id=r.accepted_result_id
               JOIN tasks AS t ON t.id=r.task_id
               WHERE r.state='callback_consumed'
                 AND t.cancelled_at IS NULL
                 AND ((t.status=? AND t.task_type IN ('subtask', 'task'))
                      OR (t.status=? AND t.task_type='task'
                          AND t.parent_task_id IS NOT NULL))
                 AND tr.task_id=r.task_id AND tr.agent=r.agent
                 AND tr.session_id=r.recovery_session_id
                 AND t.assigned_agent=r.agent
                 AND t.current_session_id=r.recovery_session_id
               ORDER BY r.id""",
            (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def consumed_task_completion_recovery_owner_is_current(
        self, *, task_id: str, agent: str, recovery_session_id: str,
        result_row_id: int, terminal_status: str,
    ) -> bool:
        """Revalidate a selected owner at a startup effect boundary.

        This is deliberately separate from a job UPDATE: zero running rows is
        a valid no-op, not evidence that the selected receipt lost ownership.
        """
        if terminal_status not in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            return False
        row = self._conn.execute(
            """SELECT 1 FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id=r.accepted_result_id
               JOIN tasks AS t ON t.id=r.task_id
               WHERE r.task_id=? AND r.agent=? AND r.recovery_session_id=?
                 AND r.accepted_result_id=?
                 AND r.state='callback_consumed'
                 AND t.status=?
                 AND t.cancelled_at IS NULL
                 AND tr.task_id=r.task_id AND tr.agent=r.agent
                 AND tr.session_id=r.recovery_session_id
                 AND t.assigned_agent=r.agent
                 AND t.current_session_id=r.recovery_session_id""",
            (task_id, agent, recovery_session_id, result_row_id,
             terminal_status),
        ).fetchone()
        return row is not None

    @_synchronized
    def settle_expired_task_completion_recovery(
        self, *, task_id: str, agent: str, session_id: str, settled_at: str,
    ) -> bool:
        """Spend an unaccepted episode once its persisted deadline has passed.

        This is deliberately narrower than later startup/process cleanup.  It
        only arbitrates the durable callback winner: an accepted callback has
        already changed state and wins; otherwise settlement fences the
        recovery binding permanently.  It never changes the immutable result
        row or makes the origin eligible again.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state = 'expired', settled_at = ?
                   WHERE task_id = ? AND agent = ? AND recovery_session_id = ?
                     AND state = 'claimed' AND expires_at <= ?""",
                (settled_at, task_id, agent, session_id, settled_at),
            )
            self._conn.commit()
            return cursor.rowcount == 1
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def settle_interrupted_task_completion_recovery(
        self, *, task_id: str, agent: str, settled_at: str, note: str,
    ) -> bool:
        """Fail closed an unaccepted recovery found after daemon restart.

        This is one transaction because the startup process has no trustworthy
        in-memory containment control.  It deliberately does not inspect or
        signal ``executor_pid``: a recovery PID can be unpublished or
        recycled.  An accepted callback, cancellation, or a newer binding
        wins by making the guarded update match no row.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            recovery = self._conn.execute(
                """SELECT origin_session_id, recovery_session_id
                   FROM task_completion_recoveries
                   WHERE task_id=? AND agent=? AND state='claimed'""",
                (task_id, agent),
            ).fetchone()
            if recovery is None:
                self._conn.rollback()
                return False
            cursor = self._conn.execute(
                """UPDATE tasks SET status=?, block_kind=NULL, note=?, completed_at=?
                   WHERE id=? AND assigned_agent=? AND status=?
                     AND cancelled_at IS NULL
                     AND current_session_id IN (?, ?)""",
                (TaskStatus.FAILED.value, note, settled_at, task_id, agent,
                 TaskStatus.IN_PROGRESS.value, recovery["origin_session_id"],
                 recovery["recovery_session_id"]),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            self._conn.execute(
                """UPDATE task_completion_recoveries
                   SET state='restart_settled', settled_at=?
                   WHERE task_id=? AND agent=? AND state='claimed'""",
                (settled_at, task_id, agent),
            )
            # A restart may interrupt the ordinary fire-and-forget runner
            # cleanup after this terminal transition.  Reconcile only durable
            # rows for this terminal task here; without a live owned control,
            # row bookkeeping is safe but signalling a persisted PID is not.
            self._conn.execute(
                """UPDATE jobs
                   SET status='failed', reason='task_ended', finished_at=?,
                       duration_ms=COALESCE(duration_ms, 0)
                   WHERE task_id=? AND status='running'""",
                (settled_at, task_id),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def get_claimed_task_completion_recovery(self, *, task_id: str, agent: str) -> dict | None:
        """Return the sole unaccepted episode, if any, for restart routing."""
        row = self._conn.execute(
            """SELECT * FROM task_completion_recoveries
               WHERE task_id=? AND agent=? AND state='claimed'""",
            (task_id, agent),
        ).fetchone()
        return dict(row) if row is not None else None

    @_synchronized
    def get_accepted_task_completion_recovery_result(
        self, *, task_id: str, agent: str,
    ) -> dict | None:
        """Return the ledger-selected recovery result, never a session latest row.

        Recovery callback admission records the immutable ``task_results.id`` in
        the same transaction as ``callback_accepted``.  Consumers must use that
        identity: a prior or later result from the same task is not a substitute.
        """
        row = self._conn.execute(
            """SELECT tr.*
               FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id = r.accepted_result_id
               JOIN tasks AS t ON t.id = r.task_id
               WHERE r.task_id = ? AND r.agent = ?
                 AND r.state = 'callback_accepted'
                 AND tr.task_id = r.task_id AND tr.agent = r.agent
                 AND tr.session_id = r.recovery_session_id
                 AND t.assigned_agent = r.agent
                 AND t.current_session_id = r.recovery_session_id
               ORDER BY r.id DESC LIMIT 1""",
            (task_id, agent),
        ).fetchone()
        return dict(row) if row is not None else None

    @_synchronized
    def get_accepted_task_completion_recovery_task_ids(self) -> list[str]:
        """Return exact-owner accepted recovery rows, including terminal tasks.

        A crash after the completion effects commit can make the task terminal
        before the recovery marker commits.  Those rows are intentionally not
        returned by the ordinary nonterminal startup iterator, but must be
        reconciled through the same exact ledger/result binding.
        """
        rows = self._conn.execute(
            """SELECT r.task_id
               FROM task_completion_recoveries AS r
               JOIN task_results AS tr ON tr.id = r.accepted_result_id
               JOIN tasks AS t ON t.id = r.task_id
               WHERE r.state = 'callback_accepted'
                 AND tr.task_id = r.task_id AND tr.agent = r.agent
                 AND tr.session_id = r.recovery_session_id
                 AND t.assigned_agent = r.agent
                 AND t.current_session_id = r.recovery_session_id
                 AND t.cancelled_at IS NULL
               ORDER BY r.id"""
        ).fetchall()
        return [row["task_id"] for row in rows]

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

    def _authenticate_v2_attempt_admission_uncommitted(
        self, *, task_id: str, agent: str, session_id: str, admission: dict,
    ) -> bool:
        """Match the supplied v2 admission against the durable launch binding."""
        required = (
            "team", "binding_id", "contract_id", "contract_version",
            "contract_digest", "release_id", "activation_id", "activation_epoch",
            "selector_id", "assessment_digest", "assessment_canonical_json",
            "origin_boot_id",
        )
        if any(admission.get(key) in (None, "") for key in required):
            return False
        if len(str(admission["assessment_canonical_json"]).encode("utf-8")) > 65536:
            return False
        binding = self.get_authority_policy_v2_session_binding(
            root_task_id=task_id, manager_agent=agent, manager_session_id=session_id,
        )
        if binding is None:
            return False
        return (
            binding.binding_id == admission["binding_id"]
            and binding.team == admission["team"]
            and binding.contract_id == admission["contract_id"]
            and binding.contract_version == admission["contract_version"]
            and binding.contract_digest == admission["contract_digest"]
            and binding.release_id == admission["release_id"]
            and binding.activation_id == admission["activation_id"]
            and binding.activation_epoch == admission["activation_epoch"]
            and binding.selector_id == admission["selector_id"]
        )

    def _authenticate_v2_attempt_admission_audit_uncommitted(
        self, attempt_row: dict,
    ) -> bool:
        """Require exactly one closed admission-stage audit for one attempt.

        The immutable ``authority_policy_v2_attempts`` row is only authoritative
        admitted evidence together with its write-once
        ``authority_policy_v2_result_stage`` audit.  A deleted, duplicated,
        mutated or mismatched audit is missing evidence and refuses: the caller
        must not allocate, repair, or repair-by-reinsert anything.  The audit's
        admission-stage snapshot is compared against the immutable attempt
        identity/reference columns plus its own frozen stage/finalization
        markers, so a later legitimate finalization of the mutable attempt row
        does not invalidate the admission record.

        C3b stage scoping: this authenticates the **admitted** event only,
        inside its own stage.  Later legitimate stage events on the same
        attempt (``claim_audited``) add result-stage rows but never invalidate
        the single immutable ``a0`` admission event.
        """
        return self._authenticate_v2_attempt_stage_audit_uncommitted(
            attempt_row, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
            require_unfinalized=True,
        )

    def _authenticate_v2_result_stage_audit_uncommitted(
        self, attempt_row: dict, stage: str, *, candidate_id: str | None = None,
        require_finalization: bool = False,
    ) -> bool:
        """Require exactly one authentic CLOSED result-stage event for ``stage``.

        BOTH halves of every required prior stage are authenticated: this is the
        ``audit_log``/``authority_policy_v2_result_stage`` half (its sibling
        candidate-audit half is checked by
        ``_authenticate_v2_candidate_audit_uncommitted``).  The event must match
        the immutable attempt identity on every field, carry EXACTLY the closed
        payload key set for its stage (the later stages additionally carry the
        candidate identity), and — where the stage persists it — the required
        ``unfinalized`` finalization marker.  A missing, duplicated, mutated,
        foreign-candidate or malformed/extra-key event refuses.  Events
        belonging to a different stage are ignored, so a later legitimate stage
        never invalidates an earlier one and a not-yet-created stage is never
        required.
        """
        root_task_id = attempt_row["root_task_id"]
        manager_agent = attempt_row["manager_agent"]
        attempt_id = attempt_row["attempt_id"]
        try:
            candidates = [
                row for row in self.get_audit_logs(root_task_id)
                if row.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
                and row.get("agent") == manager_agent
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_id
                and row["payload"].get("stage") == stage
            ]
        except Exception:
            return False
        if len(candidates) != 1:
            return False
        payload = candidates[0]["payload"]
        expected = {
            "stage": stage,
            "attempt_id": attempt_id,
            "result_id": attempt_row["result_id"],
            "binding_id": attempt_row["binding_id"],
            "contract_id": attempt_row["contract_id"],
            "contract_version": attempt_row["contract_version"],
            "contract_digest": attempt_row["contract_digest"],
            "release_id": attempt_row["release_id"],
            "activation_id": attempt_row["activation_id"],
            "activation_epoch": attempt_row["activation_epoch"],
            "selector_id": attempt_row["selector_id"],
            "assessment_digest": attempt_row["assessment_digest"],
            "owner_attempt_id": attempt_row["owner_attempt_id"],
            "origin_boot_id": attempt_row["origin_boot_id"],
        }
        if candidate_id is not None:
            expected["candidate_id"] = candidate_id
        if require_finalization:
            expected["finalization_state"] = "unfinalized"
        if set(payload.keys()) != set(expected.keys()):
            return False
        return all(payload.get(key) == value for key, value in expected.items())

    def _authenticate_v2_attempt_stage_audit_uncommitted(
        self, attempt_row: dict, stage: str, *, require_unfinalized: bool = False,
    ) -> bool:
        """Wrapper over the closed result-stage authentication for ``stage``.

        Every needed later stage is authenticated independently; a missing,
        duplicated, mutated, foreign-candidate or closed-key/malformed event for
        the requested stage refuses.  Events belonging to a different stage are
        ignored.
        """
        return self._authenticate_v2_result_stage_audit_uncommitted(
            attempt_row, stage, require_finalization=require_unfinalized,
        )

    def _authenticate_v2_prior_result_stages_uncommitted(
        self, attempt_row: dict, stages: tuple[str, ...], candidate_id: str,
    ) -> bool:
        """Require the result-stage half of every named prior stage."""
        return all(
            self._authenticate_v2_result_stage_audit_uncommitted(
                attempt_row, stage, candidate_id=candidate_id,
                require_finalization=True,
            )
            for stage in stages
        )

    def _insert_authority_policy_v2_attempt_uncommitted(
        self, *, task_id: str, agent: str, session_id: str, result_id: int,
        admission: dict, now: str,
    ) -> AuthorityPolicyV2Attempt:
        """Insert one admitted attempt row plus its admission audit.

        Runs inside the callback admission transaction.  The deterministic
        attempt ID and the randomly allocated ``owner_attempt_id`` are fixed by
        this winning insert; no second result/attempt/audit is ever allocated
        for the same exact tuple.
        """
        attempt_id = authority_policy_v2_attempt_id(
            manager_agent=agent, manager_session_id=session_id,
            result_id=result_id, root_task_id=task_id, team=admission["team"],
        )
        attempt = AuthorityPolicyV2Attempt(
            attempt_id=attempt_id,
            team=admission["team"],
            root_task_id=task_id,
            manager_agent=agent,
            manager_session_id=session_id,
            result_id=result_id,
            binding_id=admission["binding_id"],
            contract_id=admission["contract_id"],
            contract_version=admission["contract_version"],
            contract_digest=admission["contract_digest"],
            release_id=admission["release_id"],
            activation_id=admission["activation_id"],
            activation_epoch=admission["activation_epoch"],
            selector_id=admission["selector_id"],
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
            finalization_state="unfinalized",
            refusal_code=None,
            origin_boot_id=admission["origin_boot_id"],
            owner_attempt_id=str(uuid.uuid4()),
            assessment_digest=admission["assessment_digest"],
        )
        snapshot = attempt.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_attempts
               (attempt_id, team, root_task_id, manager_agent, manager_session_id,
                result_id, binding_id, contract_id, contract_version, contract_digest,
                release_id, activation_id, activation_epoch, selector_id, stage,
                finalization_state, refusal_code, origin_boot_id, owner_attempt_id,
                assessment_digest, canonical_payload_json, created_at)
               VALUES (:attempt_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:result_id,:binding_id,:contract_id,
                       :contract_version,:contract_digest,:release_id,:activation_id,
                       :activation_epoch,:selector_id,:stage,:finalization_state,
                       :refusal_code,:origin_boot_id,:owner_attempt_id,
                       :assessment_digest,:canonical_payload_json,:created_at)""",
            {
                **snapshot,
                "attempt_id": attempt_id,
                "finalization_state": "unfinalized",
                "refusal_code": None,
                "owner_attempt_id": attempt.owner_attempt_id,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    snapshot
                ).decode("utf-8"),
            },
        )
        self.insert_audit_log_uncommitted(
            task_id, agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                "attempt_id": attempt_id,
                "result_id": result_id,
                "binding_id": admission["binding_id"],
                "contract_id": admission["contract_id"],
                "contract_version": admission["contract_version"],
                "contract_digest": admission["contract_digest"],
                "release_id": admission["release_id"],
                "activation_id": admission["activation_id"],
                "activation_epoch": admission["activation_epoch"],
                "selector_id": admission["selector_id"],
                "assessment_digest": admission["assessment_digest"],
                "owner_attempt_id": attempt.owner_attempt_id,
                "origin_boot_id": admission["origin_boot_id"],
                "finalization_state": "unfinalized",
            },
        )
        return attempt

    @_synchronized
    def get_authority_policy_v2_attempt_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Attempt | None:
        """Authenticated read of the admitted attempt bound to one result.

        Returns ``None`` when the attempt row is absent/corrupt or its unique
        matching admission-stage audit is missing, duplicated, mutated or
        mismatched — a partial attempt without its admitted audit is never a
        usable authenticated read.
        """
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return None
        return attempt

    @_synchronized
    def get_authority_policy_v2_attempt(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2Attempt | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return None
        return attempt

    def _authority_policy_v2_attempt_from_row(self, row) -> AuthorityPolicyV2Attempt:
        try:
            attempt = AuthorityPolicyV2Attempt.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 attempt has a corrupt canonical payload") from exc
        if attempt.attempt_id != row["attempt_id"]:
            raise ValueError("authority v2 attempt identity mismatch")
        for column, value in attempt.model_dump(mode="json").items():
            if column == "finalization_state":
                continue
            if row[column] != value:
                raise ValueError("authority v2 attempt column/preimage mismatch")
        if row["finalization_state"] != attempt.finalization_state:
            raise ValueError("authority v2 attempt finalization mismatch")
        return attempt

    @_synchronized
    def list_authority_policy_v2_result_stage_audits(
        self, *, root_task_id: str, manager_agent: str,
    ) -> list[dict]:
        """Return the closed admission-stage audit rows for one root/agent."""
        return [
            row for row in self.get_audit_logs(root_task_id)
            if row["action"] == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and row.get("agent") == manager_agent
        ]

    # -- THR-229 checkpoint C3b: durable v2 candidate/pin claim and the
    # separate claim-audit stage.  The Database owns synchronization, the
    # BEGIN IMMEDIATE boundary and every write; the store is a thin forwarder.
    # This checkpoint deliberately implements NO refusal housekeeping and NO
    # evaluation/consume/envelope/finalization: a refusal returns only a
    # bounded disposition for the later consumer.

    def bind_authority_policy_v2_permission_surface_reader(self, reader) -> None:
        """Bind the server-side permission reader ``reader(agent) -> digest``.

        The orchestration seam (the store the orchestrator constructs) supplies
        a callable that reads the live org permission surface; it is never a
        caller-supplied allow/deny boolean or a precomputed digest.  Unbinding
        (``None``) makes every claim refuse fail-closed.
        """
        self._v2_permission_surface_reader = reader

    def bind_authority_policy_v2_process_boot_id(self, boot_id: str | None) -> None:
        """Bind the trusted current daemon-process identity for housekeeping.

        This is the existing server-owned process context (the same per-daemon
        ``authority_v2_origin_boot_id`` recorded on admitted attempts), never a
        caller-supplied allow boolean.  When unbound, an attempt whose origin
        daemon boot may be gone cannot be safely attributed without the live
        owner token or a recorded failed-stage obligation; housekeeping then
        returns bounded ``housekeeping_pending`` without mutating task/Q/J.
        """
        self._v2_process_boot_id = boot_id

    def _forget_v2_live_owner(self, attempt_id: str, owner_attempt_id: str) -> None:
        if self._v2_live_attempt_owners.get(attempt_id) == owner_attempt_id:
            self._v2_live_attempt_owners.pop(attempt_id, None)

    def _mark_v2_refusal_failure_authority(
        self, attempt_id: str, owner_attempt_id: str,
    ) -> None:
        """Record that the authentic owner's refusal transaction failed.

        This is set only after the live-owner token proved the caller was the
        uninterrupted authentic owner, so a malformed/foreign/nested/duplicate
        contender can never poison a valid winner.  Claim/evaluate/consume/audit
        advancement is already prohibited because the live token was forgotten;
        this marker exists solely so safely attributable housekeeping may still
        retry when the durable obligation could not be persisted.
        """
        self._v2_refusal_failed_owners[attempt_id] = owner_attempt_id

    def _clear_v2_refusal_failure_authority(
        self, attempt_id: str, owner_attempt_id: str | None = None,
    ) -> None:
        if owner_attempt_id is None or (
            self._v2_refusal_failed_owners.get(attempt_id) == owner_attempt_id
        ):
            self._v2_refusal_failed_owners.pop(attempt_id, None)

    def _v2_refusal_failure_authority(
        self, *, attempt_id: str, owner_attempt_id: str | None,
    ) -> bool:
        """True only for the exact owner whose refusal transaction failed."""
        return (
            owner_attempt_id is not None
            and self._v2_refusal_failed_owners.get(attempt_id) == owner_attempt_id
        )

    def _v2_contender_is_authentic_owner(
        self, *, attempt_id: str, owner_attempt_id: str, origin_boot_id: str,
    ) -> bool:
        """True only when this caller presents the real uninterrupted owner proof.

        The registered in-memory token must match AND the durable attempt's
        ``owner_attempt_id``/``origin_boot_id`` must equal the caller's values.
        Persisted UUID strings are readable identity markers, not proof, so a
        wrong-boot / wrong-token / wrong-tuple contender is classified as
        unauthorized and never changes the original owner's liveness.
        """
        if self._v2_live_attempt_owners.get(attempt_id) != owner_attempt_id:
            return False
        try:
            row = self._conn.execute(
                """SELECT owner_attempt_id, origin_boot_id
                     FROM authority_policy_v2_attempts WHERE attempt_id=?""",
                (attempt_id,),
            ).fetchone()
        except Exception:
            return False
        if row is None:
            return False
        return (
            row["owner_attempt_id"] == owner_attempt_id
            and row["origin_boot_id"] == origin_boot_id
        )

    def _refuse_v2_stage(
        self, *, attempt_id: str, owner_attempt_id: str, code: str,
        origin_boot_id: str | None = None,
        candidate_id: str | None = None, claim_key: str | None = None,
        stage: str | None = None, poison: bool | None = None,
    ) -> AuthorityPolicyV2StageOutcome:
        """Return a bounded refusal, classifying liveness before poisoning.

        ``poison=None`` auto-classifies: only the authentic continuing owner
        (registered live token AND matching durable owner/boot) is forgotten, so
        an unauthorized/stale/duplicate contender cannot poison the winner.
        ``poison=True`` forces the owned-stage failure residue; ``poison=False``
        never forgets the owner (transaction-nesting and duplicate refusals).
        """
        if poison is None:
            forget = (
                origin_boot_id is not None
                and self._v2_contender_is_authentic_owner(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    origin_boot_id=origin_boot_id,
                )
            )
        else:
            forget = poison
        if forget:
            # THR-229 C3d1: record the durable server-owned failed-stage
            # obligation BEFORE forgetting the process-local token, so a
            # genuine failed-stage request remains safely attributable for
            # refusal housekeeping after the winner is poisoned.  Best-effort:
            # a storage failure here must never mask the primary refusal, and an
            # exhausted/unwritable store promises no new durable diagnostic.
            self._record_v2_failed_stage_obligation(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code=code,
            )
            self._forget_v2_live_owner(attempt_id, owner_attempt_id)
        return AuthorityPolicyV2StageOutcome(
            status="refused", refusal_code=code, attempt_id=attempt_id,
            candidate_id=candidate_id, claim_key=claim_key, stage=stage,
        )

    def _record_v2_failed_stage_obligation(
        self, *, attempt_id: str, owner_attempt_id: str, code: str,
    ) -> None:
        """Persist the closed failed-stage housekeeping obligation (best effort).

        Only a server-owned attempt whose ``origin_boot_id`` equals the bound
        current daemon-process identity can produce an obligation, so the record
        is trusted context rather than a caller-supplied boolean.  It is written
        only for a closed refusal code on an unfinalized attempt, and never
        fabricates task/receipt ownership.
        """
        # Stage writers expose a more precise closed refusal vocabulary than
        # terminal housekeeping. Persist the same lossy mapping consumed by
        # run_authority_hook before the live owner is forgotten; otherwise a
        # schema/evidence refusal leaves no durable authority for the later
        # finalizer and the task remains a recoverable zombie forever.
        housekeeping_code = (
            _AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING.get(
                code, "interrupted_pre_final",
            )
        )
        if housekeeping_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            return
        if self._v2_process_boot_id is None:
            return
        try:
            if self._conn.in_transaction:
                return
            row = self._conn.execute(
                """SELECT root_task_id, manager_agent, result_id, origin_boot_id,
                          owner_attempt_id, finalization_state
                     FROM authority_policy_v2_attempts WHERE attempt_id=?""",
                (attempt_id,),
            ).fetchone()
            if row is None or row["finalization_state"] != "unfinalized":
                return
            if row["origin_boot_id"] != self._v2_process_boot_id:
                return
            if row["owner_attempt_id"] != owner_attempt_id:
                return
            existing = self._conn.execute(
                """SELECT 1 FROM audit_log
                   WHERE action=? AND task_id=? AND agent=?
                     AND json_extract(payload,'$.attempt_id')=?""",
                (
                    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
                    row["root_task_id"], row["manager_agent"], attempt_id,
                ),
            ).fetchone()
            if existing is not None:
                # Exactly one obligation authorizes housekeeping; a repeated
                # failure must never create a second row (which the
                # cardinality-1 authenticator would reject).
                return
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row["root_task_id"], row["manager_agent"],
                    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
                    json.dumps({
                        "attempt_id": attempt_id,
                        "result_id": row["result_id"],
                        "refusal_code": housekeeping_code,
                        "origin_boot_id": row["origin_boot_id"],
                        "owner_attempt_id": row["owner_attempt_id"],
                    }),
                    _now().isoformat(),
                ),
            )
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def _authenticate_v2_result_body_uncommitted(
        self, result_row, attempt: AuthorityPolicyV2Attempt,
    ) -> bool:
        """Authenticate the persisted result's normalized assessment body AND
        the actual persisted manager decision it carries.

        CRD is only the row-identity digest; the persisted decision carrier's
        ``_manager_self_evaluation`` value is re-canonicalized here and must
        hash to the admitted ``assessment_digest``.  SEPARATELY, the persisted
        manager decision's ``action`` must be the accepted root escalation
        action: eligible v2 authority evaluation is for the accepted root
        escalation decision only, so a missing/malformed/non-escalate decision
        (an ordinary ``delegate``/``supersede``/``done`` etc., or an unchanged
        assessment whose decision was drifted between stages) can never acquire
        the v2 continuation path.  This inspects persisted decision DATA only —
        no prose, clause or sentinel detector is introduced and ordinary manager
        validation is untouched.
        """
        raw = result_row["decision_json"]
        if not isinstance(raw, str) or not raw:
            return False
        try:
            parsed = json.loads(raw)
        except Exception:
            return False
        if not isinstance(parsed, dict):
            return False
        if parsed.get("action") != AUTHORITY_POLICY_V2_ESCALATION_DECISION_ACTION:
            return False
        carrier = parsed.get("_manager_self_evaluation")
        if not isinstance(carrier, dict):
            return False
        try:
            return authority_policy_v2_sha256(carrier) == attempt.assessment_digest
        except Exception:
            return False

    def _advance_v2_attempt_stage_uncommitted(
        self, attempt: AuthorityPolicyV2Attempt, new_stage: str,
    ) -> None:
        """Advance the mutable stage/payload pair in ONE consistent update."""
        expected = AUTHORITY_POLICY_V2_ATTEMPT_STAGE_TRANSITIONS.get(attempt.stage)
        if expected != new_stage:
            raise ValueError("illegal v2 attempt stage transition")
        snapshot = attempt.model_dump(mode="json")
        snapshot["stage"] = new_stage
        self._conn.execute(
            """UPDATE authority_policy_v2_attempts
                  SET stage=?, canonical_payload_json=?
                WHERE attempt_id=?""",
            (
                new_stage,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                attempt.attempt_id,
            ),
        )

    def _authority_policy_v2_candidate_from_row(self, row) -> AuthorityPolicyV2Candidate:
        try:
            candidate = AuthorityPolicyV2Candidate.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 candidate has a corrupt canonical payload") from exc
        if candidate.candidate_id != row["candidate_id"]:
            raise ValueError("authority v2 candidate identity mismatch")
        for column, value in candidate.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 candidate column/preimage mismatch")
        return candidate

    def _authority_policy_v2_pin_from_row(self, row) -> AuthorityPolicyV2Pin:
        try:
            pin = AuthorityPolicyV2Pin.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 pin has a corrupt canonical payload") from exc
        if pin.candidate_id != row["candidate_id"]:
            raise ValueError("authority v2 pin identity mismatch")
        for column, value in pin.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 pin column/preimage mismatch")
        return pin

    def _retained_v2_mechanical_eligibility_uncommitted(
        self, task, *, max_revise_rounds: int,
    ) -> str | None:
        """Re-derive retained mechanical eligibility from persisted state.

        Used at EVERY v2 stage boundary (claim and all later pre-final
        boundaries): root-only, revisit/successor lineage, active chain/fanout,
        blocked job and the CURRENTLY-configured revise ceiling against the
        persisted ``tasks.revision_count``.  No caller boolean is trusted and no
        v1 adverse-review/partial-work/raw-DDL clause is a v2 veto.
        """
        if task["parent_task_id"] is not None or task["revisit_of_task_id"]:
            return "claim_failed"
        if (
            task["active_chain"]
            or task["active_fanout"]
            or task["blocked_on_job_ids"]
        ):
            return "claim_failed"
        successor = self._conn.execute(
            "SELECT 1 FROM manager_supersessions WHERE successor_task_id=? LIMIT 1",
            (task["id"],),
        ).fetchone()
        if successor is not None:
            return "claim_failed"
        if max_revise_rounds > 0 and int(task["revision_count"] or 0) >= max_revise_rounds:
            return "claim_failed"
        return None

    def _authority_policy_v2_attempt_id_for_identity(
        self, *, root_task_id: str, manager_agent: str,
        manager_session_id: str, result_id: int,
    ) -> str:
        """Derive an attempt identity from durable bound team evidence only.

        Existing attempt rows win. Before an attempt exists, the immutable v2
        session binding supplies the team; the persisted task team is the
        final pre-admission source. If none exists, the bounded refusal ID uses
        a null team member rather than inventing or defaulting a team.
        """
        row = self._conn.execute(
            """SELECT attempt_id FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is not None:
            return str(row["attempt_id"])
        binding = self._conn.execute(
            """SELECT team FROM authority_policy_v2_session_bindings
               WHERE root_task_id=? AND manager_agent=? AND manager_session_id=?""",
            (root_task_id, manager_agent, manager_session_id),
        ).fetchone()
        task = self._conn.execute(
            "SELECT team FROM tasks WHERE id=?", (root_task_id,),
        ).fetchone()
        team = binding["team"] if binding is not None else (
            task["team"] if task is not None else None
        )
        if isinstance(team, str) and team:
            return authority_policy_v2_attempt_id(
                manager_agent=manager_agent,
                manager_session_id=manager_session_id,
                result_id=result_id,
                root_task_id=root_task_id,
                team=team,
            )
        return "APV2R-" + authority_policy_v2_sha256({
            "manager_agent": manager_agent,
            "manager_session_id": manager_session_id,
            "result_id": result_id,
            "root_task_id": root_task_id,
            "team": None,
        })

    def _authenticate_v2_claim_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> tuple[str | None, dict | None]:
        """Re-read and authenticate the complete claim-stage evidence.

        Shared by BOTH the first (claim) and second (claim-audit) boundaries so
        the second boundary re-authenticates the causal result row/body, the
        immutable launch binding, the authenticated pinned
        release/activation/selector prefix, the single ``a0`` admitted audit and
        the current task ownership/cancellation — rather than trusting the
        already-persisted K/P row.  It also re-derives the retained mechanical
        eligibility from the persisted task at every boundary.  Returns
        ``(refusal_code, None)`` on any mismatch/mutation/deletion, or
        ``(None, ctx)`` with the authenticated values.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return "identity_mismatch", None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return "identity_mismatch", None
        if attempt.finalization_state != "unfinalized":
            return "owner_lost", None
        # Uninterrupted live-owner proof: the in-memory winning token, the
        # persisted random owner marker and the daemon-process boot UUID must
        # all agree.  Persisted UUID strings alone are never proof.
        if self._v2_live_attempt_owners.get(attempt.attempt_id) != owner_attempt_id:
            return "owner_lost", None
        if (
            owner_attempt_id != attempt.owner_attempt_id
            or origin_boot_id != attempt.origin_boot_id
        ):
            return "owner_lost", None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return "identity_mismatch", None

        task = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        if task is None:
            return "identity_mismatch", None
        if task["cancelled_at"] is not None or task["status"] in {
            "completed", "failed", "cancelled", "superseded",
        }:
            return "cancelled", None
        if task["status"] != "in_progress" or task["block_kind"] is not None:
            return "cancelled", None
        if (
            task["assigned_agent"] != manager_agent
            or task["current_session_id"] != manager_session_id
        ):
            return "owner_lost", None

        # THR-229 C3c correction: retained current mechanical eligibility is
        # re-derived from the authenticated task at EVERY stage boundary, not
        # only at claim.  Valid-at-claim is insufficient: a chain/fanout/blocked
        # job/revisit/successor/root change or a currently-exhausted configured
        # revise ceiling refuses here with the exact prior residue retained.
        eligibility = self._retained_v2_mechanical_eligibility_uncommitted(
            task, max_revise_rounds=max_revise_rounds,
        )
        if eligibility is not None:
            return eligibility, None

        result_row = self._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (result_id,)
        ).fetchone()
        if (
            result_row is None
            or result_row["task_id"] != root_task_id
            or result_row["agent"] != manager_agent
            or result_row["session_id"] != manager_session_id
        ):
            return "identity_mismatch", None
        if not self._authenticate_v2_result_body_uncommitted(result_row, attempt):
            return "identity_mismatch", None

        binding = self.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        )
        if binding is None:
            return "identity_mismatch", None
        try:
            self._authenticate_v2_session_binding_uncommitted(binding)
        except Exception:
            return "identity_mismatch", None
        if (
            binding.binding_id != attempt.binding_id
            or binding.team != attempt.team
            or binding.contract_id != attempt.contract_id
            or binding.contract_version != attempt.contract_version
            or binding.contract_digest != attempt.contract_digest
            or binding.release_id != attempt.release_id
            or binding.activation_id != attempt.activation_id
            or binding.activation_epoch != attempt.activation_epoch
            or binding.selector_id != attempt.selector_id
        ):
            return "identity_mismatch", None

        release = self.get_authority_policy_v2_release(attempt.release_id)
        activation = self.get_authority_policy_v2_activation(attempt.activation_id)
        if release is None or activation is None:
            return "identity_mismatch", None
        if (
            release.team != attempt.team
            or release.policy_digest != binding.policy_digest
            or release.version != binding.policy_version
            or activation.team != attempt.team
            or activation.release_id != attempt.release_id
            or activation.release_digest != release.policy_digest
            or activation.selector_epoch != attempt.activation_epoch
            or binding.provider_id == "" or binding.executor_kind == ""
            or binding.model_id == ""
        ):
            return "identity_mismatch", None
        return None, {
            "attempt": attempt, "row": row, "task": task,
            "result_row": result_row, "binding": binding,
            "release": release, "activation": activation,
        }

    def _claim_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int, now: str, schema_observation,
    ) -> AuthorityPolicyV2StageOutcome:
        from runtime.orchestrator.authority import (
            capture_authority_policy_v2_permission_surface,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
            )

        code, ctx = self._authenticate_v2_claim_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED:
            if attempt.stage in (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
            ):
                return _refused("already_claimed")
            return _refused("identity_mismatch")
        # The applicable mechanical eligibility predicates (revisit/successor
        # lineage, root-only, active chain/fanout, blocked job and the current
        # revise ceiling against the persisted revision_count) are re-derived
        # inside ``_authenticate_v2_claim_evidence_uncommitted``, which is
        # shared by every stage boundary.  No caller boolean and no
        # ``_server_fact_clause`` adverse-review/partial-work/raw-DDL clause is a
        # v2 veto or a phrase/clause unlock.

        # Freeze the read-only permission-surface evidence through the bound
        # server-side reader.  Captured after authentication (so an
        # unauthorized contender still refuses with its real code) but before
        # any K/P write; an unbound/read-failed/malformed read fails closed and
        # can never become a sentinel digest.
        permission = capture_authority_policy_v2_permission_surface(
            self, manager_agent,
        )
        if permission.evidence is None:
            return _refused("evidence_drift")

        claim_key = authority_policy_v2_sha256(
            authority_policy_v2_candidate_claim_preimage(
                activation_id=attempt.activation_id,
                activation_selector_epoch=attempt.activation_epoch,
                causal_result_digest=authority_policy_v2_causal_result_digest(result_id),
                causal_result_id=result_id,
                contract_digest=attempt.contract_digest,
                executor_kind=binding.executor_kind,
                manager_agent=manager_agent,
                manager_session_id=manager_session_id,
                model_id=binding.model_id,
                policy_digest=release.policy_digest,
                policy_version=release.version,
                provider_id=binding.provider_id,
                release_id=attempt.release_id,
                root_task_id=root_task_id,
                team=attempt.team,
            )
        )
        candidate = self._v2_candidate_from_claim_key(
            claim_key, attempt=attempt, binding=binding,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            schema_observation=schema_observation,
            permission_surface_digest=permission.evidence.digest,
        )
        return self._insert_v2_candidate_and_pin_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id,
            now=now,
        )

    def _v2_candidate_from_claim_key(
        self, claim_key: str, *, attempt: AuthorityPolicyV2Attempt,
        binding: AuthorityPolicyV2SessionBinding, origin_boot_id: str,
        owner_attempt_id: str, schema_observation, permission_surface_digest: str,
    ) -> AuthorityPolicyV2Candidate:
        return AuthorityPolicyV2Candidate(
            candidate_id=f"APV2C-{claim_key}",
            claim_key=claim_key,
            team=attempt.team,
            root_task_id=attempt.root_task_id,
            manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
            attempt_id=attempt.attempt_id,
            result_id=attempt.result_id,
            binding_id=attempt.binding_id,
            contract_id=attempt.contract_id,
            contract_version=attempt.contract_version,
            contract_digest=attempt.contract_digest,
            release_id=attempt.release_id,
            policy_version=binding.policy_version,
            policy_digest=binding.policy_digest,
            activation_id=attempt.activation_id,
            activation_epoch=attempt.activation_epoch,
            selector_id=attempt.selector_id,
            provider_id=binding.provider_id,
            executor_kind=binding.executor_kind,
            model_id=binding.model_id,
            causal_result_id=attempt.result_id,
            causal_result_digest=authority_policy_v2_causal_result_digest(
                attempt.result_id
            ),
            origin_boot_id=origin_boot_id,
            owner_attempt_id=owner_attempt_id,
            schema_raw_digest=schema_observation.raw_digest,
            schema_inventory_digest=schema_observation.inventory_digest,
            schema_object_count=schema_observation.object_count,
            permission_surface_digest=permission_surface_digest,
        )

    def _insert_v2_candidate_and_pin_uncommitted(
        self, *, candidate: AuthorityPolicyV2Candidate,
        attempt: AuthorityPolicyV2Attempt,
        binding: AuthorityPolicyV2SessionBinding, owner_attempt_id: str,
        origin_boot_id: str, now: str,
    ) -> AuthorityPolicyV2StageOutcome:
        candidate_snapshot = candidate.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidates
               (candidate_id, claim_key, team, root_task_id, manager_agent,
                manager_session_id, attempt_id, result_id, binding_id, contract_id,
                contract_version, contract_digest, release_id, policy_version,
                policy_digest, activation_id, activation_epoch, selector_id,
                provider_id, executor_kind, model_id, causal_result_id,
                causal_result_digest, origin_boot_id, owner_attempt_id,
                schema_raw_digest, schema_inventory_digest, schema_object_count,
                permission_surface_digest,
                lifecycle_stage, canonical_payload_json, created_at)
               VALUES (:candidate_id,:claim_key,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:attempt_id,:result_id,:binding_id,
                       :contract_id,:contract_version,:contract_digest,:release_id,
                       :policy_version,:policy_digest,:activation_id,
                       :activation_epoch,:selector_id,:provider_id,:executor_kind,
                       :model_id,:causal_result_id,:causal_result_digest,
                       :origin_boot_id,:owner_attempt_id,:schema_raw_digest,
                       :schema_inventory_digest,:schema_object_count,
                       :permission_surface_digest,:lifecycle_stage,
                       :canonical_payload_json,
                       :created_at)""",
            {
                **candidate_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    candidate_snapshot
                ).decode("utf-8"),
            },
        )
        pin = AuthorityPolicyV2Pin(
            candidate_id=candidate.candidate_id,
            claim_key=candidate.claim_key,
            team=candidate.team,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            attempt_id=candidate.attempt_id,
            result_id=candidate.result_id,
            binding_id=candidate.binding_id,
            release_id=candidate.release_id,
            activation_id=candidate.activation_id,
            activation_epoch=candidate.activation_epoch,
            selector_id=candidate.selector_id,
            policy_version=candidate.policy_version,
            policy_digest=candidate.policy_digest,
            contract_digest=candidate.contract_digest,
            provider_id=candidate.provider_id,
            executor_kind=candidate.executor_kind,
            model_id=candidate.model_id,
            schema_raw_digest=candidate.schema_raw_digest,
            schema_inventory_digest=candidate.schema_inventory_digest,
            schema_object_count=candidate.schema_object_count,
            permission_surface_digest=candidate.permission_surface_digest,
        )
        pin_snapshot = pin.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_pins
               (candidate_id, claim_key, team, root_task_id, manager_agent,
                manager_session_id, attempt_id, result_id, binding_id, release_id,
                activation_id, activation_epoch, selector_id, policy_version,
                policy_digest, contract_digest, provider_id, executor_kind,
                model_id, schema_raw_digest, schema_inventory_digest,
                schema_object_count, permission_surface_digest,
                canonical_payload_json, created_at)
               VALUES (:candidate_id,:claim_key,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:attempt_id,:result_id,:binding_id,
                       :release_id,:activation_id,:activation_epoch,:selector_id,
                       :policy_version,:policy_digest,:contract_digest,:provider_id,
                       :executor_kind,:model_id,:schema_raw_digest,
                       :schema_inventory_digest,:schema_object_count,
                       :permission_surface_digest,:canonical_payload_json,
                       :created_at)""",
            {
                **pin_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    pin_snapshot
                ).decode("utf-8"),
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="claimed", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
        )

    @_synchronized
    def claim_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """First C3b transaction: atomically create K+P and advance J to claimed.

        Real schema values are observed once while holding BEGIN IMMEDIATE and
        stored on K/P as diagnostics.  They are not compared with any reference
        and are never rechecked.  The permission surface remains independently
        captured and rechecked.  This transaction never appends the separate
        ``a1`` claim-stage audit.

        Transaction ownership: when the caller ALREADY owns a transaction this
        method refuses with ``transaction_owned`` BEFORE it would BEGIN,
        ROLLBACK or invalidate the live owner, so the caller's transaction and
        its work are left untouched.  Moving the two stage commits into the
        caller's transaction, or silently using a savepoint, would change the
        R4 durability meaning and is deliberately not done.
        """
        from runtime.orchestrator.authority import (
            capture_authority_policy_v2_schema_observation,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            observation = capture_authority_policy_v2_schema_observation(self)
            if observation is None:
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code="claim_failed",
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                    origin_boot_id=origin_boot_id,
                )
            outcome = self._claim_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                max_revise_rounds=max_revise_rounds, now=now,
                schema_observation=observation,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "claim_failed",
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_claimed", "already_audited",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="claim_failed", origin_boot_id=origin_boot_id,
            )
            raise

    def _authenticate_v2_candidate_pin_joins_uncommitted(
        self, *, attempt: AuthorityPolicyV2Attempt,
        candidate: AuthorityPolicyV2Candidate, pin: AuthorityPolicyV2Pin,
        binding, release,
    ) -> bool:
        """The frozen K/P cross-row joins against the authenticated J/binding/release.

        Shared by the pre-final claim/evaluation boundaries and the post-final
        authenticator so both accept exactly the same immutable candidate/pin
        tuple; a mixed or drifted K/P row never matches.
        """
        return not (
            candidate.team != attempt.team
            or candidate.root_task_id != attempt.root_task_id
            or candidate.manager_agent != attempt.manager_agent
            or candidate.manager_session_id != attempt.manager_session_id
            or candidate.attempt_id != attempt.attempt_id
            or candidate.result_id != attempt.result_id
            or candidate.binding_id != attempt.binding_id
            or candidate.contract_id != attempt.contract_id
            or candidate.contract_version != attempt.contract_version
            or candidate.contract_digest != attempt.contract_digest
            or candidate.release_id != attempt.release_id
            or candidate.activation_id != attempt.activation_id
            or candidate.activation_epoch != attempt.activation_epoch
            or candidate.selector_id != attempt.selector_id
            or candidate.policy_version != release.version
            or candidate.policy_digest != release.policy_digest
            or candidate.provider_id != binding.provider_id
            or candidate.executor_kind != binding.executor_kind
            or candidate.model_id != binding.model_id
            or candidate.origin_boot_id != attempt.origin_boot_id
            or candidate.owner_attempt_id != attempt.owner_attempt_id
            or pin.candidate_id != candidate.candidate_id
            or pin.claim_key != candidate.claim_key
            or pin.team != candidate.team
            or pin.root_task_id != candidate.root_task_id
            or pin.manager_agent != candidate.manager_agent
            or pin.manager_session_id != candidate.manager_session_id
            or pin.attempt_id != candidate.attempt_id
            or pin.result_id != candidate.result_id
            or pin.binding_id != candidate.binding_id
            or pin.release_id != candidate.release_id
            or pin.activation_id != candidate.activation_id
            or pin.activation_epoch != candidate.activation_epoch
            or pin.selector_id != candidate.selector_id
            or pin.policy_version != candidate.policy_version
            or pin.policy_digest != candidate.policy_digest
            or pin.contract_digest != candidate.contract_digest
            or pin.provider_id != candidate.provider_id
            or pin.executor_kind != candidate.executor_kind
            or pin.model_id != candidate.model_id
            or pin.permission_surface_digest != candidate.permission_surface_digest
        )

    def _authenticate_v2_candidate_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> tuple[str | None, dict | None]:
        """Re-read and authenticate the complete claim-stage K/P evidence.

        Shared by the claim-audit boundary and every C3c evaluation/consumption
        boundary: the causal result row/body, the immutable launch binding, the
        authenticated pinned release/activation/selector prefix, the single a0
        admitted audit, the current task ownership/cancellation, the retained
        current mechanical eligibility AND the full candidate/pin cross-row
        joins with the frozen claim-time permission evidence.  The schema
        values remain immutable observations but are never compared.  A
        between-stage mutation, deletion or mixed identity refuses; the
        already-persisted K/P row is never trusted on its own.
        """
        code, ctx = self._authenticate_v2_claim_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return code, None
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]

        candidate_row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidates
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if candidate_row is None:
            return "claim_audit_missing", None
        try:
            candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
        except ValueError:
            return "identity_mismatch", None
        pin_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if pin_row is None:
            return "claim_audit_missing", None
        try:
            pin = self._authority_policy_v2_pin_from_row(pin_row)
        except ValueError:
            return "identity_mismatch", None

        if not self._authenticate_v2_candidate_pin_joins_uncommitted(
            attempt=attempt, candidate=candidate, pin=pin, binding=binding,
            release=release,
        ):
            return "identity_mismatch", None

        from runtime.models import (
            AuthorityPolicyV2PermissionSurface as _PermissionSurface,
        )
        from runtime.orchestrator.authority import (
            V2_PERMISSION_SURFACE_CONTRACT,
            recheck_authority_policy_v2_permission_surface,
        )
        frozen_permission = _PermissionSurface(
            contract_version=V2_PERMISSION_SURFACE_CONTRACT,
            digest=candidate.permission_surface_digest,
        )
        if not recheck_authority_policy_v2_permission_surface(
            frozen_permission, self, manager_agent,
        ):
            return "evidence_drift", None
        return None, {**ctx, "candidate": candidate, "pin": pin}

    def _authenticate_v2_candidate_audit_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, event: str,
    ) -> bool:
        """Require exactly one authentic closed candidate-audit event.

        The event must carry the candidate/attempt/result/owner/boot identity
        and a canonical payload that re-validates to the same closed event.  A
        missing, duplicated, mutated or mismatched event refuses; a later
        legitimate event on the same candidate never invalidates an earlier one.
        """
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, event),
        ).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        try:
            audit = AuthorityPolicyV2CandidateAudit.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception:
            return False
        # Every closed event field must equal the persisted column (identity,
        # event, candidate, attempt/result/owner/boot AND the created_at
        # preimage), the stored payload must be the canonical bytes of exactly
        # that value, and the event must belong to THIS candidate/event.  A
        # mutated column, a foreign candidate/identity or a malformed/extra-key
        # payload refuses.
        snapshot = audit.model_dump(mode="json")
        for column, value in snapshot.items():
            if row[column] != value:
                return False
        try:
            canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        except Exception:
            return False
        if canonical != row["canonical_payload_json"]:
            return False
        if (
            audit.candidate_id != candidate.candidate_id
            or audit.event != event
            or audit.claim_key != candidate.claim_key
            or audit.attempt_id != candidate.attempt_id
            or audit.result_id != candidate.result_id
            or audit.owner_attempt_id != candidate.owner_attempt_id
            or audit.origin_boot_id != candidate.origin_boot_id
            or audit.team != candidate.team
            or audit.root_task_id != candidate.root_task_id
            or audit.manager_agent != candidate.manager_agent
            or audit.manager_session_id != candidate.manager_session_id
        ):
            return False
        return True

    def _insert_v2_candidate_audit_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, event: str, *,
        owner_attempt_id: str, origin_boot_id: str, now: str,
    ) -> None:
        """Append exactly one closed candidate-stage audit event (uncommitted)."""
        audit = AuthorityPolicyV2CandidateAudit(
            candidate_id=candidate.candidate_id,
            team=candidate.team,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            event=event,
            claim_key=candidate.claim_key,
            attempt_id=candidate.attempt_id,
            result_id=candidate.result_id,
            owner_attempt_id=owner_attempt_id,
            origin_boot_id=origin_boot_id,
        )
        audit_snapshot = audit.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidate_audit
               (candidate_id, team, root_task_id, manager_agent, manager_session_id,
                event, claim_key, attempt_id, result_id, owner_attempt_id,
                origin_boot_id, canonical_payload_json, created_at)
               VALUES (:candidate_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:event,:claim_key,:attempt_id,:result_id,
                       :owner_attempt_id,:origin_boot_id,:canonical_payload_json,
                       :created_at)""",
            {
                **audit_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    audit_snapshot
                ).decode("utf-8"),
            },
        )

    def _advance_v2_candidate_lifecycle_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, new_stage: str,
    ) -> None:
        """Advance the candidate lifecycle in ONE consistent update."""
        expected = AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_TRANSITIONS.get(
            candidate.lifecycle_stage
        )
        if expected != new_stage:
            raise ValueError("illegal v2 candidate lifecycle transition")
        snapshot = candidate.model_dump(mode="json")
        snapshot["lifecycle_stage"] = new_stage
        self._conn.execute(
            """UPDATE authority_policy_v2_candidates
                  SET lifecycle_stage=?, canonical_payload_json=?
                WHERE candidate_id=?""",
            (
                new_stage,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                candidate.candidate_id,
            ),
        )

    def _authority_policy_v2_evaluation_from_row(
        self, row,
    ) -> AuthorityPolicyV2Evaluation:
        try:
            evaluation = AuthorityPolicyV2Evaluation.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 evaluation has a corrupt canonical payload") from exc
        if evaluation.evaluation_id != row["evaluation_id"]:
            raise ValueError("authority v2 evaluation identity mismatch")
        for column, value in evaluation.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 evaluation column/preimage mismatch")
        return evaluation

    def _authenticate_v2_evaluation_evidence_uncommitted(
        self, *, candidate: AuthorityPolicyV2Candidate,
        attempt: AuthorityPolicyV2Attempt, binding, release,
    ) -> tuple[str | None, dict | None]:
        """Authenticate the stored V evidence WITHOUT re-deriving the outcome.

        Re-reads the immutable evaluation row and checks its canonical
        column/preimage consistency plus its full joins to the authenticated
        candidate/attempt/binding/release.  The outcome is never recomputed here.
        """
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if row is None:
            return "evaluation_missing", None
        try:
            evaluation = self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return "identity_mismatch", None
        if (
            evaluation.candidate_id != candidate.candidate_id
            or evaluation.claim_key != candidate.claim_key
            or evaluation.team != attempt.team
            or evaluation.root_task_id != candidate.root_task_id
            or evaluation.manager_agent != candidate.manager_agent
            or evaluation.manager_session_id != candidate.manager_session_id
            or evaluation.attempt_id != attempt.attempt_id
            or evaluation.result_id != candidate.result_id
            or evaluation.binding_id != attempt.binding_id
            or evaluation.release_id != candidate.release_id
            or evaluation.activation_id != attempt.activation_id
            or evaluation.activation_epoch != attempt.activation_epoch
            or evaluation.selector_id != candidate.selector_id
            or evaluation.policy_version != release.version
            or evaluation.policy_digest != release.policy_digest
            or evaluation.contract_digest != attempt.contract_digest
            or evaluation.provider_id != binding.provider_id
            or evaluation.executor_kind != binding.executor_kind
            or evaluation.model_id != binding.model_id
            or evaluation.assessment_digest != attempt.assessment_digest
        ):
            return "identity_mismatch", None
        return None, {"evaluation": evaluation}

    def _audit_authority_policy_v2_candidate_claim_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        # Re-authenticate the complete claim-stage evidence at the SECOND
        # boundary: the causal result row/body, the immutable launch binding,
        # the authenticated pinned release/activation/selector prefix, the
        # single a0 admitted audit, the current task ownership/cancellation AND
        # the full K/P cross-row joins, including the immutable claim-time
        # schema observation columns, plus the frozen permission evidence.
        # Permission evidence is rechecked; schema values are observed-only
        # and are never compared or rechecked.  The already-persisted K/P row
        # is NOT trusted on its own; a between-stage mutation/deletion/mixed
        # identity refuses without inventing evidence or advancing J.
        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        if attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED:
            return _refused("already_audited")
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED:
            return _refused("claim_audit_missing")

        existing_audit = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED),
        ).fetchone()
        if existing_audit is not None:
            return _refused("already_audited", candidate.candidate_id)

        audit = AuthorityPolicyV2CandidateAudit(
            candidate_id=candidate.candidate_id,
            team=candidate.team,
            root_task_id=root_task_id,
            manager_agent=manager_agent,
            manager_session_id=manager_session_id,
            event=AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
            claim_key=candidate.claim_key,
            attempt_id=attempt.attempt_id,
            result_id=result_id,
            owner_attempt_id=owner_attempt_id,
            origin_boot_id=origin_boot_id,
        )
        audit_snapshot = audit.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidate_audit
               (candidate_id, team, root_task_id, manager_agent, manager_session_id,
                event, claim_key, attempt_id, result_id, owner_attempt_id,
                origin_boot_id, canonical_payload_json, created_at)
               VALUES (:candidate_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:event,:claim_key,:attempt_id,:result_id,
                       :owner_attempt_id,:origin_boot_id,:canonical_payload_json,
                       :created_at)""",
            {
                **audit_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    audit_snapshot
                ).decode("utf-8"),
            },
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="claim_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_claim(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """Second C3b transaction: append the a1 claim event and audited stage.

        Re-authenticates the complete evidence (result row/body, immutable
        binding, authenticated pinned release/activation/selector prefix, K/P/J
        joins, prior a0, task ownership/cancellation), including the immutable
        claim-time schema observation values.  It rechecks only the frozen
        permission evidence; schema values are never compared or rechecked.
        Inserts exactly one candidate claim event plus the required
        ``claim_audited`` result-stage evidence and advances J to
        ``claim_audited`` atomically.  A failure preserves the claimed K/P.

        Transaction ownership: when the caller ALREADY owns a transaction this
        method refuses with ``transaction_owned`` BEFORE it would BEGIN,
        ROLLBACK or invalidate the live owner, so the caller's transaction and
        its work are left untouched.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_authority_policy_v2_candidate_claim_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "claim_audit_missing",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_claimed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="claim_audit_missing",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                origin_boot_id=origin_boot_id,
            )
            raise

    # -- THR-229 checkpoint C3c: the four pre-final evaluation and single
    # consumption stages.  Each public method owns exactly ONE BEGIN
    # IMMEDIATE/commit/rollback and re-authenticates the complete prior evidence
    # (result/body, immutable binding, pinned history, K/P, required a0+a1 and,
    # from consumption onward, the stored V and a2) rather than trusting the
    # already-persisted rows.  The pure outcome is derived once, in the
    # evaluation transaction only; audit/consumption authenticate the canonical
    # stored evidence and never re-derive it.

    def _evaluate_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        from runtime.models import AuthorityPolicyV2ManagerSelfEvaluation
        from runtime.orchestrator.authority_policy import (
            authority_policy_v2_persisted_assessment_outcome,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED:
            return _refused("already_evaluated", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED:
            if attempt.stage in (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
            ):
                return _refused("already_evaluated", candidate.candidate_id)
            return _refused("claim_audit_missing", candidate.candidate_id)
        # BOTH halves of the required prior stage: the candidate ``claimed``
        # event AND its sibling closed ``claim_audited`` result-stage audit.
        # Neither the audit created by this transaction nor any later/not-yet
        # created stage is required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,),
            candidate.candidate_id,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)

        try:
            parsed = json.loads(ctx["result_row"]["decision_json"])
        except Exception:
            return _refused("identity_mismatch", candidate.candidate_id)
        carrier = parsed.get("_manager_self_evaluation") if isinstance(parsed, dict) else None
        if not isinstance(carrier, dict):
            return _refused("identity_mismatch", candidate.candidate_id)

        what_json: str | None = None
        not_json: str | None = None
        if "_error_code" not in carrier:
            try:
                assessment = AuthorityPolicyV2ManagerSelfEvaluation.model_validate(carrier)
            except Exception:
                return _refused("identity_mismatch", candidate.candidate_id)
            if (
                assessment.root_task_id != root_task_id
                or assessment.manager_session_id != manager_session_id
                or assessment.release_id != attempt.release_id
                or assessment.policy_digest != release.policy_digest
                or assessment.policy_version != release.version
                or assessment.activation_id != attempt.activation_id
                or assessment.activation_epoch != attempt.activation_epoch
                or assessment.contract_id != attempt.contract_id
                or assessment.contract_version != attempt.contract_version
                or assessment.contract_digest != attempt.contract_digest
                or assessment.provider_id != binding.provider_id
                or assessment.executor_kind != binding.executor_kind
                or assessment.model_id != binding.model_id
            ):
                return _refused("identity_mismatch", candidate.candidate_id)
            what_json = authority_policy_v2_canonical_json_bytes(
                carrier["what_to_escalate"]
            ).decode("utf-8")
            not_json = authority_policy_v2_canonical_json_bytes(
                carrier["what_not_to_escalate"]
            ).decode("utf-8")

        # The pure accepted precedence is invoked exactly once, here.
        outcome, diagnostic = authority_policy_v2_persisted_assessment_outcome(carrier)
        evaluation = AuthorityPolicyV2Evaluation(
            evaluation_id=candidate.candidate_id,
            candidate_id=candidate.candidate_id,
            claim_key=candidate.claim_key,
            team=attempt.team,
            root_task_id=root_task_id,
            manager_agent=manager_agent,
            manager_session_id=manager_session_id,
            attempt_id=attempt.attempt_id,
            result_id=result_id,
            binding_id=attempt.binding_id,
            release_id=attempt.release_id,
            activation_id=attempt.activation_id,
            activation_epoch=attempt.activation_epoch,
            selector_id=attempt.selector_id,
            policy_version=release.version,
            policy_digest=release.policy_digest,
            contract_digest=attempt.contract_digest,
            provider_id=binding.provider_id,
            executor_kind=binding.executor_kind,
            model_id=binding.model_id,
            outcome=outcome.value,
            assessment_digest=attempt.assessment_digest,
            diagnostic_code=diagnostic,
            what_to_escalate_json=what_json,
            what_not_to_escalate_json=not_json,
        )
        evaluation_snapshot = evaluation.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_evaluations
               (evaluation_id, candidate_id, claim_key, team, root_task_id,
                manager_agent, manager_session_id, attempt_id, result_id,
                binding_id, release_id, activation_id, activation_epoch,
                selector_id, policy_version, policy_digest, contract_digest,
                provider_id, executor_kind, model_id, outcome, assessment_digest,
                diagnostic_code, what_to_escalate_json, what_not_to_escalate_json,
                canonical_payload_json, created_at)
               VALUES (:evaluation_id,:candidate_id,:claim_key,:team,:root_task_id,
                       :manager_agent,:manager_session_id,:attempt_id,:result_id,
                       :binding_id,:release_id,:activation_id,:activation_epoch,
                       :selector_id,:policy_version,:policy_digest,:contract_digest,
                       :provider_id,:executor_kind,:model_id,:outcome,
                       :assessment_digest,:diagnostic_code,:what_to_escalate_json,
                       :what_not_to_escalate_json,:canonical_payload_json,
                       :created_at)""",
            {
                **evaluation_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    evaluation_snapshot
                ).decode("utf-8"),
            },
        )
        self._advance_v2_candidate_lifecycle_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="evaluated", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
        )

    @_synchronized
    def evaluate_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c first transaction: persist one V, advance K/J to ``evaluated``.

        Derives the clause-free outcome exactly once from the persisted
        sanitized assessment; a missing/null/malformed/mismatched diagnostic
        carrier is retained honestly with the fail-closed ``invalid`` outcome.
        The task and recovery receipt are unchanged, and no envelope,
        notification or dispatch is created.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._evaluate_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "evaluation_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_evaluated", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="evaluation_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _audit_evaluation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if attempt.stage in (
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        ):
            return _refused("already_audited", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED:
            return _refused("evaluation_missing", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of the required prior stage: the candidate ``claimed``
        # event AND its closed ``claim_audited`` result-stage audit.  The
        # ``evaluation_audited`` audit created by THIS transaction is not
        # required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,),
            candidate.candidate_id,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        existing = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED),
        ).fetchone()
        if existing is not None:
            return _refused("already_audited", candidate.candidate_id)
        self._insert_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id, now=now,
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="evaluation_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_evaluation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c second transaction: append a2 plus the audited stage.

        Re-authenticates the complete prior evidence INCLUDING the stored V and
        a0+a1, then appends exactly one candidate ``evaluated`` event plus the
        ``evaluation_audited`` result-stage evidence and advances J.  It never
        re-derives the outcome to authenticate V.  A failure preserves V and the
        evaluated K.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_evaluation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "evaluation_audit_missing",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="evaluation_audit_missing",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _consume_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if (
            candidate.lifecycle_stage == AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED
            or attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED
        ):
            return _refused("already_consumed", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED:
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of every required prior stage: the candidate
        # ``claimed``/``evaluated`` events AND their closed ``claim_audited`` /
        # ``evaluation_audited`` result-stage audits.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        self._advance_v2_candidate_lifecycle_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="consumed", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
        )

    @_synchronized
    def consume_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c third transaction: CAS K/J to ``consumed`` exactly once.

        Re-authenticates the complete prior evidence and a0+a1+a2, requires the
        evaluated candidate and J ``evaluation_audited`` and consumes the
        persisted V with NO second model call and NO repeated pure derivation.
        It mints no authority and does not change the task.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED, poison=False,
            )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._consume_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "consume_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="consume_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _audit_consumption_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED:
            return _refused("already_audited", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED:
            return _refused("consume_failed", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of every required prior stage: the candidate
        # ``claimed``/``evaluated`` events AND their closed ``claim_audited`` /
        # ``evaluation_audited`` result-stage audits.  The ``consumed_audited``
        # audit created by THIS transaction is not required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        existing = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED),
        ).fetchone()
        if existing is not None:
            return _refused("already_audited", candidate.candidate_id)
        self._insert_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id, now=now,
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="consumed_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_consumption(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c fourth transaction: append a3 and advance J to consumed_audited.

        Re-authenticates the consumed K/P/V and a0+a1+a2, appends exactly one
        candidate ``consumed`` event plus the ``consumed_audited`` result-stage
        evidence and advances J in one transaction.  A failure preserves the
        earlier consumed K and V, leaves a3 absent and J consumed; the shipping
        hook remains fail-closed until the later finalization/refusal/recovery
        unit.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_consumption_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "consume_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="consume_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                origin_boot_id=origin_boot_id,
            )
            raise

    @_synchronized
    def get_authority_policy_v2_candidate(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Candidate | None:
        """Authenticated read of one persisted candidate (independent of claim)."""
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_candidate_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_candidate_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Candidate | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_candidate_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_pin(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Pin | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_pin_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def list_authority_policy_v2_candidate_audits(
        self, candidate_id: str,
    ) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? ORDER BY id""",
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_v2_candidate_audit(
        self, candidate_id: str, event: str,
    ) -> dict | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate_id, event),
        ).fetchone()
        return None if row is None else dict(row)

    # -- THR-229 checkpoint C3c: authenticated V reads.  They authenticate the
    # canonical stored column/preimage evidence only; they never re-derive the
    # outcome and never authorize a stage advance.

    @_synchronized
    def get_authority_policy_v2_evaluation(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Evaluation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_evaluation_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Evaluation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def list_authority_policy_v2_evaluations(
        self, *, root_task_id: str, manager_agent: str,
    ) -> list[AuthorityPolicyV2Evaluation]:
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_evaluations
               WHERE root_task_id=? AND manager_agent=?
               ORDER BY created_at, evaluation_id""",
            (root_task_id, manager_agent),
        ).fetchall()
        evaluations = []
        for row in rows:
            try:
                evaluations.append(self._authority_policy_v2_evaluation_from_row(row))
            except ValueError:
                continue
        return evaluations

    # -- THR-229 checkpoint C3d1: durable pre-final refusal and exact recovery
    # receipt housekeeping.  ONE Database-owned synchronized BEGIN IMMEDIATE
    # transaction over the exact attempt journal (J) / immutable causal result
    # (R), the current task and the optional exact recovery receipt (Q).  It
    # authenticates immutable OWNERSHIP evidence (attempt/result/binding
    # identity and the greatest durable stage) but never the failed CONTINUATION
    # evidence whose absence caused the refusal (missing stage audit, corrupt
    # assessment).  It mints no successor, envelope, notification or dispatch
    # generation and never touches an unrelated/mismatched receipt.  The store
    # is a thin forwarder; this method owns BEGIN/ROLLBACK/commit.

    def _v2_refusal_stage_payload(
        self, attempt_row: dict, candidate_id: str | None, refusal_code: str,
        finalization_state: str,
    ) -> dict:
        payload = {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED,
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "binding_id": attempt_row["binding_id"],
            "contract_id": attempt_row["contract_id"],
            "contract_version": attempt_row["contract_version"],
            "contract_digest": attempt_row["contract_digest"],
            "release_id": attempt_row["release_id"],
            "activation_id": attempt_row["activation_id"],
            "activation_epoch": attempt_row["activation_epoch"],
            "selector_id": attempt_row["selector_id"],
            "assessment_digest": attempt_row["assessment_digest"],
            "owner_attempt_id": attempt_row["owner_attempt_id"],
            "origin_boot_id": attempt_row["origin_boot_id"],
            "refusal_code": refusal_code,
            "finalization_state": finalization_state,
        }
        if candidate_id is not None:
            payload["candidate_id"] = candidate_id
        return payload

    def _authenticate_v2_refusal_result_stage_uncommitted(
        self, attempt_row: dict, *, candidate_id: str | None, refusal_code: str,
        finalization_state: str,
    ) -> bool:
        """Require exactly one authentic closed terminal refusal stage event."""
        expected = self._v2_refusal_stage_payload(
            attempt_row, candidate_id, refusal_code, finalization_state,
        )
        try:
            candidates = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
                and row["payload"].get("stage") == AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED
            ]
        except Exception:
            return False
        if len(candidates) != 1:
            return False
        payload = candidates[0]["payload"]
        if set(payload.keys()) != set(expected.keys()):
            return False
        return all(payload.get(key) == value for key, value in expected.items())

    def _v2_refusal_completion_payload(
        self, *, attempt_row: dict, refusal_code: str,
        recovery_session_id: str | None,
    ) -> dict:
        payload = {
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "refusal_code": refusal_code,
        }
        if recovery_session_id is not None:
            payload["_recovery_session_id"] = recovery_session_id
            payload["_result_row_id"] = attempt_row["result_id"]
        return payload

    def _authenticate_v2_refusal_completion_uncommitted(
        self, attempt_row: dict, *, refusal_code: str,
    ) -> tuple[bool, bool]:
        """Authenticate exactly one identity-scoped bounded refusal completion.

        Identity-scoped rows are enumerated FIRST (action/agent/attempt only),
        then the exact closed key set, types, values and cardinality are
        required.  A second ``completion_report`` for the same attempt with a
        DIFFERENT refusal code is a conflicting terminal-evidence row and
        refuses: mismatching codes/discriminator values are never filtered out
        of the uniqueness check.  Returns ``(authenticated, recovery_claimed)``
        where ``recovery_claimed`` says the stored evidence carries the exact
        recovery receipt fields.
        """
        try:
            candidates = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") == "completion_report"
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
        except Exception:
            return False, False
        if len(candidates) != 1:
            return False, False
        payload = candidates[0]["payload"]
        base_keys = {"attempt_id", "result_id", "refusal_code"}
        recovery_keys = base_keys | {"_recovery_session_id", "_result_row_id"}
        keys = set(payload.keys())
        if keys != base_keys and keys != recovery_keys:
            return False, False
        if (
            not isinstance(payload.get("attempt_id"), str)
            or payload["attempt_id"] != attempt_row["attempt_id"]
        ):
            return False, False
        if (
            not isinstance(payload.get("result_id"), int)
            or isinstance(payload.get("result_id"), bool)
            or payload["result_id"] != attempt_row["result_id"]
        ):
            return False, False
        if (
            not isinstance(payload.get("refusal_code"), str)
            or payload["refusal_code"] != refusal_code
        ):
            return False, False
        if keys == recovery_keys:
            if (
                not isinstance(payload.get("_recovery_session_id"), str)
                or payload["_recovery_session_id"] != attempt_row["manager_session_id"]
            ):
                return False, False
            if (
                not isinstance(payload.get("_result_row_id"), int)
                or isinstance(payload.get("_result_row_id"), bool)
                or payload["_result_row_id"] != attempt_row["result_id"]
            ):
                return False, False
            return True, True
        return True, False

    def _authenticate_v2_refusal_escalation_uncommitted(
        self, attempt_row: dict, *, refusal_code: str,
    ) -> str | None:
        """Authenticate exactly one complete root-escalated or child-failed shape."""
        try:
            rows = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") in (
                    "escalation", AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
                )
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (attempt_row["root_task_id"],),
            ).fetchone()
        except Exception:
            return None
        if len(rows) != 1 or task is None:
            return None
        payload = rows[0]["payload"]
        note = f"authority_v2_refusal:{refusal_code}"
        if rows[0]["action"] == "escalation":
            expected = {
                "reason": "authority_v2_refusal",
                "refusal_code": refusal_code,
                "attempt_id": attempt_row["attempt_id"],
            }
            if (
                task["parent_task_id"] is None
                and task["status"] == TaskStatus.ESCALATED.value
                and task["note"] == note
                and set(payload) == set(expected)
                and all(payload.get(key) == value for key, value in expected.items())
            ):
                return "escalated"
            return None
        expected = {
            "reason": "authority_v2_refusal",
            "refusal_code": refusal_code,
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "parent_task_id": task["parent_task_id"],
        }
        if (
            task["parent_task_id"] is not None
            and task["status"] == TaskStatus.FAILED.value
            and task["note"] == note
            and task["completed_at"] is not None
            and set(payload) == set(expected)
            and all(payload.get(key) == value for key, value in expected.items())
        ):
            return "failed"
        return None

    def _authenticate_v2_obligation_uncommitted(self, attempt_row: dict) -> str | None:
        """Return the closed code of exactly one authentic failed-stage obligation."""
        try:
            rows = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") == AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
        except Exception:
            return None
        if len(rows) != 1:
            return None
        payload = rows[0]["payload"]
        expected_keys = {
            "attempt_id", "result_id", "refusal_code", "origin_boot_id",
            "owner_attempt_id",
        }
        if set(payload.keys()) != expected_keys:
            return None
        if (
            payload["result_id"] != attempt_row["result_id"]
            or payload["origin_boot_id"] != attempt_row["origin_boot_id"]
            or payload["owner_attempt_id"] != attempt_row["owner_attempt_id"]
            or payload["refusal_code"]
            not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES
        ):
            return None
        return payload["refusal_code"]

    def _update_v2_attempt_finalization_uncommitted(
        self, attempt: AuthorityPolicyV2Attempt, finalization_state: str,
        refusal_code: str | None,
    ) -> None:
        """CAS the attempt to a terminal finalization, retaining its stage."""
        if attempt.finalization_state != "unfinalized":
            raise ValueError("v2 attempt is already finalized")
        if finalization_state not in (
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
            "continued",
        ):
            raise ValueError("illegal v2 attempt finalization")
        if finalization_state == "continued":
            refusal_code = None
        elif refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            raise ValueError("finalized v2 attempt requires a closed refusal code")
        snapshot = attempt.model_dump(mode="json")
        snapshot["finalization_state"] = finalization_state
        snapshot["refusal_code"] = refusal_code
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_attempts
                  SET finalization_state=?, refusal_code=?, canonical_payload_json=?
                WHERE attempt_id=? AND finalization_state='unfinalized' AND stage=?""",
            (
                finalization_state, refusal_code,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                attempt.attempt_id, attempt.stage,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 attempt finalization CAS lost")

    def _settle_v2_exact_receipt_uncommitted(
        self, receipt_row, *, root_task_id: str, manager_agent: str,
        manager_session_id: str, result_id: int, now: str,
    ) -> bool:
        """Settle exactly the still-accepted obsolete matching recovery receipt."""
        cursor = self._conn.execute(
            """UPDATE task_completion_recoveries
                  SET state='callback_consumed', accepted_result_session_id=?,
                      settled_at=?
                WHERE id=? AND task_id=? AND agent=? AND recovery_session_id=?
                  AND accepted_result_id=? AND accepted_result_session_id=?
                  AND state='callback_accepted'""",
            (
                manager_session_id, now, receipt_row["id"], root_task_id,
                manager_agent, manager_session_id, result_id, manager_session_id,
            ),
        )
        return cursor.rowcount == 1

    @_synchronized
    def finalize_authority_policy_v2_attempt_refusal(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, refusal_code: str, owner_attempt_id: str | None = None,
        recovery_session_id: str | None = None,
    ) -> AuthorityPolicyV2HousekeepingOutcome:
        """ONE terminal refusal-housekeeping transaction over exact J/R/task/Q.

        Authenticate-then-mutate: a wrong/mixed tuple, a missing binding, an
        unrelated receipt, an unauthorized contender or an unsafe attribution
        refuses BEFORE any task/Q/J mutation.  A still-current root owner is
        escalated while a delegated owner is failed; both refuse J.  A
        cancelled/terminal/replaced owner keeps the winning task row exactly
        and records the old attempt owner_lost.  An
        already-finalized J returns a read-only exact replay only when its exact
        refusal/completion evidence authenticates, and is never repaired.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _pending(reason: str) -> AuthorityPolicyV2HousekeepingOutcome:
            return AuthorityPolicyV2HousekeepingOutcome(
                status="housekeeping_pending", refusal_code=reason,
                attempt_id=attempt_id,
            )

        if refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            raise ValueError("refusal code is not a closed v2 housekeeping value")
        if recovery_session_id is not None and recovery_session_id != manager_session_id:
            return _pending("identity_mismatch")
        # Reject caller-owned transaction nesting BEFORE BEGIN/ROLLBACK/liveness.
        if self._conn.in_transaction:
            return _pending("transaction_owned")
        attempt: AuthorityPolicyV2Attempt | None = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                """SELECT * FROM authority_policy_v2_attempts
                   WHERE root_task_id=? AND manager_agent=?
                     AND manager_session_id=? AND result_id=?""",
                (root_task_id, manager_agent, manager_session_id, result_id),
            ).fetchone()
            if row is None:
                self._conn.rollback()
                return _pending("identity_mismatch")
            try:
                attempt = self._authority_policy_v2_attempt_from_row(row)
            except ValueError:
                self._conn.rollback()
                return _pending("identity_mismatch")
            attempt_row = dict(row)
            if attempt_id != attempt.attempt_id:
                self._conn.rollback()
                return _pending("identity_mismatch")

            # Optional candidate (K) identity, if one was created.
            candidate = None
            candidate_row = self._conn.execute(
                """SELECT * FROM authority_policy_v2_candidates
                   WHERE root_task_id=? AND manager_agent=?
                     AND manager_session_id=? AND result_id=?""",
                (root_task_id, manager_agent, manager_session_id, result_id),
            ).fetchone()
            if candidate_row is not None:
                try:
                    candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
                except ValueError:
                    self._conn.rollback()
                    return _pending("identity_mismatch")
                if (
                    candidate.attempt_id != attempt.attempt_id
                    or candidate.root_task_id != root_task_id
                    or candidate.manager_agent != manager_agent
                    or candidate.manager_session_id != manager_session_id
                ):
                    self._conn.rollback()
                    return _pending("identity_mismatch")
            candidate_id = None if candidate is None else candidate.candidate_id

            # Immutable attribution FIRST (never the failed continuation
            # evidence): the exact causal result row and the immutable launch
            # binding are authenticated BEFORE any terminal success or any
            # receipt classification.  A drifted result/session/binding can
            # therefore never return an ``already_refused`` success.  The
            # result BODY/assessment is deliberately NOT required: a corrupt
            # assessment or a missing pre-final audit is a legitimate refusal
            # cause and must not block terminal housekeeping or its exact replay.
            result_row = self._conn.execute(
                "SELECT * FROM task_results WHERE id=?", (result_id,)
            ).fetchone()
            if (
                result_row is None
                or result_row["task_id"] != root_task_id
                or result_row["agent"] != manager_agent
                or result_row["session_id"] != manager_session_id
            ):
                self._conn.rollback()
                return _pending("identity_mismatch")
            binding = self.get_authority_policy_v2_session_binding(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id,
            )
            if (
                binding is None
                or binding.binding_id != attempt.binding_id
                or binding.root_task_id != root_task_id
                or binding.manager_agent != manager_agent
                or binding.manager_session_id != manager_session_id
            ):
                self._conn.rollback()
                return _pending("identity_mismatch")
            try:
                self._authenticate_v2_session_binding_uncommitted(binding)
            except Exception:
                self._conn.rollback()
                return _pending("identity_mismatch")

            # Optional exact Q: match only this attempt's exact recovery-session
            # identity.  A task may legitimately retain settled receipts from
            # prior generations; those rows are historical evidence, not a
            # claim that the current ordinary result is recovery-owned.
            receipts = self._conn.execute(
                "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
                (root_task_id, manager_agent),
            ).fetchall()
            matching_receipts = [
                receipt for receipt in receipts
                if receipt["recovery_session_id"] == manager_session_id
            ]
            if len(matching_receipts) > 1:
                self._conn.rollback()
                return _pending("identity_mismatch")
            exact_receipt = matching_receipts[0] if matching_receipts else None
            if any(
                receipt["recovery_session_id"] != manager_session_id
                and receipt["state"] in ("claimed", "callback_accepted")
                for receipt in receipts
            ):
                # A different still-live recovery obligation is not ordinary
                # absence and must never be settled as this attempt's receipt.
                self._conn.rollback()
                return _pending("identity_mismatch")
            if exact_receipt is not None:
                if (
                    exact_receipt["accepted_result_id"] != result_id
                    or exact_receipt["accepted_result_session_id"] != manager_session_id
                    or exact_receipt["state"]
                    not in ("callback_accepted", "callback_consumed")
                ):
                    self._conn.rollback()
                    return _pending("identity_mismatch")
            # Receipt identity comes from a REAL Q.  An explicit recovery
            # assertion with no actual matching receipt fails closed with no
            # task/Q/J change and never manufactures recovery-shaped evidence;
            # a genuine ordinary absence (no recovery asserted, no Q) stays
            # ordinary.
            if recovery_session_id is not None and exact_receipt is None:
                self._conn.rollback()
                return _pending("receipt_missing")
            effective_recovery_session_id = (
                manager_session_id if exact_receipt is not None else None
            )

            # Already-finalized J: read-only exact replay, never repair.  The
            # complete identity-scoped terminal evidence set must authenticate
            # as ONE closed set: the refusal result-stage event, the single
            # bounded completion audit, the normal escalation audit for the
            # still-current-owner refused outcome, and the candidate refused
            # audit when K exists.  Missing/deleted/mutated/duplicate/conflicting
            # /malformed evidence refuses with no repair and no allocation.
            if attempt.finalization_state in (
                AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
            ):
                terminal_ok = self._authenticate_v2_refusal_result_stage_uncommitted(
                    attempt_row, candidate_id=candidate_id,
                    refusal_code=attempt.refusal_code,
                    finalization_state=attempt.finalization_state,
                )
                completion_ok, recovery_claimed = (
                    self._authenticate_v2_refusal_completion_uncommitted(
                        attempt_row, refusal_code=attempt.refusal_code,
                    )
                )
                terminal_ok = terminal_ok and completion_ok
                if terminal_ok:
                    if recovery_claimed:
                        # The stored recovery evidence is only authentic when the
                        # exact Q is durably `callback_consumed` with the exact
                        # task/agent/recovery-session/result/result-session tuple.
                        # A deleted/transition-only/replaced Q is missing
                        # evidence, never "ordinary".
                        terminal_ok = (
                            exact_receipt is not None
                            and exact_receipt["state"] == "callback_consumed"
                            and exact_receipt["recovery_session_id"] == manager_session_id
                            and exact_receipt["accepted_result_id"] == result_id
                            and exact_receipt["accepted_result_session_id"] == manager_session_id
                        )
                    else:
                        # Ordinary refusal evidence requires an ordinary absence.
                        terminal_ok = exact_receipt is None
                if terminal_ok and (
                    attempt.finalization_state
                    == AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED
                ):
                    task_disposition = self._authenticate_v2_refusal_escalation_uncommitted(
                        attempt_row, refusal_code=attempt.refusal_code,
                    )
                    terminal_ok = task_disposition is not None
                else:
                    task_disposition = None
                if terminal_ok and candidate is not None:
                    terminal_ok = self._authenticate_v2_candidate_audit_uncommitted(
                        candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                    )
                self._conn.rollback()
                self._clear_v2_refusal_failure_authority(attempt.attempt_id)
                if terminal_ok:
                    return AuthorityPolicyV2HousekeepingOutcome(
                        status="already_refused", refusal_code=attempt.refusal_code,
                        attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                        stage=attempt.stage,
                        finalization_state=attempt.finalization_state,
                        receipt_settled=recovery_claimed,
                        task_disposition=task_disposition,
                    )
                return _pending("identity_mismatch")
            if attempt.finalization_state != "unfinalized":
                self._conn.rollback()
                return _pending("identity_mismatch")
            if exact_receipt is not None and exact_receipt["state"] == "callback_consumed":
                # An already-consumed exact Q without a finalized J is
                # inconsistent terminal evidence; never repair or re-settle it.
                self._conn.rollback()
                return _pending("identity_mismatch")

            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (root_task_id,)
            ).fetchone()
            if task is None:
                self._conn.rollback()
                return _pending("identity_mismatch")

            now = _now().isoformat()
            still_current = (
                task["cancelled_at"] is None
                and task["status"] == TaskStatus.IN_PROGRESS.value
                and task["block_kind"] is None
                and task["assigned_agent"] == manager_agent
                and task["current_session_id"] == manager_session_id
            )

            if not still_current:
                # Preserve the winning task row exactly.  Record the old
                # attempt's owner_lost/cancellation disposition and audits; only
                # an exact obsolete matching Q may be settled.
                terminal = task["status"] in (
                    "completed", "failed", "superseded", "cancelled",
                )
                final_code = (
                    "cancelled"
                    if (task["cancelled_at"] is not None or terminal)
                    else "owner_lost"
                )
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent,
                    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
                    self._v2_refusal_stage_payload(
                        attempt_row, candidate_id, final_code,
                        AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    ),
                )
                if candidate is not None:
                    self._insert_v2_candidate_audit_uncommitted(
                        candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                        owner_attempt_id=attempt.owner_attempt_id,
                        origin_boot_id=attempt.origin_boot_id, now=now,
                    )
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent, "completion_report",
                    self._v2_refusal_completion_payload(
                        attempt_row=attempt_row, refusal_code=final_code,
                        recovery_session_id=effective_recovery_session_id,
                    ),
                )
                settled = False
                if exact_receipt is not None:
                    if not self._settle_v2_exact_receipt_uncommitted(
                        exact_receipt, root_task_id=root_task_id,
                        manager_agent=manager_agent,
                        manager_session_id=manager_session_id, result_id=result_id,
                        now=now,
                    ):
                        self._conn.rollback()
                        return _pending("identity_mismatch")
                    settled = True
                self._update_v2_attempt_finalization_uncommitted(
                    attempt, AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    final_code,
                )
                self._conn.commit()
                self._clear_v2_refusal_failure_authority(attempt.attempt_id)
                if owner_attempt_id is not None:
                    self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
                return AuthorityPolicyV2HousekeepingOutcome(
                    status="owner_lost", refusal_code=final_code,
                    attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                    stage=attempt.stage,
                    finalization_state=AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    receipt_settled=settled,
                )

            # Live-owner safety: only the authentic uninterrupted owner token, an
            # old-boot attempt (trusted current process identity differs), the
            # process-local marker left by THIS owner's failed refusal
            # transaction, or a server-written durable failed-stage obligation
            # may finalize.  A same-boot attempt with no token (e.g. a second
            # Database instance) cannot prove the winner is dead and returns
            # bounded pending.
            live_owner = (
                owner_attempt_id is not None
                and self._v2_contender_is_authentic_owner(
                    attempt_id=attempt.attempt_id,
                    owner_attempt_id=owner_attempt_id,
                    origin_boot_id=attempt.origin_boot_id,
                )
            )
            old_boot = (
                self._v2_process_boot_id is not None
                and attempt.origin_boot_id != self._v2_process_boot_id
            )
            refusal_failed = self._v2_refusal_failure_authority(
                attempt_id=attempt.attempt_id, owner_attempt_id=owner_attempt_id,
            )
            if not (live_owner or old_boot or refusal_failed):
                if self._authenticate_v2_obligation_uncommitted(attempt_row) is None:
                    self._conn.rollback()
                    return _pending("owner_lost")

            # Still-current causal owner: roots retain the existing escalation
            # shape; delegated tasks atomically fail with their own closed audit.
            self.insert_audit_log_uncommitted(
                root_task_id, manager_agent,
                AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
                self._v2_refusal_stage_payload(
                    attempt_row, candidate_id, refusal_code,
                    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                ),
            )
            if candidate is not None:
                self._insert_v2_candidate_audit_uncommitted(
                    candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                    owner_attempt_id=attempt.owner_attempt_id,
                    origin_boot_id=attempt.origin_boot_id, now=now,
                )
            parent_task_id = task["parent_task_id"]
            if parent_task_id is None:
                task_disposition = "escalated"
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent, "escalation",
                    {
                        "reason": "authority_v2_refusal",
                        "refusal_code": refusal_code,
                        "attempt_id": attempt.attempt_id,
                    },
                )
                cursor = self._conn.execute(
                    """UPDATE tasks SET status=?, block_kind=NULL, note=?, updated_at=?
                       WHERE id=? AND cancelled_at IS NULL AND status=?
                         AND block_kind IS NULL AND assigned_agent=?
                         AND current_session_id=? AND parent_task_id IS NULL""",
                    (
                        TaskStatus.ESCALATED.value,
                        f"authority_v2_refusal:{refusal_code}", now, root_task_id,
                        TaskStatus.IN_PROGRESS.value, manager_agent,
                        manager_session_id,
                    ),
                )
            else:
                task_disposition = "failed"
                self.insert_audit_log_uncommitted(
                    root_task_id,
                    manager_agent,
                    AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
                    {
                        "reason": "authority_v2_refusal",
                        "refusal_code": refusal_code,
                        "attempt_id": attempt.attempt_id,
                        "result_id": result_id,
                        "parent_task_id": parent_task_id,
                    },
                )
                cursor = self._conn.execute(
                    """UPDATE tasks
                          SET status=?, block_kind=NULL, note=?, completed_at=?,
                              updated_at=?, active_chain=NULL, active_fanout=NULL
                        WHERE id=? AND cancelled_at IS NULL AND status=?
                          AND block_kind IS NULL AND assigned_agent=?
                          AND current_session_id=? AND parent_task_id IS NOT NULL""",
                    (
                        TaskStatus.FAILED.value,
                        f"authority_v2_refusal:{refusal_code}", now, now,
                        root_task_id, TaskStatus.IN_PROGRESS.value,
                        manager_agent, manager_session_id,
                    ),
                )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return _pending("owner_lost")
            if task_disposition == "failed":
                env = self._conn.execute(
                    """SELECT * FROM authority_continue_envelopes
                       WHERE root_task_id=? AND state='active'""",
                    (root_task_id,),
                ).fetchone()
                if env is not None:
                    consumed = self._conn.execute(
                        """UPDATE authority_continue_envelopes
                              SET state='violated', consumed_at=?, updated_at=?
                            WHERE id=? AND state='active'""",
                        (now, now, env["id"]),
                    )
                    if consumed.rowcount != 1:
                        self._conn.rollback()
                        return _pending("owner_lost")
                    self.insert_audit_log_uncommitted(
                        root_task_id,
                        manager_agent,
                        "authority_continue_envelope_violated",
                        {
                            "envelope_id": env["id"],
                            "candidate_id": env["candidate_id"],
                            "root_task_id": root_task_id,
                            "decision_family": "aborted",
                            "clause_id": env["clause_id"],
                            "action": env["action"],
                            "policy_id": env["policy_id"],
                            "policy_version": env["policy_version"],
                            "policy_digest": env["policy_digest"],
                            "causal_event_id": env["causal_event_id"],
                            "causal_event_digest": env["causal_event_digest"],
                            "state": "violated",
                            "error": "task failed without the permitted continued-turn decision",
                        },
                    )
            self.insert_audit_log_uncommitted(
                root_task_id, manager_agent, "completion_report",
                self._v2_refusal_completion_payload(
                    attempt_row=attempt_row, refusal_code=refusal_code,
                    recovery_session_id=effective_recovery_session_id,
                ),
            )
            settled = False
            if exact_receipt is not None:
                if not self._settle_v2_exact_receipt_uncommitted(
                    exact_receipt, root_task_id=root_task_id,
                    manager_agent=manager_agent,
                    manager_session_id=manager_session_id, result_id=result_id,
                    now=now,
                ):
                    self._conn.rollback()
                    return _pending("identity_mismatch")
                settled = True
            self._update_v2_attempt_finalization_uncommitted(
                attempt, AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                refusal_code,
            )
            self._conn.commit()
            self._clear_v2_refusal_failure_authority(attempt.attempt_id)
            if owner_attempt_id is not None:
                self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
            return AuthorityPolicyV2HousekeepingOutcome(
                status="refused", refusal_code=refusal_code,
                attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                stage=attempt.stage,
                finalization_state=AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                receipt_settled=settled,
                task_disposition=task_disposition,
            )
        except Exception:
            self._conn.rollback()
            # Failure ownership is established BEFORE poisoning: only the exact
            # process-local winning token that this caller presented proves the
            # authentic uninterrupted owner.  A malformed/foreign/nested/
            # duplicate contender returns bounded pending/refusal above and
            # never reaches this path, so it can never poison or finalize a
            # valid winner.  The authentic owner's failed refusal poisons the
            # live token (prohibiting any later claim/evaluate/consume/audit
            # advancement and final mint) and records a bounded best-effort
            # durable obligation; the process-local marker keeps safely
            # attributable housekeeping retry possible even when persisting that
            # diagnostic fails, so liveness is never reconstructed from durable
            # UUIDs.  The original exception is preserved truthfully.
            if (
                attempt is not None
                and owner_attempt_id is not None
                and owner_attempt_id == attempt.owner_attempt_id
                and self._v2_live_attempt_owners.get(attempt.attempt_id)
                == owner_attempt_id
            ):
                self._mark_v2_refusal_failure_authority(
                    attempt.attempt_id, owner_attempt_id,
                )
                self._record_v2_failed_stage_obligation(
                    attempt_id=attempt.attempt_id,
                    owner_attempt_id=owner_attempt_id, code=refusal_code,
                )
                self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
            raise

    def _v2_housekeeping_target_from_row(
        self, row, *, obligation_code: str | None,
    ) -> AuthorityPolicyV2HousekeepingTarget:
        attempt = self._authority_policy_v2_attempt_from_row(row)
        candidate_row = self._conn.execute(
            "SELECT candidate_id FROM authority_policy_v2_candidates WHERE result_id=?",
            (attempt.result_id,),
        ).fetchone()
        candidate_id = (
            None if candidate_row is None else candidate_row["candidate_id"]
        )
        return AuthorityPolicyV2HousekeepingTarget(
            attempt_id=attempt.attempt_id,
            team=attempt.team,
            root_task_id=attempt.root_task_id,
            manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
            result_id=attempt.result_id,
            origin_boot_id=attempt.origin_boot_id,
            owner_attempt_id=attempt.owner_attempt_id,
            stage=attempt.stage,
            finalization_state=attempt.finalization_state,
            candidate_id=candidate_id,
            obligation_code=obligation_code,
        )

    @_synchronized
    def list_authority_policy_v2_unfinalized_attempts(
        self,
    ) -> list[AuthorityPolicyV2HousekeepingTarget]:
        """Read-only discovery of every unfinalized pre-final attempt.

        Reads are nonmutating and authenticate the actual persisted attempt row;
        a failed claim that created no candidate (K) remains discoverable.  A
        corrupt/unreadable present attempt fails the whole discovery closed so
        startup cannot route its root through generic recovery by omission.
        """
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE finalization_state='unfinalized'
               ORDER BY created_at, attempt_id""",
        ).fetchall()
        targets = []
        for row in rows:
            try:
                obligation_code = self._authenticate_v2_obligation_uncommitted(dict(row))
                targets.append(self._v2_housekeeping_target_from_row(
                    row, obligation_code=obligation_code,
                ))
            except ValueError as exc:
                # Startup cannot safely continue around a present malformed J:
                # silently omitting it would let generic recovery/failure/
                # enqueue logic mutate the same root.  The caller treats this
                # as a global fail-closed discovery result for that sweep.
                raise ValueError(
                    "authority v2 unfinalized attempt discovery is malformed"
                ) from exc
        return targets

    @_synchronized
    def get_authority_policy_v2_housekeeping_target(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2HousekeepingTarget | None:
        """Read-only discovery of one exact unfinalized attempt (nonmutating)."""
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return None
        try:
            obligation_code = self._authenticate_v2_obligation_uncommitted(dict(row))
            return self._v2_housekeeping_target_from_row(
                row, obligation_code=obligation_code,
            )
        except ValueError:
            return None

    # -- THR-229 checkpoint C3d2: the ONE final continuation transaction
    # (active E + final audits + N needed + D pending(G) + task Pending + J
    # continued) and the SEPARATE exact post-final receipt-settlement
    # transaction/read.  Both are Database-owned synchronized BEGIN IMMEDIATE
    # transactions; the store is a thin forwarder.  Publication, generation
    # admission and envelope spend writers are later units and are NOT
    # implemented here.

    def _authority_policy_v2_envelope_from_row(
        self, row,
    ) -> AuthorityPolicyV2ContinueEnvelope:
        try:
            envelope = AuthorityPolicyV2ContinueEnvelope.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 envelope has a corrupt canonical payload") from exc
        if envelope.envelope_id != row["envelope_id"]:
            raise ValueError("authority v2 envelope identity mismatch")
        for column, value in envelope.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 envelope column/preimage mismatch")
        return envelope

    def _authority_policy_v2_notification_from_row(
        self, row,
    ) -> AuthorityPolicyV2RecoveryNotification:
        try:
            notification = AuthorityPolicyV2RecoveryNotification.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 notification has a corrupt canonical payload") from exc
        if notification.notification_id != row["notification_id"]:
            raise ValueError("authority v2 notification identity mismatch")
        for column, value in notification.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 notification column/preimage mismatch")
        return notification

    def _authority_policy_v2_root_dispatch_from_row(
        self, row,
    ) -> AuthorityPolicyV2RootDispatch:
        try:
            dispatch = AuthorityPolicyV2RootDispatch.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 root dispatch has a corrupt canonical payload") from exc
        if dispatch.root_task_id != row["root_task_id"]:
            raise ValueError("authority v2 root dispatch identity mismatch")
        for column, value in dispatch.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 root dispatch column/preimage mismatch")
        return dispatch

    def _v2_finalization_reason_for(self, code: str | None) -> str:
        if code == "transaction_owned":
            return "transaction_owned"
        if code in ("owner_lost", "cancelled"):
            return "owner_lost"
        if code == "schema_drift":
            return "schema_drift"
        if code in ("evidence_drift", "identity_mismatch"):
            return code
        return "identity_mismatch"

    def _v2_final_result_stage_payload(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> dict:
        return {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED,
            "attempt_id": attempt_row["attempt_id"],
            "candidate_id": candidate_id,
            "result_id": attempt_row["result_id"],
            "binding_id": attempt_row["binding_id"],
            "contract_id": attempt_row["contract_id"],
            "contract_version": attempt_row["contract_version"],
            "contract_digest": attempt_row["contract_digest"],
            "release_id": attempt_row["release_id"],
            "activation_id": attempt_row["activation_id"],
            "activation_epoch": attempt_row["activation_epoch"],
            "selector_id": attempt_row["selector_id"],
            "assessment_digest": attempt_row["assessment_digest"],
            "owner_attempt_id": attempt_row["owner_attempt_id"],
            "origin_boot_id": attempt_row["origin_boot_id"],
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "finalization_state": "continued",
        }

    def _v2_final_task_payload(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> dict:
        return {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED,
            "attempt_id": attempt_row["attempt_id"],
            "candidate_id": candidate_id,
            "result_id": attempt_row["result_id"],
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "root_task_id": attempt_row["root_task_id"],
        }

    def _v2_final_hook_payload(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> dict:
        return {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED,
            "attempt_id": attempt_row["attempt_id"],
            "candidate_id": candidate_id,
            "result_id": attempt_row["result_id"],
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "outcome": "continue_applies",
        }

    def _v2_identity_scoped_audits(
        self, root_task_id: str, manager_agent: str, action: str,
        *, attempt_id: str | None = None, include_opaque: bool = False,
    ) -> list[dict] | None:
        """Enumerate identity-scoped audit rows BEFORE any payload filtering.

        Enumerating by action/agent (and attempt where known) first means a
        second row with a DIFFERENT discriminator/code cannot hide from the
        cardinality check that follows.

        With ``include_opaque`` a row whose ``payload`` is not a JSON object is
        retained rather than silently dropped: a non-object body can never be
        independently established as unrelated, so the caller's closed typed
        comparison must fail closed on it instead of observing an apparent
        absence.
        """
        try:
            rows = []
            for row in self.get_audit_logs(root_task_id):
                if row.get("action") != action or row.get("agent") != manager_agent:
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    if include_opaque:
                        rows.append(row)
                    continue
                if attempt_id is not None and payload.get("attempt_id") != attempt_id:
                    continue
                rows.append(row)
            return rows
        except Exception:
            return None

    @classmethod
    def _v2_identity_observation(cls, payload, field: str, expected, kind: str) -> str:
        """Classify one identity field against the expected causal identity.

        ``absent`` (field not carried), ``match`` (well-typed and equal),
        ``distinct`` (well-typed and provably different) or ``malformed``
        (present but not the expected JSON type).  A malformed presence is never
        ordinary absence.
        """
        if not isinstance(payload, dict) or field not in payload:
            return "absent"
        value = payload[field]
        well_typed = cls._v2_is_int(value) if kind == "int" else isinstance(value, str)
        if not well_typed:
            return "malformed"
        return "match" if value == expected else "distinct"

    @classmethod
    def _v2_result_reference_state(cls, payload, fields, result_id: int) -> str:
        """Resolve a row's result reference across ``fields`` (presence-based).

        ``none``: the row carries no result reference at all.  ``related``: some
        present reference matches the exact result or is malformed/conflicting.
        ``distinct``: every present reference is a well-typed integer for a
        different result.
        """
        present = [
            field for field in fields
            if isinstance(payload, dict) and field in payload
        ]
        if not present:
            return "none"
        states = [
            cls._v2_identity_observation(payload, field, result_id, "int")
            for field in present
        ]
        if any(state in ("match", "malformed") for state in states):
            return "related"
        return "distinct"

    @classmethod
    def _v2_session_reference_related(cls, payload, fields, session_id: str) -> bool:
        """True when a present session reference matches or is malformed.

        A row whose every present session reference is a well-typed string for a
        DIFFERENT session is independently established as unrelated.
        """
        for field in fields:
            if not isinstance(payload, dict) or field not in payload:
                continue
            value = payload[field]
            if not isinstance(value, str) or value == session_id:
                return True
        # Every present session reference is well-typed and distinct, or the row
        # carries no session reference at all: independently unrelated.
        return False

    @classmethod
    def _v2_completion_row_is_recovery_related(
        cls, payload, *, result_id: int, manager_session_id: str,
    ) -> bool:
        """Presence-based relatedness for a recovery-shaped completion row.

        A row is recovery-shaped when it carries the ``_recovery_session_id``
        key OR the settlement-completion-only ``result_id``/``session_id`` keys,
        which the legitimate ordinary producer payload (a ``CompletionReport``
        plus ``_result_row_id``/``_result_session_id``) never contains.  Removing
        the recovery marker key does therefore not turn a settlement completion
        into ordinary absence.
        """
        if not isinstance(payload, dict):
            return True
        recovery_shaped = (
            "_recovery_session_id" in payload
            or "result_id" in payload
            or "session_id" in payload
        )
        if not recovery_shaped:
            return False
        ref_state = cls._v2_result_reference_state(
            payload, ("_result_row_id", "result_id"), result_id,
        )
        if ref_state == "related":
            return True
        return cls._v2_session_reference_related(
            payload,
            ("_recovery_session_id", "_result_session_id", "session_id"),
            manager_session_id,
        )

    @classmethod
    def _v2_completion_row_is_ordinary_related(
        cls, payload, *, result_id: int, manager_session_id: str,
    ) -> bool:
        """Presence-based relatedness for an ordinary producer completion row."""
        if not isinstance(payload, dict):
            return True
        if (
            "_recovery_session_id" in payload
            or "result_id" in payload
            or "session_id" in payload
        ):
            # Recovery-shaped evidence is classified separately and is never
            # ordinary authority.
            return False
        ref_state = cls._v2_result_reference_state(
            payload, ("_result_row_id", "result_id"), result_id,
        )
        if ref_state == "related":
            return True
        # A provably-DISTINCT result reference never vetoes a surviving exact or
        # malformed SESSION reference: EVERY present causal reference is
        # evaluated before the row may be declared independently unrelated.  A
        # well-typed-but-conflicting result id therefore cannot hide an exact or
        # malformed current-session duplicate, while a row whose every present
        # result AND session reference is well-typed and distinct stays
        # independently unrelated.
        return cls._v2_session_reference_related(
            payload, ("_result_session_id", "session_id"), manager_session_id,
        )

    def _v2_closed_audit_matches(self, rows, expected: dict) -> bool:
        if rows is None or len(rows) != 1:
            return False
        payload = rows[0]["payload"]
        if set(payload.keys()) != set(expected.keys()):
            return False
        return all(payload.get(key) == value for key, value in expected.items())

    @staticmethod
    def _v2_is_int(value) -> bool:
        """True only for a real JSON integer (never ``bool``)."""
        return isinstance(value, int) and not isinstance(value, bool)

    @classmethod
    def _v2_json_type_sensitive_equal(cls, left, right) -> bool:
        """Strict JSON equality where ``True`` never equals integer ``1``.

        Dict key sets, list length/order and every scalar JSON type must match
        exactly (``int`` vs ``bool`` vs ``str`` vs ``None`` are distinct), so a
        mistyped or coerced persisted payload value is never accepted as the
        authenticated preimage.
        """
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            if set(left.keys()) != set(right.keys()):
                return False
            return all(
                cls._v2_json_type_sensitive_equal(left[key], right[key])
                for key in left
            )
        if isinstance(left, list):
            if len(left) != len(right):
                return False
            return all(
                cls._v2_json_type_sensitive_equal(a, b)
                for a, b in zip(left, right)
            )
        return left == right

    def _authenticate_v2_final_result_stage_uncommitted(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> bool:
        expected = self._v2_final_result_stage_payload(
            attempt_row, candidate_id, envelope_id, notification_id, generation_id,
        )
        rows = self._v2_identity_scoped_audits(
            attempt_row["root_task_id"], attempt_row["manager_agent"],
            AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            attempt_id=attempt_row["attempt_id"],
        )
        if rows is None:
            return False
        continued = [
            row for row in rows
            if row["payload"].get("stage") == AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED
        ]
        return self._v2_closed_audit_matches(continued, expected)

    def _authenticate_v2_final_task_audit_uncommitted(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> bool:
        expected = self._v2_final_task_payload(
            attempt_row, candidate_id, envelope_id, notification_id, generation_id,
        )
        rows = self._v2_identity_scoped_audits(
            attempt_row["root_task_id"], attempt_row["manager_agent"],
            AUTHORITY_POLICY_V2_FINAL_TASK_AUDIT_ACTION,
            attempt_id=attempt_row["attempt_id"],
        )
        return self._v2_closed_audit_matches(rows, expected)

    def _authenticate_v2_final_hook_audit_uncommitted(
        self, attempt_row, candidate_id: str, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> bool:
        expected = self._v2_final_hook_payload(
            attempt_row, candidate_id, envelope_id, notification_id, generation_id,
        )
        rows = self._v2_identity_scoped_audits(
            attempt_row["root_task_id"], attempt_row["manager_agent"],
            AUTHORITY_POLICY_V2_FINAL_HOOK_AUDIT_ACTION,
            attempt_id=attempt_row["attempt_id"],
        )
        return self._v2_closed_audit_matches(rows, expected)

    def _authenticate_v2_final_rows_uncommitted(
        self, *, attempt: AuthorityPolicyV2Attempt, attempt_row,
        candidate: AuthorityPolicyV2Candidate,
        evaluation: AuthorityPolicyV2Evaluation,
        require_dispatch_generation: bool = True,
    ) -> tuple[str | None, dict | None]:
        """Authenticate E/N/D plus the complete final audit set read-only.

        Does not require the task to still be in progress, so it also serves the
        exact post-final causal replay.  Missing/corrupt/mixed evidence refuses.

        ``require_dispatch_generation`` stays ``True`` for every existing
        settlement/replay reader (the live root pointer MUST name this exact
        generation).  The C3d3a invalidation seam passes ``False`` so an exact
        old generation can still be authenticated and invalidated after the
        root pointer has legitimately advanced to a replacement generation B;
        the dispatch identity/envelope/owner joins are still authenticated
        unchanged, so this never broadens settlement or admits a foreign tuple.
        """
        if (
            evaluation.outcome != "continue_applies"
            or evaluation.diagnostic_code is not None
        ):
            return "evidence_drift", None
        envelope_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_continue_envelopes WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if envelope_row is None:
            return "evidence_drift", None
        try:
            envelope = self._authority_policy_v2_envelope_from_row(envelope_row)
        except ValueError:
            return "identity_mismatch", None
        if (
            envelope.candidate_id != candidate.candidate_id
            or envelope.claim_key != candidate.claim_key
            or envelope.team != candidate.team
            or envelope.root_task_id != candidate.root_task_id
            or envelope.manager_agent != candidate.manager_agent
            or envelope.manager_session_id != candidate.manager_session_id
            or envelope.attempt_id != attempt.attempt_id
            or envelope.result_id != candidate.result_id
            or envelope.binding_id != candidate.binding_id
            or envelope.contract_id != candidate.contract_id
            or envelope.contract_version != candidate.contract_version
            or envelope.contract_digest != candidate.contract_digest
            or envelope.release_id != candidate.release_id
            or envelope.policy_version != candidate.policy_version
            or envelope.policy_digest != candidate.policy_digest
            or envelope.activation_id != candidate.activation_id
            or envelope.activation_epoch != candidate.activation_epoch
            or envelope.selector_id != candidate.selector_id
            or envelope.provider_id != candidate.provider_id
            or envelope.executor_kind != candidate.executor_kind
            or envelope.model_id != candidate.model_id
            or envelope.causal_result_id != candidate.causal_result_id
            or envelope.causal_result_digest != candidate.causal_result_digest
            or envelope.evaluation_outcome != evaluation.outcome
        ):
            return "identity_mismatch", None
        notification_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_recovery_notifications WHERE envelope_id=?",
            (envelope.envelope_id,),
        ).fetchone()
        if notification_row is None:
            return "evidence_drift", None
        try:
            notification = self._authority_policy_v2_notification_from_row(
                notification_row
            )
        except ValueError:
            return "identity_mismatch", None
        if (
            notification.envelope_id != envelope.envelope_id
            or notification.candidate_id != candidate.candidate_id
            or notification.result_id != candidate.result_id
            or notification.root_task_id != candidate.root_task_id
            or notification.manager_agent != candidate.manager_agent
            or notification.manager_session_id != candidate.manager_session_id
            or notification.selector_id != candidate.selector_id
        ):
            return "identity_mismatch", None
        generation_id = notification.notification_id
        dispatch_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (candidate.root_task_id,),
        ).fetchone()
        if dispatch_row is None:
            return "evidence_drift", None
        try:
            dispatch = self._authority_policy_v2_root_dispatch_from_row(dispatch_row)
        except ValueError:
            return "identity_mismatch", None
        if dispatch.generation_id == generation_id:
            if (
                dispatch.envelope_id != envelope.envelope_id
                or dispatch.expected_manager_agent != candidate.manager_agent
                or dispatch.expected_manager_session_id != candidate.manager_session_id
            ):
                return "already_finalized", None
        elif require_dispatch_generation:
            return "already_finalized", None
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL,
        ):
            return "evidence_drift", None
        if not self._authenticate_v2_final_result_stage_uncommitted(
            attempt_row, candidate.candidate_id, envelope.envelope_id,
            notification.notification_id, generation_id,
        ):
            return "evidence_drift", None
        if not self._authenticate_v2_final_task_audit_uncommitted(
            attempt_row, candidate.candidate_id, envelope.envelope_id,
            notification.notification_id, generation_id,
        ):
            return "evidence_drift", None
        if not self._authenticate_v2_final_hook_audit_uncommitted(
            attempt_row, candidate.candidate_id, envelope.envelope_id,
            notification.notification_id, generation_id,
        ):
            return "evidence_drift", None
        return None, {
            "envelope": envelope, "notification": notification,
            "dispatch": dispatch, "evaluation": evaluation,
            "generation_id": generation_id,
        }

    def _authenticate_v2_post_final_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, require_dispatch_generation: bool = True,
    ) -> tuple[str | None, dict | None]:
        """Read-only authentication of the COMPLETE durable post-final evidence.

        Shared by the exact post-final causal replay and by settlement BEFORE
        any settlement write.  It reuses the established evidence rules — the
        exact J/R/K/P/V joins, the authentic immutable binding and pinned
        release/activation/selector history, the persisted assessment/decision
        identity, the consumed candidate/evaluation, BOTH halves of a0..a3 and
        the complete final E/N/D + audit set — but deliberately does NOT require
        the pre-final live-owner token, an ``in_progress`` pre-final task, a
        fresh mechanical-eligibility re-derivation or today's selector equality.
        A legitimate finalized replay/reopen must authenticate without
        restoring a live pre-final owner, re-evaluating or reminting.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return "identity_mismatch", None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return "identity_mismatch", None
        if attempt.attempt_id != attempt_id:
            return "identity_mismatch", None
        if attempt.finalization_state != "continued":
            return "identity_mismatch", None
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED:
            return "evidence_drift", None
        attempt_row = dict(row)
        # a0 half: the single immutable admission event.  A deleted, mutated,
        # duplicated or foreign admitted audit is missing evidence.
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(attempt_row):
            return "identity_mismatch", None
        # R: the exact causal result row, its identity AND its persisted
        # normalized body/decision (including the accepted escalation action and
        # the assessment digest the attempt was admitted with).
        result_row = self._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (result_id,)
        ).fetchone()
        if (
            result_row is None
            or result_row["task_id"] != root_task_id
            or result_row["agent"] != manager_agent
            or result_row["session_id"] != manager_session_id
        ):
            return "identity_mismatch", None
        if not self._authenticate_v2_result_body_uncommitted(result_row, attempt):
            return "identity_mismatch", None
        # Authentic immutable launch binding and its pinned release/activation/
        # selector history (a later legitimate activation never invalidates the
        # pinned tuple; today's selector equality is never required).
        binding = self.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        )
        if binding is None:
            return "identity_mismatch", None
        try:
            self._authenticate_v2_session_binding_uncommitted(binding)
        except Exception:
            return "identity_mismatch", None
        if (
            binding.binding_id != attempt.binding_id
            or binding.team != attempt.team
            or binding.contract_id != attempt.contract_id
            or binding.contract_version != attempt.contract_version
            or binding.contract_digest != attempt.contract_digest
            or binding.release_id != attempt.release_id
            or binding.activation_id != attempt.activation_id
            or binding.activation_epoch != attempt.activation_epoch
            or binding.selector_id != attempt.selector_id
        ):
            return "identity_mismatch", None
        release = self.get_authority_policy_v2_release(attempt.release_id)
        activation = self.get_authority_policy_v2_activation(attempt.activation_id)
        if release is None or activation is None:
            return "identity_mismatch", None
        if (
            release.team != attempt.team
            or release.policy_digest != binding.policy_digest
            or release.version != binding.policy_version
            or activation.team != attempt.team
            or activation.release_id != attempt.release_id
            or activation.release_digest != release.policy_digest
            or activation.selector_epoch != attempt.activation_epoch
            or binding.provider_id == "" or binding.executor_kind == ""
            or binding.model_id == ""
        ):
            return "identity_mismatch", None
        # K/P: the exact consumed candidate and its immutable pin tuple.
        candidate_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if candidate_row is None:
            return "evidence_drift", None
        try:
            candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
        except ValueError:
            return "identity_mismatch", None
        pin_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if pin_row is None:
            return "evidence_drift", None
        try:
            pin = self._authority_policy_v2_pin_from_row(pin_row)
        except ValueError:
            return "identity_mismatch", None
        if not self._authenticate_v2_candidate_pin_joins_uncommitted(
            attempt=attempt, candidate=candidate, pin=pin, binding=binding,
            release=release,
        ):
            return "identity_mismatch", None
        if candidate.lifecycle_stage != "consumed":
            return "evidence_drift", None
        # BOTH halves of a1/a2/a3.
        for event in (
            AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
            AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
            AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
        ):
            if not self._authenticate_v2_candidate_audit_uncommitted(candidate, event):
                return "evidence_drift", None
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            attempt_row,
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return "evidence_drift", None
        # V: the persisted outcome is authoritative and is never re-derived.
        eval_code, eval_ctx = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if eval_code is not None:
            return "evidence_drift", None
        assert eval_ctx is not None
        evaluation = eval_ctx["evaluation"]
        # E/N/D plus the complete final audit set (candidate ``final`` event,
        # closed ``continued`` result-stage evidence, final task/hook audits).
        code, ctx = self._authenticate_v2_final_rows_uncommitted(
            attempt=attempt, attempt_row=attempt_row, candidate=candidate,
            evaluation=evaluation,
            require_dispatch_generation=require_dispatch_generation,
        )
        if code is not None:
            return code, None
        assert ctx is not None
        return None, {
            **ctx, "attempt": attempt, "candidate": candidate, "pin": pin,
            "binding": binding, "release": release, "activation": activation,
            "result_row": result_row, "evaluation": evaluation,
        }

    def _authenticate_v2_post_final_task_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
    ) -> bool:
        """The post-final causal task projection: Pending with owner preserved.

        Finalization returns the root to ``Pending``/null ``block_kind`` while
        preserving the causal owner/session; settlement and exact replay both
        authenticate that exact projection (never a live pre-final owner).
        """
        task = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        return not (
            task is None
            or task["cancelled_at"] is not None
            or task["status"] != TaskStatus.PENDING.value
            or task["block_kind"] is not None
            or task["assigned_agent"] != manager_agent
            or task["current_session_id"] != manager_session_id
        )

    def _insert_v2_continue_envelope_uncommitted(
        self, envelope: AuthorityPolicyV2ContinueEnvelope,
    ) -> None:
        snapshot = envelope.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_continue_envelopes
               (envelope_id, candidate_id, claim_key, team, root_task_id,
                manager_agent, manager_session_id, attempt_id, result_id,
                binding_id, contract_id, contract_version, contract_digest,
                release_id, policy_version, policy_digest, activation_id,
                activation_epoch, selector_id, provider_id, executor_kind,
                model_id, causal_result_id, causal_result_digest,
                evaluation_outcome, origin_boot_id, owner_attempt_id,
                lifecycle_state, spending_result_id, decision_state,
                canonical_payload_json, created_at)
               VALUES (:envelope_id,:candidate_id,:claim_key,:team,:root_task_id,
                       :manager_agent,:manager_session_id,:attempt_id,:result_id,
                       :binding_id,:contract_id,:contract_version,:contract_digest,
                       :release_id,:policy_version,:policy_digest,:activation_id,
                       :activation_epoch,:selector_id,:provider_id,:executor_kind,
                       :model_id,:causal_result_id,:causal_result_digest,
                       :evaluation_outcome,:origin_boot_id,:owner_attempt_id,
                       :lifecycle_state,:spending_result_id,:decision_state,
                       :canonical_payload_json,:created_at)""",
            {
                **snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    snapshot
                ).decode("utf-8"),
            },
        )

    def _insert_v2_recovery_notification_uncommitted(
        self, notification: AuthorityPolicyV2RecoveryNotification,
    ) -> None:
        snapshot = notification.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_recovery_notifications
               (notification_id, envelope_id, candidate_id, result_id,
                root_task_id, manager_agent, manager_session_id, selector_id,
                state, publication_attempt, publisher_boot_id, lease_deadline,
                next_session_id, canonical_payload_json, created_at, updated_at)
               VALUES (:notification_id,:envelope_id,:candidate_id,:result_id,
                       :root_task_id,:manager_agent,:manager_session_id,
                       :selector_id,:state,:publication_attempt,:publisher_boot_id,
                       :lease_deadline,:next_session_id,:canonical_payload_json,
                       :created_at,:updated_at)""",
            {
                **snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    snapshot
                ).decode("utf-8"),
            },
        )

    def _upsert_v2_root_dispatch_pending_uncommitted(
        self, dispatch: AuthorityPolicyV2RootDispatch, *,
        prior_generation_id: str | None,
    ) -> None:
        snapshot = dispatch.model_dump(mode="json")
        if prior_generation_id is None:
            canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
            self._conn.execute(
                """INSERT INTO authority_policy_v2_root_dispatch
                   (root_task_id, generation_id, envelope_id, state,
                    expected_manager_agent, expected_manager_session_id,
                    canonical_payload_json, created_at, updated_at)
                   VALUES (:root_task_id,:generation_id,:envelope_id,:state,
                           :expected_manager_agent,:expected_manager_session_id,
                           :canonical_payload_json,:created_at,:updated_at)""",
                {**snapshot, "canonical_payload_json": canonical},
            )
            return
        # The CAS replaces only the live generation; the row's immutable
        # ``created_at`` is preserved so the canonical preimage stays consistent
        # with the persisted columns.
        existing = self._conn.execute(
            "SELECT created_at FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (dispatch.root_task_id,),
        ).fetchone()
        if existing is not None:
            snapshot["created_at"] = existing["created_at"]
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_root_dispatch
                  SET generation_id=?, envelope_id=?, state='pending',
                      expected_manager_agent=?, expected_manager_session_id=?,
                      canonical_payload_json=?, updated_at=?
                WHERE root_task_id=? AND state='retired' AND generation_id=?""",
            (
                dispatch.generation_id, dispatch.envelope_id,
                dispatch.expected_manager_agent, dispatch.expected_manager_session_id,
                canonical, snapshot["updated_at"], dispatch.root_task_id,
                prior_generation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 root dispatch CAS lost")

    def _finalize_v2_continuation_replay_uncommitted(
        self, attempt: AuthorityPolicyV2Attempt, attempt_row,
    ) -> AuthorityPolicyV2FinalizationOutcome:
        def _pending(
            reason: str, candidate_id: str | None = None,
        ) -> AuthorityPolicyV2FinalizationOutcome:
            return AuthorityPolicyV2FinalizationOutcome(
                status="finalization_pending", reason=reason,
                attempt_id=attempt.attempt_id, candidate_id=candidate_id,
            )

        # The exact post-final causal replay authenticates the COMPLETE durable
        # evidence read-only (J/R/K/P/V joins, authentic pinned binding history,
        # persisted assessment/decision identity, consumed candidate/evaluation,
        # BOTH halves of a0..a3 and the complete final E/N/D + audit set).  A
        # deleted/mutated/mixed prior audit or a result-body/identity drift
        # refuses with the prior residue intact and allocates no second E/N/D,
        # spends no envelope and resets no N.
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=attempt.root_task_id, manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
            result_id=attempt.result_id,
        )
        if code is not None:
            return _pending(self._v2_finalization_reason_for(code))
        assert ctx is not None
        candidate = ctx["candidate"]
        if not self._authenticate_v2_post_final_task_uncommitted(
            root_task_id=attempt.root_task_id, manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
        ):
            return _pending("identity_mismatch", candidate.candidate_id)
        return AuthorityPolicyV2FinalizationOutcome(
            status="already_continued", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id,
            envelope_id=ctx["envelope"].envelope_id,
            notification_id=ctx["notification"].notification_id,
            generation_id=ctx["generation_id"],
            finalization_state="continued",
        )

    def _finalize_v2_continuation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int, now: str,
    ) -> AuthorityPolicyV2FinalizationOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _pending(reason: str, candidate_id: str | None = None) -> AuthorityPolicyV2FinalizationOutcome:
            return AuthorityPolicyV2FinalizationOutcome(
                status="finalization_pending", reason=reason,
                attempt_id=attempt_id, candidate_id=candidate_id,
            )

        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return _pending("identity_mismatch")
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return _pending("identity_mismatch")
        if attempt.attempt_id != attempt_id:
            return _pending("identity_mismatch")
        attempt_row = dict(row)

        if attempt.finalization_state == "continued":
            return self._finalize_v2_continuation_replay_uncommitted(
                attempt, attempt_row,
            )
        if attempt.finalization_state != "unfinalized":
            return _pending("already_finalized")
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED:
            return _pending("evidence_drift")

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _pending(self._v2_finalization_reason_for(code))
        assert ctx is not None
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        binding = ctx["binding"]
        release = ctx["release"]
        attempt_row = ctx["row"]

        # BOTH halves of EVERY required prior stage: the candidate
        # claimed/evaluated/consumed events AND their sibling closed
        # admitted/claim_audited/evaluation_audited/consumed_audited result-stage
        # audits.  No audit is trusted from the caller.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _pending("evidence_drift", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
        ):
            return _pending("evidence_drift", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
        ):
            return _pending("evidence_drift", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            attempt_row,
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return _pending("evidence_drift", candidate.candidate_id)

        # The persisted V outcome is authoritative; it is never re-derived and
        # no second evaluator is invoked.
        eval_code, eval_ctx = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if eval_code is not None:
            return _pending("evidence_drift", candidate.candidate_id)
        assert eval_ctx is not None
        evaluation = eval_ctx["evaluation"]
        if (
            evaluation.outcome != "continue_applies"
            or evaluation.diagnostic_code is not None
        ):
            return _pending("evidence_drift", candidate.candidate_id)

        # Never replace a live v1/v2 envelope and never mint a second v2
        # envelope for the same candidate.
        if self._conn.execute(
            "SELECT 1 FROM authority_continue_envelopes "
            "WHERE root_task_id=? AND state='active'",
            (root_task_id,),
        ).fetchone() is not None:
            return _pending("already_finalized", candidate.candidate_id)
        if self._conn.execute(
            "SELECT 1 FROM authority_policy_v2_continue_envelopes WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone() is not None:
            return _pending("already_finalized", candidate.candidate_id)

        envelope_id = authority_policy_v2_envelope_id(candidate.candidate_id)
        notification_id = authority_policy_v2_notification_id(envelope_id)
        generation_id = notification_id

        dispatch_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (root_task_id,),
        ).fetchone()
        prior_generation_id: str | None = None
        if dispatch_row is not None:
            try:
                prior_dispatch = self._authority_policy_v2_root_dispatch_from_row(
                    dispatch_row
                )
            except ValueError:
                return _pending("identity_mismatch", candidate.candidate_id)
            if prior_dispatch.state != "retired":
                return _pending("already_finalized", candidate.candidate_id)
            if prior_dispatch.generation_id == generation_id:
                return _pending("already_finalized", candidate.candidate_id)
            prior_generation_id = prior_dispatch.generation_id

        envelope = AuthorityPolicyV2ContinueEnvelope(
            envelope_id=envelope_id,
            candidate_id=candidate.candidate_id,
            claim_key=candidate.claim_key,
            team=candidate.team,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            attempt_id=attempt.attempt_id,
            result_id=candidate.result_id,
            binding_id=candidate.binding_id,
            contract_id=candidate.contract_id,
            contract_version=candidate.contract_version,
            contract_digest=candidate.contract_digest,
            release_id=candidate.release_id,
            policy_version=candidate.policy_version,
            policy_digest=candidate.policy_digest,
            activation_id=candidate.activation_id,
            activation_epoch=candidate.activation_epoch,
            selector_id=candidate.selector_id,
            provider_id=candidate.provider_id,
            executor_kind=candidate.executor_kind,
            model_id=candidate.model_id,
            causal_result_id=candidate.causal_result_id,
            causal_result_digest=candidate.causal_result_digest,
            evaluation_outcome=evaluation.outcome,
            origin_boot_id=attempt.origin_boot_id,
            owner_attempt_id=attempt.owner_attempt_id,
        )
        notification = AuthorityPolicyV2RecoveryNotification(
            notification_id=notification_id,
            envelope_id=envelope_id,
            candidate_id=candidate.candidate_id,
            result_id=candidate.result_id,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            selector_id=candidate.selector_id,
            state="needed",
            publication_attempt=0,
            publisher_boot_id=None,
            lease_deadline=None,
            next_session_id=None,
        )
        dispatch = AuthorityPolicyV2RootDispatch(
            root_task_id=candidate.root_task_id,
            generation_id=generation_id,
            envelope_id=envelope_id,
            state="pending",
            expected_manager_agent=candidate.manager_agent,
            expected_manager_session_id=candidate.manager_session_id,
        )

        self._insert_v2_continue_envelope_uncommitted(envelope)
        self._insert_v2_recovery_notification_uncommitted(notification)
        self._upsert_v2_root_dispatch_pending_uncommitted(
            dispatch, prior_generation_id=prior_generation_id,
        )

        self._insert_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL,
            owner_attempt_id=attempt.owner_attempt_id,
            origin_boot_id=attempt.origin_boot_id, now=now,
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            self._v2_final_result_stage_payload(
                attempt_row, candidate.candidate_id, envelope_id, notification_id,
                generation_id,
            ),
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_FINAL_TASK_AUDIT_ACTION,
            self._v2_final_task_payload(
                attempt_row, candidate.candidate_id, envelope_id, notification_id,
                generation_id,
            ),
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_FINAL_HOOK_AUDIT_ACTION,
            self._v2_final_hook_payload(
                attempt_row, candidate.candidate_id, envelope_id, notification_id,
                generation_id,
            ),
        )

        cursor = self._conn.execute(
            """UPDATE tasks SET status=?, block_kind=NULL, updated_at=?
               WHERE id=? AND cancelled_at IS NULL AND status=?
                 AND block_kind IS NULL AND assigned_agent=?
                 AND current_session_id=?""",
            (
                TaskStatus.PENDING.value, now, root_task_id,
                TaskStatus.IN_PROGRESS.value, manager_agent, manager_session_id,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("owner_lost", candidate.candidate_id)
        self._update_v2_attempt_finalization_uncommitted(attempt, "continued", None)
        self._clear_v2_refusal_failure_authority(attempt.attempt_id)
        return AuthorityPolicyV2FinalizationOutcome(
            status="continued", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, envelope_id=envelope_id,
            notification_id=notification_id, generation_id=generation_id,
            finalization_state="continued",
        )

    @_synchronized
    def finalize_authority_policy_v2_continuation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2FinalizationOutcome:
        """ONE final continuation transaction: E/N/D + final audits + Pending.

        Requires J unfinalized/``consumed_audited``, consumed K, exact P/V,
        authenticated a0..a3 (both halves), the persisted continue outcome, the
        original uninterrupted process-local winning owner and an eligible
        current root/task.  It inserts active E, final candidate/task/hook and
        closed ``continued`` result-stage audits, N needed, D pending(G),
        changes the task to Pending/null block_kind preserving the causal
        owner/session, and CASes J to ``continued``.  No receipt settlement and
        no queue call happen here.  A genuine failure poisons only the authentic
        winning owner and selects C3d1 refusal-only housekeeping.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return AuthorityPolicyV2FinalizationOutcome(
                status="finalization_pending", reason="transaction_owned",
                attempt_id=attempt_id,
            )
        authentic_owner = self._v2_contender_is_authentic_owner(
            attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
            origin_boot_id=origin_boot_id,
        )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._finalize_v2_continuation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                max_revise_rounds=max_revise_rounds, now=now,
            )
            if outcome.status == "finalization_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            if authentic_owner:
                # Safe same-process fallback: the process-local marker keeps
                # safely attributable refusal housekeeping possible even when
                # the durable obligation diagnostic cannot be written.
                self._mark_v2_refusal_failure_authority(attempt_id, owner_attempt_id)
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="final_commit_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
                origin_boot_id=origin_boot_id if not authentic_owner else None,
                poison=authentic_owner,
            )
            raise

    # -- THR-229 checkpoint C3d2: the separate exact post-final receipt
    # settlement transaction/read.  It authenticates the complete final
    # J/K/P/V/E/N/D evidence and attribution BEFORE any settlement write.

    def _authenticate_v2_settlement_final_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> tuple[str | None, dict | None]:
        # Settle authenticates the COMPLETE durable post-final evidence (J/R/K/P/V,
        # pinned binding history, persisted assessment/decision identity, the
        # consumed candidate/evaluation, BOTH halves of a0..a3, the final E/N/D
        # and the complete final audit set) AND the actual Pending/current causal
        # owner/cancellation projection BEFORE any settlement write; a partial
        # final row or a stale/foreign owner refuses read-only.
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            # Map the final-row classification into the bounded settlement
            # pending vocabulary (a mismatched live generation is unsafe
            # evidence, never a new settlement).
            return ("evidence_drift" if code == "evidence_drift" else "identity_mismatch"), None
        assert ctx is not None
        if ctx["notification"].state != "needed" or ctx["dispatch"].state != "pending":
            return "identity_mismatch", None
        if not self._authenticate_v2_post_final_task_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        ):
            return "identity_mismatch", None
        return None, ctx

    def _v2_recovery_completion_rows(
        self, rows, *, result_id: int, manager_session_id: str,
    ) -> list[dict]:
        """Potentially recovery-settlement-related completion rows.

        Enumerated BEFORE discriminator filtering and classified from FIELD
        PRESENCE.  A recovery-shaped completion row (the ``_recovery_session_id``
        key OR the settlement-only ``result_id``/``session_id`` keys) is
        identity-related whenever any present result reference matches the exact
        current result or is malformed, or any present recovery/session
        reference matches the exact current session or is malformed.  A
        null/bool/list/string recovery marker with the exact integer result
        reference, or a stringified result reference with the exact session, can
        therefore never hide from the cardinality check.  Ordinary (non-recovery)
        completion evidence and unrelated genuine historical sessions are a
        different evidence class and are not counted here.
        """
        related = []
        for row in rows:
            if self._v2_completion_row_is_recovery_related(
                row["payload"], result_id=result_id,
                manager_session_id=manager_session_id,
            ):
                related.append(row)
        return related

    def _v2_settled_rows(
        self, rows, *, attempt, candidate, envelope, notification,
    ) -> list[dict]:
        """Potentially settlement-related settled rows (before filtering).

        The settled payload carries one closed causal tuple.  A row is related
        when ANY present identity field matches the exact authenticated
        attempt/candidate/envelope/notification/generation/result/session or is
        malformed/conflicting; only a row whose every present identity is
        well-typed and provably a DIFFERENT value is unrelated.  A row with no
        causal identity at all cannot be established as unrelated.
        """
        identities = (
            ("_result_row_id", candidate.result_id, "int"),
            ("result_id", candidate.result_id, "int"),
            ("attempt_id", attempt.attempt_id, "str"),
            ("candidate_id", candidate.candidate_id, "str"),
            ("envelope_id", envelope.envelope_id, "str"),
            ("notification_id", notification.notification_id, "str"),
            ("generation_id", notification.notification_id, "str"),
            ("recovery_session_id", candidate.manager_session_id, "str"),
            ("manager_session_id", candidate.manager_session_id, "str"),
        )
        related = []
        for row in rows:
            payload = row["payload"]
            if not isinstance(payload, dict):
                related.append(row)
                continue
            states = [
                self._v2_identity_observation(payload, field, expected, kind)
                for field, expected, kind in identities
            ]
            if not any(state != "absent" for state in states):
                related.append(row)
                continue
            if any(state in ("match", "malformed") for state in states):
                related.append(row)
        return related

    def _authenticate_v2_settlement_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
        manager_session_id: str, result_row, attempt, candidate, envelope,
        notification,
    ) -> bool:
        """Authenticate the exact existing completion + recovery-settled audits.

        Every closed payload value/type is bound to the exact authenticated
        attempt/candidate/envelope/notification/generation/result/root/agent/
        session and to the actual persisted completion projection.  Comparisons
        are type-sensitive (``true`` never equals integer ``1``).  Potentially
        identity-related rows are enumerated BEFORE discriminator filtering, so
        malformed/conflicting/duplicate rows cannot disappear into an apparent
        absence; unrelated genuine historical sessions are never duplicates.
        """
        completion = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, "completion_report",
            include_opaque=True,
        )
        if completion is None:
            return False
        related = self._v2_recovery_completion_rows(
            completion, result_id=result_id, manager_session_id=manager_session_id,
        )
        if len(related) != 1:
            return False
        expected_completion = self._v2_settlement_completion_payload(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_row=result_row, result_id=result_id,
            manager_session_id=manager_session_id,
        )
        if not self._v2_json_type_sensitive_equal(
            related[0]["payload"], expected_completion,
        ):
            return False
        settled = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION,
            attempt_id=None, include_opaque=True,
        )
        if settled is None:
            return False
        related_settled = self._v2_settled_rows(
            settled, attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification,
        )
        if len(related_settled) != 1:
            return False
        expected_settled = self._v2_settlement_settled_payload(
            attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification,
        )
        return self._v2_json_type_sensitive_equal(
            related_settled[0]["payload"], expected_settled,
        )

    def _authenticate_v2_settlement_pre_state_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
        manager_session_id: str, attempt, candidate, envelope, notification,
    ) -> bool:
        """True only when NO settlement-owned terminal evidence exists yet.

        Initial ``callback_accepted`` settlement writes the completion and
        recovery-settled audits atomically; contradictory or partially present
        terminal evidence refuses without synthesizing, replacing or adding
        evidence.  Rows are enumerated BEFORE discriminator filtering and a
        non-object body is retained, so a malformed/conflicting/duplicate row
        can never be filtered away into an apparent clean pre-state.  Only
        recovery-settlement-owned rows are considered; the ordinary v2 producer
        audit and unrelated genuine historical sessions are not
        settlement-owned.
        """
        completion = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, "completion_report",
            include_opaque=True,
        )
        if completion is None:
            return False
        if self._v2_recovery_completion_rows(
            completion, result_id=result_id, manager_session_id=manager_session_id,
        ):
            return False
        settled = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION,
            attempt_id=None, include_opaque=True,
        )
        if settled is None:
            return False
        return not self._v2_settled_rows(
            settled, attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification,
        )

    def _v2_settlement_completion_payload(
        self, *, root_task_id: str, manager_agent: str, result_row,
        result_id: int, manager_session_id: str,
    ) -> dict:
        raw_decision = result_row["decision_json"]
        try:
            decision = json.loads(raw_decision) if raw_decision else None
        except Exception:
            decision = None
        return {
            "task_id": root_task_id,
            "agent": manager_agent,
            "result_id": result_id,
            "session_id": manager_session_id,
            "status": result_row["status"],
            "output_summary": result_row["output_summary"],
            "confidence": result_row["confidence_score"],
            "decision": decision,
            "_recovery_session_id": manager_session_id,
            "_result_row_id": result_id,
        }

    def _v2_settlement_settled_payload(
        self, *, attempt, candidate, envelope, notification,
    ) -> dict:
        return {
            "attempt_id": attempt.attempt_id,
            "candidate_id": candidate.candidate_id,
            "envelope_id": envelope.envelope_id,
            "notification_id": notification.notification_id,
            "generation_id": notification.notification_id,
            "result_id": candidate.result_id,
            "recovery_session_id": candidate.manager_session_id,
            "root_task_id": candidate.root_task_id,
            "manager_agent": candidate.manager_agent,
            "manager_session_id": candidate.manager_session_id,
            "_result_row_id": candidate.result_id,
        }

    def _v2_ordinary_completion_payload(
        self, *, root_task_id: str, manager_agent: str, result_row,
    ) -> dict | None:
        """The exact normalized ordinary payload the real producer writes.

        Reconstructs the persisted report through the SAME production
        reconstruction the completion producer uses and appends the v2-only
        result/session attribution, so a hand-built partial stand-in or an
        identical old-session body can never match the current event.
        """
        from runtime.orchestrator.orchestrator import completion_report_from_result_row

        try:
            report = completion_report_from_result_row(
                root_task_id, dict(result_row), fallback_agent=manager_agent,
            )
        except Exception:
            return None
        return {
            **report.model_dump(),
            "_result_row_id": result_row["id"],
            "_result_session_id": result_row["session_id"],
        }

    def _authenticate_v2_ordinary_completion_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_row,
    ) -> bool:
        """Require exactly one REAL ordinary completion_report for the result.

        Ordinary callbacks have no recovery receipt: the accepted
        completion-consumer seam's actual completion_report is required instead,
        scoped to the exact causal result/session the v2 producer attributed.
        A legitimate earlier manager completion (or an identical old-session
        body, or a unattributed historical row) can never stand in for the
        current event; a recovery-shaped report is never fabricated or accepted
        here, and the payload is compared type-sensitively against the real
        persisted normalized report.
        """
        result_id = result_row["id"]
        rows = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, "completion_report",
            include_opaque=True,
        )
        if rows is None:
            return False
        if self._v2_recovery_completion_rows(
            rows, result_id=result_id, manager_session_id=result_row["session_id"],
        ):
            # A recovery-shaped receipt for this exact result/session is never
            # ordinary authority, even when no Q currently matches it.
            return False
        current = [
            row for row in rows
            if self._v2_completion_row_is_ordinary_related(
                row["payload"], result_id=result_id,
                manager_session_id=result_row["session_id"],
            )
        ]
        if len(current) != 1:
            return False
        expected = self._v2_ordinary_completion_payload(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_row=result_row,
        )
        if expected is None:
            return False
        return self._v2_json_type_sensitive_equal(current[0]["payload"], expected)

    # A recovery receipt is an established unrelated terminal history only in
    # one of these durable end states.  Any other (nonterminal or unknown) state
    # is handled conservatively and still fails closed.
    _V2_TERMINAL_RECEIPT_STATES = frozenset({
        "callback_consumed", "superseded", "expired", "restart_settled",
    })

    def _v2_receipt_blocks_ordinary(
        self, receipt, *, result_id: int, manager_session_id: str,
    ) -> bool:
        """Whether one recovery receipt still vetoes the current ordinary result.

        A receipt is potentially related to the current ordinary completion when
        any of its ``recovery_session_id`` / ``origin_session_id`` /
        ``accepted_result_session_id`` identities matches the current session, or
        its ``accepted_result_id`` matches the current result.  A malformed
        identity value and any nonterminal/unknown state cannot be established as
        unrelated.  Only a receipt that is an ESTABLISHED TERMINAL history with
        every identity well-typed and provably disjoint is non-blocking.
        """
        state = receipt["state"]
        if not isinstance(state, str) or state not in self._V2_TERMINAL_RECEIPT_STATES:
            return True
        origin = receipt["origin_session_id"]
        recovery = receipt["recovery_session_id"]
        if not isinstance(origin, str) or not isinstance(recovery, str):
            return True
        if origin == manager_session_id or recovery == manager_session_id:
            return True
        accepted_session = receipt["accepted_result_session_id"]
        if accepted_session is not None:
            if not isinstance(accepted_session, str) or accepted_session == manager_session_id:
                return True
        accepted_result = receipt["accepted_result_id"]
        if accepted_result is not None:
            if not self._v2_is_int(accepted_result) or accepted_result == result_id:
                return True
        return False

    @_synchronized
    def settle_authority_policy_v2_continuation_receipt(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, recovery_session_id: str | None = None,
        accepted_result_id: int | None = None,
        accepted_result_session_id: str | None = None,
    ) -> AuthorityPolicyV2SettlementOutcome:
        """Separate exact post-final receipt-settlement transaction/read.

        For genuine recovery the REAL exact root/agent/recovery_session_id/
        accepted_result_id/accepted_result_session_id Q is required; a
        ``callback_accepted`` Q is CASed to ``callback_consumed`` together with
        the required completion/recovery-settled audits.  An already
        ``callback_consumed`` exact Q authenticates the complete existing
        settlement/final evidence read-only and returns ``already_settled_exact``.
        Explicit recovery without a matching Q refuses.  Ordinary absence stays
        ordinary and requires real ordinary completion evidence at the accepted
        completion-consumer seam.  A failed settlement retains Pending/E/N/D/J
        and callback_accepted and permits ONLY exact settlement retry.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _pending(reason: str, **kw) -> AuthorityPolicyV2SettlementOutcome:
            return AuthorityPolicyV2SettlementOutcome(
                status="settlement_pending", reason=reason,
                attempt_id=attempt_id, **kw,
            )

        if self._conn.in_transaction:
            return _pending("transaction_owned")
        if recovery_session_id is not None and recovery_session_id != manager_session_id:
            return _pending("identity_mismatch")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            code, ctx = self._authenticate_v2_settlement_final_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
            )
            if code is not None:
                self._conn.rollback()
                return _pending(code)
            assert ctx is not None
            attempt = ctx["attempt"]
            candidate = ctx["candidate"]
            envelope = ctx["envelope"]
            notification = ctx["notification"]
            notification_id = notification.notification_id
            generation_id = ctx["generation_id"]
            result_row = ctx["result_row"]
            base = {
                "candidate_id": candidate.candidate_id,
                "envelope_id": envelope.envelope_id,
                "notification_id": notification_id,
                "generation_id": generation_id,
            }
            receipts = self._conn.execute(
                "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
                (root_task_id, manager_agent),
            ).fetchall()

            if recovery_session_id is not None:
                exact = [
                    receipt for receipt in receipts
                    if receipt["recovery_session_id"] == manager_session_id
                ]
                if len(exact) != 1:
                    self._conn.rollback()
                    return _pending(
                        "receipt_missing" if not exact else "identity_mismatch", **base,
                    )
                receipt = exact[0]
                if (
                    accepted_result_id != result_id
                    or accepted_result_session_id != manager_session_id
                    or receipt["accepted_result_id"] != result_id
                    or receipt["accepted_result_session_id"] != manager_session_id
                ):
                    self._conn.rollback()
                    return _pending("identity_mismatch", **base)
                if receipt["state"] == "callback_accepted":
                    # Initial settlement may only write when NO settlement-owned
                    # terminal evidence already exists for this exact result.  A
                    # contradictory or partially present terminal state refuses
                    # without synthesizing, replacing or adding evidence; the
                    # atomic Q+completion+settled write below is unchanged.
                    if not self._authenticate_v2_settlement_pre_state_uncommitted(
                        root_task_id=root_task_id, manager_agent=manager_agent,
                        result_id=result_id, manager_session_id=manager_session_id,
                        attempt=attempt, candidate=candidate, envelope=envelope,
                        notification=notification,
                    ):
                        self._conn.rollback()
                        return _pending("identity_mismatch", **base)
                    now = _now().isoformat()
                    cursor = self._conn.execute(
                        """UPDATE task_completion_recoveries
                              SET state='callback_consumed',
                                  accepted_result_session_id=?, settled_at=?
                            WHERE id=? AND task_id=? AND agent=?
                              AND recovery_session_id=? AND accepted_result_id=?
                              AND accepted_result_session_id=?
                              AND state='callback_accepted'""",
                        (
                            manager_session_id, now, receipt["id"], root_task_id,
                            manager_agent, manager_session_id, result_id,
                            manager_session_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        self._conn.rollback()
                        return _pending("identity_mismatch", **base)
                    self.insert_audit_log_uncommitted(
                        root_task_id, manager_agent, "completion_report",
                        self._v2_settlement_completion_payload(
                            root_task_id=root_task_id, manager_agent=manager_agent,
                            result_row=result_row, result_id=result_id,
                            manager_session_id=manager_session_id,
                        ),
                    )
                    self.insert_audit_log_uncommitted(
                        root_task_id, manager_agent,
                        AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION,
                        self._v2_settlement_settled_payload(
                            attempt=attempt, candidate=candidate,
                            envelope=envelope, notification=notification,
                        ),
                    )
                    self._conn.commit()
                    return AuthorityPolicyV2SettlementOutcome(
                        status="settled", attempt_id=attempt.attempt_id,
                        recovery=True, receipt_settled=True, **base,
                    )
                if receipt["state"] == "callback_consumed":
                    ok = self._authenticate_v2_settlement_evidence_uncommitted(
                        root_task_id=root_task_id, manager_agent=manager_agent,
                        result_id=result_id, manager_session_id=manager_session_id,
                        result_row=result_row, attempt=attempt,
                        candidate=candidate, envelope=envelope,
                        notification=notification,
                    )
                    self._conn.rollback()
                    if ok:
                        return AuthorityPolicyV2SettlementOutcome(
                            status="already_settled_exact",
                            attempt_id=attempt.attempt_id, recovery=True,
                            receipt_settled=True, **base,
                        )
                    return _pending("identity_mismatch", **base)
                self._conn.rollback()
                return _pending("identity_mismatch", **base)

            # Ordinary absence: no recovery assertion.  An unrelated
            # ESTABLISHED TERMINAL receipt/history neither supplies current
            # authority nor vetoes current ordinary completion, but any
            # potentially related (exact or partially matching/conflicting),
            # malformed, nonterminal or unknown-state receipt still fails closed.
            if any(
                self._v2_receipt_blocks_ordinary(
                    receipt, result_id=result_id,
                    manager_session_id=manager_session_id,
                )
                for receipt in receipts
            ):
                self._conn.rollback()
                return _pending("identity_mismatch", **base)
            if not self._authenticate_v2_ordinary_completion_evidence_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                result_row=result_row,
            ):
                self._conn.rollback()
                return _pending("completion_evidence_missing", **base)
            self._conn.rollback()
            return AuthorityPolicyV2SettlementOutcome(
                status="settled", attempt_id=attempt.attempt_id,
                recovery=False, receipt_settled=False, **base,
            )
        except Exception:
            self._conn.rollback()
            raise

    # -- THR-229 checkpoint C3d4b: read-only recovery-receipt identity discovery
    # used by the real post-final orchestration seams (accepted-completion
    # recovery and startup) to decide whether settlement runs through the genuine
    # recovery branch (an exact durable Q exists) or the ordinary branch.  It
    # performs NO transition and grants NO authority: the public settlement
    # writer independently re-reads and authenticates the complete evidence, so a
    # stale/partial identity here can only produce a refusal.

    @_synchronized
    def get_authority_policy_v2_settlement_receipt_identity(
        self, *, root_task_id: str, manager_agent: str,
        manager_session_id: str | None = None, result_id: int | None = None,
    ) -> dict | None:
        """Return the exact recovery-receipt (Q) identity for a root, or ``None``.

        ``None`` means the root has NO settlement-relevant recovery receipt for
        this manager: the finalized continuation was settled through the genuine
        ordinary completion-evidence branch (an unrelated ESTABLISHED TERMINAL
        historical receipt is history, not current settlement).  Otherwise the
        bounded exact ``recovery_session_id`` / ``accepted_result_id`` /
        ``accepted_result_session_id`` / ``state`` columns are returned for the
        settlement writer's recovery branch.  ``{"conflict": True}`` means the
        receipt set cannot be reduced to exactly one receipt that IS the exact
        current causal recovery identity, which the caller must refuse rather
        than guess at.

        When the caller supplies the exact expected ``manager_session_id`` /
        ``result_id`` (the finalized continuation's immutable E/R/session
        identity), discovery reuses the SAME potentially-related classification
        as the ordinary settlement branch (``_v2_receipt_blocks_ordinary``):

        * exactly one potentially-related receipt that is the exact current
          recovery identity (``recovery_session_id`` and
          ``accepted_result_session_id`` equal the current manager session and
          ``accepted_result_id`` equal the current result) is returned;
        * zero potentially-related receipts means a genuine unrelated
          ESTABLISHED TERMINAL history, which neither supplies current authority
          nor blocks the ordinary branch, so ``None`` is returned;
        * any other shape -- a second potentially-related receipt or one related
          (partial/malformed/nonterminal/unknown-state) receipt that is not the
          exact current recovery identity -- is a bounded conflict, so a related
          Q can never disappear behind an unrelated sibling or the ordinary
          branch.

        Without the expected identity the bounded conservative contract is
        preserved (exactly one receipt, else conflict).  This is read-only: it
        performs NO transition and grants NO authority; the public settlement
        writer independently re-reads and authenticates the complete evidence.
        """
        rows = self._conn.execute(
            "SELECT * FROM task_completion_recoveries "
            "WHERE task_id=? AND agent=? ORDER BY id",
            (root_task_id, manager_agent),
        ).fetchall()
        if not rows:
            return None
        if manager_session_id is None or result_id is None:
            if len(rows) != 1:
                return {"conflict": True}
            return self._v2_receipt_identity_of(rows[0])
        blocking = [
            receipt for receipt in rows
            if self._v2_receipt_blocks_ordinary(
                receipt, result_id=result_id,
                manager_session_id=manager_session_id,
            )
        ]
        if not blocking:
            return None
        exact = [
            receipt for receipt in blocking
            if receipt["recovery_session_id"] == manager_session_id
            and receipt["accepted_result_id"] == result_id
            and receipt["accepted_result_session_id"] == manager_session_id
        ]
        if len(blocking) != 1 or len(exact) != 1:
            return {"conflict": True}
        return self._v2_receipt_identity_of(exact[0])

    @staticmethod
    def _v2_receipt_identity_of(row) -> dict:
        """Bounded exact recovery-receipt identity columns for one Q row."""
        return {
            "conflict": False,
            "state": row["state"],
            "recovery_session_id": row["recovery_session_id"],
            "accepted_result_id": row["accepted_result_id"],
            "accepted_result_session_id": row["accepted_result_session_id"],
        }

    # -- THR-229 checkpoint C3d3a: callable authenticated publication
    # bookkeeping.  Discovery is a read-only listing; claim/acknowledge/failure
    # and invalidation are Database-owned synchronized transactions.  NONE of
    # these methods calls the queue, launches work, reevaluates a candidate,
    # remints an envelope, changes a task status or admits a generation: the
    # real publisher plus the non-bypassable generation-admission fallback and
    # the next-result spend remain later units.

    def _authenticate_v2_publication_final_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, allow_post_admission: bool = False,
    ) -> tuple[str | None, dict | None]:
        """The minimum post-final seam publication bookkeeping needs.

        Reuses the COMPLETE post-final authentication (J/R/K/P/V, the pinned
        binding history, a0..a3 and the final E/N/D + audit set) plus the actual
        Pending causal-owner projection, but accepts the notification in
        ``needed`` (first claim) or a reclaimable ``publishing``/``published``
        state.  ``admitted``/``settled`` are only admitted for the narrow
        post-admission acknowledgement classification (``allow_post_admission``)
        and are never publishable; ``invalidated`` never is.  It additionally
        requires the read-only settlement proof (genuine ordinary completion
        evidence OR the exact real ``callback_consumed`` Q plus both complete
        settlement audits), so publication cannot proceed on a deleted/absent/
        merely-accepted settlement.  The settlement reader's own ``needed``-only
        contract is untouched.
        """
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return (
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            ), None
        assert ctx is not None
        if ctx["dispatch"].state != "pending":
            return "evidence_drift", None
        if ctx["notification"].notification_id != ctx["dispatch"].generation_id:
            return "evidence_drift", None
        allowed = {"needed", "publishing", "published"}
        if allow_post_admission:
            allowed = allowed | {"admitted", "settled"}
        if ctx["notification"].state not in allowed:
            return "evidence_drift", None
        if not self._authenticate_v2_post_final_task_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        ):
            return "identity_mismatch", None
        # Publication may only proceed on a GENUINELY settled final generation:
        # real ordinary completion evidence OR the exact real callback_consumed
        # recovery Q plus both complete settlement audits.  This is read-only and
        # never transitions Q or fabricates a receipt.
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=ctx["attempt"], candidate=ctx["candidate"],
            envelope=ctx["envelope"], notification=ctx["notification"],
            result_row=ctx["result_row"],
        ):
            return "evidence_drift", None
        return None, ctx

    def _authenticate_v2_publication_settlement_proof_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, attempt, candidate, envelope, notification, result_row,
    ) -> bool:
        """Read-only proof that the final generation was GENUINELY settled.

        Accepted evidence is exactly one of: the real ordinary
        ``completion_report`` for the exact causal result/session (no related
        receipt blocking it), OR the exact real ``callback_consumed`` Q for this
        root/agent/recovery session plus BOTH complete settlement audits.  A
        ``callback_accepted`` Q, an explicit missing Q, a mixed/malformed/
        related-conflicting Q or an incomplete settlement audit set is not proof
        and refuses with no new publication writes; a related/nonterminal Q also
        vetoes the ordinary branch (an unrelated established terminal receipt
        stays non-blocking).  This seam performs no settlement write and never
        calls a transaction-owning public settlement method or transitions Q.
        """
        receipts = self._conn.execute(
            "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
            (root_task_id, manager_agent),
        ).fetchall()
        # Ordinary completion authority exists ONLY while no potentially related
        # receipt still blocks it -- exactly the accepted settlement rule.  A
        # related exact/partial/malformed or nonterminal/unknown-state Q can
        # therefore never be hidden behind replayed ordinary evidence, while an
        # unrelated ESTABLISHED TERMINAL receipt remains non-blocking.
        blocking = [
            receipt for receipt in receipts
            if self._v2_receipt_blocks_ordinary(
                receipt, result_id=result_id,
                manager_session_id=manager_session_id,
            )
        ]
        if not blocking and self._authenticate_v2_ordinary_completion_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_row=result_row,
        ):
            return True
        # Exact recovery proof: the ONE potentially-related receipt must BE the
        # exact real ``callback_consumed`` Q.  An additional related, malformed
        # or nonterminal receipt is a conflict, so mixed/conflicting evidence can
        # never be hidden by the single exact Q either.
        if len(blocking) != 1:
            return False
        receipt = blocking[0]
        if receipt["recovery_session_id"] != manager_session_id:
            return False
        if receipt["state"] != "callback_consumed":
            return False
        if (
            receipt["accepted_result_id"] != result_id
            or receipt["accepted_result_session_id"] != manager_session_id
        ):
            return False
        return self._authenticate_v2_settlement_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_id=result_id, manager_session_id=manager_session_id,
            result_row=result_row, attempt=attempt, candidate=candidate,
            envelope=envelope, notification=notification,
        )

    def _authenticate_v2_retained_claim_boot_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, publication_attempt: int,
        expected_boot: str | None,
    ) -> str | None:
        """Return the boot of the ONE closed retained ``publish_claimed`` event.

        The exact prior claim is state-required evidence before any reclaim,
        initial failure recording or exact failure replay: a missing, mutated,
        duplicated or conflicting retained claim refuses with no repair-by-
        reinsertion.  ``expected_boot`` pins the bound publisher when the
        notification still carries it; when a recorded failure cleared the lease
        the retained claim's own well-typed boot is returned so the caller can
        bind the state-required failure evidence to that same publisher.
        ``None`` means the retained claim evidence does not authenticate.
        """
        claims = self._v2_authenticate_publication_claim_history_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, candidate_id=candidate_id, result_id=result_id,
            envelope_id=envelope_id, notification_id=notification_id,
            generation_id=generation_id, publication_attempt=publication_attempt,
        )
        if claims is None:
            return None
        claim = claims[publication_attempt]
        boot = claim["publisher_boot_id"]
        expected = self._v2_publication_audit_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED,
            attempt_id=attempt_id, candidate_id=candidate_id, result_id=result_id,
            envelope_id=envelope_id, notification_id=notification_id,
            generation_id=generation_id, publication_attempt=publication_attempt,
            publisher_boot_id=boot,
        )
        if not self._v2_json_type_sensitive_equal(claim, expected):
            return None
        if expected_boot is not None and boot != expected_boot:
            return None
        return boot

    def _authenticate_v2_publication_stage_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, publication_attempt: int,
        publisher_boot_id: str, stage: str,
    ) -> bool:
        """Exactly one closed state-required ``published``/``publish_failed``."""
        return self._authenticate_v2_publication_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id,
            expected=self._v2_publication_audit_payload(
                stage=stage, attempt_id=attempt_id, candidate_id=candidate_id,
                result_id=result_id, envelope_id=envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id,
            ),
        )

    def _v2_publication_audit_payload(
        self, *, stage: str, attempt_id: str, candidate_id: str, result_id: int,
        envelope_id: str, notification_id: str, generation_id: str,
        publication_attempt: int | None = None,
        publisher_boot_id: str | None = None,
    ) -> dict:
        """One closed publication/invalidation result-stage payload."""
        payload = {
            "stage": stage,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "result_id": result_id,
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
        }
        if publication_attempt is not None:
            payload["publication_attempt"] = publication_attempt
        if publisher_boot_id is not None:
            payload["publisher_boot_id"] = publisher_boot_id
        return payload

    def _v2_publication_event_identity_keys(self, stage: str) -> set[str]:
        """The exact closed key set of one publication/invalidation event.

        ``invalidated`` is the one closed stage with no publication-attempt/boot
        discriminator; every other publication stage carries both.
        """
        keys = {
            "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
            "notification_id", "generation_id",
        }
        if stage != AUTHORITY_POLICY_V2_RESULT_STAGE_INVALIDATED:
            keys = keys | {"publication_attempt", "publisher_boot_id"}
        return keys

    def _v2_authenticated_publication_events_by_attempt_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        stage: str, candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, reference_attempt: int,
    ) -> dict[int, dict] | None:
        """Authenticate the complete ``{P: one closed event}`` history for G.

        Every POTENTIALLY related row for ``stage`` must be a closed event for
        THIS exact causal identity carrying a well-typed positive
        ``publication_attempt`` in ``1..reference_attempt``.  ``None`` means any
        row was opaque, extra-key, foreign, malformed, duplicated or carried an
        impossible zero/negative/future P -- so a different numeric
        discriminator alone can never be mistaken for legitimately distinct
        prior publication history.  A returned map holds at most one row per P.
        """
        related = self._v2_related_publication_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, stage=stage, candidate_id=candidate_id,
            result_id=result_id, envelope_id=envelope_id,
            notification_id=notification_id, generation_id=generation_id,
        )
        if related is None:
            return None
        expected_keys = self._v2_publication_event_identity_keys(stage)
        by_attempt: dict[int, dict] = {}
        for payload in related:
            if not isinstance(payload, dict) or set(payload.keys()) != expected_keys:
                return None
            if payload.get("stage") != stage:
                return None
            for field, value in (
                ("attempt_id", attempt_id), ("candidate_id", candidate_id),
                ("result_id", result_id), ("envelope_id", envelope_id),
                ("notification_id", notification_id),
                ("generation_id", generation_id),
            ):
                if not self._v2_json_type_sensitive_equal(payload.get(field), value):
                    return None
            attempt_value = payload.get("publication_attempt")
            if not self._v2_is_int(attempt_value):
                return None
            if attempt_value < 1 or attempt_value > reference_attempt:
                return None
            if attempt_value in by_attempt:
                return None
            boot = payload.get("publisher_boot_id")
            if not isinstance(boot, str) or not boot:
                return None
            by_attempt[attempt_value] = payload
        return by_attempt

    def _v2_authenticate_publication_claim_history_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, publication_attempt: int,
    ) -> dict[int, dict] | None:
        """The one authentic closed claim history: exactly ``{1..P}``, one each.

        Every committed claim increments P and appends exactly one
        ``publish_claimed`` event, so a genuine generation retains a contiguous,
        duplicate-free claim set ending at its current P.  Missing, mutated,
        duplicated, foreign, malformed or impossible-P claim evidence returns
        ``None`` -- there is no repair by reinsertion and no future/zero/negative
        attempt is ever accepted as prior history.
        """
        if not self._v2_is_int(publication_attempt) or publication_attempt < 1:
            return None
        claims = self._v2_authenticated_publication_events_by_attempt_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id,
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED,
            candidate_id=candidate_id, result_id=result_id,
            envelope_id=envelope_id, notification_id=notification_id,
            generation_id=generation_id, reference_attempt=publication_attempt,
        )
        if claims is None:
            return None
        # COMPLETENESS WITHOUT AN INTEGER-SIZED ALLOCATION: ``by_attempt`` is a
        # mapping whose keys are already unique and each authenticated to be in
        # ``1..publication_attempt``.  A unique in-range key set with exactly
        # ``publication_attempt`` members is therefore provably ``{1..P}`` --
        # contiguous and duplicate-free -- without materializing a set/list of
        # size P.  A huge (e.g. 2147483646) or otherwise impossible P can no
        # longer request proportional memory; the work stays proportional to the
        # retained audit events actually read.
        if len(claims) != publication_attempt:
            return None
        return claims

    def _v2_related_publication_events_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        stage: str, candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> list[dict] | None:
        """Enumerate POTENTIALLY related publication events for one exact G.

        Identity-scoped enumeration happens BEFORE any discriminator filtering,
        so an appended duplicate whose ``attempt_id`` is null/missing/distinct
        -- or whose candidate/result/envelope/notification/generation reference
        is wrong or malformed -- can never be discarded before classification.
        A row is independently unrelated ONLY when every present causal
        reference is well-typed and provably DIFFERENT; an opaque (non-object)
        body is never unrelated, and a row carrying the expected stage with no
        causal reference at all cannot be established as unrelated.

        ``None`` means the enumeration was unreadable or contained an opaque
        body: the caller must fail closed.  A returned list is every row that is
        potentially related to this exact generation, including legitimately
        distinct prior publication attempts (which the caller filters on the
        well-typed ``publication_attempt`` value).
        """
        rows = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            attempt_id=None, include_opaque=True,
        )
        if rows is None:
            return None
        identities = (
            ("attempt_id", attempt_id, "str"),
            ("candidate_id", candidate_id, "str"),
            ("result_id", result_id, "int"),
            ("envelope_id", envelope_id, "str"),
            ("notification_id", notification_id, "str"),
            ("generation_id", generation_id, "str"),
        )
        related: list[dict] = []
        for row in rows:
            payload = row["payload"]
            if not isinstance(payload, dict):
                return None
            if payload.get("stage") != stage:
                # A different closed stage is legitimate distinct history.
                continue
            states = [
                self._v2_identity_observation(payload, field, value, kind)
                for field, value, kind in identities
            ]
            if all(state == "distinct" for state in states):
                # Every present causal reference is well-typed and provably
                # different from this exact generation.
                continue
            related.append(payload)
        return related

    def _authenticate_v2_publication_event_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        expected: dict,
    ) -> bool:
        """Exactly one authentic CLOSED publication/invalidation event.

        Identity evidence is evaluated at the READER before any discriminator
        filtering: an appended duplicate whose ``attempt_id`` is null/missing/
        distinct, or whose generation/attempt discriminator conflicts, is a
        RELATED conflict rather than an unrelated row -- a wrong discriminator
        cannot hide behind the filter.  Several legitimate reclaims of the same
        generation are preserved: only a well-typed DIFFERENT
        ``publication_attempt`` value is a legitimately distinct prior ``P``
        event, and a row whose every present causal reference is well-typed and
        provably different is independently unrelated.  A missing, duplicated,
        mutated, foreign or extra-key event refuses, and only an authentic
        earlier attempt with a valid P in ``1..current`` is prior history.
        """
        stage = expected["stage"]
        if "publication_attempt" in expected:
            reference = expected["publication_attempt"]
            if not self._v2_is_int(reference) or reference < 1:
                return False
            if stage == AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED:
                history = (
                    self._v2_authenticate_publication_claim_history_uncommitted(
                        root_task_id=root_task_id, manager_agent=manager_agent,
                        attempt_id=attempt_id,
                        candidate_id=expected["candidate_id"],
                        result_id=expected["result_id"],
                        envelope_id=expected["envelope_id"],
                        notification_id=expected["notification_id"],
                        generation_id=expected["generation_id"],
                        publication_attempt=reference,
                    )
                )
            else:
                history = (
                    self._v2_authenticated_publication_events_by_attempt_uncommitted(
                        root_task_id=root_task_id, manager_agent=manager_agent,
                        attempt_id=attempt_id, stage=stage,
                        candidate_id=expected["candidate_id"],
                        result_id=expected["result_id"],
                        envelope_id=expected["envelope_id"],
                        notification_id=expected["notification_id"],
                        generation_id=expected["generation_id"],
                        reference_attempt=reference,
                    )
                )
            if history is None or reference not in history:
                return False
            return self._v2_json_type_sensitive_equal(history[reference], expected)
        related = self._v2_related_publication_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, stage=stage,
            candidate_id=expected["candidate_id"], result_id=expected["result_id"],
            envelope_id=expected["envelope_id"],
            notification_id=expected["notification_id"],
            generation_id=expected["generation_id"],
        )
        if related is None:
            return False
        if len(related) != 1:
            return False
        payload = related[0]
        if not isinstance(payload, dict):
            return False
        if set(payload.keys()) != self._v2_publication_event_identity_keys(stage):
            return False
        return self._v2_json_type_sensitive_equal(payload, expected)

    @_synchronized
    def list_authority_policy_v2_publication_targets(
        self,
    ) -> list[AuthorityPolicyV2PublicationTarget]:
        """Read-only discovery of every needed/publishing/published N with a
        current pending root-dispatch pointer and no generation admission.

        Listing is DISCOVERY, never authority: it deliberately includes rows
        whose lease is still live (or an exact already-consumed receipt history)
        so the caller can decide, and the claim transaction always re-reads and
        authenticates the complete evidence itself.  Unreadable/corrupt rows are
        not surfaced as bounded targets.
        """
        rows = self._conn.execute(
            """SELECT n.notification_id, n.envelope_id, n.candidate_id,
                      n.result_id, n.root_task_id, n.manager_agent,
                      n.manager_session_id, n.selector_id, n.state,
                      n.publication_attempt, n.publisher_boot_id, n.lease_deadline
                 FROM authority_policy_v2_recovery_notifications n
                 JOIN authority_policy_v2_root_dispatch d
                   ON d.generation_id = n.notification_id
                WHERE n.state IN ('needed','publishing','published')
                  AND d.state = 'pending'
                ORDER BY n.created_at, n.notification_id"""
        ).fetchall()
        targets: list[AuthorityPolicyV2PublicationTarget] = []
        for row in rows:
            try:
                targets.append(AuthorityPolicyV2PublicationTarget(**dict(row)))
            except ValidationError:
                continue
        return targets

    def _claim_v2_notification_publication_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, publisher_boot_id: str, now_dt: datetime,
    ) -> AuthorityPolicyV2PublicationClaimOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2PublicationClaimOutcome:
            return AuthorityPolicyV2PublicationClaimOutcome(
                status="publication_pending", reason=reason, **kw,
            )

        code, ctx = self._authenticate_v2_publication_final_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        candidate = ctx["candidate"]
        attempt = ctx["attempt"]
        envelope = ctx["envelope"]
        generation_id = ctx["generation_id"]
        notification_id = notification.notification_id
        base = {
            "notification_id": notification_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": generation_id,
        }
        state = notification.state
        now_iso = now_dt.isoformat()
        if state == "publishing" or state == "published":
            # Reclaim only after verified publisher process death/restart (the
            # bound daemon-process identity differs) or expiry of the 30-second
            # server-clock lease.  A live same-process lease is never stolen.
            reclaimable = (
                notification.publisher_boot_id != publisher_boot_id
                or notification.lease_deadline is None
                or now_iso >= notification.lease_deadline
            )
            if not reclaimable:
                return _pending("lease_live", **base)
            if notification.publication_attempt >= 2147483647:
                return _pending("attempt_overflow", **base)
            # The exact retained prior claim -- and its state-required published/
            # failure evidence -- must authenticate before any reclaim allocates
            # a new P or changes the bound boot.  A null lease alone is not proof
            # of a genuine audited failure, and missing/damaged claim evidence is
            # refused, never repaired by reinsertion.
            retained_boot = self._authenticate_v2_retained_claim_boot_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=notification.publication_attempt,
                expected_boot=notification.publisher_boot_id,
            )
            if retained_boot is None:
                return _pending("evidence_drift", **base)
            state_stage = None
            if state == "published":
                state_stage = AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED
            elif notification.publisher_boot_id is None:
                state_stage = AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED
            if state_stage is not None and not (
                self._authenticate_v2_publication_stage_evidence_uncommitted(
                    root_task_id=root_task_id, manager_agent=manager_agent,
                    attempt_id=attempt.attempt_id,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification_id, generation_id=generation_id,
                    publication_attempt=notification.publication_attempt,
                    publisher_boot_id=retained_boot, stage=state_stage,
                )
            ):
                return _pending("evidence_drift", **base)
        elif state != "needed":
            return _pending("not_publishable", **base)
        prior_attempt = notification.publication_attempt
        if prior_attempt >= 2147483647:
            return _pending("attempt_overflow", **base)
        new_attempt = prior_attempt + 1
        lease_deadline = (
            now_dt + timedelta(seconds=AUTHORITY_POLICY_V2_PUBLICATION_LEASE_SECONDS)
        ).isoformat()
        updated = notification.model_copy(update={
            "state": "publishing",
            "publication_attempt": new_attempt,
            "publisher_boot_id": publisher_boot_id,
            "lease_deadline": lease_deadline,
            "updated_at": now_dt,
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET state='publishing', publication_attempt=?, publisher_boot_id=?,
                      lease_deadline=?, canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state=? AND publication_attempt=?""",
            (
                new_attempt, publisher_boot_id, lease_deadline, canonical,
                snapshot["updated_at"], notification_id, state, prior_attempt,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("identity_mismatch", **base)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            self._v2_publication_audit_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=new_attempt, publisher_boot_id=publisher_boot_id,
            ),
        )
        return AuthorityPolicyV2PublicationClaimOutcome(
            status="claimed", notification_id=notification_id,
            envelope_id=envelope.envelope_id, generation_id=generation_id,
            publication_attempt=new_attempt, publisher_boot_id=publisher_boot_id,
            lease_deadline=lease_deadline,
        )

    @_synchronized
    def claim_authority_policy_v2_notification_publication(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2PublicationClaimOutcome:
        """ONE synchronized publication claim/reclaim transaction.

        CAS ``needed`` -> ``publishing`` (or reclaims a dead/expired
        ``publishing``/``published``) with the current bound daemon-process
        identity and a 30-second server-clock lease, increments the bounded
        positive publication attempt and appends exactly one closed
        ``publish_claimed`` audit for the exact G/P in the SAME transaction.
        Failure restores the previous state/counter/lease and appends nothing.
        No queue call, task mutation, reevaluation or admission happens here.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2PublicationClaimOutcome(
                status="publication_pending", reason="transaction_owned",
            )
        publisher_boot_id = self._v2_process_boot_id
        if not isinstance(publisher_boot_id, str) or not publisher_boot_id:
            return AuthorityPolicyV2PublicationClaimOutcome(
                status="publication_pending", reason="boot_unbound",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._claim_v2_notification_publication_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                publisher_boot_id=publisher_boot_id, now_dt=now_dt,
            )
            if outcome.status == "publication_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2PublicationClaimOutcome(
                status="publication_pending", reason="publication_failed",
            )

    def _ack_v2_notification_publication_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, publication_attempt: int, publisher_boot_id: str,
        now_dt: datetime,
    ) -> AuthorityPolicyV2PublicationAckOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2PublicationAckOutcome:
            return AuthorityPolicyV2PublicationAckOutcome(
                status="ack_pending", reason=reason, **kw,
            )

        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        candidate = ctx["candidate"]
        attempt = ctx["attempt"]
        envelope = ctx["envelope"]
        dispatch = ctx["dispatch"]
        generation_id = ctx["generation_id"]
        notification_id = notification.notification_id
        base = {
            "notification_id": notification_id,
            "generation_id": generation_id,
        }
        if notification_id != dispatch.generation_id:
            return _pending("stale_claim", **base)
        state = notification.state
        if state not in ("admitted", "settled") and dispatch.state != "pending":
            return _pending("stale_claim", **base)
        if state in ("admitted", "settled"):
            # The consumer outran acknowledgement.  With the REAL generation
            # admission producer now present, authenticate the exact admitted/
            # settled reservation plus the durable generation_claimed (and, for
            # settled, the notification_settled) audits, then append ONLY the
            # exact ``publish_returned(P)`` observation for this prior P.  The
            # notification is NEVER regressed to ``published`` and duplicate
            # acknowledgement is either read-only-exact or one observation.
            # Stale P/boot/G, a missing reservation or conflicting evidence
            # refuse with zero mutation.
            if (
                notification.publication_attempt != publication_attempt
                or notification.publisher_boot_id != publisher_boot_id
                or notification.next_session_id is None
                or dispatch.state != "admitted"
                or dispatch.generation_id != notification_id
            ):
                return _pending("stale_claim", **base)
            # Complete retained publication evidence for THIS exact generation:
            # the bounded contiguous ``{1..P}`` claim history, the bound P/boot
            # and every state-required published/failure/return observation,
            # each classified before any discriminator filtering.  A missing,
            # duplicated, foreign, malformed or conflicting claim/observation
            # refuses with the exact admitted/settled residue preserved.
            if not self._authenticate_v2_retained_publication_evidence_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification=notification, generation_id=generation_id,
                allowed_states=("admitted", "settled"),
            ):
                return _pending("evidence_drift", **base)
            # The stage must be GENUINELY settled: real ordinary completion
            # evidence OR the exact real callback_consumed Q plus both complete
            # settlement audits.  A deleted/absent/malformed/conflicting
            # completion or receipt refuses with zero mutation.
            if not self._authenticate_v2_publication_settlement_proof_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                attempt=attempt, candidate=candidate, envelope=envelope,
                notification=notification, result_row=ctx["result_row"],
            ):
                return _pending("evidence_drift", **base)
            admission_claim = self._v2_admission_event_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                next_session_id=notification.next_session_id,
            )
            if not self._authenticate_v2_admission_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, expected=admission_claim,
            ):
                return _pending("admission_evidence_missing", **base)
            if state == "settled":
                admission_settled = self._v2_admission_event_payload(
                    stage=AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
                    attempt_id=attempt.attempt_id,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification_id, generation_id=generation_id,
                    next_session_id=notification.next_session_id,
                )
                if not self._authenticate_v2_admission_event_uncommitted(
                    root_task_id=root_task_id, manager_agent=manager_agent,
                    attempt_id=attempt.attempt_id, expected=admission_settled,
                ):
                    return _pending("admission_evidence_missing", **base)
            else:
                # N admitted requires the PROVABLE ABSENCE of any related
                # notification_settled settlement evidence: a preexisting,
                # duplicate, foreign, malformed or opaque related event is a
                # conflict and is never ignored or repaired by appending the
                # acknowledgement observation.
                if not self._v2_related_result_stage_events_absent_uncommitted(
                    root_task_id=root_task_id, manager_agent=manager_agent,
                    attempt_id=attempt.attempt_id,
                    stage=AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification_id, generation_id=generation_id,
                ):
                    return _pending("evidence_drift", **base)
            observed = self._v2_publication_audit_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id,
            )
            # Explicit THREE-WAY classification of every POTENTIALLY related
            # publish_returned observation, classified BEFORE any attempt/G/P/
            # boot filtering.  Zero authentic-related observations permits
            # exactly ONE insert after every prerequisite above authenticated;
            # exactly one byte/type/closed-shape-correct observation is a
            # read-only exact replay; any malformed/duplicate/conflicting/
            # opaque/extra-key/foreign-with-an-exact-reference observation
            # refuses with the exact prior residue.  A single authenticator's
            # ``False`` return is NEVER read as permission to append.
            related_returned = self._v2_related_publication_events_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id,
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED,
                candidate_id=candidate.candidate_id, result_id=result_id,
                envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
            )
            if related_returned is None:
                return _pending("evidence_drift", **base)
            if len(related_returned) == 0:
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent,
                    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION, observed,
                )
            elif len(related_returned) == 1:
                payload = related_returned[0]
                if (
                    not isinstance(payload, dict)
                    or set(payload.keys())
                    != self._v2_publication_event_identity_keys(
                        AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED
                    )
                    or not self._v2_json_type_sensitive_equal(payload, observed)
                ):
                    return _pending("evidence_drift", **base)
            else:
                return _pending("evidence_drift", **base)
            return AuthorityPolicyV2PublicationAckOutcome(
                status="publish_returned", state=state,
                publication_attempt=publication_attempt, **base,
            )
        if state not in ("publishing", "published"):
            return _pending("stale_claim", **base)
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification, result_row=ctx["result_row"],
        ):
            return _pending("evidence_drift", **base)
        if not self._authenticate_v2_post_final_task_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        ):
            return _pending("identity_mismatch", **base)
        if (
            notification.publication_attempt != publication_attempt
            or notification.publisher_boot_id != publisher_boot_id
        ):
            return _pending("stale_claim", **base)
        if not self._authenticate_v2_publication_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id,
            expected=self._v2_publication_audit_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id,
            ),
        ):
            return _pending("evidence_drift", **base)
        if state == "published":
            if self._authenticate_v2_publication_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id,
                expected=self._v2_publication_audit_payload(
                    stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED,
                    attempt_id=attempt.attempt_id,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification_id, generation_id=generation_id,
                    publication_attempt=publication_attempt,
                    publisher_boot_id=publisher_boot_id,
                ),
            ):
                return AuthorityPolicyV2PublicationAckOutcome(
                    status="published", state="published",
                    publication_attempt=publication_attempt, **base,
                )
            return _pending("evidence_drift", **base)
        updated = notification.model_copy(update={
            "state": "published", "updated_at": now_dt,
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET state='published', canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state='publishing'
                  AND publication_attempt=? AND publisher_boot_id=?""",
            (
                canonical, snapshot["updated_at"], notification_id,
                publication_attempt, publisher_boot_id,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("stale_claim", **base)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            self._v2_publication_audit_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id,
            ),
        )
        return AuthorityPolicyV2PublicationAckOutcome(
            status="published", state="published",
            publication_attempt=publication_attempt, **base,
        )

    @_synchronized
    def acknowledge_authority_policy_v2_notification_publication(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, publication_attempt: int, publisher_boot_id: str,
    ) -> AuthorityPolicyV2PublicationAckOutcome:
        """Exact publication acknowledgement: ``publishing`` -> ``published``.

        Requires the exact G/publisher boot/P and state ``publishing``,
        authenticates the retained closed claim audit and the actual Pending
        causal owner, CASes to ``published`` and appends exactly one closed
        ``published`` audit atomically.  An exact retry authenticates the
        retained evidence read-only.  A stale P/boot never acknowledges or
        resets a newer claim.  No queue call happens here.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2PublicationAckOutcome(
                status="ack_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._ack_v2_notification_publication_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id, now_dt=now_dt,
            )
            if outcome.status == "ack_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2PublicationAckOutcome(
                status="ack_pending", reason="ack_failed",
            )

    def _record_v2_notification_publication_failure_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, publication_attempt: int, publisher_boot_id: str,
        now_dt: datetime,
    ) -> AuthorityPolicyV2PublicationFailureOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2PublicationFailureOutcome:
            return AuthorityPolicyV2PublicationFailureOutcome(
                status="failure_pending", reason=reason, **kw,
            )

        code, ctx = self._authenticate_v2_publication_final_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        candidate = ctx["candidate"]
        attempt = ctx["attempt"]
        envelope = ctx["envelope"]
        generation_id = ctx["generation_id"]
        notification_id = notification.notification_id
        base = {
            "notification_id": notification_id,
            "generation_id": generation_id,
        }
        if notification.publication_attempt != publication_attempt:
            return _pending("stale_claim", **base)
        if notification.state != "publishing":
            return _pending("stale_claim", **base)
        replay = (
            notification.publisher_boot_id is None
            and notification.lease_deadline is None
        )
        if not replay and notification.publisher_boot_id != publisher_boot_id:
            return _pending("stale_claim", **base)
        # Authenticate the exact retained prior claim BEFORE either the read-only
        # replay or the initial recording.  A cleared lease/boot is not itself
        # proof of a genuine audited failure: the retained claim and the exact
        # publish_failed event (bound to that same publisher) are required, and
        # missing/damaged claim evidence is refused rather than reinserted.
        retained_boot = self._authenticate_v2_retained_claim_boot_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification_id, generation_id=generation_id,
            publication_attempt=publication_attempt,
            expected_boot=(
                publisher_boot_id if replay else notification.publisher_boot_id
            ),
        )
        if retained_boot is None:
            return _pending("evidence_drift", **base)
        expected_failed = self._v2_publication_audit_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification_id, generation_id=generation_id,
            publication_attempt=publication_attempt,
            publisher_boot_id=retained_boot,
        )
        if replay:
            # Exact read-only replay of the already-recorded failure: the
            # retained publish_failed event is the evidence, not a new write.
            if self._authenticate_v2_publication_stage_evidence_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification_id, generation_id=generation_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=retained_boot,
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED,
            ):
                return AuthorityPolicyV2PublicationFailureOutcome(
                    status="failure_recorded", state="publishing",
                    publication_attempt=publication_attempt, **base,
                )
            return _pending("evidence_drift", **base)
        # Bounded audited retry state: keep the monotonic attempt number but
        # clear the lease so the notification is safely reclaimable.  A
        # recording failure below rolls the whole thing back, leaving the
        # prior publishing lease reclaimable by death/expiry.
        updated = notification.model_copy(update={
            "state": "publishing",
            "publisher_boot_id": None,
            "lease_deadline": None,
            "updated_at": now_dt,
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET publisher_boot_id=NULL, lease_deadline=NULL,
                      canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state='publishing'
                  AND publication_attempt=? AND publisher_boot_id=?""",
            (
                canonical, snapshot["updated_at"], notification_id,
                publication_attempt, publisher_boot_id,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("stale_claim", **base)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            expected_failed,
        )
        return AuthorityPolicyV2PublicationFailureOutcome(
            status="failure_recorded", state="publishing",
            publication_attempt=publication_attempt, **base,
        )

    @_synchronized
    def record_authority_policy_v2_notification_publication_failure(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, publication_attempt: int, publisher_boot_id: str,
    ) -> AuthorityPolicyV2PublicationFailureOutcome:
        """Bounded audited queue-failure bookkeeping for one exact claim.

        Requires the exact G/P/boot and state ``publishing``, keeps the monotonic
        publication attempt, clears the publisher lease so the notification stays
        safely reclaimable and appends exactly one closed ``publish_failed``
        audit atomically.  If recording fails, the prior publishing
        lease/state survives reclaimable by death/expiry.  This method does not
        call the queue, reevaluate or mint.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2PublicationFailureOutcome(
                status="failure_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._record_v2_notification_publication_failure_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                publication_attempt=publication_attempt,
                publisher_boot_id=publisher_boot_id, now_dt=now_dt,
            )
            if outcome.status == "failure_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2PublicationFailureOutcome(
                status="failure_pending", reason="failure_failed",
            )

    def _invalidate_v2_notification_generation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, now_dt: datetime,
    ) -> AuthorityPolicyV2InvalidationOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2InvalidationOutcome:
            return AuthorityPolicyV2InvalidationOutcome(
                status="invalidation_pending", reason=reason, **kw,
            )

        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            require_dispatch_generation=False,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        candidate = ctx["candidate"]
        attempt = ctx["attempt"]
        envelope = ctx["envelope"]
        dispatch = ctx["dispatch"]
        generation_id = ctx["generation_id"]
        notification_id = notification.notification_id
        base = {
            "notification_id": notification_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": generation_id,
        }
        expected_audit = self._v2_publication_audit_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_INVALIDATED,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification_id, generation_id=generation_id,
        )
        if notification.state == "invalidated":
            if self._authenticate_v2_publication_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, expected=expected_audit,
            ):
                return AuthorityPolicyV2InvalidationOutcome(
                    status="already_invalidated", notification_state="invalidated",
                    dispatch_state=dispatch.state, **base,
                )
            return _pending("evidence_drift", **base)
        if notification.state == "settled":
            return _pending("not_invalidatable", **base)
        # Invalidate only on an ESTABLISHED cancellation/replacement cause read
        # from CURRENT durable state under this same owned transaction: a
        # cancelled/terminal/replaced-owner causal task, or a stale generation
        # displaced by a replacement root pointer.  A caller request/boolean is
        # never sufficient, partial/malformed identity is not affirmative proof,
        # and a healthy exact owner/pointer stays publishable and unchanged.  A
        # failed publication/audit is never an invented cause.
        task = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        cancelled = task is not None and task["cancelled_at"] is not None
        replaced_owner = False
        if task is not None and not cancelled:
            current_agent = task["assigned_agent"]
            current_session = task["current_session_id"]
            # Affirmative replacement evidence requires WELL-TYPED, non-empty
            # current identity fields that name a different owner/session than
            # the authenticated causal one.  A null/malformed field is missing
            # evidence, never proof of cancellation or replacement, so
            # `assigned_agent=NULL` or `current_session_id=NULL` alone must
            # refuse.
            replaced_owner = bool(
                isinstance(current_agent, str) and current_agent
                and isinstance(current_session, str) and current_session
                and (
                    current_agent != candidate.manager_agent
                    or current_session != candidate.manager_session_id
                )
            )
        stale_displacement = dispatch.generation_id != generation_id
        if not (cancelled or replaced_owner or stale_displacement):
            return _pending("not_invalidatable", **base)
        updated = notification.model_copy(update={
            "state": "invalidated", "updated_at": now_dt,
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET state='invalidated', canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state=?""",
            (canonical, snapshot["updated_at"], notification_id, notification.state),
        )
        if cursor.rowcount != 1:
            return _pending("identity_mismatch", **base)
        # Retire the root dispatch ONLY while it still points at this exact
        # generation; a replacement generation B is never mutated.
        dispatch_state = dispatch.state
        if dispatch.generation_id == generation_id and dispatch.state != "retired":
            d_updated = dispatch.model_copy(update={
                "state": "retired", "updated_at": now_dt,
            })
            d_snapshot = d_updated.model_dump(mode="json")
            d_canonical = authority_policy_v2_canonical_json_bytes(
                d_snapshot
            ).decode("utf-8")
            d_cursor = self._conn.execute(
                """UPDATE authority_policy_v2_root_dispatch
                      SET state='retired', canonical_payload_json=?, updated_at=?
                    WHERE root_task_id=? AND generation_id=? AND state=?""",
                (
                    d_canonical, d_snapshot["updated_at"], root_task_id,
                    generation_id, dispatch.state,
                ),
            )
            if d_cursor.rowcount != 1:
                return _pending("identity_mismatch", **base)
            dispatch_state = "retired"
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            expected_audit,
        )
        return AuthorityPolicyV2InvalidationOutcome(
            status="invalidated", notification_state="invalidated",
            dispatch_state=dispatch_state, **base,
        )

    @_synchronized
    def invalidate_authority_policy_v2_notification_generation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2InvalidationOutcome:
        """Exact cancellation/replacement-owner generation invalidation.

        Authenticates the complete post-final evidence (allowing the root pointer
        to have legitimately advanced to a replacement generation) AND an
        established cancellation/replacement cause read from current durable task
        and pointer state under the same owned transaction, then atomically marks
        the exact old G ``invalidated`` and retires the root dispatch ONLY while
        it still points at that G, with one closed ``invalidated`` audit.  A
        healthy exact causal owner with the pointer still naming G refuses and
        remains publishable and unchanged.  It never mutates a
        cancelled/terminal/replacement task, retires a replacement generation,
        resets N to needed, spends the envelope or creates any
        escalation/notification-routing side effect.  A failed audit rolls its
        own invalidation changes back.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2InvalidationOutcome(
                status="invalidation_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._invalidate_v2_notification_generation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                now_dt=now_dt,
            )
            if outcome.status == "invalidation_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2InvalidationOutcome(
                status="invalidation_pending", reason="invalidation_failed",
            )

    # -- THR-229 checkpoint C3d3b: atomic generation admission and the separate
    # admission-settlement bookkeeping.  Claim is the non-bypassable fence for a
    # pending v2 continuation generation: it authenticates the complete
    # post-final evidence, the settled final generation, the exact tagged token
    # G and the causal Pending owner, then atomically reserves the next runtime
    # session and admits the generation.  Settlement is a separate transaction
    # and grants no launch authority by itself.

    def _v2_admission_event_payload(
        self, *, stage: str, attempt_id: str, candidate_id: str, result_id: int,
        envelope_id: str, notification_id: str, generation_id: str,
        next_session_id: str,
    ) -> dict:
        """One closed generation-admission result-stage payload."""
        return {
            "stage": stage,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "result_id": result_id,
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "next_session_id": next_session_id,
        }

    def _authenticate_v2_admission_event_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        expected: dict,
    ) -> bool:
        """Exactly one authentic CLOSED generation-admission event.

        Reuses the identity-scoped enumeration so a duplicate/foreign/malformed/
        extra-key row can never hide behind a discriminator filter.  Missing or
        conflicting evidence refuses; nothing is repaired by reinsertion.
        """
        related = self._v2_related_publication_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, stage=expected["stage"],
            candidate_id=expected["candidate_id"], result_id=expected["result_id"],
            envelope_id=expected["envelope_id"],
            notification_id=expected["notification_id"],
            generation_id=expected["generation_id"],
        )
        if related is None or len(related) != 1:
            return False
        payload = related[0]
        if not isinstance(payload, dict) or set(payload.keys()) != set(expected.keys()):
            return False
        return self._v2_json_type_sensitive_equal(payload, expected)

    def _v2_related_result_stage_events_absent_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        stage: str, candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str,
    ) -> bool:
        """True only when ZERO potentially-related events exist for ``stage``.

        Enumerates and classifies BEFORE any discriminator filtering (the shared
        identity reader), so a preexisting, malformed, duplicate or foreign
        generation-admission/settlement event can never be mistaken for absence.
        A provably unrelated genuine historical event whose every present causal
        reference is well-typed and distinct stays unrelated; an opaque body or
        an unreadable enumeration fails closed.
        """
        related = self._v2_related_publication_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, stage=stage, candidate_id=candidate_id,
            result_id=result_id, envelope_id=envelope_id,
            notification_id=notification_id, generation_id=generation_id,
        )
        return related is not None and len(related) == 0

    def _authenticate_v2_retained_publication_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification, generation_id: str, allowed_states: tuple[str, ...],
    ) -> bool:
        """The complete retained publication evidence for one exact generation.

        Requires the state-required bounded contiguous ``{1..P}`` claim history,
        the exact bound ``P``/publisher boot, and every retained
        ``published``/``publish_failed``/``publish_returned`` observation as a
        duplicate-free closed event set with a well-typed in-range ``P``.  Every
        row is classified before any discriminator filtering, so missing,
        null/mistyped, wrong/distinct, conflicting, duplicate, foreign, opaque or
        extra-key evidence refuses with no repair; a genuine earlier attempt
        remains valid prior history after a real reclaim.  This is an
        UNCOMMITTED read-only reader: it performs no write.
        """
        state = notification.state
        if state not in allowed_states:
            return False
        publication_attempt = notification.publication_attempt
        if not self._v2_is_int(publication_attempt) or publication_attempt < 1:
            return False
        boot = notification.publisher_boot_id
        if not isinstance(boot, str) or not boot:
            return False
        claims = self._v2_authenticate_publication_claim_history_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt_id, candidate_id=candidate_id, result_id=result_id,
            envelope_id=envelope_id, notification_id=notification.notification_id,
            generation_id=generation_id, publication_attempt=publication_attempt,
        )
        if claims is None:
            return False
        current_claim = claims.get(publication_attempt)
        if current_claim is None:
            return False
        if current_claim.get("publisher_boot_id") != boot:
            return False
        observations: dict[str, dict[int, dict] | None] = {}
        for stage in (
            AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED,
        ):
            authenticated = (
                self._v2_authenticated_publication_events_by_attempt_uncommitted(
                    root_task_id=root_task_id, manager_agent=manager_agent,
                    attempt_id=attempt_id, stage=stage, candidate_id=candidate_id,
                    result_id=result_id, envelope_id=envelope_id,
                    notification_id=notification.notification_id,
                    generation_id=generation_id,
                    reference_attempt=publication_attempt,
                )
            )
            if authenticated is None:
                return False
            observations[stage] = authenticated
        published = observations[AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED] or {}
        failed = observations[AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED] or {}
        returned = observations[AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED] or {}
        # A recorded failure at the CURRENT attempt clears the publisher lease and
        # boot, so it can never coexist with a bound current boot; a failure at an
        # EARLIER attempt remains legitimate reclaim history.
        if publication_attempt in failed:
            return False
        if state == "published":
            if publication_attempt not in published:
                return False
            if returned:
                return False
        elif state == "publishing":
            # A live bound claim can never coexist with a ``published`` event at
            # the same attempt, and a ``publish_returned`` observation implies a
            # prior admission that this pre-admission reader must never accept.
            if publication_attempt in published:
                return False
            if returned:
                return False
        else:
            # admitted/settled: a genuine consumer may have admitted from the live
            # publishing claim or from the acknowledged published state; a
            # ``publish_returned`` observation may legitimately exist afterwards.
            pass
        return True

    def _authenticate_v2_admission_ready_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> tuple[str | None, dict | None]:
        """Post-final evidence plus a settled, pointer-current, unadmitted G.

        Requires N ``publishing`` OR ``published`` (a queue consumer may outrun
        publication acknowledgement), D still ``pending(G)``, no prior reserved
        session, the read-only settlement proof, the COMPLETE retained
        publication evidence for the exact generation (bounded contiguous
        ``{1..P}`` claim history, bound P/boot and state-required
        published/failure/return observations) and the PROVABLE ABSENCE of any
        prior generation admission/settlement evidence.  ``needed`` (not yet
        published) refuses so admission is only reachable from a publication
        claim; ``admitted``/``settled``/``invalidated`` refuse as already
        attempted.  Preexisting related ``generation_claimed``/
        ``notification_settled`` evidence is classified before any
        discriminator filtering and can never be ignored or repaired by another
        insertion.
        """
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return (
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            ), None
        assert ctx is not None
        notification = ctx["notification"]
        dispatch = ctx["dispatch"]
        if (
            dispatch.state != "pending"
            or dispatch.generation_id != notification.notification_id
        ):
            return "evidence_drift", None
        if notification.state not in ("publishing", "published"):
            return "evidence_drift", None
        if notification.next_session_id is not None:
            return "evidence_drift", None
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=ctx["attempt"], candidate=ctx["candidate"],
            envelope=ctx["envelope"], notification=notification,
            result_row=ctx["result_row"],
        ):
            return "evidence_drift", None
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        envelope = ctx["envelope"]
        # The complete retained publication evidence for THIS exact generation:
        # a deleted/duplicated/foreign/malformed claim or state-required
        # published/failure/return observation refuses with the exact prior
        # residue preserved, never repaired by reinsertion.
        if not self._authenticate_v2_retained_publication_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification=notification, generation_id=notification.notification_id,
            allowed_states=("publishing", "published"),
        ):
            return "evidence_drift", None
        # The FIRST admission must be the ONLY one: any preexisting, duplicate,
        # foreign, opaque or malformed related generation-claim/settlement event
        # refuses.  Classification happens before any discriminator filtering so
        # a wrong/null/absent discriminator cannot masquerade as absence.
        for absent_stage in (
            AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
        ):
            if not self._v2_related_result_stage_events_absent_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, stage=absent_stage,
                candidate_id=candidate.candidate_id, result_id=result_id,
                envelope_id=envelope.envelope_id,
                notification_id=notification.notification_id,
                generation_id=notification.notification_id,
            ):
                return "evidence_drift", None
        return None, ctx

    def _try_claim_v2_continuation_generation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str,
        now_dt: datetime,
    ) -> AuthorityPolicyV2GenerationClaimOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2GenerationClaimOutcome:
            return AuthorityPolicyV2GenerationClaimOutcome(
                status="generation_pending", reason=reason, **kw,
            )

        if not isinstance(generation_id, str) or not generation_id:
            return _pending("missing_generation")
        if not isinstance(next_session_id, str) or not next_session_id:
            return _pending("generation_failed")
        code, ctx = self._authenticate_v2_admission_ready_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        dispatch = ctx["dispatch"]
        envelope = ctx["envelope"]
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        base = {
            "attempt_id": attempt.attempt_id,
            "notification_id": notification.notification_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": notification.notification_id,
        }
        # Only the exact tagged generation may admit; a stale/absent/foreign or
        # retired token never adopts today's generation.
        if generation_id != notification.notification_id:
            return _pending("stale_generation", **base)
        task_row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        if (
            task_row is None
            or task_row["cancelled_at"] is not None
            or task_row["status"] != TaskStatus.PENDING.value
            or task_row["block_kind"] is not None
            or task_row["assigned_agent"] != manager_agent
            or task_row["current_session_id"] != manager_session_id
        ):
            return _pending("owner_lost", **base)
        new_count = int(task_row["orchestration_step_count"] or 0) + 1
        # N: publishing|published -> admitted with the reserved session.
        updated = notification.model_copy(update={
            "state": "admitted", "next_session_id": next_session_id,
            "updated_at": now_dt,
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET state='admitted', next_session_id=?,
                      canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state=? AND next_session_id IS NULL""",
            (
                next_session_id, canonical, snapshot["updated_at"],
                notification.notification_id, notification.state,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("already_admitted", **base)
        # D: pending(G) -> admitted(G).  Never a replacement generation.
        d_updated = dispatch.model_copy(update={"state": "admitted", "updated_at": now_dt})
        d_snapshot = d_updated.model_dump(mode="json")
        d_canonical = authority_policy_v2_canonical_json_bytes(d_snapshot).decode("utf-8")
        d_cursor = self._conn.execute(
            """UPDATE authority_policy_v2_root_dispatch
                  SET state='admitted', canonical_payload_json=?, updated_at=?
                WHERE root_task_id=? AND generation_id=? AND state='pending'""",
            (d_canonical, d_snapshot["updated_at"], root_task_id, generation_id),
        )
        if d_cursor.rowcount != 1:
            return _pending("identity_mismatch", **base)
        # Task: Pending/null block_kind/not cancelled/exact causal owner ->
        # in_progress + monotonic step increment + reserved owner/session.
        task_cursor = self._conn.execute(
            """UPDATE tasks
                  SET status=?, block_kind=NULL, note=NULL,
                      orchestration_step_count=?, assigned_agent=?,
                      current_session_id=?, updated_at=?
                WHERE id=? AND status=? AND block_kind IS NULL
                  AND cancelled_at IS NULL AND assigned_agent=?
                  AND current_session_id=?""",
            (
                TaskStatus.IN_PROGRESS.value, new_count, manager_agent,
                next_session_id, now_dt.isoformat(), root_task_id,
                TaskStatus.PENDING.value, manager_agent, manager_session_id,
            ),
        )
        if task_cursor.rowcount != 1:
            return _pending("owner_lost", **base)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            self._v2_admission_event_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
                attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
                result_id=result_id, envelope_id=envelope.envelope_id,
                notification_id=notification.notification_id,
                generation_id=generation_id, next_session_id=next_session_id,
            ),
        )
        return AuthorityPolicyV2GenerationClaimOutcome(
            status="claimed", attempt_id=attempt.attempt_id,
            notification_id=notification.notification_id,
            envelope_id=envelope.envelope_id, generation_id=generation_id,
            next_session_id=next_session_id, orchestration_step_count=new_count,
        )

    @_synchronized
    def try_claim_v2_continuation_generation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str,
    ) -> AuthorityPolicyV2GenerationClaimOutcome:
        """ONE synchronized atomic generation-admission claim transaction.

        Requires the exact tagged token G, a settled final generation with N
        ``publishing``/``published`` and D ``pending(G)``, the causal Pending
        owner/session and no prior reservation.  Atomically writes N admitted +
        ``next_session_id``, D admitted(G), the task to in_progress with a normal
        monotonic step increment and the reserved owner/session, and exactly one
        closed ``generation_claimed`` audit.  Any failure rolls ALL fields back,
        leaving the durable owner Pending and the generation re-admissible from a
        duplicate publication.  No queue call and no external launch happen here;
        a non-``claimed`` outcome forbids any ordinary claim/launch.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2GenerationClaimOutcome(
                status="generation_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._try_claim_v2_continuation_generation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                generation_id=generation_id, next_session_id=next_session_id,
                now_dt=now_dt,
            )
            if outcome.status == "generation_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2GenerationClaimOutcome(
                status="generation_pending", reason="generation_failed",
            )

    def _settle_v2_continuation_generation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str,
        now_dt: datetime,
    ) -> AuthorityPolicyV2AdmissionSettlementOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2AdmissionSettlementOutcome:
            return AuthorityPolicyV2AdmissionSettlementOutcome(
                status="settlement_pending", reason=reason, **kw,
            )

        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        notification = ctx["notification"]
        dispatch = ctx["dispatch"]
        envelope = ctx["envelope"]
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        base = {
            "attempt_id": attempt.attempt_id,
            "notification_id": notification.notification_id,
            "generation_id": notification.notification_id,
            "next_session_id": next_session_id,
        }
        if (
            not isinstance(generation_id, str) or not generation_id
            or not isinstance(next_session_id, str) or not next_session_id
            or generation_id != notification.notification_id
            or notification.next_session_id != next_session_id
            or dispatch.generation_id != generation_id
        ):
            return _pending("identity_mismatch", **base)
        # The admitted generation's COMPLETE retained publication evidence must
        # authenticate: a deleted/duplicated/foreign/malformed claim or
        # state-required published/return observation, or a bound boot that no
        # retained claim proves, refuses with the exact admitted residue intact.
        if not self._authenticate_v2_retained_publication_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification=notification, generation_id=generation_id,
            allowed_states=("admitted", "settled"),
        ):
            return _pending("evidence_drift", **base)
        # The final generation's GENUINE settlement proof (real ordinary
        # completion OR the exact real callback_consumed Q plus both complete
        # settlement audits) is required at every settlement/replay boundary, so
        # deleting the completion/receipt after admission holds settlement.
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification, result_row=ctx["result_row"],
        ):
            return _pending("evidence_drift", **base)
        expected_claim = self._v2_admission_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id, next_session_id=next_session_id,
        )
        if not self._authenticate_v2_admission_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, expected=expected_claim,
        ):
            return _pending("evidence_drift", **base)
        expected_settled = self._v2_admission_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id, next_session_id=next_session_id,
        )
        if notification.state == "settled":
            # An already-settled generation must still own the exact admitted
            # dispatch pointer; a conflicting D is a conflict, not a replay.
            if dispatch.state != "admitted":
                return _pending("evidence_drift", **base)
            if self._authenticate_v2_admission_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id, expected=expected_settled,
            ):
                return AuthorityPolicyV2AdmissionSettlementOutcome(
                    status="already_settled_exact", **base,
                )
            return _pending("evidence_drift", **base)
        if notification.state != "admitted" or dispatch.state != "admitted":
            return _pending("not_settleable", **base)
        # The FIRST admitted -> settled transition requires coherent ABSENCE of
        # any retained settlement-stage evidence: a preexisting/duplicate/
        # foreign/malformed notification_settled event is a conflict and is never
        # ignored or repaired by appending a second one.
        if not self._v2_related_result_stage_events_absent_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id,
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
            candidate_id=candidate.candidate_id, result_id=result_id,
            envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id,
        ):
            return _pending("evidence_drift", **base)
        task_row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        if (
            task_row is None
            or task_row["cancelled_at"] is not None
            or task_row["status"] != TaskStatus.IN_PROGRESS.value
            or task_row["assigned_agent"] != manager_agent
            or task_row["current_session_id"] != next_session_id
        ):
            return _pending("owner_lost", **base)
        updated = notification.model_copy(update={"state": "settled", "updated_at": now_dt})
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_recovery_notifications
                  SET state='settled', canonical_payload_json=?, updated_at=?
                WHERE notification_id=? AND state='admitted' AND next_session_id=?""",
            (
                canonical, snapshot["updated_at"], notification.notification_id,
                next_session_id,
            ),
        )
        if cursor.rowcount != 1:
            return _pending("not_settleable", **base)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            expected_settled,
        )
        return AuthorityPolicyV2AdmissionSettlementOutcome(
            status="settled", **base,
        )

    @_synchronized
    def settle_v2_continuation_generation_admission(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str,
    ) -> AuthorityPolicyV2AdmissionSettlementOutcome:
        """Separate exact admission-settlement bookkeeping transaction.

        Requires the exact admitted N/G/reserved session and the durable claim
        audit.  CASes ``admitted`` -> ``settled`` with exactly one closed
        ``notification_settled`` audit.  It grants no launch authority and never
        performs a second generation admission; a duplicate/reopened caller may
        settle exact evidence (or read-only replay ``already_settled_exact``) but
        must never dispatch.  Failure keeps the admitted + claim-audit evidence.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2AdmissionSettlementOutcome(
                status="settlement_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._settle_v2_continuation_generation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                generation_id=generation_id, next_session_id=next_session_id,
                now_dt=now_dt,
            )
            if outcome.status == "settlement_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2AdmissionSettlementOutcome(
                status="settlement_pending", reason="settlement_failed",
            )

    # ------------------------------------------------------------------
    # THR-229 checkpoint C3d3c1: the ONE atomic next-result spend-to-ready
    # writer and its result-keyed receipt.  Public, callable and deliberately
    # DARK: no production consumer calls it, no queue/child/task-status effect
    # and no launch authority is produced here.  The ordinary decision consumer
    # (ready -> claimed -> applied/refused) remains the NEXT serial unit.
    # ------------------------------------------------------------------

    def _v2_spend_event_payload(
        self, *, attempt_id: str, candidate_id: str, result_id: int,
        envelope_id: str, notification_id: str, generation_id: str,
        next_session_id: str, spending_result_id: int, report_digest: str,
    ) -> dict:
        """One closed ``ax`` spend result-stage payload (result-keyed receipt).

        ``report_digest`` is the durable exact report identity of the retained
        spending result R2: the type/field-presence-preserving normalized report
        projection captured at FIRST spend.  A later read-only replay re-derives
        it from the persisted R2 row, so ANY material report-field drift (a
        syntactically valid changed decision body, summary, status, confidence,
        verdict, output path, risks, wait IDs or local-CI evidence) fails the
        closed comparison and refuses without a write.
        """
        return {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_SPENT,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "result_id": result_id,
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "next_session_id": next_session_id,
            "spending_result_id": spending_result_id,
            "report_digest": report_digest,
        }

    def _v2_spend_event_identity_keys(self) -> set[str]:
        return {
            "stage", "attempt_id", "candidate_id", "result_id", "envelope_id",
            "notification_id", "generation_id", "next_session_id",
            "spending_result_id", "report_digest",
        }

    def _v2_related_spend_events_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, next_session_id: str,
        spending_result_id: int,
    ) -> list[dict] | None:
        """Enumerate POTENTIALLY related ``spent`` events BEFORE filtering.

        Identity-scoped enumeration happens before any stage/discriminator
        filtering, so an appended row whose attempt/result/generation/session
        reference is null/missing/mistyped/foreign -- or whose body is opaque or
        carries extra keys -- can never be discarded before classification.
        ``None`` means unreadable or opaque: the caller must fail closed.

        A non-``spent`` row is skipped ONLY when its discriminator names a
        recognized result stage AND its payload carries that stage's exact
        authentic closed key set (``_v2_spend_other_stage_shape_is_closed``).
        An absent/null/mistyped/unknown discriminator, or a recognized name with
        a non-authentic shape, is therefore classified by the SAME typed causal
        identities as a ``spent`` row: any matching or malformed present
        reference makes it related.  A row is independently unrelated ONLY when
        either it is a closed-shape other-stage event or every present causal
        reference is well-typed and provably different.
        """
        rows = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            attempt_id=None, include_opaque=True,
        )
        if rows is None:
            return None
        identities = (
            ("attempt_id", attempt_id, "str"),
            ("candidate_id", candidate_id, "str"),
            ("result_id", result_id, "int"),
            ("envelope_id", envelope_id, "str"),
            ("notification_id", notification_id, "str"),
            ("generation_id", generation_id, "str"),
            ("next_session_id", next_session_id, "str"),
            ("spending_result_id", spending_result_id, "int"),
        )
        related: list[dict] = []
        for row in rows:
            payload = row["payload"]
            if not isinstance(payload, dict):
                return None
            stage = payload.get("stage")
            if (
                stage != AUTHORITY_POLICY_V2_RESULT_STAGE_SPENT
                and self._v2_spend_other_stage_shape_is_closed(stage, payload)
            ):
                # Independently legitimate OTHER-stage history is authenticated
                # by its actual closed shape (exact key set) for a recognized
                # result stage.  An absent/null/mistyped/unknown discriminator
                # -- or a recognized name carrying a non-authentic shape -- is
                # NOT proof of unrelatedness and falls through to identity
                # classification below.
                continue
            states = [
                self._v2_identity_observation(payload, field, value, kind)
                for field, value, kind in identities
            ]
            if all(state == "distinct" for state in states):
                continue
            related.append(payload)
        return related

    @staticmethod
    def _v2_spend_other_stage_shape_is_closed(stage, payload) -> bool:
        """True only for a recognized OTHER result stage in its closed shape.

        A different string is never assumed to be a valid other stage: the
        discriminator must be one of the closed result-stage values that
        actually produces a result-stage payload AND the payload's key set must
        equal that stage's authentic closed key set.  Everything else (absent,
        ``None``, non-string, unknown, or a recognized name with extra/missing
        keys) is NOT independently legitimate history.
        """
        if not isinstance(stage, str):
            return False
        key_sets = _V2_SPEND_OTHER_RESULT_STAGE_KEY_SETS.get(stage)
        if not key_sets:
            return False
        return frozenset(payload.keys()) in key_sets

    def _v2_spend_event_absent_uncommitted(self, **kwargs) -> bool:
        """True only when ZERO potentially-related ``spent`` events exist.

        Absence is proven separately from a false/malformed authentication: a
        malformed/duplicate/foreign/opaque related row is a conflict, never
        absence, and is never repaired by inserting another event.
        """
        related = self._v2_related_spend_events_uncommitted(**kwargs)
        return related is not None and len(related) == 0

    def _authenticate_v2_spend_event_uncommitted(
        self, *, root_task_id: str, manager_agent: str, expected: dict,
    ) -> bool:
        """Exactly one authentic CLOSED ``spent`` event for the exact receipt."""
        related = self._v2_related_spend_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=expected["attempt_id"],
            candidate_id=expected["candidate_id"],
            result_id=expected["result_id"], envelope_id=expected["envelope_id"],
            notification_id=expected["notification_id"],
            generation_id=expected["generation_id"],
            next_session_id=expected["next_session_id"],
            spending_result_id=expected["spending_result_id"],
        )
        if related is None or len(related) != 1:
            return False
        payload = related[0]
        if not isinstance(payload, dict):
            return False
        if set(payload.keys()) != self._v2_spend_event_identity_keys():
            return False
        return self._v2_json_type_sensitive_equal(payload, expected)

    @classmethod
    def _v2_spending_report_identity(cls, row) -> dict:
        """Normalized material report identity of one persisted spending result.

        Mirrors the shipping callback admission's exact completion projection
        (``completion_result_payload_matches``): every material report field the
        callback route persists is carried with its JSON scalar type and
        presence preserved (an explicit JSON ``null`` never equals an
        absent/``None`` value, and ``True`` never equals integer ``1``).
        Server-owned ``created_at`` and the separately authenticated
        task/agent/session identity columns are deliberately excluded.
        """
        return {
            "output_summary": row["output_summary"],
            "confidence_score": row["confidence_score"],
            "status": row["status"],
            "output_dir": row["output_dir"],
            "verdict": row["verdict"],
            "risks_flagged": _canonical_completion_json(row["risks_flagged"]),
            "decision_json": _canonical_completion_json(row["decision_json"]),
            "waiting_on_job_ids": _canonical_completion_json(
                row["waiting_on_job_ids"]
            ),
            "local_ci": _canonical_completion_json(row["local_ci"]),
        }

    @classmethod
    def _v2_spending_report_digest(cls, row) -> str | None:
        """Durable exact report identity digest, or ``None`` when underivable.

        A failure to derive the normalized identity fails closed: the caller
        treats it as a malformed/absent report, never as an ordinary path.
        """
        try:
            return authority_policy_v2_sha256(
                cls._v2_spending_report_identity(row)
            )
        except Exception:
            return None

    def _authenticate_v2_spending_result_uncommitted(
        self, *, root_task_id: str, manager_agent: str, next_session_id: str,
        spending_result_id, causal_result_id: int,
    ):
        """Authenticate the immutable spending result R2 report identity.

        R2 must be a real persisted result of the SAME root + reserved manager
        session (never a foreign/root/other-session row), must differ from the
        causal result R, and must carry an exact persisted decision/report body
        (a JSON object).  Returns the explicit ``(row, report_digest)`` exact
        report identity: ``report_digest`` is the normalized, type/field-
        presence-preserving projection digest that the FIRST spend binds into
        the durable ``spent`` receipt, so a later replay CANNOT authenticate a
        changed payload.  A missing/null/mistyped identifier, an equal-to-R id,
        a foreign session or a malformed/underivable report returns ``None`` --
        never absence and never an ordinary-path permission.
        """
        if not self._v2_is_int(spending_result_id) or spending_result_id < 1:
            return None
        if not self._v2_is_int(causal_result_id) or spending_result_id == causal_result_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (spending_result_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            row["task_id"] != root_task_id
            or row["agent"] != manager_agent
            or row["session_id"] != next_session_id
        ):
            return None
        raw = row["decision_json"]
        if not isinstance(raw, str) or not raw:
            return None
        try:
            decision = json.loads(raw)
        except Exception:
            return None
        if not isinstance(decision, dict):
            return None
        report_digest = self._v2_spending_report_digest(row)
        if report_digest is None:
            return None
        return row, report_digest

    def _consume_v2_continue_envelope_uncommitted(
        self, envelope: AuthorityPolicyV2ContinueEnvelope, spending_result_id: int,
    ) -> None:
        """CAS E active -> consumed with the result-keyed READY receipt."""
        updated = envelope.model_copy(update={
            "lifecycle_state": "consumed",
            "spending_result_id": spending_result_id,
            "decision_state": "ready",
        })
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_continue_envelopes
                  SET lifecycle_state='consumed', spending_result_id=?,
                      decision_state='ready', canonical_payload_json=?
                WHERE envelope_id=? AND lifecycle_state='active'
                  AND spending_result_id IS NULL AND decision_state IS NULL""",
            (spending_result_id, canonical, envelope.envelope_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 continuation envelope spend CAS lost")

    def _retire_v2_root_dispatch_uncommitted(
        self, dispatch: AuthorityPolicyV2RootDispatch, now_dt: datetime,
    ) -> None:
        """CAS D admitted(G) -> retired(G) for the exact spent generation."""
        updated = dispatch.model_copy(update={"state": "retired", "updated_at": now_dt})
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_root_dispatch
                  SET state='retired', canonical_payload_json=?, updated_at=?
                WHERE root_task_id=? AND generation_id=? AND state='admitted'""",
            (
                canonical, snapshot["updated_at"], dispatch.root_task_id,
                dispatch.generation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 root dispatch retire CAS lost")

    def _spend_v2_continuation_envelope_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str | None,
        spending_result_id, now_dt: datetime,
    ) -> AuthorityPolicyV2SpendOutcome:
        def _pending(
            reason: str, **kw,
        ) -> AuthorityPolicyV2SpendOutcome:
            return AuthorityPolicyV2SpendOutcome(
                status="spend_pending", reason=reason, **kw,
            )

        if not self._v2_is_int(spending_result_id) or spending_result_id < 1:
            return _pending("identity_mismatch")
        if not isinstance(next_session_id, str) or not next_session_id:
            return _pending("identity_mismatch")
        if not self._v2_is_int(result_id) or spending_result_id == result_id:
            return _pending("identity_mismatch")
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if code is not None:
            return _pending(
                "evidence_drift" if code == "evidence_drift" else "identity_mismatch"
            )
        assert ctx is not None
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        envelope = ctx["envelope"]
        notification = ctx["notification"]
        dispatch = ctx["dispatch"]
        base = {
            "attempt_id": attempt.attempt_id,
            "candidate_id": candidate.candidate_id,
            "notification_id": notification.notification_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": notification.notification_id,
            "result_id": result_id,
            "spending_result_id": spending_result_id,
            "next_session_id": next_session_id,
        }
        # "G is authority, P is diagnostic": the exact tagged generation is
        # required, the reserved session must be the one this generation
        # reserved, and the live root pointer must still name it.  A missing/
        # null/mistyped token is invalid, never an ordinary-path permission.
        if (
            not isinstance(generation_id, str) or not generation_id
            or generation_id != notification.notification_id
            or dispatch.generation_id != notification.notification_id
        ):
            return _pending("identity_mismatch", **base)
        if notification.next_session_id != next_session_id:
            return _pending("identity_mismatch", **base)
        if dispatch.state not in ("admitted", "retired"):
            return _pending("identity_mismatch", **base)
        # R2: the immutable spending result of the reserved same-root manager.
        # Its EXACT normalized report identity is derived from the retained row
        # now and bound into the durable ``spent`` receipt below; a later replay
        # re-derives it and refuses on any material report-field drift.
        r2 = self._authenticate_v2_spending_result_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            next_session_id=next_session_id, spending_result_id=spending_result_id,
            causal_result_id=result_id,
        )
        if r2 is None:
            return _pending("missing_result", **base)
        _r2_row, report_digest = r2
        expected_spent = self._v2_spend_event_payload(
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id, next_session_id=next_session_id,
            spending_result_id=spending_result_id, report_digest=report_digest,
        )
        # Complete retained publication evidence + genuine ordinary OR exact
        # callback_consumed recovery settlement proof for the CAUSAL tuple
        # (including any conflicting potentially-related Q).
        if not self._authenticate_v2_retained_publication_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification=notification, generation_id=generation_id,
            allowed_states=("admitted", "settled"),
        ):
            return _pending("evidence_drift", **base)
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification, result_row=ctx["result_row"],
        ):
            return _pending("evidence_drift", **base)
        # Complete retained generation-admission evidence (ag + as).
        for stage in (
            AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
        ):
            if not self._authenticate_v2_admission_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id,
                expected=self._v2_admission_event_payload(
                    stage=stage, attempt_id=attempt.attempt_id,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification.notification_id,
                    generation_id=generation_id, next_session_id=next_session_id,
                ),
            ):
                return _pending("evidence_drift", **base)
        # R2's own authentic launch binding / pinned lineage.  Equality with
        # TODAY'S selector is deliberately NOT required: a later legitimate
        # activation never invalidates this pinned generation, so only the
        # binding's own sealed release/activation/selector lineage is proved.
        binding2 = self.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=next_session_id,
        )
        if binding2 is None:
            return _pending("identity_mismatch", **base)
        try:
            self._authenticate_v2_session_binding_uncommitted(binding2)
        except Exception:
            return _pending("identity_mismatch", **base)
        if (
            binding2.team != candidate.team
            or binding2.contract_id != candidate.contract_id
            or binding2.contract_version != candidate.contract_version
            or binding2.contract_digest != candidate.contract_digest
        ):
            return _pending("identity_mismatch", **base)
        # ------------------------------------------------------------------
        # Exact same-R2 replay: an authenticated consumed READY receipt is read
        # back without any write, remint or dispatch.  Consumed/retired states
        # are authenticated WITHOUT requiring an impossible active pre-state.
        # ------------------------------------------------------------------
        if envelope.lifecycle_state == "consumed":
            if (
                envelope.spending_result_id != spending_result_id
                or envelope.decision_state is None
            ):
                return _pending("receipt_conflict", **base)
            if (
                dispatch.state != "retired"
                or dispatch.generation_id != generation_id
                or notification.state != "settled"
            ):
                return _pending("receipt_conflict", **base)
            if not self._authenticate_v2_spend_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                expected=expected_spent,
            ):
                return _pending("receipt_conflict", **base)
            return AuthorityPolicyV2SpendOutcome(
                status="already_spent_exact", **base,
                decision_state=envelope.decision_state,
                report_digest=report_digest,
            )
        # ------------------------------------------------------------------
        # First spend.  Only an active/null-receipt envelope with an admitted
        # pointer and a settled notification is spendable.
        # ------------------------------------------------------------------
        if (
            envelope.lifecycle_state != "active"
            or envelope.spending_result_id is not None
            or envelope.decision_state is not None
            or dispatch.state != "admitted"
            or notification.state != "settled"
        ):
            return _pending("not_spendable", **base)
        task_row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        if (
            task_row is None
            or task_row["cancelled_at"] is not None
            or task_row["status"] != TaskStatus.IN_PROGRESS.value
            or task_row["block_kind"] is not None
            or task_row["assigned_agent"] != manager_agent
            or task_row["current_session_id"] != next_session_id
        ):
            return _pending("owner_lost", **base)
        # A distinct later R2 authority attempt / generation B is NEVER
        # overwritten: any OTHER envelope already carrying this exact spending
        # result is a conflict.  The partial unique index is the durable backstop.
        other = self._conn.execute(
            """SELECT envelope_id FROM authority_policy_v2_continue_envelopes
               WHERE spending_result_id=? AND envelope_id<>?""",
            (spending_result_id, envelope.envelope_id),
        ).fetchone()
        if other is not None:
            return _pending("receipt_conflict", **base)
        # The FIRST spend must be the ONLY one: prove ABSENCE of any related
        # spent event separately from a false/malformed authentication.
        if not self._v2_spend_event_absent_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id, next_session_id=next_session_id,
            spending_result_id=spending_result_id,
        ):
            return _pending("receipt_conflict", **base)
        # One atomic mutation: E active -> consumed(R2, ready), D admitted ->
        # retired, exactly one closed ``spent`` audit.  Task, R2, N, K/P/V,
        # causal J and every prior audit are retained byte-for-byte.
        self._consume_v2_continue_envelope_uncommitted(envelope, spending_result_id)
        self._retire_v2_root_dispatch_uncommitted(dispatch, now_dt)
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            expected_spent,
        )
        return AuthorityPolicyV2SpendOutcome(
            status="spent", **base, decision_state="ready",
            report_digest=report_digest,
        )

    @_synchronized
    def spend_authority_policy_v2_continue_envelope(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, generation_id: str | None, next_session_id: str | None,
        spending_result_id: int,
    ) -> AuthorityPolicyV2SpendOutcome:
        """ONE synchronized atomic next-result spend-to-ready transaction.

        Authenticates the complete post-final causal evidence, the exact tagged
        generation G, the reserved same-root manager session and the immutable
        spending result R2 — its persisted report AND the exact normalized report
        identity derived from that retained row (its own launch binding is
        authenticated too).  The derived ``report_digest`` is bound into the
        durable ``spent`` receipt and returned on the committed outcome, so an
        exact replay compares persisted R2 against the ACCEPTED identity; no
        caller argument can supply, bypass or waive that comparison.  It then
        atomically CASes E ``active`` -> ``consumed`` with
        ``spending_result_id=R2`` + ``decision_state='ready'``, D ``admitted``
        -> ``retired`` and exactly one closed ``spent`` audit.  An exact same-R2
        retry is a read-only ``already_spent_exact``; everything missing/null/
        mistyped/conflicting/malformed refuses (``spend_pending``) with the whole
        transaction rolled back, E active, D admitted, R2 retained and the
        decision unapplied.  This method performs NO consumer call, NO queue
        call, NO child creation and NO task-status effect.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2SpendOutcome(
                status="spend_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._spend_v2_continuation_envelope_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                generation_id=generation_id, next_session_id=next_session_id,
                spending_result_id=spending_result_id, now_dt=now_dt,
            )
            if outcome.status == "spend_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2SpendOutcome(
                status="spend_pending", reason="spend_failed",
            )

    # ------------------------------------------------------------------
    # THR-229 checkpoint C3d3c2: the ordinary decision-dispatch family.
    #
    # The consumed continuation envelope (E) carrying a non-null
    # ``spending_result_id`` IS the result-keyed decision receipt (R2).  Its
    # closed, forward-only ``decision_state`` is the durable single-use token:
    # exactly ONE winning ``ready -> claimed`` CAS (plus one closed
    # ``decision_claimed`` audit) authorizes exactly ONE ordinary consumer
    # entry; a separate ``claimed -> applied`` acknowledgement records the
    # consumer's return; and an audited ``decision_dispatch_interrupted``
    # refusal records an exception/restart after the claim without ever
    # re-invoking the consumer.  No writer here calls the consumer, a queue or
    # an external process.
    # ------------------------------------------------------------------

    def _v2_decision_event_payload(
        self, *, stage: str, attempt_id: str, candidate_id: str, result_id: int,
        envelope_id: str, notification_id: str, generation_id: str,
        next_session_id: str, spending_result_id: int, report_digest: str,
    ) -> dict:
        """One closed result-keyed decision-dispatch event payload."""
        return {
            "stage": stage,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "result_id": result_id,
            "envelope_id": envelope_id,
            "notification_id": notification_id,
            "generation_id": generation_id,
            "next_session_id": next_session_id,
            "spending_result_id": spending_result_id,
            "report_digest": report_digest,
        }

    @staticmethod
    def _v2_result_stage_shape_is_closed(stage, payload) -> bool:
        """True only for a recognized result stage in its exact closed shape."""
        if not isinstance(stage, str):
            return False
        key_sets = _V2_ALL_RESULT_STAGE_KEY_SETS.get(stage)
        if not key_sets:
            return False
        return frozenset(payload.keys()) in key_sets

    def _v2_related_decision_events_uncommitted(
        self, *, root_task_id: str, manager_agent: str, attempt_id: str,
        candidate_id: str, result_id: int, envelope_id: str,
        notification_id: str, generation_id: str, next_session_id: str,
        spending_result_id: int,
    ) -> dict[str, list[dict]] | None:
        """Enumerate POTENTIALLY related decision events BEFORE filtering.

        Identity-scoped enumeration happens before any stage/discriminator
        filtering, so an appended decision row whose attempt/result/envelope/
        notification/generation/next-session/spending-result reference is
        null/missing/mistyped/foreign -- or whose body is opaque or carries
        extra keys -- can never be discarded before classification.  ``None``
        means unreadable/opaque or a related row with a corrupt non-decision
        shape: the caller must fail closed.

        A row is skipped as independently legitimate history ONLY when its
        discriminator names a recognized result stage AND its payload carries
        that stage's exact closed key set.  Rows whose stage is one of the
        decision-dispatch stages are classified by identity and grouped by
        stage, so a conflicting decision event can never hide behind a
        same-generation filter.
        """
        rows = self._v2_identity_scoped_audits(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            attempt_id=None, include_opaque=True,
        )
        if rows is None:
            return None
        decision_stages = (
            AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
        )
        identities = (
            ("attempt_id", attempt_id, "str"),
            ("candidate_id", candidate_id, "str"),
            ("result_id", result_id, "int"),
            ("envelope_id", envelope_id, "str"),
            ("notification_id", notification_id, "str"),
            ("generation_id", generation_id, "str"),
            ("next_session_id", next_session_id, "str"),
            ("spending_result_id", spending_result_id, "int"),
        )
        by_stage: dict[str, list[dict]] = {}
        for row in rows:
            payload = row["payload"]
            if not isinstance(payload, dict):
                return None
            stage = payload.get("stage")
            states = [
                self._v2_identity_observation(payload, field, value, kind)
                for field, value, kind in identities
            ]
            if stage not in decision_stages:
                if self._v2_result_stage_shape_is_closed(stage, payload):
                    continue
                if all(state == "distinct" for state in states):
                    continue
                # A corrupt-shape non-decision row with a matching/malformed
                # causal reference is a conflict, never independently absent.
                return None
            if all(state == "distinct" for state in states):
                continue
            by_stage.setdefault(stage, []).append(payload)
        return by_stage

    def _v2_decision_events_absent_uncommitted(self, **kwargs) -> bool:
        """True only when ZERO potentially-related decision events exist."""
        by_stage = self._v2_related_decision_events_uncommitted(**kwargs)
        return by_stage is not None and len(by_stage) == 0

    def _authenticate_v2_decision_event_uncommitted(
        self, *, root_task_id: str, manager_agent: str,
        expected_events: dict[str, dict],
    ) -> bool:
        """Exactly the COMPLETE closed decision-event set for the exact receipt.

        ``expected_events`` maps EVERY allowed decision stage to its exact
        closed payload.  The persisted set must equal that key set with exactly
        one CLOSED, type-sensitive-equal payload per stage.  A preceding claim
        whose retained ``report_digest`` (or any other field) was mutated is
        therefore refused exactly like a mutated final event -- an exact replay
        never authenticates a corrupted history.  Missing, duplicated,
        conflicting, foreign, opaque or extra-key rows refuse with no repair.
        """
        if not expected_events:
            return False
        expected = next(iter(expected_events.values()))
        by_stage = self._v2_related_decision_events_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=expected["attempt_id"], candidate_id=expected["candidate_id"],
            result_id=expected["result_id"], envelope_id=expected["envelope_id"],
            notification_id=expected["notification_id"],
            generation_id=expected["generation_id"],
            next_session_id=expected["next_session_id"],
            spending_result_id=expected["spending_result_id"],
        )
        if by_stage is None:
            return False
        if set(by_stage.keys()) != set(expected_events.keys()):
            return False
        for stage, expected_payload in expected_events.items():
            rows = by_stage.get(stage, [])
            if len(rows) != 1:
                return False
            payload = rows[0]
            if set(payload.keys()) != set(expected_payload.keys()):
                return False
            if not self._v2_json_type_sensitive_equal(payload, expected_payload):
                return False
        return True

    def _authenticate_v2_spent_decision_receipt_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> tuple[str | None, dict | None]:
        """Read-only authentication of the complete spent result-keyed receipt.

        Reuses the exact post-final evidence rules (J/K/P/V/E/N/D and every
        final audit), then additionally requires the consumed envelope carrying
        this exact spending result with a closed decision state, the retired
        exact generation, the settled notification, the complete retained
        publication + settlement proof, both generation-admission events, R2's
        own authenticated launch binding and the exact ``spent`` audit whose
        closed ``report_digest`` still re-derives from the persisted R2 row.
        It deliberately does NOT require any task projection: the ordinary
        consumer may legitimately have changed the task, and the claim/refusal
        boundaries apply their own current-owner predicate.
        """
        # The post-final authenticator needs the causal manager session, which
        # the caller resolves from the attempt journal.  Resolve it here so the
        # exact causal tuple is never caller-supplied.
        attempt_row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=? AND result_id=?""",
            (root_task_id, manager_agent, result_id),
        ).fetchone()
        if attempt_row is None:
            return "identity_mismatch", None
        manager_session_id = attempt_row["manager_session_id"]
        if not isinstance(manager_session_id, str) or not manager_session_id:
            return "identity_mismatch", None
        code, ctx = self._authenticate_v2_post_final_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            require_dispatch_generation=False,
        )
        if code is not None:
            return code, None
        assert ctx is not None
        envelope = ctx["envelope"]
        notification = ctx["notification"]
        dispatch = ctx["dispatch"]
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        generation_id = ctx["generation_id"]
        if (
            envelope.lifecycle_state != "consumed"
            or envelope.spending_result_id is None
            or envelope.decision_state is None
        ):
            return "not_claimable", None
        spending_result_id = envelope.spending_result_id
        next_session_id = notification.next_session_id
        if not isinstance(next_session_id, str) or not next_session_id:
            return "identity_mismatch", None
        # The C3d3b pointer is REQUIRED to name this exact spent generation while
        # it is still current (``retired``).  A later legitimate generation B
        # may legitimately own the single root pointer; A's own retirement is
        # then durably proven by the exact ``spent`` audit below, so historical
        # acknowledgement/refusal must not depend on a still-current D, regress
        # B, or lose the ability to settle A.
        if dispatch.generation_id == generation_id:
            if dispatch.state != "retired":
                return "evidence_drift", None
        if notification.state != "settled":
            return "evidence_drift", None
        r2 = self._authenticate_v2_spending_result_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            next_session_id=next_session_id, spending_result_id=spending_result_id,
            causal_result_id=result_id,
        )
        if r2 is None:
            return "missing_result", None
        _r2_row, report_digest = r2
        expected_spent = self._v2_spend_event_payload(
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification_id=notification.notification_id,
            generation_id=generation_id, next_session_id=next_session_id,
            spending_result_id=spending_result_id, report_digest=report_digest,
        )
        if not self._authenticate_v2_spend_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            expected=expected_spent,
        ):
            return "evidence_drift", None
        if not self._authenticate_v2_retained_publication_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=attempt.attempt_id, candidate_id=candidate.candidate_id,
            result_id=result_id, envelope_id=envelope.envelope_id,
            notification=notification, generation_id=generation_id,
            allowed_states=("admitted", "settled"),
        ):
            return "evidence_drift", None
        if not self._authenticate_v2_publication_settlement_proof_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            attempt=attempt, candidate=candidate, envelope=envelope,
            notification=notification, result_row=ctx["result_row"],
        ):
            return "evidence_drift", None
        for stage in (
            AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED,
            AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED,
        ):
            if not self._authenticate_v2_admission_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                attempt_id=attempt.attempt_id,
                expected=self._v2_admission_event_payload(
                    stage=stage, attempt_id=attempt.attempt_id,
                    candidate_id=candidate.candidate_id, result_id=result_id,
                    envelope_id=envelope.envelope_id,
                    notification_id=notification.notification_id,
                    generation_id=generation_id, next_session_id=next_session_id,
                ),
            ):
                return "evidence_drift", None
        binding2 = self.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=next_session_id,
        )
        if binding2 is None:
            return "identity_mismatch", None
        try:
            self._authenticate_v2_session_binding_uncommitted(binding2)
        except Exception:
            return "identity_mismatch", None
        if (
            binding2.team != candidate.team
            or binding2.contract_id != candidate.contract_id
            or binding2.contract_version != candidate.contract_version
            or binding2.contract_digest != candidate.contract_digest
        ):
            return "identity_mismatch", None
        return None, {
            **ctx,
            "manager_session_id": manager_session_id,
            "spending_result_id": spending_result_id,
            "next_session_id": next_session_id,
            "report_digest": report_digest,
            "expected_spent": expected_spent,
            "r2_row": _r2_row,
        }

    def _v2_claim_task_owner_current_uncommitted(
        self, *, root_task_id: str, manager_agent: str, next_session_id: str,
    ) -> bool:
        """The still-current nonterminal reserved invocation for the claim."""
        task_row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        return not (
            task_row is None
            or task_row["cancelled_at"] is not None
            or task_row["status"] != TaskStatus.IN_PROGRESS.value
            or task_row["block_kind"] is not None
            or task_row["assigned_agent"] != manager_agent
            or task_row["current_session_id"] != next_session_id
        )

    def _set_v2_decision_state_uncommitted(
        self, envelope: AuthorityPolicyV2ContinueEnvelope, new_state: str,
    ) -> None:
        """CAS the closed forward-only decision state with its canonical body."""
        updated = envelope.model_copy(update={"decision_state": new_state})
        snapshot = updated.model_dump(mode="json")
        canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_continue_envelopes
                  SET decision_state=?, canonical_payload_json=?
                WHERE envelope_id=? AND lifecycle_state='consumed'
                  AND decision_state=?""",
            (new_state, canonical, envelope.envelope_id, envelope.decision_state),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 decision receipt CAS lost")

    def _claim_v2_decision_dispatch_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> AuthorityPolicyV2DecisionClaimOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2DecisionClaimOutcome:
            return AuthorityPolicyV2DecisionClaimOutcome(
                status="decision_pending", reason=reason, **kw,
            )

        code, receipt = self._authenticate_v2_spent_decision_receipt_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_id=result_id,
        )
        if code is not None:
            return _pending(code)
        assert receipt is not None
        envelope = receipt["envelope"]
        decision_state = envelope.decision_state
        base = {
            "attempt_id": receipt["attempt"].attempt_id,
            "candidate_id": receipt["candidate"].candidate_id,
            "notification_id": receipt["notification"].notification_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": receipt["generation_id"],
            "result_id": result_id,
            "spending_result_id": receipt["spending_result_id"],
            "next_session_id": receipt["next_session_id"],
            "report_digest": receipt["report_digest"],
        }
        if decision_state == "claimed":
            return _pending("already_claimed", **base)
        if decision_state == "applied":
            return _pending("already_applied", **base)
        if decision_state == "refused":
            return _pending("already_refused", **base)
        if decision_state != "ready":
            return _pending("not_claimable", **base)
        if not self._v2_claim_task_owner_current_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            next_session_id=receipt["next_session_id"],
        ):
            return _pending("owner_lost", **base)
        # The FIRST claim must be the ONLY one: prove ABSENCE of any related
        # decision event separately from a false/malformed authentication.
        if not self._v2_decision_events_absent_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            attempt_id=receipt["attempt"].attempt_id,
            candidate_id=receipt["candidate"].candidate_id, result_id=result_id,
            envelope_id=envelope.envelope_id,
            notification_id=receipt["notification"].notification_id,
            generation_id=receipt["generation_id"],
            next_session_id=receipt["next_session_id"],
            spending_result_id=receipt["spending_result_id"],
        ):
            return _pending("evidence_drift", **base)
        self._set_v2_decision_state_uncommitted(envelope, "claimed")
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            self._v2_decision_event_payload(
                stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED, **base,
            ),
        )
        return AuthorityPolicyV2DecisionClaimOutcome(
            status="claimed", **base, decision_state="claimed",
        )

    @_synchronized
    def claim_authority_policy_v2_decision_dispatch(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> AuthorityPolicyV2DecisionClaimOutcome:
        """ONE synchronized atomic ``ready -> claimed`` decision-dispatch claim.

        Authenticates the complete spent result-keyed receipt (J/K/P/V/E/N/D,
        every final audit, the retained publication + settlement proof, the two
        generation-admission events, R2 and its bound ``report_digest``, and the
        exact ``spent`` audit), the still-current nonterminal reserved
        invocation, then CASes ``decision_state ready -> claimed`` with exactly
        one closed ``decision_claimed`` audit.  ONLY a ``claimed`` return
        authorizes exactly one ordinary consumer entry.  A duplicate/restarted
        ``claimed``, or an ``applied``/``refused`` receipt, returns bounded
        ``decision_pending`` and never authorizes the consumer.  A failed
        claim/audit rolls back to ``ready`` and permits an exact retry without
        spending or evaluating again.  This method performs NO consumer call,
        queue call, child creation or task-status effect.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2DecisionClaimOutcome(
                status="decision_pending", reason="transaction_owned",
            )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._claim_v2_decision_dispatch_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                result_id=result_id,
            )
            if outcome.status == "decision_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2DecisionClaimOutcome(
                status="decision_pending", reason="claim_failed",
            )

    def _acknowledge_v2_decision_dispatch_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> AuthorityPolicyV2DecisionAckOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2DecisionAckOutcome:
            return AuthorityPolicyV2DecisionAckOutcome(
                status="ack_pending", reason=reason, **kw,
            )

        code, receipt = self._authenticate_v2_spent_decision_receipt_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_id=result_id,
        )
        if code is not None:
            return _pending(code)
        assert receipt is not None
        envelope = receipt["envelope"]
        base = {
            "attempt_id": receipt["attempt"].attempt_id,
            "candidate_id": receipt["candidate"].candidate_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": receipt["generation_id"],
            "spending_result_id": receipt["spending_result_id"],
            "next_session_id": receipt["next_session_id"],
            "report_digest": receipt["report_digest"],
        }
        event_base = {
            **base, "result_id": result_id,
            "notification_id": receipt["notification"].notification_id,
        }
        claim_event = self._v2_decision_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED, **event_base,
        )
        applied_event = self._v2_decision_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED, **event_base,
        )
        decision_state = envelope.decision_state
        if decision_state == "applied":
            if not self._authenticate_v2_decision_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                expected_events={
                    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED: claim_event,
                    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED: applied_event,
                },
            ):
                return _pending("evidence_drift", **base)
            return AuthorityPolicyV2DecisionAckOutcome(
                status="already_applied_exact", **base, decision_state="applied",
            )
        if decision_state == "refused":
            return _pending("already_refused", **base)
        if decision_state != "claimed":
            return _pending("not_claimed", **base)
        # The historical claim must be exactly one authentic closed event, and
        # no applied/interrupted event may already exist for this receipt.
        if not self._authenticate_v2_decision_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            expected_events={
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED: claim_event,
            },
        ):
            return _pending("evidence_drift", **base)
        # The ordinary consumer may legitimately have completed, replaced or
        # blocked the task: acknowledgement is bookkeeping and never requires
        # or restores the old in_progress projection.
        self._set_v2_decision_state_uncommitted(envelope, "applied")
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            applied_event,
        )
        return AuthorityPolicyV2DecisionAckOutcome(
            status="applied", **base, decision_state="applied",
        )

    @_synchronized
    def acknowledge_authority_policy_v2_decision_dispatch(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> AuthorityPolicyV2DecisionAckOutcome:
        """ONE synchronized atomic ``claimed -> applied`` acknowledgement.

        Called by the SAME winning claim caller after the real ordinary consumer
        returns.  It re-authenticates the complete spent receipt and the single
        closed historical ``decision_claimed`` event, CASes ``claimed ->
        applied`` with exactly one closed ``decision_applied`` audit, and never
        requires, restores or regresses the old task/owner/blocked projection
        (the consumer's independently committed effects and any generation B are
        preserved byte-for-byte).  An exact applied replay is read-only
        ``already_applied_exact`` and NEVER authorizes a second consumer call; a
        failure leaves the receipt ``claimed``.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2DecisionAckOutcome(
                status="ack_pending", reason="transaction_owned",
            )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._acknowledge_v2_decision_dispatch_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                result_id=result_id,
            )
            if outcome.status == "ack_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2DecisionAckOutcome(
                status="ack_pending", reason="ack_failed",
            )

    def _refuse_v2_decision_dispatch_uncommitted(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
        now_dt: datetime,
    ) -> AuthorityPolicyV2DecisionRefusalOutcome:
        def _pending(reason: str, **kw) -> AuthorityPolicyV2DecisionRefusalOutcome:
            return AuthorityPolicyV2DecisionRefusalOutcome(
                status="refusal_pending", reason=reason, **kw,
            )

        code, receipt = self._authenticate_v2_spent_decision_receipt_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_id=result_id,
        )
        if code is not None:
            return _pending(code)
        assert receipt is not None
        envelope = receipt["envelope"]
        base = {
            "attempt_id": receipt["attempt"].attempt_id,
            "candidate_id": receipt["candidate"].candidate_id,
            "envelope_id": envelope.envelope_id,
            "generation_id": receipt["generation_id"],
            "spending_result_id": receipt["spending_result_id"],
            "next_session_id": receipt["next_session_id"],
        }
        event_base = {
            **base, "result_id": result_id,
            "notification_id": receipt["notification"].notification_id,
            "report_digest": receipt["report_digest"],
        }
        interruption_event = self._v2_decision_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
            **event_base,
        )
        claim_event = self._v2_decision_event_payload(
            stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED,
            **event_base,
        )
        decision_state = envelope.decision_state
        if decision_state == "refused":
            if not self._authenticate_v2_decision_event_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                expected_events={
                    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED: claim_event,
                    AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED: interruption_event,
                },
            ):
                return _pending("evidence_drift", **base)
            return AuthorityPolicyV2DecisionRefusalOutcome(
                status="already_refused", **base, decision_state="refused",
                refusal_code=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
            )
        if decision_state == "applied":
            return _pending("already_applied", **base)
        if decision_state != "claimed":
            # Only a committed claim may be interrupted; ``ready`` is not yet
            # consumer-authorized and ``applied`` is terminal.
            return _pending("not_claimable", **base)
        if not self._authenticate_v2_decision_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            expected_events={
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED: claim_event,
            },
        ):
            return _pending("evidence_drift", **base)
        # Escalate ONLY the still-current nonterminal reserved invocation; a
        # cancelled/terminal/replaced task (and any generation B) is preserved
        # exactly, with no task mutation.
        if self._v2_claim_task_owner_current_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            next_session_id=receipt["next_session_id"],
        ):
            cursor = self._conn.execute(
                """UPDATE tasks SET status=?, block_kind=NULL, note=?, updated_at=?
                   WHERE id=? AND cancelled_at IS NULL AND status=?
                     AND block_kind IS NULL AND assigned_agent=?
                     AND current_session_id=?""",
                (
                    TaskStatus.ESCALATED.value,
                    "authority_v2_decision_dispatch_interrupted",
                    now_dt.isoformat(), root_task_id,
                    TaskStatus.IN_PROGRESS.value, manager_agent,
                    receipt["next_session_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("v2 decision refusal task CAS lost")
            self.insert_audit_log_uncommitted(
                root_task_id, manager_agent, "escalation",
                {
                    "reason": "authority_v2_decision_dispatch_interrupted",
                    "attempt_id": receipt["attempt"].attempt_id,
                },
            )
        self._set_v2_decision_state_uncommitted(envelope, "refused")
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            interruption_event,
        )
        return AuthorityPolicyV2DecisionRefusalOutcome(
            status="refused", **base, decision_state="refused",
            refusal_code=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
        )

    @_synchronized
    def refuse_authority_policy_v2_decision_dispatch(
        self, *, root_task_id: str, manager_agent: str, result_id: int,
    ) -> AuthorityPolicyV2DecisionRefusalOutcome:
        """ONE synchronized audited ``claimed -> refused`` interruption refusal.

        An exception or restart after a committed claim (before the effect,
        after an independently committed task/child effect, or after a failed
        acknowledgement) must never re-invoke the ordinary consumer.  This
        writer preserves the spent envelope (E consumed), the causal attempt/
        candidate/pin/evaluation evidence and every already-committed effect,
        escalates ONLY the still-current nonterminal reserved invocation with
        the closed ``decision_dispatch_interrupted`` diagnostic, preserves a
        cancelled/terminal/replaced task and any replacement generation B
        exactly, and retires/settles only the exact owned obligation (never a
        replacement-pointer mutation).  A failed refusal audit/commit rolls
        back only this transaction, leaving the discoverable ``claimed`` state
        and a bounded truthful pending outcome.  An exact refused replay is
        read-only.
        """
        if self._conn.in_transaction:
            return AuthorityPolicyV2DecisionRefusalOutcome(
                status="refusal_pending", reason="transaction_owned",
            )
        now_dt = _now()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._refuse_v2_decision_dispatch_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                result_id=result_id, now_dt=now_dt,
            )
            if outcome.status == "refusal_pending":
                self._conn.rollback()
                return outcome
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            return AuthorityPolicyV2DecisionRefusalOutcome(
                status="refusal_pending", reason="refusal_failed",
            )

    @_synchronized
    def get_authority_policy_v2_decision_receipt_for_result(
        self, *, root_task_id: str, spending_result_id,
    ) -> dict | None:
        """Read-only classification of the exact result-keyed decision receipt.

        Returns ``None`` when no consumed envelope carries this exact spending
        result (the provably ordinary/v1 no-v2 path).  Otherwise returns the
        closed ``decision_state`` plus the exact receipt identity.  A row whose
        canonical body or column preimages are corrupt returns ``{"corrupt":
        True}`` so a caller can NEVER treat a malformed receipt as ordinary
        absence.
        """
        if not self._v2_is_int(spending_result_id):
            return None
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_continue_envelopes
               WHERE root_task_id=? AND spending_result_id=?""",
            (root_task_id, spending_result_id),
        ).fetchone()
        if row is None:
            return None
        try:
            envelope = self._authority_policy_v2_envelope_from_row(row)
        except ValueError:
            return {"corrupt": True, "spending_result_id": spending_result_id}
        return {
            "corrupt": False,
            "envelope_id": envelope.envelope_id,
            "candidate_id": envelope.candidate_id,
            "attempt_id": envelope.attempt_id,
            "manager_agent": envelope.manager_agent,
            "result_id": envelope.result_id,
            "spending_result_id": envelope.spending_result_id,
            "decision_state": envelope.decision_state,
        }

    def _v2_terminal_decision_receipt_authenticated_uncommitted(
        self, *, root_task_id: str, envelope,
    ) -> bool:
        """True only when a consumed terminal envelope is an EXACT genuine receipt.

        The terminal ``applied``/``refused`` decision is trusted only after the
        existing exact spent-receipt authenticator re-derives the complete
        evidence for this envelope: the retained ``decision_claimed`` (plus
        ``decision_applied``/``decision_dispatch_interrupted``) events, the
        persisted R2 ``report_digest``, the publication/settlement proof, both
        generation-admission events and the bound reservation.  A mutated or
        corrupt terminal row therefore keeps the lineage live and fail-closed
        instead of authorizing ordinary effects.
        """
        result_id = envelope.result_id
        manager_agent = envelope.manager_agent
        if not (
            self._v2_is_int(result_id)
            and isinstance(manager_agent, str)
            and manager_agent
        ):
            return False
        code, receipt = self._authenticate_v2_spent_decision_receipt_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            result_id=result_id,
        )
        if code is not None or receipt is None:
            return False
        if receipt["envelope"].envelope_id != envelope.envelope_id:
            return False
        # The terminal decision-state writes are authenticated with the SAME
        # complete closed decision-event set the ack/refusal writers require, so
        # a mutated preceding ``decision_claimed`` (or applied/interrupted) row
        # can never be treated as a genuine terminal receipt.
        event_base = {
            "attempt_id": receipt["attempt"].attempt_id,
            "candidate_id": receipt["candidate"].candidate_id,
            "result_id": result_id,
            "envelope_id": envelope.envelope_id,
            "notification_id": receipt["notification"].notification_id,
            "generation_id": receipt["generation_id"],
            "next_session_id": receipt["next_session_id"],
            "spending_result_id": receipt["spending_result_id"],
            "report_digest": receipt["report_digest"],
        }
        if envelope.decision_state == "applied":
            expected_events = {
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED:
                    self._v2_decision_event_payload(
                        stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED,
                        **event_base,
                    ),
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED:
                    self._v2_decision_event_payload(
                        stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED,
                        **event_base,
                    ),
            }
        elif envelope.decision_state == "refused":
            expected_events = {
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED:
                    self._v2_decision_event_payload(
                        stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED,
                        **event_base,
                    ),
                AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED:
                    self._v2_decision_event_payload(
                        stage=AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED,
                        **event_base,
                    ),
            }
        else:
            return False
        return self._authenticate_v2_decision_event_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            expected_events=expected_events,
        )

    def _v2_root_lineage_live_uncommitted(
        self, *, root_task_id, dispatch_row, envelopes, attempts,
    ) -> bool:
        """True while a v2 authority obligation still owns the root.

        A retired dispatch whose EVERY retained receipt is fully consumed and
        whose exact terminal decision evidence still authenticates is terminal;
        every envelope exhausted and every attempt finalized also ends the
        lineage.  Terminal flags, a latest ``E`` row or an absent exact receipt
        are NEVER sufficient on their own: the exact spent-receipt evidence is
        re-authenticated, and an older live/corrupt generation behind a terminal
        pointer keeps the lineage live and fail-closed, so a healthy later
        (ordinary) completion is never authorized by a corrupt proof.
        """
        if dispatch_row is not None:
            try:
                dispatch = self._authority_policy_v2_root_dispatch_from_row(
                    dispatch_row
                )
            except ValueError:
                return True
            if dispatch.state != "retired":
                return True
            notification_row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE notification_id=?",
                (dispatch.generation_id,),
            ).fetchone()
            if notification_row is None:
                return True
            try:
                notification = self._authority_policy_v2_notification_from_row(
                    notification_row
                )
            except ValueError:
                return True
            envelope_row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE envelope_id=?",
                (notification.envelope_id,),
            ).fetchone()
            if envelope_row is None:
                return True
            try:
                self._authority_policy_v2_envelope_from_row(envelope_row)
            except ValueError:
                return True
        for envelope_row in envelopes:
            try:
                envelope = self._authority_policy_v2_envelope_from_row(envelope_row)
            except ValueError:
                return True
            if envelope.lifecycle_state != "consumed":
                return True
            if envelope.decision_state not in ("applied", "refused"):
                return True
            if not self._v2_terminal_decision_receipt_authenticated_uncommitted(
                root_task_id=root_task_id, envelope=envelope,
            ):
                return True
        if dispatch_row is None:
            for attempt in attempts:
                if attempt["finalization_state"] not in (
                    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                ):
                    return True
        # No retained envelope at all with a live pointer is never terminal.
        return not envelopes

    def _v2_later_result_provenance_uncommitted(
        self, *, root_task_id: str, row, envelopes,
    ) -> bool:
        """True only for a GENUINE later result on a fully terminal v2 root.

        A result may be ordinary-capable on a terminal lineage only when it was
        produced by the root's CURRENT durable owner/session -- the same
        ``(assigned_agent, current_session_id)`` identity the supported callback
        admission enforces -- and is genuinely LATER than every retained
        terminal v2 evidence row (causal result and spending result), so an
        older ordinary row can never be replayed as a fresh ordinary effect.

        A larger/latest row id alone, the same ``task_id``, an arbitrary bare or
        empty session, a wrong agent or terminal flags are NEVER sufficient.
        ``row`` is the already-fetched ``task_results`` row for this exact root.
        """
        task = self._conn.execute(
            "SELECT status, cancelled_at, assigned_agent, current_session_id "
            "FROM tasks WHERE id=?",
            (root_task_id,),
        ).fetchone()
        if task is None or task["cancelled_at"] is not None:
            return False
        if task["status"] in ("completed", "failed", "cancelled", "superseded"):
            return False
        owner = task["assigned_agent"]
        session = task["current_session_id"]
        if not (isinstance(owner, str) and owner):
            return False
        if not (isinstance(session, str) and session):
            return False
        if row["agent"] != owner or row["session_id"] != session:
            return False
        latest_evidence_id = 0
        for envelope_row in envelopes:
            try:
                envelope = self._authority_policy_v2_envelope_from_row(envelope_row)
            except ValueError:
                return False
            for candidate in (envelope.result_id, envelope.spending_result_id):
                if self._v2_is_int(candidate) and candidate > latest_evidence_id:
                    latest_evidence_id = candidate
        if not self._v2_is_int(row["id"]) or row["id"] <= latest_evidence_id:
            return False
        return True

    @_synchronized
    def authority_policy_v2_completion_dispatch_context(
        self, *, root_task_id: str, result_row_id,
    ) -> AuthorityPolicyV2CompletionDispatchContext:
        """Read-only classification of one completion against the v2 lineage.

        Uses REAL persisted provenance (the attempt journal, continuation
        envelopes and the root dispatch pointer) -- never reader absence or a
        mock ``None``.  ``no_v2`` is returned only when the root has no
        finalized v2 lineage at all.  A fully terminal generation (its exact
        spent receipt already ``applied``/``refused``) classifies a GENUINE
        later result -- one produced by the root's current durable owner/session
        and genuinely later than the terminal evidence -- as ``later``; every
        other later/foreign identity is ``foreign`` and never ordinary
        permission.  The causal result R of any attempt is
        ``causal``; an exact result-keyed receipt is ``receipt``; the active
        reserved next result of the current generation is ``reserved``.
        """
        def _ctx(kind, **kw) -> AuthorityPolicyV2CompletionDispatchContext:
            return AuthorityPolicyV2CompletionDispatchContext(kind=kind, **kw)

        attempts = self._conn.execute(
            "SELECT * FROM authority_policy_v2_attempts WHERE root_task_id=?",
            (root_task_id,),
        ).fetchall()
        dispatch_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (root_task_id,),
        ).fetchone()
        envelopes = self._conn.execute(
            "SELECT * FROM authority_policy_v2_continue_envelopes "
            "WHERE root_task_id=?",
            (root_task_id,),
        ).fetchall()
        if not envelopes and dispatch_row is None:
            # Provably no finalized v2 generation: either no v2 lineage at all,
            # or only PRE-FINAL attempts/candidates (a live-owner duplicate
            # loses the candidate claim there, and a refused attempt is
            # terminal).  The ordinary path is unchanged in both cases.
            return _ctx("no_v2")

        row = None
        if self._v2_is_int(result_row_id):
            row = self._conn.execute(
                "SELECT * FROM task_results WHERE id=? AND task_id=?",
                (result_row_id, root_task_id),
            ).fetchone()

        # 1. Causal R: the causal result of a FINALIZED v2 generation (an
        # attempt that owns an envelope).  An unfinalized attempt whose causal
        # result is the reserved R2 itself (a genuine NEW R2 authority attempt)
        # is NOT a causal replay -- it is exactly the reserved next result.
        if row is not None:
            parsed_envelopes = []
            for envelope_row in envelopes:
                try:
                    parsed_envelopes.append(
                        self._authority_policy_v2_envelope_from_row(envelope_row)
                    )
                except ValueError:
                    continue
            for envelope in parsed_envelopes:
                if (
                    self._v2_is_int(envelope.result_id)
                    and envelope.result_id == result_row_id
                ):
                    return _ctx(
                        "causal", manager_agent=row["agent"],
                        causal_result_id=result_row_id,
                    )
            # 2. The exact result-keyed receipt of ANY generation.
            for envelope in parsed_envelopes:
                if (
                    self._v2_is_int(envelope.spending_result_id)
                    and envelope.spending_result_id == result_row_id
                ):
                    return _ctx(
                        "receipt",
                        manager_agent=envelope.manager_agent,
                        manager_session_id=envelope.manager_session_id,
                        causal_result_id=envelope.result_id,
                        spending_result_id=result_row_id,
                        decision_state=envelope.decision_state,
                    )

        # 3. On a root with v2 history, an identity with no real persisted
        # result row for this root is a malformed/foreign identity -- never the
        # ordinary/v1 absence path.  (Provably ordinary roots already returned
        # above, so ``None``/bool/string/unknown integers fail closed here.)
        if row is None:
            return _ctx("foreign")

        # 4. Every other identity on a FULLY TERMINAL lineage whose exact
        # terminal evidence still authenticates may be ordinary again ONLY when
        # it is a genuine later result/session/owner with real persisted
        # provenance (and, at the common gate, a supplied report that matches
        # its persisted material identity).  A bare arbitrary session, an empty
        # session, a wrong agent, a larger/latest row id, terminal flags or the
        # mere absence of an exact receipt are never sufficient; an
        # older/live/corrupt generation behind a terminal pointer keeps the
        # lineage live and fail-closed.
        if not self._v2_root_lineage_live_uncommitted(
            root_task_id=root_task_id, dispatch_row=dispatch_row,
            envelopes=envelopes, attempts=attempts,
        ):
            if self._v2_later_result_provenance_uncommitted(
                root_task_id=root_task_id, row=row, envelopes=envelopes,
            ):
                return _ctx("later")
            return _ctx("foreign")

        # 5. The current generation's active reserved next result R2.
        if dispatch_row is not None:
            try:
                dispatch = self._authority_policy_v2_root_dispatch_from_row(
                    dispatch_row
                )
            except ValueError:
                return _ctx("foreign")
            notification_row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE notification_id=?",
                (dispatch.generation_id,),
            ).fetchone()
            if notification_row is None:
                return _ctx("foreign")
            try:
                notification = self._authority_policy_v2_notification_from_row(
                    notification_row
                )
            except ValueError:
                return _ctx("foreign")
            envelope_row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE envelope_id=?",
                (notification.envelope_id,),
            ).fetchone()
            if envelope_row is None:
                return _ctx("foreign")
            try:
                envelope = self._authority_policy_v2_envelope_from_row(envelope_row)
            except ValueError:
                return _ctx("foreign")
            if (
                dispatch.state == "admitted"
                and notification.state == "settled"
                and envelope.lifecycle_state == "active"
                and envelope.spending_result_id is None
                and envelope.decision_state is None
                and isinstance(notification.next_session_id, str)
                and notification.next_session_id
                and row["agent"] == envelope.manager_agent
                and row["session_id"] == notification.next_session_id
            ):
                return _ctx(
                    "reserved",
                    manager_agent=envelope.manager_agent,
                    manager_session_id=envelope.manager_session_id,
                    causal_result_id=envelope.result_id,
                    generation_id=dispatch.generation_id,
                    next_session_id=notification.next_session_id,
                    spending_result_id=result_row_id,
                )
        return _ctx("foreign")

    @classmethod
    def _v2_parse_material_decision(cls, raw):
        """Normalize one ``decision_json`` through the shipping NextStep carrier.

        The reserved ``_manager_self_evaluation`` key is stripped exactly as
        ``completion_report_from_result_row`` strips it.  Returns the parsed
        ``NextStep`` JSON projection, ``None`` for an absent decision, or the
        malformed sentinel for a present non-object/unparseable/invalid body.
        """
        if raw is None or raw == "":
            return None
        try:
            parsed = (
                json.loads(raw)
                if isinstance(raw, (str, bytes, bytearray)) else raw
            )
        except (ValueError, TypeError):
            return _V2_MALFORMED_DECISION
        if not isinstance(parsed, dict):
            return _V2_MALFORMED_DECISION
        parsed.pop("_manager_self_evaluation", None)
        try:
            return NextStep(**parsed).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError):
            return _V2_MALFORMED_DECISION

    @classmethod
    def _v2_parse_material_list(cls, raw):
        """Normalize one persisted/collection JSON list the shipping way."""
        if raw is None or raw == "":
            return []
        try:
            parsed = (
                json.loads(raw)
                if isinstance(raw, (str, bytes, bytearray)) else raw
            )
        except (ValueError, TypeError):
            return _V2_MALFORMED_DECISION
        return parsed

    @classmethod
    def _v2_parse_material_local_ci(cls, raw):
        """Normalize persisted local-CI evidence the shipping way.

        An invalid local-CI body is treated as absent exactly like
        ``completion_report_from_result_row`` (it does not invent a model), while
        a present non-object carrier is a malformed conflict.
        """
        if raw is None or raw == "":
            return None
        try:
            parsed = (
                json.loads(raw)
                if isinstance(raw, (str, bytes, bytearray)) else raw
            )
        except (ValueError, TypeError):
            return _V2_MALFORMED_DECISION
        if not isinstance(parsed, dict):
            return _V2_MALFORMED_DECISION
        try:
            return LocalCiEvidence(**parsed).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError):
            return None

    @classmethod
    def _v2_completion_material_projection_from_row(cls, row) -> dict | None:
        """The materially consumed completion projection of one persisted row."""
        row = dict(row)
        decision = cls._v2_parse_material_decision(row.get("decision_json"))
        risks = cls._v2_parse_material_list(row.get("risks_flagged"))
        waiting = cls._v2_parse_material_list(row.get("waiting_on_job_ids"))
        local_ci = cls._v2_parse_material_local_ci(row.get("local_ci"))
        if (
            decision is _V2_MALFORMED_DECISION
            or risks is _V2_MALFORMED_DECISION
            or waiting is _V2_MALFORMED_DECISION
            or local_ci is _V2_MALFORMED_DECISION
        ):
            return None
        return {
            "output_summary": row.get("output_summary") or "",
            "confidence": row.get("confidence_score") or 0,
            "status": row.get("status") or "completed",
            "output_dir": row.get("output_dir"),
            "verdict": row.get("verdict"),
            "risks_flagged": risks,
            "waiting_on_job_ids": waiting,
            "local_ci": local_ci,
            "decision": decision,
        }

    @classmethod
    def _v2_completion_material_projection_from_report(cls, report) -> dict | None:
        """The materially consumed projection of one supplied completion."""
        decision = getattr(report, "decision", None)
        if decision is not None:
            if hasattr(decision, "model_dump"):
                decision = decision.model_dump(mode="json")
            elif isinstance(decision, dict):
                try:
                    decision = NextStep(**decision).model_dump(mode="json")
                except (ValidationError, ValueError, TypeError):
                    return None
            else:
                return None
        local_ci = getattr(report, "local_ci", None)
        if local_ci is not None:
            if hasattr(local_ci, "model_dump"):
                local_ci = local_ci.model_dump(mode="json")
            elif isinstance(local_ci, dict):
                try:
                    local_ci = LocalCiEvidence(**local_ci).model_dump(mode="json")
                except (ValidationError, ValueError, TypeError):
                    return None
            else:
                return None
        return {
            "output_summary": getattr(report, "output_summary", None) or "",
            "confidence": getattr(report, "confidence", None),
            "status": getattr(report, "status", None),
            "output_dir": getattr(report, "output_dir", None),
            "verdict": getattr(report, "verdict", None),
            "risks_flagged": getattr(report, "risks_flagged", None) or [],
            "waiting_on_job_ids": getattr(report, "waiting_on_job_ids", None) or [],
            "local_ci": local_ci,
            "decision": decision,
        }

    @_synchronized
    def authority_policy_v2_decision_result_report_binds(
        self, *, root_task_id: str, spending_result_id, report,
    ) -> bool:
        """True only when the supplied report IS the materially exact R2 body.

        Both sides are normalized through the ESTABLISHED shipping
        representation (``completion_report_from_result_row``): the persisted
        ``confidence_score`` column maps to the model ``confidence`` field and
        the parsed ``NextStep`` decision is compared field-for-field, so a
        same-action decision with a different ``summary``/``agent``/``prompt``/
        ``then``/``children``/``revisit``/``attachments`` (or any other effective
        drift) refuses before any effect.  The persisted full material digest
        bound into the durable ``spent`` receipt is unchanged and remains the
        separate authority over the raw wire carrier.
        """
        if not self._v2_is_int(spending_result_id):
            return False
        row = self._conn.execute(
            "SELECT * FROM task_results WHERE id=? AND task_id=?",
            (spending_result_id, root_task_id),
        ).fetchone()
        if row is None:
            return False
        try:
            persisted = self._v2_completion_material_projection_from_row(row)
            supplied = self._v2_completion_material_projection_from_report(report)
        except Exception:
            return False
        if persisted is None or supplied is None:
            return False
        return self._v2_json_type_sensitive_equal(persisted, supplied)

    @_synchronized
    def get_authority_policy_v2_continue_envelope(
        self, envelope_id: str,
    ) -> AuthorityPolicyV2ContinueEnvelope | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_continue_envelopes WHERE envelope_id=?",
            (envelope_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_envelope_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_continue_envelope_for_candidate(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2ContinueEnvelope | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_continue_envelopes WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_envelope_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_continue_envelope_for_root(
        self, root_task_id: str,
    ) -> AuthorityPolicyV2ContinueEnvelope | None:
        """Return the active v2 envelope for a root (if any)."""
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_continue_envelopes
               WHERE root_task_id=? AND lifecycle_state='active'
               ORDER BY created_at, envelope_id LIMIT 1""",
            (root_task_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_envelope_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_recovery_notification(
        self, notification_id: str,
    ) -> AuthorityPolicyV2RecoveryNotification | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_recovery_notifications
               WHERE notification_id=?""",
            (notification_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_notification_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_recovery_notification_for_envelope(
        self, envelope_id: str,
    ) -> AuthorityPolicyV2RecoveryNotification | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_recovery_notifications
               WHERE envelope_id=?""",
            (envelope_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_notification_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_root_dispatch(
        self, root_task_id: str,
    ) -> AuthorityPolicyV2RootDispatch | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (root_task_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_root_dispatch_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def classify_authority_policy_v2_root_dispatch_for_enqueue(
        self, root_task_id: str,
    ) -> AuthorityPolicyV2EnqueueDispatchClassification:
        """PRODUCTION-time classification of one root's durable dispatch pointer.

        ``get_authority_policy_v2_root_dispatch`` collapses a genuinely absent
        row and a present-but-malformed row into the same ``None``.  The common
        DB-aware enqueue boundary must not turn that ambiguity into ordinary
        enqueue permission, so this narrow read distinguishes them:

        * ``absent``    -- no row: the unchanged ordinary enqueue path;
        * ``pending``   -- live generation G; the target must be published
          through the authenticated notification publisher, never an ordinary
          untagged fallback;
        * ``admitted``  -- G already reserved/launched; an ordinary enqueue must
          not relaunch it;
        * ``retired``   -- an old spent generation; legitimate later work stays
          ordinary (retirement never blanket-blocks);
        * ``malformed`` -- a present row whose canonical/preimage check fails;
        * ``unreadable``-- the read itself raised.

        Read-only evidence; no mutation, no new authority semantics.
        """
        try:
            row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
                (root_task_id,),
            ).fetchone()
        except Exception:
            return AuthorityPolicyV2EnqueueDispatchClassification(
                kind="unreadable",
            )
        if row is None:
            return AuthorityPolicyV2EnqueueDispatchClassification(kind="absent")
        try:
            dispatch = self._authority_policy_v2_root_dispatch_from_row(row)
        except ValueError:
            return AuthorityPolicyV2EnqueueDispatchClassification(
                kind="malformed",
            )
        if dispatch.state == "pending":
            return AuthorityPolicyV2EnqueueDispatchClassification(
                kind="pending", generation_id=dispatch.generation_id,
            )
        if dispatch.state == "admitted":
            return AuthorityPolicyV2EnqueueDispatchClassification(
                kind="admitted", generation_id=dispatch.generation_id,
            )
        return AuthorityPolicyV2EnqueueDispatchClassification(
            kind="retired", generation_id=dispatch.generation_id,
        )

    @staticmethod
    def _zombie_marker_value(value) -> str | None:
        if isinstance(value, datetime):
            return value.isoformat()
        return value if isinstance(value, str) and value else None

    def _authenticate_exact_v2_zombie_binding_uncommitted(
        self, *, task_id: str, agent: str, session_id: str,
    ) -> AuthorityPolicyV2SessionBinding | None:
        """Authenticate the causal v2 launch binding, with no mixed fallback."""
        try:
            binding = self.get_authority_policy_v2_session_binding(
                root_task_id=task_id, manager_agent=agent,
                manager_session_id=session_id,
            )
            if binding is None:
                return None
            if self._legacy_session_binding_rows_uncommitted(
                task_id, agent, session_id,
            ):
                return None
            self._authenticate_v2_session_binding_uncommitted(binding)
        except Exception:
            return None
        if (
            binding.root_task_id != task_id
            or binding.manager_agent != agent
            or binding.manager_session_id != session_id
        ):
            return None
        return binding

    def _authenticate_v2_zombie_consumption_receipt_uncommitted(
        self, *, task_id: str, agent: str, session_id: str, result_id: int,
        task_row,
    ) -> bool:
        """Authenticate the unique terminal-refusal or continued receipt for R."""
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (task_id, agent, session_id, result_id),
        ).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return False
        if attempt.finalization_state == "continued":
            code, _ = self._authenticate_v2_post_final_evidence_uncommitted(
                root_task_id=task_id, manager_agent=agent,
                manager_session_id=session_id, result_id=result_id,
            )
            return (
                code is None
                and task_row["status"] == TaskStatus.PENDING.value
                and task_row["block_kind"] is None
            )
        if attempt.finalization_state != AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED:
            return False
        candidate_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE result_id=?",
            (result_id,),
        ).fetchone()
        candidate = None
        if candidate_row is not None:
            try:
                candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
            except ValueError:
                return False
            if candidate.attempt_id != attempt.attempt_id:
                return False
        candidate_id = None if candidate is None else candidate.candidate_id
        attempt_row = dict(row)
        if not self._authenticate_v2_refusal_result_stage_uncommitted(
            attempt_row, candidate_id=candidate_id,
            refusal_code=attempt.refusal_code,
            finalization_state=attempt.finalization_state,
        ):
            return False
        completion_ok, recovery_claimed = (
            self._authenticate_v2_refusal_completion_uncommitted(
                attempt_row, refusal_code=attempt.refusal_code,
            )
        )
        if not completion_ok:
            return False
        if recovery_claimed:
            receipts = self._conn.execute(
                """SELECT * FROM task_completion_recoveries
                   WHERE task_id=? AND agent=? AND recovery_session_id=?""",
                (task_id, agent, session_id),
            ).fetchall()
            if len(receipts) != 1 or not (
                receipts[0]["state"] == "callback_consumed"
                and receipts[0]["accepted_result_id"] == result_id
                and receipts[0]["accepted_result_session_id"] == session_id
            ):
                return False
        if not self._authenticate_v2_refusal_escalation_uncommitted(
            attempt_row, refusal_code=attempt.refusal_code,
        ):
            return False
        if candidate is not None and not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
        ):
            return False
        return (
            task_row["status"] == TaskStatus.ESCALATED.value
            and task_row["block_kind"] is None
        )

    @_synchronized
    def consume_v2_fingerprint_and_clear_zombie(
        self, *, task_id: str, expected_agent: str,
        expected_session_id: str, result_id: int,
        expected_zombie_flagged_at,
    ) -> bool:
        """Clear a v2 zombie marker only after exact R consumption committed.

        The real completion consumer runs before this transaction.  This CAS
        then authenticates the immutable result/session binding and the unique
        v2 continued/refused receipt while holding ``BEGIN IMMEDIATE``.  Any
        owner, result, marker, cancellation or lifecycle race is a zero-write
        denial and retains the winning marker/result history.
        """
        marker = self._zombie_marker_value(expected_zombie_flagged_at)
        if (
            marker is None or isinstance(result_id, bool)
            or not isinstance(result_id, int) or result_id < 1
            or self._conn.in_transaction
        ):
            return False
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            result = self._conn.execute(
                "SELECT * FROM task_results WHERE id=?", (result_id,),
            ).fetchone()
            if (
                task is None or result is None
                or task["assigned_agent"] != expected_agent
                or task["current_session_id"] != expected_session_id
                or task["cancelled_at"] is not None
                or task["zombie_flagged_at"] != marker
                or result["task_id"] != task_id
                or result["agent"] != expected_agent
                or result["session_id"] != expected_session_id
                or self._authenticate_exact_v2_zombie_binding_uncommitted(
                    task_id=task_id, agent=expected_agent,
                    session_id=expected_session_id,
                ) is None
                or not self._authenticate_v2_zombie_consumption_receipt_uncommitted(
                    task_id=task_id, agent=expected_agent,
                    session_id=expected_session_id, result_id=result_id,
                    task_row=task,
                )
            ):
                self._conn.rollback()
                return False
            cursor = self._conn.execute(
                """UPDATE tasks SET zombie_flagged_at=NULL, updated_at=?
                   WHERE id=? AND assigned_agent=? AND current_session_id=?
                     AND cancelled_at IS NULL AND zombie_flagged_at=?""",
                (_now().isoformat(), task_id, expected_agent,
                 expected_session_id, marker),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            self.insert_audit_log_uncommitted(
                task_id, expected_agent, "zombie_cleared",
                {"reason": "zombie recovered — flag cleared"},
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def cancel_zombie_without_fingerprint(
        self, *, task_id: str, expected_agent: str,
        expected_session_id: str, expected_zombie_flagged_at,
        cancelled_at: str,
    ) -> bool:
        """Atomically cancel one exact v2 zombie only while R remains absent."""
        marker = self._zombie_marker_value(expected_zombie_flagged_at)
        if marker is None or not isinstance(cancelled_at, str) or self._conn.in_transaction:
            return False
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if (
                task is None
                or task["assigned_agent"] != expected_agent
                or task["current_session_id"] != expected_session_id
                or task["status"] != TaskStatus.IN_PROGRESS.value
                or task["block_kind"] is not None
                or task["cancelled_at"] is not None
                or task["zombie_flagged_at"] != marker
                or self._authenticate_exact_v2_zombie_binding_uncommitted(
                    task_id=task_id, agent=expected_agent,
                    session_id=expected_session_id,
                ) is None
            ):
                self._conn.rollback()
                return False
            result = self._conn.execute(
                """SELECT 1 FROM task_results
                   WHERE task_id=? AND agent=? AND session_id=? LIMIT 1""",
                (task_id, expected_agent, expected_session_id),
            ).fetchone()
            if result is not None:
                self._conn.rollback()
                return False
            cursor = self._conn.execute(
                """UPDATE tasks
                      SET status=?, cancelled_at=?, completed_at=?, block_kind=NULL,
                          note=?, updated_at=?
                    WHERE id=? AND assigned_agent=? AND current_session_id=?
                      AND status=? AND block_kind IS NULL AND cancelled_at IS NULL
                      AND zombie_flagged_at=?
                      AND NOT EXISTS (
                          SELECT 1 FROM task_results
                           WHERE task_id=? AND agent=? AND session_id=?
                      )""",
                (
                    TaskStatus.CANCELLED.value, cancelled_at, cancelled_at,
                    "zombie reaped: session died without completing",
                    cancelled_at, task_id, expected_agent, expected_session_id,
                    TaskStatus.IN_PROGRESS.value, marker,
                    task_id, expected_agent, expected_session_id,
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            self.insert_audit_log_uncommitted(
                task_id, expected_agent, "zombie_cancelled",
                {"reason": "zombie cancelled after TTL expiry"},
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def get_task_results(self, task_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM task_results WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            if d.get("waiting_on_job_ids"):
                d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
            result.append(d)
        return result

    @_synchronized
    def get_agent_task_results(self, agent: str, since: str | None = None) -> list[dict]:
        if since:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? AND created_at >= ? ORDER BY id",
                (agent, since),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? ORDER BY id", (agent,)
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            if d.get("waiting_on_job_ids"):
                d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
            result.append(d)
        return result



    @_synchronized
    def get_latest_task_result(
        self, task_id: str, agent: str, session_id: str,
    ) -> dict | None:
        cursor = self._conn.execute(
            """SELECT * FROM task_results
               WHERE task_id = ? AND agent = ? AND session_id = ?
               ORDER BY id DESC LIMIT 1""",
            (task_id, agent, session_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("risks_flagged"):
            d["risks_flagged"] = json.loads(d["risks_flagged"])
        if d.get("waiting_on_job_ids"):
            d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
        return d


    @_synchronized
    def get_latest_completion_report(
        self, task_id: str, agent: str | None = None, session_id: str | None = None,
    ):
        """Return the most-recent task_results row for the given task as a
        CompletionReport, or None if no row exists.

        Used by the chain-advance logic in run_step to read the just-completed
        child's verdict without requiring the caller to know agent/session_id.

        THR-211: when ``agent`` AND ``session_id`` are both provided the lookup
        is scoped to the exact ``(task_id, agent, session_id)`` fingerprint
        (the same authority the boot sweep / zombie reaper use) so a newer
        unrelated row can never substitute for the authenticated report.
        Without the scope the most-recent row is returned (legacy behavior).

        THR-211 (TASK-5823): for the exact-fingerprint scope, a row whose
        persisted structured fields fail deserialization/structural
        validation (invalid JSON in ``risks_flagged`` / ``waiting_on_job_ids``,
        or values failing the strict ``CompletionReport`` contract) has NO
        acceptable authenticated report: it returns ``None`` so the caller's
        existing fail-closed path applies (chain cleared, parent woken once,
        task-wide evidence never consulted).  Only ``json.JSONDecodeError`` and
        ``pydantic.ValidationError`` are converted; SQLite, transaction, I/O,
        programming, and unrelated operational exceptions still propagate.
        The unscoped (legacy) read keeps its prior behavior.
        """
        from pydantic import ValidationError

        if agent is not None and session_id is not None:
            row = self._conn.execute(
                "SELECT * FROM task_results WHERE task_id = ? "
                "AND agent = ? AND session_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (task_id, agent, session_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM task_results WHERE task_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._row_to_completion_report(task_id, row)
        except (json.JSONDecodeError, ValidationError):
            if agent is None or session_id is None:
                # Unscoped (legacy) read: preserve prior behavior — a
                # structurally malformed newest row still surfaces as an
                # error rather than silently degrading the fallback.
                raise
            # Exact modern fingerprint: no acceptable authenticated report.
            return None

    def _row_to_completion_report(self, task_id: str, row) -> "CompletionReport":
        """Build a CompletionReport from a task_results row dict.

        ``risks_flagged`` / ``waiting_on_job_ids`` are persisted as JSON text
        and re-deserialized here; malformed JSON or a value failing the strict
        ``CompletionReport`` contract raises ``json.JSONDecodeError`` /
        ``pydantic.ValidationError``, which the exact-scope caller converts to
        the no-acceptable-report fail-closed outcome.  ``local_ci`` degrades
        to None (documented behavior).
        """
        from runtime.models import CompletionReport, LocalCiEvidence

        keys = row.keys()
        # Safely parse local_ci from the task_results row.
        # A missing legacy column, NULL, empty/malformed JSON, wrong shape,
        # or JSON failing the strict LocalCiEvidence contract → None.
        _local_ci_raw = row["local_ci"] if "local_ci" in keys else None
        _local_ci: LocalCiEvidence | None = None
        if _local_ci_raw:
            try:
                _parsed = json.loads(_local_ci_raw)
                if isinstance(_parsed, dict):
                    _local_ci = LocalCiEvidence(**_parsed)
            except Exception:
                pass
        manager_self_evaluation = None
        raw_decision = row["decision_json"] if "decision_json" in keys else None
        if raw_decision:
            parsed_decision = json.loads(raw_decision)
            if isinstance(parsed_decision, dict):
                manager_self_evaluation = parsed_decision.get(
                    "_manager_self_evaluation"
                )
        return CompletionReport(
            task_id=task_id,
            agent=row["agent"],
            status=row["status"] or "completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            verdict=row["verdict"] if "verdict" in keys else None,
            manager_self_evaluation=manager_self_evaluation,
            output_dir=row["output_dir"] if "output_dir" in keys else None,
            risks_flagged=(
                json.loads(row["risks_flagged"])
                if row["risks_flagged"]
                else []
            ),
            waiting_on_job_ids=(
                json.loads(row["waiting_on_job_ids"])
                if "waiting_on_job_ids" in keys and row["waiting_on_job_ids"]
                else []
            ),
            local_ci=_local_ci,
        )

    # --- Session Token Usage ---

    # --- KB views ---

    @_synchronized
    def record_kb_view(self, slug: str) -> None:
        """Increment the view counter for a KB entry, stamping last_viewed_at.

        UPSERT: inserts the row at count 1 on first view, otherwise increments
        the existing count. Caller decides *when* to record (agent-CLI reads
        only — see kb-view-tracking-caller-signal). This is a metric write, not
        an audit row; it never routes through audit_log.
        """
        now = _now().isoformat()
        self._conn.execute(
            """INSERT INTO kb_views (slug, view_count, last_viewed_at)
               VALUES (?, 1, ?)
               ON CONFLICT(slug) DO UPDATE SET
                 view_count = view_count + 1,
                 last_viewed_at = excluded.last_viewed_at""",
            (slug, now),
        )
        self._conn.commit()

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
    def handoff_consumed_task_completion_recovery_parent_effect(
        self, *, task_id: str, agent: str, recovery_session_id: str,
        result_row_id: int, terminal_status: str, effect: Callable[[], None],
    ) -> bool:
        """Run the bounded parent-state effect while this receipt is current.

        Recovery job termination deliberately happens before this method and
        outside the database lock.  The supplied effect is limited to the
        synchronous parent/chain wake; callers must run notification delivery
        after this method returns.  Keeping the final predicate and mutation in
        one re-entrant database critical section prevents a replacement owner
        from entering between them.
        """
        if not self.consumed_task_completion_recovery_owner_is_current(
            task_id=task_id, agent=agent,
            recovery_session_id=recovery_session_id,
            result_row_id=result_row_id, terminal_status=terminal_status,
        ):
            return False
        effect()
        return True

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

    @_synchronized
    def insert_task_with_attachments(
        self,
        task: "TaskRecord",
        attachments: list[dict],
        uploaded_by: str,
    ) -> None:
        """Atomically insert a task + its private attachment links + audit rows.

        Everything within one BEGIN IMMEDIATE / COMMIT transaction so a
        duplicate-storage-key UNIQUE violation, a link-write error, or an
        audit-write error rolls back the task row and every prior link.

        Caller MUST hold ``org.db_lock`` — the claimability re-check
        (SELECT by storage_key) runs inside the same serialized boundary.

        Raises ``sqlite3.IntegrityError`` when a storage_key has already
        been claimed by another task — the caller must translate this to a
        conflict response.
        """
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
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
            for att in attachments:
                # Reject if storage_key is already claimed — including by
                # legacy duplicate rows excluded from the partial unique index.
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
                        task.id,
                        att["ordinal"],
                        att["storage_key"],
                        att["display_name"],
                        att["size_bytes"],
                        att["content_type"],
                        uploaded_by,
                        now,
                    ),
                )
                # Audit row for each linked attachment.
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
                    (task.id, uploaded_by, "task_attachment_added", audit_payload, audit_ts),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

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
    def dispatch_task_followup_replacement(
        self,
        *,
        token: str,
        thread_id: str,
        dispatcher: str,
        task: TaskRecord,
        team: str,
    ) -> dict:
        """Atomically admit the single replacement allowed to a task follow-up.

        The triggering SYSTEM message and the dedicated audit row are the
        existing persisted authority for the causal-root/replacement relation.
        No task column is repurposed.  The caller must hold the teams registry
        lock after establishing that ``dispatcher`` is the team's manager.
        """
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                """SELECT i.triggering_seq, i.dispatched_task_id,
                          m.system_payload_json
                     FROM thread_invocations i
                     JOIN threads t ON t.id = i.thread_id
                     LEFT JOIN thread_messages m
                       ON m.thread_id = i.thread_id AND m.seq = i.triggering_seq
                    WHERE i.invocation_token = ? AND i.thread_id = ?
                      AND i.agent_name = ? AND i.purpose = 'task_followup'
                      AND i.status = 'pending' AND t.status = 'open'""",
                (token, thread_id, dispatcher),
            ).fetchone()
            if row is None:
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_stale"}
            if row["dispatched_task_id"] is not None:
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_already_used"}
            try:
                payload = json.loads(row["system_payload_json"] or "null")
            except (TypeError, json.JSONDecodeError):
                payload = None
            if not isinstance(payload, dict):
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_cause_invalid"}
            original_id = payload.get("original_task_id")
            source_id = payload.get("task_id")
            if (
                payload.get("kind_tag") not in {"task_completed", "task_failed", "task_escalated"}
                or not isinstance(original_id, str) or not original_id
                or not isinstance(source_id, str) or not source_id
            ):
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_cause_invalid"}
            source = self._conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (source_id,),
            ).fetchone()
            expected_status = payload.get("status")
            if (
                source is None
                or expected_status != source["status"]
                or source["status"] not in {
                    "completed", "failed", "cancelled", "superseded", "escalated",
                }
            ):
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_cause_invalid"}
            in_lineage = self._conn.execute(
                """WITH RECURSIVE lineage(id, parent_task_id, revisit_of_task_id, depth) AS (
                       SELECT id, parent_task_id, revisit_of_task_id, 0
                         FROM tasks WHERE id = ?
                       UNION
                       SELECT t.id, t.parent_task_id, t.revisit_of_task_id, l.depth + 1
                         FROM lineage l JOIN tasks t
                           ON t.id = l.parent_task_id OR t.id = l.revisit_of_task_id
                        WHERE l.depth < 399
                   ) SELECT 1 FROM lineage WHERE id = ?""",
                (source_id, original_id),
            ).fetchone()
            if in_lineage is None:
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_cause_invalid"}

            # The causal root must either be the original root dispatched from
            # this thread, or a root previously created by this exact contract.
            causal = self._conn.execute(
                "SELECT dispatched_from_thread_id FROM tasks WHERE id = ? AND parent_task_id IS NULL",
                (original_id,),
            ).fetchone()
            prior_replacement = self._conn.execute(
                """SELECT 1 FROM audit_log
                    WHERE task_id = ?
                      AND action = 'thread_task_followup_replacement_dispatched'""",
                (original_id,),
            ).fetchone()
            original_dispatch = self._conn.execute(
                """SELECT 1 FROM audit_log
                    WHERE task_id = ? AND action = 'thread_dispatch'
                      AND json_extract(payload, '$.task_id') = ?
                      AND json_extract(payload, '$.dispatcher') = ?""",
                (thread_id, original_id, dispatcher),
            ).fetchone()
            if causal is None or (
                causal["dispatched_from_thread_id"] != thread_id
                and prior_replacement is None
            ) or (prior_replacement is None and original_dispatch is None):
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_cause_invalid"}

            spent = prior_replacement is not None or self._conn.execute(
                """SELECT 1 FROM audit_log
                    WHERE action = 'thread_task_followup_replacement_dispatched'
                      AND json_extract(payload, '$.original_root_task_id') = ?""",
                (original_id,),
            ).fetchone() is not None
            if spent:
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_already_used"}

            params = (
                task.id, task.status.value, task.assigned_agent, task.team,
                task.brief, task.revision_count, task.created_at.isoformat(),
                task.updated_at.isoformat(), None, None, None,
                task.dispatched_from_thread_id, None, task.note,
                task.orchestration_step_count, task.session_timeout_seconds,
                task.task_type, task.active_fanout, task.current_session_id, None,
            )
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_thread_id, block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type,
                   active_fanout, current_session_id, zombie_flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                params,
            )
            sys_seq = self._append_thread_message_uncommitted(
                thread_id=thread_id, speaker=dispatcher,
                kind=ThreadMessageKind.SYSTEM,
                system_payload={
                    "kind_tag": "task_dispatched", "task_id": task.id,
                    "dispatcher": dispatcher, "target_agent": dispatcher,
                    "team": team, "brief_preview": task.brief[:160],
                    "replacement_for_task_id": original_id,
                },
            )
            changed = self._conn.execute(
                """UPDATE thread_invocations SET dispatched_task_id = ?
                     WHERE invocation_token = ? AND status = 'pending'
                       AND dispatched_task_id IS NULL""",
                (task.id, token),
            )
            if changed.rowcount != 1:
                self._conn.rollback()
                return {"ok": False, "code": "task_followup_dispatch_already_used"}
            audit_payload = {
                "thread_id": thread_id,
                "original_root_task_id": original_id,
                "replacement_root_task_id": task.id,
                "invocation_token_prefix": token[:8],
                "dispatcher": dispatcher,
                "team": team,
                "rule_version": "thr-225-v1",
            }
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                (task.id, dispatcher,
                 "thread_task_followup_replacement_dispatched",
                 json.dumps(audit_payload), now),
            )
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                (thread_id, dispatcher, "thread_dispatch", json.dumps({
                    "task_id": task.id, "dispatcher": dispatcher,
                    "target_agent": dispatcher, "team": team,
                    "replacement_for_task_id": original_id,
                }), now),
            )
            self._conn.commit()
            return {"ok": True, "system_message_seq": sys_seq,
                    "original_root_task_id": original_id}
        except Exception:
            self._conn.rollback()
            raise

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
    def _set_thread_status_archived_uncommitted(
        self, thread_id: str, *, summary: str | None = None,
    ) -> None:
        """Flip one thread to ARCHIVED (summary/archived_at preserved) WITHOUT
        committing. Callers own the transaction
        (``set_thread_status``, ``archive_thread_and_reset_sessions``)."""
        now = _now().isoformat()
        self._conn.execute(
            "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
            "archived_at = COALESCE(archived_at, ?) WHERE id = ?",
            (ThreadStatus.ARCHIVED.value, summary, now, thread_id),
        )


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


    @_synchronized
    def set_thread_pinned_uncommitted(self, thread_id: str, *, pinned: bool) -> None:
        """Set/clear thread pin state WITHOUT committing (THR-209).

        Same contract as ``set_thread_subject_uncommitted``: the caller owns
        the surrounding transaction so the pin transition and its audit row
        are atomic.
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







    @_synchronized
    def terminate_agent_cleanups(
        self, agent_name: str,
        *,
        audit_scope_id: str | None = None,
        audit_agent: str | None = None,
    ) -> None:
        """Atomically cancel/skip/decline all future work for ``agent_name``.

        Runs every cleanup DML statement and each audit write inside ONE
        explicit SQLite transaction (``BEGIN IMMEDIATE`` / ``COMMIT``). On any
        exception the COMPLETE transaction is rolled back BEFORE the exception
        propagates, so control returns to the caller with no open transaction
        and no partial cancellation or audit residue. The caller is responsible
        for archiving the AgentDef/workspace and removing team membership first
        (or rolling them back if this method raises).

        THR-200: when ``audit_scope_id`` is given, the provider-session reset
        (every thread participant row owned by the agent -> id NULL, watermark
        0) and its ``thread_session_invalidated`` audit run inside the SAME
        transaction — a terminated agent must never resume a provider session,
        and a reset/audit failure rolls back the complete cleanup so the agent
        stays fully active with prior session state intact.
        """
        now_iso = _now().isoformat()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Cancel armed schedules.
            schedule_rows = self._conn.execute(
                "SELECT id FROM schedules WHERE agent_name = ? AND status = ?",
                (agent_name, ScheduleStatus.ARMED.value),
            ).fetchall()
            if schedule_rows:
                self._conn.execute(
                    "UPDATE schedules SET status = ?, active = 0, updated_at = ? "
                    "WHERE agent_name = ? AND status = ?",
                    (ScheduleStatus.CANCELLED.value, now_iso, agent_name, ScheduleStatus.ARMED.value),
                )
                for row in schedule_rows:
                    self.insert_audit_log_uncommitted(
                        task_id=row["id"],
                        agent=agent_name,
                        action="schedule_cancelled",
                        payload={"reason": "agent_terminated"},
                    )

            # Skip pending work-hours wakes.
            wake_rows = self._conn.execute(
                "SELECT id FROM work_hours WHERE agent_name = ? AND status = ?",
                (agent_name, WorkHourStatus.PENDING.value),
            ).fetchall()
            if wake_rows:
                self._conn.execute(
                    "UPDATE work_hours SET status = ?, ended_at = ?, error = ? "
                    "WHERE agent_name = ? AND status = ?",
                    (WorkHourStatus.SKIPPED.value, now_iso, "agent_terminated", agent_name, WorkHourStatus.PENDING.value),
                )
                for row in wake_rows:
                    self.insert_audit_log_uncommitted(
                        task_id=row["id"],
                        agent=agent_name,
                        action="work_hour_skipped",
                        payload={"reason": "agent_terminated"},
                    )

            # Skip pending dreams.
            dream_rows = self._conn.execute(
                "SELECT id FROM dreams WHERE agent_name = ? AND status = ?",
                (agent_name, DreamStatus.PENDING.value),
            ).fetchall()
            if dream_rows:
                self._conn.execute(
                    "UPDATE dreams SET status = ?, ended_at = ?, error = ? "
                    "WHERE agent_name = ? AND status = ?",
                    (DreamStatus.SKIPPED.value, now_iso, "agent_terminated", agent_name, DreamStatus.PENDING.value),
                )
                for row in dream_rows:
                    self.insert_audit_log_uncommitted(
                        task_id=row["id"],
                        agent=agent_name,
                        action="dream_skipped",
                        payload={"reason": "agent_terminated"},
                    )

            # Decline not-yet-started thread invocations.
            self._conn.execute(
                "UPDATE thread_invocations "
                "SET status = ?, decline_reason = ?, consumed_at = ? "
                "WHERE agent_name = ? AND status = ? AND started_at IS NULL",
                (
                    ThreadInvocationStatus.DECLINED.value,
                    "agent_terminated",
                    now_iso,
                    agent_name,
                    ThreadInvocationStatus.PENDING.value,
                ),
            )

            # THR-200: a terminated agent must never resume a thread provider
            # session. Clear every participant row it owns and record the
            # invalidation audit inside this same transaction — a failure here
            # rolls back the whole cleanup, leaving the agent fully active
            # with prior session state intact.
            session_rows = self._reset_thread_sessions_for_agent_uncommitted(
                agent_name,
            )
            if session_rows and audit_scope_id is not None:
                self.insert_audit_log_uncommitted(
                    task_id=audit_scope_id,
                    agent=audit_agent,
                    action="thread_session_invalidated",
                    payload={
                        "reason": "termination",
                        "rows": session_rows,
                        "name": agent_name,
                    },
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def update_dream_kb_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        promoted_kb_slug: str | None = None,
    ) -> None:
        allowed = {"pending", "promoted", "rejected", "superseded"}
        if status not in allowed:
            raise ValueError(f"invalid status: {status!r}, expected one of {sorted(allowed)}")
        now = _now().isoformat()
        params: list[object] = [status, now]
        slug_assign = ""
        if promoted_kb_slug is not None:
            slug_assign = ", promoted_kb_slug = ?"
            params.append(promoted_kb_slug)
        params.append(candidate_id)
        cursor = self._conn.execute(
            f"UPDATE dream_kb_candidates SET status = ?, updated_at = ?{slug_assign} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            raise ValueError(f"dream_kb_candidate {candidate_id} not found")
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
