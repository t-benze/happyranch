"""THR-107 Slice 1: POST /runtime/custom-cli/{operation_id}/commit route tests.

Projection (which spawns a bounded conformance-probe subprocess against the
wrapper) cannot run inside /connect — that route is pinned to never spawn a
process (see test_direct_connect_ingress.py). This route is the deliberate,
separate, master-bearer-authed follow-up the Settings/onboarding UI calls
immediately after a successful /connect to reach Connected.
"""
from __future__ import annotations

import hashlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_store import (
    DirectConnectAuthorityStore,
    DirectConnectRetryStaleCandidateError,
    fingerprint_registration_token,
)
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

    def fake(executable, adapter_id, **_kwargs):
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


def test_commit_replaces_orphaned_adapter_with_fresh_probe_hash(client, tmp_path, monkeypatch):
    """A reconnect must not bind against an old deterministic adapter id."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    adapter_id = custom_adapter_registry.generate_adapter_id("custom-profile-adapter")
    save_adapter(AdapterEntry(
        id=adapter_id,
        name="custom-profile",
        executable="/tmp/obsolete-wrapper",
        executable_hash="stale-hash",
        version="0.0.1",
        workspace_adapter="codex",
        status="approved",
        registered_at="2026-08-12T00:00:00Z",
        registered_by="direct-connect",
        approved_at="2026-08-12T00:00:00Z",
        approved_by="direct-connect",
        intended_profile_name="custom-profile",
    ))
    _fake_probe(monkeypatch)

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert response.status_code == 200, response.json()
    entry = get_adapter(adapter_id)
    assert entry is not None
    assert entry.executable_hash == state.direct_connect_authority_store.get_receipt_artifacts(
        operation_id
    ).wrapper_sha256
    assert entry.executable_hash != "stale-hash"


def test_profile_removal_preserves_adapter_when_direct_connect_bind_wins_race(
    client, tmp_path, monkeypatch,
):
    """A direct-connect bind that wins the store lock cannot lose its adapter."""
    from runtime.daemon import direct_connect_projection
    from runtime.daemon.routes import adapters as adapters_routes
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter
    from runtime.orchestrator.runtime_executor_store import save_runtime_profile

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    artifacts = state.direct_connect_authority_store.get_receipt_artifacts(operation_id)
    assert artifacts is not None
    adapter_id = custom_adapter_registry.generate_adapter_id("custom-profile-adapter")
    original_adapter = AdapterEntry(
        id=adapter_id,
        name="custom-profile",
        executable=str(artifacts.wrapper_path),
        executable_hash=artifacts.wrapper_sha256,
        version="1.0.0",
        workspace_adapter="codex",
        status="approved",
        registered_at="2026-08-13T00:00:00Z",
        registered_by="direct-connect",
        approved_at="2026-08-13T00:00:00Z",
        approved_by="direct-connect",
        intended_profile_name="custom-profile",
    )
    save_adapter(original_adapter)
    save_runtime_profile("custom-profile", {
        "command": "echo",
        "argv_template": ["echo", "{prompt}"],
        "adapter": "codex",
        "command_adapter_id": f"custom-adapter:{adapter_id}",
    })
    _fake_probe(monkeypatch)

    rendezvous = threading.Barrier(2)
    projection_binding_started = threading.Event()
    original_bind = custom_adapter_registry._perform_adapter_profile_binding
    original_cleanup = adapters_routes.remove_unbound_direct_connect_adapter

    def _bind_profile(**kwargs):
        projection_binding_started.set()
        return original_bind(**kwargs)

    def _cleanup_after_projection_starts(adapter_id: str):
        rendezvous.wait(timeout=5)
        assert projection_binding_started.wait(timeout=5)
        return original_cleanup(adapter_id)

    monkeypatch.setattr(custom_adapter_registry, "_perform_adapter_profile_binding", _bind_profile)
    monkeypatch.setattr(
        adapters_routes, "remove_unbound_direct_connect_adapter", _cleanup_after_projection_starts,
    )

    delete_response: list = []
    projection_outcome: list = []

    def _remove_profile():
        delete_response.append(tc.delete("/api/v1/executors/runtime/profiles/custom-profile"))

    def _commit_projection():
        rendezvous.wait(timeout=5)
        projection_outcome.append(
            direct_connect_projection.project(state.direct_connect_authority_store, operation_id)
        )

    delete_thread = threading.Thread(target=_remove_profile)
    projection_thread = threading.Thread(target=_commit_projection)
    delete_thread.start()
    projection_thread.start()
    delete_thread.join(timeout=10)
    projection_thread.join(timeout=10)

    assert not delete_thread.is_alive()
    assert not projection_thread.is_alive()
    assert delete_response[0].status_code == 200, delete_response[0].json()
    assert projection_outcome[0].state == "committed"
    surviving_adapter = get_adapter(adapter_id)
    assert surviving_adapter is not None
    assert surviving_adapter.to_dict() == original_adapter.to_dict()


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


def test_concurrent_browser_commit_reconciles_durable_planned_winner(client, tmp_path, monkeypatch):
    """A second authenticated commit observes, rather than fails, a live winner."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    probe_started = threading.Event()
    release_probe = threading.Event()
    probe_calls = 0
    owner_response = []

    def gated_probe(_executable, adapter_id, **_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        assert release_probe.wait(timeout=4)
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "9.9.9", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", gated_probe)
    owner = threading.Thread(
        target=lambda: owner_response.append(tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")),
    )
    owner.start()
    assert probe_started.wait(timeout=2)
    time.sleep(1.1)  # Exceeds the prior 50 * 20ms concurrent-winner budget.
    try:
        loser = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
        assert loser.status_code == 200
        assert loser.json() == {
            "operation_id": operation_id, "profile_state": "planned", "reason": None,
        }
        assert "concurrent projection did not reach a terminal state in time" not in loser.text
        assert probe_calls == 1
    finally:
        release_probe.set()
        owner.join(timeout=4)

    assert not owner.is_alive()
    assert owner_response[0].json()["profile_state"] == "committed"
    reconciled = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "custom-profile"},
    )
    assert reconciled.json()["profile_state"] == "committed"
    events = state.direct_connect_authority_store._conn.execute(
        "SELECT event_type FROM direct_connect_events WHERE operation_id = ?", (operation_id,),
    ).fetchall()
    assert [event["event_type"] for event in events] == ["received_nonlaunchable", "committed"]


