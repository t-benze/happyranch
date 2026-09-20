"""Dark persistence boundary for immutable DB-backed authority policy.

S1 intentionally has no production caller.  The shipped authority hook keeps
using ``Database.claim_authority_candidate`` for legacy/static-policy attempts,
which truthfully creates no sidecar pin.  A later integration slice will call
this store only after resolving an active DB release and activation.
"""

from __future__ import annotations

import base64
import json

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityCandidate,
    AuthorityCandidatePolicyPin,
    AuthorityPolicyActivation,
    AuthorityPolicyLegacyActivationRequest,
    AuthorityPolicyLegacyControlReceipt,
    AuthorityPolicyLegacyReactivationRequest,
    AuthorityPolicyRelease,
    AuthorityPolicySelector,
    AuthorityPolicyV2Activation,
    AuthorityPolicyV2ActivationControlRequest,
    AuthorityPolicyV2Attempt,
    AuthorityPolicyV2Candidate,
    AuthorityPolicyV2ControlReceipt,
    AuthorityPolicyV2Evaluation,
    AuthorityPolicyV2PairedControlRequest,
    AuthorityPolicyV2Pin,
    AuthorityPolicyV2Release,
    AuthorityPolicyV2SessionBinding,
    AuthorityPolicyV2StageOutcome,
)


