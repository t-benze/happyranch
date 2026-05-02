"""Tests for workspace_adapters.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from src.orchestrator.workspace_adapters import (
    allow_rules_for_agent,
    bash_allow_prefixes_for_agent,
)
from src.runtime import RuntimeDir


def _write_agent(rt: RuntimeDir, name: str, allow_rules: list[str]) -> None:
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    (rt.agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )


def test_baseline_only_when_agent_none(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    rules = allow_rules_for_agent(rt, None, cli=False)
    assert rules == ["Bash(opc:*)"]


def test_baseline_plus_extras_settings_form(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "eh", ["gh pr close", "gh issue close"])
    rules = allow_rules_for_agent(rt, "eh", cli=False)
    assert rules == [
        "Bash(opc:*)",
        "Bash(gh pr close:*)",
        "Bash(gh issue close:*)",
    ]


def test_baseline_plus_extras_cli_form(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "eh", ["gh pr close"])
    rules = allow_rules_for_agent(rt, "eh", cli=True)
    assert rules == ["Bash(opc *)", "Bash(gh pr close *)"]


def test_unknown_agent_gets_baseline_only(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    rules = allow_rules_for_agent(rt, "ghost", cli=False)
    assert rules == ["Bash(opc:*)"]


def test_bash_prefixes_baseline_only_when_agent_none(tmp_path: Path) -> None:
    """opencode.json renders raw prefixes (no Bash() wrapping); the source of
    truth (per-agent allow_rules + opc baseline) is the same as the Claude
    surfaces."""
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert bash_allow_prefixes_for_agent(rt, None) == ["opc"]


def test_bash_prefixes_baseline_plus_extras(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "eh", ["gh pr close", "gh issue close"])
    assert bash_allow_prefixes_for_agent(rt, "eh") == [
        "opc",
        "gh pr close",
        "gh issue close",
    ]


def test_bash_prefixes_unknown_agent_gets_baseline_only(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert bash_allow_prefixes_for_agent(rt, "ghost") == ["opc"]
