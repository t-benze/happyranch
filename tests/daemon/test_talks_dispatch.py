from __future__ import annotations

import json as _json

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
