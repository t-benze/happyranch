from pathlib import Path

import pytest

from src.infrastructure.talk_store import TalkStore, InvalidTranscript


def test_write_and_read_transcript(tmp_path: Path):
    store = TalkStore(tmp_path / "talks")
    path = store.write_transcript(
        talk_id="TALK-001",
        agent_name="dev_agent",
        started_at="2026-04-21T09:00:00Z",
        ended_at="2026-04-21T09:42:00Z",
        topic_list=["refunds", "flaky QA"],
        new_learnings_count=2,
        new_kb_slugs=["alipay-refund"],
        summary="Discussed refund timeouts.",
        transcript_markdown="## turn 1\nfounder: ...\nagent: ...",
    )
    assert path.exists()
    body = path.read_text()
    assert "talk_id: TALK-001" in body
    assert "# Summary" in body
    assert "Discussed refund timeouts." in body
    assert "# Transcript (agent's perspective)" in body
    assert "## turn 1" in body


def test_write_is_atomic(tmp_path: Path):
    """No half-written file if the rename target already exists somehow."""
    store = TalkStore(tmp_path / "talks")
    first = store.write_transcript(
        talk_id="TALK-001", agent_name="dev_agent",
        started_at="2026-04-21T09:00:00Z", ended_at="2026-04-21T09:42:00Z",
        topic_list=[], new_learnings_count=0, new_kb_slugs=[],
        summary="s1", transcript_markdown="t1",
    )
    # Second call with same talk_id is treated as an overwrite. This should
    # complete cleanly via atomic rename — not leave the target in a mixed state.
    second = store.write_transcript(
        talk_id="TALK-001", agent_name="dev_agent",
        started_at="2026-04-21T09:00:00Z", ended_at="2026-04-21T09:42:00Z",
        topic_list=[], new_learnings_count=0, new_kb_slugs=[],
        summary="s2", transcript_markdown="t2",
    )
    assert first == second
    assert "s2" in second.read_text()


def test_rejects_oversized_transcript(tmp_path: Path):
    store = TalkStore(tmp_path / "talks")
    too_big = "x" * (1_000_000)
    with pytest.raises(InvalidTranscript):
        store.write_transcript(
            talk_id="TALK-001", agent_name="dev_agent",
            started_at="2026-04-21T09:00:00Z", ended_at="2026-04-21T09:42:00Z",
            topic_list=[], new_learnings_count=0, new_kb_slugs=[],
            summary="ok", transcript_markdown=too_big,
        )


def test_read_transcript(tmp_path: Path):
    store = TalkStore(tmp_path / "talks")
    store.write_transcript(
        talk_id="TALK-001", agent_name="dev_agent",
        started_at="2026-04-21T09:00:00Z", ended_at="2026-04-21T09:42:00Z",
        topic_list=[], new_learnings_count=0, new_kb_slugs=[],
        summary="ok", transcript_markdown="hello",
    )
    content = store.read_transcript("TALK-001")
    assert "hello" in content


def test_topic_with_punctuation_roundtrips(tmp_path: Path):
    """Topics containing YAML-significant characters must not silently split/coerce."""
    import yaml as _yaml

    store = TalkStore(tmp_path / "talks")
    path = store.write_transcript(
        talk_id="TALK-001", agent_name="dev_agent",
        started_at="2026-04-21T09:00:00Z", ended_at="2026-04-21T09:42:00Z",
        topic_list=["refunds, general", "yes", "tag: urgent"],
        new_learnings_count=0, new_kb_slugs=[],
        summary="ok", transcript_markdown="hello",
    )
    body = path.read_text(encoding="utf-8")
    fm_block = body.split("---\n", 2)[1]
    fm = _yaml.safe_load(fm_block)
    assert fm["topic_list"] == ["refunds, general", "yes", "tag: urgent"]
