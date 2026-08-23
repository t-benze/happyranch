"""GH-688 Phase 1 Slice C — reply-delivery lifecycle audit events.

Slice B wired the durable ``thread_reply_delivery_state`` store transitions
(record_conversational_arrival / claim_conversational_reply /
settle_conversational_reply / discard_reply_delivery /
recover_reply_delivery_state). Slice C adds the six approved audit actions —
created, coalesced, claimed, settled, cancelled, recovered — emitted
atomically INSIDE those store transitions so duplicate notifications and
idempotent recovery can never fabricate false events.

Payload discipline (design §API, UI, and audit projection):
  * only fields the transition truthfully observed (agent, inclusive range,
    token PREFIX, outcome/reason/follow-on result);
  * no secrets — invocation tokens are single-use, so only the first 8 hex
    chars ever appear;
  * task_id keeps the existing THR-* scope-prefix convention unchanged.
"""
from __future__ import annotations

from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
)


def _make_pair(db: Database, thread_id: str = "THR-001", agent: str = "alice") -> None:
    db.insert_thread(ThreadRecord(id=thread_id, subject="x"))
    db.add_thread_participant(thread_id, agent, added_by="founder")


def _arrival(db: Database, thread_id: str = "THR-001", speaker: str = "founder",
             recipients=None, n: int = 1) -> list:
    """Append ``n`` conversational messages; return the FULL accumulated
    arrivals list (one ThreadReplyArrival per recipient per append)."""
    all_arrivals: list = []
    for i in range(n):
        _seq, arrivals = db.record_conversational_arrival(
            thread_id=thread_id, speaker=speaker,
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=recipients or ["alice"],
        )
        all_arrivals.extend(arrivals)
    return all_arrivals


def _audits(db: Database, thread_id: str = "THR-001") -> list[dict]:
    return db.get_audit_logs(thread_id)


def _wake_audits(db: Database, thread_id: str = "THR-001") -> list[dict]:
    return [a for a in _audits(db, thread_id)
            if a["action"].startswith("thread_reply_wake_")]


def _token_prefix(token: str) -> str:
    return token[:8]


# ---------------------------------------------------------------------------
# created / coalesced (record_conversational_arrival)
# ---------------------------------------------------------------------------

def test_first_arrival_emits_created_with_range_and_token_prefix(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=1)

    assert len(arrivals) == 1
    assert arrivals[0].invocation_token is not None
    rows = _wake_audits(db)
    assert [a["action"] for a in rows] == ["thread_reply_wake_created"]
    payload = rows[0]["payload"]
    assert payload["agent_name"] == "alice"
    assert payload["from_seq"] == 1
    assert payload["through_seq"] == 1
    assert payload["token_prefix"] == _token_prefix(arrivals[0].invocation_token)
    assert len(payload["token_prefix"]) == 8
    # The audit row uses the existing THR-* scope-prefix convention.
    assert rows[0]["task_id"] == "THR-001"
    assert rows[0]["agent"] == "alice"


def test_burst_emits_one_created_and_coalesced_per_extra_message(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=4)

    # Exactly one minted token (pair-level single wake) and three coalesced.
    assert [a.invocation_token is not None for a in arrivals].count(True) == 1
    assert [a.coalesced for a in arrivals].count(True) == 3

    rows = _wake_audits(db)
    assert [a["action"] for a in rows] == [
        "thread_reply_wake_created",
        "thread_reply_wake_coalesced",
        "thread_reply_wake_coalesced",
        "thread_reply_wake_coalesced",
    ]
    created = rows[0]["payload"]
    assert created["from_seq"] == 1 and created["through_seq"] == 1
    for i, r in enumerate(rows[1:], start=2):
        assert r["payload"]["through_seq"] == i  # range advanced to each arrival
    # Coalesced payloads carry the covering range, not a fabricated per-message
    # invocation: the LAST coalesced covers 1..4 with from_seq 1.
    assert rows[-1]["payload"]["from_seq"] == 1
    assert rows[-1]["payload"]["through_seq"] == 4


