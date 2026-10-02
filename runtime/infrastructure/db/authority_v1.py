from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _synchronized,
)
from runtime.models import (
    AuthorityAuditEvent,
    AuthorityAuditEventType,
    AuthorityAuditPayload,
    AuthorityCandidate,
    AuthorityCandidatePolicyPin,
    AuthorityEvaluation,
    AuthorityFenceResult,
    AuthorityRedactionClass,
    AuthorityRetentionClass,
    BlockKind,
    TaskStatus,
    validate_authority_digest,
    validate_authority_version,
)


# Terminal statuses that must never be continued by the authority hook
# (mirrors ``authority._TERMINAL_STATUSES``; kept here so the DB-level
# consumption recheck needs no orchestrator import).
_AUTHORITY_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "superseded", "cancelled",
})

# Child task-result verdicts that do NOT block a same-root continuation.
_AUTHORITY_APPROVED_VERDICTS = frozenset({"APPROVE", "PASS"})


def _authority_claim_key(
    root_task_id: str,
    manager_session_id: str,
    causal_event_id: str,
    policy_digest: str,
    prompt_digest: str,
    model_digest: str,
) -> str:
    """Deterministic CAS key for the authority candidate claim tuple.

    One durable candidate wins the
    root/session/causal-event/policy-prompt-model tuple. The key is a sha256
    digest of the tuple joined with unit-separator bytes so distinct inputs
    cannot collide across field boundaries. It is the ``claim_key`` UNIQUE
    column on ``authority_candidates`` — the database-level exactly-one
    arbiter (not merely the in-process lock).
    """
    material = "\x1f".join(
        (
            root_task_id,
            manager_session_id,
            causal_event_id,
            policy_digest,
            prompt_digest,
            model_digest,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_authority_fence_results(raw: str | None) -> dict[str, AuthorityFenceResult] | None:
    """Parse a persisted fence-results JSON column back into typed results."""
    if raw is None:
        return None
    data = json.loads(raw)
    return {name: AuthorityFenceResult.model_validate(value) for name, value in data.items()}


def _validate_authority_class(value: str, field: str, enum_cls) -> str:
    """Validate a controlled retention/redaction classification value.

    Raises ValueError for any value outside the closed vocabulary — the caller
    must fail loudly BEFORE any durable write, so a bad classification can
    never be swallowed by conflict handling into a phantom CAS loser.
    """
    allowed = {member.value for member in enum_cls}
    if value not in allowed:
        raise ValueError(
            f"{field} must be one of: {', '.join(sorted(allowed))}; got {value!r}"
        )
    return value


def _serialize_authority_fence_results(
    fence_results: dict | None,
) -> str | None:
    """Strictly validate and serialize a fence-results mapping.

    Each value must be a closed ``AuthorityFenceResult`` (extra keys and
    unknown codes are rejected); fence names must be non-empty strings. Returns
    the JSON column value, or None for an absent mapping. Raises ValueError
    (via Pydantic) rather than silently storing or redacting anything.
    """
    if fence_results is None:
        return None
    if not isinstance(fence_results, dict):
        raise ValueError("fence_results must be a dict mapping fence name -> AuthorityFenceResult")
    normalized: dict[str, AuthorityFenceResult] = {}
    for name, value in fence_results.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("fence result names must be non-empty strings")
        normalized[name] = AuthorityFenceResult.model_validate(value)
    return json.dumps(
        {name: result.model_dump(mode="json") for name, result in normalized.items()}
    )


def _serialize_authority_audit_payload(payload: object | None) -> str | None:
    """Strictly validate and serialize an authority audit payload.

    The payload must be a closed ``AuthorityAuditPayload`` — unknown keys,
    nested arbitrary JSON, prose, credentials, and raw model exchanges are
    rejected. Returns the JSON column value, or None for an absent payload.
    """
    if payload is None:
        return None
    model = AuthorityAuditPayload.model_validate(payload)
    return json.dumps(model.model_dump(mode="json", exclude_none=True))


class AuthorityV1Mixin:
    @_synchronized
    def get_authority_candidate_policy_pin(
        self, candidate_id: str
    ) -> AuthorityCandidatePolicyPin | None:
        row = self._conn.execute(
            "SELECT * FROM authority_candidate_policy_pins WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        pin = AuthorityCandidatePolicyPin.model_validate(dict(row))
        # Re-resolve every immutable identity on reads; missing/corrupt linkage
        # fails closed even if a damaged database bypassed normal FK/triggers.
        linked = self._conn.execute(
            """SELECT 1 FROM authority_candidates c
               JOIN authority_policy_releases r ON r.id=?
               JOIN authority_policy_activations a ON a.id=?
               WHERE c.id=? AND c.team=r.team AND c.team=a.team
                 AND a.release_id=r.id AND a.epoch=?
                 AND c.policy_id=r.policy_id
                 AND c.policy_version=CAST(r.version AS TEXT)
                 AND c.policy_digest=r.policy_digest""",
            (pin.release_id, pin.activation_id, pin.candidate_id, pin.activation_epoch),
        ).fetchone()
        if linked is None:
            raise ValueError(f"authority candidate policy pin {candidate_id!r} is corrupt")
        self.get_authority_policy_release(pin.release_id)
        activation = self.get_authority_policy_activation(pin.activation_id)
        if activation is None:
            raise ValueError(f"authority candidate policy pin {candidate_id!r} is corrupt")
        return pin

    @_synchronized
    def claim_authority_candidate_with_policy_pin(
        self,
        *,
        release_id: str,
        activation_id: str,
        activation_epoch: int,
        provider_id: str,
        executor_kind: str,
        **candidate_kwargs,
    ) -> tuple[AuthorityCandidate, AuthorityCandidatePolicyPin]:
        """Dark S1 API: atomically insert exactly one candidate and its pin."""
        if not provider_id or not executor_kind:
            raise ValueError("provider_id and executor_kind must be non-empty")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            candidate_id, won = self.claim_authority_candidate(
                **candidate_kwargs, _commit=False
            )
            if not won:
                raise sqlite3.IntegrityError("candidate policy pin claim already exists")
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO authority_candidate_policy_pins
                   (candidate_id,release_id,activation_id,activation_epoch,
                    provider_id,executor_kind,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (candidate_id, release_id, activation_id, activation_epoch,
                 provider_id, executor_kind, now),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        candidate = self.get_authority_candidate(candidate_id)
        pin = self.get_authority_candidate_policy_pin(candidate_id)
        if candidate is None or pin is None:
            raise RuntimeError("atomic authority candidate policy pin write disappeared")
        return candidate, pin

    @_synchronized
    def claim_authority_candidate(
        self,
        *,
        root_task_id: str,
        team: str,
        manager_agent: str,
        manager_session_id: str,
        causal_event_id: str,
        causal_event_digest: str,
        causal_result_id: str | None,
        policy_id: str,
        policy_version: str,
        policy_digest: str,
        prompt_id: str,
        prompt_version: str,
        prompt_digest: str,
        model_id: str,
        model_version: str,
        model_digest: str,
        snapshot_digest: str,
        snapshot_retention_class: str = "digest_only",
        snapshot_redaction_class: str = "redacted",
        fence_results: dict | None = None,
        _commit: bool = True,
    ) -> tuple[str, bool]:
        """Deterministic, barrier-ready CAS claim/create contract.

        Exactly one durable candidate wins the
        root/session/causal-event/policy-prompt-model tuple. The candidate id
        and ``claim_key`` are both derived deterministically from that tuple,
        and ``claim_key`` carries a UNIQUE constraint, so a concurrent second
        claim with the same tuple cannot mint a second candidate.

        Returns ``(candidate_id, won)``. ``won`` is True only for the caller
        whose INSERT actually created the row (the durable winner). A loser
        receives the same deterministic ``candidate_id`` as the winner and
        ``won=False`` — the documented loser result. Callers must never assert
        incidental thread ordering; the UNIQUE constraint, not scheduling, is
        the arbiter. No evaluator is invoked and no consumption occurs here.

        Controlled inputs (``snapshot_retention_class``/
        ``snapshot_redaction_class``) are validated against their closed
        vocabulary and raise ``ValueError`` before any durable write.
        Non-uniqueness constraint failures raise ``sqlite3.IntegrityError``;
        the deterministic id/``claim_key`` uniqueness race maps to the
        ``(candidate_id, won=False)`` loser result ONLY when the conflicting
        row is proven to be the exact deterministic immutable claim tuple — a
        raw-SQL/imported row occupying either key under a different identity
        raises ``sqlite3.IntegrityError`` instead of misreporting an
        unrelated durable row as the winner. Never a phantom loser with no
        row.
        """
        # Pre-serialization validation — reject prose/credentials/model exchanges
        # smuggled into digest fields and non-closed fence results BEFORE any row
        # is written (no silent redaction, no durable residue).
        validate_authority_digest(causal_event_digest, "causal_event_digest")
        validate_authority_digest(policy_digest, "policy_digest")
        validate_authority_digest(prompt_digest, "prompt_digest")
        validate_authority_digest(model_digest, "model_digest")
        validate_authority_digest(snapshot_digest, "snapshot_digest")
        validate_authority_version(policy_version, "policy_version")
        validate_authority_version(prompt_version, "prompt_version")
        validate_authority_version(model_version, "model_version")
        fence_results_json = _serialize_authority_fence_results(fence_results)
        # Controlled-input validation BEFORE any durable write: an invalid
        # snapshot retention/redaction class must fail loudly here, never be
        # turned by conflict handling into a phantom CAS loser.
        _validate_authority_class(
            snapshot_retention_class, "snapshot_retention_class", AuthorityRetentionClass
        )
        _validate_authority_class(
            snapshot_redaction_class, "snapshot_redaction_class", AuthorityRedactionClass
        )

        claim_key = _authority_claim_key(
            root_task_id,
            manager_session_id,
            causal_event_id,
            policy_digest,
            prompt_digest,
            model_digest,
        )
        candidate_id = f"AUTH-CAND-{claim_key}"
        now = datetime.now(timezone.utc).isoformat()
        try:
            cur = self._conn.execute(
                """INSERT INTO authority_candidates (
                   id, claim_key, root_task_id, team, manager_agent,
                   manager_session_id, causal_event_id, causal_event_digest,
                   causal_result_id, policy_id, policy_version, policy_digest,
                   prompt_id, prompt_version, prompt_digest,
                   model_id, model_version, model_digest,
                   snapshot_digest, snapshot_retention_class,
                   snapshot_redaction_class, fence_results_json,
                   disposition, lifecycle_state, consumed_at,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, NULL, 'created', NULL, ?, ?)""",
            (
                candidate_id,
                claim_key,
                root_task_id,
                team,
                manager_agent,
                manager_session_id,
                causal_event_id,
                causal_event_digest,
                causal_result_id,
                policy_id,
                policy_version,
                policy_digest,
                prompt_id,
                prompt_version,
                prompt_digest,
                model_id,
                model_version,
                model_digest,
                snapshot_digest,
                snapshot_retention_class,
                snapshot_redaction_class,
                fence_results_json,
                now,
                now,
            ),
        )
        except sqlite3.IntegrityError as exc:
            # Scope conflict-to-CAS-loss handling to the intended uniqueness
            # race ONLY. id (PRIMARY KEY) and claim_key (UNIQUE) are both
            # derived deterministically from the same claim tuple, so a
            # UNIQUE/PRIMARYKEY violation means the exact tuple was already
            # claimed — the documented loser result. Any other constraint
            # failure (CHECK, NOT NULL, FK) is a real defect and must raise;
            # it must never masquerade as won=False with a phantom loser id
            # and no durable row.
            if exc.sqlite_errorname in (
                "SQLITE_CONSTRAINT_UNIQUE",
                "SQLITE_CONSTRAINT_PRIMARYKEY",
            ):
                self._conn.rollback()
                # Prove the conflicting row IS the exact deterministic
                # immutable tuple before returning the loser result. A
                # raw-SQL or imported row can occupy the derived candidate id
                # under a different claim_key, or the claim_key under a
                # different id; neither is our tuple, and reporting either as
                # the winner would misattribute an unrelated durable row.
                # Query by BOTH relevant keys and validate the complete
                # immutable tuple (both derived keys plus the six claim-tuple
                # source fields; claim_key is their sha256, so equality is
                # the tuple proof). The exact tuple row occupies both keys,
                # so it is necessarily the only match when present. On any
                # absence or contradiction, fail closed with an integrity
                # failure instead of returning a loser.
                winner = self._conn.execute(
                    """SELECT id, claim_key, root_task_id, manager_session_id,
                              causal_event_id, policy_digest, prompt_digest,
                              model_digest
                       FROM authority_candidates
                       WHERE id = ? OR claim_key = ?""",
                    (candidate_id, claim_key),
                ).fetchone()
                if (
                    winner is not None
                    and winner["id"] == candidate_id
                    and winner["claim_key"] == claim_key
                    and winner["root_task_id"] == root_task_id
                    and winner["manager_session_id"] == manager_session_id
                    and winner["causal_event_id"] == causal_event_id
                    and winner["policy_digest"] == policy_digest
                    and winner["prompt_digest"] == prompt_digest
                    and winner["model_digest"] == model_digest
                ):
                    return candidate_id, False
                raise sqlite3.IntegrityError(
                    "authority CAS collision: conflicting row does not match "
                    "the deterministic immutable claim tuple"
                ) from exc
            raise
        if _commit:
            self._conn.commit()
        return candidate_id, cur.rowcount == 1

    @_synchronized
    def record_authority_evaluation(
        self,
        *,
        candidate_id: str,
        disposition: str,
        disposition_code: str,
        response_digest: str,
        response_retention_class: str = "digest_only",
        response_redaction_class: str = "redacted",
        fence_results: dict | None = None,
    ) -> int:
        """Atomically persist the single immutable evaluation for a candidate.

        Writes the evaluation row and transitions the candidate
        ``created -> evaluated`` (setting its mirrored disposition) in ONE
        transaction. On any failure the whole transaction rolls back — no
        evaluation row and no candidate transition survive.

        The DB is the single-evaluation guard: ``authority_evaluations.
        candidate_id`` carries a UNIQUE constraint, so a second evaluation
        for the same candidate (or a missing candidate, via the FK) raises
        ``sqlite3.IntegrityError`` and rolls back. Stores only the response
        *digest* and controlled disposition/code — never raw response text.
        """
        validate_authority_digest(response_digest, "response_digest")
        fence_results_json = _serialize_authority_fence_results(fence_results)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                """INSERT INTO authority_evaluations (
                       candidate_id, disposition, disposition_code,
                       response_digest, response_retention_class,
                       response_redaction_class, fence_results_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    disposition,
                    disposition_code,
                    response_digest,
                    response_retention_class,
                    response_redaction_class,
                    fence_results_json,
                    now,
                ),
            )
            self._conn.execute(
                """UPDATE authority_candidates
                   SET disposition = ?, lifecycle_state = 'evaluated', updated_at = ?
                   WHERE id = ?""",
                (disposition, now, candidate_id),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def record_authority_audit(
        self,
        *,
        candidate_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> int:
        """Append one immutable audit event. The table's BEFORE UPDATE/DELETE
        triggers make it append-only at the DB level, and candidate attribution
        is DB-enforced via a foreign key to ``authority_candidates``."""
        # API validation (in addition to the DB-level FK): closed event
        # vocabulary, closed payload, and an existing candidate.
        event_type_value = AuthorityAuditEventType(event_type).value
        payload_json = _serialize_authority_audit_payload(payload)
        exists = self._conn.execute(
            "SELECT 1 FROM authority_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(
                f"authority audit requires an existing candidate: {candidate_id!r}"
            )
        cur = self._conn.execute(
            """INSERT INTO authority_audit (candidate_id, event_type, payload_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                candidate_id,
                event_type_value,
                payload_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    @_synchronized
    def consume_authority_candidate(self, candidate_id: str) -> bool:
        """Exactly-once consumption CAS.

        Transitions ``evaluated -> consumed`` (setting ``consumed_at``) only
        if the candidate is currently evaluated. Returns True only for the
        first call; any later call — or a call on a candidate that was never
        evaluated (a partial record) — returns False, so no partial record
        becomes a future continuation and no extra consumption occurs.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """UPDATE authority_candidates
               SET lifecycle_state = 'consumed', consumed_at = ?, updated_at = ?
               WHERE id = ? AND lifecycle_state = 'evaluated'""",
            (now, now, candidate_id),
        )
        self._conn.commit()
        return cur.rowcount == 1

    @_synchronized
    def commit_authority_continue_same_root(
        self,
        *,
        task_id: str,
        candidate_id: str,
        expected_manager_agent: str,
        expected_session: str,
        expected_team: str,
        expected_policy_id: str,
        expected_policy_version: str,
        expected_policy_digest: str,
        expected_prompt_id: str,
        expected_prompt_version: str,
        expected_prompt_digest: str,
        expected_model_id: str,
        expected_model_version: str,
        expected_model_digest: str,
        expected_input_digest: str,
        expected_causal_event_id: str,
        expected_max_revise_rounds: int,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        note: str,
        audit_agent: str,
        authority_continue_payload: dict,
        hook_outcome_payload: dict,
        envelope_clause_id: str,
        envelope_action: str,
        envelope_causal_event_digest: str,
    ) -> bool:
        """THR-181 Track A: atomic same-root continuation CAS + full audit.

        Executes the authority hook's CONTINUE_SAME_ROOT permitted action in
        ONE transaction. Before the still-claimed root is returned to PENDING,
        the COMPLETE current fence set is atomically re-validated against
        live state — every category the hook used before/during evaluation
        (candidate/policy/input identity, manager ownership and session,
        exact team, root status, cancellation, block/active-work, revisit/
        successor lineage, revise budgets, zombie/
        partial-work evidence, adverse child verdicts). Any drift that landed
        while the evaluator ran — cancellation, session/manager/team change,
        block, active work, a successor/revisit signal, an exhausted
        budget, partial-work evidence, an adverse child verdict, or a
        candidate/policy/input mismatch — rolls the whole transaction back
        and returns False (no continuation).

        The dispatched thread id/origin remains provenance and is not a
        rollback fence.

        Only when every recheck passes are BOTH the
        ``authority_continued_same_root`` and ``authority_hook`` audit rows
        appended atomically with the continuation, so an audit failure can
        never permit continuation. The single-use continuation ENVELOPE
        (``authority_continue_envelopes``, state ``active``, bound 1:1 to the
        candidate and to the immutable causal task-result row, and carrying
        the matched policy clause + exact permitted action) is minted in the
        SAME transaction — the continuation window that the daemon restricts
        on the next turn exists only when this commit succeeds. Returns False
        (nothing written) when any gate fails.
        """
        now = datetime.now(timezone.utc).isoformat()
        block_sql = (
            "block_kind IS NULL"
            if expected_block_kind is None
            else "block_kind = ?"
        )
        block_args = () if expected_block_kind is None else (expected_block_kind.value,)
        self._conn.execute("BEGIN")
        try:
            # -- 1. Candidate identity recheck (immutable claim tuple) --
            cand = self._conn.execute(
                """SELECT id, root_task_id, manager_session_id, causal_event_id,
                          policy_id, policy_version, policy_digest,
                          prompt_id, prompt_version, prompt_digest,
                          model_id, model_version, model_digest,
                          snapshot_digest, lifecycle_state
                   FROM authority_candidates WHERE id = ?""",
                (candidate_id,),
            ).fetchone()
            if cand is None:
                self._conn.rollback()
                return False
            if not (
                cand["root_task_id"] == task_id
                and cand["manager_session_id"] == expected_session
                and cand["causal_event_id"] == expected_causal_event_id
                and cand["policy_id"] == expected_policy_id
                and cand["policy_version"] == expected_policy_version
                and cand["policy_digest"] == expected_policy_digest
                and cand["prompt_id"] == expected_prompt_id
                and cand["prompt_version"] == expected_prompt_version
                and cand["prompt_digest"] == expected_prompt_digest
                and cand["model_id"] == expected_model_id
                and cand["model_version"] == expected_model_version
                and cand["model_digest"] == expected_model_digest
                and cand["snapshot_digest"] == expected_input_digest
                and cand["lifecycle_state"] == "consumed"
            ):
                self._conn.rollback()
                return False

            # -- 2. Complete task fence recheck against live state --
            t = self._conn.execute(
                """SELECT status, block_kind, cancelled_at, assigned_agent,
                          current_session_id, team, revisit_of_task_id,
                          dispatched_from_thread_id, active_chain, active_fanout,
                          blocked_on_job_ids, orchestration_step_count,
                          revision_count, zombie_flagged_at
                   FROM tasks WHERE id = ?""",
                (task_id,),
            ).fetchone()
            if t is None:
                self._conn.rollback()
                return False
            terminal = t["status"] in _AUTHORITY_TERMINAL_STATUSES
            budget_ok = (
                expected_max_revise_rounds <= 0
                or t["revision_count"] < expected_max_revise_rounds
            )
            if not (
                t["assigned_agent"] == expected_manager_agent
                and t["current_session_id"] == expected_session
                and t["team"] == expected_team
                and t["status"] == expected_status.value
                and (t["block_kind"] is None if expected_block_kind is None else t["block_kind"] == expected_block_kind.value)
                and t["cancelled_at"] is None
                and not terminal
                and t["active_chain"] is None
                and t["active_fanout"] is None
                and t["blocked_on_job_ids"] is None
                and t["revisit_of_task_id"] is None
                and budget_ok
                and t["zombie_flagged_at"] is None
            ):
                self._conn.rollback()
                return False

            # -- 3. Successor lineage recheck --
            succ = self._conn.execute(
                "SELECT 1 FROM manager_supersessions WHERE successor_task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            if succ is not None:
                self._conn.rollback()
                return False

            # -- 4. Adverse child-verdict recheck (latest persisted verdict) --
            children = self._conn.execute(
                "SELECT id FROM tasks WHERE parent_task_id = ?", (task_id,),
            ).fetchall()
            for child in children:
                latest = self._conn.execute(
                    "SELECT verdict FROM task_results WHERE task_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (child["id"],),
                ).fetchone()
                verdict = latest["verdict"] if latest is not None else None
                if verdict is not None and verdict not in _AUTHORITY_APPROVED_VERDICTS:
                    self._conn.rollback()
                    return False

            # -- 5. Atomic continuation + audit rows --
            cur = self._conn.execute(
                f"""UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND {block_sql}
                     AND cancelled_at IS NULL""",
                (
                    TaskStatus.PENDING.value,
                    note,
                    now,
                    task_id,
                    expected_status.value,
                )
                + block_args,
            )
            if cur.rowcount != 1:
                self._conn.rollback()
                return False
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    task_id,
                    audit_agent,
                    "authority_continued_same_root",
                    json.dumps(authority_continue_payload),
                    now,
                ),
            )
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    task_id,
                    audit_agent,
                    "authority_hook",
                    json.dumps(hook_outcome_payload),
                    now,
                ),
            )
            # -- 6. Mint the single-use continuation envelope (atomic with the
            # continuation + audit rows). The envelope id is deterministic
            # from the candidate (1:1); its identity binds the immutable
            # causal task-result row, the evaluation (candidate), the matched
            # policy clause, and the exact permitted action. Any consumption
            # must recheck this identity (see
            # ``consume_authority_continue_envelope``).
            self._conn.execute(
                "INSERT INTO authority_continue_envelopes "
                "(id, candidate_id, root_task_id, team, manager_agent, "
                " manager_session_id, causal_event_id, causal_event_digest, "
                " policy_id, policy_version, policy_digest, clause_id, action, "
                " state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    f"CONT-{candidate_id}",
                    candidate_id,
                    task_id,
                    expected_team,
                    expected_manager_agent,
                    expected_session,
                    expected_causal_event_id,
                    envelope_causal_event_digest,
                    expected_policy_id,
                    expected_policy_version,
                    expected_policy_digest,
                    envelope_clause_id,
                    envelope_action,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    def get_active_authority_continue_envelope(self, root_task_id: str):
        """Return the single ACTIVE continuation envelope for ``root_task_id``
        (the continuation window that restricts the continued turn), or None.

        At most one envelope is active for a root at any time: an envelope is
        minted atomically with the continuation and spent exactly once by the
        continued turn's decision (or a failure/cancellation abort). A spent
        envelope never re-activates.
        """
        return self._conn.execute(
            "SELECT * FROM authority_continue_envelopes "
            "WHERE root_task_id = ? AND state = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (root_task_id,),
        ).fetchone()

    def get_authority_continue_envelope(self, envelope_id: str):
        """Return the envelope row by id (any state), or None."""
        return self._conn.execute(
            "SELECT * FROM authority_continue_envelopes WHERE id = ?",
            (envelope_id,),
        ).fetchone()

    @_synchronized
    def consume_authority_continue_envelope(
        self,
        *,
        envelope_id: str,
        root_task_id: str,
        decision_family: str,
        expected_manager_agent: str,
        expected_session_id: str,
        expected_causal_event_id: str,
        expected_causal_event_digest: str,
        expected_policy_id: str,
        expected_policy_version: str,
        expected_policy_digest: str,
        expected_clause_id: str,
        expected_action: str,
        audit_agent: str,
        error: str | None = None,
        violation: bool = False,
    ) -> str:
        """THR-181 Track A: consume the single-use continuation envelope
        EXACTLY ONCE, atomically rechecking the immutable identity.

        Returns ``"consumed"`` (state ``active -> consumed``; the continued
        turn produced a daemon-accepted manager decision), or
        ``"not_active"`` (the envelope was already spent or its identity
        drifted — never a second continuation).

        Inside ONE transaction the envelope's immutable identity (root,
        manager/team/session, causal task-result row, policy id/version/
        digest, matched clause, permitted action) is rechecked against the
        live row, and the root is rechecked to be still claimed
        (in_progress, not cancelled/terminal). The audit row for the
        consumption is written atomically with the transition, so a
        consumption can never be un-audited. Only bounded, non-secret-bearing
        fields are persisted (decision family + error code, never raw prose).
        """
        now = _now().isoformat()
        self._conn.execute("BEGIN")
        try:
            env = self._conn.execute(
                "SELECT * FROM authority_continue_envelopes WHERE id = ?",
                (envelope_id,),
            ).fetchone()
            if env is None or env["state"] != "active":
                self._conn.rollback()
                return "not_active"
            if not (
                env["root_task_id"] == root_task_id
                and env["manager_agent"] == expected_manager_agent
                and env["manager_session_id"] == expected_session_id
                and env["causal_event_id"] == expected_causal_event_id
                and env["causal_event_digest"] == expected_causal_event_digest
                and env["policy_id"] == expected_policy_id
                and env["policy_version"] == expected_policy_version
                and env["policy_digest"] == expected_policy_digest
                and env["clause_id"] == expected_clause_id
                and env["action"] == expected_action
            ):
                # Immutable identity drift: cannot be the continuation we
                # issued — fail closed (the envelope is never re-usable).
                self._conn.rollback()
                return "not_active"
            t = self._conn.execute(
                "SELECT status, cancelled_at "
                "FROM tasks WHERE id = ?",
                (root_task_id,),
            ).fetchone()
            if t is None:
                self._conn.rollback()
                return "not_active"
            if (
                t["status"] != "in_progress"
                or t["cancelled_at"] is not None
            ):
                # The root is no longer the claimed current turn:
                # cancellation/terminal/stale — spend fail-closed.
                self._conn.rollback()
                return "not_active"
            # The envelope is a single-use lifecycle/causality receipt, not
            # an action whitelist. Normal manager-decision validation and the
            # daemon's independent mechanical fences remain authoritative.
            new_state = "violated" if violation else "consumed"
            cur = self._conn.execute(
                "UPDATE authority_continue_envelopes "
                "SET state = ?, consumed_at = ?, updated_at = ? "
                "WHERE id = ? AND state = 'active'",
                (new_state, now, now, envelope_id),
            )
            if cur.rowcount != 1:
                self._conn.rollback()
                return "not_active"
            action_label = (
                "authority_continue_envelope_consumed"
                if new_state == "consumed"
                else "authority_continue_envelope_violated"
            )
            payload: dict = {
                "envelope_id": envelope_id,
                "candidate_id": env["candidate_id"],
                "root_task_id": root_task_id,
                "decision_family": decision_family[:200],
                "clause_id": env["clause_id"],
                "action": env["action"],
                "policy_id": env["policy_id"],
                "policy_version": env["policy_version"],
                "policy_digest": env["policy_digest"],
                "causal_event_id": env["causal_event_id"],
                "causal_event_digest": env["causal_event_digest"],
                "state": new_state,
            }
            if error is not None:
                payload["error"] = error[:500]
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (root_task_id, audit_agent, action_label, json.dumps(payload), now),
            )
            self._conn.commit()
            return new_state
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def spend_authority_continue_envelope_if_active(
        self, root_task_id: str, *, audit_agent: str, error: str,
    ) -> bool:
        """Spend any ACTIVE envelope for ``root_task_id`` as ``violated``
        (fail-closed abort — the continuation window closed without the
        permitted decision: session failure, cancellation, terminal). Returns
        True when an envelope was spent. Exactly-once: only the transition
        from ``active`` wins.
        """
        env = self.get_active_authority_continue_envelope(root_task_id)
        if env is None:
            return False
        now = _now().isoformat()
        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                "UPDATE authority_continue_envelopes "
                "SET state = 'violated', consumed_at = ?, updated_at = ? "
                "WHERE id = ? AND state = 'active'",
                (now, now, env["id"]),
            )
            if cur.rowcount != 1:
                self._conn.rollback()
                return False
            payload: dict = {
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
                "error": error[:500],
            }
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    root_task_id,
                    audit_agent,
                    "authority_continue_envelope_violated",
                    json.dumps(payload),
                    now,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    def _authority_candidate_from_row(self, row) -> AuthorityCandidate:
        return AuthorityCandidate(
            id=row["id"],
            claim_key=row["claim_key"],
            root_task_id=row["root_task_id"],
            team=row["team"],
            manager_agent=row["manager_agent"],
            manager_session_id=row["manager_session_id"],
            causal_event_id=row["causal_event_id"],
            causal_event_digest=row["causal_event_digest"],
            causal_result_id=row["causal_result_id"],
            policy_id=row["policy_id"],
            policy_version=row["policy_version"],
            policy_digest=row["policy_digest"],
            prompt_id=row["prompt_id"],
            prompt_version=row["prompt_version"],
            prompt_digest=row["prompt_digest"],
            model_id=row["model_id"],
            model_version=row["model_version"],
            model_digest=row["model_digest"],
            snapshot_digest=row["snapshot_digest"],
            snapshot_retention_class=row["snapshot_retention_class"],
            snapshot_redaction_class=row["snapshot_redaction_class"],
            fence_results=_parse_authority_fence_results(row["fence_results_json"]),
            disposition=row["disposition"],
            lifecycle_state=row["lifecycle_state"],
            consumed_at=row["consumed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @_synchronized
    def get_authority_candidate(self, candidate_id: str) -> AuthorityCandidate | None:
        row = self._conn.execute(
            "SELECT * FROM authority_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        return self._authority_candidate_from_row(row)

    @_synchronized
    def get_authority_candidate_by_claim(self, claim_key: str) -> AuthorityCandidate | None:
        row = self._conn.execute(
            "SELECT * FROM authority_candidates WHERE claim_key = ?", (claim_key,)
        ).fetchone()
        if row is None:
            return None
        return self._authority_candidate_from_row(row)

    @_synchronized
    def list_authority_candidates_for_root(self, root_task_id: str) -> list[AuthorityCandidate]:
        rows = self._conn.execute(
            "SELECT * FROM authority_candidates WHERE root_task_id = ? ORDER BY id",
            (root_task_id,),
        ).fetchall()
        return [self._authority_candidate_from_row(r) for r in rows]

    @_synchronized
    def get_authority_evaluation(self, candidate_id: str) -> AuthorityEvaluation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_evaluations WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        return AuthorityEvaluation(
            id=row["id"],
            candidate_id=row["candidate_id"],
            disposition=row["disposition"],
            disposition_code=row["disposition_code"],
            response_digest=row["response_digest"],
            response_retention_class=row["response_retention_class"],
            response_redaction_class=row["response_redaction_class"],
            fence_results=_parse_authority_fence_results(row["fence_results_json"]),
            created_at=row["created_at"],
        )

    @_synchronized
    def list_authority_audit(self, candidate_id: str) -> list[AuthorityAuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM authority_audit WHERE candidate_id = ? ORDER BY id",
            (candidate_id,),
        ).fetchall()
        return [
            AuthorityAuditEvent(
                id=r["id"],
                candidate_id=r["candidate_id"],
                event_type=r["event_type"],
                payload=(
                    AuthorityAuditPayload.model_validate(json.loads(r["payload_json"]))
                    if r["payload_json"]
                    else None
                ),
                created_at=r["created_at"],
            )
            for r in rows
        ]
