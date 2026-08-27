"""Deterministic fixtures for the THR-198 Phase-2 Slice D release-measurement
harness (``runtime/infrastructure/thread_release_measurement.py``).

Every test builds an in-memory SQLite database mirroring the subset of the
real schema the harness reads (``threads`` / ``thread_participants`` /
``thread_messages`` / ``thread_invocations``) with exact timestamps, so all
counts and rates are deterministic. No test touches a file DB or the live
org database.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from runtime.infrastructure import thread_release_measurement as m

EPOCH = "2026-08-26T14:25:23Z"
WINDOW_END = "2026-09-26T14:25:23Z"  # epoch + 1 calendar month
AUG_START = "2026-08-01T00:00:00Z"
AUG_END = "2026-09-01T00:00:00Z"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            mention_routing_enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE thread_participants (
            thread_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (thread_id, agent_name)
        );
        CREATE TABLE thread_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            speaker TEXT NOT NULL,
            kind TEXT NOT NULL,
            body_markdown TEXT,
            mentions_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE thread_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            invocation_token TEXT NOT NULL UNIQUE,
            triggering_seq INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            consumed_at TEXT,
            decline_reason TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT,
            timestamp TEXT NOT NULL
        );
        """
    )
    return conn


def _add_thread(conn, thread_id: str, *, mention_routing_enabled: int = 1) -> None:
    conn.execute(
        "INSERT INTO threads (id, mention_routing_enabled) VALUES (?, ?)",
        (thread_id, mention_routing_enabled),
    )


def _add_participant(conn, thread_id: str, agent: str, added_at: str) -> None:
    conn.execute(
        "INSERT INTO thread_participants (thread_id, agent_name, added_at) "
        "VALUES (?, ?, ?)",
        (thread_id, agent, added_at),
    )


def _add_message(
    conn, thread_id: str, seq: int, speaker: str, created_at: str, *,
    kind: str = "message", body: str | None = None,
    mentions: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
        "body_markdown, mentions_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, seq, speaker, kind, body, mentions, created_at),
    )


def _add_invocation(
    conn, thread_id: str, agent: str, triggering_seq: int, enqueued_at: str, *,
    purpose: str = "reply", status: str = "consumed",
    token: str | None = None, consumed_at: str | None = None,
) -> None:
    invocation_token = token or f"tok-{thread_id}-{agent}-{triggering_seq}-{enqueued_at}"
    if consumed_at is None and status in ("consumed", "declined"):
        # Production stamps consumed_at in the SAME UPDATE that settles an
        # invocation — the reply path (status='consumed') and every decline
        # path (status='declined') alike; the schema has no separate
        # declined_at column. Default to enqueued_at so a fixture that only
        # says "settled immediately at enqueue" is production-faithful
        # (consumed_at non-null). Tests that exercise the pre-cutoff-wake /
        # declined-at-or-after-cutoff seam pass consumed_at explicitly.
        consumed_at = enqueued_at
    conn.execute(
        "INSERT INTO thread_invocations (thread_id, agent_name, invocation_token, "
        "triggering_seq, purpose, status, enqueued_at, consumed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, agent, invocation_token, triggering_seq, purpose, status,
         enqueued_at, consumed_at),
    )


def _add_claim_audit(
    conn, thread_id: str, agent: str, token: str, from_seq: int, through_seq: int,
    timestamp: str,
) -> None:
    """Insert the authoritative immutable claimed-range audit the harness
    reads for coalesced REPLY coverage (mirrors
    ``Database.claim_conversational_reply``'s ``thread_reply_wake_claimed``
    payload shape: agent, from_seq, through_seq, 8-char token_prefix)."""
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, 'thread_reply_wake_claimed', ?, ?)",
        (thread_id, agent,
         json.dumps({
             "agent_name": agent,
             "from_seq": from_seq,
             "through_seq": through_seq,
             "token_prefix": token[:8],
         }),
         timestamp),
    )


def _add_created_audit(
    conn, thread_id: str, agent: str, token: str, from_seq: int, through_seq: int,
    timestamp: str,
) -> None:
    """Insert a ``thread_reply_wake_created`` mint audit (mirrors
    ``Database._apply_arrival_uncommitted``: agent, from_seq, through_seq,
    8-char token_prefix). In production ``through_seq`` is the seq of the
    message arrival that minted the wake — the creating arrival."""
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, 'thread_reply_wake_created', ?, ?)",
        (thread_id, agent,
         json.dumps({
             "agent_name": agent,
             "from_seq": from_seq,
             "through_seq": through_seq,
             "token_prefix": token[:8],
         }),
         timestamp),
    )


def _add_coalesced_audit(
    conn, thread_id: str, agent: str, from_seq: int, through_seq: int,
    timestamp: str,
) -> None:
    """Insert a ``thread_reply_wake_coalesced`` audit (mirrors
    ``Database._apply_arrival_uncommitted``: NO token_prefix, NO mint — the
    arrival only raised the pair's required watermark on the existing wake)."""
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, 'thread_reply_wake_coalesced', ?, ?)",
        (thread_id, agent,
         json.dumps({
             "agent_name": agent,
             "from_seq": from_seq,
             "through_seq": through_seq,
         }),
         timestamp),
    )


def _add_recovered_audit(
    conn, thread_id: str, agent: str, token: str, kind: str,
    from_seq: int, through_seq: int, timestamp: str,
) -> None:
    """Insert a ``thread_reply_wake_recovered`` recovery audit (mirrors
    ``Database.recover_thread_reply_delivery_state``: agent, kind
    ``replacement_queued`` or ``retained_queued``, from_seq, through_seq,
    8-char token_prefix). A ``replacement_queued`` recovery MINTED the token
    at restart (it has no created audit); a ``retained_queued`` recovery kept
    the pre-existing queued receipt (which carries its own created audit)."""
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, 'thread_reply_wake_recovered', ?, ?)",
        (thread_id, agent,
         json.dumps({
             "agent_name": agent,
             "kind": kind,
             "from_seq": from_seq,
             "through_seq": through_seq,
             "token_prefix": token[:8],
         }),
         timestamp),
    )


def _add_settled_audit(
    conn, thread_id: str, agent: str, token: str, outcome: str,
    acked: int, required: int, follow_on_token: str | None, timestamp: str,
) -> None:
    """Insert a ``thread_reply_wake_settled`` audit (mirrors
    ``Database._settle_reply_uncommitted``). A follow-on wake minted at
    settlement is referenced ONLY by ``follow_on_token_prefix`` — it has no
    ``thread_reply_wake_created`` audit."""
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, 'thread_reply_wake_settled', ?, ?)",
        (thread_id, agent,
         json.dumps({
             "agent_name": agent,
             "outcome": outcome,
             "acknowledged_through_seq": acked,
             "required_through_seq": required,
             "retry_required": follow_on_token is None and required > acked,
             "follow_on_token_prefix": (
                 follow_on_token[:8] if follow_on_token else None
             ),
         }),
         timestamp),
    )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def test_add_calendar_months_clamps_to_month_end() -> None:
    assert m.add_calendar_months(m.parse_timestamp("2026-08-26T14:25:23Z"), 1) \
        == m.parse_timestamp("2026-09-26T14:25:23Z")
    # Jan 31 -> Feb 28 (2026 is not a leap year)
    assert m.add_calendar_months(m.parse_timestamp("2026-01-31T00:00:00Z"), 1) \
        == m.parse_timestamp("2026-02-28T00:00:00Z")
    # Year rollover
    assert m.add_calendar_months(m.parse_timestamp("2026-12-15T00:00:00Z"), 1) \
        == m.parse_timestamp("2027-01-15T00:00:00Z")


