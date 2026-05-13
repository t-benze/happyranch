from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.infrastructure.database import Database
from src.models import TaskRecord


def test_dispatched_from_thread_id_round_trips(tmp_path):
    """After Task 4 wires TaskRecord + insert_task, a thread-dispatched task
    should round-trip its dispatched_from_thread_id through SQLite. Today
    this fails: Pydantic drops the unknown field and/or insert_task ignores
    the column.
    """
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-001", brief="x", dispatched_from_thread_id="THR-007",
    ))
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_thread_id == "THR-007"


def test_dispatched_from_talk_id_round_trips(tmp_path):
    """Regression guard for the sibling column. Should pass today and after Task 4."""
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-002", brief="x", dispatched_from_talk_id="TALK-1",
    ))
    fetched = db.get_task("TASK-002")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id == "TALK-1"


from src.models import (
    ThreadInvocation, ThreadInvocationPurpose, ThreadInvocationStatus,
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
        addressed_to=["@all"],
    )
    assert m.kind is ThreadMessageKind.MESSAGE
    inv = ThreadInvocation(
        thread_id="THR-001", agent_name="dev",
        invocation_token="abc", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING


def test_next_thread_id_starts_at_one(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.next_thread_id() == "THR-001"


def test_next_thread_id_uses_max_suffix(tmp_path):
    db = Database(tmp_path / "opc.db")
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
    db = Database(tmp_path / "opc.db")
    t = ThreadRecord(id="THR-001", subject="Refund policy")
    db.insert_thread(t)
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.id == "THR-001"
    assert got.subject == "Refund policy"
    assert got.status is ThreadStatus.OPEN
    assert got.turn_cap == 500


def test_get_thread_missing_returns_none(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.get_thread("THR-404") is None


def test_list_threads_orders_by_started_desc(tmp_path):
    db = Database(tmp_path / "opc.db")
    a = ThreadRecord(id="THR-001", subject="a", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = ThreadRecord(id="THR-002", subject="b", started_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    db.insert_thread(a)
    db.insert_thread(b)
    rows = db.list_threads(limit=10)
    assert [r.id for r in rows] == ["THR-002", "THR-001"]


def test_add_and_list_participants(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    names = [p.agent_name for p in db.list_thread_participants("THR-001")]
    assert sorted(names) == ["alice", "bob"]


def test_add_thread_participant_idempotent(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.add_thread_participant("THR-001", "alice", added_by="founder") is False


def test_is_thread_participant(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.is_thread_participant("THR-001", "alice")
    assert not db.is_thread_participant("THR-001", "bob")


def test_append_thread_message_allocates_monotonic_seq(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    seq_a = db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hello", addressed_to=["@all"],
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
    assert msgs[0].addressed_to == ["@all"]
    assert msgs[1].addressed_to is None


def test_append_thread_decline_message(tmp_path):
    db = Database(tmp_path / "opc.db")
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
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "participant_added", "agent_name": "alice"},
    )
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].system_payload["kind_tag"] == "participant_added"


def test_mint_thread_invocation(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING
    assert len(inv.invocation_token) >= 16
    assert inv.purpose is ThreadInvocationPurpose.REPLY


def test_get_pending_invocation_by_token(tmp_path):
    db = Database(tmp_path / "opc.db")
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
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.consume_invocation(inv.invocation_token) is True
    assert db.consume_invocation(inv.invocation_token) is False
    assert db.get_pending_invocation(inv.invocation_token) is None


def test_record_dispatch_on_invocation(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-009") is True
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-010") is False


def test_reap_pending_invocations(tmp_path):
    db = Database(tmp_path / "opc.db")
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
        triggering_seq=2, purpose=ThreadInvocationPurpose.CLOSE_OUT,
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
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.increment_thread_turns_used("THR-001", by=2)
    db.increment_thread_turns_used("THR-001", by=1)
    t = db.get_thread("THR-001")
    assert t.turns_used == 3


def test_set_thread_status_archiving(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status(
        "THR-001",
        status=ThreadStatus.ARCHIVING,
        summary="done talking",
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVING
    assert t.summary == "done talking"
    assert t.archive_requested_at is not None


def test_finalize_thread_archived(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="s")
    db.finalize_thread_archived(
        "THR-001",
        transcript_path="/tmp/THR-001.md",
        new_kb_slugs=["refund-policy"],
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.archived_at is not None
    assert t.transcript_path == "/tmp/THR-001.md"
    assert t.new_kb_slugs == ["refund-policy"]


def test_set_thread_turn_cap(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_turn_cap("THR-001", new_cap=1000)
    assert db.get_thread("THR-001").turn_cap == 1000
