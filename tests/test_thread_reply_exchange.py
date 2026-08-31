"""TASK-5966 — strict mention-led exchange (founder-ratified THR-198
seq 194/195/196; TASK-5939 verdict).

Covers the store-level adversarial acceptance for the strict exchange packet:

  * U0 — full-recipient obligation raising separated from wake-mint
    eligibility (monotonic contiguous watermarks, no wake audit for held);
  * U1 — additive ``thread_reply_exchange`` + ``thread_exchange_deferrals``
    schema/store with partial-unique at-most-one-open constraint and
    idempotent startup DDL;
  * U2 — strict hold / no-pierce wake resolution (mention pierces, cohort
    frozen, TASK_FOLLOWUP/BOOTSTRAP isolated, outside-E byte-identical);
  * U3 — atomic quiescence + 5-minute grace closure, one slot-checked
    range-covering catch-up, tightened D1 (pre-arrival claims excluded),
    settled-audit payload extension, duplicate-closure CAS;
  * U4 — 4-hour fail-open reaper;
  * U5 — restart reconciliation, corruption sweeps, discard/archive/abort/
    removal semantics (daemon_restart stays nonterminal while replacement
    work exists);
  * monotonicity, exactly zero-or-one catch-up, unrelated-thread fairness.

Deterministic seams only: elapsed time is simulated by backdating the durable
exchange timestamps, never timing sleeps; concurrency/rollback semantics are
exercised at the transaction boundary (entered/release discipline), never
``threading.Barrier``.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.database import (
    Database,
    EXCHANGE_GRACE_SECONDS,
    MAX_PRIORITY_WAIT_SECONDS,
)
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
)

FOUNDER = "founder"
EM = "engineering_manager"
CH = "consultant_head"
PL = "product_lead"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_thread(
    db: Database,
    thread_id: str = "THR-001",
    participants: tuple[str, ...] = (EM, CH),
) -> str:
    db.insert_thread(ThreadRecord(id=thread_id, subject="x"))
    for name in participants:
        db.add_thread_participant(thread_id, name, added_by="founder")
    return thread_id


def _arrival(
    db: Database,
    thread_id: str = "THR-001",
    speaker: str = FOUNDER,
    body: str = "m",
    mentions_body: str | None = None,
    recipients: list[str] | None = None,
) -> tuple[int, list]:
    """One conversational arrival. ``recipients`` defaults to every
    participant (the broadcast candidate set — the speaker is never a
    participant in our fixtures, matching production call sites)."""
    return db.record_conversational_arrival(
        thread_id=thread_id,
        speaker=speaker,
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=mentions_body if mentions_body is not None else body,
        recipients=recipients or [EM, CH],
    )


def _pair_row(db: Database, thread_id: str, agent: str):
    return db._conn.execute(
        "SELECT * FROM thread_reply_delivery_state "
        "WHERE thread_id = ? AND agent_name = ?",
        (thread_id, agent),
    ).fetchone()


def _open_exchange(db: Database, thread_id: str = "THR-001"):
    return db._conn.execute(
        "SELECT * FROM thread_reply_exchange "
        "WHERE thread_id = ? AND state = 'open'",
        (thread_id,),
    ).fetchone()


def _deferral_rows(db: Database, thread_id: str, exchange_id: int):
    return db._conn.execute(
        "SELECT * FROM thread_exchange_deferrals "
        "WHERE thread_id = ? AND exchange_id = ? ORDER BY agent_name",
        (thread_id, exchange_id),
    ).fetchall()


def _audits(db: Database, thread_id: str = "THR-001") -> list[dict]:
    return db.get_audit_logs(thread_id)


def _wake_audits(db: Database, thread_id: str = "THR-001") -> list[dict]:
    return [a for a in _audits(db, thread_id)
            if a["action"].startswith("thread_reply_wake_")]


def _exchange_audits(db: Database, thread_id: str = "THR-001") -> list[dict]:
    return [a for a in _audits(db, thread_id)
            if a["action"].startswith("thread_exchange_")
            or a["action"].startswith("thread_deferral_")]


def _backdate_exchange(
    db: Database,
    thread_id: str,
    *,
    last_activity_ago: timedelta | None = None,
    opened_ago: timedelta | None = None,
) -> None:
    """Deterministic elapsed-time seam: backdate the durable exchange
    timestamps directly (never a timing sleep)."""
    now = datetime.now(timezone.utc)
    ex = _open_exchange(db, thread_id)
    assert ex is not None
    last_activity = now - (last_activity_ago or timedelta(0))
    opened = now - (opened_ago or timedelta(0))
    db._conn.execute(
        "UPDATE thread_reply_exchange SET last_activity_at = ?, opened_at = ? "
        "WHERE thread_id = ? AND exchange_id = ?",
        (last_activity.isoformat(), opened.isoformat(), thread_id,
         ex["exchange_id"]),
    )
    db._conn.commit()


def _settle(db: Database, token: str, outcome: str = "reply"):
    return db.settle_conversational_reply(
        token=token, outcome=outcome, decline_reason=None,
    )


def _claim(db: Database, token: str):
    return db.claim_conversational_reply(token)


def _invocation_row(db: Database, token: str):
    return db._conn.execute(
        "SELECT * FROM thread_invocations WHERE invocation_token = ?", (token,),
    ).fetchone()


def _tokens(arrivals) -> list[str]:
    return [a.invocation_token for a in arrivals
            if a.invocation_token is not None]


def test_reply_delivery_projection_distinguishes_authoritative_hold_from_retry(
    tmp_path,
) -> None:
    """THR-181 247-249: held needs both halves; stale reasons are not proof."""
    db = Database(tmp_path / "held-projection.db")
    tid = _make_thread(db, participants=(EM, CH, PL))
    _arrival(
        db, tid, mentions_body=f"@{EM} priority", recipients=[EM, CH, PL],
    )
    # Give one non-held residual pair a genuine failed retry side-by-side.
    em = _pair_row(db, tid, EM)
    _claim(db, em["queued_invocation_token"])
    _settle(db, em["queued_invocation_token"], "failed")
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET last_terminal_reason = ? "
        "WHERE thread_id = ? AND agent_name IN (?, ?)",
        ("timeout", tid, CH, PL),
    )
    # PL's matching deferral is deliberately released: historical rows cannot
    # mask a current retry. CH remains the exact OPEN+HELD conjunction.
    db._conn.execute(
        "UPDATE thread_exchange_deferrals SET state = 'released' "
        "WHERE thread_id = ? AND agent_name = ?", (tid, PL),
    )
    db._conn.commit()

    projections = {p.agent_name: p for p in db.list_reply_delivery_projections(tid)}
    assert projections[CH].state == "held"
    assert projections[CH].last_terminal_reason is None
    assert projections[PL].state == "retry_required"
    assert projections[PL].last_terminal_reason == "timeout"
    assert projections[EM].state == "retry_required"


@pytest.mark.parametrize("missing_half", ["exchange", "deferral"])
def test_reply_delivery_projection_requires_both_open_exchange_and_held_row(
    tmp_path, missing_half: str,
) -> None:
    db = Database(tmp_path / f"held-negative-{missing_half}.db")
    tid = _make_thread(db)
    _arrival(db, tid, mentions_body=f"@{EM} priority")
    if missing_half == "exchange":
        db._conn.execute(
            "UPDATE thread_reply_exchange SET state = 'released' WHERE thread_id = ?",
            (tid,),
        )
    else:
        db._conn.execute(
            "UPDATE thread_exchange_deferrals SET state = 'suppressed' "
            "WHERE thread_id = ? AND agent_name = ?", (tid, CH),
        )
    db._conn.commit()
    projection = {p.agent_name: p for p in db.list_reply_delivery_projections(tid)}
    assert projection[CH].state == "retry_required"


def _settle_cascade(db: Database, thread_id: str, agent: str, outcome: str) -> None:
    """Settle a pair's queued wake cascade until no residual remains (each
    reply/decline mints exactly one follow-on for the residual range — the
    shipped Phase-1 cascade). Reaching quiescence requires the cohort's
    cascade to fully terminalize (design §4.4: 'no live wake covering E')."""
    while True:
        row = _pair_row(db, thread_id, agent)
        token = row["queued_invocation_token"]
        if token is None or int(row["acknowledged_through_seq"]) >= int(
            row["required_through_seq"],
        ):
            return
        _claim(db, token)
        s = _settle(db, token, outcome=outcome)
        assert s is not None
        if s.follow_on_token is None:
            return


def _open_exchange_via_mention(db: Database, thread_id: str = "THR-001") -> int:
    """Open a strict exchange with the canonical fixture: founder mentions EM
    (P={EM}, D = everyone else). Returns seq of the opening message."""
    participants = [
        r["agent_name"] for r in db._conn.execute(
            "SELECT agent_name FROM thread_participants "
            "WHERE thread_id = ?", (thread_id,),
        )
    ]
    recipients = [p for p in participants if p != EM] + [EM]
    _seq, arrivals = _arrival(
        db, thread_id=thread_id, mentions_body=f"@{EM} please",
        recipients=recipients,
    )
    return _seq


# ---------------------------------------------------------------------------
# U1 — schema, idempotent DDL, partial-unique at-most-one-open constraint
# ---------------------------------------------------------------------------


def test_fresh_db_defines_exchange_tables_and_column(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    tables = {
        r["name"] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        )
    }
    assert "thread_reply_exchange" in tables
    assert "thread_exchange_deferrals" in tables
    cols = {
        r["name"] for r in db._conn.execute("PRAGMA table_info(threads)")
    }
    # TASK-6027 founder ruling: the proposed ``reply_exchange_enabled``
    # column is REMOVED (never shipped); the shipped
    # ``mention_routing_enabled`` column remains as inert legacy.
    assert "reply_exchange_enabled" not in cols
    assert "mention_routing_enabled" in cols
    # Partial unique open index exists.
    idx = db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_trex_open'",
    ).fetchone()
    assert idx is not None
    db._conn.close()


def test_reopen_is_idempotent(tmp_path):
    path = tmp_path / "happyranch.db"
    db1 = Database(path)
    db1._conn.close()
    db2 = Database(path)
    tables = {
        r["name"] for r in db2._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        )
    }
    assert "thread_reply_exchange" in tables
    assert "thread_exchange_deferrals" in tables
    db2._conn.close()


def test_partial_unique_open_index_enforces_one_open_exchange(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    now = datetime.now(timezone.utc).isoformat()
    db._conn.execute(
        "INSERT INTO thread_reply_exchange (thread_id, exchange_id, state, "
        "open_seq, close_seq, opened_at, last_activity_at) "
        "VALUES ('THR-001', 1, 'open', 1, 1, ?, ?)", (now, now),
    )
    db._conn.commit()
    with pytest.raises(Exception):
        db._conn.execute(
            "INSERT INTO thread_reply_exchange (thread_id, exchange_id, "
            "state, open_seq, close_seq, opened_at, last_activity_at) "
            "VALUES ('THR-001', 2, 'open', 1, 1, ?, ?)", (now, now),
        )
    db._conn.commit()


def test_terminal_exchange_does_not_block_new_open(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    now = datetime.now(timezone.utc).isoformat()
    for i, state in enumerate(("released", "suppressed"), start=1):
        db._conn.execute(
            "INSERT INTO thread_reply_exchange (thread_id, exchange_id, "
            "state, open_seq, close_seq, opened_at, last_activity_at, "
            "closed_at) VALUES ('THR-001', ?, ?, 1, 1, ?, ?, ?)",
            (i, state, now, now, now),
        )
    db._conn.commit()
    # A NEW open exchange is allowed after terminal rows exist.
    db._conn.execute(
        "INSERT INTO thread_reply_exchange (thread_id, exchange_id, state, "
        "open_seq, close_seq, opened_at, last_activity_at) "
        "VALUES ('THR-001', 3, 'open', 1, 1, ?, ?)", (now, now),
    )
    db._conn.commit()
    assert _open_exchange(db)["exchange_id"] == 3


# ---------------------------------------------------------------------------
# U0 — full-recipient obligations vs wake-mint eligibility
# ---------------------------------------------------------------------------


def test_obligation_raised_for_every_recipient_wake_mint_for_mentions_only(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    seq, arrivals = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert seq == 1
    # Wake set = the mention set {EM}: exactly one mint.
    assert [a.agent_name for a in arrivals] == [EM]
    assert arrivals[0].invocation_token is not None
    # Full-recipient obligations: CH's required watermark is raised too
    # (obligation row seeded ack=seq-1, NO token, NO wake audit).
    em = _pair_row(db, "THR-001", EM)
    ch = _pair_row(db, "THR-001", CH)
    assert int(em["required_through_seq"]) == 1
    assert int(em["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 1
    assert int(ch["acknowledged_through_seq"]) == 0
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    # No wake audit for the held (obligation-only) member.
    held_audits = [a for a in _wake_audits(db) if a["agent"] == CH]
    assert held_audits == []


def test_obligation_raise_is_idempotent_and_monotonic(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _arrival(db, mentions_body=f"@{EM} please", recipients=[EM, CH])
    before = _pair_row(db, "THR-001", CH)
    # A duplicate/backdated notification is a silent no-op (no audit, no
    # watermark change).
    db._conn.execute("BEGIN IMMEDIATE")
    db._raise_required_uncommitted("THR-001", CH, 1)
    db._conn.commit()
    after = _pair_row(db, "THR-001", CH)
    assert int(after["required_through_seq"]) == int(before["required_through_seq"])
    assert int(after["acknowledged_through_seq"]) == int(before["acknowledged_through_seq"])
    # Monotonicity: advancing never rewinds.
    db._conn.execute("BEGIN IMMEDIATE")
    db._raise_required_uncommitted("THR-001", CH, 5)
    db._conn.commit()
    assert int(_pair_row(db, "THR-001", CH)["required_through_seq"]) == 5
    db._conn.execute("BEGIN IMMEDIATE")
    db._raise_required_uncommitted("THR-001", CH, 3)
    db._conn.commit()
    assert int(_pair_row(db, "THR-001", CH)["required_through_seq"]) == 5


def test_obligation_only_raise_rollback_leaves_no_row(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    # Simulated crash: the obligation raise transaction rolls back.
    try:
        db._conn.execute("BEGIN IMMEDIATE")
        db._raise_required_uncommitted("THR-001", CH, 3)
        raise RuntimeError("simulated crash")
    except RuntimeError:
        db._conn.rollback()
    assert _pair_row(db, "THR-001", CH) is None


def test_reply_conversational_raises_obligations_for_all_recipients(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    seq1, arrivals1 = _arrival(db, recipients=[EM, CH])
    em_token = _tokens(arrivals1)[0]
    ch_token = _tokens(arrivals1)[1]
    # EM replies (no mention): wake set = full broadcast (outside any
    # exchange), but CH's reply slot is occupied → coalesce; obligation
    # semantics unchanged for both.
    seq2, settlement, arrivals2 = db.reply_conversational(
        thread_id="THR-001", speaker=EM,
        body_markdown="thanks", attachments=[],
        token=em_token,
        token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert seq2 == 2
    assert settlement is not None and settlement.acknowledged_through_seq == 1
    names = sorted(a.agent_name for a in arrivals2)
    assert names == [CH]
    ch2 = _pair_row(db, "THR-001", CH)
    assert int(ch2["required_through_seq"]) == 2
    # CH's own queued wake coalesced (required raised, single token).
    assert ch2["queued_invocation_token"] == ch_token
    # TASK_FOLLOWUP purpose: isolated full broadcast (byte-identical legacy).
    _seq3, _s3, arrivals3 = db.reply_conversational(
        thread_id="THR-001", speaker=CH,
        body_markdown="followup", attachments=[],
        token=ch_token,
        token_purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    assert sorted(a.agent_name for a in arrivals3) == [EM]


# ---------------------------------------------------------------------------
# U1/U2 — open conditions matrix
# ---------------------------------------------------------------------------


def test_open_requires_founder_mention_and_nonempty_deferred_set(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    # No mention → no exchange.
    _arrival(db, recipients=[EM, CH])
    assert _open_exchange(db) is None
    # Agent-authored mention → no exchange (G-open: founder only).
    _arrival(db, speaker=EM, mentions_body=f"@{CH} hi", recipients=[CH])
    assert _open_exchange(db) is None
    # Everyone mentioned → D empty → no exchange (zero overhead).
    _arrival(db, mentions_body=f"@{EM} @{CH} both", recipients=[EM, CH])
    assert _open_exchange(db) is None


def test_founder_mention_with_deferred_opens_exchange_with_frozen_cohort(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    seq = _open_exchange_via_mention(db)
    assert seq == 1
    ex = _open_exchange(db)
    assert ex is not None
    assert int(ex["open_seq"]) == 1
    assert int(ex["close_seq"]) == 1
    assert int(ex["deferred_count"]) == 1
    # Cohort P(E) = {EM} is read from the immutable opening mentions_json.
    opener = db._conn.execute(
        "SELECT mentions_json FROM thread_messages "
        "WHERE thread_id = 'THR-001' AND seq = 1",
    ).fetchone()
    assert json.loads(opener["mentions_json"]) == [EM]
    # Frozen D(E) = {CH} in thread_exchange_deferrals.
    rows = _deferral_rows(db, "THR-001", int(ex["exchange_id"]))
    assert [r["agent_name"] for r in rows] == [CH]
    assert rows[0]["state"] == "held"
    # Audits.
    actions = [a["action"] for a in _exchange_audits(db)]
    assert "thread_exchange_opened" in actions
    assert "thread_deferral_held" in actions


def test_open_conditions_reevaluated_for_each_message(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db)
    _arrival(db, recipients=[EM, CH])            # seq 1: no E
    _open_exchange_via_mention(db)               # seq 2: opens E (id 1)
    # Inside E, a second founder mention does NOT open a nested exchange —
    # it extends (extend event; exchange_id stays 1).
    _arrival(db, mentions_body=f"@{EM} again", recipients=[EM, CH])
    ex = _open_exchange(db)
    assert int(ex["exchange_id"]) == 1
    assert int(ex["close_seq"]) == 3
    # Cohort wake settles → quiescence becomes reachable.
    em_token = _pair_row(db, "THR-001", EM)["queued_invocation_token"]
    _claim(db, em_token)
    _settle(db, em_token, outcome="decline")
    # After closure, a new mention opens a NEW exchange (id increments).
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    db.reaper_sweep_reply_exchanges()
    assert _open_exchange(db) is None
    _arrival(db, mentions_body=f"@{EM} new round", recipients=[EM, CH])
    ex2 = _open_exchange(db)
    assert int(ex2["exchange_id"]) == 2
    assert int(ex2["open_seq"]) == 4


# ---------------------------------------------------------------------------
# U2 — strict hold / no-pierce wake resolution
# ---------------------------------------------------------------------------


def test_in_exchange_no_mention_wakes_cohort_only_and_holds_deferred(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH, PL))
    _open_exchange_via_mention(db)          # seq 1 opens E (P={EM}, D={CH,PL})
    # A no-mention follow-on inside E wakes ONLY the cohort.
    seq2, arrivals = _arrival(db, recipients=[EM, CH, PL])
    assert seq2 == 2
    assert [a.agent_name for a in arrivals] == [EM]
    # The opening mint for EM (seq 1) coalesced into this wake; no new mint.
    em = _pair_row(db, "THR-001", EM)
    assert int(em["required_through_seq"]) == 2
    assert int(em["acknowledged_through_seq"]) == 0
    assert em["queued_invocation_token"] is not None
    # Held members: obligation-only — no token, no wake audit.
    for held in (CH, PL):
        row = _pair_row(db, "THR-001", held)
        assert int(row["required_through_seq"]) == 2
        assert int(row["acknowledged_through_seq"]) == 0
        assert row["queued_invocation_token"] is None
        assert row["running_invocation_token"] is None
        assert [a for a in _wake_audits(db) if a["agent"] == held] == []
    # Exchange extended (activity + frontier).
    ex = _open_exchange(db)
    assert int(ex["close_seq"]) == 2


def test_mention_pierce_wakes_deferred_member_without_joining_cohort(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)          # E open, P={EM}, D={CH}
    # A later founder message mentions CH (pierce): CH wakes immediately.
    seq2, arrivals = _arrival(
        db, mentions_body=f"@{CH} your input", recipients=[EM, CH],
    )
    assert seq2 == 2
    assert sorted(a.agent_name for a in arrivals) == [CH]
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is not None
    # CH's deferral row STAYS held (frozen cohort — pierce ≠ join); the
    # closure will coalesce into this pierce wake (no extra mint).
    rows = _deferral_rows(db, "THR-001", 1)
    assert rows[0]["agent_name"] == CH
    assert rows[0]["state"] == "held"
    # The cohort is unchanged: P is still {EM}.
    ex = _open_exchange(db)
    opener = db._conn.execute(
        "SELECT mentions_json FROM thread_messages "
        "WHERE thread_id = 'THR-001' AND seq = 1",
    ).fetchone()
    assert json.loads(opener["mentions_json"]) == [EM]


def test_no_new_held_pair_wake_while_open_no_pierce_property(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH, PL))
    _open_exchange_via_mention(db)
    for i in range(3):
        _arrival(db, recipients=[EM, CH, PL])
    # No wake_created / wake_coalesced / wake_claimed for held pairs during E.
    for held in (CH, PL):
        held_audits = [a for a in _wake_audits(db) if a["agent"] == held]
        assert held_audits == []
        row = _pair_row(db, "THR-001", held)
        assert row["queued_invocation_token"] is None
        assert row["running_invocation_token"] is None


def test_task_followup_and_bootstrap_isolated_inside_exchange(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # A TASK_FOLLOWUP broadcast inside E is ISOLATED: full participant set,
    # never exchange-routed (documented pierce source).
    seq2, _s, arrivals = db.reply_conversational(
        thread_id="THR-001", speaker=EM,
        body_markdown="followup", attachments=[],
        token="bogus-not-owned",
        token_purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    # token not owned → legacy consume; but we only need the wake-set check
    # via a fresh owned token: mint one for EM first then reply as EM.
    _seq0, a0 = _arrival(db, recipients=[EM, CH])   # outside-wake for EM+CH
    token = next(a.invocation_token for a in a0 if a.agent_name == EM)
    db.claim_conversational_reply(token)
    seq3, _s3, arrivals3 = db.reply_conversational(
        thread_id="THR-001", speaker=EM,
        body_markdown="bootstrap-like", attachments=[],
        token=token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert sorted(a.agent_name for a in arrivals3) == [CH]


def test_outside_exchange_behavior_is_byte_identical(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    # With the exchange UNCONDITIONAL (TASK-6027), the opening founder
    # mention wake set is byte-identical to shipped mention routing (exactly
    # the mention set mints), while U0 full-recipient obligations still
    # raise required for the unmentioned pair (founder-approved S3 —
    # obligation rows are separate from wake-mint eligibility; no token, no
    # wake audit).
    _seq, arrivals = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert [a.agent_name for a in arrivals] == [EM]
    assert arrivals[0].invocation_token is not None
    ch = _pair_row(db, "THR-001", CH)
    assert ch is not None  # U0 obligation row exists (ack=seq-1, no token)
    assert int(ch["required_through_seq"]) == 1
    assert int(ch["acknowledged_through_seq"]) == 0
    assert ch["queued_invocation_token"] is None
    assert [a for a in _wake_audits(db) if a["agent"] == CH] == []


# ---------------------------------------------------------------------------
# U3 — closure: quiescence + 5-min grace; catch-up; tightened D1
# ---------------------------------------------------------------------------


def test_closure_after_quiescence_and_grace_mints_exactly_one_catch_up(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH, PL))
    _open_exchange_via_mention(db)   # seq 1: EM minted, CH/PL held
    # Cohort wakes settle (decline), then 3 no-mention messages extend E.
    for i in range(3):
        _arrival(db, recipients=[EM, CH, PL])
    em_row = _pair_row(db, "THR-001", EM)
    em_token = em_row["queued_invocation_token"]
    _claim(db, em_token)
    _settle(db, em_token, outcome="decline")
    # EM's decline minted a follow-on for the residual range (2..4) — the
    # cascade must fully terminalize before quiescence is reachable.
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    # No live cohort wake now. Backdate activity past the 5-min grace.
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals = db.reaper_sweep_reply_exchanges()
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "released"
    assert ex["close_reason"] == "quiescence"
    # Exactly ONE catch-up per held pair covering the FULL range 1..4.
    catchups = {a.agent_name: a for a in arrivals if a.invocation_token is not None}
    for held in (CH, PL):
        assert held in catchups, f"{held} missing catch-up"
        assert catchups[held].from_seq == 1
        assert catchups[held].through_seq == 4
        row = _pair_row(db, "THR-001", held)
        assert row["queued_invocation_token"] == catchups[held].invocation_token
    # EM (cohort) fully settled its own cascade (ack == required), so it
    # needs NO closure catch-up.
    assert EM not in catchups
    em_after = _pair_row(db, "THR-001", EM)
    assert int(em_after["acknowledged_through_seq"]) == int(
        em_after["required_through_seq"],
    )
    assert em_after["queued_invocation_token"] is None
    # No wake was minted for held pairs DURING the exchange (no-pierce).
    for held in (CH, PL):
        held_wakes = [a for a in _wake_audits(db) if a["agent"] == held]
        created = [a for a in held_wakes
                   if a["action"] == "thread_reply_wake_created"]
        assert len(created) == 1  # only the closure catch-up


def test_closure_with_live_cohort_wake_does_not_fire(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    # The cohort's opening wake is still queued (never claimed/settled) —
    # quiescence fails, the exchange stays open.
    assert db.reaper_sweep_reply_exchanges() == []
    assert _open_exchange(db) is not None


def test_pre_arrival_claim_never_covers_the_mentioned_message(tmp_path):
    """THR-198 #136 fixture shape: a wake claimed BEFORE the mentioned
    message arrives cannot count as that message's covering substantive
    reply; the post-arrival follow-on is the covering wake; the deferred
    participant gets exactly one catch-up."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    # Pre-arrival wake for EM ONLY (CH is already caught up — production
    # fixture: consultant_head settled ack=134 just before EM's wake was
    # minted 129..135). seq 1.
    seq1, arrivals = _arrival(db, recipients=[EM])
    em_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == EM)
    # EM's wake is CLAIMED BEFORE the mention arrives.
    claim = _claim(db, em_token)
    assert claim.running_through_seq == 1
    started_at = _invocation_row(db, em_token)["started_at"]
    # Mention message arrives (founder, @EM) — opens E; EM's running wake
    # coalesces (required → 2), CH is held (obligation-only).
    seq2, arrivals2 = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert seq2 == 2
    assert [a.agent_name for a in arrivals2] == [EM]
    assert arrivals2[0].invocation_token is None  # coalesced, no mint
    assert arrivals2[0].coalesced is True
    # EM settles the PRE-ARRIVAL wake: acknowledges only the claimed range
    # (1..1) — seq 2 is NOT covered (range containment) and the durable
    # claimed_at predates M's write.
    settlement = _settle(db, em_token, outcome="reply")
    assert settlement.acknowledged_through_seq == 1
    settled = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"][-1]
    assert settled["payload"]["covered_through_seq"] == 1  # cover(S)
    m2 = db._conn.execute(
        "SELECT created_at FROM thread_messages "
        "WHERE thread_id = 'THR-001' AND seq = 2",
    ).fetchone()
    assert started_at < m2["created_at"]  # claimed before M
    # The follow-on (post-arrival, covering 2..2) is EM's covering wake —
    # EM is in the cohort so the follow-on IS minted. Settle the cascade so
    # quiescence becomes reachable (production #136: the follow-on was
    # claimed 09:39:16 and settled 09:40:35, ack=136).
    assert settlement.follow_on_token is not None
    _settle_cascade(db, "THR-001", EM, outcome="reply")
    # CH: exactly one catch-up at closure, covering the FULL range 1..2
    # (never the pre-arrival wake, never a mid-E wake).
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals3 = db.reaper_sweep_reply_exchanges()
    ch_catchups = [a for a in arrivals3
                   if a.agent_name == CH and a.invocation_token is not None]
    assert len(ch_catchups) == 1
    assert ch_catchups[0].from_seq == 2
    assert ch_catchups[0].through_seq == 2


