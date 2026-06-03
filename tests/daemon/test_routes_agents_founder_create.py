from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator import prompt_loader


def _post(client, body):
    return client.post("/api/v1/orgs/alpha/agents", json=body)


def _base_worker(name: str = "alpha_worker_1") -> dict:
    return {
        "name": name,
        "role": "worker",
        "team": "engineering",
        "executor": "claude",
        "description": "does some work",
        "system_prompt": "do the work",
    }


def _base_manager(name: str = "delta_head") -> dict:
    return {
        "name": name,
        "role": "manager",
        "new_team": "delta",
        "executor": "claude",
        "description": "owns delta",
        "system_prompt": "manage the delta team",
    }


def test_founder_create_worker_into_existing_team(client_with_runtime) -> None:
    client, org = client_with_runtime
    r = _post(client, _base_worker())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "alpha_worker_1", "team": "engineering", "role": "worker"}

    # File landed in active agents/, NOT in _pending/.
    paths = OrgPaths(root=org.root)
    assert (paths.agents_dir / "alpha_worker_1.md").exists()
    assert not (paths.pending_agents_dir / "alpha_worker_1.md").exists()

    # AgentDef carries founder marker.
    agent_def = prompt_loader.load_agent(paths, "alpha_worker_1")
    assert agent_def is not None
    assert agent_def.enrolled_by == "founder"
    assert agent_def.team == "engineering"
    assert agent_def.role == "worker"

    # teams.yaml updated.
    assert "alpha_worker_1" in org.teams.manager_for_team("engineering").workers

    # Workspace bootstrapped.
    assert (org.root / "workspaces" / "alpha_worker_1" / "CLAUDE.md").exists()


def test_founder_create_manager_creates_new_team(client_with_runtime) -> None:
    client, org = client_with_runtime
    r = _post(client, _base_manager())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "delta_head", "team": "delta", "role": "manager"}

    # New team registered.
    assert "delta" in org.teams.teams()
    m = org.teams.manager_for_team("delta")
    assert m.name == "delta_head"
    assert m.workers == ()


def test_invalid_agent_name_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    bad = _base_worker(name="Has-Dash")
    r = _post(client, bad)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_agent_name"


def test_duplicate_name_returns_409(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker(name="alpha_worker_dup")
    assert _post(client, body).status_code == 200
    r = _post(client, body)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "agent_exists"


def test_role_worker_requires_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    del body["team"]
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_worker_rejects_new_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["new_team"] = "somethingelse"
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_manager_requires_new_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    del body["new_team"]
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_manager_rejects_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    body["team"] = "engineering"
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_worker_with_unknown_team_returns_404(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["team"] = "nowhere"
    r = _post(client, body)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_team"


def test_manager_with_existing_team_returns_409(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    body["new_team"] = "engineering"  # already exists
    r = _post(client, body)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "team_exists"


def test_missing_description_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["description"] = ""
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_required_field"


def test_missing_system_prompt_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["system_prompt"] = ""
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_required_field"


def test_unsafe_allow_rule_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["allow_rules"] = ["echo hi; rm -rf /"]
    r = _post(client, body)
    # Pydantic field-validator failures surface as 422 with `detail` as a list.
    assert r.status_code == 422


def test_audit_row_written_with_founder_actor(client_with_runtime) -> None:
    client, org = client_with_runtime
    _post(client, _base_worker(name="audit_check_worker"))
    rows = org.db.get_audit_logs(task_id="founder")
    actions = [r["action"] for r in rows]
    assert "agent_managed" in actions
    last = next(r for r in rows if r["action"] == "agent_managed")
    # `log_agent_managed` writes the actor into the audit_log.agent column
    # (see infrastructure/audit_logger.py:534).
    assert last["agent"] == "founder"


import os

import pytest


def test_worker_rollback_on_file_write_failure(client_with_runtime, monkeypatch) -> None:
    """If the agent-file os.replace raises, the founder route must undo the
    add_worker mutation so retry isn't blocked by a phantom roster entry."""
    client, org = client_with_runtime

    # Make os.replace raise to simulate a file-write failure mid-route.
    real_replace = os.replace
    def fail_replace(src, dst, *a, **kw):
        if str(dst).endswith("rollback_worker.md"):
            raise OSError("disk full")
        return real_replace(src, dst, *a, **kw)
    monkeypatch.setattr(os, "replace", fail_replace)

    # TestClient propagates uncaught server exceptions by default; the route
    # re-raises after rolling back, so we expect the OSError to surface here.
    body = _base_worker(name="rollback_worker")
    with pytest.raises(OSError, match="disk full"):
        _post(client, body)

    # Registry rolled back — the worker is NOT listed under engineering.
    assert "rollback_worker" not in org.teams.manager_for_team("engineering").workers

    # And the file is not on disk.
    paths = OrgPaths(root=org.root)
    assert not (paths.agents_dir / "rollback_worker.md").exists()


def test_manager_rollback_on_file_write_failure(client_with_runtime, monkeypatch) -> None:
    """If the manager-branch agent-file os.replace raises, the freshly
    created team must be removed from teams.yaml so retry can succeed."""
    client, org = client_with_runtime

    real_replace = os.replace
    def fail_replace(src, dst, *a, **kw):
        if str(dst).endswith("delta_head.md"):
            raise OSError("disk full")
        return real_replace(src, dst, *a, **kw)
    monkeypatch.setattr(os, "replace", fail_replace)

    body = _base_manager(name="delta_head")
    with pytest.raises(OSError, match="disk full"):
        _post(client, body)

    # The freshly-created team was rolled back.
    assert "delta" not in org.teams.teams()
