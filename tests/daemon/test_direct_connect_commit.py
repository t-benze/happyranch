"""THR-107 Slice 1: POST /runtime/custom-cli/{operation_id}/commit route tests.

Projection (which spawns a bounded conformance-probe subprocess against the
wrapper) cannot run inside /connect — that route is pinned to never spawn a
process (see test_direct_connect_ingress.py). This route is the deliberate,
separate, master-bearer-authed follow-up the Settings/onboarding UI calls
immediately after a successful /connect to reach Connected.
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


def _mint_and_connect(tc: TestClient, state, tmp_path) -> str:
    response = tc.post("/api/v1/auth/registration-token/runtime", json={
        "name": "custom-cli", "purpose": "adapter", "intended_profile_name": "custom-profile",
        "workspace_adapter_id": "codex",
    })
    assert response.status_code == 200
    token = response.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = {"metadata": {}, "manifest": {
        "manifest_version": 2, "wrapper_sha256": wrapper_hash,
        "upgradeable_children": [{"slot": "cli", "executable": str(child), "version_probe_argv": [str(child), "--version"]}],
        "workspace_adapter_id": "codex",
    }}
    connect_response = tc.post(
        "/api/v1/runtime/custom-cli/connect", json=payload, headers={"Authorization": f"Bearer {token}"},
    )
    assert connect_response.status_code == 201
    return connect_response.json()["operation_id"]


def _fake_probe(monkeypatch):
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    def fake(executable, adapter_id):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "9.9.9", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake)


def test_commit_after_connect_projects_to_committed(client, tmp_path, monkeypatch):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    _fake_probe(monkeypatch)

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == operation_id
    assert body["profile_state"] == "committed"
    assert body["profile_name"] == "custom-profile"

    from runtime.orchestrator.executor_registry import get_registry
    assert get_registry().get_profile("custom-profile") is not None


def test_commit_requires_master_bearer_not_registration_token(client, tmp_path, monkeypatch):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    _fake_probe(monkeypatch)

    response = tc.post(
        f"/api/v1/runtime/custom-cli/{operation_id}/commit",
        headers={"Authorization": "Bearer hrreg_not-the-master-bearer"},
    )

    assert response.status_code == 401


def test_commit_unknown_operation_returns_404(client):
    tc, state = client

    response = tc.post("/api/v1/runtime/custom-cli/does-not-exist/commit")

    assert response.status_code == 404


def test_commit_is_idempotent_on_retry(client, tmp_path, monkeypatch):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    _fake_probe(monkeypatch)

    first = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
    second = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert first.status_code == second.status_code == 200
    assert first.json()["profile_name"] == second.json()["profile_name"]


def test_commit_probe_failure_returns_failed_profile_state(client, tmp_path, monkeypatch):
    from runtime.orchestrator import custom_adapter_registry

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert response.status_code == 200
    body = response.json()
    assert body["profile_state"] == "failed"
    assert "reason" in body


def test_forget_unknown_operation_returns_404(client):
    tc, _state = client

    response = tc.post("/api/v1/runtime/custom-cli/does-not-exist/forget")

    assert response.status_code == 404


@pytest.mark.parametrize("projection_state", ["planned", "committed"])
def test_forget_refuses_nonfailed_projection(client, tmp_path, projection_state):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    assert state.direct_connect_authority_store.plan_projection(operation_id)
    if projection_state == "committed":
        assert state.direct_connect_authority_store.mark_committed(
            operation_id, adapter_id="custom-profile-adapter", profile_name="custom-profile",
        )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 409
    assert f"projection state is '{projection_state}'" in response.json()["detail"]
    assert state.direct_connect_authority_store.get_projection(operation_id).state == projection_state


def test_forget_failed_projection_removes_records_and_wrapper(client, tmp_path):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "conformance probe failed")
    wrapper = store.get_receipt_artifacts(operation_id).wrapper_path
    assert wrapper.exists()

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 200
    assert response.json() == {"operation_id": operation_id, "status": "forgotten"}
    assert not wrapper.exists()
    for table in (
        "direct_connect_artifacts", "direct_connect_receipts", "direct_connect_operations",
        "direct_connect_projections", "direct_connect_reservations", "direct_connect_authorities",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
