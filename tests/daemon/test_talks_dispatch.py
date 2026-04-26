from __future__ import annotations

import json as _json

import pytest

from tests.daemon.conftest import open_talk_for


def _seed_dev_agent_workspace(daemon_state):
    """Create just enough on disk to satisfy the unknown_agent check.

    The dispatch endpoint requires the target's workspace dir to exist;
    creating an empty dir is sufficient for unit-level coverage.
    """
    ws = daemon_state.runtime.workspaces_dir / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    # Approved enrollment row so the registered-agent check passes.
    daemon_state.db.insert_enrollment(
        name="dev_agent",
        description="dev",
        system_prompt="You are dev",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    daemon_state.db.update_enrollment_status("dev_agent", "approved")


def _seed_eh_workspace(daemon_state):
    """Seed an engineering_head (manager) workspace + approved enrollment."""
    ws = daemon_state.runtime.workspaces_dir / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
    daemon_state.db.insert_enrollment(
        name="engineering_head",
        description="eh",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    daemon_state.db.update_enrollment_status("engineering_head", "approved")


def test_worker_self_dispatch_happy_path(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
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


def test_dispatch_empty_team_rejected(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "team": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_team"


def test_dispatch_empty_target_agent_rejected(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_target_agent"


# Plan-Task 4: talk-lifecycle errors


def test_dispatch_unknown_talk_returns_404(client_with_runtime):
    client, _ = client_with_runtime
    r = client.post(
        "/api/v1/talks/TALK-999/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_dispatch_closed_talk_returns_400(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    # Close the talk via the abandon endpoint.
    client.post(
        f"/api/v1/talks/{talk_id}/abandon",
        json={"reason": "test"},
    )
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"
    assert r.json()["detail"]["status"] == "abandoned"


# Plan-Task 5: empty_brief


@pytest.mark.parametrize("bad_brief", ["", "   ", "\t\n"])
def test_dispatch_empty_brief_rejected(client_with_runtime, bad_brief):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": bad_brief},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_brief"


# Plan-Task 6: dispatcher_team_unknown (orphan agent)


def test_dispatch_dispatcher_team_unknown(client_with_runtime):
    client, state = client_with_runtime
    # Orphan workspace + enrollment so the unknown_agent check would pass.
    ws = state.runtime.workspaces_dir / "orphan_agent"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="orphan_agent",
        description="orphan",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    state.db.update_enrollment_status("orphan_agent", "approved")

    talk_id = open_talk_for(client, "orphan_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "anything"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "dispatcher_team_unknown"


# Plan-Task 7: cross_team_dispatch_forbidden


def test_dispatch_cross_team_forbidden(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "team": "content"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "cross_team_dispatch_forbidden"
    assert detail["dispatcher_team"] == "engineering"
    assert detail["requested_team"] == "content"


# Plan-Task 8: worker_must_self_dispatch


def test_dispatch_worker_must_self_dispatch(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    # Add a second registered worker on the engineering team.
    ws = state.runtime.workspaces_dir / "qa_engineer"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="qa_engineer",
        description="qa",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    state.db.update_enrollment_status("qa_engineer", "approved")

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "qa_engineer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "worker_must_self_dispatch"
    assert detail["dispatcher"] == "dev_agent"
    assert detail["requested_target"] == "qa_engineer"


# Plan-Task 9: manager intra-team (success) and out-of-team (failure)


def test_manager_dispatches_to_team_worker(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    _seed_eh_workspace(state)

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "implement X", "target_agent": "dev_agent"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "dev_agent"
    assert body["team"] == "engineering"

    rows = [
        dict(r)
        for r in state.db._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? AND action = 'task_dispatched'",
            (body["task_id"],),
        ).fetchall()
    ]
    assert len(rows) == 1
    payload = _json.loads(rows[0]["payload"])
    assert payload["dispatcher_role"] == "manager"
    assert payload["dispatcher_agent"] == "engineering_head"


def test_manager_target_not_in_team(client_with_runtime):
    client, state = client_with_runtime
    _seed_eh_workspace(state)
    # Add an agent on the content team.
    ws = state.runtime.workspaces_dir / "content_writer"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="content_writer",
        description="cw",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    state.db.update_enrollment_status("content_writer", "approved")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "content_writer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "target_not_in_team"
    assert detail["team"] == "engineering"
    assert detail["requested_target"] == "content_writer"


# Plan-Task 10: unknown_agent (workspace missing)


def test_dispatch_unknown_agent_when_workspace_missing(client_with_runtime):
    client, state = client_with_runtime
    # Manager talk so role check passes; target agent has enrollment but no workspace.
    _seed_eh_workspace(state)
    state.db.insert_enrollment(
        name="dev_agent",
        description="dev",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    state.db.update_enrollment_status("dev_agent", "approved")
    # No workspace dir created on disk.

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "dev_agent"},
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_agent"
    assert detail["agent"] == "dev_agent"
