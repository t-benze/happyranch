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
            triggering_seq INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            enqueued_at TEXT NOT NULL
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
) -> None:
    conn.execute(
        "INSERT INTO thread_invocations (thread_id, agent_name, triggering_seq, "
        "purpose, status, enqueued_at) VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, agent, triggering_seq, purpose, status, enqueued_at),
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
    assert any("no consumed REPLY yet" in note for note in live.notes)


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
