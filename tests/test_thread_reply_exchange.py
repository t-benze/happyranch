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
  * U6 — additive per-thread ``reply_exchange_enabled`` + org kill switch;
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
    assert "reply_exchange_enabled" in cols
    # Column default 1 (independent rollback control — additive).
    dflt = db._conn.execute(
        "SELECT dflt_value FROM pragma_table_info('threads') "
        "WHERE name = 'reply_exchange_enabled'",
    ).fetchone()
    assert dflt["dflt_value"] == "1"
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
    # With exchange DISABLED (mode 1 = shipped mention routing): the WAKE SET
    # is byte-identical to shipped behavior (exactly the mention set mints),
    # while U0 full-recipient obligations still raise required for the
    # unmentioned pair (founder-approved S3 — obligation rows are separate
    # from wake-mint eligibility; no token, no wake audit).
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
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
# U6 — per-thread flag + org kill switch (store level; API in
# tests/daemon/test_thread_exchange_routing_settings_api.py)
# ---------------------------------------------------------------------------


def test_exchange_flag_disables_exchange(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    assert db._thread_reply_exchange_enabled("THR-001") is False
    # A founder mention message no longer opens an exchange.
    _seq, arrivals = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert _open_exchange(db) is None
    # Mention routing itself is untouched (mention-set mode intact).
    assert [a.agent_name for a in arrivals] == [EM]


def test_mention_routing_flag_disables_exchange_but_keeps_broadcast(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    db._conn.execute(
        "UPDATE threads SET mention_routing_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    assert db._thread_reply_exchange_enabled("THR-001") is False
    _seq, arrivals = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert _open_exchange(db) is None
    # Mode 0: pure broadcast (participant order preserved).
    assert [a.agent_name for a in arrivals] == [EM, CH]


def test_org_kill_switch_overrides_per_thread_flag(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    # Per-thread on, org kill-switch false → exchange disabled everywhere.
    db.upsert_org_setting(
        "threads", json.dumps({"reply_exchange_enabled": False}),
    )
    assert db._thread_reply_exchange_enabled("THR-001") is False
    # Org setting absent → per-thread governs (still on).
    db._conn.execute(
        "DELETE FROM org_settings WHERE section = 'threads'",
    )
    db._conn.commit()
    assert db._thread_reply_exchange_enabled("THR-001") is True


def test_exchange_routing_toggle_with_audit_is_atomic_and_idempotent(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert transitioned is True
    assert arrivals == []  # no open epoch to retire
    t = db.get_thread("THR-001")
    assert t.reply_exchange_enabled is False
    # mention_routing_enabled untouched (independent control).
    assert t.mention_routing_enabled is True
    # Idempotent no-op: no write, no audit, no retirement.
    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert transitioned is False
    assert arrivals == []
    rows = db._conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log "
        "WHERE action = 'thread_exchange_routing_changed'",
    ).fetchone()
    assert rows["n"] == 1
    # Unknown thread raises.
    with pytest.raises(ValueError):
        db.set_thread_exchange_routing_with_audit(
            "THR-MISSING", enabled=False,
        )


# ---------------------------------------------------------------------------
# TASK-5982 — coverage-safe retirement of open epochs on disable (rollback
# compatibility). Disabling the per-thread flag or the org kill-switch
# atomically CAS-closes/terminalizes each open epoch with a distinct
# disable/rollback reason and mints exactly-one slot-checked catch-up per
# uncovered deferred pair (never a fabricated watermark, never a hidden
# resumable row). Re-enable creates only new epochs.
# ---------------------------------------------------------------------------


def test_per_thread_disable_retires_open_epoch_with_catch_up(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    open_seq = _open_exchange_via_mention(db)
    ex = _open_exchange(db)
    assert ex is not None
    assert int(ex["open_seq"]) == open_seq
    # Deferred member CH holds an uncovered obligation (acked = seq-1).
    ch_row = _pair_row(db, "THR-001", CH)
    assert ch_row["acknowledged_through_seq"] < ch_row["required_through_seq"]
    assert ch_row["queued_invocation_token"] is None

    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert transitioned is True
    # Exactly-one slot-checked catch-up: CH mints; EM's priority wake is
    # coalesced (at-most-one queued/running slot honored).
    tokens = _tokens(arrivals)
    assert len(tokens) == 1
    ch_catchup = [a for a in arrivals if a.agent_name == CH][0]
    assert ch_catchup.invocation_token == tokens[0]
    assert ch_catchup.from_seq == int(ch_row["acknowledged_through_seq"]) + 1
    assert ch_catchup.through_seq == int(ch_row["required_through_seq"])
    # The token is the durable delivery-state queued slot (exactly-once
    # enqueue ownership — the caller enqueues it after commit).
    ch_after = _pair_row(db, "THR-001", CH)
    assert ch_after["queued_invocation_token"] == tokens[0]

    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    assert row["closed_at"] is not None
    # Deferral rows released; closed audit truthful.
    deferrals = _deferral_rows(db, "THR-001", int(row["exchange_id"]))
    assert [d["state"] for d in deferrals] == ["released"]
    closed = [a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"]
    assert len(closed) == 1
    assert closed[0]["payload"]["close_reason"] == "exchange_disabled"

    # Shipped mention-routing mode is preserved after disable.
    t = db.get_thread("THR-001")
    assert t.reply_exchange_enabled is False
    assert t.mention_routing_enabled is True
    _seq, wake = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    assert [a.agent_name for a in wake] == [EM]
    assert _open_exchange(db) is None  # no new epoch while disabled


def test_org_kill_switch_disable_retires_open_epochs_across_threads(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, thread_id="THR-001", participants=(EM, CH))
    _make_thread(db, thread_id="THR-002", participants=(EM, PL))
    _open_exchange_via_mention(db, thread_id="THR-001")
    _open_exchange_via_mention(db, thread_id="THR-002")

    transitioned, arrivals = db.set_org_exchange_routing_with_audit(
        enabled=False,
    )
    assert transitioned is True
    # One catch-up per uncovered deferred pair across BOTH threads (EM's
    # priority wakes are coalesced arrivals, not new tokens).
    tokens = _tokens(arrivals)
    assert len(tokens) == 2
    assert sorted({a.agent_name for a in arrivals if a.invocation_token}) == [CH, PL]
    for tid in ("THR-001", "THR-002"):
        row = db._conn.execute(
            "SELECT * FROM thread_reply_exchange WHERE thread_id = ?",
            (tid,),
        ).fetchone()
        assert row["state"] == "released"
        assert row["close_reason"] == "org_exchange_disabled"
        assert db._thread_reply_exchange_enabled(tid) is False
    # Per-thread flags untouched by the org switch.
    for tid in ("THR-001", "THR-002"):
        t = db.get_thread(tid)
        assert t.reply_exchange_enabled is True
        assert t.mention_routing_enabled is True

    # Repeated disable: idempotent — no transition, no re-mint, no re-audit.
    transitioned, arrivals = db.set_org_exchange_routing_with_audit(
        enabled=False,
    )
    assert transitioned is False
    assert arrivals == []


def test_repeated_disable_is_idempotent_no_double_mint(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)

    t1, arrivals1 = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert t1 is True
    assert len(_tokens(arrivals1)) == 1
    ch_row = _pair_row(db, "THR-001", CH)
    token = ch_row["queued_invocation_token"]
    assert token is not None

    # A second disable is a true no-op: no transition, no new mint, and the
    # epoch (already released) is a CAS miss — no duplicate close audit.
    t2, arrivals2 = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert t2 is False
    assert arrivals2 == []
    assert _pair_row(db, "THR-001", CH)["queued_invocation_token"] == token
    closed = [a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"]
    assert len(closed) == 1


def test_disable_then_reenable_creates_new_epoch_only(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    seq1 = _open_exchange_via_mention(db)
    ex1 = _open_exchange(db)
    assert ex1 is not None

    t, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert t is True
    assert len(_tokens(arrivals)) == 1

    # Re-enable: no stale epoch to retire; a fresh epoch can open.
    t, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=True,
    )
    assert t is True
    assert arrivals == []
    seq2 = _open_exchange_via_mention(db)
    ex2 = _open_exchange(db)
    assert ex2 is not None
    assert int(ex2["exchange_id"]) > int(ex1["exchange_id"])
    assert int(ex2["open_seq"]) > int(ex1["open_seq"])
    # The historical epoch stays terminal — re-enable never resurrects it.
    rows = db._conn.execute(
        "SELECT state, close_reason FROM thread_reply_exchange "
        "WHERE thread_id = 'THR-001' ORDER BY exchange_id",
    ).fetchall()
    assert [(r["state"], r["close_reason"]) for r in rows] == [
        ("released", "exchange_disabled"), ("open", None),
    ]


def test_historical_open_row_retired_on_disable_self_heal(tmp_path):
    """Code-revert/redeploy-equivalent scenario: an open epoch left by
    pre-fix code (flag already off, row still OPEN — exactly the dormant
    resumable row the old disable produced). The next disable call
    self-heals: it retires the stale row with catch-up even though the flag
    does not transition (idempotent retry that still converges)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # Simulate the pre-fix disable: flag flipped with the epoch left open.
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    assert _open_exchange(db) is not None

    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert transitioned is False  # flag already off — no re-audit
    assert len(_tokens(arrivals)) == 1  # but the stale epoch retires + catch-up
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"

    # Re-enable afterwards: nothing stale to resurrect; new epochs only.
    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=True,
    )
    assert transitioned is True
    assert arrivals == []
    assert _open_exchange(db) is None


def test_historical_open_row_retired_on_reenable(tmp_path):
    """Code-revert/redeploy-equivalent: a stale open row exists while the
    flag is off and the founder re-enables directly (no intervening disable
    call). The re-enable retires the stale epoch BEFORE the flag flips so it
    can never be resurrected."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    assert _open_exchange(db) is not None

    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=True,
    )
    assert transitioned is True
    assert len(_tokens(arrivals)) == 1
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    # No stale row remains to be consulted once enabled.
    assert _open_exchange(db) is None


def test_org_kill_switch_reenable_retires_stale_rows(tmp_path):
    """Org kill-switch set false by a pre-fix/raw write (epoch left open),
    then re-enabled through the store method: the stale epoch is retired
    with the org reason before the switch clears."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    db.upsert_org_setting(
        "threads", json.dumps({"reply_exchange_enabled": False}),
    )
    assert _open_exchange(db) is not None  # dormant row, not yet retired

    transitioned, arrivals = db.set_org_exchange_routing_with_audit(
        enabled=True,
    )
    assert transitioned is True
    assert len(_tokens(arrivals)) == 1
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "org_exchange_disabled"
    assert db._thread_reply_exchange_enabled("THR-001") is True
    assert _open_exchange(db) is None


def test_disable_retires_epoch_at_write_seam(tmp_path):
    """Belt: a raw/pre-fix disable leaves an open row; the next
    conversational write retires it (CAS-close + catch-up joining the
    write's arrivals) and the message itself is mention-routed normally."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()
    assert _open_exchange(db) is not None

    _seq, arrivals = _arrival(
        db, mentions_body=f"@{EM} please", recipients=[EM, CH],
    )
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    # CH's uncovered obligation is covered by the catch-up token minted at
    # the write seam (exactly-once slot ownership).
    assert _pair_row(db, "THR-001", CH)["queued_invocation_token"] is not None
    # Mention routing mode intact: the message wakes the mention set.
    assert EM in [a.agent_name for a in arrivals]
    assert _open_exchange(db) is None


def test_disable_retires_epoch_at_reaper_seam(tmp_path):
    """Belt: a raw/pre-fix disable leaves an open row; the 30s reaper tick
    retires it with catch-up (no sleeps — direct reaper call)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    db._conn.execute(
        "UPDATE threads SET reply_exchange_enabled = 0 WHERE id = 'THR-001'",
    )
    db._conn.commit()

    arrivals = db.reaper_sweep_reply_exchanges()
    assert len(_tokens(arrivals)) == 1
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    # A second tick is a CAS miss — no double mint.
    assert db.reaper_sweep_reply_exchanges() == []


def test_disable_races_reaper_closure_cas_exactly_once(tmp_path, monkeypatch):
    """Disable racing the reaper/closure over the SAME open epoch: the
    RLock serializes the two transactions and the CAS makes the loser a
    silent miss — exactly one catch-up mint, one close audit, no double
    release. Deterministic entered/release seam (no timing sleeps, no
    threading.Barrier)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)

    entered = threading.Event()
    release = threading.Event()
    real_close = Database._close_reply_exchange_uncommitted
    call_count = {"n": 0}

    def seamed_close(self, thread_id, exchange_id, *, reason):
        call_count["n"] += 1
        if call_count["n"] == 1:
            entered.set()
            assert release.wait(timeout=15)
        return real_close(self, thread_id, exchange_id, reason=reason)

    monkeypatch.setattr(
        Database, "_close_reply_exchange_uncommitted", seamed_close,
    )
    results: dict = {}

    def do_disable():
        results["disable"] = db.set_thread_exchange_routing_with_audit(
            "THR-001", enabled=False,
        )

    def do_reaper():
        results["reaper"] = db.reaper_sweep_reply_exchanges()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_disable = pool.submit(do_disable)
        assert entered.wait(timeout=15)  # disable holds the lock inside close
        f_reaper = pool.submit(do_reaper)  # reaper queues on the RLock
        release.set()  # let the disable finish; the reaper then sees no open row
        f_disable.result(timeout=15)
        f_reaper.result(timeout=15)

    transitioned, arrivals = results["disable"]
    assert transitioned is True
    assert len(_tokens(arrivals)) == 1
    assert results["reaper"] == []  # CAS miss — epoch already terminal
    assert call_count["n"] == 1  # exactly one close fired
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    closed = [a for a in _exchange_audits(db)
              if a["action"] == "thread_exchange_closed"]
    assert len(closed) == 1


def test_disable_races_inflight_pierce_wake_coalesces_no_second_token(tmp_path):
    """Disable racing an in-flight pierce wake: a deferred member already
    holds a covering queued wake (mention-pierce) — retirement must NOT mint
    a second token (slot-checked exactly-once); the existing wake covers the
    residual range."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, participants=(EM, CH))
    _open_exchange_via_mention(db)
    # Pierce-wake CH mid-exchange (mentions pierce without joining cohort).
    _seq, arrivals = _arrival(
        db, mentions_body=f"@{CH} please", recipients=[EM, CH],
    )
    ch_before = _pair_row(db, "THR-001", CH)
    assert ch_before["queued_invocation_token"] is not None

    transitioned, arrivals = db.set_thread_exchange_routing_with_audit(
        "THR-001", enabled=False,
    )
    assert transitioned is True
    # No second token for CH — the pierce wake owns the residual.
    assert all(a.invocation_token is None for a in arrivals)
    ch_after = _pair_row(db, "THR-001", CH)
    assert ch_after["queued_invocation_token"] == ch_before["queued_invocation_token"]
    row = db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = 'THR-001'",
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
