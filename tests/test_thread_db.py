from __future__ import annotations

import pytest
from datetime import datetime, timezone

from runtime.infrastructure.database import Database
from runtime.models import TaskRecord


def test_dispatched_from_thread_id_round_trips(tmp_path):
    """After Task 4 wires TaskRecord + insert_task, a thread-dispatched task
    should round-trip its dispatched_from_thread_id through SQLite. Today
    this fails: Pydantic drops the unknown field and/or insert_task ignores
    the column.
    """
    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-001", brief="x", dispatched_from_thread_id="THR-007",
    ))
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_thread_id == "THR-007"


from runtime.models import (
    ThreadAttachment, ThreadInvocation, ThreadInvocationPurpose, ThreadInvocationStatus,
    ThreadMessage, ThreadMessageKind, ThreadParticipant, ThreadRecord,
    ThreadStatus,
)


def test_thread_models_roundtrip():
    t = ThreadRecord(id="THR-001", subject="Refund policy")
    assert t.status is ThreadStatus.OPEN
    assert t.turn_cap == 500
    p = ThreadParticipant(thread_id="THR-001", agent_name="dev")
    assert p.added_by == "founder"
    m = ThreadMessage(
        thread_id="THR-001", seq=1, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    assert m.kind is ThreadMessageKind.MESSAGE
    inv = ThreadInvocation(
        thread_id="THR-001", agent_name="dev",
        invocation_token="abc", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING


def test_next_thread_id_starts_at_one(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    assert db.next_thread_id() == "THR-001"


def test_next_thread_id_uses_max_suffix(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-001', 's', '2026-01-01T00:00:00+00:00', 'archived')"
    )
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-005', 's', '2026-01-02T00:00:00+00:00', 'open')"
    )
    db._conn.commit()
    assert db.next_thread_id() == "THR-006"


def test_insert_and_get_thread(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    t = ThreadRecord(id="THR-001", subject="Refund policy")
    db.insert_thread(t)
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.id == "THR-001"
    assert got.subject == "Refund policy"
    assert got.status is ThreadStatus.OPEN
    assert got.turn_cap == 500


def test_get_thread_missing_returns_none(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    assert db.get_thread("THR-404") is None


def test_list_threads_orders_by_started_desc(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    a = ThreadRecord(id="THR-001", subject="a", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = ThreadRecord(id="THR-002", subject="b", started_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    db.insert_thread(a)
    db.insert_thread(b)
    rows = db.list_threads(limit=10)
    assert [r.id for r in rows] == ["THR-002", "THR-001"]


def test_list_archived_threads_orders_by_archived_at_desc(tmp_path):
    """Archived threads must be ordered by archived_at DESC (most-recently-archived first),
    not by started_at (creation order). The founder wants newest-archived-first."""
    db = Database(tmp_path / "happyranch.db")
    # Create threads in one order (THR-A first, THR-C last).
    a = ThreadRecord(id="THR-A", subject="alpha", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = ThreadRecord(id="THR-B", subject="beta", started_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    c = ThreadRecord(id="THR-C", subject="gamma", started_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
    db.insert_thread(a)
    db.insert_thread(b)
    db.insert_thread(c)
    # Archive in a DIFFERENT order: C first, then A, then B.
    import time
    db.set_thread_status("THR-C", status=ThreadStatus.ARCHIVED, summary="c done")
    time.sleep(0.01)
    db.set_thread_status("THR-A", status=ThreadStatus.ARCHIVED, summary="a done")
    time.sleep(0.01)
    db.set_thread_status("THR-B", status=ThreadStatus.ARCHIVED, summary="b done")
    # Most-recently-archived first: B, A, C.
    rows = db.list_threads(status="archived", limit=10)
    assert [r.id for r in rows] == ["THR-B", "THR-A", "THR-C"]


def test_add_and_list_participants(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    names = [p.agent_name for p in db.list_thread_participants("THR-001")]
    assert sorted(names) == ["alice", "bob"]


def test_add_thread_participant_idempotent(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.add_thread_participant("THR-001", "alice", added_by="founder") is False


def test_is_thread_participant(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.is_thread_participant("THR-001", "alice")
    assert not db.is_thread_participant("THR-001", "bob")


def test_append_thread_message_allocates_monotonic_seq(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    seq_a = db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hello",
    )
    seq_b = db.append_thread_message(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hi back",
    )
    assert seq_a == 1
    assert seq_b == 2
    msgs = db.list_thread_messages("THR-001")
    assert [m.seq for m in msgs] == [1, 2]


def test_append_thread_decline_message(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.append_thread_message(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.DECLINE,
        decline_reason="bob covered it",
    )
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].kind is ThreadMessageKind.DECLINE
    assert msgs[0].decline_reason == "bob covered it"


def test_append_thread_system_message(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "participant_added", "agent_name": "alice"},
    )
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].system_payload["kind_tag"] == "participant_added"


def test_thread_message_attachments_roundtrip(db) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="Files"))
    seq = db.append_thread_message(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=None,
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=123,
                content_type="application/pdf",
                uploaded_by="founder",
            ),
            ThreadAttachment(
                artifact_name="THR-001-data.csv",
                display_name="data.csv",
                size_bytes=42,
                content_type="text/csv",
                uploaded_by="founder",
            ),
        ],
    )

    messages = db.list_thread_messages("THR-001")

    assert seq == 1
    assert len(messages) == 1
    assert messages[0].attachments == [
        ThreadAttachment(
            artifact_name="THR-001-report.pdf",
            display_name="report.pdf",
            size_bytes=123,
            content_type="application/pdf",
            uploaded_by="founder",
        ),
        ThreadAttachment(
            artifact_name="THR-001-data.csv",
            display_name="data.csv",
            size_bytes=42,
            content_type="text/csv",
            uploaded_by="founder",
        ),
    ]


def test_thread_message_attachments_default_empty(db) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="No files"))
    db.append_thread_message(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hello",
    )

    assert db.list_thread_messages("THR-001")[0].attachments == []


def test_thread_message_attachment_failure_rolls_back_parent_message(db) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="Bad file"))

    with pytest.raises(Exception):
        db.append_thread_message(
            thread_id="THR-001",
            speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="bad attachment",
            attachments=[
                ThreadAttachment.model_construct(
                    artifact_name="THR-001-bad.txt",
                    display_name=None,
                    uploaded_by="founder",
                ),
            ],
        )

    assert db.list_thread_messages("THR-001") == []

    seq = db.append_thread_message(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="good message",
    )

    messages = db.list_thread_messages("THR-001")
    assert seq == 1
    assert len(messages) == 1
    assert messages[0].body_markdown == "good message"


def test_mint_thread_invocation(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING
    assert len(inv.invocation_token) >= 16
    assert inv.purpose is ThreadInvocationPurpose.REPLY


def test_get_pending_invocation_by_token(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    found = db.get_pending_invocation(inv.invocation_token)
    assert found is not None
    assert found.agent_name == "alice"
    assert db.get_pending_invocation("nonsense") is None


def test_consume_invocation_marks_consumed(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.consume_invocation(inv.invocation_token) is True
    assert db.consume_invocation(inv.invocation_token) is False
    assert db.get_pending_invocation(inv.invocation_token) is None


def test_record_dispatch_on_invocation(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-009") is True
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-010") is False


def test_reap_pending_invocations(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="a",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="b",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="c",
        triggering_seq=2, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    reaped = db.reap_pending_invocations(
        "THR-001",
        purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
        decline_reason="archive_started",
    )
    assert reaped == 2
    pending = db.list_thread_invocations("THR-001", status=ThreadInvocationStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].agent_name == "c"


def test_increment_turns_used(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.increment_thread_turns_used("THR-001", by=2)
    db.increment_thread_turns_used("THR-001", by=1)
    t = db.get_thread("THR-001")
    assert t.turns_used == 3


def test_set_thread_status_archived(tmp_path):
    """ARCHIVED sets status + summary + archived_at in one call."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status(
        "THR-001",
        status=ThreadStatus.ARCHIVED,
        summary="done talking",
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.summary == "done talking"
    assert t.archived_at is not None


def test_set_thread_transcript_path(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVED, summary="s")
    db.set_thread_transcript_path("THR-001", "/tmp/THR-001.md")
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.archived_at is not None
    assert t.transcript_path == "/tmp/THR-001.md"


def test_set_thread_turn_cap(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_turn_cap("THR-001", new_cap=1000)
    assert db.get_thread("THR-001").turn_cap == 1000


def test_set_thread_status_to_open_resumes_archived_thread(tmp_path):
    """OPEN status on an archived thread leaves archived_at + summary intact."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-100", subject="x"))
    # ARCHIVED sets summary + archived_at in one call (post-Task-13).
    db.set_thread_status("THR-100", status=ThreadStatus.ARCHIVED, summary="done")
    pre = db.get_thread("THR-100")
    assert pre.status is ThreadStatus.ARCHIVED
    assert pre.summary == "done"
    assert pre.archived_at is not None
    pre_archived_at = pre.archived_at

    db.set_thread_status("THR-100", status=ThreadStatus.OPEN)

    post = db.get_thread("THR-100")
    assert post.status is ThreadStatus.OPEN
    # archived_at + summary left intact as historical record
    assert post.archived_at == pre_archived_at
    assert post.summary == "done"


def test_log_thread_resumed_writes_audit_row(tmp_path):
    """Audit writer records the resume event with prior archived timestamp."""
    from runtime.infrastructure.audit_logger import AuditLogger
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-100", subject="x"))
    AuditLogger(db).log_thread_resumed(
        "THR-100", prior_archived_at="2026-05-30T12:00:00+00:00",
    )
    rows = db.get_audit_logs("THR-100")
    assert any(r["action"] == "thread_resumed" for r in rows)
    resumed = next(r for r in rows if r["action"] == "thread_resumed")
    assert resumed["payload"].get("prior_archived_at") == "2026-05-30T12:00:00+00:00"
    assert resumed["agent"] == "founder"


def test_thread_session_defaults_and_roundtrip(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")

    # Default state: no stored session, watermark 0.
    assert db.get_thread_session("THR-001", "alice") == (None, 0)

    # Unknown participant also returns the safe default (no row).
    assert db.get_thread_session("THR-001", "ghost") == (None, 0)

    db.update_thread_session(
        "THR-001", "alice", agent_session_id="sess-123", last_resumed_seq=7
    )
    assert db.get_thread_session("THR-001", "alice") == ("sess-123", 7)

    # Eviction clears the id but the accessor still returns a safe tuple.
    db.update_thread_session(
        "THR-001", "alice", agent_session_id=None, last_resumed_seq=0
    )
    assert db.get_thread_session("THR-001", "alice") == (None, 0)


def test_remove_thread_participant_succeeds(tmp_path):
    """Remove a participant returns True and the row is hard-deleted."""
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.is_thread_participant("THR-001", "alice") is True

    result = db.remove_thread_participant("THR-001", "alice")
    assert result is True
    assert db.is_thread_participant("THR-001", "alice") is False


def test_remove_thread_participant_non_participant_returns_false(tmp_path):
    """Removing a non-participant returns False (idempotent-safe)."""
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    result = db.remove_thread_participant("THR-001", "alice")
    assert result is False


def test_remove_thread_participant_only_removes_target(tmp_path):
    """Only the specified participant is removed; others remain."""
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")

    db.remove_thread_participant("THR-001", "alice")
    assert db.is_thread_participant("THR-001", "alice") is False
    assert db.is_thread_participant("THR-001", "bob") is True


def test_decline_pending_invocations_for_agent(tmp_path):
    """Bulk-decline all pending invocations for (thread, agent)."""
    from runtime.infrastructure.database import Database
    from runtime.models import (ThreadRecord, ThreadInvocationPurpose,
                                ThreadInvocationStatus)

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    count = db.decline_pending_invocations_for_agent("THR-001", "alice")
    assert count == 1

    # alice's invocation is now declined
    alice_invocations = db.list_thread_invocations("THR-001")
    alice_inv = next(inv for inv in alice_invocations if inv.agent_name == "alice")
    assert alice_inv.status is ThreadInvocationStatus.DECLINED

    # bob's invocation is still pending
    bob_inv = next(inv for inv in alice_invocations if inv.agent_name == "bob")
    assert bob_inv.status is ThreadInvocationStatus.PENDING


def test_decline_pending_invocations_no_pending_returns_zero(tmp_path):
    """No pending invocations → returns 0."""
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    count = db.decline_pending_invocations_for_agent("THR-001", "alice")
    assert count == 0


def test_grouped_invocations_include_started_at(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.models import ThreadRecord, ThreadInvocationPurpose, ThreadMessageKind

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    grouped = db.list_invocations_for_thread_grouped_by_seq("THR-001")
    entry = grouped[1][0]
    assert entry["agent_name"] == "alice"
    assert entry["status"] == "pending"
    assert entry["started_at"] is None        # not started yet

    db.stamp_invocation_started(inv.invocation_token, session_id=None)
    grouped2 = db.list_invocations_for_thread_grouped_by_seq("THR-001")
    assert grouped2[1][0]["started_at"] is not None


# ── GitHub #688 Phase 1 Slice A: thread_reply_delivery_state ─────────────
# Additive per-(thread_id, agent_name) conversational REPLY delivery state,
# plus the store-owned cutover and recovery primitives. These are UNHOOKED in
# Slice A: no existing writer/runner/startup path reads or writes the table.

from runtime.models import (
    ThreadReplyDeliveryState,
    ThreadReplyRecoveryEntry,
)


def _seed_pending_reply(db, thread_id, agent_name, triggering_seq):
    """Mint one legacy pending REPLY invocation (pre-Phase-1 writer path)."""
    return db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=triggering_seq, purpose=ThreadInvocationPurpose.REPLY,
    )


def _seed_running_state(db, thread_id, agent_name, *, ack, req):
    """Simulate the durable stage Slice B's claim leaves behind: one started
    REPLY invocation referenced as ``running_invocation_token`` with an
    immutable running range, queued slot clear."""
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=ack + 1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(inv.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "running_invocation_token, running_from_seq, running_through_seq, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, agent_name, ack, req, inv.invocation_token, ack + 1, req,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()
    return inv.invocation_token


def _pending_reply_rows(db, thread_id, agent_name):
    return [
        inv for inv in db.list_thread_invocations(thread_id)
        if inv.agent_name == agent_name
        and inv.purpose is ThreadInvocationPurpose.REPLY
        and inv.status is ThreadInvocationStatus.PENDING
    ]


def test_reply_delivery_state_table_idempotent_create(tmp_path):
    """Fresh and re-opened databases create the additive table idempotently."""
    from runtime.infrastructure.database import Database
    path = tmp_path / "happyranch.db"
    db = Database(path)
    names = {
        row[0] for row in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='thread_reply_delivery_state'"
        )
    }
    assert "thread_reply_delivery_state" in names
    # Re-open over the same file — CREATE TABLE IF NOT EXISTS must not raise
    # and must not duplicate the table.
    db.close()
    db2 = Database(path)
    assert db2.get_reply_delivery_state("THR-000", "ghost") is None
    assert db2.list_reply_delivery_states() == []


def test_reply_delivery_state_dark_under_existing_paths(tmp_path):
    """The LEGACY direct-mint path (append_thread_message + mint_thread_invocation)
    does not populate the delivery-state table: Slice B's store-owned writers
    (record_conversational_arrival / reply_conversational) are the only producers,
    so a legacy/control path can never accidentally create pair state."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)

    assert db.list_reply_delivery_states() == []
    assert db.get_reply_delivery_state("THR-001", "alice") is None


def test_cutover_no_legacy_pending_seeds_tail(tmp_path):
    """A current participant pair with no legacy pending REPLY seeds
    acknowledged == required == thread tail, with no queued/running token."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for i in range(3):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )

    created = db.cutover_thread_reply_delivery_state("THR-001")
    assert len(created) == 1
    st = created[0]
    assert st.thread_id == "THR-001"
    assert st.agent_name == "alice"
    assert st.acknowledged_through_seq == 3
    assert st.required_through_seq == 3
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None


def test_cutover_legacy_pending_coalesces_to_one_queued(tmp_path):
    """Multiple legacy pending REPLYs coalesce: terminalize with a
    coalesced_cutover receipt and mint exactly one replacement queued REPLY
    covering min(triggering_seq) .. tail."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for i in range(5):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )  # tail == 5
    # Three legacy pending REPLYs at seqs 2, 3, 5 (min == 2).
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=2)
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=3)
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=5)

    created = db.cutover_thread_reply_delivery_state("THR-001")
    assert len(created) == 1
    st = created[0]
    assert isinstance(st, ThreadReplyDeliveryState)
    assert st.acknowledged_through_seq == 1  # from_seq - 1
    assert st.required_through_seq == 5      # tail
    assert st.queued_invocation_token is not None
    assert st.running_invocation_token is None

    # Exactly one replacement pending REPLY (the coalesced wake).
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    assert pending[0].triggering_seq == 2   # min(triggering_seq)
    assert pending[0].invocation_token == st.queued_invocation_token

    # Exactly the three legacy rows were terminalized with the receipt.
    terminalized = [
        inv for inv in db.list_thread_invocations("THR-001")
        if inv.agent_name == "alice"
        and inv.purpose is ThreadInvocationPurpose.REPLY
        and inv.status is ThreadInvocationStatus.FAILED
        and inv.decline_reason == "coalesced_cutover"
    ]
    assert len(terminalized) == 3
    assert sorted(inv.triggering_seq for inv in terminalized) == [2, 3, 5]


def test_cutover_is_idempotent_across_repeat_and_reopen(tmp_path):
    """Repeat cutover (and re-opening the thread) must not duplicate state or
    mint a second coalesced wake."""
    from runtime.infrastructure.database import Database
    path = tmp_path / "happyranch.db"
    db = Database(path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)

    first = db.cutover_thread_reply_delivery_state("THR-001")
    assert len(first) == 1
    token = first[0].queued_invocation_token
    assert token is not None

    # Repeat in-process: no new state row, no new wake.
    second = db.cutover_thread_reply_delivery_state("THR-001")
    assert second == []

    # Re-open the DB (simulated restart) and cut over again: still idempotent.
    db.close()
    db2 = Database(path)
    third = db2.cutover_thread_reply_delivery_state("THR-001")
    assert third == []
    assert db2.get_reply_delivery_state("THR-001", "alice").queued_invocation_token == token
    # Still exactly one pending REPLY for the pair.
    assert len(_pending_reply_rows(db2, "THR-001", "alice")) == 1


def test_cutover_ignores_last_resumed_seq(tmp_path):
    """The cutover never reads last_resumed_seq; a bogus high watermark must
    not affect acknowledged/required seeding."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.update_thread_session(
        "THR-001", "alice", agent_session_id="sess-1", last_resumed_seq=9999,
    )
    for i in range(2):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )

    created = db.cutover_thread_reply_delivery_state("THR-001")
    st = created[0]
    # Seeded from the thread tail (2), not last_resumed_seq (9999).
    assert st.acknowledged_through_seq == 2
    assert st.required_through_seq == 2


def test_cutover_isolates_task_followup_and_bootstrap(tmp_path):
    """TASK_FOLLOWUP and BOOTSTRAP pending rows are never terminalized or
    replaced by the cutover."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    db.cutover_thread_reply_delivery_state("THR-001")

    statuses = {
        (inv.purpose, inv.status)
        for inv in db.list_thread_invocations("THR-001")
        if inv.agent_name == "alice"
    }
    # TASK_FOLLOWUP and BOOTSTRAP remain pending.
    assert (ThreadInvocationPurpose.TASK_FOLLOWUP, ThreadInvocationStatus.PENDING) in statuses
    assert (ThreadInvocationPurpose.BOOTSTRAP, ThreadInvocationStatus.PENDING) in statuses
    # No TASK_FOLLOWUP/BOOTSTRAP row was failed with coalesced_cutover.
    coalesced_non_reply = [
        inv for inv in db.list_thread_invocations("THR-001")
        if inv.agent_name == "alice"
        and inv.purpose is not ThreadInvocationPurpose.REPLY
        and inv.decline_reason == "coalesced_cutover"
    ]
    assert coalesced_non_reply == []


def test_repeated_cutover_never_duplicates_pending_pair(tmp_path):
    """The durable winner under repeated cutover is exactly one queued REPLY
    per pair (no duplicate pending pair)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)

    for _ in range(3):
        db.cutover_thread_reply_delivery_state("THR-001")

    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    states = db.list_reply_delivery_states()
    assert len(states) == 1
    assert states[0].queued_invocation_token == pending[0].invocation_token
    assert states[0].running_invocation_token is None


def test_recovery_retains_valid_queued(tmp_path):
    """A valid queued state retains and returns its existing pending token."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for i in range(3):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=2)

    created = db.cutover_thread_reply_delivery_state("THR-001")
    queued = created[0].queued_invocation_token

    entries = db.recover_reply_delivery_state()
    assert len(entries) == 1
    assert isinstance(entries[0], ThreadReplyRecoveryEntry)
    assert entries[0].kind == "retained_queued"
    assert entries[0].invocation_token == queued

    # State unchanged: the token is still queued and still pending.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token == queued
    assert st.running_invocation_token is None
    assert db.get_pending_invocation(queued) is not None


def test_recovery_running_terminalizes_once_with_replacement(tmp_path):
    """A valid running state terminalizes only the interrupted attempt as
    daemon_restart and mints exactly one replacement queued REPLY."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for i in range(4):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )
    running_token = _seed_running_state(db, "THR-001", "alice", ack=1, req=4)

    entries = db.recover_reply_delivery_state()
    assert len(entries) == 1
    assert entries[0].kind == "replacement_queued"
    replacement = entries[0].invocation_token
    assert replacement != running_token

    # The interrupted attempt is terminal with daemon_restart.
    running_inv = db.get_invocation_any_status(running_token)
    assert running_inv.status is ThreadInvocationStatus.FAILED
    assert running_inv.decline_reason == "daemon_restart"

    # Exactly one replacement pending REPLY, covering acknowledged+1.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.running_from_seq is None
    assert st.running_through_seq is None
    assert st.queued_invocation_token == replacement
    # Range preserved (acknowledged not advanced by recovery).
    assert st.acknowledged_through_seq == 1
    assert st.required_through_seq == 4
    rep_inv = db.get_pending_invocation(replacement)
    assert rep_inv is not None
    assert rep_inv.triggering_seq == 2  # acknowledged + 1
    assert rep_inv.purpose is ThreadInvocationPurpose.REPLY


def test_recovery_running_is_idempotent(tmp_path):
    """Repeat recovery after a running replacement retains (not re-mints) the
    single queued wake."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    _seed_running_state(db, "THR-001", "alice", ack=0, req=1)

    first = db.recover_reply_delivery_state()
    assert len(first) == 1
    replacement = first[0].invocation_token

    # Second pass sees a queued state → retains, does not mint again.
    second = db.recover_reply_delivery_state()
    assert len(second) == 1
    assert second[0].kind == "retained_queued"
    assert second[0].invocation_token == replacement

    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_recovery_wrong_purpose_running_fails_closed(tmp_path):
    """A running token that is not a REPLY (TASK_FOLLOWUP) fails closed: no
    runnable token, no replacement, no unowned work launched."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    followup = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    db.stamp_invocation_started(followup.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "running_invocation_token, running_from_seq, running_through_seq, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, followup.invocation_token, 1, 1,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "invalid_running_token_on_recovery"
    # No replacement REPLY was minted.
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    # The TASK_FOLLOWUP row itself was not touched by recovery.
    assert db.get_invocation_any_status(followup.invocation_token).status is ThreadInvocationStatus.PENDING


def test_recovery_wrong_pair_running_fails_closed(tmp_path):
    """A running token whose invocation belongs to a different pair fails
    closed and never launches unowned work."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    # A REPLY owned by bob, referenced from alice's state row.
    bob_reply = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(bob_reply.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "running_invocation_token, running_from_seq, running_through_seq, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, bob_reply.invocation_token, 1, 1,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []
    # bob's REPLY is untouched (still pending/started) — never terminalized
    # by alice's failed-closed recovery.
    assert db.get_invocation_any_status(bob_reply.invocation_token).status is ThreadInvocationStatus.PENDING
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.queued_invocation_token is None


def test_recovery_missing_queued_token_fails_closed(tmp_path):
    """A queued token that no longer resolves (missing row) fails closed and
    clears the queued slot without minting replacement work."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, "nonexistent-token",
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "invalid_queued_token_on_recovery"
    assert _pending_reply_rows(db, "THR-001", "alice") == []


def test_cutover_snapshot_atomic_against_concurrent_append(tmp_path):
    """BEGIN IMMEDIATE spans every cutoff-defining read, so a concurrent
    message append cannot commit between the cutover snapshot and its state
    commit (the old pre-transaction tail read dropped such a message from the
    required range). Deterministic: hold the open cutover transaction right
    after its in-transaction tail read, show a second connection's write is
    refused (SQLITE_BUSY), then release and show the append lands cleanly
    post-cutover."""
    import sqlite3 as _sqlite3
    import threading

    path = tmp_path / "happyranch.db"
    db = Database(path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m0",
    )  # tail == 1
    _seed_pending_reply(db, "THR-001", "alice", triggering_seq=1)

    # Deterministically hold the cutover's open write-lock transaction right
    # after its in-transaction tail read and before the state commit, so a
    # second connection can attempt an append against the held write lock.
    entered_txn = threading.Event()
    release_txn = threading.Event()
    real_tail = db._thread_tail_seq

    def held_tail(thread_id):
        tail = real_tail(thread_id)
        entered_txn.set()
        release_txn.wait(timeout=10)
        return tail

    db._thread_tail_seq = held_tail
    outcome = {}

    def run_cutover():
        outcome["states"] = db.cutover_thread_reply_delivery_state("THR-001")

    t = threading.Thread(target=run_cutover)
    t.start()
    try:
        assert entered_txn.wait(timeout=10), (
            "cutover never reached its open transaction"
        )
        # While the write lock is held, a second connection must NOT be able
        # to commit an append: with timeout=0 it fails immediately with
        # SQLITE_BUSY (a documented SQLite lock outcome).
        conn2 = _sqlite3.connect(str(path), timeout=0, check_same_thread=False)
        try:
            try:
                conn2.execute("BEGIN IMMEDIATE")
            except _sqlite3.OperationalError:
                outcome["append_refused"] = True
            else:
                outcome["append_refused"] = False
                conn2.rollback()
        finally:
            conn2.close()
        assert outcome["append_refused"] is True, (
            "second connection acquired the write lock during the cutover "
            "snapshot — a torn required_through_seq is possible"
        )
    finally:
        release_txn.set()
        t.join(timeout=10)
        assert not t.is_alive(), "cutover thread did not finish"
        db._thread_tail_seq = real_tail  # restore the instance method

    states = outcome["states"]
    assert len(states) == 1
    st = states[0]
    assert st.acknowledged_through_seq == 0   # from_seq(1) - 1
    assert st.required_through_seq == 1       # snapshot taken at tail == 1
    assert st.queued_invocation_token is not None

    # The append is now a clean post-cutover message (serialized after the
    # cutover commit), never silently folded into the pre-cutover range.
    conn2 = _sqlite3.connect(str(path), timeout=5, check_same_thread=False)
    try:
        conn2.execute(
            "INSERT INTO thread_messages "
            "(thread_id, seq, speaker, kind, created_at) "
            "VALUES ('THR-001', 2, 'founder', 'message', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn2.commit()
    finally:
        conn2.close()

    # Durable result: the coalesced wake covers from_seq..required == 1..1 and
    # the appended message is strictly above required_through_seq (a clean
    # post-cutover message, not a dropped tail).
    assert db._thread_tail_seq("THR-001") == 2
    assert db.get_reply_delivery_state("THR-001", "alice").required_through_seq == 1
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    assert pending[0].triggering_seq == 1


def test_recovery_consumed_running_fails_closed(tmp_path):
    """A running slot whose receipt is already CONSUMED fails closed: clear the
    running slot, mint no replacement, return no runnable token, and leave the
    consumed receipt untouched."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    running_token = _seed_running_state(db, "THR-001", "alice", ack=0, req=1)
    db.consume_invocation(running_token)

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "running_already_terminal_on_recovery"
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    assert db.get_invocation_any_status(running_token).status is ThreadInvocationStatus.CONSUMED


def test_recovery_failed_running_fails_closed(tmp_path):
    """A running slot whose receipt was already terminalized by the generic
    reaper (FAILED) fails closed: clear the running slot, mint no replacement,
    return no runnable token, and preserve the truthful failed receipt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    running_token = _seed_running_state(db, "THR-001", "alice", ack=0, req=1)
    db.reap_pending_invocations(
        "THR-001",
        purposes=[ThreadInvocationPurpose.REPLY],
        decline_reason="archive_started",
    )

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "running_already_terminal_on_recovery"
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    failed_inv = db.get_invocation_any_status(running_token)
    assert failed_inv.status is ThreadInvocationStatus.FAILED
    assert failed_inv.decline_reason == "archive_started"


def test_recovery_malformed_running_range_fails_closed(tmp_path):
    """A running slot whose durable range is inconsistent (running_through >
    required) fails closed: no replacement, no runnable token, and the
    malformed receipt is left untouched."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(inv.invocation_token, session_id=None)
    # required == 1 but running_through == 5 → malformed.
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "running_invocation_token, running_from_seq, running_through_seq, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, inv.invocation_token, 1, 5,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.running_invocation_token is None
    assert st.running_from_seq is None
    assert st.running_through_seq is None
    assert st.queued_invocation_token is None
    assert st.last_terminal_reason == "malformed_running_range_on_recovery"
    # No replacement minted: exactly the original malformed receipt remains
    # pending (recovery never terminalized it).
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    assert pending[0].invocation_token == inv.invocation_token
    assert db.get_invocation_any_status(inv.invocation_token).status is ThreadInvocationStatus.PENDING


def test_recovery_both_slots_corruption_fails_closed(tmp_path):
    """A row with BOTH queued and running slots populated is corruption:
    recovery clears both slots, mints no replacement, returns no runnable
    token, and retires (terminalizes) ONLY the validated same-pair pending
    REPLY receipts so no duplicate pending REPLY pair survives."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    queued = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    running = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(running.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, running_invocation_token, running_from_seq, "
        "running_through_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, queued.invocation_token,
         running.invocation_token, 1, 1, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    assert st.running_from_seq is None
    assert st.running_through_seq is None
    assert st.last_terminal_reason == "corrupt_both_slots_on_recovery"
    # No replacement minted and no duplicate pending pair survives: both
    # owned same-pair pending REPLY receipts are retired with a truthful
    # corruption receipt, leaving ZERO (not two) pending REPLYs for the pair.
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 0
    queued_inv = db.get_invocation_any_status(queued.invocation_token)
    assert queued_inv.status is ThreadInvocationStatus.FAILED
    assert queued_inv.decline_reason == "corrupt_both_slots_on_recovery"
    assert queued_inv.consumed_at is not None
    running_inv = db.get_invocation_any_status(running.invocation_token)
    assert running_inv.status is ThreadInvocationStatus.FAILED
    assert running_inv.decline_reason == "corrupt_both_slots_on_recovery"
    assert running_inv.consumed_at is not None


def test_recovery_both_slots_corruption_spares_foreign_and_terminal(tmp_path):
    """A corrupt both-slots row whose receipts are foreign-pair or already
    terminal fails closed WITHOUT mutating them: recovery retires only owned
    same-pair PENDING REPLY receipts, never a foreign-pair or terminal
    receipt, and issues no blanket pending-REPLY reaper."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    # queued slot references a REPLY owned by a DIFFERENT pair (bob) — foreign.
    foreign = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # running slot references an already-terminal (consumed) receipt owned by
    # alice — terminal, so it must be left untouched.
    terminal = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.consume_invocation(terminal.invocation_token)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, running_invocation_token, running_from_seq, "
        "running_through_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, foreign.invocation_token,
         terminal.invocation_token, 1, 1, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    assert st.last_terminal_reason == "corrupt_both_slots_on_recovery"

    # The foreign-pair receipt is untouched (still pending, still bob's).
    foreign_inv = db.get_invocation_any_status(foreign.invocation_token)
    assert foreign_inv.status is ThreadInvocationStatus.PENDING
    assert foreign_inv.agent_name == "bob"

    # The already-terminal receipt is untouched (still consumed, no reason).
    terminal_inv = db.get_invocation_any_status(terminal.invocation_token)
    assert terminal_inv.status is ThreadInvocationStatus.CONSUMED
    assert terminal_inv.decline_reason is None

    # No pending REPLY remains for the owned pair alice (neither referenced
    # receipt was an owned pending REPLY to retire).
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    # bob's foreign pending REPLY is untouched — NOT a blanket reaper.
    assert len(_pending_reply_rows(db, "THR-001", "bob")) == 1


def test_recovery_both_slots_corruption_is_idempotent(tmp_path):
    """Repeat recovery after a both-slots corruption retires the owned
    pending REPLYs exactly once and leaves no duplicate pending pair: a
    second pass is a clean no-op."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    queued = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    running = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(running.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, running_invocation_token, running_from_seq, "
        "running_through_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, queued.invocation_token,
         running.invocation_token, 1, 1, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    first = db.recover_reply_delivery_state()
    assert first == []
    assert _pending_reply_rows(db, "THR-001", "alice") == []

    # Second pass: the corrupt row now has no ownership slots, so it is not
    # re-selected; nothing is re-terminalized or re-minted.
    second = db.recover_reply_delivery_state()
    assert second == []
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    assert db.get_invocation_any_status(queued.invocation_token).status is ThreadInvocationStatus.FAILED
    assert db.get_invocation_any_status(running.invocation_token).status is ThreadInvocationStatus.FAILED


def test_recovery_does_not_change_generic_reap_semantics(tmp_path):
    """Before Slice B, the generic pending reaper is unchanged: it still fails
    pending REPLY/BOOTSTRAP rows with daemon_restart and never touches the
    (unwired) delivery state table."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    reaped = db.reap_pending_invocations(
        "THR-001",
        purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
        decline_reason="archive_started",
    )
    assert reaped == 2
    # The new table was not consulted or written by the legacy reaper.
    assert db.list_reply_delivery_states() == []


@pytest.mark.parametrize("n", [7, 8])
def test_recovery_both_slots_pair_scoped_sweep_parameterized(tmp_path, n):
    """Founder-required proof (THR-198 seq 20): a corrupt both-slots row whose
    pair owns N pending REPLY receipts (2 referenced by the slots + N-2
    unreferenced orphans) is swept PAIR-SCOPED. Every owned PENDING REPLY is
    retired under ``corrupt_both_slots_on_recovery`` (zero owned pending REPLY
    remain, not merely the two referenced rows), no replacement is minted, both
    slots clear with a truthful diagnostic, and the foreign-pair / wrong-purpose
    / already-terminal controls are never mutated."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )

    # Two owned pending REPLY receipts referenced by the corrupt slots.
    queued = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    running = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(running.invocation_token, session_id=None)

    # N-2 additional owned same-pair PENDING REPLY receipts OUTSIDE both slots
    # (orphaned duplicates the old slot-scoped retirement left behind).
    orphan_tokens = [
        db.mint_thread_invocation(
            thread_id="THR-001", agent_name="alice",
            triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
        ).invocation_token
        for _ in range(n - 2)
    ]

    # Protected controls that must survive the sweep untouched:
    foreign = db.mint_thread_invocation(  # foreign-pair PENDING REPLY (bob)
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    wrong_purpose = db.mint_thread_invocation(  # same-pair wrong-purpose PENDING
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    terminal = db.mint_thread_invocation(  # same-pair already-terminal REPLY
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.consume_invocation(terminal.invocation_token)

    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, running_invocation_token, running_from_seq, "
        "running_through_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, queued.invocation_token,
         running.invocation_token, 1, 1, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    # (b) no returned/minted replacement on this fail-closed path.
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    # (c) cleared ownership slots + truthful corruption diagnostic.
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    assert st.running_from_seq is None
    assert st.running_through_seq is None
    assert st.last_terminal_reason == "corrupt_both_slots_on_recovery"

    # (a) zero owned PENDING REPLY rows for the pair — the N-2 orphans are
    # swept too, not merely the two referenced receipts.
    assert _pending_reply_rows(db, "THR-001", "alice") == []

    # (d) each owned PENDING REPLY terminalized under the corruption reason.
    for token in [queued.invocation_token, running.invocation_token, *orphan_tokens]:
        inv = db.get_invocation_any_status(token)
        assert inv.status is ThreadInvocationStatus.FAILED
        assert inv.decline_reason == "corrupt_both_slots_on_recovery"
        assert inv.consumed_at is not None

    # (e) every protected control remains unchanged.
    foreign_inv = db.get_invocation_any_status(foreign.invocation_token)
    assert foreign_inv.status is ThreadInvocationStatus.PENDING
    assert foreign_inv.decline_reason is None
    wrong_inv = db.get_invocation_any_status(wrong_purpose.invocation_token)
    assert wrong_inv.status is ThreadInvocationStatus.PENDING
    assert wrong_inv.decline_reason is None
    terminal_inv = db.get_invocation_any_status(terminal.invocation_token)
    assert terminal_inv.status is ThreadInvocationStatus.CONSUMED
    assert terminal_inv.decline_reason is None
    assert len(_pending_reply_rows(db, "THR-001", "bob")) == 1

    # (f) repeat recovery is a no-op.
    second = db.recover_reply_delivery_state()
    assert second == []
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    assert len(_pending_reply_rows(db, "THR-001", "bob")) == 1


def test_recovery_both_slots_corruption_missing_token_in_slot(tmp_path):
    """A corrupt both-slots row whose running slot references a non-existent
    invocation token fails closed without a crash: the pair-scoped sweep retires
    only real owned PENDING REPLY receipts, the missing token is never mutated
    (no such row exists), and no replacement is minted."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    queued = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # running slot references a token with no invocation row.
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, running_invocation_token, running_from_seq, "
        "running_through_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, queued.invocation_token,
         "missing-token", 1, 1, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    entries = db.recover_reply_delivery_state()
    assert entries == []

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    assert st.last_terminal_reason == "corrupt_both_slots_on_recovery"

    # The owned pending REPLY referenced by the queued slot is swept; the
    # missing token has no row to mutate and never minted a replacement.
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    queued_inv = db.get_invocation_any_status(queued.invocation_token)
    assert queued_inv.status is ThreadInvocationStatus.FAILED
    assert queued_inv.decline_reason == "corrupt_both_slots_on_recovery"
    assert db.get_invocation_any_status("missing-token") is None


@pytest.mark.parametrize("n", [7, 8])
def test_cutover_legacy_pending_deep_stack_parameterized(tmp_path, n):
    """Founder-required proof (THR-198 seq 20): a deep historical stack of N
    legacy pending REPLY rows for one pair coalesces to exactly one queued wake
    spanning min(triggering_seq)..tail, terminalizing all N with
    ``coalesced_cutover``, while foreign-pair and wrong-purpose rows are
    preserved and repeat/reopen cutover stays idempotent."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    # Only alice is a participant: cutover iterates participants, so bob's
    # foreign pending REPLY must never be touched.
    db.add_thread_participant("THR-001", "alice", added_by="founder")

    # Foreign-pair PENDING REPLY (bob, not a participant) — must survive.
    bob_reply = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # Same-pair wrong-purpose PENDING rows — must survive.
    task_followup = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    bootstrap = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    tail = n + 3
    for i in range(tail):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )  # transcript seqs 1..tail

    # N legacy pending REPLYs at seqs 2..N+1 (min == 2).
    seqs = list(range(2, n + 2))
    for s in seqs:
        _seed_pending_reply(db, "THR-001", "alice", triggering_seq=s)

    created = db.cutover_thread_reply_delivery_state("THR-001")
    assert len(created) == 1
    st = created[0]
    assert st.agent_name == "alice"
    assert st.acknowledged_through_seq == 1   # from_seq - 1
    assert st.required_through_seq == tail    # current tail
    assert st.queued_invocation_token is not None
    assert st.running_invocation_token is None

    # Exactly one replacement pending REPLY spanning min..tail.
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    assert pending[0].triggering_seq == 2      # min(triggering_seq)
    assert pending[0].invocation_token == st.queued_invocation_token

    # All N legacy rows terminalized with coalesced_cutover.
    terminalized = [
        inv for inv in db.list_thread_invocations("THR-001")
        if inv.agent_name == "alice"
        and inv.purpose is ThreadInvocationPurpose.REPLY
        and inv.status is ThreadInvocationStatus.FAILED
        and inv.decline_reason == "coalesced_cutover"
    ]
    assert len(terminalized) == n
    assert sorted(inv.triggering_seq for inv in terminalized) == seqs

    # Foreign-pair and wrong-purpose rows preserved.
    assert (
        db.get_invocation_any_status(bob_reply.invocation_token).status
        is ThreadInvocationStatus.PENDING
    )
    assert (
        db.get_invocation_any_status(task_followup.invocation_token).status
        is ThreadInvocationStatus.PENDING
    )
    assert (
        db.get_invocation_any_status(bootstrap.invocation_token).status
        is ThreadInvocationStatus.PENDING
    )

    # Repeat + reopen idempotence: no new state, no duplicate wake, foreign
    # pair still exactly one pending REPLY.
    assert db.cutover_thread_reply_delivery_state("THR-001") == []
    db.close()
    db2 = Database(tmp_path / "happyranch.db")
    assert db2.cutover_thread_reply_delivery_state("THR-001") == []
    assert len(_pending_reply_rows(db2, "THR-001", "alice")) == 1
    assert len(_pending_reply_rows(db2, "THR-001", "bob")) == 1


# ── GitHub #688 Phase 1 Slice B: route/runner activation store ops ───────
# The atomic arrival / claim / settle / discard / projection operations that
# wire the Slice-A additive table into the writer routes and the runner.
# These are the store-owned state transitions; routes and the runner must
# never open-code the queued/running token invariants.


def _thread_with_agents(db, *agents, subject="x"):
    db.insert_thread(ThreadRecord(id="THR-001", subject=subject))
    for a in agents:
        db.add_thread_participant("THR-001", a, added_by="founder")
    return "THR-001"


def test_record_conversational_arrival_burst_one_queued_per_pair(tmp_path):
    """A burst of N messages creates at most one unstarted REPLY per
    (thread_id, agent_name): the first arrival mints one queued wake covering
    the whole range, later arrivals only advance required_through_seq."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")

    seq, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice", "bob"],
    )
    assert seq == 1
    by_name = {a.agent_name: a for a in arrivals}
    assert set(by_name) == {"alice", "bob"}
    assert by_name["alice"].coalesced is False
    assert by_name["alice"].from_seq == 1 and by_name["alice"].through_seq == 1
    alice_token = by_name["alice"].invocation_token
    assert alice_token is not None
    bob_token = by_name["bob"].invocation_token
    assert bob_token is not None

    # Messages 2..4 coalesce into the existing wakes: no new queue tokens.
    for i in range(2, 5):
        _, arrivals = db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice", "bob"],
        )
        for a in arrivals:
            assert a.coalesced is True
            assert a.invocation_token is None
            assert a.through_seq == i

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.acknowledged_through_seq == 0
    assert st.required_through_seq == 4
    assert st.queued_invocation_token == alice_token
    assert st.running_invocation_token is None

    # Exactly one pending REPLY per pair.
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1
    assert len(_pending_reply_rows(db, "THR-001", "bob")) == 1
    assert _pending_reply_rows(db, "THR-001", "alice")[0].invocation_token == alice_token


def test_record_conversational_arrival_idempotent_replay_noop(tmp_path):
    """Re-arrival of an already-covered sequence is a no-op (duplicate
    notification safety): no token, required watermark unchanged."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    seq, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    assert seq == 1
    assert arrivals[0].invocation_token is not None

    # Replay the same message seq (e.g. a duplicate notification path).
    seq2, arrivals2 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1-dup",
        recipients=["alice"],
    )
    assert seq2 == 2  # a NEW transcript row always appends
    assert arrivals2[0].coalesced is True
    assert arrivals2[0].invocation_token is None
    assert arrivals2[0].through_seq == 2
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.required_through_seq == 2
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_record_conversational_arrival_coalesces_while_running(tmp_path):
    """An arrival while a REPLY is running raises required only; it must not
    touch the running token, the immutable range, or create a queued token."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    # Transcript rows 1..3 exist; a running wake covers 1..3 (claimed from ack=0).
    for i in range(1, 4):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
        )
    running_token = _seed_running_state(db, "THR-001", "alice", ack=0, req=3)
    before = db.get_reply_delivery_state("THR-001", "alice")
    assert before is not None and before.running_invocation_token == running_token

    seq, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m4",
        recipients=["alice"],
    )
    assert seq == 4
    assert arrivals[0].coalesced is True
    assert arrivals[0].invocation_token is None
    after = db.get_reply_delivery_state("THR-001", "alice")
    assert after is not None
    assert after.required_through_seq == 4
    assert after.running_invocation_token == running_token
    assert after.running_from_seq == 1
    assert after.running_through_seq == 3
    assert after.queued_invocation_token is None
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_claim_conversational_reply_transfers_queued_to_running(tmp_path):
    """The durable queued→running CAS stamps started_at and snapshots the
    inclusive range (acknowledged+1 .. required) atomically."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    seq, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    for i in range(2, 5):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice"],
        )

    claim = db.claim_conversational_reply(token)
    assert claim is not None
    assert claim.thread_id == "THR-001"
    assert claim.agent_name == "alice"
    assert claim.acknowledged_through_seq == 0
    assert claim.running_from_seq == 1
    assert claim.running_through_seq == 4
    assert claim.required_through_seq == 4

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.queued_invocation_token is None
    assert st.running_invocation_token == token
    assert st.running_from_seq == 1
    assert st.running_through_seq == 4
    inv = db.get_invocation_any_status(token)
    assert inv is not None
    assert inv.status is ThreadInvocationStatus.PENDING
    assert inv.started_at is not None  # working evidence for recovery


def test_claim_conversational_reply_stale_duplicate_returns_none(tmp_path):
    """A stale/duplicate queue notification no-ops: the CAS only succeeds once
    for the pair's queued token, before any provider/prompt work."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token

    assert db.claim_conversational_reply(token) is not None
    # Second notification for the same token: the slot now holds it as running,
    # so the queued→running CAS must miss (no double claim, no re-prompt).
    assert db.claim_conversational_reply(token) is None
    # A token with no delivery-state row (e.g. BOOTSTRAP/TASK_FOLLOWUP or a
    # random/foreign token) never claims.
    assert db.claim_conversational_reply("no-such-token") is None


def test_claim_conversational_reply_rejects_mismatched_invocation(tmp_path):
    """Claim is refused when the queued token's invocation is not a pending
    same-pair REPLY (fail closed — never launch an unowned token)."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token

    # Consume the invocation row directly (simulates an already-settled receipt
    # still referenced by a stale queued slot).
    db.consume_invocation(token)
    assert db.claim_conversational_reply(token) is None


def test_settle_reply_acks_claimed_range_only_and_mints_single_followon(tmp_path):
    """A successful reply acknowledges ONLY the claimed coverage
    (running_through); an arrival during the run yields exactly one
    post-settlement follow-on covering the retained range."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    for i in range(2, 5):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice"],
        )
    claim = db.claim_conversational_reply(token)
    assert claim is not None and claim.running_through_seq == 4

    # Arrival during the run: raises required to 5.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m5-in-run",
        recipients=["alice"],
    )

    settlement = db.settle_conversational_reply(
        token=token, outcome="reply",
    )
    assert settlement is not None
    assert settlement.outcome == "reply"
    assert settlement.acknowledged_through_seq == 4  # claimed coverage only
    assert settlement.required_through_seq == 5
    assert settlement.follow_on_token is not None  # exactly one follow-on
    assert settlement.retry_required is False

    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.acknowledged_through_seq == 4
    assert st.required_through_seq == 5
    assert st.running_invocation_token is None
    assert st.queued_invocation_token == settlement.follow_on_token
    assert st.last_terminal_reason is None  # successful reply leaves no reason

    # The follow-on is the single pending REPLY and covers the retained range.
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 1
    assert pending[0].invocation_token == settlement.follow_on_token
    assert pending[0].triggering_seq == 5

    # Original token is CONSUMED; no duplicate follow-on can be minted because
    # settling the same token again is a no-op (slots are clear).
    assert (
        db.get_invocation_any_status(token).status
        is ThreadInvocationStatus.CONSUMED
    )
    assert db.settle_conversational_reply(token=token, outcome="reply") is None


def test_settle_decline_acks_claimed_range_only_and_mints_single_followon(tmp_path):
    """A silent decline acknowledges only the claimed range; arrivals during
    the run yield exactly one follow-on. No transcript row, no turns bump."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    for i in range(2, 5):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice"],
        )
    db.claim_conversational_reply(token)
    # Arrival during the run → required 5.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m5-in-run",
        recipients=["alice"],
    )

    settlement = db.settle_conversational_reply(
        token=token, outcome="decline", decline_reason="nothing to add",
    )
    assert settlement is not None
    assert settlement.acknowledged_through_seq == 4
    assert settlement.follow_on_token is not None
    assert (
        db.get_invocation_any_status(token).status
        is ThreadInvocationStatus.DECLINED
    )
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_settle_failure_and_timeout_leave_retry_required_no_followon(tmp_path):
    """Failure/timeout do not advance acknowledgement, mint no immediate
    retry (no hot loop), and leave retry_required for the next arrival."""
    for idx, (outcome, status) in enumerate((
        ("failed", ThreadInvocationStatus.FAILED),
        ("timeout", ThreadInvocationStatus.TIMEOUT),
    )):
        db = Database(tmp_path / f"happyranch-{idx}.db")
        _thread_with_agents(db, "alice")
        _, arrivals = db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
            recipients=["alice"],
        )
        token = arrivals[0].invocation_token
        for i in range(2, 5):
            db.record_conversational_arrival(
                thread_id="THR-001", speaker="founder",
                kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
                recipients=["alice"],
            )
        db.claim_conversational_reply(token)
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="m5-in-run",
            recipients=["alice"],
        )

        settlement = db.settle_conversational_reply(
            token=token, outcome=outcome, decline_reason="provider boom",
        )
        assert settlement is not None
        assert settlement.acknowledged_through_seq == 0  # untouched
        assert settlement.required_through_seq == 5
        assert settlement.follow_on_token is None  # no hot loop
        assert settlement.retry_required is True
        st = db.get_reply_delivery_state("THR-001", "alice")
        assert st is not None
        assert st.queued_invocation_token is None
        assert st.running_invocation_token is None
        assert st.last_terminal_reason == "provider boom"
        assert (
            db.get_invocation_any_status(token).status is status
        )
        # No immediate retry minted: zero pending REPLYs after failure.
        assert len(_pending_reply_rows(db, "THR-001", "alice")) == 0

        # The NEXT conversational arrival covers the retained + new range with
        # exactly one queued wake (retry-required delivery).
        _, arrivals2 = db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="m6-next",
            recipients=["alice"],
        )
        assert arrivals2[0].invocation_token is not None
        assert arrivals2[0].from_seq == 1  # retained unacknowledged range
        assert arrivals2[0].through_seq == 6
        assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_settle_stale_token_returns_none(tmp_path):
    """Settling a token that owns no delivery-state slot returns None so the
    caller applies the legacy terminal transition (BOOTSTRAP/TASK_FOLLOWUP,
    or an already-settled REPLY)."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    db.claim_conversational_reply(token)
    db.settle_conversational_reply(token=token, outcome="decline")
    # Already settled → None (no double settlement, no second follow-on).
    assert db.settle_conversational_reply(token=token, outcome="decline") is None
    # A BOOTSTRAP token is never owned by delivery state.
    boot = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert (
        db.settle_conversational_reply(
            token=boot.invocation_token, outcome="decline",
        ) is None
    )


def test_reply_conversational_atomic_append_settle_broadcast(tmp_path):
    """Reply ordering is material: append the reply, settle the speaker's
    claimed range, schedule broadcast arrivals to the OTHER participants,
    all in one commit. The speaker never wakes for their own reply."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")
    # bob holds a running REPLY covering 1..1 with an in-run arrival at 2.
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["bob"],
    )
    bob_token = arrivals[0].invocation_token
    db.claim_conversational_reply(bob_token)
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m2-in-run",
        recipients=["bob"],
    )

    seq, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001",
        speaker="bob",
        body_markdown="my reply",
        attachments=[],
        token=bob_token,
        token_purpose=ThreadInvocationPurpose.REPLY,
    )
    assert seq == 3
    assert settlement is not None
    assert settlement.acknowledged_through_seq == 1  # claimed coverage only
    assert settlement.follow_on_token is not None  # covers in-run message 2
    bob_state = db.get_reply_delivery_state("THR-001", "bob")
    assert bob_state is not None
    assert bob_state.acknowledged_through_seq == 1
    assert bob_state.required_through_seq == 2
    assert bob_state.queued_invocation_token == settlement.follow_on_token

    # Broadcast: alice (the only other participant) gets a queued wake for the
    # reply; bob is NOT woken for his own reply.
    by_name = {a.agent_name: a for a in broadcast}
    assert set(by_name) == {"alice"}
    assert by_name["alice"].invocation_token is not None
    assert by_name["alice"].from_seq == 3 and by_name["alice"].through_seq == 3
    alice_state = db.get_reply_delivery_state("THR-001", "alice")
    assert alice_state is not None
    assert alice_state.acknowledged_through_seq == 2
    assert alice_state.required_through_seq == 3

    # Exactly one pending REPLY each.
    assert len(_pending_reply_rows(db, "THR-001", "bob")) == 1
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1
    assert (
        db.get_invocation_any_status(bob_token).status
        is ThreadInvocationStatus.CONSUMED
    )


