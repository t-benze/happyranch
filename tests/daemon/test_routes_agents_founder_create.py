from __future__ import annotations

import asyncio
import json
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

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
    from tests.workflows.authority_test_support import ensure_coherent_authority

    client, org = client_with_runtime
    ensure_coherent_authority(org)
    selector = org.workflow_authority.ensure_authority_selector(
        team="engineering",
        publisher="founder-worker-control:selector-initialization",
    )
    before = org.workflow_authority.verify_admission_ready()
    history_before = org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history"
    ).fetchone()[0]
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
    after = org.workflow_authority.verify_admission_ready()
    assert after.generation == before.generation + 1
    published = {
        row["team"]: row["selector"]
        for row in json.loads(after.snapshot_bytes)["active_policy_selectors"]
    }
    assert published["engineering"] == selector.model_dump(mode="json")
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history"
    ).fetchone()[0] == history_before

    # Workspace bootstrapped.
    assert (org.root / "workspaces" / "alpha_worker_1" / "CLAUDE.md").exists()


def test_founder_create_manager_initializes_selector_before_real_launch(
    client_with_runtime, monkeypatch,
) -> None:
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    from tests.workflows.authority_test_support import ensure_coherent_authority

    client, org = client_with_runtime
    before_generation = ensure_coherent_authority(org)
    r = _post(client, _base_manager())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "delta_head", "team": "delta", "role": "manager"}

    # New team registered.
    assert "delta" in org.teams.teams()
    m = org.teams.manager_for_team("delta")
    assert m.name == "delta_head"
    assert m.workers == ()

    store = AuthorityPolicyStore(org.db)
    selector = store.get_authority_selector("delta")
    assert selector is not None and selector.family == "empty"
    ready = org.workflow_authority.verify_admission_ready()
    assert ready.generation == before_generation + 1
    published = {
        row["team"]: row["selector"]
        for row in json.loads(ready.snapshot_bytes)["active_policy_selectors"]
    }
    assert published["delta"] == selector.model_dump(mode="json")
    history_count = org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history"
    ).fetchone()[0]

    # Reach the shared task/thread/dream/wake/schedule policy resolver through
    # the real task launch seam. Only the external executor is replaced.
    org.orchestrator.attach_host_supervisor(None)
    executor = MagicMock()
    executor.run.return_value = ExecutorResult(
        success=True, duration_seconds=1, session_id="provider-session",
    )
    task_id = org.orchestrator.create_task("delta manager launch")
    monkeypatch.setattr(
        org.orchestrator, "_build_session_id", lambda: "sess-delta-manager",
    )
    with patch.object(org.orchestrator, "_build_executor", return_value=executor):
        org.orchestrator._run_agent(task_id, "delta_head", "decide")

    assert executor.run.called
    assert org.workflow_authority.verify_admission_ready() == ready
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history"
    ).fetchone()[0] == history_count


def test_approve_pending_manager_initializes_selector_in_same_generation(
    client_with_runtime,
) -> None:
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    from tests.workflows.authority_test_support import ensure_coherent_authority

    client, org = client_with_runtime
    before_generation = ensure_coherent_authority(org)
    paths = OrgPaths(root=org.root)
    paths.pending_agents_dir.mkdir(parents=True, exist_ok=True)
    active = paths.agents_dir / "engineering_head.md"
    pending = paths.pending_agents_dir / "engineering_head.md"
    active.replace(pending)

    r = client.post("/api/v1/orgs/alpha/agents/engineering_head/approve")
    assert r.status_code == 200, r.text
    selector = AuthorityPolicyStore(org.db).get_authority_selector("engineering")
    assert selector is not None and selector.family == "empty"
    ready = org.workflow_authority.verify_admission_ready()
    assert ready.generation == before_generation + 1
    published = {
        row["team"]: row["selector"]
        for row in json.loads(ready.snapshot_bytes)["active_policy_selectors"]
    }
    assert published["engineering"] == selector.model_dump(mode="json")


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


