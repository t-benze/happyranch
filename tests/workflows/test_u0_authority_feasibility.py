from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from runtime.models import CompletionReport
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.chain import build_prior_leg_context


def _paths(org_state):
    from runtime.orchestrator._paths import OrgPaths

    return OrgPaths(root=org_state.root)


def _org_state(tmp_path):
    """Minimal real daemon state; no daemon lifecycle or queue is started."""
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir

    runtime = RuntimeDir.init(tmp_path / "runtime")
    root = runtime.orgs_dir / "alpha"
    (root / "org").mkdir(parents=True)
    (root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    return DaemonState.from_runtime(runtime, Settings()).orgs["alpha"]


def _seed_agent(org_state, name: str, prompt: str) -> None:
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    paths = _paths(org_state)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(name=name, team="engineering", role="worker", executor="claude", allow_rules=(), repos={}, enrolled_by="engineering_head", enrolled_at_task="TASK-U0", enrolled_at=datetime.now(timezone.utc), system_prompt=prompt)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _update(revision: str, prompt: str):
    from runtime.daemon.routes.agents import ManageAgentBody

    return ManageAgentBody(action="update", name="dev_agent", task_id="TASK-U0", session_id="sess-u0", expected_revision=revision, system_prompt=prompt)


def test_actual_prior_leg_context_is_immediate_report_only_not_authority_snapshot() -> None:
    report = CompletionReport(task_id="TASK-1", agent="maker_a", status="completed", confidence=90, output_summary="approved r1", verdict="APPROVE", output_dir="output/TASK-1")
    context = build_prior_leg_context(child_task_id="TASK-1", report=report)
    assert "maker_a" in context and "approved r1" in context
    assert "submission_digest" not in context
    assert "authority_envelope" not in context
    assert "current_revision" not in context


def test_actual_manage_agent_route_demonstrates_hash_aba_and_no_dispatch(tmp_path) -> None:
    """Supported route experiment: A→B→A re-admits stale A's content hash."""
    from runtime.daemon.routes import agents as agents_mod

    org_state = _org_state(tmp_path)
    org_state.sessions.set_active("TASK-U0", "engineering_head", "sess-u0")
    _seed_agent(org_state, "dev_agent", "A\n")
    paths = _paths(org_state)
    a = prompt_loader.agent_revision(paths, "dev_agent")
    assert a is not None

    async def exercise() -> None:
        assert await agents_mod.manage_agent("alpha", _update(a, "B\n"), org_state) == {"ok": True}
        b = prompt_loader.agent_revision(paths, "dev_agent")
        assert b is not None and b != a
        assert await agents_mod.manage_agent("alpha", _update(b, "A\n"), org_state) == {"ok": True}
        assert prompt_loader.agent_revision(paths, "dev_agent") == a
        assert await agents_mod.manage_agent("alpha", _update(a, "C\n"), org_state) == {"ok": True}

    asyncio.run(exercise())
    assert "C" in (paths.agents_dir / "dev_agent.md").read_text()
    assert not (paths.workspaces_dir / "dev_agent").exists()
    assert len([r for r in org_state.db.get_audit_logs("TASK-U0") if r["action"] == "agent_managed"]) == 3


def test_actual_manage_agent_route_serializes_same_hash_writers_at_lock(tmp_path) -> None:
    from runtime.daemon.routes import agents as agents_mod

    org_state = _org_state(tmp_path)
    org_state.sessions.set_active("TASK-U0", "engineering_head", "sess-u0")
    _seed_agent(org_state, "dev_agent", "initial\n")
    revision = prompt_loader.agent_revision(_paths(org_state), "dev_agent")
    assert revision is not None

    async def exercise() -> None:
        async with org_state.teams_lock:
            winner = asyncio.create_task(agents_mod.manage_agent("alpha", _update(revision, "winner\n"), org_state))
            loser = asyncio.create_task(agents_mod.manage_agent("alpha", _update(revision, "loser\n"), org_state))
            await asyncio.sleep(0)  # deterministic barrier: both await the real lock
        assert await winner == {"ok": True}
        with pytest.raises(HTTPException) as raised:
            await loser
        assert raised.value.status_code == 409
        assert raised.value.detail["code"] == "stale_agent_revision"

    asyncio.run(exercise())
    assert "winner" in (_paths(org_state).agents_dir / "dev_agent.md").read_text()
    assert len([r for r in org_state.db.get_audit_logs("TASK-U0") if r["action"] == "agent_managed"]) == 1
