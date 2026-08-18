"""THR-107 slice 3: GET /runtime/custom-cli/status route tests.

Master-bearer-authed, browser-facing polling route. Never touches token
plaintext — keyed only by intended_profile_name, which the founder's
browser already knows client-side (it's the form input).
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.daemon.state import DaemonState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(
        tmp_path / "direct.db", runtime_root=tmp_path / "daemon"
    )
    from runtime.daemon.routes import auth, direct_connect

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"})
    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})
    return tc, state


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset

    _reset()
    yield
    _reset()


def _write_executable(path, body: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o700)
    return hashlib.sha256(body).hexdigest()


def test_status_before_mint_returns_wrapper_destination_and_no_operation(client):
    tc, state = client

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] is None
    assert body["profile_state"] is None
    assert body["reason"] is None
    assert body["wrapper_destination"].endswith("adapters/status-profile-adapter")


def test_status_after_connect_reports_operation_and_null_profile_state(client, tmp_path):
    tc, state = client
    mint = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "status-cli", "purpose": "adapter", "intended_profile_name": "status-profile",
        "workspace_adapter_id": "codex",
    })
    token = mint.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    connect = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json={"metadata": {}, "manifest": {
            "manifest_version": 2, "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
            "workspace_adapter_id": "codex",
        }},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect.status_code == 201
    operation_id = connect.json()["operation_id"]

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["profile_state"] is None
    assert token not in response.text


def test_status_after_commit_reports_committed(client, tmp_path, monkeypatch):
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    tc, state = client
    mint = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "status-cli", "purpose": "adapter", "intended_profile_name": "status-profile",
        "workspace_adapter_id": "codex",
    })
    token = mint.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    connect = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json={"metadata": {}, "manifest": {
            "manifest_version": 2, "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
            "workspace_adapter_id": "codex",
        }},
        headers={"Authorization": f"Bearer {token}"},
    )
    operation_id = connect.json()["operation_id"]

    def fake_probe(executable, adapter_id, **_kwargs):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.0.0", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    commit = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
    assert commit.status_code == 200

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["profile_state"] == "committed"
    assert body["reason"] is None


def test_status_hides_committed_projection_after_live_profile_is_removed(client, tmp_path, monkeypatch):
    """A historical committed projection is not proof of a live profile."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    tc, state = client
    mint = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "status-cli", "purpose": "adapter", "intended_profile_name": "status-profile",
        "workspace_adapter_id": "codex",
    })
    token = mint.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    connect = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json={"metadata": {}, "manifest": {
            "manifest_version": 2, "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
            "workspace_adapter_id": "codex",
        }},
        headers={"Authorization": f"Bearer {token}"},
    )
    operation_id = connect.json()["operation_id"]

    def fake_probe(executable, adapter_id, **_kwargs):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.0.0", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    assert tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit").status_code == 200
    assert tc.delete("/api/v1/executors/runtime/profiles/status-profile").status_code == 200

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "wrapper_destination": str(authority.wrapper_destination),
        "operation_id": None,
        "profile_state": None,
        "reason": None,
        "attempt_count": 1,
        "retry_eligible": False,
        "expires_at": authority.expires_at,
    }


def test_status_after_failed_projection_reports_reason(client, tmp_path):
    tc, state = client
    mint = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "status-cli", "purpose": "adapter", "intended_profile_name": "status-profile",
        "workspace_adapter_id": "codex",
    })
    token = mint.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    connect = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json={"metadata": {}, "manifest": {
            "manifest_version": 2, "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
            "workspace_adapter_id": "codex",
        }},
        headers={"Authorization": f"Bearer {token}"},
    )
    operation_id = connect.json()["operation_id"]
    assert state.direct_connect_authority_store.plan_projection(operation_id)
    assert state.direct_connect_authority_store.mark_failed(operation_id, "conformance probe failed")

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "wrapper_destination": str(authority.wrapper_destination),
        "operation_id": operation_id,
        "profile_state": "failed",
        "reason": "conformance probe failed",
        "attempt_count": 1,
        "retry_eligible": False,
        "expires_at": authority.expires_at,
    }


def test_status_reports_live_retry_connection_without_rewriting_failed_projection(client, tmp_path, monkeypatch):
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    tc, state = client
    mint = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "status-cli", "purpose": "adapter", "intended_profile_name": "status-profile",
        "workspace_adapter_id": "codex",
    })
    token = mint.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    connect = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json={"metadata": {}, "manifest": {
            "manifest_version": 2, "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
            "workspace_adapter_id": "codex",
        }}, headers={"Authorization": f"Bearer {token}"},
    )
    operation_id = connect.json()["operation_id"]
    assert state.direct_connect_authority_store.plan_projection(operation_id)
    assert state.direct_connect_authority_store.mark_failed(operation_id, "original failure")

    def fake_probe(_executable, adapter_id, **_kwargs):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.0.0", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    assert tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry").status_code == 200

    response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "status-profile"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "wrapper_destination": str(authority.wrapper_destination),
        "operation_id": operation_id,
        "profile_state": "committed",
        "reason": None,
        "historical_projection_state": "failed",
        "historical_projection_reason": "original failure",
        "retry_state": "succeeded",
        "attempt_count": 1,
        "retry_eligible": False,
        "expires_at": authority.expires_at,
    }
    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None and projection.state == "failed" and projection.reason == "original failure"


def test_status_requires_master_bearer(client):
    tc, state = client

    response = tc.get(
        "/api/v1/runtime/custom-cli/status",
        params={"intended_profile_name": "status-profile"},
        headers={"Authorization": "Bearer hrreg_not-the-master-bearer"},
    )

    assert response.status_code == 401
