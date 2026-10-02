from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _synchronized,
)
from runtime.models import (
    AuthorityPolicyActivation,
    AuthorityPolicyLegacyActivationRequest,
    AuthorityPolicyLegacyControlReceipt,
    AuthorityPolicyLegacyReactivationRequest,
    AuthorityPolicyRelease,
    AuthorityPolicySelector,
    AuthorityPolicyV2Activation,
    AuthorityPolicyV2ActivationControlRequest,
    AuthorityPolicyV2ControlReceipt,
    AuthorityPolicyV2PairedControlRequest,
    AuthorityPolicyV2Release,
    AuthorityPolicyV2SessionBinding,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_initializer_selector_id,
    authority_policy_v2_initializer_selector_preimage,
    authority_policy_v2_selector_id,
    authority_policy_v2_sha256,
    validate_authority_digest,
)


# Existing and additive supplemental launch-binding audit actions (THR-229 C1).
# The first is unchanged historical legacy binding; the second is the additive
# selector supplement written in the SAME transaction for a selected-v1 launch.
_AUTHORITY_POLICY_SESSION_BINDING_ACTION = "authority_policy_session_binding"
_AUTHORITY_POLICY_SELECTOR_SESSION_BINDING_ACTION = "authority_policy_selector_session_binding"


class AuthorityPolicyMixin:
    # --- THR-181 Track A: authority candidate/evaluation/audit API ---
    #
    # Narrow, additive persistence for the pre-escalation authority-evaluation
    # foundation, consumed by the authority hook (runtime/orchestrator/
    # authority.py). No evaluator is invoked HERE and no policy is enforced
    # HERE — these methods only persist/read controlled records — but they are
    # the durable surface the hook claims, records, and consumes through.
    # Prose-bearing content is stored as digests; raw bearer/provider
    # credentials, task prose, and unredacted model exchanges are never
    # accepted or persisted.

    def _authority_policy_release_from_row(self, row) -> AuthorityPolicyRelease:
        try:
            release = AuthorityPolicyRelease.model_validate(dict(row))
        except ValueError as exc:
            raise ValueError("authority policy release has corrupt digest or semantics") from exc
        canonical_digest = hashlib.sha256(
            release.canonical_payload_json.encode("utf-8")
        ).hexdigest()
        if canonical_digest != release.policy_digest:
            raise ValueError(f"authority policy release {release.id!r} has a corrupt digest")
        return release

    def _authority_policy_activation_from_row(self, row) -> AuthorityPolicyActivation:
        try:
            activation = AuthorityPolicyActivation.model_validate(dict(row))
        except ValueError as exc:
            raise ValueError("authority policy activation has corrupt digest or semantics") from exc
        release = self.get_authority_policy_release(activation.release_id)
        if release is None or release.team != activation.team:
            raise ValueError(f"authority policy activation {activation.id!r} has corrupt linkage")
        self._validate_authority_activation_history(activation)
        return activation

    def _validate_authority_activation_history(
        self, receipt: AuthorityPolicyActivation
    ) -> None:
        """Replay the sealed team history through the requested receipt."""
        rows = self._conn.execute(
            "SELECT * FROM authority_policy_activations "
            "WHERE team=? AND epoch<=? ORDER BY epoch",
            (receipt.team, receipt.epoch),
        ).fetchall()
        if len(rows) != receipt.epoch or not rows:
            raise ValueError(f"authority policy activation {receipt.id!r} has corrupt history")
        prior = None
        activated_release_ids: set[str] = set()
        for expected_epoch, row in enumerate(rows, 1):
            try:
                item = AuthorityPolicyActivation.model_validate(dict(row))
            except ValueError as exc:
                raise ValueError("authority policy activation history is corrupt") from exc
            release = self.get_authority_policy_release(item.release_id)
            if release is None or release.team != item.team or item.epoch != expected_epoch:
                raise ValueError("authority policy activation history linkage is corrupt")
            if prior is None:
                valid = (
                    item.action == "bootstrap"
                    and item.previous_activation_id is None
                    and item.expected_previous_epoch in (None, 0)
                )
            else:
                valid = (
                    item.previous_activation_id == prior.id
                    and item.expected_previous_epoch == prior.epoch
                    and item.action != "bootstrap"
                )
                if valid and item.action == "activate":
                    valid = item.release_id not in activated_release_ids
                elif valid and item.action == "reactivate_rollback":
                    current = self.get_authority_policy_release(prior.release_id)
                    valid = (
                        item.release_id in activated_release_ids
                        and item.release_id != prior.release_id
                        and current is not None
                        and release.policy_id == current.policy_id
                        and release.version < current.version
                    )
            if not valid:
                raise ValueError("authority policy activation history semantics are corrupt")
            activated_release_ids.add(item.release_id)
            prior = item
        if prior is None or prior.id != receipt.id:
            raise ValueError(f"authority policy activation {receipt.id!r} has corrupt history")

    @_synchronized
    def create_authority_policy_release(self, release: AuthorityPolicyRelease) -> AuthorityPolicyRelease:
        """Append an immutable release, idempotent only for an exact record."""
        # Pydantic intentionally reuses an already-constructed model instance.
        # Rebuild from a deep primitive snapshot so post-validation mutation,
        # including a nested value smuggled into a typed field, cannot bypass
        # canonical semantic identity validation at the durable boundary.
        release = AuthorityPolicyRelease.model_validate(
            release.model_dump(mode="python", round_trip=True, warnings=False)
        )
        expected = hashlib.sha256(release.canonical_payload_json.encode("utf-8")).hexdigest()
        if expected != release.policy_digest:
            raise ValueError("policy_digest does not match canonical_payload_json")
        values = release.model_dump(mode="json")
        try:
            self._conn.execute(
                """INSERT INTO authority_policy_releases
                   (id,team,policy_id,version,title,normative_text,clauses_json,
                    continuation_phrase,canonical_payload_json,policy_digest,
                    based_on_release_id,actor_kind,created_at)
                   VALUES (:id,:team,:policy_id,:version,:title,:normative_text,
                           :clauses_json,:continuation_phrase,:canonical_payload_json,
                           :policy_digest,:based_on_release_id,:actor_kind,:created_at)""",
                values,
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT * FROM authority_policy_releases WHERE id=? OR policy_digest=?",
                (release.id, release.policy_digest),
            ).fetchone()
            if row is not None and dict(row) == values:
                return release
            raise
        return release

    @_synchronized
    def create_authority_policy_release_with_audit(
        self,
        release: AuthorityPolicyRelease,
        *,
        request_id: str,
        request_digest: str,
    ) -> AuthorityPolicyRelease:
        """Resolve replay, then append a release and closed audit atomically."""
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("request_id must be a non-empty string of at most 128 characters")
        validate_authority_digest(request_digest, "request_digest")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                "SELECT task_id,agent,payload FROM audit_log "
                "WHERE action=? ORDER BY id",
                ("authority_policy_release_created",),
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("authority policy release audit receipt is corrupt") from exc
                if not isinstance(payload, dict):
                    raise ValueError("authority policy release audit receipt is corrupt")
                release_id = payload.get("release_id")
                persisted = (
                    self.get_authority_policy_release(release_id)
                    if isinstance(release_id, str)
                    else None
                )
                if persisted is None:
                    raise ValueError("authority policy release audit linkage is corrupt")
                expected_payload = {
                    "action": "create_release",
                    "actor_kind": "shared_local_operator_credential",
                    "policy_digest": persisted.policy_digest,
                    "release_id": persisted.id,
                    "request_digest": payload.get("request_digest"),
                    "request_id": payload.get("request_id"),
                    "team": persisted.team,
                }
                if (
                    payload != expected_payload
                    or row["task_id"] != f"config:authority-policy:{persisted.team}"
                    or row["agent"] != "shared_local_operator_credential"
                ):
                    raise ValueError("authority policy release audit receipt is corrupt")
                if payload.get("request_id") != request_id:
                    continue
                if payload.get("request_digest") != request_digest:
                    raise sqlite3.IntegrityError(
                        "authority policy release idempotency mismatch"
                    )
                self._conn.commit()
                return persisted

            release = AuthorityPolicyRelease.model_validate(
                release.model_dump(mode="python", round_trip=True, warnings=False)
            )
            values = release.model_dump(mode="json")
            scope = f"config:authority-policy:{release.team}"
            current = self._conn.execute(
                "SELECT * FROM authority_policy_activations WHERE team=? "
                "ORDER BY epoch DESC LIMIT 1",
                (release.team,),
            ).fetchone()
            current_release = None
            if current is not None:
                current_activation = self._authority_policy_activation_from_row(current)
                current_release = self.get_authority_policy_release(
                    current_activation.release_id
                )
                if current_release is None:
                    raise ValueError("active authority policy release is missing")
            expected_base = None if current_release is None else current_release.id
            if release.based_on_release_id != expected_base:
                raise sqlite3.IntegrityError("authority policy release base conflict")
            self._conn.execute(
                """INSERT INTO authority_policy_releases
                   (id,team,policy_id,version,title,normative_text,clauses_json,
                    continuation_phrase,canonical_payload_json,policy_digest,
                    based_on_release_id,actor_kind,created_at)
                   VALUES (:id,:team,:policy_id,:version,:title,:normative_text,
                           :clauses_json,:continuation_phrase,:canonical_payload_json,
                           :policy_digest,:based_on_release_id,:actor_kind,:created_at)""",
                values,
            )
            self.insert_audit_log_uncommitted(
                task_id=scope,
                agent="shared_local_operator_credential",
                action="authority_policy_release_created",
                payload={
                    "action": "create_release",
                    "actor_kind": "shared_local_operator_credential",
                    "policy_digest": release.policy_digest,
                    "release_id": release.id,
                    "request_digest": request_digest,
                    "request_id": request_id,
                    "team": release.team,
                },
            )
            self._conn.commit()
            return release
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def get_authority_policy_release(self, release_id: str) -> AuthorityPolicyRelease | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_releases WHERE id=?", (release_id,)
        ).fetchone()
        return None if row is None else self._authority_policy_release_from_row(row)

    @_synchronized
    def get_next_authority_policy_release_version(self, team: str, policy_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) AS version FROM authority_policy_releases "
            "WHERE team=? AND policy_id=?",
            (team, policy_id),
        ).fetchone()
        return 1 if row is None or row["version"] is None else int(row["version"]) + 1

    @_synchronized
    def list_authority_policy_history(
        self,
        team: str,
        *,
        snapshot_version: int,
        snapshot_epoch: int,
        after_version: int | None,
        after_epoch: int | None,
        after_release_id: str | None,
        after_activation_id: str | None,
        limit: int,
    ) -> list[dict]:
        """Read immutable release/activation receipts without policy prose."""
        rows = self._conn.execute(
            """SELECT r.id AS release_id,r.policy_id,r.version,r.policy_digest,
                      r.created_at AS release_created_at,r.actor_kind,
                      a.id AS activation_id,a.epoch,a.action,
                      a.created_at AS activation_created_at,a.activation_digest
               FROM authority_policy_releases r
               LEFT JOIN authority_policy_activations a
                 ON a.release_id=r.id AND a.epoch<=?
               WHERE r.team=? AND r.version<=?
                 AND (? IS NULL OR (r.version,COALESCE(a.epoch,0),r.id,
                                      COALESCE(a.id,'')) < (?,?,?,?))
               ORDER BY r.version DESC,COALESCE(a.epoch,0) DESC,r.id DESC,
                        COALESCE(a.id,'') DESC LIMIT ?""",
            (
                snapshot_epoch, team, snapshot_version, after_version,
                after_version, after_epoch, after_release_id, after_activation_id,
                limit,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_history_snapshot(self, team: str) -> tuple[int, int]:
        row = self._conn.execute(
            """SELECT COALESCE(MAX(r.version),0) AS version,
                      COALESCE((SELECT MAX(epoch) FROM authority_policy_activations
                                WHERE team=?),0) AS epoch
               FROM authority_policy_releases r WHERE r.team=?""",
            (team, team),
        ).fetchone()
        return int(row["version"]), int(row["epoch"])

    @_synchronized
    def list_authority_policy_outcomes(
        self,
        team: str,
        *,
        snapshot_created_at: str,
        snapshot_id: str,
        after_created_at: str | None,
        after_id: str | None,
        limit: int,
    ) -> list[dict]:
        """Read the joined immutable evaluation identity; projection authenticates completeness."""
        rows = self._conn.execute(
            """SELECT c.*,p.release_id,p.activation_id,p.activation_epoch,
                      p.provider_id,p.executor_kind,e.id AS evaluation_id,
                      e.disposition AS evaluation_disposition,e.disposition_code,
                      e.response_digest,e.created_at AS evaluation_created_at,
                      env.id AS envelope_id,env.state AS envelope_state,
                      env.consumed_at AS envelope_consumed_at
               FROM authority_candidates c
               LEFT JOIN authority_candidate_policy_pins p ON p.candidate_id=c.id
               LEFT JOIN authority_evaluations e ON e.candidate_id=c.id
               LEFT JOIN authority_continue_envelopes env ON env.candidate_id=c.id
               WHERE c.team=? AND (c.created_at,c.id) <= (?,?)
                 AND (? IS NULL OR (c.created_at,c.id) < (?,?))
               ORDER BY c.created_at DESC,c.id DESC LIMIT ?""",
            (team, snapshot_created_at, snapshot_id, after_created_at,
             after_created_at, after_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_outcomes_snapshot(self, team: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT created_at,id FROM authority_candidates WHERE team=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            (team,),
        ).fetchone()
        return None if row is None else (str(row["created_at"]), str(row["id"]))

    @_synchronized
    def activate_authority_policy(
        self, activation: AuthorityPolicyActivation
    ) -> AuthorityPolicyActivation:
        """Append the next team epoch under BEGIN IMMEDIATE CAS semantics."""
        # Rebuild from a deep primitive snapshot without generating a seal.
        # Missing, malformed, or mismatched receipts fail before BEGIN, even
        # when a caller bypassed Pydantic's assignment guard.
        activation = AuthorityPolicyActivation.model_validate(
            activation.model_dump(mode="python", round_trip=True, warnings=False)
        )
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            duplicate = self._conn.execute(
                "SELECT * FROM authority_policy_activations WHERE request_id=?",
                (activation.request_id,),
            ).fetchone()
            if duplicate is not None:
                persisted = self._authority_policy_activation_from_row(duplicate)
                if persisted.model_dump(mode="json") != activation.model_dump(mode="json"):
                    raise sqlite3.IntegrityError("authority activation idempotency mismatch")
                self._conn.commit()
                return persisted
            previous = self._conn.execute(
                "SELECT * FROM authority_policy_activations WHERE team=? ORDER BY epoch DESC LIMIT 1",
                (activation.team,),
            ).fetchone()
            previous_epoch = None if previous is None else previous["epoch"]
            expected_epoch = activation.expected_previous_epoch
            if previous is None:
                if expected_epoch not in (None, 0) or activation.epoch != 1 or activation.previous_activation_id is not None:
                    raise sqlite3.IntegrityError("authority activation bootstrap CAS conflict")
                if activation.action != "bootstrap":
                    raise sqlite3.IntegrityError("first authority activation must be bootstrap")
            elif (
                expected_epoch != previous_epoch
                or activation.epoch != previous_epoch + 1
                or activation.previous_activation_id != previous["id"]
            ):
                raise sqlite3.IntegrityError("authority activation CAS conflict")
            elif activation.action == "bootstrap":
                raise sqlite3.IntegrityError("authority bootstrap is forbidden after history exists")
            else:
                previously_activated = self._conn.execute(
                    "SELECT 1 FROM authority_policy_activations WHERE team=? AND release_id=? LIMIT 1",
                    (activation.team, activation.release_id),
                ).fetchone() is not None
                if activation.action == "activate" and previously_activated:
                    raise sqlite3.IntegrityError("activate target was previously activated")
                if activation.action == "reactivate_rollback" and (
                    not previously_activated or activation.release_id == previous["release_id"]
                ):
                    raise sqlite3.IntegrityError("reactivation target is not a non-current prior release")
                if activation.action == "reactivate_rollback":
                    older = self._conn.execute(
                        """SELECT 1
                           FROM authority_policy_releases target
                           JOIN authority_policy_releases current ON current.id=?
                           WHERE target.id=?
                             AND target.policy_id=current.policy_id
                             AND target.version<current.version""",
                        (previous["release_id"], activation.release_id),
                    ).fetchone()
                    if older is None:
                        raise sqlite3.IntegrityError(
                            "reactivation rollback target is not an older policy version"
                        )
            self._conn.execute(
                """INSERT INTO authority_policy_activations
                   (id,team,epoch,release_id,previous_activation_id,
                    expected_previous_epoch,action,actor_kind,request_id,
                    request_digest,created_at,activation_digest)
                   VALUES (:id,:team,:epoch,:release_id,:previous_activation_id,
                           :expected_previous_epoch,:action,:actor_kind,:request_id,
                           :request_digest,:created_at,:activation_digest)""",
                activation.model_dump(mode="json"),
            )
            self._conn.commit()
            return activation
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def activate_authority_policy_with_audit(
        self,
        *,
        team: str,
        release_id: str,
        expected_previous_epoch: int,
        action: str,
        request_id: str,
        request_digest: str,
    ) -> AuthorityPolicyActivation:
        """Server-construct and atomically append a CAS activation plus audit."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            duplicate = self._conn.execute(
                "SELECT * FROM authority_policy_activations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if duplicate is not None:
                if duplicate["request_digest"] != request_digest:
                    raise sqlite3.IntegrityError(
                        "authority activation idempotency mismatch"
                    )
                persisted = self._authority_policy_activation_from_row(duplicate)
                self._conn.commit()
                return persisted
            release = self.get_authority_policy_release(release_id)
            if release is None or release.team != team:
                raise LookupError("authority activation release unavailable")
            activation = self._append_authority_policy_activation_uncommitted(
                team=team, release=release,
                expected_previous_epoch=expected_previous_epoch,
                action=action, request_id=request_id,
                request_digest=request_digest,
            )
            self._insert_authority_policy_activation_audit_uncommitted(
                team=team, release=release, activation=activation,
                request_digest=request_digest,
            )
            self._conn.commit()
            return activation
        except Exception:
            self._conn.rollback()
            raise

    def _append_authority_policy_activation_uncommitted(
        self,
        *,
        team: str,
        release: AuthorityPolicyRelease,
        expected_previous_epoch: int,
        action: str,
        request_id: str,
        request_digest: str,
    ) -> AuthorityPolicyActivation:
        """Validate the legacy CAS/action rules and INSERT one activation.

        The transaction owner is the caller. This seam never begins, commits or
        rolls back, so a selector-aware legacy write can keep the legacy
        activation and the selector/history/control audit in ONE
        ``BEGIN IMMEDIATE`` without changing the legacy semantics.
        """
        previous = self._conn.execute(
            "SELECT * FROM authority_policy_activations WHERE team=? "
            "ORDER BY epoch DESC LIMIT 1",
            (team,),
        ).fetchone()
        if previous is None:
            if expected_previous_epoch != 0 or action != "bootstrap":
                raise sqlite3.IntegrityError("authority activation bootstrap CAS conflict")
            epoch = 1
        elif previous["epoch"] != expected_previous_epoch or action == "bootstrap":
            raise sqlite3.IntegrityError("authority activation CAS conflict")
        else:
            epoch = previous["epoch"] + 1
        created_at = _now()
        activation_id = "APA-" + hashlib.sha256(
            f"{team}:{request_id}:{request_digest}".encode("utf-8")
        ).hexdigest()
        activation = AuthorityPolicyActivation.create(
            id=activation_id,
            team=team,
            epoch=epoch,
            release_id=release.id,
            previous_activation_id=None if previous is None else previous["id"],
            expected_previous_epoch=expected_previous_epoch,
            action=action,
            actor_kind="shared_local_operator_credential",
            request_id=request_id,
            request_digest=request_digest,
            created_at=created_at,
        )
        previously_activated = self._conn.execute(
            "SELECT 1 FROM authority_policy_activations "
            "WHERE team=? AND release_id=? LIMIT 1",
            (team, release.id),
        ).fetchone() is not None
        if action == "activate" and previously_activated:
            raise sqlite3.IntegrityError("activate target was previously activated")
        if action == "reactivate_rollback":
            older = self._conn.execute(
                """SELECT 1 FROM authority_policy_releases target
                   JOIN authority_policy_releases current ON current.id=?
                   WHERE target.id=? AND target.policy_id=current.policy_id
                     AND target.version<current.version""",
                (previous["release_id"], release.id),
            ).fetchone()
            if (
                not previously_activated
                or release.id == previous["release_id"]
                or older is None
            ):
                raise sqlite3.IntegrityError(
                    "reactivation target is not an older prior release"
                )
        self._conn.execute(
            """INSERT INTO authority_policy_activations
               (id,team,epoch,release_id,previous_activation_id,
                expected_previous_epoch,action,actor_kind,request_id,
                request_digest,created_at,activation_digest)
               VALUES (:id,:team,:epoch,:release_id,:previous_activation_id,
                       :expected_previous_epoch,:action,:actor_kind,:request_id,
                       :request_digest,:created_at,:activation_digest)""",
            activation.model_dump(mode="json"),
        )
        return activation

    def _insert_authority_policy_activation_audit_uncommitted(
        self,
        *,
        team: str,
        release: AuthorityPolicyRelease,
        activation: AuthorityPolicyActivation,
        request_digest: str,
    ) -> None:
        audit_action = (
            "authority_policy_activated"
            if activation.action in ("activate", "bootstrap")
            else "authority_policy_reactivated"
        )
        self.insert_audit_log_uncommitted(
            task_id=f"config:authority-policy:{team}",
            agent="shared_local_operator_credential",
            action=audit_action,
            payload={
                "activation_id": activation.id,
                "action": activation.action,
                "actor_kind": "shared_local_operator_credential",
                "epoch": activation.epoch,
                "policy_digest": release.policy_digest,
                "release_id": release.id,
                "request_digest": request_digest,
                "team": team,
            },
        )

    @_synchronized
    def get_authority_policy_activation(self, activation_id: str) -> AuthorityPolicyActivation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_activations WHERE id=?", (activation_id,)
        ).fetchone()
        return None if row is None else self._authority_policy_activation_from_row(row)

    @_synchronized
    def get_current_authority_policy_activation(
        self, team: str
    ) -> AuthorityPolicyActivation | None:
        """Return the latest authenticated activation for ``team``, if any.

        The point-read decoder is deliberately the sole validation boundary:
        it authenticates the linked release and replays the complete sealed
        activation history through the selected receipt.
        """
        row = self._conn.execute(
            "SELECT * FROM authority_policy_activations "
            "WHERE team=? ORDER BY epoch DESC LIMIT 1",
            (team,),
        ).fetchone()
        return None if row is None else self._authority_policy_activation_from_row(row)

    # --- THR-229 checkpoint B1: v2 control-plane persistence ---
    #
    # Private, callable storage for the accepted dual-text v2 control contract.
    # This unit wires NO startup/route/launch/queue/reaper/UI reader or writer
    # and does NOT converge legacy writers onto the selector (B2). The Database
    # owns every lock/BEGIN IMMEDIATE/commit/rollback; the store facade only
    # forwards. Legacy releases/activations/history are read through the
    # authenticated existing readers and never rewritten.

    @staticmethod
    def _validate_authority_selector_team(team: str) -> str:
        if not isinstance(team, str) or not team or team.isspace() or len(team) > 128:
            raise ValueError("authority selector team must be a bounded nonblank string")
        return team

    def _authority_policy_v2_release_from_row(self, row) -> AuthorityPolicyV2Release:
        try:
            release = AuthorityPolicyV2Release.model_validate_json(row["canonical_payload_json"])
        except Exception as exc:
            raise ValueError("authority v2 release has a corrupt canonical payload") from exc
        if release.policy_digest != row["policy_digest"] or release.release_id != row["id"]:
            raise ValueError("authority v2 release digest/id mismatch")
        if (
            release.team != row["team"]
            or release.policy_id != row["policy_id"]
            or release.version != row["version"]
            or release.title != row["title"]
            or release.what_to_escalate != row["what_to_escalate"]
            or release.what_not_to_escalate != row["what_not_to_escalate"]
            or release.contract_digest != row["contract_digest"]
        ):
            raise ValueError("authority v2 release column/preimage mismatch")
        return release

    def get_authority_policy_v2_release(self, release_id: str) -> AuthorityPolicyV2Release | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_releases WHERE id=?", (release_id,)
        ).fetchone()
        return None if row is None else self._authority_policy_v2_release_from_row(row)

    def _authority_policy_v2_activation_from_row(self, row) -> AuthorityPolicyV2Activation:
        try:
            activation = AuthorityPolicyV2Activation.model_validate(dict(row))
        except Exception as exc:
            raise ValueError("authority v2 activation has corrupt fields") from exc
        release = self.get_authority_policy_v2_release(activation.release_id)
        if release is None or release.team != activation.team:
            raise ValueError("authority v2 activation release linkage is corrupt")
        if release.policy_digest != activation.release_digest:
            raise ValueError("authority v2 activation release digest is corrupt")
        return activation

    @_synchronized
    def get_authority_policy_v2_activation(
        self, activation_id: str
    ) -> AuthorityPolicyV2Activation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_activations WHERE id=?", (activation_id,)
        ).fetchone()
        return None if row is None else self._authority_policy_v2_activation_from_row(row)

    # -- THR-229 checkpoint C1: immutable launch session bindings --
    #
    # The v2 binding table stores the authenticated launch tuple for a session.
    # The Database owns every lock/BEGIN IMMEDIATE/commit boundary; the caller
    # never nests a committing facade inside these writers.

    def _authority_begin(self) -> bool:
        """Begin a write transaction; return True when nested under an outer one."""
        if self._conn.in_transaction:
            self._conn.execute("SAVEPOINT authority_write")
            return True
        self._conn.execute("BEGIN IMMEDIATE")
        return False

    def _authority_commit(self, nested: bool) -> None:
        if nested:
            self._conn.execute("RELEASE authority_write")
        else:
            self._conn.commit()

    def _authority_rollback(self, nested: bool) -> None:
        if nested:
            self._conn.execute("ROLLBACK TO authority_write")
            self._conn.execute("RELEASE authority_write")
        else:
            self._conn.rollback()

    @contextmanager
    def _authority_write_transaction(self):
        """Own a write transaction, degrading to a savepoint if one is open.

        The launch path may be entered with an already-open implicit
        transaction (for example a caller that used the raw ``execute``
        passthrough). In that case a nested ``BEGIN IMMEDIATE`` would fail, so
        the write joins the outer transaction under a savepoint instead; a
        failure rolls back only this savepoint and never the caller's work.
        """
        nested = self._authority_begin()
        try:
            yield
        except Exception:
            self._authority_rollback(nested)
            raise
        else:
            self._authority_commit(nested)

    def _authority_policy_v2_session_binding_from_row(
        self, row
    ) -> AuthorityPolicyV2SessionBinding:
        try:
            binding = AuthorityPolicyV2SessionBinding.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError(
                "authority v2 session binding has a corrupt canonical payload"
            ) from exc
        if binding.binding_id != row["binding_id"]:
            raise ValueError("authority v2 session binding identity mismatch")
        for column, value in binding.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 session binding column/preimage mismatch")
        return binding

    @_synchronized
    def get_authority_policy_v2_session_binding(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
    ) -> AuthorityPolicyV2SessionBinding | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_session_bindings "
            "WHERE root_task_id=? AND manager_agent=? AND manager_session_id=?",
            (root_task_id, manager_agent, manager_session_id),
        ).fetchone()
        return None if row is None else self._authority_policy_v2_session_binding_from_row(row)

    def _authenticate_v2_session_binding_uncommitted(
        self, binding: AuthorityPolicyV2SessionBinding
    ) -> None:
        """Authenticate a binding against its sealed release/activation/selector."""
        activation = self.get_authority_policy_v2_activation(binding.activation_id)
        if activation is None or activation.team != binding.team:
            raise ValueError("v2 session binding activation is not durable")
        release = self.get_authority_policy_v2_release(binding.release_id)
        if release is None or release.team != binding.team:
            raise ValueError("v2 session binding release is not durable")
        if (
            activation.release_id != binding.release_id
            or activation.release_digest != binding.policy_digest
            or activation.selector_epoch != binding.activation_epoch
            or release.policy_digest != binding.policy_digest
            or release.version != binding.policy_version
            or release.contract_digest != binding.contract_digest
        ):
            raise ValueError(
                "v2 session binding does not match its sealed release/activation"
            )
        selector = self.get_authority_policy_selector_by_id(
            binding.team, binding.selector_id
        )
        if (
            selector is None or selector.family != "v2"
            or selector.selector_epoch != binding.activation_epoch
            or selector.v2_activation_id != binding.activation_id
        ):
            raise ValueError("v2 session binding selector is not authenticated")

    @_synchronized
    def bind_authority_policy_v2_session(
        self, binding: AuthorityPolicyV2SessionBinding | dict
    ) -> AuthorityPolicyV2SessionBinding:
        """Persist one immutable v2 launch binding in its own transaction.

        Exact-repeat idempotency returns the prior immutable row; a changed
        tuple for the same session, or a conflicting legacy binding, refuses.
        Authentication happens against the sealed release, activation and the
        pinned selector's own predecessor chain.
        """
        if not isinstance(binding, AuthorityPolicyV2SessionBinding):
            binding = AuthorityPolicyV2SessionBinding.model_validate(binding)
        with self._authority_write_transaction():
            self._authenticate_v2_session_binding_uncommitted(binding)
            existing = self._conn.execute(
                "SELECT * FROM authority_policy_v2_session_bindings "
                "WHERE root_task_id=? AND manager_agent=? AND manager_session_id=?",
                (binding.root_task_id, binding.manager_agent, binding.manager_session_id),
            ).fetchone()
            if existing is not None:
                prior = self._authority_policy_v2_session_binding_from_row(existing)
                if prior.binding_id != binding.binding_id:
                    raise ValueError(
                        "v2 session binding conflicts with an existing binding"
                    )
                return prior
            legacy_rows = self._legacy_session_binding_rows_uncommitted(
                binding.root_task_id, binding.manager_agent, binding.manager_session_id
            )
            if legacy_rows:
                raise ValueError(
                    "v2 session binding conflicts with a legacy session binding"
                )
            snapshot = binding.model_dump(mode="json")
            self._conn.execute(
                """INSERT INTO authority_policy_v2_session_bindings
                   (binding_id,team,root_task_id,manager_agent,manager_session_id,
                    selector_id,activation_epoch,release_id,policy_version,policy_digest,
                    activation_id,contract_id,contract_version,contract_digest,
                    provider_id,executor_kind,model_id,canonical_payload_json,created_at)
                   VALUES (:binding_id,:team,:root_task_id,:manager_agent,
                           :manager_session_id,:selector_id,:activation_epoch,:release_id,
                           :policy_version,:policy_digest,:activation_id,:contract_id,
                           :contract_version,:contract_digest,:provider_id,:executor_kind,
                           :model_id,:canonical_payload_json,:created_at)""",
                {
                    **snapshot,
                    "binding_id": binding.binding_id,
                    "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                        snapshot
                    ).decode("utf-8"),
                },
            )
            return binding

    def _legacy_session_binding_rows_uncommitted(
        self, task_id: str, agent_name: str, session_id: str
    ) -> list[dict]:
        return [
            row for row in self.get_audit_logs(task_id)
            if row["action"] == _AUTHORITY_POLICY_SESSION_BINDING_ACTION
            and row.get("agent") == agent_name
            and (row.get("payload") or {}).get("session_id") == session_id
        ]

    def _selector_session_binding_rows_uncommitted(
        self, task_id: str, agent_name: str, session_id: str
    ) -> list[dict]:
        return [
            row for row in self.get_audit_logs(task_id)
            if row["action"] == _AUTHORITY_POLICY_SELECTOR_SESSION_BINDING_ACTION
            and row.get("agent") == agent_name
            and (row.get("payload") or {}).get("session_id") == session_id
        ]

    @_synchronized
    def bind_authority_policy_legacy_session(
        self,
        *,
        task_id: str,
        agent_name: str,
        session_id: str,
        legacy_payload: dict,
        selector_payload: dict | None,
    ) -> None:
        """Write the unchanged legacy binding and its supplemental selector audit.

        Both audit rows commit in ONE transaction (R2); a changed payload or a
        conflicting v2 binding for the session refuses without partial writes.
        """
        with self._authority_write_transaction():
            v2 = self.get_authority_policy_v2_session_binding(
                root_task_id=task_id, manager_agent=agent_name,
                manager_session_id=session_id,
            )
            if v2 is not None:
                raise ValueError(
                    "legacy session binding conflicts with a v2 session binding"
                )
            legacy_rows = self._legacy_session_binding_rows_uncommitted(
                task_id, agent_name, session_id
            )
            selector_rows = self._selector_session_binding_rows_uncommitted(
                task_id, agent_name, session_id
            )
            if legacy_rows or selector_rows:
                selector_ok = (
                    (selector_payload is None and not selector_rows)
                    or (
                        selector_payload is not None
                        and len(selector_rows) == 1
                        and selector_rows[0].get("payload") == selector_payload
                    )
                )
                if (
                    len(legacy_rows) != 1
                    or legacy_rows[0].get("payload") != legacy_payload
                    or not selector_ok
                ):
                    raise ValueError("session policy binding is ambiguous")
                return
            self.insert_audit_log_uncommitted(
                task_id, agent_name,
                _AUTHORITY_POLICY_SESSION_BINDING_ACTION, legacy_payload,
            )
            if selector_payload is not None:
                self.insert_audit_log_uncommitted(
                    task_id, agent_name,
                    _AUTHORITY_POLICY_SELECTOR_SESSION_BINDING_ACTION, selector_payload,
                )

    def _authority_policy_selector_from_row(self, row) -> AuthorityPolicySelector:
        try:
            selector = AuthorityPolicySelector.model_validate(dict(row))
        except Exception as exc:
            raise ValueError("authority selector has corrupt fields") from exc
        if selector.family == "legacy_v1":
            activation = self.get_authority_policy_activation(selector.legacy_activation_id)
            if activation is None or activation.team != selector.team:
                raise ValueError("authority selector legacy activation linkage is corrupt")
        elif selector.family == "v2":
            activation = self.get_authority_policy_v2_activation(selector.v2_activation_id)
            if activation is None or activation.team != selector.team:
                raise ValueError("authority selector v2 activation linkage is corrupt")
            if activation.selector_epoch != selector.selector_epoch:
                raise ValueError("authority selector epoch/activation mismatch")
        return selector

    def _load_authority_selector_history_chain(
        self, team: str, *, up_to_selector_id: str | None = None
    ) -> list[AuthorityPolicySelector]:
        """Load and authenticate the immutable selector history chain.

        With ``up_to_selector_id`` the read is bounded: only the initializer
        through the pinned selector is validated, and later legitimate
        activations can neither replace nor invalidate that older prefix. The
        current-selector and control-writer readers still require the complete
        chain and tip coherence through the unbounded callers.
        """
        rows = self._conn.execute(
            "SELECT * FROM authority_policy_active_selector_history "
            "WHERE team=? ORDER BY selector_epoch",
            (team,),
        ).fetchall()
        if not rows:
            return []
        if up_to_selector_id is not None:
            prefix: list = []
            for row in rows:
                prefix.append(row)
                if row["selector_id"] == up_to_selector_id:
                    break
            else:
                # The pin is not in this team's authenticated history.
                return []
            rows = prefix
        selectors = [self._authority_policy_selector_from_row(row) for row in rows]
        first = selectors[0]
        if first.family == "empty":
            start = 0
        elif first.family == "legacy_v1":
            start = 1
        else:
            raise ValueError("authority selector history does not start at a valid initializer")
        for expected_epoch, item in enumerate(selectors, start):
            if item.selector_epoch != expected_epoch:
                raise ValueError("authority selector history epochs are not contiguous")
            if expected_epoch == start:
                if item.previous_selector_id is not None:
                    raise ValueError("authority selector initializer has a predecessor")
            else:
                previous = selectors[expected_epoch - start - 1]
                if item.previous_selector_id != previous.selector_id:
                    raise ValueError("authority selector history is not a single chain")
                if item.family == "empty":
                    raise ValueError("authority empty selector must be the initializer")
        # Authenticated control audit/preimage/receipt links are part of the
        # selector's authority: a missing/mutated/duplicated initializer or
        # selection audit, or a receipt without its durable activation/release,
        # refuses without reconstructing anything.
        self._authenticate_authority_selector_control_audit(
            team, selectors, bounded=up_to_selector_id is not None,
        )
        return selectors

    @_synchronized
    def get_authority_selector(self, team: str) -> AuthorityPolicySelector | None:
        team = self._validate_authority_selector_team(team)
        chain = self._load_authority_selector_history_chain(team)
        row = self._conn.execute(
            "SELECT * FROM authority_policy_active_selector WHERE team=?", (team,)
        ).fetchone()
        if row is None:
            if chain:
                raise ValueError("authority selector history exists without a live selector")
            return None
        selector = self._authority_policy_selector_from_row(row)
        if not chain or chain[-1] != selector:
            raise ValueError("authority live selector does not match its authenticated history tip")
        return selector

    @_synchronized
    def get_authority_policy_selector_by_id(
        self, team: str, selector_id: str
    ) -> AuthorityPolicySelector | None:
        """Authenticate one pinned selector through its own predecessor chain.

        Bounded historical authentication: rows AFTER the pin are never
        consulted, so a later legitimate v1/v2 activation cannot replace or
        invalidate an older authentic launch binding.
        """
        team = self._validate_authority_selector_team(team)
        for selector in self._load_authority_selector_history_chain(
            team, up_to_selector_id=selector_id
        ):
            if selector.selector_id == selector_id:
                return selector
        return None

    @_synchronized
    def list_authority_policy_selector_history(
        self, team: str
    ) -> list[AuthorityPolicySelector]:
        team = self._validate_authority_selector_team(team)
        return self._load_authority_selector_history_chain(team)

    @_synchronized
    def list_authority_policy_v2_control_audit(self, team: str) -> list[dict]:
        team = self._validate_authority_selector_team(team)
        rows = self._conn.execute(
            "SELECT * FROM authority_policy_v2_control_audit WHERE team=? ORDER BY id",
            (team,),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_v2_history_snapshot(self, team: str) -> tuple[int, int]:
        """Bounded, stable snapshot identity for the v2 release history stream."""
        team = self._validate_authority_selector_team(team)
        row = self._conn.execute(
            """SELECT COALESCE(MAX(r.version),0) AS version,
                      COALESCE((SELECT MAX(selector_epoch)
                                FROM authority_policy_v2_activations
                                WHERE team=?),0) AS epoch
               FROM authority_policy_v2_releases r WHERE r.team=?""",
            (team, team),
        ).fetchone()
        return int(row["version"]), int(row["epoch"])

    @_synchronized
    def list_authority_policy_v2_history(
        self,
        team: str,
        *,
        snapshot_version: int,
        snapshot_epoch: int,
        after_version: int | None,
        after_release_id: str | None,
        limit: int,
    ) -> list[dict]:
        """Read immutable v2 release receipts with their last in-snapshot selection.

        Only B2b read projection: no prose, no control audit, deterministic
        newest-first cursor on ``(version, release_id)``. A later selection of an
        older release does not move a cursor opened against the earlier snapshot.
        """
        team = self._validate_authority_selector_team(team)
        rows = self._conn.execute(
            """SELECT r.id AS release_id,r.policy_id,r.version,r.title,
                      r.what_to_escalate,r.what_not_to_escalate,r.contract_digest,
                      r.policy_digest,r.created_at AS release_created_at,
                      a.id AS activation_id,a.selector_epoch,a.action,
                      a.activation_digest,a.created_at AS activation_created_at
               FROM authority_policy_v2_releases r
               LEFT JOIN authority_policy_v2_activations a
                 ON a.release_id=r.id AND a.selector_epoch=(
                     SELECT MAX(a2.selector_epoch)
                     FROM authority_policy_v2_activations a2
                     WHERE a2.release_id=r.id AND a2.selector_epoch<=?)
               WHERE r.team=? AND r.version<=?
                 AND (? IS NULL OR (r.version,r.id) < (?,?))
               ORDER BY r.version DESC,r.id DESC LIMIT ?""",
            (
                snapshot_epoch, team, snapshot_version, after_version,
                after_version, after_release_id, limit,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_activation_for_release(
        self, team: str, release_id: str
    ) -> AuthorityPolicyActivation | None:
        """Latest authenticated legacy activation of one release, if any.

        Read adapter for the selector-aware legacy rollback path: the v1 wire
        contract names a release, while the transaction-owning
        ``reactivate_authority_policy_legacy`` names the exact authenticated
        activation. Never fabricates a missing activation.
        """
        team = self._validate_authority_selector_team(team)
        row = self._conn.execute(
            "SELECT * FROM authority_policy_activations "
            "WHERE team=? AND release_id=? ORDER BY epoch DESC LIMIT 1",
            (team, release_id),
        ).fetchone()
        return None if row is None else self._authority_policy_activation_from_row(row)

    # -- internal uncommitted write seams (transaction owner above) --

    def _insert_authority_policy_selector_history_uncommitted(
        self, selector: AuthorityPolicySelector
    ) -> None:
        self._conn.execute(
            """INSERT INTO authority_policy_active_selector_history
               (selector_id,team,family,selector_epoch,previous_selector_id,
                legacy_activation_id,v2_activation_id,created_at)
               VALUES (:selector_id,:team,:family,:selector_epoch,:previous_selector_id,
                       :legacy_activation_id,:v2_activation_id,:created_at)""",
            selector.model_dump(mode="json"),
        )

    def _write_authority_policy_active_selector_uncommitted(
        self, selector: AuthorityPolicySelector
    ) -> None:
        self._conn.execute(
            """INSERT INTO authority_policy_active_selector
               (team,selector_id,family,selector_epoch,previous_selector_id,
                legacy_activation_id,v2_activation_id,created_at)
               VALUES (:team,:selector_id,:family,:selector_epoch,:previous_selector_id,
                       :legacy_activation_id,:v2_activation_id,:created_at)
               ON CONFLICT(team) DO UPDATE SET
                   selector_id=excluded.selector_id, family=excluded.family,
                   selector_epoch=excluded.selector_epoch,
                   previous_selector_id=excluded.previous_selector_id,
                   legacy_activation_id=excluded.legacy_activation_id,
                   v2_activation_id=excluded.v2_activation_id,
                   created_at=excluded.created_at""",
            selector.model_dump(mode="json"),
        )

    def _write_authority_policy_v2_release_uncommitted(
        self, release: AuthorityPolicyV2Release, *, created_at: str
    ) -> None:
        self._conn.execute(
            """INSERT INTO authority_policy_v2_releases
               (id,team,policy_id,version,title,what_to_escalate,what_not_to_escalate,
                contract_digest,canonical_payload_json,policy_digest,created_at)
               VALUES (:id,:team,:policy_id,:version,:title,:what_to_escalate,
                       :what_not_to_escalate,:contract_digest,:canonical_payload_json,
                       :policy_digest,:created_at)""",
            {
                "id": release.release_id,
                "team": release.team,
                "policy_id": release.policy_id,
                "version": release.version,
                "title": release.title,
                "what_to_escalate": release.what_to_escalate,
                "what_not_to_escalate": release.what_not_to_escalate,
                "contract_digest": release.contract_digest,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    release.preimage()
                ).decode("utf-8"),
                "policy_digest": release.policy_digest,
                "created_at": created_at,
            },
        )

    def _write_authority_policy_v2_activation_uncommitted(
        self, activation: AuthorityPolicyV2Activation
    ) -> None:
        self._conn.execute(
            """INSERT INTO authority_policy_v2_activations
               (id,team,selector_epoch,release_id,release_digest,previous_selector_id,
                action,request_id,request_digest,activation_digest,created_at)
               VALUES (:id,:team,:selector_epoch,:release_id,:release_digest,
                       :previous_selector_id,:action,:request_id,:request_digest,
                       :activation_digest,:created_at)""",
            activation.model_dump(mode="json"),
        )

    def _insert_authority_policy_v2_control_audit_uncommitted(
        self, *, team: str, request_id: str | None, request_digest: str | None,
        kind: str, release_id: str | None, activation_id: str | None,
        selector_id: str | None, action: str | None, payload_json: str,
        created_at: str,
    ) -> None:
        self._conn.execute(
            """INSERT INTO authority_policy_v2_control_audit
               (team,request_id,request_digest,kind,release_id,activation_id,
                selector_id,action,payload_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (team, request_id, request_digest, kind, release_id, activation_id,
             selector_id, action, payload_json, created_at),
        )

    @staticmethod
    def _authority_policy_v2_receipt_from_audit_row(
        row,
    ) -> AuthorityPolicyV2ControlReceipt:
        try:
            payload = json.loads(row["payload_json"])
            receipt = AuthorityPolicyV2ControlReceipt.model_validate(payload["receipt"])
        except Exception as exc:
            raise ValueError("authority v2 control receipt is corrupt") from exc
        return receipt

    @staticmethod
    def _authority_policy_legacy_receipt_from_audit_row(
        row,
    ) -> AuthorityPolicyLegacyControlReceipt:
        try:
            payload = json.loads(row["payload_json"])
            receipt = AuthorityPolicyLegacyControlReceipt.model_validate(payload["receipt"])
        except Exception as exc:
            raise ValueError("authority legacy control receipt is corrupt") from exc
        return receipt

    def _authenticate_authority_v2_control_receipt(
        self, receipt: AuthorityPolicyV2ControlReceipt, team: str,
        selectors: list[AuthorityPolicySelector],
    ) -> AuthorityPolicySelector:
        """Prove a type-valid v2 receipt against the authenticated durable state."""
        if receipt.team != team:
            raise ValueError("authority v2 control receipt team mismatch")
        matches = [s for s in selectors if s.selector_id == receipt.selector_id]
        if len(matches) != 1:
            raise ValueError("authority v2 control receipt selector is not authenticated")
        selector = matches[0]
        if selector.family != "v2" or selector.selector_epoch != receipt.selector_epoch:
            raise ValueError("authority v2 control receipt selector is not authenticated")
        if selector.v2_activation_id != receipt.activation_id:
            raise ValueError("authority v2 control receipt activation is not authenticated")
        if selector.previous_selector_id != receipt.previous_selector_id:
            raise ValueError("authority v2 control receipt predecessor mismatch")
        activation = self.get_authority_policy_v2_activation(receipt.activation_id)
        if activation is None or activation.team != team:
            raise ValueError("authority v2 control receipt activation is not durable")
        if (
            activation.release_id != receipt.release_id
            or activation.release_digest != receipt.policy_digest
            or activation.selector_epoch != receipt.selector_epoch
            or activation.previous_selector_id != receipt.previous_selector_id
            or activation.action != receipt.action
            or activation.activation_digest != receipt.activation_digest
            or activation.request_id != receipt.activation_request_id
            or activation.request_digest != receipt.activation_request_digest
        ):
            raise ValueError("authority v2 control receipt does not match its durable activation")
        release = self.get_authority_policy_v2_release(receipt.release_id)
        if (
            release is None or release.team != team
            or release.policy_digest != receipt.policy_digest
            or release.version != receipt.release_version
        ):
            raise ValueError("authority v2 control receipt release is not durable")
        return selector

    def _authenticate_authority_legacy_control_receipt(
        self, receipt: AuthorityPolicyLegacyControlReceipt, team: str,
        selectors: list[AuthorityPolicySelector],
    ) -> AuthorityPolicySelector:
        """Prove a type-valid legacy receipt against the authenticated durable state."""
        if receipt.team != team:
            raise ValueError("authority legacy control receipt team mismatch")
        matches = [s for s in selectors if s.selector_id == receipt.selector_id]
        if len(matches) != 1:
            raise ValueError("authority legacy control receipt selector is not authenticated")
        selector = matches[0]
        if selector.family != "legacy_v1" or selector.selector_epoch != receipt.selector_epoch:
            raise ValueError("authority legacy control receipt selector is not authenticated")
        if selector.legacy_activation_id != receipt.activation_id:
            raise ValueError("authority legacy control receipt activation is not authenticated")
        if selector.previous_selector_id != receipt.previous_selector_id:
            raise ValueError("authority legacy control receipt predecessor mismatch")
        activation = self.get_authority_policy_activation(receipt.activation_id)
        if activation is None or activation.team != team:
            raise ValueError("authority legacy control receipt activation is not durable")
        if (
            activation.release_id != receipt.release_id
            or activation.activation_digest != receipt.activation_digest
        ):
            raise ValueError(
                "authority legacy control receipt does not match its durable activation")
        if receipt.kind == "legacy_activate":
            if (
                activation.action != receipt.action
                or activation.request_id != receipt.request_id
                or activation.request_digest != receipt.request_digest
            ):
                raise ValueError(
                    "authority legacy control receipt does not match its durable activation")
        release = self.get_authority_policy_release(receipt.release_id)
        if (
            release is None or release.team != team
            or release.policy_digest != receipt.policy_digest
            or release.version != receipt.release_version
        ):
            raise ValueError("authority legacy control receipt release is not durable")
        return selector

    def _authenticate_authority_selector_control_audit(
        self, team: str, selectors: list[AuthorityPolicySelector], *, bounded: bool = False
    ) -> None:
        """Authenticate the accepted control audit/preimage/receipt links.

        Every selector in ``selectors`` must be backed by exactly one accepted
        control-audit event; orphan or duplicated audit rows, a missing
        initializer, a mismatched preimage/receipt or a receipt whose durable
        activation or release is absent all refuse. Diagnostic
        ``activation_rejected`` events are not successful write authority and
        are ignored. Read-only: never allocates, reconstructs or replaces
        anything.

        ``bounded`` restricts the same authentication to the supplied prefix:
        audit evidence for selectors AFTER the pinned selector is neither
        required nor treated as orphaned, so a later legitimate activation
        cannot invalidate an older pin.
        """
        rows = self._conn.execute(
            "SELECT * FROM authority_policy_v2_control_audit WHERE team=? ORDER BY id",
            (team,),
        ).fetchall()
        initializers: dict[str, list] = {}
        selections: dict[str, list] = {}
        created: dict[str, list] = {}
        for row in rows:
            kind = row["kind"]
            if kind in ("selector_initialized_empty", "selector_initialized_legacy"):
                selector_id = row["selector_id"]
                if not isinstance(selector_id, str) or not selector_id:
                    raise ValueError("authority selector initialization audit is corrupt")
                initializers.setdefault(selector_id, []).append(row)
            elif kind == "activation_selected":
                selector_id = row["selector_id"]
                if not isinstance(selector_id, str) or not selector_id:
                    raise ValueError("authority selector selection audit is corrupt")
                selections.setdefault(selector_id, []).append(row)
            elif kind == "release_created":
                request_id = row["request_id"]
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("authority v2 create audit is corrupt")
                created.setdefault(request_id, []).append(row)
            elif kind == "activation_rejected":
                continue
            else:
                raise ValueError("authority selector control audit kind is invalid")

        first = selectors[0]
        if bounded:
            # Authenticate only the pinned prefix: later selections/creates are
            # legitimate future history, not residue of this pin.
            initializers = {key: value for key, value in initializers.items()
                            if key == first.selector_id}
            selections = {key: value for key, value in selections.items()
                          if key in {s.selector_id for s in selectors[1:]}}
        initializer_rows = initializers.get(first.selector_id, [])
        if len(initializer_rows) != 1:
            raise ValueError(
                "authority selector initialization audit is missing or duplicated")
        initializer_row = initializer_rows[0]
        if first.family == "empty":
            expected_kind = "selector_initialized_empty"
            expected_release_id = None
            expected_activation_id = None
            preimage = authority_policy_v2_initializer_selector_preimage(
                family="empty", legacy_activation_id=None, selector_epoch=0, team=team)
        else:
            activation = self.get_authority_policy_activation(first.legacy_activation_id)
            if activation is None or activation.team != team:
                raise ValueError("authority selector legacy initializer is corrupt")
            expected_kind = "selector_initialized_legacy"
            expected_release_id = activation.release_id
            expected_activation_id = activation.id
            preimage = authority_policy_v2_initializer_selector_preimage(
                family="legacy_v1", legacy_activation_id=activation.id,
                selector_epoch=1, team=team)
        if (
            initializer_row["kind"] != expected_kind
            or initializer_row["request_id"] is not None
            or initializer_row["request_digest"] is not None
            or initializer_row["release_id"] != expected_release_id
            or initializer_row["activation_id"] != expected_activation_id
            or initializer_row["action"] is not None
            or initializer_row["payload_json"]
            != authority_policy_v2_canonical_json_bytes(preimage).decode("utf-8")
        ):
            raise ValueError("authority selector initialization audit is corrupt")
        if set(initializers) != {first.selector_id}:
            raise ValueError("authority selector initialization audit residue")

        expected_selection_ids = {s.selector_id for s in selectors[1:]}
        if set(selections) != expected_selection_ids:
            raise ValueError("authority selector selection audit is missing or orphaned")

        paired_create_ids: set[str] = set()
        for selector in selectors[1:]:
            selector_rows = selections.get(selector.selector_id, [])
            if len(selector_rows) != 1:
                raise ValueError("authority selector selection audit is duplicated")
            row = selector_rows[0]
            if selector.family == "v2":
                activation = self.get_authority_policy_v2_activation(
                    selector.v2_activation_id)
                if activation is None or activation.team != team:
                    raise ValueError("authority selector v2 activation linkage is corrupt")
                if (
                    row["activation_id"] != activation.id
                    or row["release_id"] != activation.release_id
                    or row["action"] != activation.action
                    or row["request_id"] != activation.request_id
                    or row["request_digest"] != activation.request_digest
                ):
                    raise ValueError("authority selector selection audit is corrupt")
                receipt = self._authority_policy_v2_receipt_from_audit_row(row)
                self._authenticate_authority_v2_control_receipt(receipt, team, selectors)
                if receipt.create_request_id is not None:
                    paired = created.get(receipt.create_request_id, [])
                    if len(paired) != 1:
                        raise ValueError("authority v2 create audit is missing or duplicated")
                    paired_row = paired[0]
                    if (
                        paired_row["release_id"] != receipt.release_id
                        or paired_row["request_digest"] != receipt.create_request_digest
                        or paired_row["selector_id"] is not None
                        or paired_row["activation_id"] is not None
                        or self._authority_policy_v2_receipt_from_audit_row(paired_row)
                        != receipt
                    ):
                        raise ValueError("authority v2 create audit is corrupt")
                    paired_create_ids.add(receipt.create_request_id)
            else:
                activation = self.get_authority_policy_activation(
                    selector.legacy_activation_id)
                if activation is None or activation.team != team:
                    raise ValueError("authority selector legacy activation linkage is corrupt")
                if (
                    row["activation_id"] != activation.id
                    or row["release_id"] != activation.release_id
                    or row["action"] not in (
                        "bootstrap", "activate", "reactivate_rollback")
                ):
                    raise ValueError("authority selector selection audit is corrupt")
                receipt = self._authority_policy_legacy_receipt_from_audit_row(row)
                self._authenticate_authority_legacy_control_receipt(receipt, team, selectors)

        if bounded:
            created = {key: value for key, value in created.items()
                       if key in paired_create_ids}
        if set(created) != paired_create_ids:
            raise ValueError("authority v2 create audit is missing or orphaned")
        for request_id, request_rows in created.items():
            if len(request_rows) != 1:
                raise ValueError("authority v2 create audit is duplicated")

    def _get_authority_selector_uncommitted(
        self, team: str
    ) -> AuthorityPolicySelector | None:
        chain = self._load_authority_selector_history_chain(team)
        row = self._conn.execute(
            "SELECT * FROM authority_policy_active_selector WHERE team=?", (team,)
        ).fetchone()
        if row is None:
            if chain:
                raise ValueError("authority selector history exists without a live selector")
            return None
        selector = self._authority_policy_selector_from_row(row)
        if not chain or chain[-1] != selector:
            raise ValueError("authority live selector does not match its authenticated history tip")
        return selector

    @staticmethod
    def _require_authority_selector_cas(
        selector: AuthorityPolicySelector, expected_selector_id: str | None
    ) -> None:
        if expected_selector_id is None:
            if selector.family != "empty" or selector.selector_epoch != 0:
                raise sqlite3.IntegrityError("v2 authority control base selector conflict")
        elif expected_selector_id != selector.selector_id:
            raise sqlite3.IntegrityError("v2 authority control base selector conflict")

    @_synchronized
    def ensure_authority_selector(self, team: str) -> AuthorityPolicySelector:
        """Serialized, idempotent, authenticated selector initialization.

        The Database owns ``BEGIN IMMEDIATE`` and commits the initializer,
        history row and control audit together. Initialization commits
        separately BEFORE any client control write, so a later policy-write
        rollback never erases it. An existing selector is authenticated and
        returned unchanged (never reconstructed).
        """
        team = self._validate_authority_selector_team(team)
        nested = self._authority_begin()
        try:
            existing = self._conn.execute(
                "SELECT * FROM authority_policy_active_selector WHERE team=?", (team,)
            ).fetchone()
            if existing is not None:
                selector = self._authority_policy_selector_from_row(existing)
                chain = self._load_authority_selector_history_chain(team)
                if not chain or chain[-1] != selector:
                    raise ValueError(
                        "authority selector initialization_unavailable: "
                        "live selector does not match its history tip"
                    )
                self._authority_commit(nested)
                return selector
            history_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authority_policy_active_selector_history WHERE team=?",
                (team,),
            ).fetchone()["n"]
            if history_count:
                raise ValueError(
                    "authority selector initialization_unavailable: selector history residue"
                )
            # Orphan initializer/control audit without a selector or history is
            # refusal, never a reason to allocate a replacement initializer.
            control_audit_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authority_policy_v2_control_audit WHERE team=?",
                (team,),
            ).fetchone()["n"]
            if control_audit_count:
                raise ValueError(
                    "authority selector initialization_unavailable: control audit residue"
                )
            if self._conn.execute(
                "SELECT 1 FROM authority_policy_v2_activations WHERE team=? LIMIT 1", (team,)
            ).fetchone() is not None:
                raise ValueError(
                    "authority selector initialization_unavailable: v2 activation residue"
                )
            v2_release_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authority_policy_v2_releases WHERE team=?", (team,)
            ).fetchone()["n"]
            legacy_activation_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authority_policy_activations WHERE team=?", (team,)
            ).fetchone()["n"]
            legacy_release_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authority_policy_releases WHERE team=?", (team,)
            ).fetchone()["n"]
            # Full sealed-history authentication, not a bare MAX(epoch) lookup.
            legacy_current = self.get_current_authority_policy_activation(team)
            covered = 0 if legacy_current is None else legacy_current.epoch
            if legacy_activation_count != covered:
                raise ValueError(
                    "authority selector initialization_unavailable: legacy history is not covered"
                )
            if legacy_activation_count == 0 and legacy_release_count > 0:
                raise ValueError(
                    "authority selector initialization_unselected_history: legacy releases"
                )
            if v2_release_count > 0:
                raise ValueError(
                    "authority selector initialization_unselected_history: v2 releases"
                )
            created_at = _now().isoformat()
            if legacy_activation_count == 0 and legacy_release_count == 0:
                selector_id = authority_policy_v2_initializer_selector_id(
                    family="empty", legacy_activation_id=None, selector_epoch=0, team=team,
                )
                selector = AuthorityPolicySelector(
                    team=team, selector_id=selector_id, family="empty",
                    selector_epoch=0, previous_selector_id=None,
                    legacy_activation_id=None, v2_activation_id=None,
                    created_at=created_at,
                )
                self._insert_authority_policy_selector_history_uncommitted(selector)
                self._write_authority_policy_active_selector_uncommitted(selector)
                self._insert_authority_policy_v2_control_audit_uncommitted(
                    team=team, request_id=None, request_digest=None,
                    kind="selector_initialized_empty", release_id=None,
                    activation_id=None, selector_id=selector_id, action=None,
                    payload_json=authority_policy_v2_canonical_json_bytes(
                        authority_policy_v2_initializer_selector_preimage(
                            family="empty", legacy_activation_id=None,
                            selector_epoch=0, team=team,
                        )
                    ).decode("utf-8"),
                    created_at=created_at,
                )
            elif legacy_activation_count > 0 and legacy_current is not None:
                release = self.get_authority_policy_release(legacy_current.release_id)
                if release is None or release.team != team:
                    raise ValueError(
                        "authority selector initialization_unavailable: "
                        "legacy release linkage is corrupt"
                    )
                selector_id = authority_policy_v2_initializer_selector_id(
                    family="legacy_v1", legacy_activation_id=legacy_current.id,
                    selector_epoch=1, team=team,
                )
                selector = AuthorityPolicySelector(
                    team=team, selector_id=selector_id, family="legacy_v1",
                    selector_epoch=1, previous_selector_id=None,
                    legacy_activation_id=legacy_current.id, v2_activation_id=None,
                    created_at=created_at,
                )
                self._insert_authority_policy_selector_history_uncommitted(selector)
                self._write_authority_policy_active_selector_uncommitted(selector)
                self._insert_authority_policy_v2_control_audit_uncommitted(
                    team=team, request_id=None, request_digest=None,
                    kind="selector_initialized_legacy",
                    release_id=legacy_current.release_id,
                    activation_id=legacy_current.id, selector_id=selector_id, action=None,
                    payload_json=authority_policy_v2_canonical_json_bytes(
                        authority_policy_v2_initializer_selector_preimage(
                            family="legacy_v1", legacy_activation_id=legacy_current.id,
                            selector_epoch=1, team=team,
                        )
                    ).decode("utf-8"),
                    created_at=created_at,
                )
            else:
                raise ValueError(
                    "authority selector initialization_unavailable: unsupported family"
                )
            self._authority_commit(nested)
            return selector
        except Exception:
            self._authority_rollback(nested)
            raise

    @_synchronized
    def create_and_activate_authority_policy_v2(
        self, request: AuthorityPolicyV2PairedControlRequest | dict
    ) -> AuthorityPolicyV2ControlReceipt:
        """Atomically save paired texts and select them in ONE policy transaction."""
        request = AuthorityPolicyV2PairedControlRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, AuthorityPolicyV2PairedControlRequest) else request
        )
        team = request.team
        create_digest = request.create_request_digest()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_control_audit "
                "WHERE team=? AND request_id=? ORDER BY id DESC LIMIT 1",
                (team, request.create_request_id),
            ).fetchone()
            if row is not None:
                if row["kind"] != "release_created" or row["request_digest"] != create_digest:
                    raise sqlite3.IntegrityError(
                        "v2 authority create request conflicts with an existing control write"
                    )
                receipt = self._authority_policy_v2_receipt_from_audit_row(row)
                incoming_activation = authority_policy_v2_sha256(
                    request.activation_request_preimage(receipt.release_id)
                )
                if incoming_activation != receipt.activation_request_digest:
                    raise sqlite3.IntegrityError(
                        "v2 authority create replay conflicts with a different activation request"
                    )
                selectors = self._load_authority_selector_history_chain(team)
                self._authenticate_authority_v2_control_receipt(receipt, team, selectors)
                self._conn.commit()
                return receipt
            if self._conn.execute(
                "SELECT 1 FROM authority_policy_v2_control_audit "
                "WHERE team=? AND request_id=? LIMIT 1",
                (team, request.activation_request_id),
            ).fetchone() is not None:
                raise sqlite3.IntegrityError(
                    "v2 authority activation request id is already used"
                )
            selector = self._get_authority_selector_uncommitted(team)
            if selector is None:
                raise sqlite3.IntegrityError("authority selector is not initialized")
            self._require_authority_selector_cas(selector, request.based_on_selector_id)
            self._require_authority_selector_cas(selector, request.expected_selector_id)
            if request.action == "bootstrap" and selector.family != "empty":
                raise sqlite3.IntegrityError(
                    "v2 authority bootstrap requires the empty selector"
                )
            if request.action == "activate" and selector.family == "empty":
                raise sqlite3.IntegrityError(
                    "v2 authority activate requires an existing selection"
                )
            version_row = self._conn.execute(
                "SELECT MAX(version) AS version FROM authority_policy_v2_releases "
                "WHERE team=? AND policy_id=?",
                (team, request.policy_id),
            ).fetchone()
            version = 1 if version_row is None or version_row["version"] is None else int(
                version_row["version"]
            ) + 1
            if version > 2147483647:
                raise sqlite3.IntegrityError("v2 authority policy version is exhausted")
            release = request.to_release(version=version)
            new_epoch = selector.selector_epoch + 1
            if new_epoch > 2147483647:
                raise sqlite3.IntegrityError("authority selector epoch is exhausted")
            created_at = _now().isoformat()
            activation_digest = authority_policy_v2_sha256(
                request.activation_request_preimage(release.release_id)
            )
            activation = AuthorityPolicyV2Activation.create(
                team=team, selector_epoch=new_epoch, release_id=release.release_id,
                release_digest=release.policy_digest,
                previous_selector_id=selector.selector_id, action=request.action,
                request_id=request.activation_request_id,
                request_digest=activation_digest, created_at=created_at,
            )
            selector_id = authority_policy_v2_selector_id(
                activation_id=activation.id, family="v2",
                previous_selector_id=selector.selector_id,
                selector_epoch=new_epoch, team=team,
            )
            new_selector = AuthorityPolicySelector(
                team=team, selector_id=selector_id, family="v2",
                selector_epoch=new_epoch, previous_selector_id=selector.selector_id,
                legacy_activation_id=None, v2_activation_id=activation.id,
                created_at=created_at,
            )
            receipt = AuthorityPolicyV2ControlReceipt(
                team=team, kind="v2_create_activate",
                create_request_id=request.create_request_id,
                create_request_digest=create_digest,
                activation_request_id=request.activation_request_id,
                activation_request_digest=activation_digest,
                release_id=release.release_id, policy_digest=release.policy_digest,
                release_version=release.version, activation_id=activation.id,
                activation_digest=activation.activation_digest, selector_id=selector_id,
                selector_epoch=new_epoch, action=request.action,
                previous_selector_id=selector.selector_id, created_at=created_at,
            )
            receipt_payload = json.dumps(
                {"receipt": json.loads(receipt.canonical_json())},
                sort_keys=True, separators=(",", ":"),
            )
            self._write_authority_policy_v2_release_uncommitted(release, created_at=created_at)
            self._write_authority_policy_v2_activation_uncommitted(activation)
            self._insert_authority_policy_selector_history_uncommitted(new_selector)
            self._write_authority_policy_active_selector_uncommitted(new_selector)
            self._insert_authority_policy_v2_control_audit_uncommitted(
                team=team, request_id=request.create_request_id,
                request_digest=create_digest, kind="release_created",
                release_id=release.release_id, activation_id=None,
                selector_id=None, action=None, payload_json=receipt_payload,
                created_at=created_at,
            )
            self._insert_authority_policy_v2_control_audit_uncommitted(
                team=team, request_id=request.activation_request_id,
                request_digest=activation_digest, kind="activation_selected",
                release_id=release.release_id, activation_id=activation.id,
                selector_id=selector_id, action=request.action,
                payload_json=receipt_payload, created_at=created_at,
            )
            self._conn.commit()
            return receipt
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def activate_authority_policy_v2(
        self, request: AuthorityPolicyV2ActivationControlRequest | dict
    ) -> AuthorityPolicyV2ControlReceipt:
        """Select an existing v2 release under the same transaction boundary."""
        request = AuthorityPolicyV2ActivationControlRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, AuthorityPolicyV2ActivationControlRequest) else request
        )
        team = request.team
        request_digest = request.request_digest()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_control_audit "
                "WHERE team=? AND request_id=? ORDER BY id DESC LIMIT 1",
                (team, request.request_id),
            ).fetchone()
            if row is not None:
                if row["kind"] != "activation_selected" or row["request_digest"] != request_digest:
                    raise sqlite3.IntegrityError(
                        "v2 authority activation request conflicts with an existing control write"
                    )
                receipt = self._authority_policy_v2_receipt_from_audit_row(row)
                selectors = self._load_authority_selector_history_chain(team)
                self._authenticate_authority_v2_control_receipt(receipt, team, selectors)
                self._conn.commit()
                return receipt
            selector = self._get_authority_selector_uncommitted(team)
            if selector is None:
                raise sqlite3.IntegrityError("authority selector is not initialized")
            self._require_authority_selector_cas(selector, request.expected_selector_id)
            release = self.get_authority_policy_v2_release(request.release_id)
            if release is None or release.team != team:
                raise sqlite3.IntegrityError("v2 authority activation release is unavailable")
            previously_selected = self._conn.execute(
                "SELECT 1 FROM authority_policy_v2_activations WHERE team=? AND release_id=? LIMIT 1",
                (team, release.release_id),
            ).fetchone() is not None
            if request.action == "bootstrap":
                raise sqlite3.IntegrityError(
                    "v2 authority bootstrap is only valid for a newly saved release"
                )
            if request.action == "activate":
                if selector.family == "empty":
                    raise sqlite3.IntegrityError(
                        "v2 authority activate requires an existing selection"
                    )
                if previously_selected:
                    raise sqlite3.IntegrityError(
                        "v2 authority activate target was previously selected"
                    )
            else:
                if not previously_selected:
                    raise sqlite3.IntegrityError(
                        "v2 authority rollback target was never selected"
                    )
                if selector.family != "v2" or selector.v2_activation_id is None:
                    raise sqlite3.IntegrityError(
                        "v2 authority rollback requires a current v2 selection"
                    )
                current_activation = self.get_authority_policy_v2_activation(
                    selector.v2_activation_id
                )
                if current_activation is None:
                    raise ValueError("authority selector v2 activation linkage is corrupt")
                current_release = self.get_authority_policy_v2_release(
                    current_activation.release_id
                )
                if current_release is None:
                    raise ValueError("authority v2 current release is missing")
                if release.release_id == current_release.release_id:
                    raise sqlite3.IntegrityError(
                        "v2 authority rollback target is the current release"
                    )
                if (
                    release.policy_id != current_release.policy_id
                    or release.version >= current_release.version
                ):
                    raise sqlite3.IntegrityError(
                        "v2 authority rollback target is not an older revision of the current policy"
                    )
            new_epoch = selector.selector_epoch + 1
            if new_epoch > 2147483647:
                raise sqlite3.IntegrityError("authority selector epoch is exhausted")
            created_at = _now().isoformat()
            activation = AuthorityPolicyV2Activation.create(
                team=team, selector_epoch=new_epoch, release_id=release.release_id,
                release_digest=release.policy_digest,
                previous_selector_id=selector.selector_id, action=request.action,
                request_id=request.request_id, request_digest=request_digest,
                created_at=created_at,
            )
            selector_id = authority_policy_v2_selector_id(
                activation_id=activation.id, family="v2",
                previous_selector_id=selector.selector_id,
                selector_epoch=new_epoch, team=team,
            )
            new_selector = AuthorityPolicySelector(
                team=team, selector_id=selector_id, family="v2",
                selector_epoch=new_epoch, previous_selector_id=selector.selector_id,
                legacy_activation_id=None, v2_activation_id=activation.id,
                created_at=created_at,
            )
            receipt = AuthorityPolicyV2ControlReceipt(
                team=team, kind="v2_activate", create_request_id=None,
                create_request_digest=None, activation_request_id=request.request_id,
                activation_request_digest=request_digest, release_id=release.release_id,
                policy_digest=release.policy_digest, release_version=release.version,
                activation_id=activation.id, activation_digest=activation.activation_digest,
                selector_id=selector_id, selector_epoch=new_epoch, action=request.action,
                previous_selector_id=selector.selector_id, created_at=created_at,
            )
            receipt_payload = json.dumps(
                {"receipt": json.loads(receipt.canonical_json())},
                sort_keys=True, separators=(",", ":"),
            )
            self._write_authority_policy_v2_activation_uncommitted(activation)
            self._insert_authority_policy_selector_history_uncommitted(new_selector)
            self._write_authority_policy_active_selector_uncommitted(new_selector)
            self._insert_authority_policy_v2_control_audit_uncommitted(
                team=team, request_id=request.request_id, request_digest=request_digest,
                kind="activation_selected", release_id=release.release_id,
                activation_id=activation.id, selector_id=selector_id,
                action=request.action, payload_json=receipt_payload,
                created_at=created_at,
            )
            self._conn.commit()
            return receipt
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def activate_authority_policy_legacy(
        self, request: AuthorityPolicyLegacyActivationRequest | dict
    ) -> AuthorityPolicyLegacyControlReceipt:
        """Select a NEW legacy v1 release under the shared selector CAS.

        The legacy family epoch is allocated from the sealed legacy stream and
        the legacy activation, its legacy audit, the new selector/history and
        the control audit/receipt commit in ONE ``BEGIN IMMEDIATE``.
        """
        request = AuthorityPolicyLegacyActivationRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, AuthorityPolicyLegacyActivationRequest) else request
        )
        team = request.team
        request_digest = request.request_digest()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_control_audit "
                "WHERE team=? AND request_id=? ORDER BY id DESC LIMIT 1",
                (team, request.request_id),
            ).fetchone()
            if row is not None:
                if (
                    row["kind"] != "activation_selected"
                    or row["request_digest"] != request_digest
                ):
                    raise sqlite3.IntegrityError(
                        "legacy authority activation request conflicts with an existing control write"
                    )
                receipt = self._authority_policy_legacy_receipt_from_audit_row(row)
                selectors = self._load_authority_selector_history_chain(team)
                self._authenticate_authority_legacy_control_receipt(receipt, team, selectors)
                self._conn.commit()
                return receipt
            selector = self._get_authority_selector_uncommitted(team)
            if selector is None:
                raise sqlite3.IntegrityError("authority selector is not initialized")
            self._require_authority_selector_cas(selector, request.expected_selector_id)
            release = self.get_authority_policy_release(request.release_id)
            if release is None or release.team != team:
                raise sqlite3.IntegrityError(
                    "legacy authority activation release is unavailable")
            previous = self._conn.execute(
                "SELECT * FROM authority_policy_activations WHERE team=? "
                "ORDER BY epoch DESC LIMIT 1",
                (team,),
            ).fetchone()
            expected_previous_epoch = 0 if previous is None else previous["epoch"]
            action = "bootstrap" if previous is None else "activate"
            activation = self._append_authority_policy_activation_uncommitted(
                team=team, release=release,
                expected_previous_epoch=expected_previous_epoch, action=action,
                request_id=request.request_id, request_digest=request_digest,
            )
            self._insert_authority_policy_activation_audit_uncommitted(
                team=team, release=release, activation=activation,
                request_digest=request_digest,
            )
            new_epoch = selector.selector_epoch + 1
            if new_epoch > 2147483647:
                raise sqlite3.IntegrityError("authority selector epoch is exhausted")
            created_at = _now().isoformat()
            selector_id = authority_policy_v2_selector_id(
                activation_id=activation.id, family="legacy_v1",
                previous_selector_id=selector.selector_id,
                selector_epoch=new_epoch, team=team,
            )
            new_selector = AuthorityPolicySelector(
                team=team, selector_id=selector_id, family="legacy_v1",
                selector_epoch=new_epoch, previous_selector_id=selector.selector_id,
                legacy_activation_id=activation.id, v2_activation_id=None,
                created_at=created_at,
            )
            receipt = AuthorityPolicyLegacyControlReceipt(
                team=team, kind="legacy_activate", request_id=request.request_id,
                request_digest=request_digest, release_id=release.id,
                policy_digest=release.policy_digest, release_version=release.version,
                activation_id=activation.id,
                activation_digest=activation.activation_digest,
                selector_id=selector_id, selector_epoch=new_epoch,
                previous_selector_id=selector.selector_id, action=action,
                created_at=created_at,
            )
            receipt_payload = json.dumps(
                {"receipt": json.loads(receipt.canonical_json())},
                sort_keys=True, separators=(",", ":"),
            )
            self._insert_authority_policy_selector_history_uncommitted(new_selector)
            self._write_authority_policy_active_selector_uncommitted(new_selector)
            self._insert_authority_policy_v2_control_audit_uncommitted(
                team=team, request_id=request.request_id,
                request_digest=request_digest, kind="activation_selected",
                release_id=release.id, activation_id=activation.id,
                selector_id=selector_id, action=action,
                payload_json=receipt_payload, created_at=created_at,
            )
            self._conn.commit()
            return receipt
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def reactivate_authority_policy_legacy(
        self, request: AuthorityPolicyLegacyReactivationRequest | dict
    ) -> AuthorityPolicyLegacyControlReceipt:
        """Re-select an authenticated previously selected legacy v1 activation.

        Appends a NEW selector/history/control audit with
        ``reactivate_rollback``. The original legacy activation is never
        appended, renumbered or resealed, so a v1->v2->v1 cross-family
        rollback is byte-preserving.
        """
        request = AuthorityPolicyLegacyReactivationRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, AuthorityPolicyLegacyReactivationRequest) else request
        )
        team = request.team
        request_digest = request.request_digest()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM authority_policy_v2_control_audit "
                "WHERE team=? AND request_id=? ORDER BY id DESC LIMIT 1",
                (team, request.request_id),
            ).fetchone()
            if row is not None:
                if (
                    row["kind"] != "activation_selected"
                    or row["request_digest"] != request_digest
                ):
                    raise sqlite3.IntegrityError(
                        "legacy authority reactivation request conflicts with an existing control write"
                    )
                receipt = self._authority_policy_legacy_receipt_from_audit_row(row)
                selectors = self._load_authority_selector_history_chain(team)
                self._authenticate_authority_legacy_control_receipt(receipt, team, selectors)
                self._conn.commit()
                return receipt
            selector = self._get_authority_selector_uncommitted(team)
            if selector is None:
                raise sqlite3.IntegrityError("authority selector is not initialized")
            self._require_authority_selector_cas(selector, request.expected_selector_id)
            activation = self.get_authority_policy_activation(request.activation_id)
            if activation is None or activation.team != team:
                raise sqlite3.IntegrityError(
                    "legacy authority reactivation target is unavailable")
            release = self.get_authority_policy_release(activation.release_id)
            if release is None or release.team != team:
                raise ValueError("legacy authority reactivation release linkage is corrupt")
            previously_selected = self._conn.execute(
                "SELECT 1 FROM authority_policy_active_selector_history "
                "WHERE team=? AND legacy_activation_id=? LIMIT 1",
                (team, activation.id),
            ).fetchone() is not None
            if not previously_selected:
                raise sqlite3.IntegrityError(
                    "legacy authority reactivation target was never selected")
            if selector.family == "legacy_v1" and selector.legacy_activation_id == activation.id:
                raise sqlite3.IntegrityError(
                    "legacy authority reactivation target is the current selection")
            new_epoch = selector.selector_epoch + 1
            if new_epoch > 2147483647:
                raise sqlite3.IntegrityError("authority selector epoch is exhausted")
            created_at = _now().isoformat()
            selector_id = authority_policy_v2_selector_id(
                activation_id=activation.id, family="legacy_v1",
                previous_selector_id=selector.selector_id,
                selector_epoch=new_epoch, team=team,
            )
            new_selector = AuthorityPolicySelector(
                team=team, selector_id=selector_id, family="legacy_v1",
                selector_epoch=new_epoch, previous_selector_id=selector.selector_id,
                legacy_activation_id=activation.id, v2_activation_id=None,
                created_at=created_at,
            )
            receipt = AuthorityPolicyLegacyControlReceipt(
                team=team, kind="legacy_reactivate_rollback",
                request_id=request.request_id, request_digest=request_digest,
                release_id=release.id, policy_digest=release.policy_digest,
                release_version=release.version, activation_id=activation.id,
                activation_digest=activation.activation_digest,
                selector_id=selector_id, selector_epoch=new_epoch,
                previous_selector_id=selector.selector_id,
                action="reactivate_rollback", created_at=created_at,
            )
            receipt_payload = json.dumps(
                {"receipt": json.loads(receipt.canonical_json())},
                sort_keys=True, separators=(",", ":"),
            )
            self._insert_authority_policy_selector_history_uncommitted(new_selector)
            self._write_authority_policy_active_selector_uncommitted(new_selector)
            self._insert_authority_policy_v2_control_audit_uncommitted(
                team=team, request_id=request.request_id,
                request_digest=request_digest, kind="activation_selected",
                release_id=release.id, activation_id=activation.id,
                selector_id=selector_id, action="reactivate_rollback",
                payload_json=receipt_payload, created_at=created_at,
            )
            self._conn.commit()
            return receipt
        except Exception:
            self._conn.rollback()
            raise