def test_pre_arrival_claim_decline_variant_still_yields_one_catch_up(tmp_path):
    """The accepted decline fixture: a mention landing inside an already-
    running wake, followed by a DECLINE, still yields exactly one range-
    covering catch-up for the deferred participant — not zero (the silent-
    drop-hole coverage fix), not N."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    seq1, arrivals = _arrival(db, recipients=[EM])
    em_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == EM)
    _claim(db, em_token)
    _arrival(db, mentions_body=f"@{EM} please", recipients=[EM, CH])
    _settle(db, em_token, outcome="decline")
    # EM's decline minted a follow-on (2..2) — settle the cascade (decline
    # variant) so quiescence becomes reachable.
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals2 = db.reaper_sweep_reply_exchanges()
    ch_catchups = [a for a in arrivals2
                   if a.agent_name == CH and a.invocation_token is not None]
    assert len(ch_catchups) == 1
    assert ch_catchups[0].from_seq == 2
    assert ch_catchups[0].through_seq == 2


def test_settled_audit_payload_extension(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _seq, arrivals = _arrival(db, recipients=[EM, CH])
    em_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == EM)
    _claim(db, em_token)
    _settle(db, em_token, outcome="reply")
    settled = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"]
    assert len(settled) == 1
    p = settled[0]["payload"]
    # S4 extension: authoritative cover(S) + claim/mint instants.
    assert p["covered_from_seq"] == 1
    assert p["covered_through_seq"] == 1
    assert p["claimed_at"] is not None
    assert p["minted_at"] is not None
    assert p["exchange_held"] is False


def test_held_follow_on_suppressed_and_exchange_held_recorded(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)   # seq 1: E open, P={EM}, D={CH}
    # CH pierces by mention (seq 2): CH gets a wake.
    seq2, arrivals2 = _arrival(
        db, mentions_body=f"@{CH} now", recipients=[EM, CH],
    )
    assert seq2 == 2
    ch_token = next(a.invocation_token for a in arrivals2
                    if a.agent_name == CH)
    _claim(db, ch_token)
    # While CH's wake runs, EM sends a no-mention message (seq 3): CH held,
    # required → 3, no mint (slot occupied).
    seq3, _a3 = _arrival(db, recipients=[EM, CH])
    assert seq3 == 3
    # CH settles reply: acknowledged range 2..2, residual 3..3 would mint a
    # follow-on — but CH is HELD, so the follow-on is SUPPRESSED.
    settlement = _settle(db, ch_token, outcome="reply")
    assert settlement.exchange_held is True
    assert settlement.follow_on_token is None
    assert settlement.retry_required is False
    settled = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"
               and a["agent"] == CH][-1]
    assert settled["payload"]["exchange_held"] is True
    # The residual range is covered by the closure catch-up. The cohort's own
    # cascade must terminalize first (quiescence is a cohort predicate).
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    db.reaper_sweep_reply_exchanges()
    ch = _pair_row(db, "THR-001", CH)
    assert int(ch["acknowledged_through_seq"]) == 2
    assert ch["queued_invocation_token"] is not None  # catch-up covers 3..3


def test_duplicate_closure_is_cas_miss_without_audit(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # Cohort cascade fully terminalizes → quiescence reachable.
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    n1 = len([a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"])
    db.reaper_sweep_reply_exchanges()
    n2 = len([a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"])
    assert n2 == n1 + 1
    # Second evaluation: CAS miss — no extra audit, no extra mints.
    db.reaper_sweep_reply_exchanges()
    n3 = len([a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"])
    assert n3 == n2


def test_exactly_zero_or_one_catchup_per_pair(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # CH pierces and settles everything (ack == required) → zero catch-up.
    seq2, arrivals2 = _arrival(
        db, mentions_body=f"@{CH} now", recipients=[EM, CH],
    )
    ch_token = next(a.invocation_token for a in arrivals2
                    if a.agent_name == CH)
    _claim(db, ch_token)
    _settle(db, ch_token, outcome="reply")
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals3 = db.reaper_sweep_reply_exchanges()
    ch_catchups = [a for a in arrivals3
                   if a.agent_name == CH and a.invocation_token is not None]
    assert len(ch_catchups) == 0  # already covered by the pierce settlement


def test_watermark_monotonicity_across_exchange(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    prev_ack, prev_req = {}, {}
    for agent in (EM, CH):
        row = _pair_row(db, "THR-001", agent)
        prev_ack[agent] = int(row["acknowledged_through_seq"])
        prev_req[agent] = int(row["required_through_seq"])
    for i in range(3):
        _arrival(db, recipients=[EM, CH])
    for agent in (EM, CH):
        row = _pair_row(db, "THR-001", agent)
        assert int(row["acknowledged_through_seq"]) >= prev_ack[agent]
        assert int(row["required_through_seq"]) >= prev_req[agent]
    # Contiguity invariant: acknowledged <= required always.
    for agent in (EM, CH):
        row = _pair_row(db, "THR-001", agent)
        assert int(row["acknowledged_through_seq"]) <= int(
            row["required_through_seq"],
        )


def test_unrelated_thread_is_not_starved_or_blocked(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH), thread_id="THR-001")
    _make_thread(db, participants=(EM, CH), thread_id="THR-002")
    _open_exchange_via_mention(db)  # THR-001 exchange open, CH held
    # THR-002 broadcasts flow normally (both woken, participant order).
    seq, arrivals = _arrival(db, thread_id="THR-002", recipients=[EM, CH])
    assert seq == 1
    assert [a.agent_name for a in arrivals] == [EM, CH]
    assert all(a.invocation_token is not None for a in arrivals)


# ---------------------------------------------------------------------------
# U4 — 4-hour fail-open reaper
# ---------------------------------------------------------------------------


def test_absolute_bound_fail_open_after_four_hours(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # Even with a live cohort wake, the absolute 4h bound fails open.
    _backdate_exchange(db, "THR-001", opened_ago=timedelta(
        seconds=MAX_PRIORITY_WAIT_SECONDS + 1,
    ))
    arrivals = db.reaper_sweep_reply_exchanges()
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "released"
    assert ex["close_reason"] == "max_priority_wait"
    assert any(a.invocation_token is not None for a in arrivals)


def test_grace_does_not_fire_before_five_minutes(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS - 60,
    ))
    assert db.reaper_sweep_reply_exchanges() == []
    assert _open_exchange(db) is not None


# ---------------------------------------------------------------------------
# U5 — restart reconcile, corruption sweeps, discard/archive/removal
# ---------------------------------------------------------------------------


def test_reconcile_is_idempotent_and_preserves_open_exchanges(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # Cohort wake still queued → reconcile keeps E open (no stale close).
    assert db.reconcile_reply_exchanges() == []
    assert _open_exchange(db) is not None
    # After settlement + grace, reconcile closes it exactly once.
    em_row = _pair_row(db, "THR-001", EM)
    em_token = em_row["queued_invocation_token"]
    _claim(db, em_token)
    _settle(db, em_token, outcome="decline")
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals = db.reconcile_reply_exchanges()
    assert any(a.invocation_token is not None for a in arrivals)
    assert _open_exchange(db) is None
    # Second reconcile: no-op.
    assert db.reconcile_reply_exchanges() == []


def test_daemon_restart_replacement_keeps_cohort_non_quiescent(tmp_path):
    """daemon_restart is an interruption, NOT terminal, while a replacement
    wake covering the exchange is queued: recovery's replacement keeps the
    quiescence predicate false, so the exchange cannot stale-close."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    em_row = _pair_row(db, "THR-001", EM)
    em_token = em_row["queued_invocation_token"]
    _claim(db, em_token)
    # Simulate daemon death mid-run: running attempt present; recovery
    # terminalizes it and mints a replacement queued wake.
    recovered = db.recover_reply_delivery_state()
    replacement = [e for e in recovered
                   if e.thread_id == "THR-001" and e.agent_name == EM]
    assert len(replacement) == 1
    assert replacement[0].kind == "replacement_queued"
    # Backdate activity beyond grace: the replacement wake (queued, required
    # >= open_seq) keeps the cohort non-quiescent → no stale close.
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    assert db.reaper_sweep_reply_exchanges() == []
    assert _open_exchange(db) is not None


