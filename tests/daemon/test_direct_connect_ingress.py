"""Slice-A direct ingress remains a receipt-only, no-process boundary."""
from __future__ import annotations

import hashlib
import logging

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
        "workspace_adapter_id": "codex",
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
    # THR-160: the consumed registration token is invisible, but the accepted
    # candidate identity is durably recorded, so an identical replay is a 409.
    assert replay.status_code == 409
    assert replay.json()["detail"].startswith("duplicate")
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 1


def test_manifest_declared_workspace_adapter_id_wins_over_mint_time_value(client, tmp_path):
    """The wrapper's own /connect declaration is authoritative — the
    founder's mint-time value (an unrelated activation trigger) never
    reaches the durable receipt."""
    tc, state = client
    token = _mint(tc)  # minted with workspace_adapter_id="codex"
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload["manifest"]["workspace_adapter_id"] = "pi"  # CLI declares a different one

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect", json=payload, headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    operation_id = response.json()["operation_id"]
    artifacts = state.direct_connect_authority_store.get_receipt_artifacts(operation_id)
    assert artifacts.workspace_adapter_id == "pi"


def test_manifest_rejects_unknown_workspace_adapter_id(client, tmp_path, caplog):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload["manifest"]["workspace_adapter_id"] = "not-a-real-cli"

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect", json=payload, headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] != "invalid direct manifest"
    assert response.json()["detail"].startswith("invalid direct manifest schema: ")
    assert "ValidationError" in caplog.text
    assert state.direct_connect_authority_store.counts()["direct_connect_operations"] == 0


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


def test_secret_metadata_terminalizes_without_operation(client, tmp_path, caplog):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload["metadata"] = {"token": token}

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert token not in response.text
    assert "ValidationError" in caplog.text
    assert token not in caplog.text


def test_secret_named_extra_manifest_field_never_leaks(client, tmp_path, caplog):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, child)
    payload[token] = "unexpected"

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid direct manifest schema: unexpected or invalid field in manifest"
    assert token not in response.text
    assert token not in caplog.text


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


def test_known_malformed_json_terminalizes_before_validation(client, caplog):
    tc, state = client
    token = _mint(tc)

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect",
            content=b"{",
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] != "invalid direct manifest"
    assert response.json()["detail"].startswith("invalid direct manifest JSON: ")
    assert "JSONDecodeError" in caplog.text
    assert state.direct_connect_authority_store.counts() == {
        "direct_connect_operations": 0,
        "direct_connect_artifacts": 0,
        "direct_connect_receipts": 0,
        "direct_connect_events": 1,
    }
    assert not state.registration_token_store.validate_runtime(token)


def test_wrapper_hash_mismatch_keeps_terse_detail_and_logs(client, tmp_path, caplog):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect",
            json=_payload("0" * 64, child),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid direct manifest"
    assert "ValueError" in caplog.text
    assert "invalid artifact or manifest integrity" in caplog.text


def test_symlink_path_with_minted_token_never_leaks_to_log(client, tmp_path, caplog):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    real_children = tmp_path / "real-children"
    real_children.mkdir()
    secret_symlink = tmp_path / token
    secret_symlink.symlink_to(real_children, target_is_directory=True)
    child = secret_symlink / "child"

    with caplog.at_level(logging.WARNING, logger="runtime.daemon.routes.direct_connect"):
        response = tc.post(
            "/api/v1/runtime/custom-cli/connect",
            json=_payload(wrapper_hash, child),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid direct manifest"
    assert "ValueError" in caplog.text
    assert token not in caplog.text


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
    # THR-160: receipt artifacts are retained after token commit failure so the
    # failure is attributable to the generic token seam, not the candidate identity.
    assert state.direct_connect_authority_store.counts() == {
        "direct_connect_operations": 1,
        "direct_connect_artifacts": 2,
        "direct_connect_receipts": 1,
        "direct_connect_events": 2,
    }


def test_unexpected_admission_fault_returns_generic_and_leaves_no_unauthorized_residue(client, tmp_path, monkeypatch):
    """Arbitrary exceptions at the admission seam reduce to a fixed category.

    The generic ``except Exception`` handler in ``connect()`` must never emit
    candidate-controlled exception text on the HTTP trust boundary. It logs
    server-side, consumes the registration token, terminalizes the known
    authority with ``intake_fault``, and leaves no candidate/operation/receipt/
    artifact/identity rows behind.
    """
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    sentinel = "UNIQUE_SENTINEL_7a3f9e2b"

    original_evaluate_admission = state.direct_connect_authority_store.evaluate_admission

    def _boom(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(state.direct_connect_authority_store, "evaluate_admission", _boom)

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_payload(wrapper_hash, child),
        headers={"Authorization": f"Bearer {token}"},
    )

    # Restore the real admission seam before the positive controls below.
    monkeypatch.setattr(state.direct_connect_authority_store, "evaluate_admission", original_evaluate_admission)

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "direct intake failed"
    assert sentinel.lower() not in response.text.lower()
    assert token not in response.text

    # Generic registration-token lock: consumed, not reserved.
    assert state.registration_token_store.validate_runtime(token) is None
    record = state.registration_token_store.consume_runtime(token)
    assert record is None  # already consumed

    store = state.direct_connect_authority_store
    fingerprint = store.get_for_token(token).token_fingerprint

    # Authorized terminalization residue only: one terminalized reservation and
    # one terminalized event, both carrying the fixed category, not the sentinel.
    reservation = store._conn.execute(
        "SELECT state, reason FROM direct_connect_reservations WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    assert reservation is not None
    assert reservation["state"] == "terminalized"
    assert reservation["reason"] == "intake_fault"
    assert sentinel.lower() not in str(reservation["reason"]).lower()

    event = store._conn.execute(
        "SELECT event_type, detail FROM direct_connect_events WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    assert event is not None
    assert event["event_type"] == "terminalized"
    assert event["detail"] == "intake_fault"
    assert sentinel.lower() not in str(event["detail"]).lower()

    # No candidate/operation/receipt/artifact/identity rows created by the fault.
    assert store.counts() == {
        "direct_connect_operations": 0,
        "direct_connect_artifacts": 0,
        "direct_connect_receipts": 0,
        "direct_connect_events": 1,
    }
    assert store._conn.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert store._conn.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0

    # Parent lifecycle is closed nonretryable.
    parent = store._conn.execute(
        "SELECT state FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    assert parent is not None
    assert parent["state"] == "failed"

    # Normal first-submit control: a fresh token still succeeds end-to-end.
    fresh_token = _mint(tc)
    fresh_authority = store.get_for_token(fresh_token)
    _write_executable(fresh_authority.wrapper_destination)
    fresh_child = tmp_path / "bin" / "fresh-child"
    _write_executable(fresh_child)
    first = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_payload(hashlib.sha256(fresh_authority.wrapper_destination.read_bytes()).hexdigest(), fresh_child),
        headers={"Authorization": f"Bearer {fresh_token}"},
    )
    assert first.status_code == 201
    assert first.json()["state"] == "received_nonlaunchable"

    # Documented expected-error control: identical replay is a non-consuming 409.
    replay = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_payload(hashlib.sha256(fresh_authority.wrapper_destination.read_bytes()).hexdigest(), fresh_child),
        headers={"Authorization": f"Bearer {fresh_token}"},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"].startswith("duplicate")
