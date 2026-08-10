"""THR-160 daemon-owned direct-connect projection sweep tests."""
from __future__ import annotations

import hashlib
import threading

import pytest


def _mint_and_receive(store, tmp_path, *, token: str, profile_name: str) -> str:
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name=profile_name,
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(b"#!/bin/sh\ncat\n")
    wrapper.chmod(0o700)
    child = tmp_path / "bin" / profile_name
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    operation_id = store.reserve(token, now=2)
    store.receive(
        token, operation_id,
        wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        wrapper_facts={},
        children=[{
            "slot": "cli", "path": str(child),
            "sha256": hashlib.sha256(child.read_bytes()).hexdigest(), "facts": {},
        }],
        workspace_adapter_id="codex", now=2,
    )
    return operation_id


def _fake_probe_output(adapter_id: str):
    from runtime.orchestrator.adapter_contract import AdapterOutput

    return AdapterOutput.model_validate({
        "success": True, "duration_seconds": 0,
        "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
        "returncode": 0, "stdout_tail": "", "stderr_tail": "",
        "adapter_metadata": {
            "adapter": adapter_id, "adapter_version": "1.2.3", "contract_version": 1,
        },
    })


@pytest.fixture
def store(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    authority_store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    yield authority_store
    authority_store.close()


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry

    reset_registry()
    yield
    reset_registry()


def test_sweep_commits_received_operation_without_browser_commit(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection_sweep import _sweep_once
    from runtime.orchestrator import custom_adapter_registry

    operation_id = _mint_and_receive(
        store, tmp_path, token="hrreg_sweep", profile_name="sweep-profile",
    )
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda _executable, adapter_id: _fake_probe_output(adapter_id),
    )

    _sweep_once(store)

    projection = store.get_projection(operation_id)
    assert projection is not None
    assert projection.state == "committed"
    assert projection.profile_name == "sweep-profile"


def test_sweep_leaves_terminal_operations_alone(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.daemon.direct_connect_projection_sweep import _sweep_once
    from runtime.orchestrator import custom_adapter_registry

    operation_id = _mint_and_receive(
        store, tmp_path, token="hrreg_terminal", profile_name="terminal-profile",
    )
    probe_calls = 0

    def fake_probe(_executable, adapter_id):
        nonlocal probe_calls
        probe_calls += 1
        return _fake_probe_output(adapter_id)

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    assert project(store, operation_id).state == "committed"

    _sweep_once(store)

    assert probe_calls == 1
    assert store.get_projection(operation_id).state == "committed"


def test_sweep_and_browser_projection_race_to_one_committer(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.daemon.direct_connect_projection_sweep import _sweep_once
    from runtime.orchestrator import custom_adapter_registry

    operation_id = _mint_and_receive(
        store, tmp_path, token="hrreg_race", profile_name="race-profile",
    )
    original_plan = store.plan_projection
    original_list_pending = store.list_operations_pending_projection
    snapshot_barrier = threading.Barrier(2)
    plan_barrier = threading.Barrier(2)
    probe_calls = 0
    browser_outcomes = []

    def synchronized_plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        plan_barrier.wait()
        return result

    def synchronized_list_pending():
        operation_ids = original_list_pending()
        snapshot_barrier.wait()
        return operation_ids

    def fake_probe(_executable, adapter_id):
        nonlocal probe_calls
        probe_calls += 1
        return _fake_probe_output(adapter_id)

    monkeypatch.setattr(store, "list_operations_pending_projection", synchronized_list_pending)
    monkeypatch.setattr(store, "plan_projection", synchronized_plan)
    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    def browser_commit():
        snapshot_barrier.wait()
        browser_outcomes.append(project(store, operation_id))

    browser = threading.Thread(target=browser_commit)
    browser.start()
    _sweep_once(store)
    browser.join()

    projection = store.get_projection(operation_id)
    assert probe_calls == 1
    assert projection is not None and projection.state == "committed"
    assert browser_outcomes[0].state == "committed"
    assert browser_outcomes[0].adapter_id == projection.adapter_id


def test_sweep_preserves_bounded_conformance_probe_diagnostic(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection_sweep import _sweep_once
    from runtime.orchestrator import custom_adapter_registry

    operation_id = _mint_and_receive(
        store, tmp_path, token="hrreg_diagnostic", profile_name="diagnostic-profile",
    )
    detail = "Conformance probe exited with code 7 for '/tmp/wrapper'. stderr tail: bounded stderr detail"
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda _executable, _adapter_id: (_ for _ in ()).throw(ValueError(detail)),
    )

    _sweep_once(store)

    projection = store.get_projection(operation_id)
    assert projection is not None and projection.state == "failed"
    assert projection.reason == f"conformance_probe_failed: {detail}"


def test_one_failing_operation_does_not_block_later_pending_operation(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection_sweep import _sweep_once
    from runtime.orchestrator import custom_adapter_registry

    failing = _mint_and_receive(
        store, tmp_path, token="hrreg_failing", profile_name="failing-profile",
    )
    succeeding = _mint_and_receive(
        store, tmp_path, token="hrreg_succeeding", profile_name="succeeding-profile",
    )
    original_project = __import__(
        "runtime.daemon.direct_connect_projection", fromlist=["project"],
    ).project

    def fail_one_then_project(authority_store, operation_id):
        if operation_id == failing:
            raise RuntimeError("unexpected sweep error")
        return original_project(authority_store, operation_id)

    monkeypatch.setattr(
        "runtime.daemon.direct_connect_projection_sweep.direct_connect_projection.project",
        fail_one_then_project,
    )
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda _executable, adapter_id: _fake_probe_output(adapter_id),
    )

    _sweep_once(store)

    assert store.get_projection(failing) is None
    assert store.get_projection(succeeding).state == "committed"
