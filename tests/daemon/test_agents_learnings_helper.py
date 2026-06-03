from pathlib import Path

from runtime.daemon.routes.agents import _append_to_learnings_file


def test_append_creates_header_on_first_write(tmp_path: Path):
    learnings = tmp_path / "learnings.md"
    _append_to_learnings_file(learnings, "dev_agent", "first thing")
    content = learnings.read_text()
    assert content.startswith("# Learnings: dev_agent\n")
    assert "- first thing\n" in content


def test_append_multiple_entries(tmp_path: Path):
    learnings = tmp_path / "learnings.md"
    _append_to_learnings_file(learnings, "dev_agent", "first")
    _append_to_learnings_file(learnings, "dev_agent", "second")
    content = learnings.read_text()
    assert "- first\n" in content
    assert "- second\n" in content
    # No duplicate header
    assert content.count("# Learnings") == 1