def test_multi_recipient_arrival_emits_created_per_recipient(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    for agent in ("alice", "bob"):
        db.add_thread_participant("THR-001", agent, added_by="founder")

    seq, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
        recipients=["alice", "bob"],
    )
    assert seq == 1
    assert {a.agent_name for a in arrivals} == {"alice", "bob"}

    created = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_created"]
    assert {c["payload"]["agent_name"] for c in created} == {"alice", "bob"}


def test_already_covered_idempotent_arrival_emits_no_audit(tmp_path):
    """Duplicate/backdated notification (seq <= required) is a no-op: it must
    not fabricate a coalesced event for a range already covered."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    _arrival(db, n=2)  # required == 2
    before = len(_wake_audits(db))

    # Same recipient, backdated seq already covered — defensive idempotent
    # branch inside the store must stay silent.
    _apply_private = db._apply_arrival_uncommitted
    r = _apply_private("THR-001", "alice", 2)
    assert r.coalesced is True
    assert len(_wake_audits(db)) == before  # no new audit row


def test_reply_broadcast_arrivals_emit_created_for_other_participants(tmp_path):
    """A replied message broadcasts to every other participant via the same
    arrival store path — those wakes are created/coalesced and audited too."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "founder", added_by="founder")
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    # First arrival wakes ONLY alice (bob is a participant but not a recipient
    # in this call — recipients are the caller's broadcast set).
    _arrival(db, recipients=["alice"], n=1)
    inv = [i for i in db.list_thread_invocations("THR-001")
           if i.purpose is ThreadInvocationPurpose.REPLY
           and i.status is ThreadInvocationStatus.PENDING][0]

    seq, settlement, arrivals = db.reply_conversational(
        thread_id="THR-001", speaker="alice", body_markdown="reply",
        attachments=None, token=inv.invocation_token,
        token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert settlement is not None
    assert seq == 2
    # The broadcast wakes bob (new pair) — one created for bob; alice excluded.
    created = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_created"
               and a["payload"]["agent_name"] == "bob"]
    assert len(created) == 1
    assert created[0]["payload"]["through_seq"] == 2


def test_bootstrap_and_followup_never_emit_wake_audits(tmp_path):
    """BOOTSTRAP / TASK_FOLLOWUP are direct mints — they must not emit any
    reply-wake lifecycle audit row (Phase 1 isolation invariant)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice", triggering_seq=1,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    db.mint_followup_invocation_with_cap_extend(
        "THR-001", agent_name="alice", triggering_seq=1,
    )
    assert _wake_audits(db) == []


# ---------------------------------------------------------------------------
# claimed (claim_conversational_reply)
# ---------------------------------------------------------------------------

def test_claim_emits_claimed_with_immutable_range(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=3)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)

    claim = db.claim_conversational_reply(token)
    assert claim is not None

    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_claimed"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["agent_name"] == "alice"
    assert payload["from_seq"] == 1
    assert payload["through_seq"] == 3
    assert payload["token_prefix"] == _token_prefix(token)


def test_stale_duplicate_claim_emits_no_claimed_audit(tmp_path):
    """A duplicate/stale notification no-ops at the CAS — it must not create a
    false claimed audit event."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)

    assert db.claim_conversational_reply(token) is not None
    claimed = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_claimed"]
    assert len(claimed) == 1

    # Second notification for the same token — no-op, no new audit.
    assert db.claim_conversational_reply(token) is None
    assert len([a for a in _wake_audits(db)
                if a["action"] == "thread_reply_wake_claimed"]) == 1


# ---------------------------------------------------------------------------
# settled (settle_conversational_reply)
# ---------------------------------------------------------------------------

def _claimed_token(db):
    arrivals = _arrival(db, n=2)
    return next(a.invocation_token for a in arrivals
                if a.invocation_token is not None)


