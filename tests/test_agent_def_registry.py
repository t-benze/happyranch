"""Tests for agent_def with registry-based executor validation (THR-052)."""
from __future__ import annotations

import pytest

from runtime.orchestrator.agent_def import (
    AgentParseError,
    parse_agent_text,
)
from runtime.orchestrator.executor_registry import (
    ExecutorProfile,
    get_registry,
    reset_registry,
)


class TestAgentDefWithRegistryExecutors:
    def setup_method(self) -> None:
        reset_registry()

    def teardown_method(self) -> None:
        reset_registry()

    def test_accepts_registered_builtin_executor(self) -> None:
        text = (
            "---\nname: x\nteam: t\nrole: worker\nexecutor: codex\n---\nbody\n"
        )
        agent = parse_agent_text(text, expected_name="x")
        assert agent.executor == "codex"

    def test_accepts_registered_custom_executor(self) -> None:
        registry = get_registry()
        registry.register_custom_profile(
            ExecutorProfile(
                name="openclaw",
                kind="custom",
                workspace_adapter_id="pi",
                command_adapter_id="custom-adapter:openclaw",
                readiness_marker_fragment="AGENTS.md",
            )
        )
        text = (
            "---\nname: x\nteam: t\nrole: worker\nexecutor: openclaw\n---\nbody\n"
        )
        agent = parse_agent_text(text, expected_name="x")
        assert agent.executor == "openclaw"

    def test_accepts_unregistered_executor(self) -> None:
        """Parsing must not enforce registry membership: an org must be able
        to attach (and stay reachable via manage-agent/set-executor) even
        when an agent references a not-currently-registered executor.
        Registry enforcement remains fail-closed at launch time
        (executor_registry.build_executor) and at write time
        (PUT /agents/{name}/executor)."""
        text = (
            "---\nname: x\nteam: t\nrole: worker\nexecutor: gpt5\n---\nbody\n"
        )
        agent = parse_agent_text(text, expected_name="x")
        assert agent.executor == "gpt5"

    def test_rejects_empty_executor_string(self) -> None:
        text = (
            "---\nname: x\nteam: t\nrole: worker\nexecutor: ''\n---\nbody\n"
        )
        with pytest.raises(AgentParseError, match="non-empty string"):
            parse_agent_text(text, expected_name="x")

    def test_all_four_builtins_accepted(self) -> None:
        for name in ("claude", "codex", "opencode", "pi"):
            text = (
                "---\n"
                f"name: x\n"
                "team: t\n"
                "role: worker\n"
                f"executor: {name}\n"
                "---\n"
                "body\n"
            )
            agent = parse_agent_text(text, expected_name="x")
            assert agent.executor == name