class AuthorityPolicyStore:
    """Typed, test-callable facade over the transaction-owning DB methods."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_release(self, release: AuthorityPolicyRelease) -> AuthorityPolicyRelease:
        return self._db.create_authority_policy_release(release)

    def create_release_with_audit(
        self,
        release: AuthorityPolicyRelease,
        *,
        request_id: str,
        request_digest: str,
    ) -> AuthorityPolicyRelease:
        return self._db.create_authority_policy_release_with_audit(
            release,
            request_id=request_id,
            request_digest=request_digest,
        )

    def activate(self, activation: AuthorityPolicyActivation) -> AuthorityPolicyActivation:
        receipt = AuthorityPolicyActivation.model_validate(
            activation.model_dump(mode="python", round_trip=True, warnings=False)
        )
        return self._db.activate_authority_policy(receipt)

    def activate_with_audit(
        self,
        *,
        team: str,
        release_id: str,
        expected_previous_epoch: int,
        action: str,
        request_id: str,
        request_digest: str,
    ) -> AuthorityPolicyActivation:
        return self._db.activate_authority_policy_with_audit(
            team=team,
            release_id=release_id,
            expected_previous_epoch=expected_previous_epoch,
            action=action,
            request_id=request_id,
            request_digest=request_digest,
        )

    def get_release(self, release_id: str) -> AuthorityPolicyRelease | None:
        return self._db.get_authority_policy_release(release_id)

    def next_release_version(self, team: str, policy_id: str) -> int:
        return self._db.get_next_authority_policy_release_version(team, policy_id)

    def get_activation(self, activation_id: str) -> AuthorityPolicyActivation | None:
        return self._db.get_authority_policy_activation(activation_id)

    def get_activation_for_release(
        self, team: str, release_id: str
    ) -> AuthorityPolicyActivation | None:
        return self._db.get_authority_policy_activation_for_release(team, release_id)

    def get_current_activation(self, team: str) -> AuthorityPolicyActivation | None:
        return self._db.get_current_authority_policy_activation(team)

    # -- THR-229 checkpoint B1: typed facade over the DB-owned v2 control
    # transactions/readers. No method here begins, commits or rolls back: the
    # Database owns the transaction boundary, and the facade never nests a
    # committing call inside another committing call.

    def ensure_authority_selector(self, team: str) -> AuthorityPolicySelector:
        return self._db.ensure_authority_selector(team)

    def get_authority_selector(self, team: str) -> AuthorityPolicySelector | None:
        return self._db.get_authority_selector(team)

    def get_authority_selector_by_id(
        self, team: str, selector_id: str
    ) -> AuthorityPolicySelector | None:
        return self._db.get_authority_policy_selector_by_id(team, selector_id)

    def list_selector_history(self, team: str) -> list[AuthorityPolicySelector]:
        return self._db.list_authority_policy_selector_history(team)

    def list_control_audit(self, team: str) -> list[dict]:
        return self._db.list_authority_policy_v2_control_audit(team)

    def get_v2_release(self, release_id: str) -> AuthorityPolicyV2Release | None:
        return self._db.get_authority_policy_v2_release(release_id)

    def get_v2_activation(self, activation_id: str) -> AuthorityPolicyV2Activation | None:
        return self._db.get_authority_policy_v2_activation(activation_id)

    def create_and_activate_v2(
        self, request: AuthorityPolicyV2PairedControlRequest
    ) -> AuthorityPolicyV2ControlReceipt:
        return self._db.create_and_activate_authority_policy_v2(request)

    def activate_v2(
        self, request: AuthorityPolicyV2ActivationControlRequest
    ) -> AuthorityPolicyV2ControlReceipt:
        return self._db.activate_authority_policy_v2(request)

    def activate_legacy_authority_policy(
        self, request: AuthorityPolicyLegacyActivationRequest
    ) -> AuthorityPolicyLegacyControlReceipt:
        return self._db.activate_authority_policy_legacy(request)

    def reactivate_legacy_authority_policy(
        self, request: AuthorityPolicyLegacyReactivationRequest
    ) -> AuthorityPolicyLegacyControlReceipt:
        return self._db.reactivate_authority_policy_legacy(request)

    # -- THR-229 checkpoint C1: immutable launch session bindings. The Database
    # owns the transaction/lock boundary; these forwarders never commit.

    def bind_v2_session(
        self, binding: AuthorityPolicyV2SessionBinding
    ) -> AuthorityPolicyV2SessionBinding:
        return self._db.bind_authority_policy_v2_session(binding)

    def get_v2_session_binding(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str
    ) -> AuthorityPolicyV2SessionBinding | None:
        return self._db.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        )

    # -- THR-229 checkpoint C2: result-keyed admitted attempt journal. These are
    # authenticated reads only; admission itself is owned by the Database
    # callback transaction and no continuation/evaluation entry point exists.

    def get_v2_attempt_for_result(
        self, result_id: int
    ) -> AuthorityPolicyV2Attempt | None:
        return self._db.get_authority_policy_v2_attempt_for_result(result_id)

    def get_v2_attempt(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2Attempt | None:
        return self._db.get_authority_policy_v2_attempt(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

    def list_v2_result_stage_audits(
        self, *, root_task_id: str, manager_agent: str
    ) -> list[dict]:
        return self._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_task_id, manager_agent=manager_agent,
        )

    # -- THR-229 checkpoint C3b: thin forwarders over the DB-owned v2
    # candidate/pin claim and the separate claim-audit stage.  The facade never
    # begins, commits or rolls back and never nests a committing call.

    def bind_v2_permission_surface_reader(self, reader) -> None:
        """Bind the server-side permission reader ``reader(agent) -> digest``.

        This is the narrowly scoped orchestration seam: the caller supplies a
        callable that reads the live permission surface inside the server
        process (e.g. the orchestrator's ``_permission_digest`` for the agent),
        never an allow/deny boolean or a precomputed digest.  Until it is bound,
        a v2 claim refuses fail-closed with ``evidence_drift``.
        """
        self._db.bind_authority_policy_v2_permission_surface_reader(reader)

    def claim_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.claim_authority_policy_v2_candidate(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )

    def audit_v2_candidate_claim(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.audit_authority_policy_v2_candidate_claim(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
        )

    # -- THR-229 checkpoint C3c: thin forwarders over the DB-owned evaluation,
    # evaluation-audit, consumption and consumed-audit stages plus the
    # authenticated V reads.  The facade never begins, commits or rolls back.

    def evaluate_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.evaluate_authority_policy_v2_candidate(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
        )

    def audit_v2_candidate_evaluation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.audit_authority_policy_v2_candidate_evaluation(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
        )

    def consume_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.consume_authority_policy_v2_candidate(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
        )

    def audit_v2_candidate_consumption(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
    ) -> AuthorityPolicyV2StageOutcome:
        return self._db.audit_authority_policy_v2_candidate_consumption(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
        )

    def get_v2_evaluation(self, candidate_id: str) -> AuthorityPolicyV2Evaluation | None:
        return self._db.get_authority_policy_v2_evaluation(candidate_id)

    def get_v2_evaluation_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Evaluation | None:
        return self._db.get_authority_policy_v2_evaluation_for_result(result_id)

    def get_v2_candidate_audit(self, candidate_id: str, event: str) -> dict | None:
        return self._db.get_authority_policy_v2_candidate_audit(candidate_id, event)

    def get_v2_candidate(self, candidate_id: str) -> AuthorityPolicyV2Candidate | None:
        return self._db.get_authority_policy_v2_candidate(candidate_id)

    def get_v2_candidate_for_result(
        self, result_id: int
    ) -> AuthorityPolicyV2Candidate | None:
        return self._db.get_authority_policy_v2_candidate_for_result(result_id)

    def get_v2_pin(self, candidate_id: str) -> AuthorityPolicyV2Pin | None:
        return self._db.get_authority_policy_v2_pin(candidate_id)

    def list_v2_candidate_audits(self, candidate_id: str) -> list[dict]:
        return self._db.list_authority_policy_v2_candidate_audits(candidate_id)

    def get_v2_candidate_claim_audit(self, candidate_id: str) -> dict | None:
        return self._db.get_authority_policy_v2_candidate_audit(
            candidate_id, "claimed",
        )

    def bind_legacy_session(
        self,
        *,
        task_id: str,
        agent_name: str,
        session_id: str,
        legacy_payload: dict,
        selector_payload: dict | None,
    ) -> None:
        return self._db.bind_authority_policy_legacy_session(
            task_id=task_id, agent_name=agent_name, session_id=session_id,
            legacy_payload=legacy_payload, selector_payload=selector_payload,
        )

    @staticmethod
    def _encode_cursor(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str, *, stream: str) -> dict:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload = json.loads(raw)
        except Exception as exc:
            raise ValueError("invalid pagination cursor") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("stream") != stream:
            raise ValueError("invalid pagination cursor")
        return payload

    @staticmethod
    def _cursor_int(payload: dict, key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid pagination cursor")
        return value

    @staticmethod
    def _cursor_str(payload: dict, key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("invalid pagination cursor")
        return value

    def list_history(self, team: str, *, cursor: str | None, limit: int) -> tuple[list[dict], str | None]:
        """Return an immutable-snapshot, deterministic newest-first page."""
        if cursor is None:
            snapshot_version, snapshot_epoch = self._db.get_authority_policy_history_snapshot(team)
            after = None
        else:
            token = self._decode_cursor(cursor, stream="history")
            if token.get("team") != team:
                raise ValueError("invalid pagination cursor")
            try:
                snapshot_version = self._cursor_int(token, "sv")
                snapshot_epoch = self._cursor_int(token, "se")
                after = (
                    self._cursor_int(token, "av"), self._cursor_int(token, "ae"),
                    self._cursor_str(token, "ar"), token.get("aa"),
                )
                if not isinstance(after[3], str) or len(after[3]) > 256:
                    raise ValueError("invalid pagination cursor")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid pagination cursor") from exc
        rows = self._db.list_authority_policy_history(
            team, snapshot_version=snapshot_version, snapshot_epoch=snapshot_epoch,
            after_version=None if after is None else after[0],
            after_epoch=None if after is None else after[1],
            after_release_id=None if after is None else after[2],
            after_activation_id=None if after is None else after[3], limit=limit + 1,
        )
        page = rows[:limit]
        if len(rows) <= limit or not page:
            return page, None
        last = page[-1]
        return page, self._encode_cursor({
            "v": 1, "stream": "history", "team": team,
            "sv": snapshot_version, "se": snapshot_epoch,
            "av": last["version"], "ae": last["epoch"] or 0,
            "ar": last["release_id"], "aa": last["activation_id"] or "",
        })

    def list_v2_history(
        self, team: str, *, cursor: str | None, limit: int
    ) -> tuple[list[dict], str | None]:
        """Return a stable newest-first page of immutable v2 release receipts."""
        if cursor is None:
            snapshot_version, snapshot_epoch = (
                self._db.get_authority_policy_v2_history_snapshot(team)
            )
            after = None
        else:
            token = self._decode_cursor(cursor, stream="v2_history")
            if token.get("team") != team:
                raise ValueError("invalid pagination cursor")
            try:
                snapshot_version = self._cursor_int(token, "sv")
                snapshot_epoch = self._cursor_int(token, "se")
                after = (self._cursor_int(token, "av"), self._cursor_str(token, "ar"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid pagination cursor") from exc
        rows = self._db.list_authority_policy_v2_history(
            team, snapshot_version=snapshot_version, snapshot_epoch=snapshot_epoch,
            after_version=None if after is None else after[0],
            after_release_id=None if after is None else after[1], limit=limit + 1,
        )
        page = rows[:limit]
        if len(rows) <= limit or not page:
            return page, None
        last = page[-1]
        return page, self._encode_cursor({
            "v": 1, "stream": "v2_history", "team": team,
            "sv": snapshot_version, "se": snapshot_epoch,
            "av": last["version"], "ar": last["release_id"],
        })

    def list_outcomes(self, team: str, *, cursor: str | None, limit: int) -> tuple[list[dict], str | None]:
        """Return secret-free receipts from a stable initial snapshot."""
        if cursor is None:
            snapshot = self._db.get_authority_policy_outcomes_snapshot(team)
            if snapshot is None:
                return [], None
            after = None
        else:
            token = self._decode_cursor(cursor, stream="outcomes")
            if token.get("team") != team:
                raise ValueError("invalid pagination cursor")
            try:
                snapshot = (self._cursor_str(token, "sc"), self._cursor_str(token, "si"))
                after = (self._cursor_str(token, "ac"), self._cursor_str(token, "ai"))
            except (KeyError, TypeError) as exc:
                raise ValueError("invalid pagination cursor") from exc
        rows = self._db.list_authority_policy_outcomes(
            team, snapshot_created_at=snapshot[0], snapshot_id=snapshot[1],
            after_created_at=None if after is None else after[0],
            after_id=None if after is None else after[1], limit=limit + 1,
        )
        page = rows[:limit]
        if len(rows) <= limit or not page:
            return page, None
        last = page[-1]
        return page, self._encode_cursor({
            "v": 1, "stream": "outcomes", "team": team,
            "sc": snapshot[0], "si": snapshot[1],
            "ac": last["created_at"], "ai": last["id"],
        })

    def get_candidate_pin(self, candidate_id: str) -> AuthorityCandidatePolicyPin | None:
        return self._db.get_authority_candidate_policy_pin(candidate_id)

    def claim_candidate_with_pin(
        self,
        *,
        release_id: str,
        activation_id: str,
        activation_epoch: int,
        provider_id: str,
        executor_kind: str,
        **candidate_kwargs,
    ) -> tuple[AuthorityCandidate, AuthorityCandidatePolicyPin]:
        return self._db.claim_authority_candidate_with_policy_pin(
            release_id=release_id,
            activation_id=activation_id,
            activation_epoch=activation_epoch,
            provider_id=provider_id,
            executor_kind=executor_kind,
            **candidate_kwargs,
        )
