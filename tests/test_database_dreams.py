from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import DreamKbCandidate, DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_next_dream_id_and_insert_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.next_dream_id() == "DREAM-001"

    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    )
    db.insert_dream(rec)

    got = db.get_dream("DREAM-001")
    assert got is not None
    assert got.id == "DREAM-001"
    assert got.agent_name == "dev_agent"
    assert got.status == DreamStatus.PENDING
    assert db.next_dream_id() == "DREAM-002"


def test_dream_unique_per_agent_local_date(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
    )
    db.insert_dream(rec)

    with pytest.raises(Exception):
        db.insert_dream(rec.model_copy(update={"id": "DREAM-002"}))


def test_list_dreams_filters_and_newest_first(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-08",
        scheduled_for=_dt(1), window_end=_dt(1),
    ))
    db.insert_dream(DreamRecord(
        id="DREAM-002", agent_name="qa_engineer", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))

    assert [d.id for d in db.list_dreams()] == ["DREAM-002", "DREAM-001"]
    assert [d.id for d in db.list_dreams(agent="dev_agent")] == ["DREAM-001"]


def test_last_successful_dream_uses_completed_status(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-08",
        scheduled_for=_dt(1), window_end=_dt(1), status=DreamStatus.FAILED,
        ended_at=_dt(1),
    ))
    db.insert_dream(DreamRecord(
        id="DREAM-002", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2), status=DreamStatus.COMPLETED,
        ended_at=_dt(2),
    ))

    got = db.get_last_successful_dream("dev_agent")
    assert got is not None
    assert got.id == "DREAM-002"


def test_dream_kb_candidates_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001",
        agent_name="dev_agent",
        slug="candidate-one",
        title="Candidate One",
        topic="workflow",
        rationale="Observed repeatedly in task history.",
        body_markdown="Use this rule when it is promoted.\n",
    ))

    rows = db.list_dream_kb_candidates(dream_id="DREAM-001")
    assert len(rows) == 1
    assert rows[0].slug == "candidate-one"
    assert rows[0].status == "pending"