def test_parse_timestamp_accepts_z_and_offset() -> None:
    assert m.parse_timestamp("2026-08-26T14:25:23Z") == \
        m.parse_timestamp("2026-08-26T14:25:23+00:00")
    assert m.parse_timestamp("2026-08-26T14:25:23.500000+00:00").microsecond == 500000


# ---------------------------------------------------------------------------
# Live window — populations and edge cases
# ---------------------------------------------------------------------------

def test_live_mentioned_wake_counts_and_rate() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # Wake for the mentioned agent (declined) + a broadcast wake to bob
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="declined")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-27T00:01:01Z",
                    status="consumed")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.interim is True
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 2
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 50.0
    assert live.org_wakes == 2
    assert live.org_declines == 1
    assert live.org_decline_rate_pct == 50.0
    assert live.malformed_mentions_json == 0


def test_live_mentioned_attribution_uses_creating_arrival_not_range_floor() -> None:
    """[adversarial regression — the confirmed live-measurement defect]
    GH-688 Phase-1 coalescing makes ``triggering_seq`` the retained range
    floor (``acknowledged+1``), so a later unmentioned broadcast wake can be
    falsely attributed to an earlier mentioned arrival. A mention-routed
    message is immediately followed by an unmentioned broadcast; the
    non-mentioned agent's later wake has the mentioned seq as its retained
    range floor but was MINTED by the broadcast arrival — it must be EXCLUDED
    from the live mentioned population (its decline never counts in G1),
    while the mention-minted wake stays counted and the broadcast decline
    stays org-wide."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    # seq 10: mention-routed message (@alice) — wakes ONLY alice.
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # seq 11: unmentioned broadcast — wakes everyone, including bob.
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # alice's wake: minted by the seq-10 mention arrival, covering [10,10].
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="consumed")
    _add_created_audit(conn, "T1", "alice", "aaa11111", 10, 10,
                       "2026-08-27T00:01:00Z")
    # bob's wake: minted by the seq-11 broadcast arrival covering [10,11];
    # triggering_seq = 10 = the retained range floor (the mentioned seq).
    _add_invocation(conn, "T1", "bob", 10, "2026-08-27T00:01:30Z",
                    token="bbb22222", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "bob", "bbb22222", 10, 11,
                       "2026-08-27T00:01:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    # bob's broadcast-created wake is NOT in the mentioned population.
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 0
    assert live.mentioned_decline_rate_pct == 0.0
    # Both wakes stay in the org-wide population; bob's decline counts there.
    assert live.org_wakes == 2
    assert live.org_declines == 1
    assert live.org_decline_rate_pct == 50.0


def test_live_mint_at_mentioned_arrival_with_broadcast_floor_included() -> None:
    """[created semantics] A wake minted by a MENTIONED arrival whose range
    floor is an earlier unmentioned broadcast message is INCLUDED in the live
    mentioned population — the creating arrival (the mention) is the
    attribution key, never the floor. The pre-fix code under-attributed it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # seq 10: unmentioned broadcast (alice's pair ack still at 9).
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    # seq 11: mention-routed message (@alice).
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions='["alice"]')
    # alice's wake: minted by the seq-11 mention, floor = acknowledged+1 = 10.
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "alice", "aaa11111", 10, 11,
                       "2026-08-27T00:01:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1  # only seq 11
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1


