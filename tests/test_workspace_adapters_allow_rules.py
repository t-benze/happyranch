"""Tests for workspace_adapters.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from src.orchestrator._paths import OrgPaths
from src.orchestrator.workspace_adapters import allow_rules_for_agent
from src.runtime import RuntimeDir


def _make_paths(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "test")


def _write_agent(paths: OrgPaths, name: str, allow_rules: list[str]) -> None:
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    (paths.agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )


def test_baseline_only_when_agent_none(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    rules = allow_rules_for_agent(paths, None, cli=False)
    assert rules == ["Bash(opc:*)"]


def test_baseline_plus_extras_settings_form(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_agent(paths, "eh", ["gh pr close", "gh issue close"])
    rules = allow_rules_for_agent(paths, "eh", cli=False)
    assert rules == [
        "Bash(opc:*)",
        "Bash(gh pr close:*)",
        "Bash(gh issue close:*)",
    ]


def test_baseline_plus_extras_cli_form(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_agent(paths, "eh", ["gh pr close"])
    rules = allow_rules_for_agent(paths, "eh", cli=True)
    assert rules == ["Bash(opc *)", "Bash(gh pr close *)"]


def test_unknown_agent_gets_baseline_only(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    rules = allow_rules_for_agent(paths, "ghost", cli=False)
    assert rules == ["Bash(opc:*)"]