def test_orphan_deferral_row_fail_closed_on_reconcile(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    now = datetime.now(timezone.utc).isoformat()
    # A held deferral row with NO exchange row (corruption class 1/2) — the
    # orphan can only arise from a crash/corruption (FKs normally forbid the
    # insert), so the sweep must fail closed: suppressed + diagnostic, never
    # minted.
    db._conn.execute("PRAGMA foreign_keys=OFF")
    db._conn.execute(
        "INSERT INTO thread_exchange_deferrals (thread_id, exchange_id, "
        "agent_name, state, created_at) VALUES ('THR-001', 999, 'charlie', "
        "'held', ?)", (now,),
    )
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys=ON")
    db.reconcile_reply_exchanges()
    row = db._conn.execute(
        "SELECT state FROM thread_exchange_deferrals "
        "WHERE thread_id = 'THR-001' AND exchange_id = 999",
    ).fetchone()
    assert row["state"] == "suppressed"
    diag = [a for a in _audits(db)
            if a["action"] == "thread_deferral_suppressed"]
    assert len(diag) == 1
    assert diag[0]["payload"]["reason"] == "orphan_deferral_row"


def test_corrupt_open_exchange_suppressed_with_diagnostic(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    now = datetime.now(timezone.utc).isoformat()
    # Open exchange with close_seq < open_seq (corruption class 4) and a
    # malformed opening mention signal (class 3).
    db._conn.execute(
        "INSERT INTO thread_reply_exchange (thread_id, exchange_id, state, "
        "open_seq, close_seq, opened_at, last_activity_at, deferred_count) "
        "VALUES ('THR-001', 1, 'open', 10, 3, ?, ?, 1)", (now, now),
    )
    db._conn.commit()
    db.reconcile_reply_exchanges()
    ex = db._conn.execute(
        "SELECT state, close_reason FROM thread_reply_exchange "
        "WHERE thread_id = 'THR-001' AND exchange_id = 1",
    ).fetchone()
    assert ex["state"] == "suppressed"
    assert ex["close_reason"] == "corrupt"
    diag = [a for a in _audits(db) if a["action"] == "thread_exchange_corrupt"]
    assert len(diag) == 1


def test_abort_suppresses_exchange_with_no_catch_up(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    aborted = db.discard_reply_delivery(
        "THR-001", decline_reason="founder_aborted",
    )
    assert aborted >= 1
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "suppressed"
    assert ex["close_reason"] == "founder_aborted"
    # No catch-up was minted for the deferred member.
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert int(ch["acknowledged_through_seq"]) == int(
        ch["required_through_seq"],
    )


def test_archive_suppresses_exchange_with_thread_archived_reason(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    db.discard_reply_delivery("THR-001", decline_reason="archive_started")
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "suppressed"
    assert ex["close_reason"] == "thread_archived"


def test_participant_removal_suppresses_removed_deferral_and_skips_catchup(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)  # D={CH}
    db.discard_reply_delivery(
        "THR-001", agent_name=CH, decline_reason="participant_removed",
    )
    rows = _deferral_rows(db, "THR-001", 1)
    assert rows[0]["state"] == "suppressed"
    # Exchange stays open (closure fires later at a proper evaluation point);
    # the removed pair is terminal (ack == required) so it can never receive
    # a catch-up.
    assert _open_exchange(db) is not None
    ch = _pair_row(db, "THR-001", CH)
    assert int(ch["acknowledged_through_seq"]) == int(
        ch["required_through_seq"],
    )
    # Closure later releases the surviving members. The cohort cascade must
    # terminalize first (quiescence is a cohort predicate).
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    arrivals = db.reaper_sweep_reply_exchanges()
    ch_catchups = [a for a in arrivals if a.agent_name == CH]
    assert ch_catchups == []  # removed member never receives a catch-up
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "released"


# ---------------------------------------------------------------------------
# TASK-6027 — unconditional strict exchange (founder ruling)
# ---------------------------------------------------------------------------
#
# The founder directed removal of ALL THREE switches: the shipped per-thread
# ``mention_routing_enabled``, the proposed per-thread ``reply_exchange_enabled``,
# and the proposed org key ``org_settings.threads.reply_exchange_enabled``.
# Mention routing and the strict mention-led exchange are UNCONDITIONAL.
# The ``mention_routing_enabled`` column remains as an inert legacy
# compatibility field (schema compat only); a persisted ``reply_exchange_enabled``
# column does NOT exist and a persisted org key is ignored. Adversarial proofs:
# none of the legacy persisted values can disable either behavior after upgrade.


def test_persisted_legacy_mention_routing_false_cannot_disable_exchange(tmp_path):
    """A raw persisted ``mention_routing_enabled = 0`` (shipped column,
    inert legacy) must NOT stop the strict exchange from opening/holding."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH, PL))
    db._conn.execute(
        "UPDATE threads SET mention_routing_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    _open_exchange_via_mention(db)          # still opens (P={EM}, D={CH,PL})
    ex = _open_exchange(db)
    assert ex["state"] == "open"
    # A no-mention follow-on inside E wakes ONLY the frozen cohort (hold
    # semantics intact despite the legacy persisted false).
    seq2, arrivals = _arrival(db, recipients=[EM, CH, PL])
    assert seq2 == 2
    assert [a.agent_name for a in arrivals] == [EM]
    for held in (CH, PL):
        row = _pair_row(db, "THR-001", held)
        assert row["queued_invocation_token"] is None
        assert row["running_invocation_token"] is None


def test_persisted_legacy_org_key_cannot_disable_exchange(tmp_path):
    """A persisted ``org_settings.threads`` section carrying the removed
    ``reply_exchange_enabled: false`` key (from a dev DB created by the
    unmerged PR) is IGNORED — the strict exchange remains unconditional."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    db.upsert_org_setting(
        "threads", json.dumps({
            "enabled": True,
            "reply_exchange_enabled": False,   # removed proposal — inert
        }),
    )
    _open_exchange_via_mention(db)
    ex = _open_exchange(db)
    assert ex["state"] == "open"
    seq2, arrivals = _arrival(db, recipients=[EM, CH])
    assert [a.agent_name for a in arrivals] == [EM]
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None  # held, not woken


def test_no_valid_mentions_still_broadcasts_outside_exchange(tmp_path):
    """No-valid-mention messages outside an exchange still broadcast to every
    recipient minus the speaker (mention routing unconditional)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH, PL))
    _seq, arrivals = _arrival(
        db, recipients=[EM, CH, PL], body="@founder @typo_agent",
    )
    assert sorted(a.agent_name for a in arrivals) == sorted([EM, CH, PL])
    # The mention set was empty -> no exchange opened.
    assert _open_exchange(db) is None


def test_reply_exchange_close_reason_check_has_no_disable_reasons(tmp_path):
    """The ``thread_reply_exchange.close_reason`` CHECK constraint no longer
    admits the removed disable/rollback reasons (``exchange_disabled`` /
    ``org_exchange_disabled``) — no code path can write them anymore."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ddl = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'thread_reply_exchange'",
    ).fetchone()["sql"]
    assert "exchange_disabled" not in ddl
    assert "org_exchange_disabled" not in ddl


# ---------------------------------------------------------------------------
# TASK-6057 — post-slot catch-up for released deferrals whose live slot does
# not cover the released range (reviewer TASK-6056 HIGH finding).
#
# Counterexample shape: a deferred pair owns a PRE-EXCHANGE running wake whose
# immutable claimed range ends before open_seq; priority settles; grace (or
# the 4h reaper) closes the exchange; the old wake then fails/timeouts. The
# released deferral must NOT strand: exactly ONE range-covering post-slot
# catch-up is minted when the blocking slot reaches a terminal outcome, and
# the durable marker survives daemon restart.
# ---------------------------------------------------------------------------


def _pre_claim_deferred_scenario(
    db: Database, *, priority_outcome: str = "decline",
) -> str:
    """Build the reviewer counterexample: seq 1 pre-exchange wake for CH
    (consultant_head) claimed, immutable running range 1..1; seq 2 founder
    mention @EM opens E with P={EM}, D={CH}; CH held, required -> 2; the
    priority cohort's cascade fully terminalizes with ``priority_outcome``.
    Returns CH's pre-exchange token."""
    _seq1, arrivals = _arrival(db, recipients=[CH])
    ch_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == CH)
    claim = _claim(db, ch_token)
    assert claim.running_through_seq == 1
    _open_exchange_via_mention(db)   # seq 2: P={EM}, D={CH}
    ch = _pair_row(db, "THR-001", CH)
    assert int(ch["required_through_seq"]) == 2
    assert ch["running_invocation_token"] == ch_token
    _settle_cascade(db, "THR-001", EM, outcome=priority_outcome)
    return ch_token


def _close_via_grace(db: Database):
    _backdate_exchange(db, "THR-001", last_activity_ago=timedelta(
        seconds=EXCHANGE_GRACE_SECONDS + 1,
    ))
    return db.reaper_sweep_reply_exchanges()


def _close_via_four_hours(db: Database):
    _backdate_exchange(db, "THR-001", opened_ago=timedelta(
        seconds=MAX_PRIORITY_WAIT_SECONDS + 1,
    ))
    return db.reaper_sweep_reply_exchanges()


def _deferral(db: Database, agent: str):
    return db._conn.execute(
        "SELECT * FROM thread_exchange_deferrals "
        "WHERE thread_id = 'THR-001' AND agent_name = ? ORDER BY exchange_id",
        (agent,),
    ).fetchall()


def test_grace_closure_non_covering_old_wake_failed_mints_post_slot_catchup(
    tmp_path,
):
    """Reviewer counterexample: closure reports the released deferral as
    pending (never coalesced-into-a-covering-wake); the old wake FAILS; the
    exactly-one range-covering catch-up is minted — no stranding."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    arrivals = _close_via_grace(db)
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "released"
    assert ex["close_reason"] == "quiescence"
    # No catch-up minted at closure: the old wake occupies the pair's single
    # slot (at-most-one invariant). The deferral is durably marked pending.
    assert all(a.invocation_token is None for a in arrivals)
    ch = _pair_row(db, "THR-001", CH)
    assert ch["running_invocation_token"] == ch_token  # old wake still live
    drow = _deferral(db, CH)
    assert len(drow) == 1
    assert drow[0]["state"] == "released"
    assert drow[0]["catchup_pending"] == 1
    assert drow[0]["mint_token_prefix"] is None
    pending_audits = [a for a in _exchange_audits(db)
                      if a["action"] == "thread_deferral_catchup_pending"]
    assert len(pending_audits) == 1
    assert pending_audits[0]["payload"]["reason"] == "old_wake_does_not_cover"
    # The old wake FAILS (a terminal outcome): the owed catch-up fires.
    settlement = _settle(db, ch_token, outcome="failed")
    assert settlement is not None
    assert settlement.acknowledged_through_seq == 0
    assert settlement.required_through_seq == 2
    assert settlement.retry_required is False      # not stranded
    assert settlement.follow_on_token is not None  # the owed catch-up
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] == settlement.follow_on_token
    assert ch["running_invocation_token"] is None
    assert int(ch["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 2
    # Durable marker consumed exactly once; the wake covers the full range.
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    minted_audits = [a for a in _exchange_audits(db)
                     if a["action"] == "thread_deferral_catchup_minted"]
    assert len(minted_audits) == 1
    settled = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_settled"
               and a["agent"] == CH][-1]
    assert settled["payload"]["catchup_minted"] is True
    assert settled["payload"]["retry_required"] is False
    # At-most-one slot preserved throughout: exactly one queued wake now.
    assert ch["queued_invocation_token"] and ch["running_invocation_token"] is None


def test_grace_closure_non_covering_old_wake_timeout_mints_post_slot_catchup(
    tmp_path,
):
    """Same counterexample with a TIMEOUT terminal outcome."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    settlement = _settle(db, ch_token, outcome="timeout")
    assert settlement is not None
    assert settlement.retry_required is False
    assert settlement.follow_on_token is not None
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] == settlement.follow_on_token
    assert _deferral(db, CH)[0]["catchup_pending"] == 0