def test_reply_conversational_non_reply_token_uses_legacy_consume(tmp_path):
    """A BOOTSTRAP/TASK_FOLLOWUP token replying through the reply route keeps
    the legacy consume — no delivery-state settlement is involved."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")
    boot = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bob",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    seq, settlement, broadcast = db.reply_conversational(
        thread_id="THR-001",
        speaker="bob",
        body_markdown="bootstrap reply",
        attachments=[],
        token=boot.invocation_token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert settlement is None  # legacy path, no delivery-state settlement
    assert seq == 1
    assert (
        db.get_invocation_any_status(boot.invocation_token).status
        is ThreadInvocationStatus.CONSUMED
    )
    # Broadcast still wakes the other participant.
    assert {a.agent_name for a in broadcast} == {"alice"}
    # No delivery-state row was created for the BOOTSTRAP speaker's pair.
    assert db.get_reply_delivery_state("THR-001", "bob") is None


def test_discard_reply_delivery_no_resurrection(tmp_path):
    """Founder abort / archive discards through an explicit boundary: owned
    REPLY rows terminalize once, state clears, and a later message starts a
    FRESH wake — the discarded tokens never resurrect."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice", "bob"],
    )
    alice_token = arrivals[0].invocation_token
    bob_token = next(a.invocation_token for a in arrivals if a.agent_name == "bob")
    db.claim_conversational_reply(alice_token)
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m2-in-run",
        recipients=["alice", "bob"],
    )

    aborted = db.discard_reply_delivery(
        "THR-001", decline_reason="founder_aborted",
    )
    # alice's running + queued-side and bob's queued REPLY rows all terminalize.
    assert aborted >= 2
    for token in (alice_token, bob_token):
        inv = db.get_invocation_any_status(token)
        assert inv is not None and inv.status is ThreadInvocationStatus.FAILED
        assert inv.decline_reason == "founder_aborted"
    # State cleared to the boundary: acknowledged == required, no slots.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.acknowledged_through_seq == st.required_through_seq == 2
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None
    # Live projection is empty (nothing queued/running/retry-required).
    assert db.list_reply_delivery_projections("THR-001") == []
    # No resurrection: zero pending REPLYs.
    assert _pending_reply_rows(db, "THR-001", "alice") == []
    assert _pending_reply_rows(db, "THR-001", "bob") == []

    # A later message starts a fresh wake after the boundary.
    _, arrivals2 = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m3-after-abort",
        recipients=["alice"],
    )
    assert arrivals2[0].invocation_token is not None
    assert arrivals2[0].from_seq == 3  # after the explicit discard boundary
    assert arrivals2[0].invocation_token != alice_token  # no resurrection
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_discard_reply_delivery_agent_scoped(tmp_path):
    """Participant removal discards only the removed pair's state; other
    participants' wakes are untouched."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice", "bob"],
    )
    alice_token = arrivals[0].invocation_token
    bob_token = next(a.invocation_token for a in arrivals if a.agent_name == "bob")

    removed = db.discard_reply_delivery(
        "THR-001", agent_name="alice",
        decline_reason="participant_removed",
        status=ThreadInvocationStatus.DECLINED,
    )
    assert removed == 1
    assert (
        db.get_invocation_any_status(alice_token).status
        is ThreadInvocationStatus.DECLINED
    )
    assert (
        db.get_invocation_any_status(bob_token).status
        is ThreadInvocationStatus.PENDING
    )
    alice_state = db.get_reply_delivery_state("THR-001", "alice")
    assert alice_state is not None
    assert alice_state.queued_invocation_token is None
    bob_state = db.get_reply_delivery_state("THR-001", "bob")
    assert bob_state is not None
    assert bob_state.queued_invocation_token == bob_token


def test_reply_delivery_projection_states_truthful(tmp_path):
    """The pair-level projection truthfully distinguishes queued, working, and
    retry_required with inclusive ranges and a store-computed coalesced count;
    fully-settled pairs are omitted and no subprocess is fabricated."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice", "bob")

    # queued state (alice) + running state (bob).
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
        recipients=["alice", "bob"],
    )
    alice_token = arrivals[0].invocation_token
    bob_token = next(a.invocation_token for a in arrivals if a.agent_name == "bob")
    for i in range(2, 5):
        db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice", "bob"],
        )
    db.claim_conversational_reply(bob_token)

    proj = {p.agent_name: p for p in db.list_reply_delivery_projections("THR-001")}
    assert set(proj) == {"alice", "bob"}
    alice = proj["alice"]
    assert alice.state == "queued"
    assert alice.from_seq == 1 and alice.through_seq == 4
    assert alice.coalesced_message_count == 4
    assert alice.started_at is None  # queued is not a running subprocess
    bob = proj["bob"]
    assert bob.state == "running"
    assert bob.from_seq == 1 and bob.through_seq == 4
    assert bob.started_at is not None  # working evidence only from started_at

    # retry_required after a failed run with in-run arrivals.
    db.record_conversational_arrival(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m5-in-run",
        recipients=["bob"],
    )
    db.settle_conversational_reply(
        token=bob_token, outcome="failed", decline_reason="boom",
    )
    proj2 = {p.agent_name: p for p in db.list_reply_delivery_projections("THR-001")}
    assert proj2["bob"].state == "retry_required"
    assert proj2["bob"].from_seq == 1
    assert proj2["bob"].through_seq == 5
    assert proj2["bob"].last_terminal_reason == "boom"
    assert proj2["bob"].started_at is None  # diagnostic, not a live subprocess

    # Fully-settled pair (decline the alice wake) is omitted from the live
    # projection (terminal history stays on per-message responder strips).
    db.settle_conversational_reply(token=alice_token, outcome="decline")
    proj3 = {p.agent_name: p for p in db.list_reply_delivery_projections("THR-001")}
    assert "alice" not in proj3