def test_live_coalesced_broadcast_does_not_reattribute_existing_wake() -> None:
    """[coalesced semantics] A ``thread_reply_wake_coalesced`` audit carries
    no token and mints nothing: an unmentioned broadcast coalescing onto an
    existing mentioned wake neither adds a second wake nor flips the wake's
    attribution — the wake stays attributed to the mention that minted it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # alice's wake minted by the seq-10 mention [10,10], then the seq-11
    # broadcast only RAISED required (coalesced audit, no new invocation).
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "alice", "aaa11111", 10, 10,
                       "2026-08-27T00:01:00Z")
    _add_coalesced_audit(conn, "T1", "alice", 10, 11,
                         "2026-08-27T00:00:31Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    # Exactly ONE wake exists; it is the mention-minted wake, still counted.
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.org_wakes == 1


def test_live_mention_coalesced_onto_broadcast_wake_stays_broadcast() -> None:
    """[coalesced semantics] A mention that coalesces onto an already-pending
    broadcast wake does NOT make that wake a mentioned wake — the wake's
    existence traces to the broadcast arrival that minted it; the later
    mention only raised the required watermark (no new wake, no
    re-attribution)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 9, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:30Z",
                 mentions='["alice"]')
    # alice's wake minted by the seq-9 broadcast [9,9]; the seq-10 mention
    # coalesced onto it (coalesced audit [9,10]) — no second invocation.
    _add_invocation(conn, "T1", "alice", 9, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "alice", "aaa11111", 9, 9,
                       "2026-08-27T00:01:00Z")
    _add_coalesced_audit(conn, "T1", "alice", 9, 10,
                         "2026-08-27T00:00:31Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1  # seq 10 IS a mentioned message
    # ...but the only wake was minted by the seq-9 broadcast: not mentioned.
    assert live.mentioned_wakes == 0
    assert live.mentioned_decline_rate_pct is None
    assert live.org_wakes == 1
    assert live.org_declines == 1


def test_live_recovery_replacement_uses_recovered_through_seq_not_floor() -> None:
    """[adversarial recovery regression — the blocking review finding]
    A replacement minted by restart recovery has NO created audit; its only
    authoritative evidence is the ``thread_reply_wake_recovered``
    (``replacement_queued``) audit, whose ``through_seq`` is the pair's
    required watermark at recovery — the actual wake-causing arrival. In the
    production-faithful case the retained floor seq 10 is a MENTIONED message
    while the later wake-causing seq 11 is an unmentioned broadcast: the
    replacement (triggering_seq/from_seq 10, recovered through_seq 11) must be
    EXCLUDED from G1 (its decline never counts) while G3 still includes it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # seq 10: mentioned message (the retained range floor).
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # seq 11: unmentioned broadcast — the actual wake-causing arrival.
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # Recovery replacement: triggering_seq = 10 (floor), recovered 10..11.
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="rrr11111", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    _add_recovered_audit(conn, "T1", "alice", "rrr11111", "replacement_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    # Attributed to the recovered through_seq 11 (broadcast) → not mentioned.
    assert live.mentioned_messages == 1  # seq 10 IS mentioned...
    assert live.mentioned_wakes == 0  # ...but the wake's creating arrival is 11
    assert live.mentioned_decline_rate_pct is None
    # The replacement still counts org-wide; its decline counts in G3.
    assert live.org_wakes == 1
    assert live.org_declines == 1
    assert live.org_decline_rate_pct == 100.0


def test_live_recovery_replacement_with_mentioned_through_seq_included() -> None:
    """[converse recovery regression] The recovered through_seq is the
    authoritative wake-causing arrival: when it IS a mentioned message, the
    recovery replacement is a mentioned wake — even though the retained floor
    is an unmentioned broadcast. The pre-fix floor fallback would have
    excluded it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # seq 10: unmentioned broadcast (the retained range floor).
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    # seq 11: mentioned message — the actual wake-causing arrival.
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="sss22222", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    _add_recovered_audit(conn, "T1", "alice", "sss22222", "replacement_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1
    assert live.org_declines == 1


def test_live_recovered_retained_queued_never_reattributes_created_wake() -> None:
    """[retained precedence] A ``retained_queued`` recovery keeps the
    pre-existing receipt — which carries its own created audit. The recovered
    audit's ``through_seq`` (the recovery-time required watermark) must NOT
    re-attribute the wake to a later coalesced arrival: created evidence wins,
    so the wake stays attributed to the mention that minted it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # Wake minted by the seq-10 mention [10,10]; a seq-11 broadcast coalesced
    # onto it (required 10->11), then the daemon restarted and the queued
    # receipt was RETAINED (recovered audit [10,11], same token).
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="ttt33333", status="declined",
                    consumed_at="2026-08-27T00:04:00Z")
    _add_created_audit(conn, "T1", "alice", "ttt33333", 10, 10,
                       "2026-08-27T00:01:00Z")
    _add_recovered_audit(conn, "T1", "alice", "ttt33333", "retained_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    # Still attributed to the seq-10 mention (created audit): a mentioned wake.
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1


def test_live_recovery_created_evidence_takes_precedence_over_recovered() -> None:
    """[precedence boundary] When BOTH a created audit and a recovery audit
    name the same token prefix, the created audit's through_seq is the
    attribution key (the authoritative minting arrival) — recovery evidence
    is consulted only for wakes the created trail cannot attribute."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="uuu44444", status="declined",
                    consumed_at="2026-08-27T00:04:00Z")
    _add_created_audit(conn, "T1", "alice", "uuu44444", 10, 10,
                       "2026-08-27T00:01:00Z")
    # Double-evidence row (defensive; production mints replacements with no
    # created audit): created says minted at 10 (mentioned), recovery
    # watermark at 11 (broadcast) — created wins.
    _add_recovered_audit(conn, "T1", "alice", "uuu44444", "replacement_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1


def test_live_recovery_replacement_malformed_payload_falls_back_to_floor() -> None:
    """[malformed boundary] A recovery audit with an unparseable payload
    attributes nothing — the replacement keeps the genuine legacy fallback
    (``triggering_seq``), never a fabricated seq. Production emits
    well-formed payloads; this guards the harness against data defects."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="vvv55555", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES ('T1', 'alice', 'thread_reply_wake_recovered', ?, "
        "'2026-08-27T00:02:00Z')",
        ("{not-json",),
    )

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    # Unattributable → floor fallback (pre-fix behavior preserved).
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0


def test_live_recovery_replacement_missing_fields_falls_back_to_floor() -> None:
    """[missing-field boundary] Recovery payloads missing ``kind``,
    ``token_prefix``, or ``through_seq`` never fabricate an attribution — the
    replacement keeps the legacy ``triggering_seq`` fallback. A
    ``retained_queued`` kind is never consumed as mint evidence."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="www66666", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    # retained_queued kind (not a mint): must NOT be consumed as evidence.
    _add_recovered_audit(conn, "T1", "alice", "www66666", "retained_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1

    # Missing token_prefix: no attribution.
    conn2 = _conn()
    _add_thread(conn2, "T1")
    _add_participant(conn2, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn2, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn2, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="xxx77777", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    conn2.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES ('T1', 'alice', 'thread_reply_wake_recovered', ?, "
        "'2026-08-27T00:02:00Z')",
        (json.dumps({"agent_name": "alice", "kind": "replacement_queued",
                     "from_seq": 10, "through_seq": 11}),),
    )
    live2 = m.measure_live_window(conn2, epoch=EPOCH,
                                  as_of="2026-08-27T12:00:00Z")
    assert live2.mentioned_wakes == 1

    # Missing through_seq: no attribution.
    conn3 = _conn()
    _add_thread(conn3, "T1")
    _add_participant(conn3, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn3, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn3, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="yyy88888", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    conn3.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES ('T1', 'alice', 'thread_reply_wake_recovered', ?, "
        "'2026-08-27T00:02:00Z')",
        (json.dumps({"agent_name": "alice", "kind": "replacement_queued",
                     "from_seq": 10, "token_prefix": "yyy88888"}),),
    )
    live3 = m.measure_live_window(conn3, epoch=EPOCH,
                                  as_of="2026-08-27T12:00:00Z")
    assert live3.mentioned_wakes == 1


def test_live_recovered_retained_queued_legacy_row_keeps_floor_fallback() -> None:
    """[legacy boundary] A ``retained_queued`` recovery whose receipt has NO
    created audit (a legacy pre-audit row) is genuinely unattributable: the
    recovered audit is NOT consumed for attribution, so the row keeps the
    documented ``triggering_seq`` fallback (a mentioned floor is counted)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="qqq00000", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    _add_recovered_audit(conn, "T1", "alice", "qqq00000", "retained_queued",
                         10, 11, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1  # floor fallback: seq 10 IS mentioned
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1


def test_live_recovery_payload_never_leaks_across_threads_or_tokens() -> None:
    """[unrelated boundary] A recovery audit only ever attributes the token
    it names within its own thread: a same-prefix audit in a DIFFERENT thread
    never re-attributes another wake, and a follow-on row in the same thread
    keeps its genuine floor fallback (follow-on semantics are not
    reattributed)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_thread(conn, "T2")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T2", "alice", "2026-08-01T00:00:00Z")
    # T1: mentioned seq 5; follow-on wake minted at settlement (no created,
    # no recovered audit) — floor fallback, counted.
    _add_message(conn, "T1", 5, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 5, "2026-08-27T00:01:00Z",
                    token="zzz9999a-1", status="consumed",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_settled_audit(conn, "T1", "alice", "eee44444", "reply",
                       4, 5, "zzz9999a-1", "2026-08-27T00:01:00Z")
    # T2: a recovery replacement whose 8-char prefix collides with T1's
    # follow-on token prefix — must NOT attribute T1's wake (audits are
    # thread-scoped). Its own attribution is the recovered through_seq 1
    # (broadcast → not mentioned).
    _add_message(conn, "T2", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    _add_invocation(conn, "T2", "alice", 1, "2026-08-27T00:02:00Z",
                    token="zzz9999a-2", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    _add_recovered_audit(conn, "T2", "alice", "zzz9999a-2", "replacement_queued",
                         1, 1, "2026-08-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    # T1's follow-on wake is unaffected by T2's same-prefix recovery audit
    # (thread-scoped keying): floor fallback → mentioned → counted.
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 0
    assert live.org_wakes == 2  # T1 follow-on + T2 replacement
    assert live.org_declines == 1


def test_live_recovery_non_integer_through_seq_fails_closed() -> None:
    """[adversarial regression — TASK-5893 HIGH finding #1] A syntactically
    valid ``replacement_queued`` payload whose ``through_seq`` is NOT a
    positive JSON integer must never crash the measurement: the row is
    skipped (fail closed) and the replacement keeps the genuine legacy
    fallback (``triggering_seq``). Variants: non-integer string (the
    reviewer's probe), numeric string, float, boolean, zero, negative,
    missing."""
    malformed = ["not-an-int", "11", 11.0, True, 0, -5, None]
    for through_seq in malformed:
        conn = _conn()
        _add_thread(conn, "T1")
        _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
        _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                     mentions='["alice"]')
        _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                        token="rrr11111", status="declined",
                        consumed_at="2026-08-27T00:03:00Z")
        payload = {"agent_name": "alice", "kind": "replacement_queued",
                   "from_seq": 10, "token_prefix": "rrr11111"}
        if through_seq is not None:
            payload["through_seq"] = through_seq
        conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, "
            "timestamp) VALUES ('T1', 'alice', "
            "'thread_reply_wake_recovered', ?, '2026-08-27T00:02:00Z')",
            (json.dumps(payload),),
        )
        # Must not raise; the malformed recovery evidence is skipped and the
        # replacement keeps the floor fallback (seq 10 IS mentioned).
        live = m.measure_live_window(conn, epoch=EPOCH,
                                     as_of="2026-08-27T12:00:00Z")
        assert live.mentioned_wakes == 1, f"through_seq={through_seq!r}"
        assert live.mentioned_declines == 1, f"through_seq={through_seq!r}"
        assert live.mentioned_decline_rate_pct == 100.0
        assert live.org_wakes == 1
        assert live.org_declines == 1


def test_live_recovery_invalid_range_shape_fails_closed() -> None:
    """[adversarial regression — TASK-5893 HIGH finding #1] ``replacement_queued``
    payloads whose range is internally invalid (inverted ``from_seq >
    through_seq``), non-positive, or carries a non-integer/boolean boundary
    are skipped without exception — the replacement keeps the floor
    fallback, never a fabricated attribution, and the measurement never
    crashes."""
    variants = [
        (12, 11),     # inverted range
        (0, 11),      # non-positive from_seq
        (10, 0),      # non-positive through_seq
        ("oops", 11),  # non-integer from_seq
        (True, 11),   # boolean-like from_seq
        (10, None),   # missing through_seq
    ]
    for from_seq, through_seq in variants:
        conn = _conn()
        _add_thread(conn, "T1")
        _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
        _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                     mentions='["alice"]')
        _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                        token="rrr11111", status="declined",
                        consumed_at="2026-08-27T00:03:00Z")
        payload = {"agent_name": "alice", "kind": "replacement_queued",
                   "token_prefix": "rrr11111"}
        if from_seq is not None:
            payload["from_seq"] = from_seq
        if through_seq is not None:
            payload["through_seq"] = through_seq
        conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, "
            "timestamp) VALUES ('T1', 'alice', "
            "'thread_reply_wake_recovered', ?, '2026-08-27T00:02:00Z')",
            (json.dumps(payload),),
        )
        live = m.measure_live_window(conn, epoch=EPOCH,
                                     as_of="2026-08-27T12:00:00Z")
        assert live.mentioned_wakes == 1, f"{from_seq=} {through_seq=}"
        assert live.mentioned_declines == 1, f"{from_seq=} {through_seq=}"
        assert live.org_wakes == 1


def test_live_created_malformed_payload_fails_closed() -> None:
    """[adversarial regression] The created-audit map parses fail-closed too:
    a ``thread_reply_wake_created`` payload with a non-integer ``through_seq``
    or an internally invalid range is skipped without crashing — the wake
    keeps the genuine floor fallback (no fabricated attribution)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    # Non-integer through_seq AND an inverted-range variant, same token.
    for payload in (
        {"agent_name": "alice", "from_seq": 10, "through_seq": "nope",
         "token_prefix": "aaa11111"},
        {"agent_name": "alice", "from_seq": 12, "through_seq": 10,
         "token_prefix": "aaa11111"},
    ):
        conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, "
            "timestamp) VALUES ('T1', 'alice', "
            "'thread_reply_wake_created', ?, '2026-08-27T00:01:00Z')",
            (json.dumps(payload),),
        )
    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1  # floor fallback: seq 10 IS mentioned
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1


def test_live_ownership_tuple_resolves_same_prefix_cross_agent_collision() -> None:
    """[adversarial regression — TASK-5893 HIGH finding #2, positive
    ownership] Ownership is the production tuple ``(thread_id, agent_name,
    token_prefix)``: TWO agents in the SAME thread with the SAME 8-char
    prefix are attributed ONLY by their own audit evidence. alice's wake
    (created audit at mentioned seq 10) is unaffected by bob's recovery
    audit naming the same prefix with a broadcast ``through_seq`` 11, and
    bob's own wake (no created audit) is attributed to HIS recovered
    ``through_seq`` 11 (broadcast → not mentioned). No weaker key may merge
    the two ownerships."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_message(conn, "T1", 11, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # alice's wake: minted by the seq-10 mention; created audit [10,10].
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="abc12345-a1", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "alice", "abc12345-a1", 10, 10,
                       "2026-08-27T00:01:00Z")
    # bob's replacement: SAME 8-char prefix, recovered through_seq 11.
    _add_invocation(conn, "T1", "bob", 10, "2026-08-27T00:03:00Z",
                    token="abc12345-b2", status="declined",
                    consumed_at="2026-08-27T00:04:00Z")
    _add_recovered_audit(conn, "T1", "bob", "abc12345-b2",
                         "replacement_queued", 10, 11,
                         "2026-08-27T00:03:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    # alice's wake stays attributed to the seq-10 mention; bob's wake is
    # attributed to HIS recovered through_seq 11 (broadcast → not mentioned).
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 2
    assert live.org_declines == 2
    assert live.org_decline_rate_pct == 100.0


def test_live_same_prefix_other_agent_recovery_audit_does_not_reattribute() -> None:
    """[adversarial regression — the reviewer's Bob-changes-Alice probe] A
    recovery audit owned by a DIFFERENT agent in the same thread with the
    same 8-char prefix must NOT reattribute the target agent's wake: alice's
    replacement (no audits of her own) keeps the genuine floor fallback
    (mentioned seq 10), even though bob's audit names the same prefix with a
    broadcast ``through_seq`` 11. The pre-fix ``(thread_id, token_prefix)``
    key let bob's audit flip alice's G1 wake out of the mentioned
    population."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # alice's replacement: no created audit, no recovered audit of her own.
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:02:00Z",
                    token="def45678-a1", status="declined",
                    consumed_at="2026-08-27T00:03:00Z")
    # bob's unrelated recovery audit with the SAME 8-char prefix.
    _add_recovered_audit(conn, "T1", "bob", "def45678-b2",
                         "replacement_queued", 10, 11,
                         "2026-08-27T00:02:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    # alice keeps her floor fallback: seq 10 IS mentioned → counted in G1.
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1
    assert live.org_declines == 1


def test_live_same_prefix_other_agent_created_audit_does_not_reattribute() -> None:
    """[adversarial regression — created-map ownership] The created map is
    scoped by the same ownership tuple: a ``thread_reply_wake_created`` audit
    owned by a DIFFERENT agent in the same thread with the same 8-char
    prefix cannot reattribute alice's wake. alice's wake (no own audits)
    keeps the floor fallback (mentioned seq 10) despite bob's created audit
    pointing at broadcast seq 11."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 10, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 10, "2026-08-27T00:01:00Z",
                    token="abc12345-a1", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "bob", "abc12345-b2", 10, 11,
                       "2026-08-27T00:01:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.org_wakes == 1
    assert live.org_declines == 1


def test_live_claimed_malformed_range_fails_closed() -> None:
    """[adversarial regression — coverage path] A ``thread_reply_wake_claimed``
    payload with a non-integer range boundary or an internally inverted
    range is skipped without crashing: the consumed invocation then covers
    ONLY its own ``triggering_seq`` (the honest under-approximation) — the
    malformed row contributes no fabricated coverage to G2."""
    for payload in (
        {"agent_name": "alice", "from_seq": 1, "through_seq": "oops",
         "token_prefix": "aaa11111"},
        {"agent_name": "alice", "from_seq": 5, "through_seq": 1,
         "token_prefix": "aaa11111"},
    ):
        conn = _conn()
        _add_thread(conn, "T1")
        _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
        _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
        _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
        _add_message(conn, "T1", 3, "founder", "2026-08-27T00:02:00Z")
        _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:03:00Z",
                        token="aaa11111", status="consumed",
                        consumed_at="2026-08-27T00:04:00Z")
        conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, "
            "timestamp) VALUES ('T1', 'alice', "
            "'thread_reply_wake_claimed', ?, '2026-08-27T00:03:30Z')",
            (json.dumps(payload),),
        )
        live = m.measure_live_window(conn, epoch=EPOCH,
                                     as_of="2026-08-27T12:00:00Z")
        # Only seq 1 (the fallback triggering_seq) is covered — no fabricated
        # coverage from the malformed claim, no crash.
        assert live.founder_messages == 3
        assert live.founder_covered == 1
        assert live.founder_uncovered == 2


def test_live_followon_wake_with_mentioned_floor_is_counted() -> None:
    """[fallback semantics] A follow-on wake minted at settlement has NO
    ``thread_reply_wake_created`` audit — it is referenced only by the
    settled audit's ``follow_on_token_prefix``. Its floor (``triggering_seq``
    = running_through + 1) is a genuine wake-causing arrival (the retained
    range contains only arrivals that raised the pair's required watermark),
    so attribution falls back to the floor: a mentioned floor is counted."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # seq 5: mention-routed (@alice). Its arrival raised required during a run.
    _add_message(conn, "T1", 5, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # The follow-on wake minted at settlement covers [5,5]; no created audit.
    _add_invocation(conn, "T1", "alice", 5, "2026-08-27T00:01:00Z",
                    token="fff55555", status="consumed",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_settled_audit(conn, "T1", "alice", "eee44444", "reply",
                       4, 5, "fff55555", "2026-08-27T00:01:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 0
    assert live.org_wakes == 1


def test_live_followon_wake_with_broadcast_floor_not_counted() -> None:
    """[fallback negative] A follow-on wake whose floor is an unmentioned
    broadcast message is not a mentioned wake (its retained arrivals were all
    broadcasts) — the floor fallback correctly keeps it out of G1."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 5, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    _add_invocation(conn, "T1", "alice", 5, "2026-08-27T00:01:00Z",
                    token="fff55555", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_settled_audit(conn, "T1", "alice", "eee44444", "reply",
                       4, 5, "fff55555", "2026-08-27T00:01:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 0
    assert live.mentioned_decline_rate_pct is None
    assert live.org_wakes == 1
    assert live.org_declines == 1


def test_live_legacy_wake_without_any_audit_falls_back_to_floor() -> None:
    """[negative/fallback] A REPLY wake with NO created audit (legacy
    pre-audit row) falls back to ``triggering_seq`` — preserving the prior
    harness behavior for rows the audit trail cannot attribute. This is the
    documented approximation, never a fabrication."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # No audits at all for this wake (legacy row): floor attribution.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0


def test_live_creating_arrival_not_mentioned_never_fabricates() -> None:
    """[negative] A wake whose creating arrival carries a malformed
    mentions_json (or no in-window message at all) is never counted as a
    mentioned wake — the malformed signal is a diagnostic, never fabricated
    into a mention; a missing creating message is simply not mentioned."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # seq 7: malformed mentions_json (creating arrival for wake A).
    _add_message(conn, "T1", 7, "founder", "2026-08-27T00:00:00Z",
                 mentions="not-json")
    # wake A: minted at seq 7, floor 7 — creating arrival malformed.
    _add_invocation(conn, "T1", "alice", 7, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T00:02:00Z")
    _add_created_audit(conn, "T1", "alice", "aaa11111", 7, 7,
                       "2026-08-27T00:01:00Z")
    # wake B: created audit points to seq 99 — no in-window message there.
    _add_invocation(conn, "T1", "alice", 98, "2026-08-27T00:03:00Z",
                    token="bbb22222", status="declined",
                    consumed_at="2026-08-27T00:04:00Z")
    _add_created_audit(conn, "T1", "alice", "bbb22222", 98, 99,
                       "2026-08-27T00:03:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.malformed_mentions_json == 1
    assert live.mentioned_messages == 0
    assert live.mentioned_wakes == 0
    assert live.mentioned_decline_rate_pct is None
    assert live.org_wakes == 2
    assert live.org_declines == 2


def test_live_broadcast_fallback_not_mentioned() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    # Zero-valid signal ("[]") and NULL signal (legacy/system): neither is a
    # mentioned message, but their broadcast wakes still count org-wide.
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions="[]")
    _add_message(conn, "T1", 2, "alice", "2026-08-27T00:00:01Z",
                 mentions=None)
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="declined")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-27T00:01:01Z",
                    status="consumed")
    _add_invocation(conn, "T1", "bob", 2, "2026-08-27T00:01:02Z",
                    status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 0
    assert live.mentioned_wakes == 0
    assert live.mentioned_decline_rate_pct is None
    assert live.org_wakes == 3
    assert live.org_declines == 2


def test_live_disabled_thread_signal_still_counts_as_mentioned() -> None:
    conn = _conn()
    _add_thread(conn, "T1", mention_routing_enabled=0)
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="declined")
    # The persisted signal is non-empty even for a disabled thread (the
    # setting only changes the WAKE SET, not the signal) — so the message is
    # still a mentioned message for G1.
    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0


def test_task_followup_and_bootstrap_isolated_from_populations() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # A REPLY wake plus same-trigger TASK_FOLLOWUP/BOOTSTRAP rows: only the
    # REPLY wake enters G1/G3.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="consumed")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:02:00Z",
                    purpose="task_followup", status="declined")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:03:00Z",
                    purpose="bootstrap", status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 0
    assert live.mentioned_decline_rate_pct == 0.0
    assert live.org_wakes == 1
    assert live.org_declines == 0


