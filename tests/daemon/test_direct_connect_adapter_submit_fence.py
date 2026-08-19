"""THR-160 B2: legacy adapter-submit fence for durable direct-connect authority.

Verifies that ``POST /api/v1/runtime/adapters/submit`` rejects a known direct-
connect authority token non-consumingly in every direct parent state, while
unknown/non-direct adapter tokens and the read-only contract-reference endpoint
keep their existing behavior.
"""
from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.daemon.registration_token import RegistrationTokenStore
from runtime.daemon.state import DaemonState
from runtime.orchestrator.adapter_store import compute_sha256
from runtime.orchestrator.custom_adapter_registry import compute_canonical_adapter_path


@pytest.fixture(autouse=True)
def _allow_testclient_loopback(monkeypatch):
    """Extend loopback allow-lists so TestClient can reach scoped routes."""
    from runtime.daemon import auth as auth_mod
    from runtime.daemon.routes import auth as auth_route, direct_connect

    monkeypatch.setattr(auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(
        auth_mod, "_REGISTRATION_LOCAL_HOSTS", auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"}
    )
    monkeypatch.setattr(
        direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"}
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Full runtime-backed TestClient with real token and direct-connect stores."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(
        tmp_path / "direct.db", runtime_root=tmp_path / "daemon"
    )
    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})
    return tc, state


