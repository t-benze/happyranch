"""THR-209 thread rename + pin storage layer tests.

Covers: durable subject rename, durable pin state (additive ``pinned_at``),
idempotent additive migration on legacy DBs, pinned-first open-list ordering
by immutable numeric thread id DESC (msg-9 correction), zero pin
presentation in archived/status-less views, and the unchanged ordinary order
of unpinned threads.

Atomicity (TASK-5644): the rename/pin WITH-audit methods are ONE rollback-safe
transaction — authoritative read + conditional decision + uncommitted write +
uncommitted audit row + single commit. The uncommitted helpers never commit
independently; an audit-insert failure rolls back ALL state with no stray
audit row.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ThreadRecord, ThreadStatus


def _db(tmp_path):
    return Database(tmp_path / "happyranch.db")


def _insert(db: Database, thread_id: str, started_at: datetime) -> ThreadRecord:
    t = ThreadRecord(
        id=thread_id, subject=f"subject {thread_id}", started_at=started_at,
    )
    db.insert_thread(t)
    return t


def _append_message(db: Database, thread_id: str, created_at: datetime) -> None:
    db._conn.execute(
        "INSERT INTO thread_messages (thread_id, seq, speaker, kind, created_at) "
        "VALUES (?, ?, 'founder', 'message', ?)",
        (thread_id, db._thread_tail_seq(thread_id) + 1, created_at.isoformat()),
    )
    db._conn.commit()


def test_rename_updates_subject_durably(tmp_path) -> None:
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.set_thread_subject("THR-001", subject="Renamed title")
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.subject == "Renamed title"
    # Identity/status untouched by rename.
    assert got.id == "THR-001"
    assert got.status is ThreadStatus.OPEN


def test_rename_unknown_thread_is_noop(tmp_path) -> None:
    db = _db(tmp_path)
    # No exception; no row exists to mutate.
    db.set_thread_subject("THR-NOPE", subject="x")
    assert db.get_thread("THR-NOPE") is None


def test_pin_state_roundtrip(tmp_path) -> None:
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert db.get_thread("THR-001").pinned_at is None
    db.set_thread_pinned("THR-001", pinned=True)
    assert db.get_thread("THR-001").pinned_at is not None
    db.set_thread_pinned("THR-001", pinned=False)
    assert db.get_thread("THR-001").pinned_at is None


def test_pin_does_not_change_activity_fields(tmp_path) -> None:
    """Pinning must not touch started_at / archived_at / subject."""
    db = _db(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert(db, "THR-001", started)
    db.set_thread_pinned("THR-001", pinned=True)
    got = db.get_thread("THR-001")
    assert got.started_at == started
    assert got.subject == "subject THR-001"


def test_legacy_threads_table_gets_additive_pinned_column(tmp_path) -> None:
    """A DB created before ``pinned_at`` existed must migrate additively and
    read existing rows (NULL pin state)."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            started_at TEXT NOT NULL,
            archived_at TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            forwarded_from_id TEXT,
            forwarded_from_kind TEXT,
            turn_cap INTEGER NOT NULL DEFAULT 500,
            turns_used INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            transcript_path TEXT,
            composed_by TEXT NOT NULL DEFAULT 'founder',
            composed_from_task_id TEXT,
            composed_from_dream_id TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-OLD', 'legacy', '2026-01-01T00:00:00+00:00', 'open')"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    got = db.get_thread("THR-OLD")
    assert got is not None
    assert got.subject == "legacy"
    assert got.pinned_at is None
    # New column usable after migration.
    db.set_thread_pinned("THR-OLD", pinned=True)
    assert db.get_thread("THR-OLD").pinned_at is not None


