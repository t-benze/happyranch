"""Phase-2 mention routing (THR-198) Slice A — schema, migration, store.

Covers the additive storage contract approved at THR-198 seq 108-109:

  * threads.mention_routing_enabled INTEGER NOT NULL DEFAULT 1
  * thread_messages.mentions_json TEXT (NULL for system/decline + history)

Lifecycle fixtures: fresh DB (CREATE defines columns, ALTER swallowed),
pre-change legacy DB (ALTER adds columns), restart/idempotent double-open,
inserts on both shapes, model/read round-trips, statement-identical
migration convergence, store-seam persistence for every conversational
write path, rollback/compensation seams, GH-688 coalescing preservation,
and TASK_FOLLOWUP/BOOTSTRAP isolation.

Slice A deliberately does NOT change production wake routing — every
assertion that touches fan-out pins the current broadcast behavior.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadMessageKind,
    ThreadRecord,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _column_info(db: Database, table: str, name: str) -> dict | None:
    for row in db._conn.execute(f"PRAGMA table_info({table})"):
        if row["name"] == name:
            return {
                "type": row["type"],
                "notnull": row["notnull"],
                "dflt_value": row["dflt_value"],
            }
    return None


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


# ---------------------------------------------------------------------------
# fresh DB — columns defined in CREATE TABLE; ALTER swallowed
# ---------------------------------------------------------------------------


def test_fresh_db_defines_both_columns(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    enabled = _column_info(db, "threads", "mention_routing_enabled")
    assert enabled is not None
    assert enabled["type"] == "INTEGER"
    assert enabled["notnull"] == 1
    assert enabled["dflt_value"] == "1"

    mentions = _column_info(db, "thread_messages", "mentions_json")
    assert mentions is not None
    assert mentions["type"] == "TEXT"
    assert mentions["notnull"] == 0
    assert mentions["dflt_value"] is None

    # The idempotent ALTER statements exist in the migration block (they
    # raise duplicate-column on fresh DBs and are swallowed). The column
    # definition fragment ``mention_routing_enabled INTEGER NOT NULL
    # DEFAULT 1`` is shared verbatim by the CREATE definition and the ALTER,
    # so fresh-CREATE and ALTER paths converge (behaviorally proven by
    # test_migrated_and_fresh_column_metadata_are_identical).
    src = Path(
        "runtime/infrastructure/database.py"
    ).read_text()
    assert "ALTER TABLE threads ADD COLUMN " in src
    assert "mention_routing_enabled INTEGER NOT NULL DEFAULT 1" in src
    assert "ALTER TABLE thread_messages ADD COLUMN mentions_json TEXT" in src


def test_fresh_db_init_succeeds_and_reopen_is_noop(tmp_path):
    path = tmp_path / "happyranch.db"
    db1 = Database(path)
    db1._conn.close()
    # Second open re-runs every migration incl. the two new ALTERs — no error.
    db2 = Database(path)
    assert _column_info(db2, "threads", "mention_routing_enabled") is not None
    assert _column_info(db2, "thread_messages", "mentions_json") is not None
    db2._conn.close()
    # Third open still fine (idempotent across restarts).
    db3 = Database(path)
    assert _column_info(db3, "threads", "mention_routing_enabled") is not None
    assert _column_info(db3, "thread_messages", "mentions_json") is not None


# ---------------------------------------------------------------------------
# legacy pre-change DB — ALTER migration path
# ---------------------------------------------------------------------------


def _build_legacy_db(path: Path) -> None:
    """Construct a representative pre-change database: every current column
    except the two new ones, with legacy rows seeded under the OLD column
    sets. The two new columns are dropped from a fresh schema (SQLite
    DROP COLUMN), yielding exactly the pre-change inventory."""
    db = Database(path)
    db._conn.execute(
        "ALTER TABLE threads DROP COLUMN mention_routing_enabled"
    )
    db._conn.execute(
        "ALTER TABLE thread_messages DROP COLUMN mentions_json"
    )
    db._conn.commit()
    # Seed legacy rows with the pre-change column sets.
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-001', 'legacy thread', '2026-01-01T00:00:00+00:00', 'open')"
    )
    db._conn.execute(
        "INSERT INTO thread_messages "
        "(thread_id, seq, speaker, kind, body_markdown, created_at) "
        "VALUES ('THR-001', 1, 'founder', 'message', 'legacy body', "
        "'2026-01-01T00:00:00+00:00')"
    )
    db._conn.execute(
        "INSERT INTO thread_messages "
        "(thread_id, seq, speaker, kind, body_markdown, created_at) "
        "VALUES ('THR-001', 2, 'system', 'system', 'sys', "
        "'2026-01-01T00:00:01+00:00')"
    )
    db._conn.commit()
    db._conn.close()


def test_legacy_db_missing_columns_migrates_with_defaults(tmp_path):
    path = tmp_path / "happyranch.db"
    _build_legacy_db(path)

    db = Database(path)  # migration runs
    # Both columns now present, metadata matches the fresh shape.
    assert _column_info(db, "threads", "mention_routing_enabled") == {
        "type": "INTEGER", "notnull": 1, "dflt_value": "1",
    }
    assert _column_info(db, "thread_messages", "mentions_json") == {
        "type": "TEXT", "notnull": 0, "dflt_value": None,
    }
    # Existing rows survive; defaults apply: the inert legacy column keeps
    # enabled=1 (schema compatibility only — never read/written for
    # behavior), mentions_json=NULL. ThreadRecord no longer exposes the
    # switch (TASK-6027 founder ruling — unconditional mention routing).
    t = db.get_thread("THR-001")
    assert t is not None
    assert not hasattr(t, "mention_routing_enabled")
    row = db._conn.execute(
        "SELECT mention_routing_enabled FROM threads WHERE id='THR-001'"
    ).fetchone()
    assert row["mention_routing_enabled"] == 1
    # Historical message rows read as empty mentions (NULL -> []).
    msgs = db.list_thread_messages("THR-001")
    assert [m.mentions for m in msgs] == [[], []]
    assert _mentions_json(db, "THR-001", 1) is None
    assert _mentions_json(db, "THR-001", 2) is None
    # No data loss: seq count and row count identical.
    n = db._conn.execute(
        "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id='THR-001'"
    ).fetchone()["n"]
    assert n == 2


def test_migrated_and_fresh_column_metadata_are_identical(tmp_path):
    """Statement-identical migration: fresh-CREATE and ALTER paths converge
    to byte-identical column metadata."""
    fresh = Database(tmp_path / "fresh.db")
    fresh_info = (
        _column_info(fresh, "threads", "mention_routing_enabled"),
        _column_info(fresh, "thread_messages", "mentions_json"),
    )
    fresh._conn.close()

    legacy_path = tmp_path / "legacy.db"
    _build_legacy_db(legacy_path)
    migrated = Database(legacy_path)
    migrated_info = (
        _column_info(migrated, "threads", "mention_routing_enabled"),
        _column_info(migrated, "thread_messages", "mentions_json"),
    )
    assert migrated_info == fresh_info


def test_inserts_work_on_both_shapes(tmp_path):
    """insert_thread + record_conversational_arrival succeed and persist the
    new columns on fresh AND migrated (legacy) schemas."""
    fresh = Database(tmp_path / "fresh.db")
    _make_thread(fresh, "THR-F", ("alpha",))
    fresh.record_conversational_arrival(
        thread_id="THR-F", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha hi",
        recipients=["alpha"],
    )
    assert _mentions_json(fresh, "THR-F", 1) == '["alpha"]'

    legacy_path = tmp_path / "legacy.db"
    _build_legacy_db(legacy_path)
    migrated = Database(legacy_path)
    migrated.add_thread_participant("THR-001", "alpha", added_by="founder")
    migrated.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="migrated shape",
        recipients=["alpha"],
    )
    assert _mentions_json(migrated, "THR-001", 3) == "[]"


# ---------------------------------------------------------------------------
# model mappings / round trips
# ---------------------------------------------------------------------------


def test_mention_routing_enabled_is_inert_legacy_column_only(tmp_path):
    """TASK-6027 founder ruling: ``mention_routing_enabled`` remains a
    shipped schema-compat column (NOT NULL DEFAULT 1) but is NEVER exposed
    on ThreadRecord, NEVER written through a store/product surface, and
    NEVER read to alter routing (unconditional mention routing). A raw
    persisted value of 0 must not change any behavior."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    t = db.get_thread("THR-001")
    assert not hasattr(t, "mention_routing_enabled")
    # The column exists with the shipped default (schema compat).
    row = db._conn.execute(
        "SELECT mention_routing_enabled FROM threads WHERE id='THR-001'"
    ).fetchone()
    assert row["mention_routing_enabled"] == 1
    # A raw persisted 0 (legacy data) changes nothing: routing still
    # narrows to the valid mention set.
    db._conn.execute(
        "UPDATE threads SET mention_routing_enabled = 0 WHERE id='THR-001'"
    )
    db._conn.commit()
    for name in ("alpha", "bravo", "charlie"):
        db.add_thread_participant("THR-001", name, added_by="founder")
    arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="only @bravo please",
        recipients=["alpha", "bravo", "charlie"],
    )[1]
    assert [a.agent_name for a in arrivals] == ["bravo"]