def test_founder_create_refreshes_bootstrap_inputs_after_clone_winner(
    client_with_runtime, monkeypatch,
) -> None:
    """A real accepted update wins while founder-create cloning is suspended."""
    from runtime.daemon.routes import agents as agents_mod

    _, org = client_with_runtime
    paths = OrgPaths(root=org.root)
    arrived, release = asyncio.Event(), asyncio.Event()
    real_to_thread = agents_mod.asyncio.to_thread

    with patch("runtime.daemon.routes.agents.ContextBuilder") as mock_builder:
        clone = mock_builder.return_value.clone_repo

        async def controlled_to_thread(func, *args, **kwargs):
            if func is clone:
                arrived.set()
                await asyncio.wait_for(release.wait(), timeout=1)
                return True
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(agents_mod.asyncio, "to_thread", controlled_to_thread)

        async def exercise() -> None:
            create = asyncio.create_task(agents_mod.founder_create_agent(
                "alpha", agents_mod.FounderCreateAgentBody(
                    **_base_worker("fresh_founder"), repos={"docs": "https://example.test/docs.git"},
                ), org,
            ))
            await asyncio.wait_for(arrived.wait(), timeout=1)
            org.sessions.set_active("TASK-100", "engineering_head", "sess-eh-test")
            revision = prompt_loader.agent_revision(paths, "fresh_founder")
            assert revision is not None
            assert await agents_mod.manage_agent("alpha", agents_mod.ManageAgentBody(
                action="update", name="fresh_founder", task_id="TASK-100",
                session_id="sess-eh-test", expected_revision=revision,
                system_prompt="winner prompt\n", executor="codex",
                description="winner", repos={"winner": "/winner"},
            ), org) == {"ok": True}
            winning_bytes = (paths.agents_dir / "fresh_founder.md").read_bytes()
            release.set()
            assert await asyncio.wait_for(create, timeout=1) == {
                "name": "fresh_founder", "team": "engineering", "role": "worker",
            }
            assert (paths.agents_dir / "fresh_founder.md").read_bytes() == winning_bytes

        asyncio.run(exercise())
        bootstrap = mock_builder.return_value.ensure_workspace_ready.call_args
        assert bootstrap.args[2] == "winner prompt\n"
        assert bootstrap.kwargs["provider"] == "codex"

    winner = prompt_loader.load_agent(paths, "fresh_founder")
    assert winner is not None and winner.repos == {"winner": "/winner"}
    audits = org.db.get_audit_logs("TASK-100")
    assert len([row for row in audits if row["action"] == "agent_managed"]) == 1


def test_founder_create_refuses_missing_canonical_after_suspended_clone(
    client_with_runtime, monkeypatch,
) -> None:
    """Controlled removal is a negative injection, not a supported writer."""
    from fastapi import HTTPException
    from runtime.daemon.routes import agents as agents_mod

    _, org = client_with_runtime
    paths = OrgPaths(root=org.root)
    arrived, release = asyncio.Event(), asyncio.Event()
    real_to_thread = agents_mod.asyncio.to_thread

    with patch("runtime.daemon.routes.agents.ContextBuilder") as mock_builder:
        clone = mock_builder.return_value.clone_repo

        async def controlled_to_thread(func, *args, **kwargs):
            if func is clone:
                arrived.set()
                await asyncio.wait_for(release.wait(), timeout=1)
                return True
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(agents_mod.asyncio, "to_thread", controlled_to_thread)

        async def exercise() -> None:
            create = asyncio.create_task(agents_mod.founder_create_agent(
                "alpha", agents_mod.FounderCreateAgentBody(
                    **_base_worker("missing_founder"), repos={"docs": "https://example.test/docs.git"},
                ), org,
            ))
            await asyncio.wait_for(arrived.wait(), timeout=1)
            (paths.agents_dir / "missing_founder.md").unlink()
            release.set()
            with pytest.raises(HTTPException) as raised:
                await asyncio.wait_for(create, timeout=1)
            assert raised.value.status_code == 404
            assert raised.value.detail == "agent 'missing_founder' not found"

        asyncio.run(exercise())
        mock_builder.return_value.ensure_workspace_ready.assert_not_called()
        mock_builder.return_value.create_agent_dirs.assert_not_called()

    assert prompt_loader.load_agent(paths, "missing_founder") is None
    assert not org.db.get_audit_logs("founder")


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