def test_list_threads_pins_first_then_unpinned_unchanged(tmp_path) -> None:
    """Pinned threads rank above unpinned; within pinned, immutable NUMERIC
    thread ID descending (THR-010 above THR-002 above THR-001 — never
    lexicographic, never activity); unpinned keep the existing started_at
    DESC order (THR-209 message-9 correction)."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-002", datetime(2026, 1, 5, tzinfo=timezone.utc))
    _insert(db, "THR-010", datetime(2026, 1, 3, tzinfo=timezone.utc))
    _insert(db, "THR-100", datetime(2026, 1, 2, tzinfo=timezone.utc))
    # Activity must NOT influence pinned rank: THR-001 has the most recent
    # message, THR-002 older activity, THR-010/THR-100 none.
    _append_message(db, "THR-001", datetime(2026, 1, 10, tzinfo=timezone.utc))
    _append_message(db, "THR-002", datetime(2026, 1, 8, tzinfo=timezone.utc))

    db.set_thread_pinned("THR-002", pinned=True)
    db.set_thread_pinned("THR-010", pinned=True)
    db.set_thread_pinned("THR-001", pinned=True)

    rows = db.list_threads(status="open", limit=10)
    ids = [r.id for r in rows]
    # Pinned section first, numeric descending: THR-010(10) > THR-002(2) >
    # THR-001(1); then the unpinned row THR-100 in ordinary started_at DESC.
    # Activity (THR-001 has the newest message) does NOT affect pinned rank.
    assert ids == ["THR-010", "THR-002", "THR-001", "THR-100"]
    assert [r.pinned_at is not None for r in rows] == [True, True, True, False]


def test_list_threads_pinned_numeric_not_lexicographic(tmp_path) -> None:
    """Multi-digit IDs prove NUMERIC (THR-10 above THR-2) rather than
    lexicographic (THR-2 above THR-10) pinned comparison — THR-209 msg 9."""
    db = _db(tmp_path)
    _insert(db, "THR-2", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-10", datetime(2026, 1, 2, tzinfo=timezone.utc))
    _insert(db, "THR-3", datetime(2026, 1, 3, tzinfo=timezone.utc))
    for tid in ("THR-2", "THR-10", "THR-3"):
        db.set_thread_pinned(tid, pinned=True)
    rows = db.list_threads(status="open", limit=10)
    ids = [r.id for r in rows]
    # Numeric desc: 10 > 3 > 2. Lexicographic desc would be 3 > 2 > 10.
    assert ids == ["THR-10", "THR-3", "THR-2"]


def test_list_threads_pinned_order_ignores_activity(tmp_path) -> None:
    """A pinned thread with OLDER activity but a HIGHER id ranks above a
    pinned thread with NEWER activity — pinned rank is ID-desc only."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-002", datetime(2026, 1, 1, tzinfo=timezone.utc))
    # THR-001 has the most recent activity by far.
    _append_message(db, "THR-001", datetime(2026, 1, 20, tzinfo=timezone.utc))
    _append_message(db, "THR-002", datetime(2026, 1, 2, tzinfo=timezone.utc))
    db.set_thread_pinned("THR-001", pinned=True)
    db.set_thread_pinned("THR-002", pinned=True)
    assert [r.id for r in db.list_threads(status="open", limit=10)] == ["THR-002", "THR-001"]


def test_list_archived_ignores_pin_state(tmp_path) -> None:
    """Archived views must have ZERO pin presentation: pinned and unpinned
    archived threads interleave in the ordinary archived_at DESC order with
    no pinned-first rank (THR-209 message-9 correction)."""
    import time

    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-002", datetime(2026, 1, 2, tzinfo=timezone.utc))
    _insert(db, "THR-003", datetime(2026, 1, 3, tzinfo=timezone.utc))
    # Archive in a different order: 003 first, then 001, then 002.
    db.set_thread_status("THR-003", status=ThreadStatus.ARCHIVED, summary="a")
    time.sleep(0.01)
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVED, summary="b")
    time.sleep(0.01)
    db.set_thread_status("THR-002", status=ThreadStatus.ARCHIVED, summary="c")
    # Pin the MOST-RECENTLY-ARCHIVED (THR-002) and the LEAST (THR-003);
    # THR-001 stays unpinned. Pin must not move any row.
    db.set_thread_pinned("THR-002", pinned=True)
    db.set_thread_pinned("THR-003", pinned=True)
    rows = db.list_threads(status="archived", limit=10)
    # Ordinary archived order: most-recently-archived first.
    assert [r.id for r in rows] == ["THR-002", "THR-001", "THR-003"]
    assert [r.pinned_at is not None for r in rows] == [True, False, True]