def test_grace_closure_non_covering_old_wake_reply_uses_natural_follow_on(
    tmp_path,
):
    """Reply terminal outcome: the natural follow-on (2..2) IS the owed
    catch-up — exactly one wake, marker consumed, no double mint."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    settlement = _settle(db, ch_token, outcome="reply")
    assert settlement.acknowledged_through_seq == 1   # claimed coverage only
    assert settlement.retry_required is False
    assert settlement.follow_on_token is not None     # 2..2 follow-on
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] == settlement.follow_on_token
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    minted = [a for a in _exchange_audits(db)
              if a["action"] == "thread_deferral_catchup_minted"]
    assert len(minted) == 1
    # No second wake anywhere for CH.
    created = [a for a in _wake_audits(db)
               if a["action"] == "thread_reply_wake_created"
               and a["agent"] == CH]
    assert len(created) == 1  # only the closure-era wake at seq 1


def test_grace_closure_non_covering_old_wake_decline_uses_natural_follow_on(
    tmp_path,
):
    """Decline terminal outcome: same exactly-one semantics as reply."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    settlement = _settle(db, ch_token, outcome="decline")
    assert settlement.acknowledged_through_seq == 1
    assert settlement.retry_required is False
    assert settlement.follow_on_token is not None
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert _pair_row(db, "THR-001", CH)["queued_invocation_token"] is not None


