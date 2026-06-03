"""Tests for prompt_loader.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


def _make_paths(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "x")


def _write(paths: OrgPaths, name: str, allow_rules: list[str]) -> None:
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
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
    (paths.agents_dir / f"{name}.md").write_text(text)


def test_returns_empty_for_unknown_agent(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    assert prompt_loader.allow_rules_for_agent(paths, "ghost") == ()


def test_returns_declared_rules(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write(paths, "eh", ["gh pr close", "gh issue close"])
    assert prompt_loader.allow_rules_for_agent(paths, "eh") == ("gh pr close", "gh issue close")


def test_returns_empty_when_field_empty(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write(paths, "dev", [])
    assert prompt_loader.allow_rules_for_agent(paths, "dev") == ()
