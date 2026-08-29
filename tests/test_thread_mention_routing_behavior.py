"""Phase-2 mention routing (THR-198) Slice B — production wake routing.

Slice B wires the merged pure resolver (runtime/daemon/thread_mentions.py)
into the two conversational store seams using the persisted structured
mention signal and the thread's default-enabled setting. Ratified matrix:

    resolve_wake_set(mentioned, participants, speaker, enabled):
      * disabled                        -> participants - speaker (broadcast)
      * enabled + valid mentions        -> exactly that valid set
      * enabled + zero valid mentions   -> participants - speaker (fallback)
        (including no mentions, invalid/nonparticipant-only, self-only)

Routing decision is at message-write time (matches where invocations are
minted today); the signal is write-time-frozen — a roster change AFTER the
write does not retroactively re-route the already-minted wakes (the next
message re-resolves against the current roster). TASK_FOLLOWUP / BOOTSTRAP
stay isolated (full broadcast). GH-688 per-pair coalescing, claim,
settlement, follow-on, retry, rollback, and compensation semantics are
preserved — only the recipient iteration set narrows.
"""
from __future__ import annotations

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadMessageKind,
    ThreadRecord,
)


def _make_thread(
    db: Database,
    thread_id: str = "THR-001",
    participants: tuple[str, ...] = ("alpha", "bravo", "charlie"),
) -> str:
    db.insert_thread(ThreadRecord(id=thread_id, subject="x"))
    for name in participants:
        db.add_thread_participant(thread_id, name, added_by="founder")
    return thread_id


def _mentions_json(db: Database, thread_id: str, seq: int):
    row = db._conn.execute(
        "SELECT mentions_json FROM thread_messages "
        "WHERE thread_id = ? AND seq = ?",
        (thread_id, seq),
    ).fetchone()
    return row["mentions_json"]


def _arrival_wake_names(db, thread_id: str, *, speaker: str, body: str,
                        recipients: list[str]) -> list[str]:
    _seq, arrivals = db.record_conversational_arrival(
        thread_id=thread_id, speaker=speaker,
        kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        recipients=recipients,
    )
    return [a.agent_name for a in arrivals]


# ---------------------------------------------------------------------------
# enabled (default) — the ratified matrix at the arrival seam
# ---------------------------------------------------------------------------


def test_enabled_single_valid_mention_wakes_exactly_that_agent(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder",
        body="only @bravo should wake", recipients=["alpha", "bravo", "charlie"],
    ) == ["bravo"]


