from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _synchronized,
)
from runtime.infrastructure.thread_mentions import (
    parse_mentions,
    resolve_wake_set,
    valid_mentions,
)
from runtime.models import (
    ReplyDeliveryProjection,
    ThreadAttachment,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadReplyArrival,
    ThreadReplyBreakerEpisode,
    ThreadReplyClaim,
    ThreadReplyDeliveryState,
    ThreadReplyRecoveryEntry,
    ThreadReplySettlement,
)
from runtime.reply_delivery import reply_failure_category


class ReplyDeliveryMixin:
    # ── GitHub #688 Phase 1 Slice A: reply delivery state store ──────────
    #
    # HANDOFF CONTRACT (Slice B):
    #   These primitives are intentionally UNHOOKED in Slice A. Slice B MUST
    #   call them atomically with its route/runner activation:
    #     * cutover_thread_reply_delivery_state(thread_id) — once per thread at
    #       activation (and again on reopen; it is idempotent) to seed/coalesce
    #       per-pair state from any legacy pending REPLY rows.
    #     * recover_reply_delivery_state() — at startup, before thread workers
    #       start, replacing the conversational REPLY portion of
    #       _sweep_on_startup's generic reaper (Branch 6). Enqueue the returned
    #       tokens AFTER commit. BOOTSTRAP and TASK_FOLLOWUP keep the generic
    #       reaper's daemon_restart semantics.
    #   The claim (queued → running CAS) and settlement primitives are Slice B;
    #   routes/runner must NOT open-code the queued/running token transitions.

    def _row_to_reply_delivery_state(self, row) -> ThreadReplyDeliveryState:
        return ThreadReplyDeliveryState(
            thread_id=row["thread_id"],
            agent_name=row["agent_name"],
            acknowledged_through_seq=int(row["acknowledged_through_seq"] or 0),
            required_through_seq=int(row["required_through_seq"] or 0),
            queued_invocation_token=row["queued_invocation_token"],
            running_invocation_token=row["running_invocation_token"],
            running_from_seq=row["running_from_seq"],
            running_through_seq=row["running_through_seq"],
            last_terminal_reason=row["last_terminal_reason"],
            last_terminal_at=row["last_terminal_at"],
            updated_at=row["updated_at"],
        )

    @_synchronized
    def get_reply_delivery_state(
        self, thread_id: str, agent_name: str,
    ) -> ThreadReplyDeliveryState | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        row = cursor.fetchone()
        return self._row_to_reply_delivery_state(row) if row else None

    def _row_to_reply_breaker_episode(self, row) -> ThreadReplyBreakerEpisode:
        return ThreadReplyBreakerEpisode(
            thread_id=row["thread_id"], agent_name=row["agent_name"],
            executor_key=row["executor_key"], episode_id=row["episode_id"],
            state=row["state"], consecutive_failures=row["consecutive_failures"],
            opened_at=row["opened_at"], cooldown_until=row["cooldown_until"],
            probe_lease_id=row["probe_lease_id"],
            last_failure_category=row["last_failure_category"],
            updated_at=row["updated_at"],
        )

    @_synchronized
    def get_thread_reply_breaker(
        self, thread_id: str, agent_name: str, executor_key: str,
    ) -> ThreadReplyBreakerEpisode | None:
        """Return the durable episode; absence means CLOSED."""
        row = self._conn.execute(
            "SELECT * FROM thread_reply_breaker_episodes WHERE thread_id = ? "
            "AND agent_name = ? AND executor_key = ?",
            (thread_id, agent_name, executor_key),
        ).fetchone()
        return self._row_to_reply_breaker_episode(row) if row else None

    @_synchronized
    def record_thread_reply_breaker_failure(
        self, *, thread_id: str, agent_name: str, executor_key: str,
        invocation_token: str, failure_category: str, threshold: int,
        cooldown_seconds: int, now: datetime | None = None,
    ) -> ThreadReplyBreakerEpisode:
        """Count one qualifying final launched-provider outcome exactly once."""
        if threshold < 1 or cooldown_seconds < 1:
            raise ValueError("breaker threshold and cooldown must be positive")
        at = (now or _now()).astimezone(timezone.utc)
        at_s = at.isoformat()
        transition_action: str | None = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing_receipt = self._conn.execute(
                "SELECT episode_id FROM thread_reply_breaker_receipts "
                "WHERE invocation_token = ?", (invocation_token,),
            ).fetchone()
            row = self._conn.execute(
                "SELECT * FROM thread_reply_breaker_episodes WHERE thread_id = ? "
                "AND agent_name = ? AND executor_key = ?",
                (thread_id, agent_name, executor_key),
            ).fetchone()
            if existing_receipt is not None:
                if row is None or row["episode_id"] != existing_receipt["episode_id"]:
                    raise RuntimeError("breaker receipt continuity mismatch")
                self._conn.commit()
                return self._row_to_reply_breaker_episode(row)
            if row is None or (
                row["state"] == "closed" and int(row["consecutive_failures"]) == 0
            ):
                episode_id = f"brep-{uuid.uuid4().hex}"
                failures = 1
                state = "open" if failures >= threshold else "closed"
                if state == "open":
                    transition_action = "thread_reply_breaker_opened"
                opened_at = at_s if state == "open" else None
                cooldown_until = (
                    (at + timedelta(seconds=cooldown_seconds)).isoformat()
                    if state == "open" else None
                )
                self._conn.execute(
                    "INSERT INTO thread_reply_breaker_episodes "
                    "(thread_id,agent_name,executor_key,episode_id,state,"
                    "consecutive_failures,opened_at,cooldown_until,probe_lease_id,"
                    "last_failure_category,updated_at) VALUES (?,?,?,?,?,?,?,?,NULL,?,?) "
                    "ON CONFLICT(thread_id,agent_name,executor_key) DO UPDATE SET "
                    "episode_id=excluded.episode_id,state=excluded.state,"
                    "consecutive_failures=excluded.consecutive_failures,"
                    "opened_at=excluded.opened_at,cooldown_until=excluded.cooldown_until,"
                    "probe_lease_id=NULL,last_failure_category=excluded.last_failure_category,"
                    "updated_at=excluded.updated_at",
                    (thread_id, agent_name, executor_key, episode_id, state,
                     failures, opened_at, cooldown_until, failure_category, at_s),
                )
            else:
                episode_id = row["episode_id"]
                failures = int(row["consecutive_failures"]) + 1
                state = "open" if failures >= threshold else row["state"]
                opened_at = row["opened_at"] or (at_s if state == "open" else None)
                cooldown_until = row["cooldown_until"]
                if state == "open" and row["state"] != "open":
                    cooldown_until = (at + timedelta(seconds=cooldown_seconds)).isoformat()
                    transition_action = "thread_reply_breaker_opened"
                if row["state"] == "probe":
                    state = "open"
                    transition_action = "thread_reply_breaker_reopened"
                    opened_at = at_s
                    cooldown_until = (at + timedelta(seconds=cooldown_seconds)).isoformat()
                self._conn.execute(
                    "UPDATE thread_reply_breaker_episodes SET state=?,"
                    "consecutive_failures=?,opened_at=?,cooldown_until=?,"
                    "probe_lease_id=NULL,last_failure_category=?,updated_at=? "
                    "WHERE episode_id=?",
                    (state, failures, opened_at, cooldown_until,
                     failure_category, at_s, episode_id),
                )
            self._conn.execute(
                "INSERT INTO thread_reply_breaker_receipts "
                "(invocation_token,episode_id,outcome,failure_category,recorded_at) "
                "VALUES (?,?,'failure',?,?)",
                (invocation_token, episode_id, failure_category, at_s),
            )
            if transition_action is not None:
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent=agent_name,
                    action=transition_action,
                    payload={
                        "episode_id": episode_id,
                        "failure_category": failure_category,
                        "consecutive_failures": failures,
                    },
                )
            result = self._conn.execute(
                "SELECT * FROM thread_reply_breaker_episodes WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            self._conn.commit()
            return self._row_to_reply_breaker_episode(result)
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def acquire_thread_reply_breaker_probe(
        self, *, thread_id: str, agent_name: str, executor_key: str,
        lease_id: str, now: datetime | None = None,
    ) -> ThreadReplyBreakerEpisode | None:
        """Acquire the single durable half-open lease when its cooldown is due."""
        at_s = (now or _now()).astimezone(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "UPDATE thread_reply_breaker_episodes SET state='probe',"
            "probe_lease_id=?,updated_at=? WHERE thread_id=? AND agent_name=? "
            "AND executor_key=? AND state='open' AND cooldown_until<=?",
            (lease_id, at_s, thread_id, agent_name, executor_key, at_s),
        )
        self._conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get_thread_reply_breaker(thread_id, agent_name, executor_key)

    @_synchronized
    def mint_due_thread_reply_breaker_probes(
        self, *, now: datetime | None = None,
        no_episode_executor_keys: dict[tuple[str, str], str] | None = None,
        cooldown_seconds: int = 900,
    ) -> list[ThreadReplyRecoveryEntry]:
        """Atomically lease and mint timer-driven probes that are actually runnable.

        Startup/gap recovery can leave a durable obligation with neither an
        owner slot nor a breaker episode.  Such a pair is admitted only as a
        cooldown-aged probe, using the scheduler-resolved current continuity
        key; it is never silently converted into an ordinary CLOSED launch.
        """
        if cooldown_seconds < 1:
            raise ValueError("breaker cooldown must be positive")
        at = (now or _now()).astimezone(timezone.utc)
        at_s = at.isoformat()
        results: list[ThreadReplyRecoveryEntry] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # A prior tick may have committed the durable probe lease/token but
            # failed or been cancelled before publishing to the process queue.
            # Re-emit every still-pending queued probe on every tick. The
            # process queue deduplicates publication and the invocation claim
            # CAS remains the exactly-once launch boundary.
            for queued in self._conn.execute(
                "SELECT b.thread_id,b.agent_name,d.queued_invocation_token "
                "FROM thread_reply_breaker_episodes b JOIN "
                "thread_reply_delivery_state d ON d.thread_id=b.thread_id AND "
                "d.agent_name=b.agent_name JOIN thread_invocations i ON "
                "i.invocation_token=d.queued_invocation_token "
                "WHERE b.state='probe' AND b.probe_lease_id IS NOT NULL AND "
                "d.queued_invocation_token IS NOT NULL AND i.status='pending' "
                "ORDER BY b.thread_id,b.agent_name"
            ).fetchall():
                results.append(ThreadReplyRecoveryEntry(
                    thread_id=queued["thread_id"],
                    agent_name=queued["agent_name"],
                    invocation_token=queued["queued_invocation_token"],
                    kind="breaker_probe",
                ))
            cutoff_s = (at - timedelta(seconds=cooldown_seconds)).isoformat()
            for candidate in self._conn.execute(
                "SELECT d.thread_id,d.agent_name,d.updated_at FROM "
                "thread_reply_delivery_state d JOIN threads t ON t.id=d.thread_id "
                "AND t.status='open' JOIN thread_participants p ON "
                "p.thread_id=d.thread_id AND p.agent_name=d.agent_name WHERE "
                "d.required_through_seq>d.acknowledged_through_seq AND "
                "d.queued_invocation_token IS NULL AND "
                "d.running_invocation_token IS NULL AND d.updated_at<=? AND "
                "NOT EXISTS (SELECT 1 FROM thread_reply_breaker_episodes b "
                "WHERE b.thread_id=d.thread_id AND b.agent_name=d.agent_name) "
                "ORDER BY d.updated_at,d.thread_id,d.agent_name",
                (cutoff_s,),
            ).fetchall():
                pair = (candidate["thread_id"], candidate["agent_name"])
                executor_key = (no_episode_executor_keys or {}).get(pair)
                if executor_key is None:
                    continue
                held = self._conn.execute(
                    "SELECT 1 FROM thread_reply_exchange e JOIN "
                    "thread_exchange_deferrals x ON x.thread_id=e.thread_id "
                    "AND x.exchange_id=e.exchange_id WHERE e.thread_id=? AND "
                    "e.state='open' AND x.agent_name=? AND x.state='held' LIMIT 1",
                    pair,
                ).fetchone()
                if held is not None:
                    continue
                episode_id = f"brep-{uuid.uuid4().hex}"
                self._conn.execute(
                    "INSERT INTO thread_reply_breaker_episodes "
                    "(thread_id,agent_name,executor_key,episode_id,state,"
                    "consecutive_failures,opened_at,cooldown_until,probe_lease_id,"
                    "last_failure_category,updated_at) VALUES "
                    "(?,?,?,?,'open',0,?,?,NULL,NULL,?)",
                    (candidate["thread_id"], candidate["agent_name"], executor_key,
                     episode_id, candidate["updated_at"], candidate["updated_at"], at_s),
                )
            rows = self._conn.execute(
                "SELECT b.*,d.acknowledged_through_seq,d.required_through_seq,"
                "d.queued_invocation_token,d.running_invocation_token "
                "FROM thread_reply_breaker_episodes b "
                "JOIN thread_reply_delivery_state d ON d.thread_id=b.thread_id "
                "AND d.agent_name=b.agent_name "
                "JOIN threads t ON t.id=b.thread_id AND t.status='open' "
                "JOIN thread_participants p ON p.thread_id=b.thread_id "
                "AND p.agent_name=b.agent_name "
                "WHERE b.state='open' AND b.cooldown_until<=? "
                "ORDER BY b.cooldown_until,b.thread_id,b.agent_name,b.executor_key",
                (at_s,),
            ).fetchall()
            for row in rows:
                if (row["queued_invocation_token"] is not None
                        or row["running_invocation_token"] is not None
                        or int(row["required_through_seq"] or 0)
                        <= int(row["acknowledged_through_seq"] or 0)):
                    continue
                held = self._conn.execute(
                    "SELECT 1 FROM thread_reply_exchange e JOIN "
                    "thread_exchange_deferrals d ON d.thread_id=e.thread_id "
                    "AND d.exchange_id=e.exchange_id WHERE e.thread_id=? "
                    "AND e.state='open' AND d.agent_name=? AND d.state='held' LIMIT 1",
                    (row["thread_id"], row["agent_name"]),
                ).fetchone()
                if held is not None:
                    continue
                lease_id = f"brlease-{uuid.uuid4().hex}"
                token = self._mint_reply_invocation_uncommitted(
                    row["thread_id"], row["agent_name"],
                    int(row["acknowledged_through_seq"] or 0) + 1,
                )
                changed = self._conn.execute(
                    "UPDATE thread_reply_breaker_episodes SET state='probe',"
                    "probe_lease_id=?,updated_at=? WHERE episode_id=? AND state='open'",
                    (lease_id, at_s, row["episode_id"]),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("breaker probe lease CAS lost inside transaction")
                self._conn.execute(
                    "UPDATE thread_reply_delivery_state SET queued_invocation_token=?,"
                    "updated_at=? WHERE thread_id=? AND agent_name=?",
                    (token, at_s, row["thread_id"], row["agent_name"]),
                )
                self.insert_audit_log_uncommitted(
                    task_id=row["thread_id"], agent=row["agent_name"],
                    action="thread_reply_breaker_probe_started",
                    payload={"episode_id": row["episode_id"], "category": row["last_failure_category"]},
                )
                results.append(ThreadReplyRecoveryEntry(
                    thread_id=row["thread_id"], agent_name=row["agent_name"],
                    invocation_token=token, kind="breaker_probe",
                ))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return results

    @_synchronized
    def close_thread_reply_breaker(
        self, *, thread_id: str, agent_name: str, executor_key: str,
        now: datetime | None = None,
    ) -> bool:
        """Close/rearm one continuity identity without deleting episode history."""
        at_s = (now or _now()).astimezone(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "UPDATE thread_reply_breaker_episodes SET state='closed',"
            "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
            "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
            "WHERE thread_id=? AND agent_name=? AND executor_key=?",
            (at_s, thread_id, agent_name, executor_key),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def close_thread_reply_breakers_except(
        self, *, thread_id: str, agent_name: str, executor_key: str,
        now: datetime | None = None,
    ) -> int:
        """Close old executor/model/config continuity before a fresh launch."""
        at_s = (now or _now()).astimezone(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            rows = self._conn.execute(
                "SELECT episode_id FROM thread_reply_breaker_episodes WHERE "
                "thread_id=? AND agent_name=? AND executor_key<>? "
                "AND state IN ('open','probe')",
                (thread_id, agent_name, executor_key),
            ).fetchall()
            for row in rows:
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent=agent_name,
                    action="thread_reply_breaker_closed",
                    payload={"episode_id": row["episode_id"], "reason": "continuity_switch"},
                )
            cursor = self._conn.execute(
                "UPDATE thread_reply_breaker_episodes SET state='closed',"
                "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
                "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
                "WHERE thread_id=? AND agent_name=? AND executor_key<>? "
                "AND state IN ('open','probe')",
                (at_s, thread_id, agent_name, executor_key),
            )
            self._conn.commit()
            return cursor.rowcount
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def settle_thread_reply_breaker_success(
        self, *, thread_id: str, agent_name: str, executor_key: str,
        invocation_token: str, episode_id: str, probe_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Idempotently settle success without allowing stale episode closure.

        Probe completions additionally bind to the unique durable lease.  A
        callback from an older episode or lease is a truthful no-op.
        """
        at_s = (now or _now()).astimezone(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            receipt = self._conn.execute(
                "SELECT episode_id,outcome FROM thread_reply_breaker_receipts "
                "WHERE invocation_token=?", (invocation_token,),
            ).fetchone()
            if receipt is not None:
                self._conn.commit()
                return receipt["episode_id"] == episode_id and receipt["outcome"] == "success"
            row = self._conn.execute(
                "SELECT state,probe_lease_id FROM thread_reply_breaker_episodes "
                "WHERE thread_id=? AND agent_name=? AND executor_key=? AND episode_id=?",
                (thread_id, agent_name, executor_key, episode_id),
            ).fetchone()
            if row is None or (
                probe_lease_id is not None
                and (row["state"] != "probe" or row["probe_lease_id"] != probe_lease_id)
            ):
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO thread_reply_breaker_receipts "
                "(invocation_token,episode_id,outcome,failure_category,recorded_at) "
                "VALUES (?,?,'success',NULL,?)",
                (invocation_token, episode_id, at_s),
            )
            self._conn.execute(
                "UPDATE thread_reply_breaker_episodes SET state='closed',"
                "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
                "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
                "WHERE episode_id=?",
                (at_s, episode_id),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def list_reply_delivery_states(self) -> list[ThreadReplyDeliveryState]:
        """Every per-pair reply delivery state row (diagnostic surface)."""
        cursor = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state "
            "ORDER BY thread_id, agent_name",
        )
        return [self._row_to_reply_delivery_state(r) for r in cursor.fetchall()]


    def _mint_reply_invocation_uncommitted(
        self, thread_id: str, agent_name: str, triggering_seq: int,
    ) -> str:
        """INSERT a pending REPLY invocation row inside the open transaction.

        Mirrors ``mint_thread_invocation`` without committing so the caller can
        bundle the mint with the state transition in one atomic transaction.
        Returns the generated invocation token.
        """
        import uuid as _uuid
        token = _uuid.uuid4().hex
        now = _now().isoformat()
        self._conn.execute(
            "INSERT INTO thread_invocations (thread_id, agent_name, "
            "invocation_token, triggering_seq, purpose, status, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (thread_id, agent_name, token, triggering_seq,
             ThreadInvocationPurpose.REPLY.value, now),
        )
        return token

    @_synchronized
    def cutover_thread_reply_delivery_state(
        self, thread_id: str,
    ) -> list[ThreadReplyDeliveryState]:
        """Idempotently initialize per-pair reply delivery state for a thread.

        Callable explicitly by Slice B (per thread at activation / on reopen);
        Slice A does NOT auto-run it. For every CURRENT participant pair that
        has no state row yet:

          * no legacy pending REPLY → seed acknowledged_through_seq and
            required_through_seq to the thread tail (no queued/running token):
            Phase 1 starts at cutover and creates no historic work.
          * legacy pending REPLY(s) → derive ``from_seq`` = MIN(triggering_seq)
            across that pair's pending REPLYs; terminalize exactly those rows
            (status='failed', decline_reason='coalesced_cutover'); mint exactly
            one replacement pending REPLY and record it as queued, covering
            ``from_seq`` .. current tail (acknowledged = from_seq - 1,
            required = tail).

        Idempotent: a pair that already has a state row is left untouched, so
        repeat invocation/reopen never duplicates a queued wake or
        re-terminalizes rows. Only REPLY rows are touched — TASK_FOLLOWUP and
        BOOTSTRAP are never terminalized or minted here, and
        ``last_resumed_seq`` is never read.
        """
        now = _now().isoformat()
        created: list[ThreadReplyDeliveryState] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # Every cutoff-defining read (tail, participants, state existence,
            # legacy pending REPLY selection) runs AFTER the write lock is held
            # so a concurrent append cannot commit between the snapshot and the
            # state commit (a torn snapshot would drop a message from the
            # required range).
            tail = self._thread_tail_seq(thread_id)
            participants = [
                p.agent_name for p in self.list_thread_participants(thread_id)
            ]
            for agent_name in participants:
                existing = self._conn.execute(
                    "SELECT 1 FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (thread_id, agent_name),
                ).fetchone()
                if existing is not None:
                    continue  # already cut over — idempotent no-op

                # Legacy pending REPLYs for this pair (never BOOTSTRAP /
                # TASK_FOLLOWUP).
                legacy = self._conn.execute(
                    "SELECT MIN(triggering_seq) AS from_seq "
                    "FROM thread_invocations "
                    "WHERE thread_id = ? AND agent_name = ? "
                    "AND status = 'pending' AND purpose = 'reply'",
                    (thread_id, agent_name),
                ).fetchone()
                from_seq = legacy["from_seq"]
                if from_seq is not None:
                    # Terminalize exactly those legacy pending REPLYs with an
                    # explicit coalesced_cutover receipt.
                    self._conn.execute(
                        "UPDATE thread_invocations SET status = 'failed', "
                        "decline_reason = 'coalesced_cutover', consumed_at = ? "
                        "WHERE thread_id = ? AND agent_name = ? "
                        "AND status = 'pending' AND purpose = 'reply'",
                        (now, thread_id, agent_name),
                    )
                    # Mint exactly one replacement queued REPLY covering
                    # from_seq .. tail.
                    token = self._mint_reply_invocation_uncommitted(
                        thread_id, agent_name, from_seq,
                    )
                    self._conn.execute(
                        "INSERT INTO thread_reply_delivery_state "
                        "(thread_id, agent_name, acknowledged_through_seq, "
                        "required_through_seq, queued_invocation_token, "
                        "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (thread_id, agent_name, from_seq - 1, tail, token, now),
                    )
                else:
                    # No legacy pending REPLY: seed to tail, nothing queued.
                    self._conn.execute(
                        "INSERT INTO thread_reply_delivery_state "
                        "(thread_id, agent_name, acknowledged_through_seq, "
                        "required_through_seq, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (thread_id, agent_name, tail, tail, now),
                    )
                row = self._conn.execute(
                    "SELECT * FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (thread_id, agent_name),
                ).fetchone()
                created.append(self._row_to_reply_delivery_state(row))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return created

    def _running_recovery_fail_reason(
        self, inv, *, same_pair: bool, right_purpose: bool,
        pending: bool, started: bool, range_ok: bool,
    ) -> str:
        """Truthful fail-closed diagnostic for a non-recoverable running slot.

        Ordered so the most specific cause wins while keeping the distinct
        terminal vs malformed vs ownership causes that Slice B's retry
        projection and audit settlement depend on.
        """
        if inv is None or not same_pair or not right_purpose:
            return "invalid_running_token_on_recovery"
        if not pending:
            return "running_already_terminal_on_recovery"
        if not range_ok:
            return "malformed_running_range_on_recovery"
        if not started:
            return "running_missing_start_evidence_on_recovery"
        return "invalid_running_token_on_recovery"

    @_synchronized
    def recover_reply_delivery_state(self) -> list[ThreadReplyRecoveryEntry]:
        """Durable reply-delivery recovery (Slice A ships it UNHOOKED).

        Slice B must call this at startup (before thread workers start) and
        enqueue the returned tokens after commit, replacing the conversational
        REPLY portion of the generic reaper. Contract per state row:

          * queued token set, running clear → validate it is a pending
            UNSTARTED same-pair REPLY (the claim CAS enforces the same
            precondition). Valid → retain and return it. A queued receipt
            with started_at set (malformed/crash-window state) fails closed
            with a PAIR-SCOPED sweep: retire every owned pending REPLY
            receipt, clear the queued slot, preserve required_through_seq,
            never mint/return a replacement — the next conversational arrival
            mints the single covering wake. Any other invalid queued token →
            fail closed: clear the queued slot, record a diagnostic, return
            nothing.
          * both ownership slots populated → corruption. Fail closed: clear
            both slots, record a diagnostic, return nothing, never mint.
          * running token set → recoverable ONLY when the receipt is owned by
            this pair, is a REPLY, is still PENDING (the expected interrupted
            in-flight status), carries started evidence, and its durable range
            is internally consistent (acknowledged <= running_from <=
            running_through <= required). Recoverable → terminalize ONLY that
            owned attempt as daemon_restart, preserve the unacknowledged
            required range, clear running, mint/record exactly one replacement
            queued REPLY. Otherwise (consumed/failed/declined terminal,
            missing, wrong-pair, wrong-purpose, malformed range, missing start)
            → fail closed: clear the running slot, record a truthful
            diagnostic, never mint/return a runnable token.

        Pending released-deferral catch-up markers (``catchup_pending = 1``)
        are resolved by the post-loop reconciliation in the same transaction —
        consumed by a covering slot, minted exactly once when the blocking slot
        is terminal with no covering replacement, or cleared when fully
        covered. Every FAIL-CLOSED branch above (both-slots corruption,
        non-recoverable running slot, invalid queued started/ownership)
        revokes the pair's pending marker ATOMICALLY in that same transaction,
        so the reconciliation never mints a catch-up from state the branch
        declared untrustworthy (TASK-6065): the marker survives only when the
        blocking slot's terminal outcome was legitimately observed (settlement
        or a recoverable restart terminalization).

        Repeat recovery is idempotent: after a running row is replaced its slot
        holds a queued token, so a second pass retains rather than re-mints.
        BOOTSTRAP / TASK_FOLLOWUP rows are never touched.
        """
        now = _now().isoformat()
        results: list[ThreadReplyRecoveryEntry] = []
        rows = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state "
            "WHERE queued_invocation_token IS NOT NULL "
            "OR running_invocation_token IS NOT NULL "
            "ORDER BY thread_id, agent_name",
        ).fetchall()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                thread_id = row["thread_id"]
                agent_name = row["agent_name"]
                running_token = row["running_invocation_token"]
                queued_token = row["queued_invocation_token"]

                if running_token is not None and queued_token is not None:
                    # Both ownership slots populated: the mutually-exclusive
                    # claim/settle invariant was violated. Fail closed with a
                    # transactionally atomic PAIR-SCOPED sweep: retire EVERY
                    # invocation owned by this corrupt row's (thread_id,
                    # agent_name) pair that is purpose REPLY and status PENDING
                    # — including unreferenced same-pair pending receipts the
                    # slots never pointed at — so no duplicate/orphaned pending
                    # REPLY survives once Slice B replaces generic reaping. The
                    # sweep is gated on the pair, purpose='reply', and
                    # status='pending', so a foreign-pair, wrong-purpose,
                    # missing, or already-terminal receipt (even one referenced
                    # by a corrupt slot) is never mutated. No blanket global
                    # reaper is issued. Then clear both slots, record a
                    # truthful corruption diagnostic, and never mint or return
                    # a runnable replacement.
                    swept = self._conn.execute(
                        "UPDATE thread_invocations SET status = 'failed', "
                        "decline_reason = 'corrupt_both_slots_on_recovery', "
                        "consumed_at = ? "
                        "WHERE thread_id = ? AND agent_name = ? "
                        "AND status = 'pending' AND purpose = 'reply'",
                        (now, thread_id, agent_name),
                    )
                    self._conn.execute(
                        "UPDATE thread_reply_delivery_state SET "
                        "queued_invocation_token = NULL, "
                        "running_invocation_token = NULL, "
                        "running_from_seq = NULL, running_through_seq = NULL, "
                        "last_terminal_reason = ?, last_terminal_at = ?, "
                        "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                        ("corrupt_both_slots_on_recovery", now, now,
                         thread_id, agent_name),
                    )
                    # TASK-6065: revoke any pending released-deferral catch-up
                    # marker ATOMICALLY in the same transaction. The marker is
                    # a promise to mint exactly one post-slot catch-up; this
                    # fail-closed branch declared the pair's ownership state
                    # untrustworthy and explicitly never mints, so the marker
                    # must not survive into the post-loop reconciliation where
                    # it would fabricate runnable ownership from corrupt state
                    # (TASK-6063 reproduction). The revocation is audited with
                    # the corruption diagnostic so the suppression is truthful.
                    suppressed = self._clear_catchup_pending_uncommitted(
                        thread_id, agent_name,
                    )
                    # One truthful cancelled audit per corrupted pair — the
                    # pair-scoped sweep retired its owned obligations with a
                    # diagnostic reason; never mint a replacement.
                    self._emit_reply_wake_audit(
                        thread_id=thread_id, agent_name=agent_name,
                        action="thread_reply_wake_cancelled",
                        payload={
                            "agent_name": agent_name,
                            "boundary_seq": int(row["required_through_seq"] or 0),
                            "reason": "corrupt_both_slots_on_recovery",
                            "swept_count": swept.rowcount,
                            "catchup_suppressed": suppressed > 0,
                        },
                    )
                    continue

                if running_token is not None:
                    inv = self._conn.execute(
                        "SELECT * FROM thread_invocations "
                        "WHERE invocation_token = ?",
                        (running_token,),
                    ).fetchone()
                    same_pair = (
                        inv is not None
                        and inv["thread_id"] == thread_id
                        and inv["agent_name"] == agent_name
                    )
                    right_purpose = (
                        inv is not None
                        and inv["purpose"] == ThreadInvocationPurpose.REPLY.value
                    )
                    pending = (
                        inv is not None
                        and inv["status"] == ThreadInvocationStatus.PENDING.value
                    )
                    started = inv is not None and inv["started_at"] is not None

                    acknowledged = int(row["acknowledged_through_seq"] or 0)
                    required = int(row["required_through_seq"] or 0)
                    running_from = row["running_from_seq"]
                    running_through = row["running_through_seq"]
                    range_ok = (
                        running_from is not None
                        and running_through is not None
                        and acknowledged <= running_from
                        and running_from <= running_through
                        and running_through <= required
                    )

                    recoverable = (
                        same_pair and right_purpose and pending
                        and started and range_ok
                    )

                    if not recoverable:
                        reason = self._running_recovery_fail_reason(
                            inv, same_pair=same_pair,
                            right_purpose=right_purpose, pending=pending,
                            started=started, range_ok=range_ok,
                        )
                        # Fail closed: clear the running slot (never leave an
                        # ownership slot referencing a terminal/mismatched/
                        # malformed attempt), never mint a replacement, never
                        # return a runnable token. The invocation row itself is
                        # left untouched so truthful terminal diagnostics survive.
                        self._conn.execute(
                            "UPDATE thread_reply_delivery_state SET "
                            "running_invocation_token = NULL, "
                            "running_from_seq = NULL, "
                            "running_through_seq = NULL, "
                            "last_terminal_reason = ?, last_terminal_at = ?, "
                            "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                            (reason, now, now, thread_id, agent_name),
                        )
                        # TASK-6065: the non-recoverable running slot is an
                        # invalid/malformed ownership classification — the
                        # state is untrustworthy, so any pending released-
                        # deferral catch-up marker is revoked in the same
                        # transaction (never minted by the post-loop scan).
                        self._clear_catchup_pending_uncommitted(
                            thread_id, agent_name,
                        )
                        continue

                    # Recoverable interrupted in-flight attempt: terminalize
                    # ONLY the owned pending receipt as daemon_restart, preserve
                    # the unacknowledged required range, clear running,
                    # mint/record exactly one replacement queued REPLY.
                    self._conn.execute(
                        "UPDATE thread_invocations SET status = 'failed', "
                        "decline_reason = 'daemon_restart', consumed_at = ? "
                        "WHERE invocation_token = ? AND status = 'pending'",
                        (now, running_token),
                    )
                    replacement = self._mint_reply_invocation_uncommitted(
                        thread_id, agent_name, acknowledged + 1,
                    )
                    self._conn.execute(
                        "UPDATE thread_reply_delivery_state SET "
                        "running_invocation_token = NULL, "
                        "running_from_seq = NULL, "
                        "running_through_seq = NULL, "
                        "queued_invocation_token = ?, "
                        "last_terminal_reason = 'daemon_restart', "
                        "last_terminal_at = ?, updated_at = ? "
                        "WHERE thread_id = ? AND agent_name = ?",
                        (replacement, now, now, thread_id, agent_name),
                    )
                    self._emit_reply_wake_audit(
                        thread_id=thread_id, agent_name=agent_name,
                        action="thread_reply_wake_recovered",
                        payload={
                            "agent_name": agent_name,
                            "kind": "replacement_queued",
                            "from_seq": acknowledged + 1,
                            "through_seq": required,
                            "token_prefix": replacement[:8],
                        },
                    )
                    results.append(ThreadReplyRecoveryEntry(
                        thread_id=thread_id,
                        agent_name=agent_name,
                        invocation_token=replacement,
                        kind="replacement_queued",
                    ))
                    continue

                if queued_token is not None:
                    inv = self._conn.execute(
                        "SELECT * FROM thread_invocations "
                        "WHERE invocation_token = ?",
                        (queued_token,),
                    ).fetchone()
                    same_pair = (
                        inv is not None
                        and inv["thread_id"] == thread_id
                        and inv["agent_name"] == agent_name
                    )
                    right_purpose = (
                        inv is not None
                        and inv["purpose"] == ThreadInvocationPurpose.REPLY.value
                    )
                    pending = (
                        inv is not None
                        and inv["status"] == ThreadInvocationStatus.PENDING.value
                    )
                    started = inv is not None and inv["started_at"] is not None
                    # A valid queued wake is a same-pair pending REPLY whose
                    # receipt is UNSTARTED — claim_conversational_reply
                    # enforces the identical precondition. started_at on a
                    # queued receipt is malformed/crash-window state: the
                    # worker claim would no-op and the pair would strand
                    # forever, with later arrivals only coalescing into it.
                    valid_queued = (
                        inv is not None
                        and same_pair and right_purpose and pending
                        and not started
                    )
                    if valid_queued:
                        self._emit_reply_wake_audit(
                            thread_id=thread_id, agent_name=agent_name,
                            action="thread_reply_wake_recovered",
                            payload={
                                "agent_name": agent_name,
                                "kind": "retained_queued",
                                "from_seq": (
                                    int(row["acknowledged_through_seq"] or 0) + 1
                                ),
                                "through_seq": int(row["required_through_seq"] or 0),
                                "token_prefix": queued_token[:8],
                            },
                        )
                        results.append(ThreadReplyRecoveryEntry(
                            thread_id=thread_id,
                            agent_name=agent_name,
                            invocation_token=queued_token,
                            kind="retained_queued",
                        ))
                    elif same_pair and right_purpose and pending and started:
                        # Queued slot references a started receipt: invalid
                        # queued ownership. Fail closed with a transactionally
                        # atomic PAIR-SCOPED sweep (same class as the
                        # both-slots corruption branch): retire EVERY owned
                        # pending REPLY receipt for this pair — including
                        # unreferenced orphans no slot points at — so no
                        # pending REPLY survives that no claim can ever run,
                        # then clear the queued slot. Never mint or return a
                        # runnable replacement (no unowned provider run).
                        # required_through_seq is preserved, so the next
                        # conversational arrival mints a fresh wake covering
                        # the retained range (no swallowed arrival).
                        swept = self._conn.execute(
                            "UPDATE thread_invocations SET status = 'failed', "
                            "decline_reason = 'invalid_queued_started_on_recovery', "
                            "consumed_at = ? "
                            "WHERE thread_id = ? AND agent_name = ? "
                            "AND status = 'pending' AND purpose = 'reply'",
                            (now, thread_id, agent_name),
                        )
                        self._conn.execute(
                            "UPDATE thread_reply_delivery_state SET "
                            "queued_invocation_token = NULL, "
                            "last_terminal_reason = ?, last_terminal_at = ?, "
                            "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                            ("invalid_queued_started_on_recovery", now, now,
                             thread_id, agent_name),
                        )
                        # TASK-6065: invalid queued ownership is a fail-closed
                        # classification — revoke any pending released-deferral
                        # catch-up marker in the same transaction so the
                        # post-loop reconciliation can never mint from it.
                        suppressed = self._clear_catchup_pending_uncommitted(
                            thread_id, agent_name,
                        )
                        self._emit_reply_wake_audit(
                            thread_id=thread_id, agent_name=agent_name,
                            action="thread_reply_wake_cancelled",
                            payload={
                                "agent_name": agent_name,
                                "boundary_seq": int(row["required_through_seq"] or 0),
                                "reason": "invalid_queued_started_on_recovery",
                                "swept_count": swept.rowcount,
                                "catchup_suppressed": suppressed > 0,
                            },
                        )
                    else:
                        # Fail closed: clear the queued slot, return nothing.
                        self._conn.execute(
                            "UPDATE thread_reply_delivery_state SET "
                            "queued_invocation_token = NULL, "
                            "last_terminal_reason = ?, last_terminal_at = ?, "
                            "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                            ("invalid_queued_token_on_recovery", now, now,
                             thread_id, agent_name),
                        )
                        # TASK-6065: invalid queued ownership is a fail-closed
                        # classification — revoke any pending released-deferral
                        # catch-up marker in the same transaction so the
                        # post-loop reconciliation can never mint from it.
                        self._clear_catchup_pending_uncommitted(
                            thread_id, agent_name,
                        )

            # TASK-6057 — released-deferral post-slot catch-up reconciliation at
            # the restart boundary. A closure that found a live but NON-covering
            # slot (its immutable range ended before the released range) durably
            # marked the pair's deferral row ``catchup_pending``; the branches
            # above terminalized exactly the blocking slot (replacement minted /
            # retained / cleared). This pass resolves every still-pending marker
            # in the same transaction so the owed wake survives daemon restart:
            #   * a covering slot exists (replacement/retained queued wake) → the
            #     marker is satisfied, consume it;
            #   * no slot and ``acknowledged < required`` → the blocking slot is
            #     terminal with no covering replacement: mint exactly ONE
            #     range-covering catch-up now and consume the marker
            #     (exactly-once in this transaction);
            #   * ``acknowledged == required`` (fully covered) → nothing owed,
            #     consume the marker.
            # TASK-6065 — the FAIL-CLOSED branches above (both-slots corruption,
            # non-recoverable running slot, invalid queued started/ownership)
            # each revoked the pair's pending marker ATOMICALLY in the same
            # transaction, so the mint rule below can never fabricate runnable
            # ownership from state those branches declared untrustworthy: an
            # authoritative fail-closed terminal classification always survives
            # into this reconciliation as a revoked marker, never as a mint.
            for d in self._conn.execute(
                "SELECT DISTINCT thread_id, agent_name "
                "FROM thread_exchange_deferrals WHERE catchup_pending = 1",
            ).fetchall():
                thread_id = d["thread_id"]
                agent_name = d["agent_name"]
                srow = self._conn.execute(
                    "SELECT * FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (thread_id, agent_name),
                ).fetchone()
                if (
                    srow is None
                    or srow["queued_invocation_token"]
                    or srow["running_invocation_token"]
                ):
                    self._clear_catchup_pending_uncommitted(thread_id, agent_name)
                    continue
                ack = int(srow["acknowledged_through_seq"] or 0)
                req = int(srow["required_through_seq"] or 0)
                if ack >= req:
                    self._clear_catchup_pending_uncommitted(thread_id, agent_name)
                    continue
                token = self._mint_reply_invocation_uncommitted(
                    thread_id, agent_name, ack + 1,
                )
                self._conn.execute(
                    "UPDATE thread_reply_delivery_state SET "
                    "queued_invocation_token = ?, updated_at = ? "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (token, now, thread_id, agent_name),
                )
                self._clear_catchup_pending_uncommitted(thread_id, agent_name)
                self._emit_reply_wake_audit(
                    thread_id=thread_id, agent_name=agent_name,
                    action="thread_reply_wake_created",
                    payload={
                        "agent_name": agent_name,
                        "from_seq": ack + 1,
                        "through_seq": req,
                        "token_prefix": token[:8],
                        "kind": "deferred_catch_up",
                    },
                )
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent=agent_name,
                    action="thread_deferral_catchup_minted",
                    payload={
                        "thread_id": thread_id,
                        "agent_name": agent_name,
                        "from_seq": ack + 1,
                        "through_seq": req,
                        "mint_token_prefix": token[:8],
                        "kind": "recovery",
                    },
                )
                results.append(ThreadReplyRecoveryEntry(
                    thread_id=thread_id,
                    agent_name=agent_name,
                    invocation_token=token,
                    kind="deferred_catchup",
                ))

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return results

    # ── GitHub #688 Phase 1 Slice B: reply delivery state wiring ────────────
    #
    # Slice A shipped the table + cutover/recovery primitives UNHOOKED. Slice B
    # owns the route/runner activation and the atomic conversational-arrival,
    # claim, and settlement operations. The store is the single authority for
    # the queued/running token transitions; routes/runner MUST NOT open-code
    # them. All state-changing methods run one explicit SQLite transaction
    # (BEGIN IMMEDIATE) so the append+arrival / append+settle+broadcast units
    # are atomic and queue notifications happen strictly after commit.


    def _apply_arrival_uncommitted(
        self, thread_id: str, agent_name: str, seq: int,
    ) -> ThreadReplyArrival:
        """Raise ``required_through_seq`` to ``seq`` for one recipient pair,
        minting exactly one queued REPLY only when neither queued nor running
        ownership exists. Runs inside an open transaction (no commit).

        ``seq`` must be the just-appended conversational message sequence;
        the speaker is excluded by the caller. A missing pair row is created
        with acknowledged = seq - 1 (no historic replay) so Phase 1 delivery
        for a newly-seen pair starts at this message.
        """
        now = _now().isoformat()
        row = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        ).fetchone()
        if row is None:
            # New pair: seed acknowledged = seq - 1 (tail before this message),
            # required = seq, and mint one queued wake covering seq..seq.
            token = self._mint_reply_invocation_uncommitted(
                thread_id, agent_name, seq,
            )
            self._conn.execute(
                "INSERT INTO thread_reply_delivery_state "
                "(thread_id, agent_name, acknowledged_through_seq, "
                "required_through_seq, queued_invocation_token, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, agent_name, seq - 1, seq, token, now),
            )
            self._emit_reply_wake_audit(
                thread_id=thread_id, agent_name=agent_name,
                action="thread_reply_wake_created",
                payload={
                    "agent_name": agent_name,
                    "from_seq": seq,
                    "through_seq": seq,
                    "token_prefix": token[:8],
                },
            )
            return ThreadReplyArrival(
                agent_name=agent_name, invocation_token=token,
                coalesced=False, from_seq=seq, through_seq=seq,
            )

        acknowledged = int(row["acknowledged_through_seq"] or 0)
        required = int(row["required_through_seq"] or 0)
        queued = row["queued_invocation_token"]
        running = row["running_invocation_token"]
        if seq <= required:
            # Idempotent safety: already covered by the required watermark.
            # No durable change — deliberately NO audit (a duplicate/backdated
            # notification must not fabricate a coalesced event).
            return ThreadReplyArrival(
                agent_name=agent_name, invocation_token=None,
                coalesced=True, from_seq=acknowledged + 1, through_seq=required,
            )

        new_required = seq
        if queued is not None or running is not None:
            # Coalesce: raise required only; the existing wake already owns the
            # delivery obligation.
            self._conn.execute(
                "UPDATE thread_reply_delivery_state SET required_through_seq = ?, "
                "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                (new_required, now, thread_id, agent_name),
            )
            self._emit_reply_wake_audit(
                thread_id=thread_id, agent_name=agent_name,
                action="thread_reply_wake_coalesced",
                payload={
                    "agent_name": agent_name,
                    "from_seq": acknowledged + 1,
                    "through_seq": new_required,
                },
            )
            return ThreadReplyArrival(
                agent_name=agent_name, invocation_token=None,
                coalesced=True,
                from_seq=acknowledged + 1, through_seq=new_required,
            )

        # No queued/running ownership: mint one queued wake covering
        # acknowledged+1 .. seq.
        from_seq = acknowledged + 1
        token = self._mint_reply_invocation_uncommitted(
            thread_id, agent_name, from_seq,
        )
        self._conn.execute(
            "UPDATE thread_reply_delivery_state SET required_through_seq = ?, "
            "queued_invocation_token = ?, updated_at = ? "
            "WHERE thread_id = ? AND agent_name = ?",
            (new_required, token, now, thread_id, agent_name),
        )
        self._emit_reply_wake_audit(
            thread_id=thread_id, agent_name=agent_name,
            action="thread_reply_wake_created",
            payload={
                "agent_name": agent_name,
                "from_seq": from_seq,
                "through_seq": new_required,
                "token_prefix": token[:8],
            },
        )
        return ThreadReplyArrival(
            agent_name=agent_name, invocation_token=token,
            coalesced=False, from_seq=from_seq, through_seq=new_required,
        )

    def _derive_conversational_mentions(
        self,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None,
    ) -> list[str] | None:
        """Server-side derivation of the durable mention signal for a
        conversational write (THR-198 Slice A). Only kind=MESSAGE rows carry
        the signal; system/decline rows stay NULL. The stored value is the
        canonical valid set: live participants at write time, excluding the
        speaker, deduped in first-occurrence order — derived from
        ``body_markdown``, never client-declared.
        """
        if kind is not ThreadMessageKind.MESSAGE:
            return None
        participants = [
            r["agent_name"] for r in self._conn.execute(
                "SELECT agent_name FROM thread_participants WHERE thread_id = ?",
                (thread_id,),
            ).fetchall()
        ]
        return valid_mentions(
            parse_mentions(body_markdown), participants, speaker,
        )

    @_synchronized
    def record_conversational_arrival(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        attachments: list[ThreadAttachment] | None = None,
        sent_from_task_id: str | None = None,
        recipients: list[str],
    ) -> tuple[int, list[ThreadReplyArrival]]:
        """Atomic conversational-arrival: append the message, raise the
        obligation watermark for EVERY recipient (U0 — full-recipient
        obligations), and mint/coalesce exactly one queued REPLY for the
        resolved WAKE SET only (mention routing; strict exchange hold while an
        exchange is open). Held members get obligation-only raises — no token,
        no wake audit.

        Returns (seq, arrivals). ``arrivals`` carry the newly-minted queue
        tokens (invocation_token is None when coalesced) — including any
        exchange catch-up tokens minted by a stale-exchange closure evaluated
        at the top of this write; the caller enqueues them ONLY after this
        transaction commits.
        """
        arrivals: list[ThreadReplyArrival] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            mentions = self._derive_conversational_mentions(
                thread_id, speaker, kind, body_markdown,
            )
            # TASK-5966: a stale exchange closes BEFORE the new message is
            # classified (its catch-up tokens join this write's arrivals).
            arrivals.extend(self._evaluate_exchange_closure(thread_id))
            seq = self._append_thread_message_uncommitted(
                thread_id=thread_id,
                speaker=speaker,
                kind=kind,
                body_markdown=body_markdown,
                attachments=attachments,
                sent_from_task_id=sent_from_task_id,
                mentions=mentions,
            )
            open_exchange = self._get_open_exchange_uncommitted(thread_id)
            if open_exchange is not None and seq > int(open_exchange["open_seq"]):
                # Inside E: strict-hold resolution (U2).
                wake_set = self._resolve_exchange_wake_set(
                    thread_id=thread_id,
                    mentions=mentions or [],
                    recipients=recipients,
                    open_exchange=open_exchange,
                )
                if kind is ThreadMessageKind.MESSAGE:
                    self._extend_reply_exchange_uncommitted(thread_id, seq)
            else:
                # Outside E (or the opening message itself): unconditional
                # Phase-2 mention routing (valid mentions → exactly that
                # set; zero valid mentions → broadcast).
                wake_set = resolve_wake_set(
                    mentions or [], recipients, speaker,
                )
            # U0: wake-set members mint/coalesce exactly one queued REPLY.
            for name in wake_set:
                arrivals.append(
                    self._apply_arrival_uncommitted(thread_id, name, seq)
                )
            # U0: every OTHER recipient gets the obligation-only raise
            # (full-recipient obligations — I4 totality; no token, no wake
            # audit; watermarks stay contiguous and monotonic).
            for name in recipients:
                if name not in wake_set:
                    self._raise_required_uncommitted(thread_id, name, seq)
            # TASK-5966: a founder-authored MESSAGE with a non-empty valid
            # mention set and a non-empty deferred set OPENS a new exchange
            # (only when none is open after the stale-close above). The
            # opening message's own wake set is the mention set (priority
            # mints); D members were obligation-raised above and are now held.
            if (
                self._get_open_exchange_uncommitted(thread_id) is None
                and kind is ThreadMessageKind.MESSAGE
                and speaker == "founder"
                and mentions
            ):
                deferred = [r for r in recipients if r not in mentions]
                if deferred:
                    self._open_reply_exchange_uncommitted(
                        thread_id=thread_id,
                        open_seq=seq,
                        speaker=speaker,
                        mentions=mentions,
                        recipients=recipients,
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return seq, arrivals

    def _settle_reply_uncommitted(
        self,
        token: str,
        *,
        outcome: str,
        decline_reason: str | None = None,
        reply_message_seq: int | None = None,
        reply_thread_id: str | None = None,
        reply_agent_name: str | None = None,
    ) -> ThreadReplySettlement | None:
        """Settle a conversational REPLY terminal path inside the open
        transaction. Returns None when ``token`` is not the running token of
        any delivery-state row (caller falls back to the legacy terminal path).

        Settlement contract (brief item 4):
          * reply/decline acknowledge ONLY the claimed coverage
            (``running_through``). The agent's own reply sequence is never
            part of its own required range (recipients exclude the speaker),
            so acknowledging through the claimed range never swallows an
            arrival that landed after the prompt was built.
          * arrivals during the run (``required > running_through``) yield
            exactly one post-settlement follow-on covering the retained
            unacknowledged range; it is the single wake for all of them.
            TASK-5966: a HELD pair (deferred member of an open exchange) has
            that follow-on SUPPRESSED — the exchange's single range-covering
            catch-up at closure carries the residual range
            (``exchange_held=True``, ``retry_required`` stays False).
          * failed/timeout do NOT advance acknowledgement and mint no
            immediate retry, leaving ``retry_required``
            (``required > acknowledged``) for the next conversational arrival
            to cover — EXCEPT the owed post-slot catch-up of a released
            deferral whose old slot never covered the released range (the
            closure durably marked ``catchup_pending``): that exactly-one
            range-covering wake is minted here so the release promise never
            strands (TASK-6057).

        The settled-audit payload is extended (founder-approved S4):
        ``covered_from_seq``/``covered_through_seq`` = the authoritative
        cover(S) (the immutable claimed range, or old acknowledged+1..required
        for a never-claimed queued settlement), ``claimed_at`` =
        ``thread_invocations.started_at``, ``minted_at`` =
        ``thread_invocations.enqueued_at`` (the durable mint instant), and
        ``exchange_held``. This is the tightened-D1 attribution substrate:
        a wake claimed before M has ``running_through < M.seq`` (range
        containment excludes it automatically) and ``claimed_at < M.created_at``
        (the durable recorded form); a queued settlement attributes only
        messages minted-at-or-before the wake.
        """
        now = _now().isoformat()
        row = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state "
            "WHERE running_invocation_token = ? OR queued_invocation_token = ?",
            (token, token),
        ).fetchone()
        if row is None:
            return None
        thread_id = row["thread_id"]
        agent_name = row["agent_name"]
        acknowledged = int(row["acknowledged_through_seq"] or 0)
        required = int(row["required_through_seq"] or 0)
        inv = self._conn.execute(
            "SELECT started_at, enqueued_at FROM thread_invocations "
            "WHERE invocation_token = ?",
            (token,),
        ).fetchone()
        minted_at = inv["enqueued_at"] if inv is not None else None
        if row["running_invocation_token"] == token:
            running_through = int(row["running_through_seq"] or 0)
            claimed_at = inv["started_at"] if inv is not None else None
        else:
            # A queued (not-yet-claimed) token: its delivery coverage is
            # acknowledged+1 .. required. Declining/replying to the whole
            # unclaimed wake acknowledges that full coverage.
            running_through = required
            claimed_at = None

        if outcome == "reply":
            status = ThreadInvocationStatus.CONSUMED.value
        elif outcome == "decline":
            status = ThreadInvocationStatus.DECLINED.value
        elif outcome == "timeout":
            status = ThreadInvocationStatus.TIMEOUT.value
        else:
            status = ThreadInvocationStatus.FAILED.value
        # reply/decline acknowledge exactly the claimed coverage; failure and
        # timeout leave the previously acknowledged watermark untouched.
        new_ack = running_through if outcome in ("reply", "decline") else acknowledged

        terminal = self._conn.execute(
            "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (status, decline_reason, now, token),
        )
        if (
            outcome == "reply"
            and reply_message_seq is not None
            and reply_thread_id == thread_id
            and reply_agent_name == agent_name
            and terminal.rowcount == 1
        ):
            self._conn.execute(
                "UPDATE thread_invocations SET reply_message_seq = ? "
                "WHERE invocation_token = ? AND thread_id = ? AND agent_name = ? "
                "AND status = 'consumed' AND reply_message_seq IS NULL",
                (reply_message_seq, token, reply_thread_id, reply_agent_name),
            )
        terminal_reason = (
            decline_reason if outcome in ("failed", "timeout") else None
        )
        self._conn.execute(
            "UPDATE thread_reply_delivery_state SET "
            "queued_invocation_token = NULL, "
            "running_invocation_token = NULL, running_from_seq = NULL, "
            "running_through_seq = NULL, acknowledged_through_seq = ?, "
            "last_terminal_reason = ?, last_terminal_at = ?, updated_at = ? "
            "WHERE thread_id = ? AND agent_name = ?",
            (new_ack, terminal_reason, now, now, thread_id, agent_name),
        )

        # THR-200 PR E: a real terminal reply/decline is the breaker success
        # boundary. Close the active continuity in this SAME transaction as
        # acknowledgement so neither state can commit without the other.
        if outcome in ("reply", "decline"):
            active_breakers = self._conn.execute(
                "SELECT episode_id FROM thread_reply_breaker_episodes "
                "WHERE thread_id=? AND agent_name=? AND state IN ('open','probe')",
                (thread_id, agent_name),
            ).fetchall()
            for active in active_breakers:
                self._conn.execute(
                    "INSERT OR IGNORE INTO thread_reply_breaker_receipts "
                    "(invocation_token,episode_id,outcome,failure_category,recorded_at) "
                    "VALUES (?,?,'success',NULL,?)",
                    (token, active["episode_id"], now),
                )
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent=agent_name,
                    action="thread_reply_breaker_closed",
                    payload={"episode_id": active["episode_id"], "outcome": outcome},
                )
            self._conn.execute(
                "UPDATE thread_reply_breaker_episodes SET state='closed',"
                "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
                "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
                "WHERE thread_id=? AND agent_name=? AND state IN ('open','probe')",
                (now, thread_id, agent_name),
            )

        follow_on: str | None = None
        exchange_held = False
        catchup_minted = False
        if required > new_ack:
            # A residual range remains. At most one wake may carry it:
            #   * a held pair (member of an OPEN exchange) suppresses the
            #     follow-on — the exchange's single catch-up at closure
            #     carries the residual range (exchange_held=True);
            #   * reply/decline mint exactly one follow-on covering arrivals
            #     strictly after the immutable running range
            #     (``required > running_through``);
            #   * failed/timeout mint ONLY the owed post-slot catch-up of a
            #     released deferral whose old slot never covered it (the
            #     closure durably marked catchup_pending) — a plain
            #     unacknowledged range gets no immediate retry (Phase-1
            #     fail-open: the next conversational arrival covers it).
            pending_catchup = self._pair_catchup_pending_uncommitted(
                thread_id, agent_name,
            )
            if self._pair_held_by_open_exchange(thread_id, agent_name):
                exchange_held = True
            elif outcome in ("reply", "decline") or pending_catchup:
                follow_on = self._mint_reply_invocation_uncommitted(
                    thread_id, agent_name, new_ack + 1,
                )
                self._conn.execute(
                    "UPDATE thread_reply_delivery_state SET "
                    "queued_invocation_token = ?, updated_at = ? "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (follow_on, now, thread_id, agent_name),
                )
                if pending_catchup:
                    # The minted wake is the owed post-slot catch-up (for a
                    # reply/decline it is the natural follow-on; for a
                    # failed/timeout it is the release promise honoured) —
                    # consume the durable marker in the same transaction
                    # (exactly-once; a crash rolls both back).
                    catchup_minted = True
                    self._clear_catchup_pending_uncommitted(
                        thread_id, agent_name,
                    )
                    self.insert_audit_log_uncommitted(
                        task_id=thread_id, agent=agent_name,
                        action="thread_deferral_catchup_minted",
                        payload={
                            "thread_id": thread_id,
                            "agent_name": agent_name,
                            "from_seq": new_ack + 1,
                            "through_seq": required,
                            "mint_token_prefix": follow_on[:8],
                        },
                    )

        self._emit_reply_wake_audit(
            thread_id=thread_id, agent_name=agent_name,
            action="thread_reply_wake_settled",
            payload={
                "agent_name": agent_name,
                "outcome": outcome,
                "acknowledged_through_seq": new_ack,
                "required_through_seq": required,
                "covered_from_seq": acknowledged + 1,
                "covered_through_seq": running_through,
                "claimed_at": claimed_at,
                "minted_at": minted_at,
                "exchange_held": exchange_held,
                "catchup_minted": catchup_minted,
                "retry_required": (
                    required > new_ack and follow_on is None
                    and not exchange_held
                ),
                "follow_on_token_prefix": follow_on[:8] if follow_on else None,
                "decline_reason": decline_reason,
            },
        )

        return ThreadReplySettlement(
            thread_id=thread_id,
            agent_name=agent_name,
            outcome=outcome,  # type: ignore[arg-type]
            acknowledged_through_seq=new_ack,
            required_through_seq=required,
            # ``retry_required`` is the residual-obligation diagnostic: True
            # only when the range is still unacknowledged AND no follow-on wake
            # was minted to carry it (failure/timeout without an owed
            # catch-up). A reply/decline that minted a follow-on has an active
            # queued wake, so it is not retry_required; a held suppression
            # defers the range to the exchange catch-up, which is not a retry
            # condition; a failed/timeout that honoured a released-deferral
            # catch-up marker minted the owed wake, which is not a retry
            # either.
            retry_required=(
                required > new_ack and follow_on is None and not exchange_held
            ),
            follow_on_token=follow_on,
            exchange_held=exchange_held,
        )

    @_synchronized
    def settle_conversational_reply(
        self,
        *,
        token: str,
        outcome: str,
        decline_reason: str | None = None,
    ) -> ThreadReplySettlement | None:
        """Public settlement seam for a conversational REPLY terminal path.

        Returns None when the token is not the running token of any delivery-
        state row (BOOTSTRAP/TASK_FOLLOWUP, or an already-settled/stale REPLY);
        the caller then applies the legacy terminal transition.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            settlement = self._settle_reply_uncommitted(
                token,
                outcome=outcome,
                decline_reason=decline_reason,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return settlement

    @_synchronized
    def settle_conversational_reply_with_breaker_failure(
        self, *, token: str, outcome: str, decline_reason: str,
        thread_id: str, agent_name: str, executor_key: str,
        failure_category: str, threshold: int, cooldown_seconds: int,
        now: datetime | None = None,
    ) -> tuple[ThreadReplySettlement | None, ThreadReplyBreakerEpisode | None]:
        """Atomically settle a qualifying failure and advance its breaker."""
        if threshold < 1 or cooldown_seconds < 1:
            raise ValueError("breaker threshold and cooldown must be positive")
        at = (now or _now()).astimezone(timezone.utc)
        at_s = at.isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            settlement = self._settle_reply_uncommitted(
                token, outcome=outcome, decline_reason=decline_reason,
            )
            if settlement is None:
                self._conn.commit()
                return None, None
            receipt = self._conn.execute(
                "SELECT episode_id FROM thread_reply_breaker_receipts "
                "WHERE invocation_token=?", (token,),
            ).fetchone()
            row = self._conn.execute(
                "SELECT * FROM thread_reply_breaker_episodes WHERE thread_id=? "
                "AND agent_name=? AND executor_key=?",
                (thread_id, agent_name, executor_key),
            ).fetchone()
            if receipt is not None:
                episode = self._conn.execute(
                    "SELECT * FROM thread_reply_breaker_episodes WHERE episode_id=?",
                    (receipt["episode_id"],),
                ).fetchone()
                self._conn.commit()
                return settlement, self._row_to_reply_breaker_episode(episode)
            new_episode = row is None or (
                row["state"] == "closed" and int(row["consecutive_failures"]) == 0
            )
            episode_id = f"brep-{uuid.uuid4().hex}" if new_episode else row["episode_id"]
            failures = 1 if new_episode else int(row["consecutive_failures"]) + 1
            prior_state = "closed" if new_episode else row["state"]
            state = "open" if failures >= threshold or prior_state == "probe" else prior_state
            opened_at = at_s if state == "open" and prior_state != "open" else (
                None if new_episode else row["opened_at"]
            )
            cooldown_until = (
                (at + timedelta(seconds=cooldown_seconds)).isoformat()
                if state == "open" and prior_state != "open"
                else (None if new_episode else row["cooldown_until"])
            )
            self._conn.execute(
                "INSERT INTO thread_reply_breaker_episodes "
                "(thread_id,agent_name,executor_key,episode_id,state,consecutive_failures,"
                "opened_at,cooldown_until,probe_lease_id,last_failure_category,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(thread_id,agent_name,executor_key) "
                "DO UPDATE SET episode_id=excluded.episode_id,state=excluded.state,"
                "consecutive_failures=excluded.consecutive_failures,opened_at=excluded.opened_at,"
                "cooldown_until=excluded.cooldown_until,probe_lease_id=NULL,"
                "last_failure_category=excluded.last_failure_category,updated_at=excluded.updated_at",
                (thread_id, agent_name, executor_key, episode_id, state, failures,
                 opened_at, cooldown_until, failure_category, at_s),
            )
            self._conn.execute(
                "INSERT INTO thread_reply_breaker_receipts VALUES (?,?,'failure',?,?)",
                (token, episode_id, failure_category, at_s),
            )
            if state == "open" and prior_state != "open":
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent=agent_name,
                    action=("thread_reply_breaker_reopened" if prior_state == "probe"
                            else "thread_reply_breaker_opened"),
                    payload={"episode_id": episode_id,
                             "failure_category": failure_category,
                             "consecutive_failures": failures},
                )
            episode = self._conn.execute(
                "SELECT * FROM thread_reply_breaker_episodes WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            self._conn.commit()
            return settlement, self._row_to_reply_breaker_episode(episode)
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def settle_conversational_reply_with_exchange(
        self,
        *,
        token: str,
        outcome: str,
        decline_reason: str | None = None,
    ) -> tuple[ThreadReplySettlement | None, list[ThreadReplyArrival]]:
        """Settlement seam for caller-owned exchange enqueue (TASK-5966).

        Same settlement transaction as ``settle_conversational_reply`` PLUS
        the exchange closure evaluation on the settled thread — the design's
        "every REPLY settlement is a closure evaluation point" — returning
        any catch-up arrivals whose tokens the caller enqueues after commit.
        A settlement on a thread with no open exchange is a no-op closure
        evaluation. Used by the decline route (which can enqueue); the
        runner's failure paths keep ``settle_conversational_reply`` and rely
        on the reaper for closure.
        """
        arrivals: list[ThreadReplyArrival] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            settlement = self._settle_reply_uncommitted(
                token,
                outcome=outcome,
                decline_reason=decline_reason,
            )
            if settlement is not None:
                arrivals = self._evaluate_exchange_closure(
                    settlement.thread_id,
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return settlement, arrivals

    @_synchronized
    def reply_conversational(
        self,
        *,
        thread_id: str,
        speaker: str,
        body_markdown: str | None,
        attachments: list[ThreadAttachment] | None,
        token: str,
        token_purpose: ThreadInvocationPurpose,
    ) -> tuple[int, ThreadReplySettlement | None, list[ThreadReplyArrival]]:
        """Atomic reply: append the reply message, settle the held token, and
        broadcast to every OTHER participant.

        Returns (seq, settlement, arrivals). ``settlement`` is None for a
        non-REPLY token (BOOTSTRAP/TASK_FOLLOWUP use the legacy consume); the
        broadcast to other participants always uses the coalescing arrival path
        (replacing the legacy per-recipient REPLY mint).
        """
        now = _now().isoformat()
        arrivals: list[ThreadReplyArrival] = []
        settlement: ThreadReplySettlement | None = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # TASK-5966: a stale exchange closes before this reply is
            # classified (catch-up tokens join this write's arrivals).
            arrivals.extend(self._evaluate_exchange_closure(thread_id))
            participants = [
                p["agent_name"] for p in self._conn.execute(
                    "SELECT agent_name FROM thread_participants WHERE thread_id = ?",
                    (thread_id,),
                ).fetchall()
            ]
            mentions = valid_mentions(
                parse_mentions(body_markdown), participants, speaker,
            )
            seq = self._append_thread_message_uncommitted(
                thread_id=thread_id,
                speaker=speaker,
                kind=ThreadMessageKind.MESSAGE,
                body_markdown=body_markdown,
                attachments=attachments,
                mentions=mentions,
            )
            if token_purpose is ThreadInvocationPurpose.REPLY:
                settlement = self._settle_reply_uncommitted(
                    token,
                    outcome="reply",
                    reply_message_seq=seq,
                    reply_thread_id=thread_id,
                    reply_agent_name=speaker,
                )
                if settlement is None:
                    # Legacy/stale pending REPLY not owned by delivery state:
                    # fall back to the legacy consume transition.
                    terminal = self._conn.execute(
                        "UPDATE thread_invocations SET status = 'consumed', "
                        "consumed_at = ? WHERE invocation_token = ? "
                        "AND status = 'pending'",
                        (now, token),
                    )
                    if terminal.rowcount == 1:
                        self._conn.execute(
                            "UPDATE thread_invocations SET reply_message_seq = ? "
                            "WHERE invocation_token = ? AND thread_id = ? "
                            "AND agent_name = ? AND purpose = 'reply' "
                            "AND status = 'consumed' AND reply_message_seq IS NULL",
                            (seq, token, thread_id, speaker),
                        )
            else:
                terminal = self._conn.execute(
                    "UPDATE thread_invocations SET status = 'consumed', "
                    "consumed_at = ? WHERE invocation_token = ? "
                    "AND status = 'pending'",
                    (now, token),
                )
                if terminal.rowcount == 1:
                    self._conn.execute(
                        "UPDATE thread_invocations SET reply_message_seq = ? "
                        "WHERE invocation_token = ? AND thread_id = ? "
                        "AND agent_name = ? AND purpose = ? "
                        "AND status = 'consumed' AND reply_message_seq IS NULL",
                        (seq, token, thread_id, speaker, token_purpose.value),
                    )
            recipients = [name for name in participants if name != speaker]
            # Phase-2 mention routing (THR-198, Slice B): REPLY tokens resolve
            # the broadcast at write time from the persisted structured
            # mention signal + the thread setting. TASK_FOLLOWUP and BOOTSTRAP
            # are ISOLATED — they keep the full participants-minus-speaker
            # broadcast and are never mention-routed (documented pierce source
            # inside an exchange).
            if token_purpose is ThreadInvocationPurpose.REPLY:
                open_exchange = self._get_open_exchange_uncommitted(thread_id)
                if open_exchange is not None and seq > int(
                    open_exchange["open_seq"],
                ):
                    # Inside E: strict-hold resolution (U2) — mention-pierce
                    # or cohort-only; every other recipient is held
                    # (obligation-only raise below).
                    wake_set = self._resolve_exchange_wake_set(
                        thread_id=thread_id,
                        mentions=mentions,
                        recipients=recipients,
                        open_exchange=open_exchange,
                    )
                    self._extend_reply_exchange_uncommitted(thread_id, seq)
                else:
                    # Outside E: unconditional Phase-2 mention routing.
                    wake_set = resolve_wake_set(
                        mentions, recipients, speaker,
                    )
            else:
                wake_set = recipients
            # U0: wake-set members mint/coalesce; every other recipient gets
            # the obligation-only raise (full-recipient obligations).
            for name in wake_set:
                arrivals.append(
                    self._apply_arrival_uncommitted(thread_id, name, seq)
                )
            for name in recipients:
                if name not in wake_set:
                    self._raise_required_uncommitted(thread_id, name, seq)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return seq, settlement, arrivals

    @_synchronized
    def claim_conversational_reply(
        self,
        token: str,
        *,
        executor: str | None = None,
        model: str | None = None,
    ) -> ThreadReplyClaim | None:
        """Durable queued→running CAS for a conversational REPLY.

        Succeeds only when ``token`` is the pair's queued_invocation_token AND
        the receipt is a pending, unstarted, same-pair REPLY. In one
        transaction it transfers queued→running, snapshots the immutable
        inclusive range (running_from = acknowledged + 1, running_through =
        required), and stamps started_at. A duplicate/stale job returns None so
        the runner no-ops before any prompt/subprocess work.
        """
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT * FROM thread_reply_delivery_state "
                "WHERE queued_invocation_token = ?",
                (token,),
            ).fetchone()
            if row is None or row["running_invocation_token"] is not None:
                self._conn.commit()
                return None
            inv = self._conn.execute(
                "SELECT * FROM thread_invocations WHERE invocation_token = ?",
                (token,),
            ).fetchone()
            valid = (
                inv is not None
                and inv["thread_id"] == row["thread_id"]
                and inv["agent_name"] == row["agent_name"]
                and inv["purpose"] == ThreadInvocationPurpose.REPLY.value
                and inv["status"] == ThreadInvocationStatus.PENDING.value
                and inv["started_at"] is None
            )
            if not valid:
                self._conn.commit()
                return None
            acknowledged = int(row["acknowledged_through_seq"] or 0)
            required = int(row["required_through_seq"] or 0)
            running_from = acknowledged + 1
            running_through = required
            self._conn.execute(
                "UPDATE thread_invocations SET started_at = ?, executor = ?, "
                "model = ? "
                "WHERE invocation_token = ? AND status = 'pending'",
                (now, executor, model, token),
            )
            self._conn.execute(
                "UPDATE thread_reply_delivery_state SET "
                "queued_invocation_token = NULL, "
                "running_invocation_token = ?, running_from_seq = ?, "
                "running_through_seq = ?, updated_at = ? "
                "WHERE thread_id = ? AND agent_name = ?",
                (token, running_from, running_through, now,
                 row["thread_id"], row["agent_name"]),
            )
            self._emit_reply_wake_audit(
                thread_id=row["thread_id"], agent_name=row["agent_name"],
                action="thread_reply_wake_claimed",
                payload={
                    "agent_name": row["agent_name"],
                    "from_seq": running_from,
                    "through_seq": running_through,
                    "token_prefix": token[:8],
                },
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return ThreadReplyClaim(
            thread_id=row["thread_id"],
            agent_name=row["agent_name"],
            invocation_token=token,
            acknowledged_through_seq=acknowledged,
            required_through_seq=required,
            running_from_seq=running_from,
            running_through_seq=running_through,
        )

    @_synchronized
    def discard_reply_delivery(
        self,
        thread_id: str,
        *,
        agent_name: str | None = None,
        decline_reason: str,
        status: ThreadInvocationStatus = ThreadInvocationStatus.FAILED,
    ) -> int:
        """Terminalize owned conversational REPLY state with an explicit
        discard boundary (abort / archive / participant removal).

        Terminalizes every pending REPLY invocation for (thread_id[,
        agent_name]) under ``status`` + ``decline_reason``, clears the queued/
        running ownership slots + range, and advances acknowledged to required
        so no queued/running/retry_required obligation survives and a later
        message starts after the boundary. Never touches BOOTSTRAP /
        TASK_FOLLOWUP rows and never resurrects a discarded wake.
        Returns the number of reply invocation rows terminalized.
        """
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # Snapshot the per-pair obligations BEFORE the terminalizing
            # UPDATE so each affected pair emits exactly one truthful cancelled
            # audit. Two obligation classes are merged: (a) every pair with a
            # pending REPLY invocation (legacy-only pairs without a state row
            # included), and (b) every pair with a live delivery-state
            # obligation — a queued/running token, or an unacknowledged
            # retry_required range — even when no pending receipt row exists
            # (e.g. a failed wake awaiting the next conversational arrival).
            # Boundary = state required watermark when present, else the
            # pair's max triggering seq.
            if agent_name is None:
                pair_rows = self._conn.execute(
                    "SELECT agent_name, COUNT(*) AS n, MAX(triggering_seq) "
                    "AS max_seq FROM thread_invocations "
                    "WHERE thread_id = ? AND status = 'pending' "
                    "AND purpose = 'reply' GROUP BY agent_name",
                    (thread_id,),
                ).fetchall()
                state_rows = self._conn.execute(
                    "SELECT agent_name FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND (queued_invocation_token IS NOT NULL "
                    "OR running_invocation_token IS NOT NULL "
                    "OR required_through_seq > acknowledged_through_seq)",
                    (thread_id,),
                ).fetchall()
            else:
                pair_rows = self._conn.execute(
                    "SELECT agent_name, COUNT(*) AS n, MAX(triggering_seq) "
                    "AS max_seq FROM thread_invocations "
                    "WHERE thread_id = ? AND agent_name = ? "
                    "AND status = 'pending' AND purpose = 'reply' "
                    "GROUP BY agent_name",
                    (thread_id, agent_name),
                ).fetchall()
                state_rows = self._conn.execute(
                    "SELECT agent_name FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND agent_name = ? "
                    "AND (queued_invocation_token IS NOT NULL "
                    "OR running_invocation_token IS NOT NULL "
                    "OR required_through_seq > acknowledged_through_seq)",
                    (thread_id, agent_name),
                ).fetchall()
            # Merge state-only pairs (n = 0 receipts terminalized by the sweep)
            # into the audit set without duplicate rows.
            seen: set[str] = set()
            merged: list[dict] = []
            for pr in pair_rows:
                merged.append(pr)
                seen.add(pr["agent_name"])
            for sr in state_rows:
                if sr["agent_name"] not in seen:
                    merged.append({"agent_name": sr["agent_name"], "n": 0,
                                   "max_seq": None})
                    seen.add(sr["agent_name"])
            if agent_name is None:
                cursor = self._conn.execute(
                    "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
                    "consumed_at = ? WHERE thread_id = ? AND status = 'pending' "
                    "AND purpose = 'reply'",
                    (status.value, decline_reason, now, thread_id),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
                    "consumed_at = ? WHERE thread_id = ? AND agent_name = ? "
                    "AND status = 'pending' AND purpose = 'reply'",
                    (status.value, decline_reason, now, thread_id, agent_name),
                )
            if agent_name is None:
                self._conn.execute(
                    "UPDATE thread_reply_breaker_episodes SET state='closed',"
                    "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
                    "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
                    "WHERE thread_id=?",
                    (now, thread_id),
                )
            else:
                self._conn.execute(
                    "UPDATE thread_reply_breaker_episodes SET state='closed',"
                    "consecutive_failures=0,opened_at=NULL,cooldown_until=NULL,"
                    "probe_lease_id=NULL,last_failure_category=NULL,updated_at=? "
                    "WHERE thread_id=? AND agent_name=?",
                    (now, thread_id, agent_name),
                )
            if agent_name is None:
                self._conn.execute(
                    "UPDATE thread_reply_delivery_state SET "
                    "acknowledged_through_seq = required_through_seq, "
                    "queued_invocation_token = NULL, "
                    "running_invocation_token = NULL, running_from_seq = NULL, "
                    "running_through_seq = NULL, "
                    "last_terminal_reason = ?, last_terminal_at = ?, "
                    "updated_at = ? WHERE thread_id = ?",
                    (decline_reason, now, now, thread_id),
                )
                # TASK-5966: whole-thread discard (abort/archive) SUPPRESSES
                # any open exchange — no catch-up is ever minted (G5: the
                # human stopped). Idempotent per-exchange CAS.
                if decline_reason == "archive_started":
                    _exchange_reason = "thread_archived"
                else:
                    _exchange_reason = "founder_aborted"
                self._suppress_open_exchanges_uncommitted(
                    thread_id, reason=_exchange_reason,
                )
            else:
                self._conn.execute(
                    "UPDATE thread_reply_delivery_state SET "
                    "acknowledged_through_seq = required_through_seq, "
                    "queued_invocation_token = NULL, "
                    "running_invocation_token = NULL, running_from_seq = NULL, "
                    "running_through_seq = NULL, "
                    "last_terminal_reason = ?, last_terminal_at = ?, "
                    "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
                    (decline_reason, now, now, thread_id, agent_name),
                )
                # TASK-5966: participant removal suppresses the removed
                # agent's held deferral rows in any open exchange (the pair is
                # terminal — a removed member never receives a catch-up). The
                # exchange itself stays open; closure fires at the next
                # evaluation point (write/reaper/reconcile) with proper
                # enqueue, so no catch-up token can strand.
                self._conn.execute(
                    "UPDATE thread_exchange_deferrals SET state = 'suppressed' "
                    "WHERE thread_id = ? AND agent_name = ? AND state = 'held'",
                    (thread_id, agent_name),
                )
            for pr in merged:
                pair_agent = pr["agent_name"]
                st = self._conn.execute(
                    "SELECT required_through_seq FROM thread_reply_delivery_state "
                    "WHERE thread_id = ? AND agent_name = ?",
                    (thread_id, pair_agent),
                ).fetchone()
                boundary = (
                    int(st["required_through_seq"] or 0)
                    if st is not None else int(pr["max_seq"] or 0)
                )
                self._emit_reply_wake_audit(
                    thread_id=thread_id, agent_name=pair_agent,
                    action="thread_reply_wake_cancelled",
                    payload={
                        "agent_name": pair_agent,
                        "boundary_seq": boundary,
                        "reason": decline_reason,
                        "swept_count": int(pr["n"]),
                    },
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return cursor.rowcount

    @_synchronized
    def list_reply_delivery_projections(
        self, thread_id: str,
    ) -> list[ReplyDeliveryProjection]:
        """Pair-level reply_delivery projection for a thread (server contract).

        Derived from authoritative reply-delivery and exchange state — never
        fabricated from per-message invocation rows. A fully-settled pair (nothing queued/
        running/required) is omitted; terminal history remains on the
        per-message responder strips. ``coalesced_message_count`` is the number
        of transcript rows the wake's range covers (COUNT, not subtraction).
        """
        rows = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state WHERE thread_id = ? "
            "ORDER BY agent_name",
            (thread_id,),
        ).fetchall()
        out: list[ReplyDeliveryProjection] = []
        for row in rows:
            acknowledged = int(row["acknowledged_through_seq"] or 0)
            required = int(row["required_through_seq"] or 0)
            queued = row["queued_invocation_token"]
            running = row["running_invocation_token"]
            running_from = row["running_from_seq"]
            running_through = row["running_through_seq"]
            started_at = None
            held = self._conn.execute(
                "SELECT 1 FROM thread_reply_exchange e "
                "JOIN thread_exchange_deferrals d ON d.thread_id=e.thread_id "
                "AND d.exchange_id=e.exchange_id WHERE e.thread_id=? "
                "AND e.state='open' AND d.agent_name=? AND d.state='held' LIMIT 1",
                (thread_id, row["agent_name"]),
            ).fetchone()
            if running is not None:
                state = "running"
                from_seq = int(running_from or 0)
                through_seq = int(running_through or 0)
                inv = self._conn.execute(
                    "SELECT started_at FROM thread_invocations "
                    "WHERE invocation_token = ?",
                    (running,),
                ).fetchone()
                if inv is not None:
                    started_at = inv["started_at"]
            elif queued is not None:
                state = "queued"
                from_seq = acknowledged + 1
                through_seq = required
            elif required > acknowledged:
                # Exchange membership persists after a mention-pierced wake
                # settles; only an outstanding range is a held delivery.
                if held is not None:
                    state = "held"
                else:
                    state = "retry_required"
                from_seq = acknowledged + 1
                through_seq = required
            else:
                continue  # fully settled — omit from the live projection
            cnt = self._conn.execute(
                "SELECT COUNT(*) AS n FROM thread_messages "
                "WHERE thread_id = ? AND seq >= ? AND seq <= ?",
                (thread_id, from_seq, through_seq),
            ).fetchone()
            out.append(ReplyDeliveryProjection(
                agent_name=row["agent_name"],
                state=state,  # type: ignore[arg-type]
                from_seq=from_seq,
                through_seq=through_seq,
                coalesced_message_count=int(cnt["n"]),
                started_at=started_at,
                updated_at=row["updated_at"],
                # Terminal reason is historical metadata, never state proof.
                # Expose it only for a currently genuine retry diagnostic.
                last_terminal_reason=(
                    row["last_terminal_reason"]
                    if state == "retry_required" else None
                ),
                current_failure_category=(
                    reply_failure_category("failed", row["last_terminal_reason"])
                    if state == "retry_required" else None
                ),
            ))
        return out
