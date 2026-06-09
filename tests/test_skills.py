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


@pytest.mark.parametrize("skill_name", ["start-task", "make-worktree", "manage-repo", "manage-agent", "talk"])
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
    assert "happyranch recall" in body
    assert "Consult memory" in body


def test_start_task_skill_documents_output_dir() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "output/" in body
    assert "output_dir" in body


def test_start_task_skill_documents_kb_consult() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "Consult the knowledge base" in body
    assert "happyranch kb list" in body
    assert "happyranch kb search" in body
    assert "happyranch kb get" in body


def test_start_task_skill_documents_kb_contribute() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "Contribute to the KB" in body or "Contribute to KB" in body
    assert "happyranch kb add" in body
    # Mandatory `--from-file` pattern — Bash(happyranch:*) permission rule
    assert "--from-file" in body


def test_start_task_skill_documents_manager_decision_field() -> None:
    """Team-manager sessions need explicit guidance that the completion payload
    must include a top-level `decision` field alongside the prose `summary`.
    Without this note the skill looks identical for managers and workers,
    which caused TASK-071 (the manager wrote prose-only and the orchestrator
    escalated for missing decision)."""
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    # The skill must distinguish the manager path from the worker path.
    assert "Team-manager" in body or "team manager" in body or "team-manager" in body
    assert "decision" in body
    # The three legal decision actions must be discoverable from the skill.
    assert "delegate" in body
    assert "done" in body
    assert "escalate" in body


def test_talk_skill_documents_start_procedure() -> None:
    body = (SKILLS_ROOT / "talk" / "SKILL.md").read_text()
    assert "happyranch talk start" in body
    assert "happyranch talk status" in body
    assert "happyranch talk resume" in body
    assert "happyranch talk abandon" in body
    assert "## Since last talk" in body
    assert "## Notable tasks" in body
    assert "## New learnings" in body
    assert "## Open questions / frictions" in body
    assert "## Suggested topics" in body


def test_talk_skill_documents_end_procedure() -> None:
    body = (SKILLS_ROOT / "talk" / "SKILL.md").read_text()
    assert "happyranch talk end" in body
    assert "--from-file" in body
    assert "summary" in body
    assert "topic_list" in body
    assert "transcript_markdown" in body
    assert "learnings" in body
    assert "kb_slugs" in body
    assert "happyranch kb add" in body


def test_talk_skill_documents_single_line_rationale() -> None:
    body = (SKILLS_ROOT / "talk" / "SKILL.md").read_text()
    assert "Bash(happyranch *)" in body


def test_talk_skill_documents_self_only_dispatch() -> None:
    body = (SKILLS_ROOT / "talk" / "SKILL.md").read_text()
    assert "talk_dispatch_must_be_self" in body
    assert "may only target" in body
    assert "managers can dispatch to any agent in their team" not in body


def test_skill_cli_commands_exist() -> None:
    """Every `happyranch <subcommand>` referenced by a skill must be a real subcommand."""
    from cli.main import build_parser

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    known = set(subparsers_action.choices.keys())

    referenced = set()
    for skill in SKILLS_ROOT.iterdir():
        body = (skill / "SKILL.md").read_text()
        for line in body.splitlines():
            if "happyranch " in line:
                idx = line.find("happyranch ")
                tokens = line[idx + len("happyranch "):].split()
                if tokens:
                    referenced.add(tokens[0].rstrip("`,."))
    referenced -= {"<subcommand>"}
    missing = referenced - known
    assert not missing, f"skills reference missing CLI commands: {missing}"