def test_window_boundaries_are_half_open() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # Message exactly AT epoch: included. Wake exactly AT epoch: included.
    _add_message(conn, "T1", 1, "founder", EPOCH, mentions='["alice"]')
    # Message exactly AT window_end: excluded (half-open).
    _add_message(conn, "T1", 2, "founder", WINDOW_END, mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, EPOCH, status="declined")
    # Wake exactly AT window_end: excluded.
    _add_invocation(conn, "T1", "alice", 2, WINDOW_END, status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-09-27T00:00:00Z")
    assert live.interim is False
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.org_wakes == 1


def test_empty_window_zero_population() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-01T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-01T00:01:00Z",
                    status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.empty_window is True
    assert live.mentioned_messages == 0
    assert live.mentioned_decline_rate_pct is None
    assert live.org_decline_rate_pct is None
    assert live.founder_messages == 0
    joined = "\n".join(live.notes)
    assert "G1 denominator is zero" in joined
    assert "G3 denominator is zero" in joined
    assert "G2 denominator is zero" in joined


def test_zero_denominator_g1_mentioned_messages_without_wakes() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # mentioned message exists but no REPLY wake at all in window
    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 1
    assert live.mentioned_wakes == 0
    assert live.mentioned_decline_rate_pct is None
    assert any("G1 denominator is zero" in note for note in live.notes)


def test_malformed_mentions_json_counted_not_fabricated() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions="not-json")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:00:01Z",
                 mentions="42")  # non-list JSON
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.malformed_mentions_json == 2
    assert live.mentioned_messages == 0  # never fabricated into mentions
    assert any("malformed mentions_json" in note for note in live.notes)


