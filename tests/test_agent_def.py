from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.orchestrator.agent_def import (
    AgentDef,
    AgentParseError,
    parse_agent_text,
    render_agent_text,
)


SAMPLE = """\
---
name: dev_agent
team: engineering
role: worker
executor: claude
allow_rules:
  - "gh issue close"
repos:
  my-opc: https://github.com/example/my-opc.git
enrolled_by: engineering_head
enrolled_at_task: TASK-042
enrolled_at: 2026-04-15T08:00:00Z
---

You are the Dev Agent. Your responsibilities are X, Y, Z.
"""


def test_parse_full_frontmatter() -> None:
    agent = parse_agent_text(SAMPLE, expected_name="dev_agent")
    assert agent.name == "dev_agent"
    assert agent.team == "engineering"
    assert agent.role == "worker"
    assert agent.executor == "claude"
    assert agent.allow_rules == ("gh issue close",)
    assert agent.repos == {"my-opc": "https://github.com/example/my-opc.git"}
    assert agent.enrolled_by == "engineering_head"
    assert agent.enrolled_at_task == "TASK-042"
    assert agent.enrolled_at == datetime(2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert "Dev Agent" in agent.system_prompt


def test_parse_minimal_frontmatter() -> None:
    text = (
        "---\n"
        "name: minimal\n"
        "team: content\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="minimal")
    assert agent.allow_rules == ()
    assert agent.repos == {}
    assert agent.enrolled_by is None
    assert agent.enrolled_at_task is None
    assert agent.enrolled_at is None


def test_parse_rejects_filename_mismatch() -> None:
    with pytest.raises(AgentParseError, match="name mismatch"):
        parse_agent_text(SAMPLE, expected_name="other_agent")


@pytest.mark.parametrize("bad", [
    "no frontmatter at all",
    "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n",  # no closing fence
])
def test_parse_rejects_malformed_frontmatter(bad: str) -> None:
    with pytest.raises(AgentParseError):
        parse_agent_text(bad, expected_name="x")


def test_parse_rejects_invalid_role() -> None:
    text = (
        "---\nname: x\nteam: t\nrole: bogus\nexecutor: claude\n---\nbody\n"
    )
    with pytest.raises(AgentParseError, match="role"):
        parse_agent_text(text, expected_name="x")


def test_parse_rejects_invalid_executor() -> None:
    text = (
        "---\nname: x\nteam: t\nrole: worker\nexecutor: gpt\n---\nbody\n"
    )
    with pytest.raises(AgentParseError, match="executor"):
        parse_agent_text(text, expected_name="x")


def test_parse_rejects_empty_body() -> None:
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\n\n"
    with pytest.raises(AgentParseError, match="empty body"):
        parse_agent_text(text, expected_name="x")


def test_render_round_trip() -> None:
    agent = parse_agent_text(SAMPLE, expected_name="dev_agent")
    text2 = render_agent_text(agent)
    agent2 = parse_agent_text(text2, expected_name="dev_agent")
    assert agent == agent2


def test_render_omits_null_optional_fields() -> None:
    agent = AgentDef(
        name="x",
        team="t",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt="hello\n",
    )
    text = render_agent_text(agent)
    assert "enrolled_by:" in text
    assert "null" in text  # YAML emits null explicitly