def test_priority_reply_variant_still_mints_deferred_catchup(tmp_path):
    """The priority cohort settles with REPLY (not decline); the deferred
    pair's old wake fails afterwards; the catch-up still fires."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db, priority_outcome="reply")
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    settlement = _settle(db, ch_token, outcome="failed")
    assert settlement.follow_on_token is not None
    assert settlement.retry_required is False
    assert _deferral(db, CH)[0]["catchup_pending"] == 0


def test_four_hour_fail_open_closure_non_covering_old_wake_failed_mints_catchup(
    tmp_path,
):
    """4-hour absolute reaper (fail-open) closes the exchange; the released
    deferral's old wake fails; exactly one catch-up — not stranded."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    arrivals = _close_via_four_hours(db)
    ex = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert ex["state"] == "released"
    assert ex["close_reason"] == "max_priority_wait"
    assert all(a.invocation_token is None for a in arrivals)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    settlement = _settle(db, ch_token, outcome="timeout")
    assert settlement.follow_on_token is not None
    assert settlement.retry_required is False
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is not None
    assert ch["running_invocation_token"] is None


def test_restart_between_release_and_old_slot_settlement_recoverable_running(
    tmp_path,
):
    """Daemon restarts AFTER closure set the marker and BEFORE the old wake
    settles. The running wake is recoverable: it is terminalized as
    daemon_restart, replaced by ONE queued wake covering the full released
    range, and the marker is consumed — no second mint, no stranding."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    # Exactly one replacement (covering 1..2) — never a second deferred mint.
    assert len(ch_entries) == 1
    assert ch_entries[0].kind == "replacement_queued"
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] == ch_entries[0].invocation_token
    assert ch["running_invocation_token"] is None
    assert int(ch["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 2
    # The replacement covers the released range: marker consumed.
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    inv = _invocation_row(db, ch_token)
    assert inv["status"] == "failed"
    assert inv["decline_reason"] == "daemon_restart"
    # The replacement is claimable and covers the full released range.
    claim = _claim(db, ch_entries[0].invocation_token)
    assert claim.running_through_seq == 2


def test_restart_between_release_and_old_slot_settlement_nonrecoverable_running(
    tmp_path,
):
    """Restart with a NON-recoverable running wake (terminal receipt): the
    slot is cleared fail-closed and the pending catch-up marker is REVOKED in
    the same transaction — the authoritative fail-closed classification
    survives into marker reconciliation, so recovery never mints a catch-up
    from untrustworthy state (TASK-6065; supersedes the TASK-6057
    release-promise-survives-restart behavior for the invalid-ownership
    class). The retained unacknowledged range is served by the next
    conversational arrival (documented Phase-1 fail-open)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Corrupt the old receipt so recovery cannot replace it (already terminal).
    db._conn.execute(
        "UPDATE thread_invocations SET status = 'consumed' "
        "WHERE invocation_token = ?", (ch_token,),
    )
    db._conn.commit()
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []          # zero returned arrivals
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] \
        == "running_already_terminal_on_recovery"
    assert int(ch["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 2
    # Marker terminally revoked (never minted from untrustworthy state).
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    # Idempotent repeat recovery + restart safety: nothing minted ever again.
    assert db.recover_reply_delivery_state() == []
    db._conn.close()
    db2 = Database(tmp_path / "happyranch.db")
    assert db2.recover_reply_delivery_state() == []
    assert _deferral(db2, CH)[0]["catchup_pending"] == 0
    assert _pair_row(db2, "THR-001", CH)["queued_invocation_token"] is None
    assert _pair_row(db2, "THR-001", CH)["running_invocation_token"] is None


def test_genuinely_covering_slot_coalesces_without_marker(tmp_path):
    """A running wake whose immutable claimed range genuinely covers the
    released range coalesces at closure — no marker, and its later failure
    is the plain Phase-1 fail-open (retry_required), never a deferred-mint."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)   # seq 1: P={EM}, D={CH}; CH required=1
    # seq 2: mention pierce wakes CH; claim covers 1..2 (required=2 at claim).
    _seq2, arrivals = _arrival(
        db, mentions_body=f"@{CH} now", recipients=[EM, CH],
    )
    ch_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == CH)
    claim = _claim(db, ch_token)
    assert claim.running_through_seq == 2
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    arrivals2 = _close_via_grace(db)
    # CH coalesced into its genuinely covering wake: no marker, no mint.
    ch_coalesced = [a for a in arrivals2 if a.agent_name == CH]
    assert len(ch_coalesced) == 1
    assert ch_coalesced[0].invocation_token is None
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_pending"]
    # The wake covered the release; its failure is a plain delivery failure
    # (documented Phase-1 fail-open: retry_required for the next arrival).
    settlement = _settle(db, ch_token, outcome="failed")
    assert settlement.follow_on_token is None
    assert settlement.retry_required is True
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]


def test_pre_exchange_queued_slot_coalesces_without_marker(tmp_path):
    """A PRE-EXCHANGE QUEUED wake covers the released range at claim
    (running_through = required-at-claim; required is monotonic) — it
    coalesces at closure with no marker and no post-slot mint."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _seq1, arrivals = _arrival(db, recipients=[CH])
    ch_token = next(a.invocation_token for a in arrivals
                    if a.agent_name == CH)
    assert _pair_row(db, "THR-001", CH)["queued_invocation_token"] == ch_token
    _open_exchange_via_mention(db)   # CH held; required -> 2 (queued intact)
    _settle_cascade(db, "THR-001", EM, outcome="decline")
    arrivals2 = _close_via_grace(db)
    ch_coalesced = [a for a in arrivals2 if a.agent_name == CH]
    assert len(ch_coalesced) == 1
    assert ch_coalesced[0].invocation_token is None
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_pending"]
    # Claimed now, the queued wake covers the full released range.
    claim = _claim(db, ch_token)
    assert claim.running_through_seq == 2


def test_duplicate_closure_and_settlement_after_marker_are_exactly_once(
    tmp_path,
):
    """Duplicate closure/reaper evaluations after the marker are CAS
    no-ops; the post-slot settlement mints exactly once."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Duplicate reaper sweep: released exchange — no evaluation, no change.
    assert db.reaper_sweep_reply_exchanges() == []
    assert db.reconcile_reply_exchanges() == []
    ch = _pair_row(db, "THR-001", CH)
    assert ch["running_invocation_token"] == ch_token
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Settlement fires the catch-up exactly once.
    settlement = _settle(db, ch_token, outcome="failed")
    assert settlement.follow_on_token is not None
    minted = [a for a in _exchange_audits(db)
              if a["action"] == "thread_deferral_catchup_minted"]
    assert len(minted) == 1
    # A second settlement of the same token is a no-op (already terminal).
    assert _settle(db, ch_token, outcome="failed") is None
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] == settlement.follow_on_token
    assert ch["running_invocation_token"] is None
    assert int(ch["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 2


def test_pending_marker_cleared_by_later_covering_wake(tmp_path):
    """A marker left pending is consumed by any later covering wake (e.g. a
    new arrival minting the full residual range) — one wake satisfies it."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # A new conversational message arrives and mints CH a covering wake
    # (the old slot still runs — coalesced raise; after it settles failed,
    # the owed catch-up carries 2..3).
    _arrival(db, recipients=[EM, CH])   # seq 3
    settlement = _settle(db, ch_token, outcome="failed")
    assert settlement.follow_on_token is not None
    claim = _claim(db, settlement.follow_on_token)
    assert claim.running_through_seq == 3   # covers the full residual 2..3
    assert _deferral(db, CH)[0]["catchup_pending"] == 0


# ---------------------------------------------------------------------------
# TASK-6065 — fail-closed recovery branches revoke the pending marker
# ---------------------------------------------------------------------------
#
# The TASK-6063 reviewer counterexample: ``catchup_pending = 1`` PLUS both
# ownership slots populated. Recovery records
# ``corrupt_both_slots_on_recovery`` and clears the slots, but the post-loop
# marker reconciliation then saw no slot with ack < required and minted a
# ``deferred_catchup`` — fabricating runnable ownership from state the
# corruption branch declared untrustworthy. The structural repair: every
# fail-closed recovery classification (both-slots corruption, non-recoverable
# running slot, invalid queued started/ownership) revokes the pair's pending
# marker ATOMICALLY in the same transaction, so the authoritative terminal
# classification survives into marker reconciliation as a revoked marker,
# never as a mint. These regressions prove, per branch, ZERO minted tokens,
# ZERO returned arrivals, marker terminally cleared, truthful terminal
# reason/audit, idempotent repeat recovery, and restart safety.


def test_both_slots_corruption_with_pending_marker_mints_nothing(tmp_path):
    """TASK-6063 exact reproduction: catchup_pending=1 plus BOTH queued and
    running slots populated. Recovery records corrupt_both_slots_on_recovery,
    clears both slots, and revokes the pending marker in the same transaction
    — the post-loop reconciliation mints no deferred_catchup (zero tokens,
    zero returned arrivals, marker terminally cleared)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Corrupt: populate the queued slot too — both ownership slots set.
    db._conn.execute("BEGIN IMMEDIATE")
    extra = db._mint_reply_invocation_uncommitted("THR-001", CH, 3)
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET queued_invocation_token = ? "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (extra, CH),
    )
    db._conn.commit()
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []            # zero returned arrivals
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] == "corrupt_both_slots_on_recovery"
    assert int(ch["acknowledged_through_seq"]) == 0
    assert int(ch["required_through_seq"]) == 2
    # Marker terminally revoked — never minted from corrupt state.
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    # Both owned pending REPLY receipts retired by the pair-scoped sweep.
    for tok in (ch_token, extra):
        inv = _invocation_row(db, tok)
        assert inv["status"] == "failed"
        assert inv["decline_reason"] == "corrupt_both_slots_on_recovery"
    # Truthful audit: one cancelled wake with the marker revocation flagged.
    cancelled = [a for a in _wake_audits(db)
                 if a["action"] == "thread_reply_wake_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["payload"]["reason"] \
        == "corrupt_both_slots_on_recovery"
    assert cancelled[0]["payload"]["catchup_suppressed"] is True
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    # Idempotent repeat recovery + restart safety.
    assert db.recover_reply_delivery_state() == []
    db._conn.close()
    db2 = Database(tmp_path / "happyranch.db")
    assert db2.recover_reply_delivery_state() == []
    row2 = _pair_row(db2, "THR-001", CH)
    assert row2["queued_invocation_token"] is None
    assert row2["running_invocation_token"] is None
    assert _deferral(db2, CH)[0]["catchup_pending"] == 0


def test_invalid_queued_started_with_pending_marker_mints_nothing(tmp_path):
    """Queued slot referencing a STARTED receipt (invalid queued ownership)
    with a pending marker: the pair-scoped sweep retires the owned pending
    REPLY, the queued slot is cleared fail-closed, and the pending marker is
    revoked in the same transaction — zero minted/returned tokens."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Move the claimed (started) receipt into the QUEUED slot: a queued wake
    # must be unstarted, so this is invalid queued ownership.
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET "
        "queued_invocation_token = running_invocation_token, "
        "running_invocation_token = NULL, "
        "running_from_seq = NULL, running_through_seq = NULL "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (CH,),
    )
    db._conn.commit()
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] \
        == "invalid_queued_started_on_recovery"
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    # The started queued receipt was retired by the pair-scoped sweep.
    assert _invocation_row(db, ch_token)["decline_reason"] \
        == "invalid_queued_started_on_recovery"
    assert _invocation_row(db, ch_token)["status"] == "failed"
    # Truthful audit: one cancelled wake with the marker revocation flagged.
    cancelled = [a for a in _wake_audits(db)
                 if a["action"] == "thread_reply_wake_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["payload"]["catchup_suppressed"] is True
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    assert db.recover_reply_delivery_state() == []


def test_invalid_queued_token_with_pending_marker_mints_nothing(tmp_path):
    """Queued slot referencing a TERMINAL receipt (invalid queued ownership)
    with a pending marker: the queued slot is cleared fail-closed and the
    pending marker is revoked in the same transaction — zero minted/returned
    tokens."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Point the queued slot at a TERMINAL receipt (the running receipt was
    # consumed): invalid queued ownership.
    db._conn.execute(
        "UPDATE thread_invocations SET status = 'consumed' "
        "WHERE invocation_token = ?", (ch_token,),
    )
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET "
        "queued_invocation_token = running_invocation_token, "
        "running_invocation_token = NULL, "
        "running_from_seq = NULL, running_through_seq = NULL "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (CH,),
    )
    db._conn.commit()
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] \
        == "invalid_queued_token_on_recovery"
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    assert db.recover_reply_delivery_state() == []


def _corrupt_running_terminal(db: Database, tok: str) -> None:
    db._conn.execute(
        "UPDATE thread_invocations SET status = 'consumed' "
        "WHERE invocation_token = ?", (tok,),
    )
    db._conn.commit()


def _corrupt_running_missing(db: Database, tok: str) -> None:
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET running_invocation_token = "
        "'no-such-token-0000' WHERE thread_id = 'THR-001' AND agent_name = ?",
        (CH,),
    )
    db._conn.commit()


def _corrupt_running_wrong_pair(db: Database, tok: str) -> None:
    other = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id = 'THR-001' AND agent_name = 'engineering_manager' "
        "LIMIT 1",
    ).fetchone()["invocation_token"]
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET running_invocation_token = ? "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (other, CH),
    )
    db._conn.commit()


def _corrupt_running_malformed_range(db: Database, tok: str) -> None:
    db._conn.execute(
        "UPDATE thread_reply_delivery_state SET running_from_seq = 5, "
        "running_through_seq = 1 "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (CH,),
    )
    db._conn.commit()


def _corrupt_running_missing_start(db: Database, tok: str) -> None:
    db._conn.execute(
        "UPDATE thread_invocations SET started_at = NULL "
        "WHERE invocation_token = ?", (tok,),
    )
    db._conn.commit()


@pytest.mark.parametrize(
    "corrupt_fn,expected_reason",
    [
        (_corrupt_running_terminal,
         "running_already_terminal_on_recovery"),
        (_corrupt_running_missing,
         "invalid_running_token_on_recovery"),
        (_corrupt_running_wrong_pair,
         "invalid_running_token_on_recovery"),
        (_corrupt_running_malformed_range,
         "malformed_running_range_on_recovery"),
        (_corrupt_running_missing_start,
         "running_missing_start_evidence_on_recovery"),
    ],
)
def test_nonrecoverable_running_variants_with_pending_marker_mint_nothing(
    tmp_path, corrupt_fn, expected_reason,
):
    """Every invalid/malformed RUNNING ownership subclass with a pending
    marker: the slot is cleared fail-closed, the marker is revoked in the
    same transaction, and recovery returns zero tokens — never minting a
    catch-up from state it declared untrustworthy."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    corrupt_fn(db, ch_token)
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []            # zero returned arrivals
    ch = _pair_row(db, "THR-001", CH)
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] == expected_reason
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    # Idempotent repeat recovery + restart safety.
    assert db.recover_reply_delivery_state() == []
    db._conn.close()
    db2 = Database(tmp_path / "happyranch.db")
    assert db2.recover_reply_delivery_state() == []
    assert _deferral(db2, CH)[0]["catchup_pending"] == 0
    row2 = _pair_row(db2, "THR-001", CH)
    assert row2["queued_invocation_token"] is None
    assert row2["running_invocation_token"] is None