def test_commit_probe_failure_returns_failed_profile_state(client, tmp_path, monkeypatch):
    from runtime.orchestrator import custom_adapter_registry

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert response.status_code == 200
    body = response.json()
    assert body["profile_state"] == "failed"
    assert "reason" in body


def _failed_operation(tc: TestClient, state, tmp_path) -> str:
    operation_id = _mint_and_connect(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "original conformance failure")
    return operation_id


def _drive_to_terminal_failure(
    tc: TestClient, state: DaemonState, operation_id: str, monkeypatch
) -> None:
    """Use the existing /commit seam to drive a receipt to terminal failed."""
    from runtime.orchestrator import custom_adapter_registry

    monkeypatch.setattr(
        custom_adapter_registry,
        "run_conformance_probe",
        lambda _executable, _name, **_kwargs: (_ for _ in ()).throw(
            ValueError("probe failed")
        ),
    )
    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
    assert response.status_code == 200, response.text
    assert response.json()["profile_state"] == "failed"


def _mint_connect_and_fail(
    tc: TestClient,
    state: DaemonState,
    tmp_path: Path,
    monkeypatch,
    *,
    wrapper_body: bytes = b"#!/bin/sh\ncat\n",
    child_name: str = "child",
) -> tuple[str, str]:
    """Mint a direct token, connect one candidate, fail its projection, return (token, operation_id)."""
    response = tc.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": "codex",
        },
    )
    assert response.status_code == 200
    token = response.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, wrapper_body)
    child = tmp_path / "bin" / child_name
    _write_executable(child)
    payload = {
        "metadata": {},
        "manifest": {
            "manifest_version": 2,
            "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [
                {
                    "slot": "cli",
                    "executable": str(child),
                    "version_probe_argv": [str(child), "--version"],
                }
            ],
            "workspace_adapter_id": "codex",
        },
    }
    connect_response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect_response.status_code == 201
    operation_id = connect_response.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_id, monkeypatch)
    return token, operation_id