def test_enabled_multiple_valid_mentions_wake_exactly_that_set(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder",
        body="@charlie and @bravo and @charlie again",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["charlie", "bravo"]


def test_enabled_mixed_valid_and_invalid_wakes_valid_only(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder",
        body="@bravo @founder @typo_agent",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["bravo"]


def test_enabled_invalid_only_broadcasts(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    for body in ("@founder", "@typo_agent", "@founder @typo_agent", "@delta"):
        assert _arrival_wake_names(
            db, "THR-001", speaker="founder", body=body,
            recipients=["alpha", "bravo", "charlie"],
        ) == ["alpha", "bravo", "charlie"], body


def test_enabled_no_mentions_broadcasts(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="plain broadcast text",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["alpha", "bravo", "charlie"]


def test_enabled_self_only_broadcasts(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="bravo", body="just @bravo myself",
        recipients=["alpha", "charlie"],
    ) == ["alpha", "charlie"]


def test_speaker_never_in_own_wake_set(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="bravo",
        body="@bravo @alpha @bravo", recipients=["alpha", "charlie"],
    ) == ["alpha"]


# ---------------------------------------------------------------------------
# TASK-6027 — the shipped ``mention_routing_enabled`` column is INERT legacy
# ---------------------------------------------------------------------------
#
# Founder ruling (TASK-6027): mention routing is UNCONDITIONAL. The
# ``threads.mention_routing_enabled`` column remains in the schema for
# compatibility (NOT NULL DEFAULT 1) but is never read to alter behavior,
# never written through a product surface, and never exposed on the wire.
# Persisted true/false values must not disable routing after upgrade.


def _set_enabled(db: Database, thread_id: str, enabled: bool) -> None:
    """Raw DB write simulating a legacy persisted value (only the inert
    column changes - no store/product surface writes it anymore)."""
    db._conn.execute(
        "UPDATE threads SET mention_routing_enabled = ? WHERE id = ?",
        (1 if enabled else 0, thread_id),
    )
    db._conn.commit()


def test_persisted_false_setting_never_disables_routing(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _set_enabled(db, "THR-001", False)
    # Valid mentions still route to exactly that set (routing unconditional).
    # A fresh thread per body: a founder mention with a deferred set opens a
    # strict exchange, which (correctly) holds later no-mention messages to
    # the frozen cohort - exchange semantics are covered in
    # tests/test_thread_reply_exchange.py.
    for body, expected in (
        ("@bravo", ["bravo"]),
        ("@bravo @charlie", ["bravo", "charlie"]),
    ):
        tid = f"THR-{len(expected)}"
        _make_thread(db, tid)
        assert _arrival_wake_names(
            db, tid, speaker="founder", body=body,
            recipients=["alpha", "bravo", "charlie"],
        ) == expected, body
    # Zero valid mentions still broadcast (no exchange: empty mention set).
    tid = "THR-B"
    _make_thread(db, tid)
    assert _arrival_wake_names(
        db, tid, speaker="founder", body="no mentions",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["alpha", "bravo", "charlie"]


def test_setting_defaults_to_enabled_for_new_threads(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    # The inert legacy column keeps the shipped default; the ThreadRecord
    # model no longer exposes the switch (unconditional routing).
    row = db._conn.execute(
        "SELECT mention_routing_enabled FROM threads WHERE id='THR-001'"
    ).fetchone()
    assert row["mention_routing_enabled"] == 1
    assert not hasattr(db.get_thread("THR-001"), "mention_routing_enabled")


# ---------------------------------------------------------------------------
# reply seam — mention-aware broadcast for REPLY; isolation for others
# ---------------------------------------------------------------------------


def test_reply_mention_narrows_broadcast(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )
    # Settle bravo+charlie so the reply's wakes are freshly minted.
    db.discard_reply_delivery("THR-001", agent_name="bravo", decline_reason="test")
    db.discard_reply_delivery("THR-001", agent_name="charlie", decline_reason="test")
    _seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="thanks @bravo for the notes", attachments=[],
        token=alpha_token, token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert _mentions_json(db, "THR-001", _seq2) == '["bravo"]'
    assert [a.agent_name for a in broadcast] == ["bravo"]
    assert settlement is not None


def test_reply_no_mentions_still_broadcasts(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )
    db.discard_reply_delivery("THR-001", agent_name="bravo", decline_reason="test")
    db.discard_reply_delivery("THR-001", agent_name="charlie", decline_reason="test")
    _seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="plain reply", attachments=[],
        token=alpha_token, token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert {a.agent_name for a in broadcast} == {"bravo", "charlie"}
    assert settlement is not None


def test_bootstrap_reply_never_mention_routed(tmp_path):
    """Isolation pin: a BOOTSTRAP reply keeps the FULL broadcast even when
    the body mentions only a subset of the other participants."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    seq1, _arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    bootstrap = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha",
        triggering_seq=seq1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    _seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="bootstrap note mentioning only @bravo", attachments=[],
        token=bootstrap.invocation_token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert settlement is None
    assert {a.agent_name for a in broadcast} == {"bravo", "charlie"}


def test_task_followup_reply_never_mention_routed(tmp_path):
    """Isolation pin: a TASK_FOLLOWUP reply keeps the FULL broadcast."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
    )
    inv, _cap = db.mint_followup_invocation_with_cap_extend(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
    )
    _seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="followup reply mentioning only @bravo", attachments=[],
        token=inv.invocation_token,
        token_purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    assert settlement is None
    assert {a.agent_name for a in broadcast} == {"bravo", "charlie"}


# ---------------------------------------------------------------------------
# participant changes between write and wake (write-time freeze)
# ---------------------------------------------------------------------------


def test_participant_removed_after_write_does_not_revoke_minted_wake(tmp_path):
    """The routing decision is frozen at write time: a wake minted for a
    mentioned participant survives their later removal; the NEXT message
    re-resolves against the current roster (mention now invalid -> broadcast).

    Uses a NON-founder speaker so no strict exchange opens (exchanges are
    founder-authored; the exchange's own removal semantics are covered in
    tests/test_thread_reply_exchange.py) - this stays a pure mention-routing
    contract test."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    assert _arrival_wake_names(
        db, "THR-001", speaker="bravo", body="@charlie please",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["charlie"]
    # Charlie leaves the thread AFTER the write.
    db.remove_thread_participant("THR-001", "charlie")
    # The already-minted wake is untouched (pair row still exists). U0
    # full-recipient obligations also created a row for every recipient
    # (only the wake-set member holds a queued token).
    states = db.list_reply_delivery_states()
    by_name = {s.agent_name: s for s in states}
    assert set(by_name) == {"alpha", "bravo", "charlie"}
    assert by_name["charlie"].queued_invocation_token is not None
    assert by_name["alpha"].queued_invocation_token is None
    assert by_name["bravo"].queued_invocation_token is None
    # The next message re-resolves: @charlie is now invalid -> broadcast
    # (minus the speaker).
    assert _arrival_wake_names(
        db, "THR-001", speaker="bravo", body="@charlie again",
        recipients=["alpha", "bravo"],
    ) == ["alpha"]


def test_participant_added_after_write_is_not_retroactively_woken(tmp_path):
    """A non-participant mention at write time is invalid at write time; the
    message fell back to broadcast and the already-minted wakes are not
    re-routed when the agent joins later. The NEXT message routes to them."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=("alpha", "bravo"))
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="where is @charlie?",
        recipients=["alpha", "bravo"],
    ) == ["alpha", "bravo"]  # charlie invalid at write -> broadcast fallback
    assert _mentions_json(db, "THR-001", 1) == "[]"
    # Charlie joins AFTER the write.
    db.add_thread_participant("THR-001", "charlie", added_by="founder")
    # Already-minted broadcast wakes are untouched; signal stays empty.
    states = db.list_reply_delivery_states()
    assert sorted(s.agent_name for s in states) == ["alpha", "bravo"]
    # The next message resolves against the CURRENT roster: charlie valid now.
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="now @charlie please",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["charlie"]


# ---------------------------------------------------------------------------
# no zero-recipient loss; GH-688 coalescing + follow-on preserved
# ---------------------------------------------------------------------------


def test_zero_valid_mentions_never_lose_recipients(tmp_path):
    """Zero valid mentions (any flavor) always broadcasts; a valid mention
    set is exactly that set — a message never resolves to zero recipients
    when other participants exist."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    # Explicit matrix:
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["alpha", "bravo", "charlie"]
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="@founder @typo",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["alpha", "bravo", "charlie"]
    assert _arrival_wake_names(
        db, "THR-001", speaker="bravo", body="@bravo",
        recipients=["alpha", "charlie"],
    ) == ["alpha", "charlie"]
    assert _arrival_wake_names(
        db, "THR-001", speaker="founder", body="@alpha",
        recipients=["alpha", "bravo", "charlie"],
    ) == ["alpha"]


def test_mentioned_burst_still_coalesces_per_pair(tmp_path):
    """GH-688 invariant under mention routing: a burst of mentions to the
    same pair coalesces into exactly ONE queued wake covering the range."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=("alpha", "bravo"))
    seq1, a1 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha urgent 1",
        recipients=["alpha"],
    )
    _seq2, a2 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha urgent 2",
        recipients=["alpha"],
    )
    _seq3, a3 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha urgent 3",
        recipients=["alpha"],
    )
    assert a1[0].invocation_token is not None and a1[0].coalesced is False
    assert a2[0].invocation_token is None and a2[0].coalesced is True
    assert a3[0].invocation_token is None and a3[0].coalesced is True
    states = db.list_reply_delivery_states()
    assert len(states) == 1
    assert states[0].required_through_seq == seq1 + 2
    assert states[0].queued_invocation_token == a1[0].invocation_token


def test_non_mentioned_participant_pair_untouched_by_narrowed_wake(tmp_path):
    """F1 (TASK-5966, founder-approved S3): when a message routes only to the
    mentioned set, the non-mentioned pairs get the OBLIGATION raise (U0
    full-recipient obligations — required advances, watermark stays
    contiguous) but NO mint and NO wake audit. Wake-mint eligibility is
    separated from obligation advancement."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    # Settle all three so watermarks are acknowledged.
    for agent in ("alpha", "bravo", "charlie"):
        db.discard_reply_delivery("THR-001", agent_name=agent, decline_reason="test")
    _seq2, arrivals2 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@bravo only this",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert [a.agent_name for a in arrivals2] == ["bravo"]
    states = {s.agent_name: s for s in db.list_reply_delivery_states()}
    # Only bravo's pair mints (wake set == mention set); acknowledged
    # watermarks stay at the settled tail for every pair.
    assert states["bravo"].required_through_seq == _seq2
    assert states["bravo"].queued_invocation_token is not None
    # Non-mentioned pairs: obligation raise only — required advances to the
    # message seq, acknowledged stays at the settled tail, NO token, NO mint.
    for agent in ("alpha", "charlie"):
        assert states[agent].required_through_seq == _seq2
        assert states[agent].acknowledged_through_seq == _seq1
        assert states[agent].queued_invocation_token is None
        assert states[agent].running_invocation_token is None


def test_settle_followon_still_minted_after_mentioned_arrival(tmp_path):
    """GH-688 already-running follow-on preserved: an arrival during a run
    (mention-routed) raises required; settlement still mints exactly one
    follow-on covering the retained unacknowledged range."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=("alpha", "bravo"))
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )
    db.claim_conversational_reply(alpha_token)
    # Mention-narrowed arrival during the run.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha during run",
        recipients=["alpha"],
    )
    settlement = db.settle_conversational_reply(
        token=alpha_token, outcome="reply",
    )
    assert settlement is not None
    assert settlement.follow_on_token is not None
    assert settlement.retry_required is False


# ---------------------------------------------------------------------------
# rollback / compensation with mention routing active
# ---------------------------------------------------------------------------


def test_arrival_failure_rolls_back_message_mentions_and_wakes(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=("alpha",))
    _seq, first = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha"],
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-arrival failure")

    monkeypatch.setattr(db, "_apply_arrival_uncommitted", _boom)
    with pytest.raises(RuntimeError):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha narrowed",
            recipients=["alpha"],
        )
    monkeypatch.undo()
    n = db._conn.execute(
        "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id='THR-001'"
    ).fetchone()["n"]
    assert n == 1, "failed arrival must roll back its message + mentions write"
