from __future__ import annotations

import json
from datetime import datetime, timedelta

from pydantic import ValidationError

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _synchronized,
)
from runtime.models import (
    AuthorityPolicyV2Attempt,
    AuthorityPolicyV2Candidate,
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
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
    AUTHORITY_POLICY_V2_FINAL_HOOK_AUDIT_ACTION,
    AUTHORITY_POLICY_V2_FINAL_TASK_AUDIT_ACTION,
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
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_envelope_id,
    authority_policy_v2_notification_id,
    authority_policy_v2_sha256,
    LocalCiEvidence,
    NextStep,
    TaskStatus,
)


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


class AuthorityV2ContinuationMixin:
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

        store = getattr(self, "_task_pause_store", None)
        if store is not None:
            store.held_uncommitted(root_task_id)
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
