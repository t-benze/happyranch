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

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry


class OrgConsistencyError(RuntimeError):
    """Raised when org/teams.yaml and org/agents/*.md disagree."""


def validate_team_membership(paths: OrgPaths, teams: TeamsRegistry) -> None:
    """Require matching roles and membership in both canonical directions.

    Active definitions must be registered exactly once. A declared pending
    worker remains valid enrollment state; pending managers cannot execute.
    Report all drift so partial three-file migrations fail attachment clearly.
    """
    known_teams = set(teams.teams())
    active = {agent.name: agent for agent in prompt_loader.list_agents(paths)}
    pending = {agent.name: agent for agent in prompt_loader.list_pending(paths)}
    drift: list[str] = []
    memberships: dict[str, list[tuple[str, str]]] = {}
    for team in teams.teams():
        manager = teams.manager_for_team(team)
        entries = [(worker, "worker") for worker in manager.workers]
        if manager.name is not None:
            entries.insert(0, (manager.name, "manager"))
        for name, role in entries:
            memberships.setdefault(name, []).append((team, role))
            agent = active.get(name) or (pending.get(name) if role == "worker" else None)
            if agent is None or agent.team != team or agent.role != role:
                drift.append(f"  - teams.yaml {team!r} {role} {name!r} has no matching {role} agent file")
    for name, registrations in memberships.items():
        if len(registrations) != 1:
            drift.append(f"  - agent {name!r} has duplicate team memberships: {registrations!r}")
    for name, agent in active.items():
        if agent.team not in known_teams:
            drift.append(f"  - agents/{name}.md declares unregistered team {agent.team!r}")
        elif memberships.get(name) != [(agent.team, agent.role)]:
            drift.append(f"  - agents/{name}.md declares {agent.team!r}/{agent.role}, but teams.yaml disagrees")
    if drift:
        raise OrgConsistencyError(
            "org content is inconsistent — agent files and teams.yaml disagree:\n"
            + "\n".join(drift) + "\nFix teams.yaml (or the offending agent file) and reload the org."
        )
