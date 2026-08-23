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
    """Existing REPLY mint path does not populate the new table (control pairs
    remain inactive until Slice B wires activation)."""
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
    token, and touches neither invocation row."""
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
    # No replacement minted: exactly the two original pending REPLYs remain,
    # both untouched (still pending).
    pending = _pending_reply_rows(db, "THR-001", "alice")
    assert len(pending) == 2
    assert db.get_invocation_any_status(queued.invocation_token).status is ThreadInvocationStatus.PENDING
    assert db.get_invocation_any_status(running.invocation_token).status is ThreadInvocationStatus.PENDING


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
