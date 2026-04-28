"""Tests for prompt_loader.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from src.orchestrator import prompt_loader
from src.runtime import RuntimeDir


def _write(runtime: RuntimeDir, name: str, allow_rules: list[str]) -> None:
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    text = (
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )
    (runtime.agents_dir / f"{name}.md").write_text(text)


def test_returns_empty_for_unknown_agent(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert prompt_loader.allow_rules_for_agent(rt, "ghost") == ()


def test_returns_declared_rules(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write(rt, "eh", ["gh pr close", "gh issue close"])
    assert prompt_loader.allow_rules_for_agent(rt, "eh") == ("gh pr close", "gh issue close")


def test_returns_empty_when_field_empty(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write(rt, "dev", [])
    assert prompt_loader.allow_rules_for_agent(rt, "dev") == ()