def test_retry_validates_failed_snapshot_without_rewriting_historical_projection(
    client, tmp_path, monkeypatch,
):
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    _fake_probe(monkeypatch)

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert response.status_code == 200, response.json()
    assert response.json()["profile_state"] == "committed"
    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None
    assert projection.state == "failed"
    assert projection.reason == "original conformance failure"
    event_types = [row[0] for row in state.direct_connect_authority_store._conn.execute(
        "SELECT event_type FROM direct_connect_events WHERE operation_id = ?", (operation_id,)
    )]
    assert "projection_failed" in event_types
    assert "retry_validation_succeeded" in event_types
    assert get_registry().get_profile("custom-profile") is not None


def test_retry_probe_failure_keeps_original_failure_and_leaves_no_binding_residue(
    client, tmp_path, monkeypatch,
):
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert response.status_code == 200
    assert response.json() == {
        "operation_id": operation_id,
        "profile_state": "failed",
        "reason": "conformance_probe_failed",
    }
    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None and projection.reason == "original conformance failure"
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    details = state.direct_connect_authority_store._conn.execute(
        "SELECT detail FROM direct_connect_events WHERE operation_id = ? AND event_type = 'retry_validation_failed'",
        (operation_id,),
    ).fetchall()
    assert [row[0] for row in details] == ["conformance_probe_failed"]


def test_retry_binding_failure_compensates_adapter_and_preserves_original_failure(
    client, tmp_path, monkeypatch,
):
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    _fake_probe(monkeypatch)
    monkeypatch.setattr(
        custom_adapter_registry, "_perform_adapter_profile_binding",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("binding failed")),
    )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "profile_binding_failed"
    assert "binding failed" not in str(body).lower()
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None and projection.reason == "original conformance failure"

    # Sentinel check: the raw exception text must not leak into any persisted
    # status or history field; every surface shows only the fixed category.
    store = state.direct_connect_authority_store
    retry_reason = store._conn.execute(
        "SELECT reason FROM direct_connect_retry_attempts WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert retry_reason is not None and retry_reason[0] == "profile_binding_failed"
    event_details = store._conn.execute(
        "SELECT detail FROM direct_connect_events WHERE operation_id = ? AND event_type = 'retry_validation_failed'",
        (operation_id,),
    ).fetchall()
    assert [row[0] for row in event_details] == ["profile_binding_failed"]
    for row in store._conn.execute(
        "SELECT detail FROM direct_connect_events WHERE operation_id = ? OR token_fingerprint = (SELECT token_fingerprint FROM direct_connect_operations WHERE operation_id = ?)",
        (operation_id, operation_id),
    ).fetchall():
        assert "binding failed" not in str(row[0]).lower()


def test_initial_commit_binding_failure_normalizes_to_category_and_never_leaks_sentinel(
    client, tmp_path, monkeypatch,
):
    """Initial projection profile-binding exceptions emit only the fixed category.

    Every durable surface (HTTP commit response, status current view,
    projection row, event trail) must show ``profile_binding_failed`` and never
    the unique sentinel exception text, candidate output, or path material.
    """
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    _fake_probe(monkeypatch)
    sentinel = "SENTINEL-INITIAL-BIND-LEAK-TASK-5249"
    monkeypatch.setattr(
        custom_adapter_registry, "_perform_adapter_profile_binding",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")

    assert response.status_code == 200
    body = response.json()
    assert body["profile_state"] == "failed"
    assert body["reason"] == "profile_binding_failed"
    assert sentinel.lower() not in str(body).lower()

    # Status route (current) also redacts.
    status = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "custom-profile"},
    )
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["state"] == "failed_nonretryable"
    assert status_body["reason"] == "profile_binding_failed"
    assert status_body["profile_state"] == "failed"
    assert sentinel.lower() not in str(status_body).lower()

    # No live adapter/profile/registry residue.
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None

    # Durable projection and event rows contain only the category.
    store = state.direct_connect_authority_store
    projection = store.get_projection(operation_id)
    assert projection is not None
    assert projection.state == "failed"
    assert projection.reason == "profile_binding_failed"
    assert sentinel.lower() not in str(projection.reason).lower()

    event_details = store._conn.execute(
        "SELECT detail FROM direct_connect_events WHERE operation_id = ? AND event_type = 'projection_failed'",
        (operation_id,),
    ).fetchall()
    assert [row[0] for row in event_details] == ["profile_binding_failed"]
    for row in store._conn.execute(
        "SELECT detail FROM direct_connect_events WHERE operation_id = ? OR token_fingerprint = (SELECT token_fingerprint FROM direct_connect_operations WHERE operation_id = ?)",
        (operation_id, operation_id),
    ).fetchall():
        assert sentinel.lower() not in str(row[0]).lower()