def test_thread_message_mentions_roundtrip(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo"))
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="check @bravo and @alpha",
        recipients=["alpha", "bravo"],
    )
    msg = db.get_thread_message_by_seq("THR-001", 1)
    assert msg is not None
    assert msg.mentions == ["bravo", "alpha"]
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].mentions == ["bravo", "alpha"]
    assert msgs[0].body_markdown == "check @bravo and @alpha"


# ---------------------------------------------------------------------------
# store-seam persistence — every conversational write path derives mentions
# ---------------------------------------------------------------------------


def test_arrival_persists_mentions_json_matrix(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo", "charlie"))

    # Valid participant mention -> canonical name persisted.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="please look @bravo",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 1) == '["bravo"]'

    # Multiple valid mentions -> exactly that set (deduped, body order).
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="@charlie and @bravo and @charlie again",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 2) == '["charlie", "bravo"]'

    # Zero mentions -> empty array (writer ran; NOT NULL-missing).
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="no mentions here",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 3) == "[]"

    # Invalid/nonparticipant-only (@founder literal + typo) -> empty array.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@founder @typo_agent",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 4) == "[]"

    # Speaker self-mention excluded; other valid mention kept.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="alpha",
        kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha self @bravo",
        recipients=["bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 5) == '["bravo"]'

    # Mention of a non-participant (roster is live at write time) -> empty.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="where is @delta?",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert _mentions_json(db, "THR-001", 6) == "[]"


def test_reply_persists_mentions_json(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo", "charlie"))
    seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )
    seq2, _settlement, _broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="cc @bravo please", attachments=[],
        token=alpha_token, token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert seq2 == seq1 + 1
    assert _mentions_json(db, "THR-001", seq2) == '["bravo"]'


def test_system_and_decline_rows_stay_null(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha",))
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM, body_markdown="sys note",
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.DECLINE, body_markdown="decline note",
    )
    assert _mentions_json(db, "THR-001", 1) is None
    assert _mentions_json(db, "THR-001", 2) is None


def test_system_rows_derived_from_body_containing_mentions_still_null(tmp_path):
    """A system row whose body text contains @tokens must stay NULL — the
    mentions signal is conversational-write-only, never system rows."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha",))
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM, body_markdown="assigned @alpha",
    )
    assert _mentions_json(db, "THR-001", 1) is None


# ---------------------------------------------------------------------------
# production wake fan-out is UNCHANGED in this slice
# ---------------------------------------------------------------------------


def test_mentioned_and_unmentioned_bodies_differ_only_by_mentions(tmp_path):
    """Slice B: a body that @-mentions a participant wakes exactly that
    participant; a plain body broadcasts. Uses two identical threads so the
    first arrival on each pair mints a fresh token (no coalescing shadow)."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-A", ("alpha", "bravo", "charlie"))
    _make_thread(db, "THR-B", ("alpha", "bravo", "charlie"))

    _seq1, a1 = db.record_conversational_arrival(
        thread_id="THR-A", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="only @bravo should care",
        recipients=["alpha", "bravo", "charlie"],
    )
    _seq2, a2 = db.record_conversational_arrival(
        thread_id="THR-B", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="plain broadcast",
        recipients=["alpha", "bravo", "charlie"],
    )
    # Mention-routed: exactly the mentioned set; plain: full broadcast.
    assert [a.agent_name for a in a1] == ["bravo"]
    assert [a.agent_name for a in a2] == ["alpha", "bravo", "charlie"]
    assert a1[0].invocation_token is not None
    assert all(a.invocation_token is not None for a in a2)
    # Mentions persist on disk (the durable signal) in both cases.
    assert _mentions_json(db, "THR-A", 1) == '["bravo"]'
    assert _mentions_json(db, "THR-B", 1) == "[]"