def test_list_threads_statusless_ignores_pin_state(tmp_path) -> None:
    """The status-less query is NOT a pin-qualifying view: mixed open+archived
    rows follow the ordinary started_at DESC order with no pinned-first rank
    (restores the pre-THR-209 mixed-query order)."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-002", datetime(2026, 1, 5, tzinfo=timezone.utc))
    _insert(db, "THR-003", datetime(2026, 1, 3, tzinfo=timezone.utc))
    db.set_thread_pinned("THR-001", pinned=True)
    rows = db.list_threads(limit=10)
    assert [r.id for r in rows] == ["THR-002", "THR-003", "THR-001"]


def test_list_threads_open_unpinned_unchanged_when_others_pinned(tmp_path) -> None:
    """Pinning some threads must not disturb the ordinary started_at DESC
    order of the unpinned open rows."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-002", datetime(2026, 1, 4, tzinfo=timezone.utc))
    _insert(db, "THR-003", datetime(2026, 1, 5, tzinfo=timezone.utc))
    db.set_thread_pinned("THR-001", pinned=True)
    rows = db.list_threads(status="open", limit=10)
    assert [r.id for r in rows] == ["THR-001", "THR-003", "THR-002"]
    assert [r.pinned_at is not None for r in rows] == [True, False, False]


def test_list_threads_empty(tmp_path) -> None:
    db = _db(tmp_path)
    assert db.list_threads(limit=10) == []
    assert db.list_threads(status="open", limit=10) == []
    assert db.list_threads(status="archived", limit=10) == []