@pytest.mark.parametrize("artifact", ["wrapper", "child"])
def test_retry_rejects_tampered_persisted_snapshot_before_probe(client, tmp_path, monkeypatch, artifact):
    from runtime.orchestrator import custom_adapter_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    artifacts = state.direct_connect_authority_store.get_receipt_artifacts(operation_id)
    assert artifacts is not None
    path = artifacts.wrapper_path if artifact == "wrapper" else artifacts.children[0]["executable"]
    with open(path, "ab") as stream:
        stream.write(b"# tampered\n")
    calls = 0

    def fake_probe(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("tampered artifacts must not be invoked")

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert response.status_code == 200
    assert response.json()["reason"] == "artifact_validation_failed"
    assert calls == 0
    assert state.direct_connect_authority_store.get_projection(operation_id).reason == "original conformance failure"


@pytest.mark.parametrize("projection_state", ["planned", "committed"])
def test_retry_refuses_nonfailed_projection(client, tmp_path, projection_state):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    assert state.direct_connect_authority_store.plan_projection(operation_id)
    if projection_state == "committed":
        assert state.direct_connect_authority_store.mark_committed(
            operation_id, adapter_id="custom-profile-adapter", profile_name="custom-profile",
        )

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert response.status_code == 409
    assert f"projection state is '{projection_state}'" in response.json()["detail"]


def test_retry_missing_operation_returns_404(client):
    tc, _state = client

    assert tc.post("/api/v1/runtime/custom-cli/does-not-exist/retry").status_code == 404


def test_retry_requires_master_bearer_not_registration_token(client, tmp_path, monkeypatch):
    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    _fake_probe(monkeypatch)

    response = tc.post(
        f"/api/v1/runtime/custom-cli/{operation_id}/retry",
        headers={"Authorization": "Bearer hrreg_not-the-master-bearer"},
    )

    assert response.status_code == 401


def test_forget_unknown_operation_returns_404(client):
    tc, _state = client

    response = tc.post("/api/v1/runtime/custom-cli/does-not-exist/forget")

    assert response.status_code == 404


def test_forget_route_module_contract_retains_every_present_wrapper():
    """Keep the route's normative cleanup claim aligned with the store contract."""
    from runtime.daemon.routes import direct_connect_commit

    contract = " ".join((direct_connect_commit.__doc__ or "").replace("`", "").split())

    assert "Master-bearer-authed cleanup route for a terminal FAILED custom-CLI operation" in contract
    assert "deletes eligible failed authority, receipt, and projection rows" not in contract
    assert "deletes derived state for one terminal failed operation" in contract
    assert "retains every present derived wrapper" not in contract
    assert "retains the immutable parent authority, accepted candidate record, canonical identity history, receipt, operation row, and event trail" in contract
    assert "only safe derived artifacts/projections and any retry-attempt row are removed" in contract
    assert "already_absent, preserved_changed, or preserved_unsafe" in contract
    assert "missing, planned, or committed" in contract
    assert "retry validation is running or has succeeded" in contract
    assert "removes its failed authority records and the derived wrapper file" not in contract
    assert "deletes every derived wrapper" not in contract


def test_forget_requires_master_bearer_not_registration_token(client, tmp_path):
    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)

    response = tc.post(
        f"/api/v1/runtime/custom-cli/{operation_id}/forget",
        headers={"Authorization": "Bearer hrreg_not-the-master-bearer"},
    )

    assert response.status_code == 401
    assert state.direct_connect_authority_store.get_projection(operation_id).state == "failed"


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


