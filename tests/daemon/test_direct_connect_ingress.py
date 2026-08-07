"""Production-seam proofs for the nonlaunchable direct-connect boundary."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _allow_testclient(monkeypatch) -> None:
    from runtime.daemon.routes import auth as auth_route
    from runtime.daemon.routes import direct_connect

    monkeypatch.setattr(auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"})


def _mint_direct(client, daemon_state) -> tuple[str, object]:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "direct-cli",
            "purpose": "adapter",
            "intended_profile_name": "direct-cli",
            "workspace_adapter_id": "codex",
        },
    )
    assert response.status_code == 200
    token = response.json()["token"]
    authority = daemon_state.direct_connect_authority_store.get_for_token(token)
    assert authority is not None
    challenge = daemon_state.registration_token_store.get_challenge_runtime(token)
    assert challenge is not None
    for step in challenge.steps:
        step.arrived = True
    return token, authority


def _body(child: str) -> dict:
    return {
        "version": "1.0.0",
        "capabilities": ["token_metering"],
        "dependency_manifest_version": 2,
        "dependencies": [{"slot": "cli", "executable": child, "version_probe_argv": [child, "--version"]}],
    }


def _write_executable(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho ok\n")
    path.chmod(0o700)


def test_direct_ingress_uses_minted_public_target_and_never_projects(client, daemon_state, tmp_path) -> None:
    token, authority = _mint_direct(client, daemon_state)
    assert authority.wrapper_destination == tmp_path / ".happyranch" / "adapters" / "direct-cli-adapter"
    _write_executable(authority.wrapper_destination)
    child = tmp_path / "child-cli"
    _write_executable(child)

    response = client.post(
        "/api/v1/runtime/custom-cli/connect", json=_body(str(child)),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "pre_projection"
    operation = daemon_state.direct_connect_authority_store.get_operation(authority.token_fingerprint)
    assert operation is not None
    assert operation.wrapper_destination == authority.wrapper_destination
    assert operation.workspace_adapter_id == "codex"
    assert operation.state == "pre_projection"
    assert not (tmp_path / ".happyranch" / "executor_profiles.yaml").exists()


@pytest.mark.parametrize("field", ["executable", "wrapper_destination", "path", "profile_name", "adapter_id", "workspace_adapter_id"])
def test_direct_ingress_forbids_authority_selectors(client, daemon_state, tmp_path, field) -> None:
    token, authority = _mint_direct(client, daemon_state)
    _write_executable(authority.wrapper_destination)
    child = tmp_path / "child-cli"
    _write_executable(child)
    body = _body(str(child))
    body[field] = "/attacker/selected" if field in {"executable", "wrapper_destination", "path"} else "attacker"

    response = client.post("/api/v1/runtime/custom-cli/connect", json=body, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 422
    assert daemon_state.direct_connect_authority_store.get_operation(authority.token_fingerprint).state == "terminal"


@pytest.mark.parametrize("manifest_version", [True, 2.0, "2"])
def test_direct_ingress_requires_an_exact_integer_v2_manifest(client, daemon_state, tmp_path, manifest_version) -> None:
    token, authority = _mint_direct(client, daemon_state)
    _write_executable(authority.wrapper_destination)
    child = tmp_path / "child-cli"
    _write_executable(child)
    body = _body(str(child))
    body["dependency_manifest_version"] = manifest_version

    response = client.post(
        "/api/v1/runtime/custom-cli/connect", json=body, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 422
    operation = daemon_state.direct_connect_authority_store.get_operation(authority.token_fingerprint)
    assert operation is not None and operation.state == "terminal"


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "nonexecutable"])
def test_invalid_wrapper_terminalizes_without_operation_commit(client, daemon_state, tmp_path, kind) -> None:
    token, authority = _mint_direct(client, daemon_state)
    child = tmp_path / "child-cli"
    _write_executable(child)
    if kind == "symlink":
        authority.wrapper_destination.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(child, authority.wrapper_destination)
    elif kind == "directory":
        authority.wrapper_destination.mkdir(parents=True)
    elif kind == "nonexecutable":
        authority.wrapper_destination.parent.mkdir(parents=True, exist_ok=True)
        authority.wrapper_destination.write_text("not executable")

    response = client.post("/api/v1/runtime/custom-cli/connect", json=_body(str(child)), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 422
    operation = daemon_state.direct_connect_authority_store.get_operation(authority.token_fingerprint)
    assert operation is not None and operation.state == "terminal"
    assert operation.adapter_id == ""


def test_malformed_known_direct_request_is_terminal_and_cannot_replay(client, daemon_state) -> None:
    token, authority = _mint_direct(client, daemon_state)
    response = client.post(
        "/api/v1/runtime/custom-cli/connect", content=b"{broken", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert daemon_state.direct_connect_authority_store.get_operation(authority.token_fingerprint).state == "terminal"
    assert daemon_state.registration_token_store.validate_runtime(token) is None


def test_direct_connect_openapi_excludes_wrapper_and_authority_selectors(app) -> None:
    schema = app.openapi()["paths"]["/api/v1/runtime/custom-cli/connect"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    properties = schema["properties"]
    for forbidden in ("executable", "wrapper_destination", "path", "profile_name", "adapter_id", "workspace_adapter_id"):
        assert forbidden not in properties
