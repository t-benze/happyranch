from __future__ import annotations

import json as _json

import pytest

from src.orchestrator._paths import OrgPaths
from src.orchestrator.agent_def import AgentDef, render_agent_text
from tests.daemon.conftest import open_talk_for


def _seed_workspace(org_state, name: str, *, with_dir: bool = True) -> None:
    """Seed an active agent file under <org-root>/org/agents/<name>.md and
    (optionally) a workspace dir.

    Mirrors the production layout the dispatch route now consults via
    prompt_loader.load_agent. The file's `team` field is a stub — dispatch
    routing reads team membership from TeamsRegistry (seeded by conftest's
    teams.yaml), not from the agent file.
    """
    paths = OrgPaths(root=org_state.root)
    if with_dir:
        (paths.workspaces_dir / name).mkdir(parents=True, exist_ok=True)
    agent = AgentDef(
        name=name,
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt="x\n",
        description=name,
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def test_worker_self_dispatch_happy_path(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "Add a /healthz route to the daemon"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"].startswith("TASK-")
    assert body["team"] == "engineering"
    assert body["assigned_agent"] == "dev_agent"
    assert body["dispatched_from_talk_id"] == talk_id

    # Persistence verified.
    task = state.db.get_task(body["task_id"])
    assert task is not None
    assert task.brief == "Add a /healthz route to the daemon"
    assert task.team == "engineering"
    assert task.assigned_agent == "dev_agent"
    assert task.parent_task_id is None
    assert task.dispatched_from_talk_id == talk_id

    # Task landed in the system — status is one of the legal lifecycle values.
    # We don't assert against the asyncio.Queue directly because a worker may
    # have already drained it, which would cause timing flakiness here.
    assert task.status.value in ("pending", "in_progress", "completed", "failed", "blocked")

    # Audit row written, with the expected payload contents.
    rows = [
        dict(r)
        for r in state.db._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? AND action = 'task_dispatched'",
            (body["task_id"],),
        ).fetchall()
    ]
    assert len(rows) == 1
    payload = _json.loads(rows[0]["payload"])
    assert payload["dispatcher_role"] == "worker"
    assert payload["dispatcher_agent"] == "dev_agent"
    assert payload["effective_target"] == "dev_agent"
    assert payload["team"] == "engineering"
    assert payload["talk_id"] == talk_id


# --- Request validation ---


def test_dispatch_empty_team_rejected(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "team": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_team"


def test_dispatch_empty_target_agent_rejected(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_target_agent"


# --- Talk lifecycle errors ---


def test_dispatch_unknown_talk_returns_404(client_with_runtime):
    client, _ = client_with_runtime
    r = client.post(
        "/api/v1/orgs/alpha/talks/TALK-999/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_dispatch_closed_talk_returns_400(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    talk_id = open_talk_for(client, "dev_agent")
    # Close the talk via the abandon endpoint.
    ar = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/abandon",
        json={"reason": "test"},
    )
    assert ar.status_code == 200, ar.text
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"
    assert r.json()["detail"]["status"] == "abandoned"


# --- Brief validation ---


@pytest.mark.parametrize("bad_brief", ["", "   ", "\t\n"])
def test_dispatch_empty_brief_rejected(client_with_runtime, bad_brief):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": bad_brief},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_brief"


# --- Team/role enforcement ---


def test_dispatch_dispatcher_team_unknown(client_with_runtime):
    client, state = client_with_runtime
    # Orphan workspace + enrollment so the unknown_agent check would pass.
    _seed_workspace(state, "orphan_agent")

    talk_id = open_talk_for(client, "orphan_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "anything"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "dispatcher_team_unknown"


def test_dispatch_cross_team_forbidden(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "team": "content"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_team_override_forbidden"
    assert detail["dispatcher_team"] == "engineering"
    assert detail["requested_team"] == "content"


def test_dispatch_worker_must_self_dispatch(client_with_runtime):
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    # Add a second registered worker on the engineering team.
    _seed_workspace(state, "qa_engineer")

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "qa_engineer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_must_be_self"
    assert detail["dispatcher"] == "dev_agent"
    assert detail["requested_target"] == "qa_engineer"


# --- Manager dispatch ---


def test_manager_cannot_dispatch_to_team_worker(client_with_runtime):
    """Manager exemption removed: managers may only self-dispatch from a talk.

    Replaces the prior happy-path test; the THR-010 founder diagnosis
    (2026-05-28) made this rejection the intended behavior. Cross-agent work
    routes via `grassland threads compose`, not via dispatch.
    """
    client, state = client_with_runtime
    _seed_workspace(state, "dev_agent")
    _seed_workspace(state, "engineering_head")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "implement X", "target_agent": "dev_agent"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_must_be_self"
    assert detail["dispatcher"] == "engineering_head"
    assert detail["requested_target"] == "dev_agent"
    assert "compose" in detail["hint"].lower()


def test_manager_cannot_dispatch_cross_team(client_with_runtime):
    """Manager dispatching to an out-of-team agent is rejected by the unified
    self-only rule (not the old `target_not_in_team` branch, which was removed
    as dead code under the new rule)."""
    client, state = client_with_runtime
    _seed_workspace(state, "engineering_head")
    _seed_workspace(state, "content_writer")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "content_writer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "talk_dispatch_must_be_self"


def test_manager_self_dispatch_from_talk_succeeds(client_with_runtime):
    """Manager dispatching with target omitted (defaults to self) is allowed."""
    client, state = client_with_runtime
    _seed_workspace(state, "engineering_head")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "drive phase X"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "engineering_head"
    assert body["team"] == "engineering"


# --- Target resolution ---


def test_dispatch_unknown_agent_when_workspace_missing(client_with_runtime):
    client, state = client_with_runtime
    # Agent has an agent file (so team resolution succeeds) but no workspace dir.
    # Under the self-only rule the dispatcher IS the effective target, so the
    # workspace-missing check is hit on a self-dispatch.
    _seed_workspace(state, "dev_agent", with_dir=False)
    # No workspace dir created on disk.

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/orgs/alpha/talks/{talk_id}/dispatch",
        json={"brief": "x"},
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_agent"
    assert detail["agent"] == "dev_agent"
