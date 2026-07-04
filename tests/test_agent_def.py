from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.orchestrator.agent_def import (
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


@pytest.mark.parametrize("executor", ["claude", "codex", "opencode", "pi"])
def test_parse_accepts_supported_executors(executor: str) -> None:
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        f"executor: {executor}\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="x")
    assert agent.executor == executor


def test_parse_rejects_empty_body() -> None:
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\n\n"
    with pytest.raises(AgentParseError, match="empty body"):
        parse_agent_text(text, expected_name="x")


def test_render_round_trip() -> None:
    agent = parse_agent_text(SAMPLE, expected_name="dev_agent")
    text2 = render_agent_text(agent)
    agent2 = parse_agent_text(text2, expected_name="dev_agent")
    assert agent == agent2


def test_description_round_trips() -> None:
    """description is a one-line summary used by managers picking workers — it
    must survive the parse/render cycle and carry through pending and active files."""
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        "executor: claude\n"
        "description: Writes destination guides for HK and Macau\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="x")
    assert agent.description == "Writes destination guides for HK and Macau"
    rerendered = parse_agent_text(render_agent_text(agent), expected_name="x")
    assert rerendered.description == agent.description


def test_description_defaults_to_none_when_absent() -> None:
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\nbody\n"
    agent = parse_agent_text(text, expected_name="x")
    assert agent.description is None


def test_legacy_session_timeout_seconds_in_frontmatter_is_ignored() -> None:
    """Older runtimes carry `session_timeout_seconds` in the agent frontmatter.
    The field is no longer honored (per-task override on `tasks` row replaces
    it), but legacy files must still parse cleanly so the runtime keeps booting.
    """
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        "executor: claude\n"
        "session_timeout_seconds: 7200\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="x")
    assert not hasattr(agent, "session_timeout_seconds")


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


# ---- model field ----

def test_parse_model_when_present() -> None:
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        "executor: claude\n"
        "model: gpt-5\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="x")
    assert agent.model == "gpt-5"


def test_parse_model_defaults_to_none_when_absent() -> None:
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\nbody\n"
    agent = parse_agent_text(text, expected_name="x")
    assert agent.model is None


def test_parse_rejects_empty_model_string() -> None:
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        "executor: claude\n"
        "model: ''\n"
        "---\n"
        "body\n"
    )
    with pytest.raises(AgentParseError, match="model"):
        parse_agent_text(text, expected_name="x")


def test_render_round_trip_with_model() -> None:
    text = (
        "---\n"
        "name: x\n"
        "team: t\n"
        "role: worker\n"
        "executor: claude\n"
        "model: claude-sonnet-5\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="x")
    rendered = render_agent_text(agent)
    agent2 = parse_agent_text(rendered, expected_name="x")
    assert agent2.model == "claude-sonnet-5"
    assert agent == agent2


def test_absent_model_round_trips_as_none() -> None:
    """An agent file without model should parse as model=None and render without it."""
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\nbody\n"
    agent = parse_agent_text(text, expected_name="x")
    assert agent.model is None
    rendered = render_agent_text(agent)
    agent2 = parse_agent_text(rendered, expected_name="x")
    assert agent2.model is None
