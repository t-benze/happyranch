"""THR-107 Slice 1: direct-connect projection coordinator tests."""
from __future__ import annotations

import hashlib
import json

import pytest


def _mint_and_receive(store, tmp_path, *, token="hrreg_proj", profile_name="custom-profile", adapter="codex"):
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name=profile_name,
        workspace_adapter_id=adapter, issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(b"#!/bin/sh\ncat\n")
    wrapper.chmod(0o700)
    wrapper_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    child = tmp_path / "bin" / "child"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    child_hash = hashlib.sha256(child.read_bytes()).hexdigest()
    operation_id = store.reserve(token, now=2)
    store.receive(
        token, operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        now=2,
    )
    return operation_id, wrapper


def _fake_probe_output(adapter_id: str):
    from runtime.orchestrator.adapter_contract import AdapterOutput

    payload = {
        "success": True, "duration_seconds": 0, "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
        "returncode": 0, "stdout_tail": "", "stderr_tail": "",
        "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.2.3", "contract_version": 1},
    }
    return AdapterOutput.model_validate(payload)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    s = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset

    _reset()
    yield
    _reset()


def test_successful_projection_commits_adapter_and_profile(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    adapter_id = custom_adapter_registry.generate_adapter_id("custom-profile-adapter")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _fake_probe_output(name),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "committed"
    assert outcome.adapter_id == adapter_id
    assert outcome.profile_name == "custom-profile"
    from runtime.orchestrator.adapter_store import get_adapter
    entry = get_adapter(adapter_id)
    assert entry.status == "approved"
    assert entry.registered_by == entry.approved_by == "direct-connect"
    assert entry.dependency_manifest_version == 1
    assert len(entry.dependencies) == 1
    profile = get_registry().get_profile("custom-profile")
    assert profile is not None
    assert profile.command_adapter_id == f"custom-adapter:{adapter_id}"
    projection = store.get_projection(operation_id)
    assert projection.state == "committed"


def test_projection_is_idempotent_on_retry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _fake_probe_output(name),
    )

    first = project(store, operation_id)
    second = project(store, operation_id)

    assert first.state == second.state == "committed"
    assert first.adapter_id == second.adapter_id


def test_conformance_probe_failure_compensates_with_no_partial_state(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    projection = store.get_projection(operation_id)
    assert projection.state == "failed"
    assert projection.reason


def test_profile_binding_failure_removes_adapter_entry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _fake_probe_output(name),
    )
    monkeypatch.setattr(
        custom_adapter_registry, "_perform_adapter_profile_binding",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("binding failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}


def test_unknown_operation_raises(store):
    from runtime.daemon.direct_connect_projection import project

    with pytest.raises(RuntimeError, match="no receipt"):
        project(store, "does-not-exist")


def test_concurrent_projection_has_one_committer(store, tmp_path, monkeypatch):
    import threading

    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _fake_probe_output(name),
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def run():
        barrier.wait()
        outcomes.append(project(store, operation_id))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(o.state == "committed" for o in outcomes)
    assert len({o.adapter_id for o in outcomes}) == 1


def test_projection_state_survives_store_reopen(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    operation_id, wrapper = _mint_and_receive(store, tmp_path, token="hrreg_reopen")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _fake_probe_output(name),
    )
    committed = project(store, operation_id)
    store.close()

    reopened = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    projection = reopened.get_projection(operation_id)
    assert projection.state == "committed"
    assert projection.adapter_id == committed.adapter_id
    reopened.close()
