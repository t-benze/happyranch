"""Cross-file consistency checks for an org's content tree.

An org's source of truth lives in two places:

- ``org/agents/<name>.md`` — agent files (parsed by ``prompt_loader``)
- ``org/teams.yaml`` — team index (parsed by ``TeamsRegistry``)

``manage-agent enroll`` pairs both writes under ``teams_lock``. Founder
hand-edits (especially when bootstrapping the first team manager) can
declare an agent in one place but forget the other. This module catches
the drift at org-load time so a misconfigured org refuses to attach
rather than silently failing later at dispatch / manage-agent.
"""
from __future__ import annotations

from src.orchestrator import prompt_loader
from src.orchestrator._paths import OrgPaths
from src.orchestrator.teams import TeamsRegistry


class OrgConsistencyError(RuntimeError):
    """Raised when org/teams.yaml and org/agents/*.md disagree."""


def validate_team_membership(paths: OrgPaths, teams: TeamsRegistry) -> None:
    """Refuse to load an org whose active agents reference unknown teams.

    Scans ``org/agents/*.md`` (active only — pending agents are validated
    again at approve time). For every agent file, checks that its declared
    ``team`` exists in ``teams.yaml``. For managers, additionally checks
    the team's manager entry matches the agent name.

    Raises ``OrgConsistencyError`` listing every drift found (not just the
    first) so the founder sees the full picture in one read.
    """
    known_teams = set(teams.teams())
    drift: list[str] = []

    for agent in prompt_loader.list_agents(paths):
        if agent.team not in known_teams:
            drift.append(
                f"  - agents/{agent.name}.md declares team {agent.team!r}, "
                f"which is not registered in teams.yaml"
            )
            continue
        if agent.role == "manager":
            registered_manager = teams.manager_for_team(agent.team).name
            if registered_manager != agent.name:
                drift.append(
                    f"  - agents/{agent.name}.md claims to manage "
                    f"{agent.team!r}, but teams.yaml lists "
                    f"{registered_manager!r} as the manager"
                )

    if not drift:
        return

    raise OrgConsistencyError(
        "org content is inconsistent — agent files and teams.yaml disagree:\n"
        + "\n".join(drift)
        + "\nFix teams.yaml (or the offending agent file) and reload the org."
    )
