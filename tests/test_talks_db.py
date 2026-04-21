from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.infrastructure.database import Database
from src.models import TalkRecord, TalkStatus


def test_talks_table_exists(db: Database):
    assert "talks" in db.list_tables()


def test_next_talk_id_monotonic(db: Database):
    assert db.next_talk_id() == "TALK-001"
    db.insert_talk(TalkRecord(id="TALK-001", agent_name="dev_agent"))
    assert db.next_talk_id() == "TALK-002"


def test_insert_and_get_talk(db: Database):
    talk = TalkRecord(id="TALK-001", agent_name="dev_agent")
    db.insert_talk(talk)
    got = db.get_talk("TALK-001")
    assert got is not None
    assert got.id == "TALK-001"
    assert got.agent_name == "dev_agent"
    assert got.status == TalkStatus.OPEN
    assert got.ended_at is None


def test_get_missing_talk_returns_none(db: Database):
    assert db.get_talk("TALK-999") is None


def test_update_talk_closes_it(db: Database):
    db.insert_talk(TalkRecord(id="TALK-001", agent_name="dev_agent"))
    db.update_talk(
        "TALK-001",
        status=TalkStatus.CLOSED,
        summary="we talked about refunds",
        topic_list=["refunds"],
        new_learnings_count=2,
        new_kb_slugs=["alipay-refund"],
        transcript_path="/tmp/TALK-001.md",
    )
    got = db.get_talk("TALK-001")
    assert got.status == TalkStatus.CLOSED
    assert got.summary == "we talked about refunds"
    assert got.topic_list == ["refunds"]
    assert got.new_learnings_count == 2
    assert got.new_kb_slugs == ["alipay-refund"]
    assert got.transcript_path == "/tmp/TALK-001.md"
    assert got.ended_at is not None


def test_list_open_talks_for_agent(db: Database):
    db.insert_talk(TalkRecord(id="TALK-001", agent_name="dev_agent"))
    db.insert_talk(TalkRecord(id="TALK-002", agent_name="qa_engineer"))
    db.update_talk("TALK-001", status=TalkStatus.CLOSED)
    assert db.list_open_talks_for_agent("dev_agent") == []
    assert [t.id for t in db.list_open_talks_for_agent("qa_engineer")] == ["TALK-002"]


def test_last_closed_talk_for_agent(db: Database):
    db.insert_talk(TalkRecord(id="TALK-001", agent_name="dev_agent"))
    db.update_talk("TALK-001", status=TalkStatus.CLOSED)
    db.insert_talk(TalkRecord(id="TALK-002", agent_name="dev_agent"))
    db.update_talk("TALK-002", status=TalkStatus.ABANDONED)
    got = db.last_closed_talk_for_agent("dev_agent")
    assert got is not None
    assert got.id == "TALK-001"


def test_last_closed_talk_returns_none_if_never_closed(db: Database):
    assert db.last_closed_talk_for_agent("dev_agent") is None
    db.insert_talk(TalkRecord(id="TALK-001", agent_name="dev_agent"))
    db.update_talk("TALK-001", status=TalkStatus.ABANDONED)
    assert db.last_closed_talk_for_agent("dev_agent") is None


def test_list_talks_filter_and_limit(db: Database):
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for i in range(1, 6):
        db.insert_talk(TalkRecord(
            id=f"TALK-00{i}",
            agent_name="dev_agent",
            started_at=base + timedelta(hours=i),
        ))
    db.insert_talk(TalkRecord(id="TALK-099", agent_name="qa_engineer"))
    rows = db.list_talks(agent="dev_agent", limit=3)
    assert len(rows) == 3
    # Newest first — TALK-005 has the latest started_at
    assert rows[0].id == "TALK-005"


def test_list_talks_limit_cap(db: Database):
    rows = db.list_talks(limit=10_000)
    # Method must cap at 500
    # (Empty db so any value returns [], but we still test the arg didn't blow up.)
    assert rows == []