def test_legacy_null_signal_not_mentioned() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions=None)
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:00:01Z",
                 kind="system", mentions=None)
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="consumed")
    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_messages == 0
    assert live.mentioned_wakes == 0
    assert live.org_wakes == 1


def test_partial_window_is_interim_and_complete_after_end() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="consumed")

    mid = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert mid.interim is True
    assert any("interim" in note for note in mid.notes)

    after = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-09-27T00:00:00Z")
    assert after.interim is False
    record = m.build_release_record(live=after)
    assert record["observation"] == "complete"
    record_mid = m.build_release_record(live=mid)
    assert record_mid["observation"] == "interim"


def test_as_of_cutoff_excludes_post_as_of_rows() -> None:
    """[MEDIUM regression] An interim run bounded by as_of: messages and
    wakes between as_of and window_end never enter ANY population — the
    record is reproducible at its stated observation instant."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # Before as_of: founder message + consumed REPLY.
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    token="aaa11111", consumed_at="2026-08-27T00:02:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 1,
                     "2026-08-27T00:01:30Z")
    # Between as_of and window_end: a founder message + its REPLY, and a
    # mentioned message + its declined wake — ALL must be excluded.
    _add_message(conn, "T1", 2, "founder", "2026-08-27T18:00:00Z")
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T18:01:00Z",
                    token="bbb22222", consumed_at="2026-08-27T18:02:00Z")
    _add_claim_audit(conn, "T1", "alice", "bbb22222", 2, 2,
                     "2026-08-27T18:01:30Z")
    _add_message(conn, "T1", 3, "founder", "2026-08-27T19:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 3, "2026-08-27T19:01:00Z",
                    token="ccc33333", status="declined",
                    consumed_at="2026-08-27T19:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.interim is True
    assert live.founder_messages == 1  # seq 2 excluded
    assert live.founder_covered == 1
    assert live.founder_uncovered == 0
    assert live.mentioned_messages == 0  # seq 3 excluded
    assert live.mentioned_wakes == 0
    assert live.org_wakes == 1  # only seq 1's REPLY; seqs 2/3 wakes excluded
    assert live.org_declines == 0


def test_as_of_cutoff_is_half_open() -> None:
    """[MEDIUM regression] Rows exactly AT the observation cutoff are
    excluded (half-open [start, as_of)): a message at as_of, a wake at as_of,
    and a REPLY consumed at as_of all fall outside the interim record."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    # Message before as_of; its REPLY is consumed EXACTLY at as_of → the reply
    # is not observable at as_of → the message is uncovered at that instant.
    _add_message(conn, "T1", 1, "founder", "2026-08-27T11:59:59Z")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T11:59:59.5Z",
                    token="aaa11111", consumed_at="2026-08-27T12:00:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 1,
                     "2026-08-27T11:59:59.6Z")
    # Message EXACTLY at as_of → excluded from the founder population.
    _add_message(conn, "T1", 2, "founder", "2026-08-27T12:00:00Z")
    # Wake EXACTLY at as_of → excluded from org-wide wakes.
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T12:00:00Z",
                    token="bbb22222", status="declined",
                    consumed_at="2026-08-27T12:00:01Z")
    # Wake just BEFORE as_of → included.
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T11:59:58Z",
                    token="eee55555", status="declined",
                    consumed_at="2026-08-27T11:59:59Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.founder_messages == 1  # seq 2 (exactly at as_of) excluded
    assert live.founder_covered == 0  # consumed exactly at as_of is excluded
    assert live.founder_uncovered == 1
    # Two in-window wakes before as_of (the consumed 11:59:59.5 REPLY and the
    # declined 11:59:58 wake); the wake exactly AT as_of is excluded.
    assert live.org_wakes == 2
    assert live.org_declines == 1