def test_forget_failed_projection_removes_records_but_preserves_matching_wrapper(client, tmp_path):
    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "conformance probe failed")
    wrapper = store.get_receipt_artifacts(operation_id).wrapper_path
    assert wrapper.exists()

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 200
    assert response.json() == {
        "operation_id": operation_id,
        "status": "forgotten",
        "wrapper_status": "preserved_unsafe",
    }
    assert wrapper.exists()
    # THR-160: immutable parent/candidate/identity/receipt/audit evidence is
    # retained; only safe derived artifacts/projections/retry_attempts are removed.
    for table in ("direct_connect_artifacts", "direct_connect_projections"):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    for table in (
        "direct_connect_authorities", "direct_connect_parent_lifecycles",
        "direct_connect_candidates", "direct_connect_identity_history",
        "direct_connect_receipts", "direct_connect_operations",
        "direct_connect_reservations",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1
    assert store._conn.execute(
        "SELECT COUNT(*) FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
    ).fetchone()[0] == 0


def test_forget_never_unlinks_a_successor_swapped_at_the_delete_point(client, tmp_path, monkeypatch):
    """No present wrapper is deleted after a post-hash canonical-path swap."""
    from runtime.daemon import direct_connect_store as store_module

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "conformance probe failed")
    wrapper = store.get_receipt_artifacts(operation_id).wrapper_path
    successor = b"#!/bin/sh\necho successor\n"
    successor_path = wrapper.with_name(f"{wrapper.name}.successor")
    successor_path.write_bytes(successor)
    successor_at_delete_path = wrapper.with_name(f"{wrapper.name}.successor-at-delete")
    successor_at_delete_path.write_bytes(b"#!/bin/sh\necho successor-at-delete\n")
    original_file_digest = store_module.hashlib.file_digest
    original_unlink = type(wrapper).unlink
    unlink_calls = []

    def swap_after_receipt_hash(file_obj, algorithm):
        digest = original_file_digest(file_obj, algorithm)
        successor_path.replace(wrapper)
        return digest

    def replace_then_unlink(path, *, missing_ok=False):
        """Reproduce the old final pathname deletion exactly if it is attempted."""
        if path == wrapper:
            unlink_calls.append(path)
            successor_at_delete_path.replace(path)
            return original_unlink(path, missing_ok=missing_ok)
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(store_module.hashlib, "file_digest", swap_after_receipt_hash)
    monkeypatch.setattr(type(wrapper), "unlink", replace_then_unlink)

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 200
    assert response.json()["wrapper_status"] == "preserved_unsafe"
    assert wrapper.read_bytes() == successor
    assert unlink_calls == []
    assert store.get_projection(operation_id) is None


def test_forget_decides_changed_wrapper_before_deleting_failed_receipt(client, tmp_path, monkeypatch):
    """The persisted failed receipt remains available through wrapper verification."""
    from runtime.daemon import direct_connect_store as store_module

    tc, state = client
    operation_id = _mint_and_connect(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "conformance probe failed")
    wrapper = store.get_receipt_artifacts(operation_id).wrapper_path
    changed = b"#!/bin/sh\necho newer wrapper\n"
    wrapper.write_bytes(changed)
    original_cleanup = store_module._remove_matching_failed_wrapper

    def observe_changed_cleanup(artifacts):
        assert artifacts is not None
        assert artifacts.wrapper_path == wrapper
        assert store.get_projection(operation_id).state == "failed"
        assert store.get_receipt_artifacts(operation_id) is not None
        return original_cleanup(artifacts)

    monkeypatch.setattr(store_module, "_remove_matching_failed_wrapper", observe_changed_cleanup)

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 200
    assert response.json()["wrapper_status"] == "preserved_changed"
    assert wrapper.read_bytes() == changed
    assert store.get_projection(operation_id) is None


