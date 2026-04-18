from __future__ import annotations

from pathlib import Path

import pytest
import yaml


SKILLS_ROOT = Path(__file__).resolve().parent.parent / "protocol" / "skills"


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise ValueError("missing frontmatter delimiter")
    _, fm, _body = text.split("---", 2)
    return yaml.safe_load(fm)


@pytest.mark.parametrize("skill_name", ["start-task", "make-worktree", "manage-repo", "manage-agent"])
def test_skill_has_required_frontmatter(skill_name: str) -> None:
    skill_md = SKILLS_ROOT / skill_name / "SKILL.md"
    assert skill_md.exists(), f"missing {skill_md}"
    fm = _parse_frontmatter(skill_md.read_text())
    assert fm["name"] == skill_name
    assert isinstance(fm.get("description"), str)
    assert len(fm["description"]) > 20


def test_start_task_references_session_id_on_callbacks() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "--session-id" in body
    assert "report-completion" in body
    assert "learning" in body


def test_make_worktree_references_claude_worktrees_path() -> None:
    body = (SKILLS_ROOT / "make-worktree" / "SKILL.md").read_text()
    assert ".claude/worktrees/" in body
    assert "git worktree add" in body


def test_start_task_skill_documents_memory_consult() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "task_history.md" in body
    assert "opc recall" in body
    assert "Consult memory" in body


def test_start_task_skill_documents_artifact_convention() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "artifacts/" in body
    assert "artifact_dir" in body


def test_skill_cli_commands_exist() -> None:
    """Every `opc <subcommand>` referenced by a skill must be a real subcommand."""
    from src.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    known = set(subparsers_action.choices.keys())

    referenced = set()
    for skill in SKILLS_ROOT.iterdir():
        body = (skill / "SKILL.md").read_text()
        for line in body.splitlines():
            if "opc " in line:
                idx = line.find("opc ")
                tokens = line[idx + 4:].split()
                if tokens:
                    referenced.add(tokens[0])
    referenced -= {"<subcommand>"}
    missing = referenced - known
    assert not missing, f"skills reference missing CLI commands: {missing}"