def test_live_pre_cutoff_wake_declined_exactly_at_cutoff() -> None:
    """[MEDIUM regression, G1+G3] The shipping seam: a REPLY wake enqueued
    strictly before observation_end stays in the G1/G3 denominator even when
    its terminal decline landed exactly AT the cutoff — the decline is not
    observable at that instant and must not retrospectively enter the interim
    record. A later as_of deterministically admits the terminal outcome."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    # Mentioned message → alice's wake is a G1 mentioned wake.
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    # Broadcast message → bob's wake is G3-only (never a mentioned wake).
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    # Both wakes: enqueued strictly BEFORE the cutoff, declined exactly AT it.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T12:00:00Z")
    _add_invocation(conn, "T1", "bob", 2, "2026-08-27T00:01:30Z",
                    token="bbb22222", status="declined",
                    consumed_at="2026-08-27T12:00:00Z")

    early = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert early.interim is True
    # Wakes remain in the denominators...
    assert early.mentioned_wakes == 1  # alice
    assert early.org_wakes == 2  # alice + bob
    # ...but declines exactly at the cutoff are not observable at it.
    assert early.mentioned_declines == 0
    assert early.mentioned_decline_rate_pct == 0.0
    assert early.org_declines == 0
    assert early.org_decline_rate_pct == 0.0

    later = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T13:00:00Z")
    assert later.mentioned_wakes == 1
    assert later.mentioned_declines == 1
    assert later.mentioned_decline_rate_pct == 100.0
    assert later.org_wakes == 2
    assert later.org_declines == 2
    assert later.org_decline_rate_pct == 100.0


def test_live_pre_cutoff_wake_declined_after_cutoff() -> None:
    """[MEDIUM regression, G1+G3] Same seam with the decline strictly AFTER
    the cutoff: the wake stays in the denominator at the earlier as_of; the
    terminal outcome is admitted only by a later as_of that observes it."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:00:30Z",
                 mentions="[]")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T18:00:00Z")
    _add_invocation(conn, "T1", "bob", 2, "2026-08-27T00:01:30Z",
                    token="bbb22222", status="declined",
                    consumed_at="2026-08-27T18:00:00Z")

    early = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert early.mentioned_wakes == 1
    assert early.org_wakes == 2
    assert early.mentioned_declines == 0
    assert early.org_declines == 0

    later = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T19:00:00Z")
    assert later.mentioned_declines == 1
    assert later.org_declines == 2
    assert later.mentioned_decline_rate_pct == 100.0


