"""Slice-A direct ingress remains a receipt-only, no-process boundary."""
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


def _mint(client: TestClient) -> str:
    response = client.post("/api/v1/auth/registration-token/runtime", json={
        "name": "custom-cli", "purpose": "adapter", "intended_profile_name": "custom-profile",
        "workspace_adapter_id": "codex",
    })
    assert response.status_code == 200
    return response.json()["token"]


def _write_executable(path, body: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o700)
    return hashlib.sha256(body).hexdigest()


def _payload(wrapper_hash: str, child_path) -> dict:
    return {"metadata": {"client": "test"}, "manifest": {
        "manifest_version": 2, "wrapper_sha256": wrapper_hash,
        "upgradeable_children": [{"slot": "cli", "executable": str(child_path), "version_probe_argv": [str(child_path), "--version"]}],
    }}


def test_valid_direct_ingress_writes_exactly_one_nonlaunchable_receipt(client, tmp_path, monkeypatch):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child, b"#!/bin/sh\necho 1\n")
    popen_calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    response = tc.post("/api/v1/runtime/custom-cli/connect", json=_payload(wrapper_hash, child), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 201
    assert response.json()["state"] == "received_nonlaunchable"
    assert token not in response.text
    assert popen_calls == []
    assert state.direct_connect_authority_store.counts() == {
        "direct_connect_operations": 1, "direct_connect_artifacts": 2,
        "direct_connect_receipts": 1, "direct_connect_events": 1,
    }
    replay = tc.post("/api/v1/runtime/custom-cli/connect", json=_payload(wrapper_hash, child), headers={"Authorization": f"Bearer {token}"})
    assert replay.status_code == 401
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 1


def test_bad_manifest_terminalizes_known_token_without_operation(client, tmp_path, monkeypatch):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    popen_calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    response = tc.post("/api/v1/runtime/custom-cli/connect", json={"manifest": {"manifest_version": 2, "wrapper_sha256": wrapper_hash, "upgradeable_children": [], "executable": "/forbidden"}}, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 422
    assert popen_calls == []
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 0
    assert state.direct_connect_authority_store.counts()["direct_connect_receipts"] == 0
    assert state.direct_connect_authority_store.counts()["direct_connect_events"] == 1


@pytest.mark.parametrize("forbidden", ["executable", "wrapper_destination", "profile_name", "adapter_id", "workspace_adapter_id"])
def test_forbidden_authority_selectors_terminalize_without_operation(client, tmp_path, forbidden):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload[forbidden] = "caller-selected"

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 0


def test_secret_metadata_terminalizes_without_operation(client, tmp_path):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload["metadata"] = {"token": token}

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert token not in response.text


def test_noncanonical_persisted_authority_fails_closed(client, tmp_path):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    state.direct_connect_authority_store._conn.execute(
        "UPDATE direct_connect_authorities SET wrapper_destination = ? WHERE token_fingerprint = ?",
        (str(tmp_path / "direct-connect" / "old-wrapper"), authority.token_fingerprint),
    )
    state.direct_connect_authority_store._conn.commit()
    response = tc.post("/api/v1/runtime/custom-cli/connect", content=b"{", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 0
    assert not state.registration_token_store.validate_runtime(token)


def test_known_malformed_json_terminalizes_before_validation(client):
    tc, state = client
    token = _mint(tc)

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        content=b"{",
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
    )

    assert response.status_code == 422
    assert state.direct_connect_authority_store.counts() == {
        "direct_connect_operations": 0,
        "direct_connect_artifacts": 0,
        "direct_connect_receipts": 0,
        "direct_connect_events": 1,
    }
    assert not state.registration_token_store.validate_runtime(token)


def test_token_commit_fault_compensates_nonlaunchable_receipt(client, tmp_path, monkeypatch):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    monkeypatch.setattr(state.registration_token_store, "commit_runtime", lambda _token: False)

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_payload(wrapper_hash, child),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 500
    assert state.direct_connect_authority_store.counts() == {
        "direct_connect_operations": 0,
        "direct_connect_artifacts": 0,
        "direct_connect_receipts": 0,
        "direct_connect_events": 2,
    }