def test_concurrent_arrivals_never_duplicate_queued_winner(tmp_path):
    """Concurrent arrival notifications for one pair serialize on the store's
    re-entrant lock + BEGIN IMMEDIATE: exactly one queued winner survives and
    the pair never holds two unstarted REPLYs."""
    import threading

    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    barrier = threading.Barrier(8)
    minted: list[str] = []

    def post(i: int) -> None:
        barrier.wait()
        _, arrivals = db.record_conversational_arrival(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{i}",
            recipients=["alice"],
        )
        tok = arrivals[0].invocation_token
        if tok is not None:
            minted.append(tok)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(minted) == 1
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.required_through_seq == 8
    assert st.queued_invocation_token == minted[0]
    assert len(_pending_reply_rows(db, "THR-001", "alice")) == 1


def test_task_followup_mint_never_touches_delivery_state(tmp_path):
    """TASK_FOLLOWUP minting is a causal one-shot direct mint: it creates no
    reply-delivery-state row, claims nothing, and settles through the legacy
    path (settle_conversational_reply returns None for its token)."""
    db = Database(tmp_path / "happyranch.db")
    _thread_with_agents(db, "alice")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="dispatch",
    )
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        "THR-001", agent_name="alice", triggering_seq=1,
    )
    assert inv.purpose is ThreadInvocationPurpose.TASK_FOLLOWUP
    # No delivery-state row is created by the followup mint.
    assert db.list_reply_delivery_states() == []
    # The followup token is not claimable and not settleable via the store.
    assert db.claim_conversational_reply(inv.invocation_token) is None
    assert (
        db.settle_conversational_reply(
            token=inv.invocation_token, outcome="decline",
        ) is None
    )
    # Legacy terminal transition applies instead.
    db.mark_invocation_declined(inv.invocation_token, decline_reason=None)
    assert (
        db.get_invocation_any_status(inv.invocation_token).status
        is ThreadInvocationStatus.DECLINED
    )
    assert db.list_reply_delivery_states() == []