def test_live_pre_cutoff_decline_control_still_counted() -> None:
    """[MEDIUM regression control] A wake enqueued AND declined strictly
    before the cutoff remains counted in both the denominator and the decline
    numerator — the cutoff fix must not drop genuinely settled declines."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    token="aaa11111", status="declined",
                    consumed_at="2026-08-27T11:00:00Z")  # before the cutoff

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.mentioned_wakes == 1
    assert live.mentioned_declines == 1
    assert live.mentioned_decline_rate_pct == 100.0
    assert live.org_wakes == 1
    assert live.org_declines == 1
    assert live.org_decline_rate_pct == 100.0


def test_completed_window_still_caps_at_window_end() -> None:
    """[MEDIUM regression] A completed window (as_of past window_end) caps at
    window_end: rows between window_end and as_of never enter a population,
    and the observation is 'complete', not interim."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-09-26T00:00:00Z")
    _add_invocation(conn, "T1", "alice", 1, "2026-09-26T00:01:00Z",
                    token="aaa11111", consumed_at="2026-09-26T00:02:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 1,
                     "2026-09-26T00:01:30Z")
    # After window_end, before as_of: excluded by the window_end cap.
    _add_message(conn, "T1", 2, "founder", "2026-09-27T00:00:00Z")
    _add_invocation(conn, "T1", "alice", 2, "2026-09-27T00:01:00Z",
                    token="bbb22222", consumed_at="2026-09-27T00:02:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-09-27T00:00:00Z")
    assert live.interim is False
    assert live.founder_messages == 1  # seq 2 after window_end excluded
    assert live.founder_covered == 1
    assert live.org_wakes == 1



def test_g2_founder_coverage_live() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
    _add_message(conn, "T1", 3, "alice", "2026-08-27T00:02:00Z")  # not founder
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:03:00Z",
                    status="consumed")
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T00:04:00Z",
                    status="declined")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.founder_messages == 2
    assert live.founder_covered == 1
    assert live.founder_uncovered == 1
    assert any("no consumed REPLY" in note for note in live.notes)


def test_g2_coalesced_consumed_reply_covers_inclusive_range() -> None:
    """[HIGH regression] One consumed coalesced REPLY covers EVERY founder
    message inside its authoritative claimed range — both boundaries and the
    middle — never only its exact triggering_seq. A founder message outside
    the range stays uncovered."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
    _add_message(conn, "T1", 3, "founder", "2026-08-27T00:02:00Z")
    _add_message(conn, "T1", 4, "founder", "2026-08-27T00:03:00Z")
    # One coalesced REPLY: minted at seq 1, claimed the inclusive range 1..3.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:04:00Z",
                    token="aaa11111", consumed_at="2026-08-27T00:05:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 3,
                     "2026-08-27T00:04:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.founder_messages == 4
    # seqs 1, 2, 3 are inside [1, 3] (both boundaries + middle); seq 4 is not.
    assert live.founder_covered == 3
    assert live.founder_uncovered == 1
    assert any("no consumed REPLY" in note for note in live.notes)


def test_g2_claimed_range_not_settled_watermark_is_coverage_authority() -> None:
    """Claimed-versus-settled semantics: the immutable claimed range at claim
    time is the authority. A wake claimed [1,1] before later arrivals
    coalesced into it covers ONLY seq 1; the follow-on wake claimed [2,3]
    covers seqs 2 and 3 (arrivals strictly after the first wake's range)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
    _add_message(conn, "T1", 3, "founder", "2026-08-27T00:02:00Z")
    _add_message(conn, "T1", 4, "founder", "2026-08-27T00:03:00Z")
    # Wake A: minted at seq 1, CLAIMED [1,1] (arrivals 2/3 coalesced into its
    # required watermark only AFTER the claim snapshot). Settled consumed.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:04:00Z",
                    token="aaa11111", consumed_at="2026-08-27T00:05:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 1,
                     "2026-08-27T00:04:30Z")
    # Follow-on B: minted at acknowledged+1 = 2, claimed [2,3].
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T00:06:00Z",
                    token="bbb22222", consumed_at="2026-08-27T00:08:00Z")
    _add_claim_audit(conn, "T1", "alice", "bbb22222", 2, 3,
                     "2026-08-27T00:06:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.founder_covered == 3  # 1 (wake A) + 2,3 (wake B)
    assert live.founder_uncovered == 1  # seq 4 outside both claimed ranges


def test_g2_declined_wake_never_covers_founder_messages() -> None:
    """A DECLINED wake's claimed range is not coverage — only a REPLY that
    reached status='consumed' covers a founder message."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
    _add_message(conn, "T1", 3, "founder", "2026-08-27T00:02:00Z")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:03:00Z",
                    token="ddd44444", status="declined",
                    consumed_at="2026-08-27T00:04:00Z")
    _add_claim_audit(conn, "T1", "alice", "ddd44444", 1, 2,
                     "2026-08-27T00:03:30Z")
    _add_invocation(conn, "T1", "alice", 3, "2026-08-27T00:05:00Z",
                    token="ccc33333", consumed_at="2026-08-27T00:06:00Z")
    _add_claim_audit(conn, "T1", "alice", "ccc33333", 3, 3,
                     "2026-08-27T00:05:30Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    # The declined wake claimed [1,2] but wrote no reply: seqs 1-2 uncovered.
    assert live.founder_covered == 1  # only seq 3
    assert live.founder_uncovered == 2  # seqs 1, 2


def test_g2_consumed_without_claim_audit_covers_only_own_trigger() -> None:
    """A consumed invocation with no matching claim audit (queued-settled or
    legacy pre-coalescing row) covers only its own triggering_seq — an honest
    under-approximation that never fabricates a coalesced range."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z")
    _add_message(conn, "T1", 2, "founder", "2026-08-27T00:01:00Z")
    _add_invocation(conn, "T1", "alice", 2, "2026-08-27T00:03:00Z",
                    token="ccc33333", consumed_at="2026-08-27T00:04:00Z")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    assert live.founder_covered == 1  # seq 2 only
    assert live.founder_uncovered == 1  # seq 1


def test_replay_coalesced_founder_population_not_undercounted() -> None:
    """[HIGH regression, replay] A coalesced consumed REPLY covering three
    founder messages counts all three in the founder-replied population
    (the 698-style definition), not just its exact triggering seq."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please @alice")
    _add_message(conn, "T1", 2, "founder", "2026-08-05T00:01:00Z",
                 body="and @alice")
    _add_message(conn, "T1", 3, "founder", "2026-08-05T00:02:00Z",
                 body="also @alice")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:03:00Z",
                    token="aaa11111", consumed_at="2026-08-05T00:05:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 3,
                     "2026-08-05T00:03:30Z")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.founder_replied_messages == 3
    assert rep.zero_loss_violations == 0


def test_replay_coalescing_does_not_mask_genuine_violation() -> None:
    """[HIGH regression, replay] Range-aware coverage must NOT mask a genuine
    zero-loss violation: founder message 2 was replied to (by alice, via her
    coalesced range 1..3) but its Phase-2 wake set {bob} misses alice — a
    real violation, not a roster artifact."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please @alice")
    _add_message(conn, "T1", 2, "founder", "2026-08-05T00:01:00Z",
                 body="please @bob")
    _add_message(conn, "T1", 3, "founder", "2026-08-05T00:02:00Z",
                 body="update @alice")
    # alice's single coalesced wake covered founder messages 1..3.
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:03:00Z",
                    token="aaa11111", consumed_at="2026-08-05T00:05:00Z")
    _add_claim_audit(conn, "T1", "alice", "aaa11111", 1, 3,
                     "2026-08-05T00:03:30Z")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.founder_replied_messages == 3
    # Message 2: baseline repliers {alice} vs Phase-2 wake set {bob} — genuine
    # violation (alice is roster-visible), never masked by coalescing.
    assert rep.zero_loss_violations == 1
    assert rep.zero_loss_artifact_candidates == 0