def _make_adapter_script(tmp_path: Path, profile_name: str) -> Path:
    """Create a conformant adapter script at the canonical path for seq141 submit."""
    _, required_path = compute_canonical_adapter_path(f"{profile_name}-adapter")
    required_path.parent.mkdir(parents=True, exist_ok=True)
    content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{profile_name}-adapter",
        "adapter_name": "{profile_name}-adapter",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "{profile_name}-adapter",
    }},
    "stdout": "ok",
    "stderr": "",
    "stdout_tail": "ok",
    "stderr_tail": "",
    "duration_seconds": 1,
    "invocation_id": inp.get("invocation", {{}}).get("invocation_id", "test-id"),
    "token_total": 0,
    "session_id": "test-session",
}}
print(json.dumps(out))
'''
    required_path.write_text(content)
    required_path.chmod(required_path.stat().st_mode | stat.S_IEXEC)
    return required_path


def _dep_manifest(script: Path) -> dict:
    return {
        "dependency_manifest_version": 1,
        "dependencies": [{"executable": str(script), "sha256": compute_sha256(str(script))}],
    }


def _submit_payload(script: Path, profile_name: str) -> dict:
    return {
        "executable": str(script),
        "version": "1.0.0",
        "capabilities": [],
        "workspace_adapter": "pi",
        **_dep_manifest(script),
    }


def _mint_direct_token(tc: TestClient, profile_name: str) -> str:
    resp = tc.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": profile_name,
            "workspace_adapter_id": "codex",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _mint_non_direct_adapter_token(store: RegistrationTokenStore, profile_name: str) -> str:
    """Mint a plain adapter-purpose token with no corresponding direct authority."""
    token_plaintext, _expires = store.mint_runtime(
        name=profile_name,
        purpose="adapter",
        intended_profile_name=profile_name,
    )
    for step_id in store.DEFAULT_CONFORMANCE_STEPS:
        store.record_step_arrival_runtime(token_plaintext, step_id)
    return token_plaintext


def _assert_direct_fence_response(resp):
    assert resp.status_code == 409, f"expected 409 direct-connect fence, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert "direct-connect" in detail.lower()
    assert "POST /api/v1/runtime/custom-cli/connect" in detail


def test_direct_token_fenced_in_live_state(client, tmp_path):
    """A freshly minted direct authority is rejected by the legacy submit route."""
    tc, state = client
    profile_name = "fence-live"
    token = _mint_direct_token(tc, profile_name)
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    _assert_direct_fence_response(resp)


def test_direct_token_fenced_in_reserved_state(client, tmp_path):
    """A direct authority with only a reservation is still fenced."""
    tc, state = client
    profile_name = "fence-reserved"
    token = _mint_direct_token(tc, profile_name)
    store = state.direct_connect_authority_store
    operation_id = store.reserve(token, now=2)
    assert operation_id is not None
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    _assert_direct_fence_response(resp)


def test_direct_token_fenced_in_consumed_state(client, tmp_path):
    """After generic token consumption the direct authority still fences submit."""
    tc, state = client
    profile_name = "fence-consumed"
    token = _mint_direct_token(tc, profile_name)
    store = state.direct_connect_authority_store
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(b"#!/bin/sh\ncat\n")
    wrapper.chmod(0o700)
    child = tmp_path / "bin" / "child"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    operation_id = store.reserve(token, now=2)
    assert operation_id is not None
    store.receive(
        token,
        operation_id,
        wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        wrapper_facts={},
        children=[{
            "slot": "cli",
            "path": str(child),
            "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
            "facts": {},
        }],
        workspace_adapter_id="codex",
        now=2,
    )
    # Simulate generic-token consumption: the direct authority persists but
    # the registration token is no longer valid for generic adapter submission.
    consumed_record = state.registration_token_store.consume_runtime(token)
    assert consumed_record is not None
    assert state.registration_token_store.validate_runtime(token) is None
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    _assert_direct_fence_response(resp)


def test_direct_token_fenced_after_daemon_restart(client, tmp_path):
    """Reopening the durable direct store from disk retains the fence."""
    tc, state = client
    profile_name = "fence-restart"
    token = _mint_direct_token(tc, profile_name)
    db_path = state.direct_connect_authority_store._conn.execute(
        "PRAGMA database_list"
    ).fetchone()[2]
    runtime_root = state.direct_connect_authority_store._runtime_root
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    _assert_direct_fence_response(resp)


def test_non_direct_adapter_token_submits_normally(client, tmp_path):
    """The fence only blocks known direct authorities; plain adapter tokens work."""
    tc, state = client
    profile_name = "fence-non-direct"
    token = _mint_non_direct_adapter_token(state.registration_token_store, profile_name)
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, f"expected 200 for non-direct token, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["id"] == f"{profile_name}-adapter"
    assert data["status"] == "pending"


def test_direct_token_fenced_after_generic_consumption_and_restart(client, tmp_path):
    """Generic token consumption + restart: direct authority persists and fences submit."""
    tc, state = client
    profile_name = "fence-consume-restart"
    token = _mint_direct_token(tc, profile_name)
    store = state.direct_connect_authority_store
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(b"#!/bin/sh\ncat\n")
    wrapper.chmod(0o700)
    child = tmp_path / "bin" / "child"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    operation_id = store.reserve(token, now=2)
    assert operation_id is not None
    store.receive(
        token,
        operation_id,
        wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        wrapper_facts={},
        children=[{
            "slot": "cli",
            "path": str(child),
            "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
            "facts": {},
        }],
        workspace_adapter_id="codex",
        now=2,
    )
    consumed_record = state.registration_token_store.consume_runtime(token)
    assert consumed_record is not None
    assert state.registration_token_store.validate_runtime(token) is None

    db_path = store._conn.execute("PRAGMA database_list").fetchone()[2]
    runtime_root = store._runtime_root
    store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)
    script = _make_adapter_script(tmp_path, profile_name)

    resp = tc.post(
        "/api/v1/runtime/adapters/submit",
        json=_submit_payload(script, profile_name),
        headers={"Authorization": f"Bearer {token}"},
    )

    _assert_direct_fence_response(resp)
    # The durable direct authority still exposes retry evaluation even though
    # the generic registration token is gone.
    assert state.direct_connect_authority_store.is_retryable(token) is False


def test_direct_token_contract_reference_unchanged(client, tmp_path):
    """The read-only contract-reference endpoint is not fenced."""
    tc, state = client
    profile_name = "fence-contract"
    token = _mint_direct_token(tc, profile_name)

    resp = tc.get(
        "/api/v1/runtime/adapters/contract-reference",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "adapter_input_schema" in data
    assert "adapter_output_schema" in data