def test_list_threads_unpinned_order_unchanged_without_pins(tmp_path) -> None:
    """With no pins at all the order must be byte-for-byte the pre-THR-209
    behavior (started_at DESC; archived → archived_at DESC)."""
    db = _db(tmp_path)
    _insert(db, "THR-A", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-B", datetime(2026, 1, 5, tzinfo=timezone.utc))
    rows = db.list_threads(limit=10)
    assert [r.id for r in rows] == ["THR-B", "THR-A"]

    # Archived bucket keeps archived_at DESC ordering (pinned CASE ties → NULL).
    import time

    db.set_thread_status("THR-A", status=ThreadStatus.ARCHIVED, summary="a")
    time.sleep(0.01)
    db.set_thread_status("THR-B", status=ThreadStatus.ARCHIVED, summary="b")
    rows = db.list_threads(status="archived", limit=10)
    assert [r.id for r in rows] == ["THR-B", "THR-A"]


def test_list_threads_status_filter_respects_pinned_rank(tmp_path) -> None:
    db = _db(tmp_path)
    _insert(db, "THR-A", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(db, "THR-B", datetime(2026, 1, 5, tzinfo=timezone.utc))
    db.set_thread_status("THR-A", status=ThreadStatus.ARCHIVED, summary="a")
    db.set_thread_pinned("THR-B", pinned=True)
    rows = db.list_threads(status="open", limit=10)
    assert [r.id for r in rows] == ["THR-B"]
    rows = db.list_threads(status="archived", limit=10)
    assert [r.id for r in rows] == ["THR-A"]


# ---------------------------------------------------------------------------
# Atomic rename/pin with audit (TASK-5644) — one rollback-safe transaction
# ---------------------------------------------------------------------------


def _committed_subject(db_path, thread_id: str) -> str | None:
    """Read the COMMITTED subject through a separate connection (WAL-safe)."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT subject FROM threads WHERE id = ?", (thread_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _committed_pinned(db_path, thread_id: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT pinned_at FROM threads WHERE id = ?", (thread_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_rename_with_audit_transition_noop_and_row_shape(tmp_path) -> None:
    """The atomic rename writes subject + ``thread_renamed`` row in ONE
    transaction; an identical save is a no-op (no write, no audit)."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))

    transitioned = db.rename_thread_with_audit("THR-001", subject="New title")
    assert transitioned is True
    assert db.get_thread("THR-001").subject == "New title"
    rows = db.get_audit_logs("THR-001")
    assert [r["action"] for r in rows] == ["thread_renamed"]
    # Preserved audit shape: THR-* task_id scope, founder actor, old/new.
    assert rows[0]["task_id"] == "THR-001"
    assert rows[0]["agent"] == "founder"
    assert rows[0]["payload"] == {
        "old_subject": "subject THR-001", "new_subject": "New title",
    }

    # Idempotent no-op: no write, no duplicate audit row.
    assert db.rename_thread_with_audit("THR-001", subject="New title") is False
    assert db.get_thread("THR-001").subject == "New title"
    assert len(db.get_audit_logs("THR-001")) == 1

    # The next real transition chains truthfully from the durable value.
    assert db.rename_thread_with_audit("THR-001", subject="Third") is True
    rows = db.get_audit_logs("THR-001")
    assert rows[-1]["payload"] == {
        "old_subject": "New title", "new_subject": "Third",
    }


def test_set_thread_pinned_with_audit_transition_noop_and_row_shape(tmp_path) -> None:
    """Pin/unpin write ``pinned_at`` + the matching audit row atomically;
    same-state saves are true no-ops (unaudited)."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert db.set_thread_pinned_with_audit("THR-001", pinned=True) is True
    assert db.get_thread("THR-001").pinned_at is not None
    rows = db.get_audit_logs("THR-001")
    assert [r["action"] for r in rows] == ["thread_pinned"]
    assert rows[0]["task_id"] == "THR-001"
    assert rows[0]["agent"] == "founder"
    assert rows[0]["payload"] == {"pinned": True}

    # Same-state no-op: no write, no audit.
    assert db.set_thread_pinned_with_audit("THR-001", pinned=True) is False
    assert len(db.get_audit_logs("THR-001")) == 1

    assert db.set_thread_pinned_with_audit("THR-001", pinned=False) is True
    assert db.get_thread("THR-001").pinned_at is None
    assert [r["action"] for r in db.get_audit_logs("THR-001")] == [
        "thread_pinned", "thread_unpinned",
    ]
    assert db.set_thread_pinned_with_audit("THR-001", pinned=False) is False
    assert len(db.get_audit_logs("THR-001")) == 2


def test_uncommitted_helpers_do_not_commit_independently(tmp_path) -> None:
    """The uncommitted write helpers must NOT commit: nothing is visible to a
    separate reader until the owning transaction commits, and a rollback
    discards the writes entirely (TASK-5644 requirement)."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))

    db._conn.execute("BEGIN IMMEDIATE")
    db.set_thread_subject_uncommitted("THR-001", subject="Draft")
    db.set_thread_pinned_uncommitted("THR-001", pinned=True)
    # Uncommitted: a separate reader sees the OLD committed state.
    assert _committed_subject(db.db_path, "THR-001") == "subject THR-001"
    assert _committed_pinned(db.db_path, "THR-001") is None
    db._conn.rollback()
    # Rollback discarded both writes.
    assert db.get_thread("THR-001").subject == "subject THR-001"
    assert db.get_thread("THR-001").pinned_at is None

    # The owning transaction's commit makes both durable together.
    db._conn.execute("BEGIN IMMEDIATE")
    db.set_thread_subject_uncommitted("THR-001", subject="Final")
    db.set_thread_pinned_uncommitted("THR-001", pinned=True)
    db._conn.commit()
    assert _committed_subject(db.db_path, "THR-001") == "Final"
    assert _committed_pinned(db.db_path, "THR-001") is not None


def test_rename_with_audit_rolls_back_on_audit_failure(tmp_path, monkeypatch) -> None:
    """Audit-insert failure inside the atomic rename rolls back the subject
    write AND the (partial) audit state: no durable mutation, no stray row,
    and the connection remains usable for the next save."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))

    def _boom(*args, **kwargs):
        raise RuntimeError("audit boom")

    with monkeypatch.context() as ctx:
        ctx.setattr(db, "insert_audit_log_uncommitted", _boom)
        with pytest.raises(RuntimeError, match="audit boom"):
            db.rename_thread_with_audit("THR-001", subject="Never saved")
        assert db.get_thread("THR-001").subject == "subject THR-001"
        assert db.get_audit_logs("THR-001") == []

    # The same connection/transaction machinery works for the next save.
    assert db.rename_thread_with_audit("THR-001", subject="Saved now") is True
    assert db.get_thread("THR-001").subject == "Saved now"


def test_set_thread_pinned_with_audit_rolls_back_on_audit_failure(
    tmp_path, monkeypatch,
) -> None:
    """Audit-insert failure inside the atomic pin rolls back the pinned_at
    write: no durable pin, no stray audit row."""
    db = _db(tmp_path)
    _insert(db, "THR-001", datetime(2026, 1, 1, tzinfo=timezone.utc))

    def _boom(*args, **kwargs):
        raise RuntimeError("audit boom")

    with monkeypatch.context() as ctx:
        ctx.setattr(db, "insert_audit_log_uncommitted", _boom)
        with pytest.raises(RuntimeError, match="audit boom"):
            db.set_thread_pinned_with_audit("THR-001", pinned=True)
        assert db.get_thread("THR-001").pinned_at is None
        assert db.get_audit_logs("THR-001") == []

    assert db.set_thread_pinned_with_audit("THR-001", pinned=True) is True
    assert db.get_thread("THR-001").pinned_at is not None