# ---------------------------------------------------------------------------
# Replay — baseline reproduction, projection, zero-loss
# ---------------------------------------------------------------------------

def test_replay_projection_and_zero_loss_violation() -> None:
    """Founder message mentions alice only; baseline wakes: alice DECLINED,
    bob consumed (bob not mentioned). Phase-2 wake set {alice} ∩ baseline
    repliers {bob} = ∅ → one zero-loss violation; retained={alice} with one
    predicted decline."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please review @alice")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="declined")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-05T00:01:01Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 1
    assert rep.baseline_mentioned_wakes == 2
    assert rep.baseline_mentioned_declines == 1
    assert rep.baseline_mentioned_decline_rate_pct == 50.0
    assert rep.retained_wakes == 1
    assert rep.projected_declines == 1
    assert rep.projected_decline_rate_pct == 100.0
    assert rep.founder_replied_messages == 1
    assert rep.zero_loss_violations == 1


def test_replay_zero_loss_satisfied() -> None:
    """Founder message mentions alice; alice's baseline wake consumed →
    retained={alice}, no violation, no predicted decline."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please review @alice")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 1
    assert rep.retained_wakes == 1
    assert rep.projected_declines == 0
    assert rep.projected_decline_rate_pct == 0.0
    assert rep.founder_replied_messages == 1
    assert rep.zero_loss_violations == 0


def test_replay_broadcast_fallback_cannot_silence() -> None:
    """Founder message with no valid mentions → broadcast wake set contains
    the baseline replier → zero-loss safe; the message is not mentioned."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="status update please")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 0
    assert rep.baseline_mentioned_wakes == 0
    assert rep.founder_replied_messages == 1
    assert rep.zero_loss_violations == 0


def test_replay_speaker_exclusion_and_dedup() -> None:
    """Body '@alice @alice @bob' by speaker alice → valid set {bob}
    (speaker excluded, deduplicated); invalid/nonparticipant mentions
    (@founder, typo) do not count."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "alice", "2026-08-05T00:00:00Z",
                 body="@alice @alice @bob @founder @typo")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="consumed")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-05T00:01:01Z",
                    status="declined")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 1
    assert rep.retained_wakes == 1  # only bob's wake retained
    assert rep.projected_declines == 1
    assert rep.projected_decline_rate_pct == 100.0


def test_replay_self_only_mention_is_zero_valid_broadcast() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "alice", "2026-08-05T00:00:00Z",
                 body="@alice only me")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-05T00:01:00Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 0  # self-only → zero valid → broadcast
    assert rep.retained_wakes == 0
    assert rep.zero_loss_violations == 0


def test_replay_disabled_thread_broadcasts_even_when_mentioned() -> None:
    conn = _conn()
    _add_thread(conn, "T1", mention_routing_enabled=0)
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please @alice")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="declined")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-05T00:01:01Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    # Message is mentioned (signal present) but the disabled thread broadcasts
    # → retained = both agents.
    assert rep.mentioned_messages == 1
    assert rep.retained_wakes == 2
    assert rep.projected_declines == 1
    assert rep.projected_decline_rate_pct == 50.0
    assert rep.zero_loss_violations == 0  # bob's wake survives


def test_replay_zero_loss_artifact_candidate_separated() -> None:
    """A violation whose baseline replier was REMOVED from the thread (so the
    reconstructed write-time roster omits them) is a roster artifact, not a
    genuine silence risk — recorded separately."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    # consultant_head was a participant at write time (and replied), but was
    # later removed — the current thread_participants table only has bob.
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please review @consultant_head")
    _add_invocation(conn, "T1", "consultant_head", 1, "2026-08-05T00:01:00Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.founder_replied_messages == 1
    assert rep.zero_loss_violations == 1
    assert rep.zero_loss_artifact_candidates == 1  # replier not roster-visible
    assert any("artifact" in limitation for limitation in rep.limitations)


def test_replay_zero_loss_genuine_violation_not_artifact() -> None:
    """Founder message mentions alice only; alice declined, unmentioned bob
    replied. Both alice and bob ARE roster-visible, so the violation is
    genuine (the mention set misses the baseline replier)."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "bob", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="please review @alice")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="declined")
    _add_invocation(conn, "T1", "bob", 1, "2026-08-05T00:01:01Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.founder_replied_messages == 1
    assert rep.zero_loss_violations == 1
    assert rep.zero_loss_artifact_candidates == 0  # bob is roster-visible


def test_replay_roster_at_write_excludes_late_added_participant() -> None:
    """A participant added AFTER the message's created_at is not in the
    write-time roster: '@carol' on an older message classifies invalid."""
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_participant(conn, "T1", "carol", "2026-08-20T00:00:00Z")  # added late
    _add_message(conn, "T1", 1, "founder", "2026-08-05T00:00:00Z",
                 body="@carol please")
    _add_invocation(conn, "T1", "alice", 1, "2026-08-05T00:01:00Z",
                    status="consumed")

    rep = m.replay_baseline(conn, window_start=AUG_START, window_end=AUG_END)
    assert rep.mentioned_messages == 0  # carol not a participant at write time
    assert rep.zero_loss_violations == 0
    assert any("under-approximation" in limitation for limitation in rep.limitations)


# ---------------------------------------------------------------------------
# Release record — observed vs required, format
# ---------------------------------------------------------------------------

def test_release_record_separates_observed_and_required() -> None:
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="consumed")

    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    record = m.build_release_record(live=live)
    assert record["observation"] == "interim"
    assert record["required"]["g1_mentioned_message_saving"] == m.REQUIRED_G1
    assert record["required"]["g2_founder_coverage"] == m.REQUIRED_G2
    assert record["required"]["g3_org_wide_decline"] == m.REQUIRED_G3
    assert "live" in record and "replay" not in record
    # observed values live under "live", never merged into "required"
    assert record["live"]["mentioned_decline_rate_pct"] == 0.0

    md = m.format_markdown(record)
    assert "## Required criteria" in md
    assert "### G1 — mentioned-message saving" in md
    assert "observation: **interim**" in md


def test_build_release_record_requires_a_mode() -> None:
    with pytest.raises(ValueError):
        m.build_release_record()


def test_cli_emits_json_without_touching_file_db() -> None:
    # A deterministic run of the CLI against the live org DB is NOT part of
    # the hermetic test suite; instead verify the record round-trips through
    # the writer used by main().
    conn = _conn()
    _add_thread(conn, "T1")
    _add_participant(conn, "T1", "alice", "2026-08-01T00:00:00Z")
    _add_message(conn, "T1", 1, "founder", "2026-08-27T00:00:00Z",
                 mentions='["alice"]')
    _add_invocation(conn, "T1", "alice", 1, "2026-08-27T00:01:00Z",
                    status="consumed")
    live = m.measure_live_window(conn, epoch=EPOCH, as_of="2026-08-27T12:00:00Z")
    record = m.build_release_record(live=live)
    assert json.loads(json.dumps(record)) == record