def test_orphan_delivery_row_with_pending_marker_mints_nothing(tmp_path):
    """A pending marker whose pair has NO delivery-state row at all: the
    post-loop reconciliation clears the orphaned marker and never mints
    (the same fail-closed class as the orphan deferral sweep)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Orphan the marker: delete the pair's delivery-state row entirely.
    db._conn.execute(
        "DELETE FROM thread_reply_delivery_state "
        "WHERE thread_id = 'THR-001' AND agent_name = ?", (CH,),
    )
    db._conn.commit()
    entries = db.recover_reply_delivery_state()
    ch_entries = [e for e in entries if e.agent_name == CH]
    assert ch_entries == []
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    assert db.recover_reply_delivery_state() == []


def test_abort_with_pending_marker_mints_nothing_and_clears_marker(tmp_path):
    """Abort/archive/removal suppression with a pending marker: the discard
    sets acknowledged = required (full coverage), so the post-loop
    reconciliation clears the marker and never mints a wake after the human
    stopped (G5 suppression preserved across the marker lifecycle)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    ch_token = _pre_claim_deferred_scenario(db)
    _close_via_grace(db)
    assert _deferral(db, CH)[0]["catchup_pending"] == 1
    # Whole-thread abort: terminal suppression for every pair.
    db.discard_reply_delivery(
        thread_id="THR-001", decline_reason="founder_aborted",
        status=ThreadInvocationStatus.FAILED, agent_name=None,
    )
    ch = _pair_row(db, "THR-001", CH)
    assert int(ch["acknowledged_through_seq"]) \
        == int(ch["required_through_seq"])
    assert ch["queued_invocation_token"] is None
    assert ch["running_invocation_token"] is None
    assert ch["last_terminal_reason"] == "founder_aborted"
    # The exchange is suppressed — no catch-up, and no marker-driven mint on
    # a later restart (the scan sees full coverage and clears the marker).
    assert not [a for a in _exchange_audits(db)
                if a["action"] == "thread_deferral_catchup_minted"]
    entries = db.recover_reply_delivery_state()
    assert entries == []
    assert _deferral(db, CH)[0]["catchup_pending"] == 0
    db._conn.close()
    db2 = Database(tmp_path / "happyranch.db")
    assert db2.recover_reply_delivery_state() == []
    assert _deferral(db2, CH)[0]["catchup_pending"] == 0
