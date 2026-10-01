from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text


def ensure_coherent_authority(org) -> int:
    """Make a daemon route fixture a coherent real OrgState authority seam."""
    paths = OrgPaths(root=org.root)
    for reviewer in ("code_reviewer", "senior_dev"):
        if org.teams.team_for_agent(reviewer) is None:
            org.teams.add_worker("engineering", reviewer)

    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    for team in org.teams.teams():
        registration = org.teams.manager_for_team(team)
        identities = ((registration.name, "manager"), *(
            (worker, "worker") for worker in registration.workers
        ))
        for name, role in identities:
            current = prompt_loader.load_agent(paths, name)
            if current is None:
                current = AgentDef(
                    name=name,
                    team=team,
                    role=role,
                    executor="claude",
                    allow_rules=(),
                    repos={},
                    enrolled_by="test-fixture",
                    enrolled_at_task="TASK-U2A-TEST",
                    enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    system_prompt=f"prompt:{name}",
                    description=f"description:{name}",
                )
            elif current.team != team or current.role != role:
                current = replace(current, team=team, role=role)
            (paths.agents_dir / f"{name}.md").write_text(render_agent_text(current))

    org.workflow_authority.recover_or_publish(publisher="test-fixture")
    return org.workflow_authority.verify_admission_ready().generation