def test_coalescing_semantics_preserved_for_mentioned_burst(tmp_path):
    """GH-688 invariant: a burst of mentioned arrivals to the same pair
    coalesces into exactly ONE queued wake covering the whole range, while
    each message row still persists its own mentions_json."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo"))

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

    assert a1[0].invocation_token is not None
    assert a1[0].coalesced is False
    assert a2[0].invocation_token is None and a2[0].coalesced is True
    assert a3[0].invocation_token is None and a3[0].coalesced is True

    states = db.list_reply_delivery_states()
    assert len(states) == 1
    assert states[0].required_through_seq == seq1 + 2
    assert states[0].queued_invocation_token == a1[0].invocation_token

    assert _mentions_json(db, "THR-001", seq1) == '["alpha"]'
    assert _mentions_json(db, "THR-001", seq1 + 1) == '["alpha"]'
    assert _mentions_json(db, "THR-001", seq1 + 2) == '["alpha"]'


def test_reply_mention_narrows_broadcast_fanout(tmp_path):
    """Slice B: a REPLY whose body mentions one participant wakes exactly
    that participant (mention-routed), not the full broadcast."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo", "charlie"))
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo", "charlie"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )
    # Clear bravo+charlie obligations so the reply's wakes mint fresh
    # (otherwise their compose-step queued tokens coalesce).
    db.discard_reply_delivery("THR-001", agent_name="bravo", decline_reason="test")
    db.discard_reply_delivery("THR-001", agent_name="charlie", decline_reason="test")
    _seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="replying to @bravo only", attachments=[],
        token=alpha_token, token_purpose=ThreadInvocationPurpose.REPLY,
    )
    # Mention-routed: exactly the mentioned OTHER participant is woken.
    assert [a.agent_name for a in broadcast] == ["bravo"]
    assert broadcast[0].invocation_token is not None
    assert settlement is not None