def test_settle_reply_emits_settled_with_followon_when_arrival_in_run(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    token = _claimed_token(db)
    db.claim_conversational_reply(token)
    # Arrival during the run raises required to 3.
    _arrival(db, n=1)

    settlement = db.settle_conversational_reply(token=token, outcome="reply")
    assert settlement is not None
    assert settlement.follow_on_token is not None

    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_settled"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["agent_name"] == "alice"
    assert payload["outcome"] == "reply"
    assert payload["acknowledged_through_seq"] == 2  # claimed range only
    assert payload["required_through_seq"] == 3
    assert payload["retry_required"] is False
    assert payload["follow_on_token_prefix"] == _token_prefix(
        settlement.follow_on_token)
    assert payload["decline_reason"] is None


def test_settle_decline_acks_claimed_range_only(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    token = _claimed_token(db)
    db.claim_conversational_reply(token)

    settlement = db.settle_conversational_reply(token=token, outcome="decline")
    assert settlement is not None
    payload = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"][0]["payload"]
    assert payload["outcome"] == "decline"
    assert payload["acknowledged_through_seq"] == 2


def test_settle_failure_emits_settled_with_retry_and_terminal_reason(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    token = _claimed_token(db)
    db.claim_conversational_reply(token)

    settlement = db.settle_conversational_reply(
        token=token, outcome="failed", decline_reason="no_callback: rc=1",
    )
    assert settlement is not None
    assert settlement.retry_required is True
    payload = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"][0]["payload"]
    assert payload["outcome"] == "failed"
    assert payload["retry_required"] is True
    assert payload["follow_on_token_prefix"] is None
    assert payload["decline_reason"] == "no_callback: rc=1"
    # Failure never advances acknowledgement.
    assert payload["acknowledged_through_seq"] == 0


def test_settle_timeout_emits_settled(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    token = _claimed_token(db)
    db.claim_conversational_reply(token)

    db.settle_conversational_reply(token=token, outcome="timeout",
                                   decline_reason="timeout")
    payload = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"][0]["payload"]
    assert payload["outcome"] == "timeout"
    assert payload["retry_required"] is True


def test_settle_stale_or_legacy_token_emits_no_wake_audit(tmp_path):
    """A token not owned by any delivery-state row (legacy REPLY /
    BOOTSTRAP / TASK_FOLLOWUP / already-settled) returns None and must not
    fabricate a settled event."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    legacy = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.settle_conversational_reply(
        token=legacy.invocation_token, outcome="reply",
    ) is None
    assert _wake_audits(db) == []


# ---------------------------------------------------------------------------
# cancelled (discard_reply_delivery + fail-closed recovery sweeps)
# ---------------------------------------------------------------------------

def test_discard_all_pairs_emits_cancelled_per_pair(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    for agent in ("alice", "bob"):
        db.add_thread_participant("THR-001", agent, added_by="founder")
    _arrival(db, recipients=["alice", "bob"], n=2)

    count = db.discard_reply_delivery(
        "THR-001", decline_reason="founder_aborted",
    )
    assert count == 2

    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert {r["payload"]["agent_name"] for r in rows} == {"alice", "bob"}
    for r in rows:
        assert r["payload"]["reason"] == "founder_aborted"
        assert r["payload"]["boundary_seq"] == 2
        assert r["payload"]["swept_count"] == 1
    # The abort also emits no further wake rows after discard.
    assert len(rows) == 2


def test_discard_single_agent_emits_cancelled_only_for_that_pair(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    for agent in ("alice", "bob"):
        db.add_thread_participant("THR-001", agent, added_by="founder")
    _arrival(db, recipients=["alice", "bob"], n=1)

    count = db.discard_reply_delivery(
        "THR-001", agent_name="alice",
        decline_reason="participant_removed",
        status=ThreadInvocationStatus.DECLINED,
    )
    assert count == 1
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert [r["payload"]["agent_name"] for r in rows] == ["alice"]
    assert rows[0]["payload"]["reason"] == "participant_removed"


def test_discard_no_obligations_emits_no_cancelled_audit(tmp_path):
    """Discard with nothing pending must not fabricate a cancellation."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    assert db.discard_reply_delivery(
        "THR-001", decline_reason="founder_aborted",
    ) == 0
    assert _wake_audits(db) == []


def test_discard_legacy_only_rows_emits_cancelled(tmp_path):
    """Pre-cutover legacy pending REPLYs (no state row) are terminalized by
    discard; the cancellation is still audited per pair."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    count = db.discard_reply_delivery(
        "THR-001", decline_reason="archive_started",
    )
    assert count == 1
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert len(rows) == 1
    assert rows[0]["payload"]["reason"] == "archive_started"
    assert rows[0]["payload"]["swept_count"] == 1


def test_discard_retry_required_pair_without_pending_receipts_emits_cancelled(
    tmp_path,
):
    """A retry_required state obligation (no pending receipt rows — a failed
    wake awaiting the next conversational arrival) is still a live obligation:
    discard must clear it AND emit exactly one cancelled audit with the
    required watermark as boundary and swept_count 0."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)
    db.claim_conversational_reply(token)
    db.settle_conversational_reply(
        token=token, outcome="failed", decline_reason="timeout",
    )
    # retry_required state now holds required=2 > acknowledged=0 with NO
    # pending receipt rows (the failed attempt is terminal).
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.required_through_seq == 2
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    pending = [i for i in db.list_thread_invocations("THR-001")
               if i.status is ThreadInvocationStatus.PENDING]
    assert pending == []

    count = db.discard_reply_delivery(
        "THR-001", decline_reason="founder_aborted",
    )
    assert count == 0  # no pending receipt rows terminalized
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["agent_name"] == "alice"
    assert payload["reason"] == "founder_aborted"
    assert payload["boundary_seq"] == 2  # required watermark, not 0
    assert payload["swept_count"] == 0
    # The obligation is gone: no retry_required residue survives.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.required_through_seq == st.acknowledged_through_seq


# ---------------------------------------------------------------------------
# recovered (recover_reply_delivery_state)
# ---------------------------------------------------------------------------

def test_recovery_retains_valid_queued_with_recovered_audit(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)

    entries = db.recover_reply_delivery_state()
    assert [(e.kind, e.invocation_token) for e in entries] == [
        ("retained_queued", token),
    ]
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_recovered"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["agent_name"] == "alice"
    assert payload["kind"] == "retained_queued"
    assert payload["token_prefix"] == _token_prefix(token)
    assert payload["from_seq"] == 1
    assert payload["through_seq"] == 2


def test_recovery_running_replacement_emits_recovered(tmp_path):
    from tests.test_thread_db import _seed_running_state

    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    token = _seed_running_state(db, "THR-001", "alice", ack=0, req=3)

    entries = db.recover_reply_delivery_state()
    assert [(e.kind, e.agent_name) for e in entries] == [
        ("replacement_queued", "alice"),
    ]
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_recovered"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["kind"] == "replacement_queued"
    assert payload["token_prefix"] == _token_prefix(entries[0].invocation_token)
    assert payload["from_seq"] == 1
    assert payload["through_seq"] == 3
    # The interrupted attempt's terminal receipt is daemon_restart (legacy
    # reaper audit path is unchanged) and no replacement duplicates exist.
    reaped = [i for i in db.list_thread_invocations("THR-001")
              if i.status is ThreadInvocationStatus.FAILED
              and i.decline_reason == "daemon_restart"]
    assert len(reaped) == 1


def test_recovery_repeat_is_idempotent_without_false_events(tmp_path):
    """A second recovery pass on an already-replaced row retains the queued
    wake exactly once — no second replacement mint, no duplicate token."""
    from tests.test_thread_db import _seed_running_state

    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    _seed_running_state(db, "THR-001", "alice", ack=0, req=3)

    first = db.recover_reply_delivery_state()
    second = db.recover_reply_delivery_state()
    assert [e.kind for e in first] == ["replacement_queued"]
    assert [e.kind for e in second] == ["retained_queued"]
    assert second[0].invocation_token == first[0].invocation_token
    pending = [i for i in db.list_thread_invocations("THR-001")
               if i.status is ThreadInvocationStatus.PENDING
               and i.purpose is ThreadInvocationPurpose.REPLY]
    assert len(pending) == 1
    recovered = [a for a in _wake_audits(db)
                 if a["action"] == "thread_reply_wake_recovered"]
    # One audit per recovery pass that acted on the row — truthful, bounded,
    # and never duplicated within a single pass.
    assert [r["payload"]["kind"] for r in recovered] == [
        "replacement_queued", "retained_queued",
    ]


def test_recovery_both_slots_corruption_fails_closed_with_cancelled_audit(tmp_path):
    """Corrupt dual-slot recovery retires EVERY owned pending REPLY for the
    pair (including unreferenced third receipts) and audits one cancelled
    event with the corruption reason."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    queued = next(a.invocation_token for a in arrivals
                  if a.invocation_token is not None)
    # Simulate corruption: same pair ALSO holds a running slot referencing a
    # second started receipt, plus an unreferenced third pending receipt.
    running_inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice", triggering_seq=3,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(running_inv.invocation_token, session_id=None)
    third = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice", triggering_seq=4,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET "
        "queued_invocation_token = ?, running_invocation_token = ?, "
        "running_from_seq = 3, running_through_seq = 3 "
        "WHERE thread_id = ? AND agent_name = ?",
        (queued, running_inv.invocation_token, "THR-001", "alice"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []  # never mint/return a runnable token

    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["reason"] == "corrupt_both_slots_on_recovery"
    assert payload["swept_count"] == 3  # queued + running + unreferenced third
    assert payload["boundary_seq"] == 2
    # No owned pending REPLY survives.
    pending = [i for i in db.list_thread_invocations("THR-001")
               if i.status is ThreadInvocationStatus.PENDING
               and i.purpose is ThreadInvocationPurpose.REPLY]
    assert pending == []

    # Idempotent: a second recovery pass sees no ownership slots → no events.
    assert db.recover_reply_delivery_state() == []
    assert len([a for a in _wake_audits(db)
                if a["action"] == "thread_reply_wake_cancelled"]) == 1


def test_recovery_queued_started_fails_closed_with_cancelled_audit(tmp_path):
    """A queued slot referencing a started receipt sweeps the pair and audits
    the fail-closed cancellation; required_through_seq survives for the next
    conversational arrival."""
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)
    db.stamp_invocation_started(token, session_id=None)  # malformed queued

    assert db.recover_reply_delivery_state() == []
    rows = [a for a in _wake_audits(db)
            if a["action"] == "thread_reply_wake_cancelled"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["reason"] == "invalid_queued_started_on_recovery"
    assert payload["swept_count"] == 1
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.required_through_seq == 2  # preserved


def test_recovery_pure_slot_clears_emit_no_audit(tmp_path):
    """Non-recoverable slot clears (invalid/missing/terminal token) only
    record a diagnostic in last_terminal_reason — no lifecycle audit row is
    fabricated because no obligation changed hands."""
    from tests.test_thread_db import _seed_running_state

    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    _seed_running_state(db, "THR-001", "alice", ack=0, req=2)
    # Corrupt the running receipt to be already-terminal (consumed).
    running = db.get_reply_delivery_state("THR-001", "alice").running_invocation_token
    db._conn.execute(
        "UPDATE thread_invocations SET status = 'consumed' "
        "WHERE invocation_token = ?", (running,),
    )
    db._conn.commit()

    assert db.recover_reply_delivery_state() == []
    assert _wake_audits(db) == []
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None  # slot cleared
    assert st.last_terminal_reason == "running_already_terminal_on_recovery"


def test_recovery_missing_queued_token_clears_slot_without_audit(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=1)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)
    # Delete the referenced receipt — slot now references nothing.
    db._conn.execute(
        "DELETE FROM thread_invocations WHERE invocation_token = ?", (token,),
    )
    db._conn.commit()

    assert db.recover_reply_delivery_state() == []
    # The pre-existing created audit stays; the recovery pass itself must add
    # NO recovered/cancelled event for a pure slot clear.
    assert [a["action"] for a in _wake_audits(db)] == [
        "thread_reply_wake_created",
    ]
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "invalid_queued_token_on_recovery"


def test_audit_payloads_never_expose_full_tokens(tmp_path):
    """Every wake audit payload may only carry an 8-char token prefix — the
    full single-use token must never appear in an audit row."""
    import json

    db = Database(tmp_path / "happyranch.db")
    _make_pair(db)
    arrivals = _arrival(db, n=2)
    token = next(a.invocation_token for a in arrivals
                 if a.invocation_token is not None)
    db.claim_conversational_reply(token)
    db.settle_conversational_reply(token=token, outcome="reply")

    for row in _wake_audits(db):
        serialized = json.dumps(row["payload"])
        assert token not in serialized
        for key, value in row["payload"].items():
            if key.endswith("token_prefix") and value is not None:
                assert isinstance(value, str) and len(value) == 8
