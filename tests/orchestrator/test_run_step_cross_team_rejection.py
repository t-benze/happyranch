from __future__ import annotations

from src.models import NextStep
from src.orchestrator.teams import TeamsRegistry, DEFAULT_LAYOUT


def test_registry_flags_cross_team_delegation() -> None:
    registry = TeamsRegistry._from_layout(DEFAULT_LAYOUT)
    # Content Manager trying to delegate to dev_agent (engineering team)
    caller_team = registry.team_for_manager("content_manager")
    target_team = registry.team_for_agent("dev_agent")
    assert caller_team == "content"
    assert target_team == "engineering"
    assert caller_team != target_team
