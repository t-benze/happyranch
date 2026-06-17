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


def test_update_dream_kb_candidate_promote(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Body.\n",
    ))
    rows = db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    db.update_dream_kb_candidate(
        candidate_id, status="promoted", promoted_kb_slug="candidate-one"
    )

    updated = db.list_dream_kb_candidates(dream_id="DREAM-001")
    assert len(updated) == 1
    assert updated[0].id == candidate_id
    assert updated[0].status == "promoted"
    assert updated[0].promoted_kb_slug == "candidate-one"


def test_update_dream_kb_candidate_reject(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Body.\n",
    ))
    rows = db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    db.update_dream_kb_candidate(candidate_id, status="rejected")

    updated = db.list_dream_kb_candidates(dream_id="DREAM-001")
    assert updated[0].status == "rejected"
    assert updated[0].promoted_kb_slug is None


def test_update_dream_kb_candidate_preserves_fields(tmp_path):
    """update_dream_kb_candidate must only mutate status, promoted_kb_slug, and updated_at."""
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Body.\n",
    ))
    rows = db.list_dream_kb_candidates(dream_id="DREAM-001")
    original = rows[0]
    original_created_at = original.created_at
    original_updated_at = original.updated_at

    import time
    time.sleep(0.01)  # ensure timestamp difference

    db.update_dream_kb_candidate(original.id, status="promoted", promoted_kb_slug="candidate-one")

    updated = db.list_dream_kb_candidates(dream_id="DREAM-001")[0]
    assert updated.slug == original.slug
    assert updated.title == original.title
    assert updated.topic == original.topic
    assert updated.rationale == original.rationale
    assert updated.body_markdown == original.body_markdown
    assert updated.dream_id == original.dream_id
    assert updated.agent_name == original.agent_name
    assert updated.created_at == original_created_at
    assert updated.updated_at > original_updated_at


def test_update_dream_kb_candidate_nonexistent_raises(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    with pytest.raises(ValueError, match="not found"):
        db.update_dream_kb_candidate(999, status="rejected")