def test_forget_after_terminal_failed_retry_removes_attempt_and_retains_event_history(
    client, tmp_path, monkeypatch,
):
    """The production retry and forget routes leave no retry orphan behind."""
    from runtime.orchestrator import custom_adapter_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    retry_response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")

    assert retry_response.status_code == 200
    assert retry_response.json()["reason"] == "conformance_probe_failed"
    assert store._conn.execute(
        "SELECT state FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
    ).fetchone()[0] == "failed"
    event_types_before_forget = [row[0] for row in store._conn.execute(
        "SELECT event_type FROM direct_connect_events WHERE operation_id = ?", (operation_id,)
    )]
    assert {"projection_failed", "retry_validation_started", "retry_validation_failed"} <= set(
        event_types_before_forget
    )

    forget_response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert forget_response.status_code == 200
    assert store._conn.execute(
        "SELECT COUNT(*) FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
    ).fetchone()[0] == 0
    # THR-160: immutable parent/candidate/identity/receipt/audit evidence is
    # retained; only safe derived artifacts/projections/retry_attempts are removed.
    for table in ("direct_connect_artifacts", "direct_connect_projections"):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    for table in (
        "direct_connect_authorities", "direct_connect_parent_lifecycles",
        "direct_connect_candidates", "direct_connect_identity_history",
        "direct_connect_receipts", "direct_connect_operations",
        "direct_connect_reservations",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1
    event_types_after_forget = [row[0] for row in store._conn.execute(
        "SELECT event_type FROM direct_connect_events WHERE operation_id = ?", (operation_id,)
    )]
    assert set(event_types_before_forget) <= set(event_types_after_forget)
    assert "forgotten" in event_types_after_forget
    status_response = tc.get(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "custom-profile"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["operation_id"] == operation_id
    assert status_response.json()["profile_state"] == "failed"


def test_forget_refuses_a_durably_running_retry_without_deleting_operation_state(
    client, tmp_path, monkeypatch,
):
    """The store transaction must protect a claimed retry from /forget."""
    from runtime.daemon.direct_connect_retry import retry_validate
    from runtime.orchestrator import custom_adapter_registry

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    store = state.direct_connect_authority_store
    probe_started = threading.Event()
    allow_probe_to_finish = threading.Event()
    probe_calls = 0
    bind_calls = 0
    original_bind = custom_adapter_registry._perform_adapter_profile_binding

    def paused_probe(executable, adapter_id, **_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        assert allow_probe_to_finish.wait(timeout=5)
        from runtime.orchestrator.adapter_contract import AdapterOutput
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "9.9.9", "contract_version": 1},
        })

    def counted_bind(**kwargs):
        nonlocal bind_calls
        bind_calls += 1
        return original_bind(**kwargs)

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", paused_probe)
    monkeypatch.setattr(custom_adapter_registry, "_perform_adapter_profile_binding", counted_bind)
    retry_outcomes = []
    retry_thread = threading.Thread(
        target=lambda: retry_outcomes.append(retry_validate(store, operation_id)),
    )
    retry_thread.start()
    assert probe_started.wait(timeout=5)
    running_attempt = store._conn.execute(
        "SELECT attempt_id, state FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    assert running_attempt is not None
    assert running_attempt["state"] == "running"

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 409
    assert response.json()["detail"] == "refused: retry validation is running"
    assert store.get_projection(operation_id).state == "failed"
    assert store.get_receipt_artifacts(operation_id) is not None
    for table in (
        "direct_connect_operations", "direct_connect_artifacts", "direct_connect_receipts",
        "direct_connect_projections", "direct_connect_authorities",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0

    allow_probe_to_finish.set()
    retry_thread.join(timeout=10)

    assert not retry_thread.is_alive()
    assert [outcome.state for outcome in retry_outcomes] == ["committed"]
    assert probe_calls == bind_calls == 1
    attempts = store._conn.execute(
        "SELECT state FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
    ).fetchall()
    assert [row[0] for row in attempts] == ["succeeded"]
    status_response = tc.get("/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "custom-profile"})
    assert status_response.status_code == 200
    assert status_response.json()["profile_state"] == "committed"


def test_forget_refuses_failed_projection_after_retry_establishes_live_connection(client, tmp_path, monkeypatch):
    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)
    wrapper = state.direct_connect_authority_store.get_receipt_artifacts(operation_id).wrapper_path
    _fake_probe(monkeypatch)
    assert tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry").status_code == 200

    response = tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")

    assert response.status_code == 409
    assert response.json()["detail"] == "refused: retry validation established a live connection"
    assert wrapper.exists()
    assert state.direct_connect_authority_store.get_projection(operation_id).state == "failed"


def test_retry_refuses_stale_candidate_after_corrected_b_is_accepted(
    client, tmp_path, monkeypatch
):
    """Once genuinely changed candidate B is accepted, retry(A) is a non-consuming 409."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    token, operation_a = _mint_connect_and_fail(tc, state, tmp_path, monkeypatch)

    # Accept genuinely changed B under the same parent token.
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash_b = _write_executable(
        authority.wrapper_destination, b"#!/bin/sh\necho v2\n"
    )
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = {
        "metadata": {},
        "manifest": {
            "manifest_version": 2,
            "wrapper_sha256": wrapper_hash_b,
            "upgradeable_children": [
                {
                    "slot": "cli",
                    "executable": str(child_b),
                    "version_probe_argv": [str(child_b), "--version"],
                }
            ],
            "workspace_adapter_id": "codex",
        },
    }
    connect_b = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload_b,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect_b.status_code == 201
    operation_b = connect_b.json()["operation_id"]
    assert operation_b != operation_a

    # Retry(A) must be refused as stale without running a probe or binding.
    probe_calls = 0

    def counting_probe(*_args, **_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        raise AssertionError("stale retry must not invoke probe")

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", counting_probe)

    retry_response = tc.post(f"/api/v1/runtime/custom-cli/{operation_a}/retry")

    assert retry_response.status_code == 409
    assert "stale" in retry_response.json()["detail"].lower()
    assert probe_calls == 0

    store = state.direct_connect_authority_store
    # No retry attempt or retry-validation-started event for A.
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM direct_connect_retry_attempts WHERE operation_id = ?",
            (operation_a,),
        ).fetchone()[0]
        == 0
    )
    assert (
        store._conn.execute(
            """SELECT COUNT(*) FROM direct_connect_events
               WHERE operation_id = ? AND event_type = 'retry_validation_started'""",
            (operation_a,),
        ).fetchone()[0]
        == 0
    )

    # No adapter/profile residue from the stale retry.
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None

    # B remains the latest/current candidate and can be committed.
    latest = store.get_latest_candidate_for_token_fingerprint(
        fingerprint_registration_token(token)
    )
    assert latest is not None
    assert latest.operation_id == operation_b

    _fake_probe(monkeypatch)
    commit_b = tc.post(f"/api/v1/runtime/custom-cli/{operation_b}/commit")
    assert commit_b.status_code == 200
    assert commit_b.json()["profile_state"] == "committed"
    assert get_registry().get_profile("custom-profile") is not None


def test_race_retry_a_with_commit_b_after_b_accepted_only_b_wins(
    client, tmp_path, monkeypatch
):
    """Concurrent retry(A) and commit(B) after B is accepted: A is stale, B commits, one probe total.

    The real HTTP seam is covered by ``test_retry_refuses_stale_candidate_after_corrected_b_is_accepted``.
    Here we race the two coordinators directly so an explicit threading barrier can
    rendezvous at the store's latest-candidate seam (TestClient serializes requests
    through a single event-loop thread, so a barrier inside an HTTP handler would not
    admit both parties). The durable one-winner facts are the same.
    """
    from runtime.daemon import direct_connect_projection
    from runtime.daemon.direct_connect_retry import retry_validate
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    tc, state = client
    token, operation_a = _mint_connect_and_fail(tc, state, tmp_path, monkeypatch)

    # Accept B first (received_nonlaunchable, no projection).
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash_b = _write_executable(
        authority.wrapper_destination, b"#!/bin/sh\necho v2\n"
    )
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = {
        "metadata": {},
        "manifest": {
            "manifest_version": 2,
            "wrapper_sha256": wrapper_hash_b,
            "upgradeable_children": [
                {
                    "slot": "cli",
                    "executable": str(child_b),
                    "version_probe_argv": [str(child_b), "--version"],
                }
            ],
            "workspace_adapter_id": "codex",
        },
    }
    connect_b = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload_b,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect_b.status_code == 201
    operation_b = connect_b.json()["operation_id"]

    probe_count = [0]
    probe_lock = threading.Lock()

    def fake_probe(_executable, adapter_id, **_kwargs):
        with probe_lock:
            probe_count[0] += 1
        return AdapterOutput.model_validate({
            "success": True,
            "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "adapter_metadata": {
                "adapter": adapter_id,
                "adapter_version": "9.9.9",
                "contract_version": 1,
            },
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)

    # Synchronize both coordinators at the latest-candidate check seam.
    seam_barrier = threading.Barrier(2)
    original_claim = DirectConnectAuthorityStore.claim_retry_attempt
    original_project = direct_connect_projection.project

    def claim_at_seam(self, operation_id, *, now=None):
        seam_barrier.wait(timeout=5)
        return original_claim(self, operation_id, now=now)

    def project_at_seam(store, operation_id, *, now=None):
        seam_barrier.wait(timeout=5)
        return original_project(store, operation_id, now=now)

    monkeypatch.setattr(DirectConnectAuthorityStore, "claim_retry_attempt", claim_at_seam)
    monkeypatch.setattr(direct_connect_projection, "project", project_at_seam)

    retry_outcomes = []
    commit_outcomes = []

    def retry_a():
        try:
            retry_outcomes.append(retry_validate(state.direct_connect_authority_store, operation_a))
        except Exception as exc:  # noqa: BLE001
            retry_outcomes.append(exc)

    def commit_b():
        commit_outcomes.append(
            direct_connect_projection.project(state.direct_connect_authority_store, operation_b)
        )

    t_retry = threading.Thread(target=retry_a)
    t_commit = threading.Thread(target=commit_b)
    t_retry.start()
    t_commit.start()
    t_retry.join(timeout=10)
    t_commit.join(timeout=10)
    assert not t_retry.is_alive()
    assert not t_commit.is_alive()

    # retry-A must have been refused as a stale candidate.
    assert len(retry_outcomes) == 1
    assert isinstance(retry_outcomes[0], DirectConnectRetryStaleCandidateError)

    # commit-B must succeed.
    assert len(commit_outcomes) == 1
    assert commit_outcomes[0].state == "committed"

    # Exactly one probe ran (for B), zero stale-A retry probes.
    assert probe_count[0] == 1

    store = state.direct_connect_authority_store
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM direct_connect_retry_attempts WHERE operation_id = ?",
            (operation_a,),
        ).fetchone()[0]
        == 0
    )
    assert (
        store._conn.execute(
            """SELECT COUNT(*) FROM direct_connect_events
               WHERE operation_id = ? AND event_type = 'retry_validation_started'""",
            (operation_a,),
        ).fetchone()[0]
        == 0
    )

    # B is committed; no stale-A adapter/profile residue.
    assert get_registry().get_profile("custom-profile") is not None
    assert len(load_adapters()) == 1


def test_retry_current_failed_candidate_still_claims_one_attempt_and_is_idempotent(
    client, tmp_path, monkeypatch
):
    """A failed current candidate's retry still claims exactly one running attempt."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    tc, state = client
    operation_id = _failed_operation(tc, state, tmp_path)

    entered_probe = threading.Event()
    release_probe = threading.Event()
    probe_count = [0]
    probe_lock = threading.Lock()

    def fake_probe(_executable, adapter_id, **_kwargs):
        with probe_lock:
            probe_count[0] += 1
        entered_probe.set()
        assert release_probe.wait(timeout=5)
        return AdapterOutput.model_validate({
            "success": True,
            "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "adapter_metadata": {
                "adapter": adapter_id,
                "adapter_version": "9.9.9",
                "contract_version": 1,
            },
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)

    responses = []

    def run_retry():
        responses.append(tc.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry"))

    t1 = threading.Thread(target=run_retry)
    t2 = threading.Thread(target=run_retry)
    t1.start()
    t2.start()
    assert entered_probe.wait(timeout=5)

    # Exactly one running attempt should exist for the current failed candidate.
    attempts = state.direct_connect_authority_store._conn.execute(
        "SELECT state FROM direct_connect_retry_attempts WHERE operation_id = ?",
        (operation_id,),
    ).fetchall()
    assert len(attempts) == 1
    assert attempts[0][0] == "running"

    # Hold the winning retry in flight past the former 1s (50 x 20ms)
    # concurrent-caller poll budget so the concurrent caller MUST wait for
    # the winner's real terminal outcome instead of receiving a fabricated
    # failure (CI regression: Python 3.14 runner descheduling exceeded the
    # old budget). The winner's conformance probe is itself bounded
    # (CONFORMANCE_PROBE_TIMEOUT_SECONDS = 30s), so 1.5s is well within the
    # repaired budget — same technique as
    # test_concurrent_browser_commit_reconciles_durable_planned_winner.
    time.sleep(1.5)
    release_probe.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive()
    assert not t2.is_alive()

    assert all(r.status_code == 200 for r in responses)
    assert all(r.json()["profile_state"] == "committed" for r in responses)
    assert probe_count[0] == 1
