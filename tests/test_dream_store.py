from __future__ import annotations

from runtime.infrastructure.dream_store import DreamStore


def test_write_and_read_dream_transcript(tmp_path):
    store = DreamStore(tmp_path / "dreams")
    path = store.write_transcript(
        dream_id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        window_start="2026-06-08T02:00:00+00:00",
        window_end="2026-06-09T02:00:00+00:00",
        summary="Found recurring friction.",
        transcript_markdown="Full private transcript.\n",
        new_learnings_count=1,
        kb_candidate_count=2,
        founder_thread_id="THR-001",
    )

    assert path == tmp_path / "dreams" / "DREAM-001.md"
    text = store.read_transcript("DREAM-001")
    assert "dream_id: DREAM-001" in text
    assert "agent_name: dev_agent" in text
    assert "# Summary" in text
    assert "Found recurring friction." in text
    assert "# Transcript" in text
    assert "Full private transcript." in text
