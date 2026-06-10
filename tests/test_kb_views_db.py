from __future__ import annotations

from runtime.infrastructure.database import Database


def test_record_kb_view_inserts_then_increments(db: Database):
    db.record_kb_view("alpha-entry")
    stats = db.kb_view_stats()
    assert len(stats) == 1
    assert stats[0]["slug"] == "alpha-entry"
    assert stats[0]["view_count"] == 1
    assert stats[0]["last_viewed_at"] is not None

    first_seen = stats[0]["last_viewed_at"]
    db.record_kb_view("alpha-entry")
    stats = db.kb_view_stats()
    assert len(stats) == 1, "UPSERT must not create a sibling row"
    assert stats[0]["view_count"] == 2
    # last_viewed_at advances (or stays equal) but is never cleared.
    assert stats[0]["last_viewed_at"] >= first_seen


def test_kb_view_stats_orders_by_count_desc(db: Database):
    db.record_kb_view("low")
    db.record_kb_view("high")
    db.record_kb_view("high")
    db.record_kb_view("high")
    db.record_kb_view("mid")
    db.record_kb_view("mid")

    ranked = [r["slug"] for r in db.kb_view_stats()]
    assert ranked == ["high", "mid", "low"]


def test_kb_view_stats_empty_when_no_views(db: Database):
    assert db.kb_view_stats() == []