# ---------------------------------------------------------------------------
# compensation / rollback seams
# ---------------------------------------------------------------------------


def test_rollback_discards_message_and_mentions_together(
    tmp_path, monkeypatch,
):
    """A failure inside the arrival transaction rolls back BOTH the message
    row (including its mentions_json write) and any delivery-state change."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha",))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-arrival failure")

    monkeypatch.setattr(db, "_apply_arrival_uncommitted", _boom)
    with pytest.raises(RuntimeError):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="@alpha x",
            recipients=["alpha"],
        )
    monkeypatch.undo()

    n = db._conn.execute(
        "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id='THR-001'"
    ).fetchone()["n"]
    assert n == 0, "message row (with mentions_json) must be rolled back"
    states = db.list_reply_delivery_states()
    assert states == [], "delivery-state rows must be rolled back"


def test_reply_rollback_discards_mentions(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo"))
    _seq1, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo"],
    )
    alpha_token = next(
        a.invocation_token for a in arrivals if a.agent_name == "alpha"
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated reply failure")

    monkeypatch.setattr(db, "_settle_reply_uncommitted", _boom)
    with pytest.raises(RuntimeError):
        db.reply_conversational(
            thread_id="THR-001", speaker="alpha",
            body_markdown="reply @bravo", attachments=[],
            token=alpha_token, token_purpose=ThreadInvocationPurpose.REPLY,
        )
    monkeypatch.undo()

    n = db._conn.execute(
        "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id='THR-001'"
    ).fetchone()["n"]
    assert n == 1, "reply message row must be rolled back"


# ---------------------------------------------------------------------------
# TASK_FOLLOWUP / BOOTSTRAP isolation
# ---------------------------------------------------------------------------


def test_bootstrap_reply_isolation_and_mentions(tmp_path):
    """A BOOTSTRAP reply flows through reply_conversational's legacy consume
    path: its message row persists derived mentions (it is a conversational
    message), the token is consumed without delivery-state involvement, and
    the broadcast to other participants is unchanged."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha", "bravo"))
    seq1, _arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
        recipients=["alpha", "bravo"],
    )
    bootstrap = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha",
        triggering_seq=seq1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    seq2, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001", speaker="alpha",
        body_markdown="bootstrap reply @bravo", attachments=[],
        token=bootstrap.invocation_token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert settlement is None, "BOOTSTRAP never uses the REPLY settlement path"
    assert _mentions_json(db, "THR-001", seq2) == '["bravo"]'
    assert {a.agent_name for a in broadcast} == {"bravo"}
    consumed = [
        inv for inv in db.list_thread_invocations("THR-001")
        if inv.invocation_token == bootstrap.invocation_token
    ][0]
    assert consumed.status.value == "consumed"


def test_followup_mint_never_touches_message_storage(tmp_path):
    """TASK_FOLLOWUP invocations are minted outside the two conversational
    seams (mint_followup_invocation_with_cap_extend) — no thread_messages
    row and therefore no mentions_json is written by that path."""
    db = Database(tmp_path / "happyranch.db")
    _make_thread(db, "THR-001", ("alpha",))
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="kickoff",
    )
    inv, _cap = db.mint_followup_invocation_with_cap_extend(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
    )
    assert inv.purpose is ThreadInvocationPurpose.TASK_FOLLOWUP
    n = db._conn.execute(
        "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id='THR-001'"
    ).fetchone()["n"]
    assert n == 1, "followup mint appends no message row"
