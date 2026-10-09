from __future__ import annotations

import json
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _parse_dt,
    _synchronized,
)
from runtime.models import ThreadReplyArrival, ThreadReplyExchangeProjection


# ── TASK-5966 strict mention-led exchange bounds (founder-ratified) ──────
# EXCHANGE_GRACE: idle-closure bound — an exchange closes when the cohort has
# no live covering wake AND no conversational activity for this long
# (founder verdict: FIVE minutes, not the design artifact's 15-minute
# default).
EXCHANGE_GRACE_SECONDS = 5 * 60
# MAX_PRIORITY_WAIT: absolute fail-open bound from exchange open — the reaper
# closes any exchange older than this (retained approved G1 = 4 hours).
MAX_PRIORITY_WAIT_SECONDS = 4 * 60 * 60


class ReplyExchangeMixin:
    # ── TASK-5966 strict mention-led exchange: store seams ────────────────
    #
    # Founder-ratified (THR-198 seq 194/195/196; TASK-5939 verdict): strict
    # exchange hold, additive exchange state, full-recipient
    # obligations, pre-claim attribution safeguard, frozen cohort, exactly one
    # range-covering catch-up per pair at closure, EXCHANGE_GRACE = 5 min,
    # absolute fail-open = 4 h. Any-burst deferral is explicitly excluded.
    #
    # Model A (exchange-epoch gate): ``thread_reply_exchange`` (1 row per
    # exchange, the state machine) + ``thread_exchange_deferrals`` (frozen
    # D(E), audit/sweep substrate). During E, held pairs get obligation-only
    # raises (no token, no wake audit — at-most-one slots untouched,
    # watermarks stay contiguous); at closure every pair with
    # ``acknowledged < required`` gets ONE slot-checked catch-up wake covering
    # the full contiguous range. No gaps, no fabricated acks.

    def _get_open_exchange_uncommitted(self, thread_id: str):
        """The single OPEN exchange row for a thread, or None. Runs inside
        an open transaction. The partial-unique index ``idx_trex_open`` makes
        at most one such row exist by construction."""
        return self._conn.execute(
            "SELECT * FROM thread_reply_exchange "
            "WHERE thread_id = ? AND state = 'open'",
            (thread_id,),
        ).fetchone()

    def _exchange_cohort_uncommitted(self, exchange_row) -> list[str]:
        """The frozen priority cohort P(E) of an exchange, read from the
        immutable ``M_open.mentions_json`` (no duplicated JSON column).
        Fail-closed: a malformed/missing opening mention signal yields []
        (the caller then treats the exchange as corrupt / quiescent-able)."""
        opener = self._conn.execute(
            "SELECT mentions_json FROM thread_messages "
            "WHERE thread_id = ? AND seq = ?",
            (exchange_row["thread_id"], exchange_row["open_seq"]),
        ).fetchone()
        if opener is None or not opener["mentions_json"]:
            return []
        try:
            data = json.loads(opener["mentions_json"])
        except (TypeError, ValueError):
            return []
        return [m for m in data if isinstance(m, str)]

    def _raise_required_uncommitted(
        self, thread_id: str, agent_name: str, seq: int,
    ) -> None:
        """U0 — obligation-only raise: advance ``required_through_seq`` to
        ``seq`` for one recipient pair WITHOUT minting any wake and WITHOUT a
        wake audit. Runs inside an open transaction (no commit).

        This is the full-recipient obligation half of U0: every conversational
        message raises ``required`` for EVERY recipient (I4 totality), while
        only the resolved wake set mints. A missing pair row is created with
        acknowledged = seq - 1 and NO token (obligation row; the next wake
        covers the full range). ``seq <= required`` is an idempotent silent
        no-op — deliberately NO audit (a duplicate/backdated notification
        must not fabricate an event). Watermarks stay monotonic and
        contiguous: ``acknowledged`` never moves, ``required`` only advances.
        """
        now = _now().isoformat()
        row = self._conn.execute(
            "SELECT required_through_seq FROM thread_reply_delivery_state "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO thread_reply_delivery_state "
                "(thread_id, agent_name, acknowledged_through_seq, "
                "required_through_seq, updated_at) VALUES (?, ?, ?, ?, ?)",
                (thread_id, agent_name, seq - 1, seq, now),
            )
            return
        required = int(row["required_through_seq"] or 0)
        if seq <= required:
            return
        self._conn.execute(
            "UPDATE thread_reply_delivery_state SET required_through_seq = ?, "
            "updated_at = ? WHERE thread_id = ? AND agent_name = ?",
            (seq, now, thread_id, agent_name),
        )

    def _open_reply_exchange_uncommitted(
        self,
        *,
        thread_id: str,
        open_seq: int,
        speaker: str,
        mentions: list[str],
        recipients: list[str],
    ) -> int:
        """Open a strict mention-led exchange inside the open transaction
        (caller commits). Preconditions (caller-verified): no open exchange,
        speaker == 'founder', ``mentions`` non-empty, deferred set non-empty,
        exchange enabled. P(E) = ``mentions`` (frozen, read later from
        ``M_open.mentions_json``); D(E) = recipients − speaker − P frozen
        into ``thread_exchange_deferrals`` (held). Returns the new
        exchange_id.
        """
        now = _now().isoformat()
        deferred = [r for r in recipients if r not in mentions]
        next_id = self._conn.execute(
            "SELECT COALESCE(MAX(exchange_id), 0) + 1 "
            "FROM thread_reply_exchange WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO thread_reply_exchange (thread_id, exchange_id, "
            "state, open_seq, close_seq, opened_at, last_activity_at, "
            "deferred_count) VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
            (thread_id, next_id, open_seq, open_seq, now, now, len(deferred)),
        )
        for name in deferred:
            self._conn.execute(
                "INSERT INTO thread_exchange_deferrals (thread_id, "
                "exchange_id, agent_name, state, created_at) "
                "VALUES (?, ?, ?, 'held', ?)",
                (thread_id, next_id, name, now),
            )
        self.insert_audit_log_uncommitted(
            task_id=thread_id, agent="founder",
            action="thread_exchange_opened",
            payload={
                "thread_id": thread_id, "exchange_id": next_id,
                "open_seq": open_seq, "priority": mentions,
                "deferred": deferred,
            },
        )
        for name in deferred:
            self.insert_audit_log_uncommitted(
                task_id=thread_id, agent=name,
                action="thread_deferral_held",
                payload={
                    "thread_id": thread_id, "exchange_id": next_id,
                    "agent_name": name,
                },
            )
        return next_id

    def _extend_reply_exchange_uncommitted(
        self, thread_id: str, seq: int,
    ) -> None:
        """Extend an open exchange: every conversational MESSAGE inside E
        (any speaker) refreshes ``last_activity_at`` and advances
        ``close_seq`` to the frontier. Runs inside the open transaction."""
        now = _now().isoformat()
        self._conn.execute(
            "UPDATE thread_reply_exchange SET last_activity_at = ?, "
            "close_seq = ? WHERE thread_id = ? AND state = 'open'",
            (now, seq, thread_id),
        )

    def _resolve_exchange_wake_set(
        self,
        *,
        thread_id: str,
        mentions: list[str],
        recipients: list[str],
        open_exchange,
        founder_only: bool = False,
    ) -> list[str]:
        """U2 — strict-hold wake resolution inside an open exchange.

        * ``M`` has valid mentions → wake set = exactly that mention set
          (mention-pierce: newly mentioned / deferred members wake
          immediately; the cohort is NOT altered — frozen).
        * ``M`` has no valid mentions → wake set = the frozen cohort P(E)
          only (seq 188: "only that cohort receives wakes"). No-mention
          messages inside E NEVER broadcast to held members.

        TASK_FOLLOWUP/BOOTSTRAP never reach here (callers keep the isolated
        full-broadcast branch). ``recipients`` is the candidate set minus the
        speaker; every recipient NOT in the wake set is held (obligation-only).
        """
        if founder_only:
            return []
        if mentions:
            return [m for m in mentions if m in recipients]
        cohort = self._exchange_cohort_uncommitted(open_exchange)
        return [c for c in cohort if c in recipients]

    def _pair_held_by_open_exchange(
        self, thread_id: str, agent_name: str,
    ) -> bool:
        """True when ``agent_name`` is a held (non-cohort) member of an open
        exchange on this thread. Used to suppress the settlement follow-on for
        a held pair (the exchange's single catch-up at closure carries the
        residual range). Runs inside the open transaction."""
        row = self._get_open_exchange_uncommitted(thread_id)
        if row is None:
            return False
        cohort = self._exchange_cohort_uncommitted(row)
        return agent_name not in cohort

    def _pair_catchup_pending_uncommitted(
        self, thread_id: str, agent_name: str,
    ) -> bool:
        """True when the pair holds an unsatisfied released-deferral catch-up
        obligation: any ``thread_exchange_deferrals`` row (across released
        exchanges) with ``catchup_pending = 1``. Set at closure when the
        pair's single live slot did not cover the released range; the owed
        catch-up is minted exactly once when that slot reaches a terminal
        outcome. Runs inside the open transaction."""
        row = self._conn.execute(
            "SELECT 1 FROM thread_exchange_deferrals "
            "WHERE thread_id = ? AND agent_name = ? AND catchup_pending = 1 "
            "LIMIT 1",
            (thread_id, agent_name),
        ).fetchone()
        return row is not None

    def _mark_catchup_pending_uncommitted(
        self, thread_id: str, exchange_id: int, agent_name: str,
    ) -> bool:
        """Durably record a pending post-slot catch-up on THIS exchange's
        deferral row (a D(E) member whose live slot did not cover the
        released range at closure). Returns True when a row was actually
        updated (the pair is a deferred member of this exchange). Runs
        inside the open transaction."""
        cur = self._conn.execute(
            "UPDATE thread_exchange_deferrals SET catchup_pending = 1 "
            "WHERE thread_id = ? AND exchange_id = ? AND agent_name = ? "
            "AND state = 'released'",
            (thread_id, exchange_id, agent_name),
        )
        return cur.rowcount > 0

    def _clear_catchup_pending_uncommitted(
        self, thread_id: str, agent_name: str,
    ) -> int:
        """Consume every pending catch-up marker for the pair — one
        range-covering wake satisfies all of them. Runs inside the open
        transaction. Returns the number of deferral rows whose marker was
        actually cleared (0 when the pair held no pending marker), so a
        caller can record a truthful audit of marker revocation."""
        cur = self._conn.execute(
            "UPDATE thread_exchange_deferrals SET catchup_pending = 0 "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        return cur.rowcount

    def _exchange_has_live_cohort_wake(
        self, thread_id: str, open_seq: int, cohort: list[str],
    ) -> bool:
        """Quiescence predicate: True when any cohort member has a live wake
        whose coverage contains the exchange — a running wake with
        ``running_through_seq >= open_seq`` (claimed coverage is immutable, so
        a pre-arrival claim with running_through < open_seq is NOT live), or a
        queued wake whose ``required_through_seq >= open_seq`` (the recovery
        replacement keeps the cohort non-quiescent — daemon_restart is an
        interruption, never terminal, while replacement work exists)."""
        if not cohort:
            return False
        placeholders = ",".join("?" for _ in cohort)
        row = self._conn.execute(
            f"SELECT 1 FROM thread_reply_delivery_state WHERE thread_id = ? "
            f"AND agent_name IN ({placeholders}) AND ("
            f"(running_through_seq IS NOT NULL AND running_through_seq >= ?) "
            f"OR (queued_invocation_token IS NOT NULL "
            f"AND required_through_seq >= ?)) LIMIT 1",
            (thread_id, *cohort, open_seq, open_seq),
        ).fetchone()
        return row is not None

    def _close_reply_exchange_uncommitted(
        self, thread_id: str, exchange_id: int, *, reason: str,
    ) -> list[ThreadReplyArrival]:
        """Atomic close/release of one exchange (inside the open transaction;
        caller commits and enqueues the returned catch-up tokens after
        commit).

        E CAS ``open -> released`` (a duplicate evaluation is a CAS miss with
        NO audit and NO mint — idempotent), deferral rows ``held -> released``,
        then the watermark-based catch-up: for EVERY pair with
        ``acknowledged < required`` and no queued/running slot, mint ONE
        queued REPLY covering the full contiguous unread range (the
        coverage-safe superset: frozen D(E), mid-E joiners, cohort members
        with residual unread). A pair whose live slot GENUINELY covers the
        released range is marked coalesced (no second token — at-most-one
        slots honored); a pair whose live slot does NOT cover it (a running
        wake with immutable ``running_through_seq < required`` — e.g. a
        pre-exchange claim) is durably marked catch-up-pending: exactly ONE
        post-slot catch-up is minted when the blocking slot reaches a
        terminal outcome (failed/timeout settlement or recovery
        terminalization), surviving daemon restart — never stranded.
        """
        now = _now().isoformat()
        cur = self._conn.execute(
            "UPDATE thread_reply_exchange SET state = 'released', "
            "closed_at = ?, close_reason = ? "
            "WHERE thread_id = ? AND exchange_id = ? AND state = 'open'",
            (now, reason, thread_id, exchange_id),
        )
        if cur.rowcount == 0:
            return []  # CAS miss — duplicate closure/reaper/settlement no-op
        self._conn.execute(
            "UPDATE thread_exchange_deferrals SET state = 'released', "
            "released_at = ? WHERE thread_id = ? AND exchange_id = ? "
            "AND state = 'held'",
            (now, thread_id, exchange_id),
        )
        self.insert_audit_log_uncommitted(
            task_id=thread_id, agent="founder",
            action="thread_exchange_closed",
            payload={
                "thread_id": thread_id, "exchange_id": exchange_id,
                "close_reason": reason, "closed_at": now,
            },
        )
        arrivals: list[ThreadReplyArrival] = []
        rows = self._conn.execute(
            "SELECT * FROM thread_reply_delivery_state WHERE thread_id = ? "
            "AND acknowledged_through_seq < required_through_seq",
            (thread_id,),
        ).fetchall()
        for row in rows:
            agent = row["agent_name"]
            ack = int(row["acknowledged_through_seq"] or 0)
            req = int(row["required_through_seq"] or 0)
            if row["queued_invocation_token"] or row["running_invocation_token"]:
                # Genuinely-covering predicate (TASK-6057): a live slot
                # coalesces ONLY when its durable coverage contains the
                # released range.
                #   * running slot — the claimed range is IMMUTABLE
                #     (running_through_seq snapshotted at claim): it covers
                #     iff running_through_seq >= required. A pre-arrival
                #     claim whose through-seq ends before the released range
                #     does NOT cover (reviewer TASK-6056 counterexample).
                #   * queued slot — covers at claim (running_through =
                #     required-at-claim; required is monotonic), so it
                #     always covers.
                # A non-covering live slot blocks immediate minting
                # (at-most-one slot invariant): the released deferral is
                # durably marked catchup-pending and its exactly-one
                # post-slot catch-up fires when the blocking slot reaches a
                # terminal outcome — failed/timeout settlement, or recovery
                # terminalization on restart. Never a second token.
                non_covering = (
                    row["running_invocation_token"] is not None
                    and (
                        row["running_through_seq"] is None
                        or int(row["running_through_seq"]) < req
                    )
                )
                if non_covering:
                    if self._mark_catchup_pending_uncommitted(
                        thread_id, exchange_id, agent,
                    ):
                        self.insert_audit_log_uncommitted(
                            task_id=thread_id, agent=agent,
                            action="thread_deferral_catchup_pending",
                            payload={
                                "thread_id": thread_id,
                                "exchange_id": exchange_id,
                                "agent_name": agent,
                                "from_seq": ack + 1,
                                "through_seq": req,
                                "reason": "old_wake_does_not_cover",
                            },
                        )
                    arrivals.append(ThreadReplyArrival(
                        agent_name=agent, invocation_token=None,
                        coalesced=True, from_seq=ack + 1, through_seq=req,
                    ))
                    continue
                # Genuinely covering wake: coalesce — never a second token.
                self._clear_catchup_pending_uncommitted(thread_id, agent)
                arrivals.append(ThreadReplyArrival(
                    agent_name=agent, invocation_token=None, coalesced=True,
                    from_seq=ack + 1, through_seq=req,
                ))
                continue
            token = self._mint_reply_invocation_uncommitted(
                thread_id, agent, ack + 1,
            )
            self._conn.execute(
                "UPDATE thread_reply_delivery_state SET "
                "queued_invocation_token = ?, updated_at = ? "
                "WHERE thread_id = ? AND agent_name = ?",
                (token, now, thread_id, agent),
            )
            # A direct mint satisfies any pending catch-up from an earlier
            # release (one range-covering wake covers all of them).
            self._clear_catchup_pending_uncommitted(thread_id, agent)
            self._emit_reply_wake_audit(
                thread_id=thread_id, agent_name=agent,
                action="thread_reply_wake_created",
                payload={
                    "agent_name": agent,
                    "from_seq": ack + 1,
                    "through_seq": req,
                    "token_prefix": token[:8],
                    "kind": "exchange_catch_up",
                },
            )
            self._conn.execute(
                "UPDATE thread_exchange_deferrals SET mint_token_prefix = ? "
                "WHERE thread_id = ? AND exchange_id = ? "
                "AND agent_name = ? AND state = 'released'",
                (token[:8], thread_id, exchange_id, agent),
            )
            self.insert_audit_log_uncommitted(
                task_id=thread_id, agent=agent,
                action="thread_deferral_released",
                payload={
                    "thread_id": thread_id, "exchange_id": exchange_id,
                    "agent_name": agent, "mint_token_prefix": token[:8],
                },
            )
            arrivals.append(ThreadReplyArrival(
                agent_name=agent, invocation_token=token, coalesced=False,
                from_seq=ack + 1, through_seq=req,
            ))
        # Deferral rows whose pair was already fully covered (pierce/ack==req)
        # are released with no mint — record the coalesced audit. Rows whose
        # catch-up is pending (a live non-covering slot at release) are
        # audited by ``thread_deferral_catchup_pending`` in the loop above,
        # never as coalesced.
        held = self._conn.execute(
            "SELECT agent_name FROM thread_exchange_deferrals "
            "WHERE thread_id = ? AND exchange_id = ? "
            "AND state = 'released' AND mint_token_prefix IS NULL "
            "AND catchup_pending = 0",
            (thread_id, exchange_id),
        ).fetchall()
        for drow in held:
            self.insert_audit_log_uncommitted(
                task_id=thread_id, agent=drow["agent_name"],
                action="thread_deferral_coalesced",
                payload={
                    "thread_id": thread_id, "exchange_id": exchange_id,
                    "agent_name": drow["agent_name"],
                },
            )
        return arrivals

    def _suppress_open_exchanges_uncommitted(
        self, thread_id: str, *, reason: str,
    ) -> None:
        """Suppress every OPEN exchange on a thread (abort / archive /
        corruption / whole-thread discard). No catch-up is ever minted — the
        human stopped. Deferral rows flip to suppressed. Idempotent CAS per
        exchange. Runs inside the open transaction."""
        now = _now().isoformat()
        rows = self._conn.execute(
            "SELECT exchange_id FROM thread_reply_exchange "
            "WHERE thread_id = ? AND state = 'open'",
            (thread_id,),
        ).fetchall()
        for r in rows:
            cur = self._conn.execute(
                "UPDATE thread_reply_exchange SET state = 'suppressed', "
                "closed_at = ?, close_reason = ? "
                "WHERE thread_id = ? AND exchange_id = ? AND state = 'open'",
                (now, reason, thread_id, r["exchange_id"]),
            )
            if cur.rowcount == 0:
                continue
            self._conn.execute(
                "UPDATE thread_exchange_deferrals SET state = 'suppressed' "
                "WHERE thread_id = ? AND exchange_id = ? AND state = 'held'",
                (thread_id, r["exchange_id"]),
            )
            self.insert_audit_log_uncommitted(
                task_id=thread_id, agent="founder",
                action="thread_exchange_closed",
                payload={
                    "thread_id": thread_id, "exchange_id": r["exchange_id"],
                    "close_reason": reason, "suppressed": True,
                },
            )

    def _evaluate_exchange_closure(
        self, thread_id: str,
    ) -> list[ThreadReplyArrival]:
        """Evaluate closure for the thread's OPEN exchange(s) inside the
        open transaction. Returns catch-up arrivals (tokens to enqueue after
        commit) when a close actually fired; [] otherwise.

        Closure fires when ANY of:
          1. quiescence + grace: no live cohort wake covering E AND
             now - last_activity_at >= EXCHANGE_GRACE_SECONDS (5 min),
          2. absolute bound: now - opened_at >= MAX_PRIORITY_WAIT_SECONDS
             (4 h, fail-open),
          3. corruption (reconcile/sweep) — handled by the sweep, not here.
        Idempotent: every evaluation is a CAS; duplicates are silent misses.
        """
        now = datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT * FROM thread_reply_exchange WHERE thread_id = ? "
            "AND state = 'open'",
            (thread_id,),
        ).fetchall()
        arrivals: list[ThreadReplyArrival] = []
        for ex in rows:
            open_seq = int(ex["open_seq"])
            cohort = self._exchange_cohort_uncommitted(ex)
            live = self._exchange_has_live_cohort_wake(
                thread_id, open_seq, cohort,
            )
            last_activity = datetime.fromisoformat(
                ex["last_activity_at"].replace("Z", "+00:00")
            )
            opened = datetime.fromisoformat(
                ex["opened_at"].replace("Z", "+00:00")
            )
            grace_elapsed = (now - last_activity).total_seconds() \
                >= EXCHANGE_GRACE_SECONDS
            absolute_elapsed = (now - opened).total_seconds() \
                >= MAX_PRIORITY_WAIT_SECONDS
            if absolute_elapsed:
                arrivals.extend(self._close_reply_exchange_uncommitted(
                    thread_id, int(ex["exchange_id"]),
                    reason="max_priority_wait",
                ))
            elif not live and grace_elapsed:
                arrivals.extend(self._close_reply_exchange_uncommitted(
                    thread_id, int(ex["exchange_id"]),
                    reason="quiescence",
                ))
        return arrivals

    def _sweep_corrupt_exchanges_uncommitted(self) -> list[ThreadReplyArrival]:
        """U5 — fail-closed corruption sweep over every open exchange (runs
        in the startup reconcile transaction; caller commits + enqueues).
        The five sweep classes:
          1. ``held`` deferral row whose exchange is not ``open`` → aligned
             to the exchange's terminal state;
          2. ``held`` deferral row with NO exchange row → suppressed +
             diagnostic, never minted;
          3. open E whose opening ``mentions_json`` is malformed/missing →
             suppressed + diagnostic (fail-closed; never blind-release);
          4. ``close_seq < open_seq`` or ``last_activity_at`` in the future →
             suppressed + diagnostic;
          5. non-int/negative seq fields → fail-closed parse (bool-before-int
             per MEM-304).
        Returns catch-up arrivals for exchanges closed by this sweep (rare;
        normally suppresses, never releases)."""
        now = _now().isoformat()
        arrivals: list[ThreadReplyArrival] = []
        # Class 1/2: orphaned or misaligned held deferral rows.
        held_rows = self._conn.execute(
            "SELECT d.*, e.state AS exchange_state "
            "FROM thread_exchange_deferrals d "
            "LEFT JOIN thread_reply_exchange e "
            "ON e.thread_id = d.thread_id AND e.exchange_id = d.exchange_id "
            "WHERE d.state = 'held'",
        ).fetchall()
        for d in held_rows:
            if d["exchange_state"] is None:
                self._conn.execute(
                    "UPDATE thread_exchange_deferrals SET state = 'suppressed' "
                    "WHERE thread_id = ? AND exchange_id = ? AND agent_name = ? "
                    "AND state = 'held'",
                    (d["thread_id"], d["exchange_id"], d["agent_name"]),
                )
                self.insert_audit_log_uncommitted(
                    task_id=d["thread_id"], agent=d["agent_name"],
                    action="thread_deferral_suppressed",
                    payload={
                        "thread_id": d["thread_id"],
                        "exchange_id": d["exchange_id"],
                        "agent_name": d["agent_name"],
                        "reason": "orphan_deferral_row",
                    },
                )
            elif d["exchange_state"] != "open":
                target = "released" if d["exchange_state"] == "released" \
                    else "suppressed"
                self._conn.execute(
                    "UPDATE thread_exchange_deferrals SET state = ? "
                    "WHERE thread_id = ? AND exchange_id = ? AND agent_name = ? "
                    "AND state = 'held'",
                    (target, d["thread_id"], d["exchange_id"], d["agent_name"]),
                )
        # Class 3/4/5: malformed open exchanges → suppress + diagnostic.
        opens = self._conn.execute(
            "SELECT * FROM thread_reply_exchange WHERE state = 'open'",
        ).fetchall()
        for ex in opens:
            thread_id = ex["thread_id"]
            exchange_id = int(ex["exchange_id"])
            try:
                open_seq = int(ex["open_seq"])
                close_seq = int(ex["close_seq"])
            except (TypeError, ValueError):
                open_seq, close_seq = -1, -2
            bad_seq = (
                isinstance(ex["open_seq"], bool)  # bool-before-int (MEM-304)
                or isinstance(ex["close_seq"], bool)
                or open_seq < 1 or close_seq < open_seq
            )
            cohort = self._exchange_cohort_uncommitted(ex)
            bad_mentions = not cohort
            try:
                last_activity = _parse_dt(ex["last_activity_at"])
                future = last_activity > datetime.now(timezone.utc)
            except (TypeError, ValueError):
                future = True
            if bad_seq or bad_mentions or future:
                self._suppress_open_exchanges_uncommitted(
                    thread_id, reason="corrupt",
                )
                self.insert_audit_log_uncommitted(
                    task_id=thread_id, agent="founder",
                    action="thread_exchange_corrupt",
                    payload={
                        "thread_id": thread_id, "exchange_id": exchange_id,
                        "reason": (
                            "bad_seq" if bad_seq else
                            "bad_mentions" if bad_mentions else "future_activity"
                        ),
                    },
                )
        return arrivals

    @_synchronized
    def reconcile_reply_exchanges(self) -> list[ThreadReplyArrival]:
        """U5 — startup exchange reconcile (daemon step 6e). One transaction:
        corruption sweep first (fail-closed), then idempotent closure
        evaluation for every open exchange. Returns catch-up arrivals whose
        tokens the caller enqueues after commit."""
        arrivals: list[ThreadReplyArrival] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            arrivals.extend(self._sweep_corrupt_exchanges_uncommitted())
            for ex in self._conn.execute(
                "SELECT DISTINCT thread_id FROM thread_reply_exchange "
                "WHERE state = 'open'",
            ).fetchall():
                arrivals.extend(self._evaluate_exchange_closure(ex["thread_id"]))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return arrivals

    @_synchronized
    def reaper_sweep_reply_exchanges(self) -> list[ThreadReplyArrival]:
        """U4 — reaper tick over every OPEN exchange (fail-open backstop).
        Closes exchanges past the 4h absolute bound or quiescence+grace;
        returns catch-up arrivals whose tokens the caller enqueues after
        commit. CAS-protected: a concurrent close is a silent miss."""
        arrivals: list[ThreadReplyArrival] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for ex in self._conn.execute(
                "SELECT DISTINCT thread_id FROM thread_reply_exchange "
                "WHERE state = 'open'",
            ).fetchall():
                arrivals.extend(self._evaluate_exchange_closure(ex["thread_id"]))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return arrivals

    @_synchronized
    def list_reply_exchange_projections(
        self, thread_id: str,
    ) -> list[ThreadReplyExchangeProjection]:
        """Exchange-level wire projection for a thread (server contract).
        Returns the most recent exchange row in any state; empty for threads
        that never opened an exchange. Truthful — never fabricated."""
        rows = self._conn.execute(
            "SELECT * FROM thread_reply_exchange WHERE thread_id = ? "
            "ORDER BY exchange_id DESC LIMIT 1",
            (thread_id,),
        ).fetchall()
        out: list[ThreadReplyExchangeProjection] = []
        for row in rows:
            out.append(ThreadReplyExchangeProjection(
                thread_id=row["thread_id"],
                exchange_id=int(row["exchange_id"]),
                state=row["state"],  # type: ignore[arg-type]
                open_seq=int(row["open_seq"]),
                close_seq=int(row["close_seq"]),
                opened_at=row["opened_at"],
                last_activity_at=row["last_activity_at"],
                closed_at=row["closed_at"],
                close_reason=row["close_reason"],
                deferred_count=int(row["deferred_count"] or 0),
            ))
        return out
