"""THR-107 slice 2: prove only a Slice-1-COMMITTED direct-connect profile
can construct or launch an executor, at the real shared seam every
shipping path funnels through.
"""
from __future__ import annotations

import hashlib

import pytest

from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths


def _commit_direct_connect_profile(tmp_path, monkeypatch, *, profile_name="fence-profile"):
    from runtime.daemon.direct_connect_projection import project
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_fence", name="fence-cli", intended_profile_name=profile_name,
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token("hrreg_fence")
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
    operation_id = store.reserve("hrreg_fence", now=2)
    store.receive(
        "hrreg_fence", operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        workspace_adapter_id="codex", now=2,
    )

    def fake_probe(executable, adapter_id, **_kwargs):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.0.0", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    outcome = project(store, operation_id)
    assert outcome.state == "committed"
    return store, operation_id, wrapper, profile_name


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset

    _reset()
    yield
    _reset()


def test_committed_direct_connect_profile_constructs_via_ordinary_task_seam(tmp_path, monkeypatch):
    """Orchestrator._build_executor (ordinary-task path) resolves a Slice-1-COMMITTED profile."""
    from runtime.orchestrator.executor_registry import build_executor
    from runtime.orchestrator.executors import CustomAdapterExecutor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)

    executor = build_executor(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    assert isinstance(executor, CustomAdapterExecutor)
    assert executor._adapter_executable == str(wrapper)


def test_committed_direct_connect_profile_constructs_via_thread_wake_dream_schedule_seam(tmp_path, monkeypatch):
    """thread_runner._build_executor_for_provider (shared by wake/dream/schedule) resolves it too."""
    from runtime.daemon.thread_runner import _build_executor_for_provider
    from runtime.orchestrator.executors import CustomAdapterExecutor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)

    executor = _build_executor_for_provider(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    assert isinstance(executor, CustomAdapterExecutor)
    assert executor._adapter_executable == str(wrapper)


def test_wake_dream_schedule_runners_import_the_identical_builder_function():
    """Not four independent implementations — one shared function object."""
    from runtime.daemon import dream_runner, schedule_runner, thread_runner, wake_runner

    assert wake_runner._build_executor_for_provider is thread_runner._build_executor_for_provider
    assert dream_runner._build_executor_for_provider is thread_runner._build_executor_for_provider
    assert schedule_runner._build_executor_for_provider is thread_runner._build_executor_for_provider


def test_uncommitted_operation_has_no_registered_profile_and_fails_closed(tmp_path, monkeypatch):
    """A direct-connect operation that never reached COMMITTED (still just
    received_nonlaunchable) must not be selectable/launchable — because no
    runtime profile or adapter entry was ever written for it, build_executor
    correctly refuses with 'Unregistered executor', at the same shared seam
    every shipping path uses."""
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.orchestrator.executor_registry import build_executor

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_uncommitted", name="fence-cli", intended_profile_name="never-committed",
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token("hrreg_uncommitted")
    authority.wrapper_destination.parent.mkdir(parents=True, exist_ok=True)
    authority.wrapper_destination.write_bytes(b"#!/bin/sh\ncat\n")
    authority.wrapper_destination.chmod(0o700)
    operation_id = store.reserve("hrreg_uncommitted", now=2)
    wrapper_hash = hashlib.sha256(authority.wrapper_destination.read_bytes()).hexdigest()
    store.receive(
        "hrreg_uncommitted", operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={}, children=[],
        workspace_adapter_id="codex", now=2,
    )
    # Deliberately no project()/commit call — this operation stays received_nonlaunchable.

    with pytest.raises(ValueError, match="Unregistered executor"):
        build_executor("never-committed", Settings(), OrgPaths(root=tmp_path / "org"))


def test_committed_profile_launches_through_canonical_wrapper_with_dependency(tmp_path, monkeypatch):
    """End-to-end: a COMMITTED direct-connect profile's executor actually
    launches the canonical wrapper (not any other path)."""
    import subprocess

    from runtime.orchestrator.executor_registry import build_executor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)
    executor = build_executor(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    executor.set_invocation_context(agent="dev_agent", org="happyranch", invocation_kind="task")

    launched: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        launched.append(argv)
        raise FileNotFoundError("intentional stop before real subprocess spawn")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = executor.run(workspace=tmp_path / "ws", prompt="hi", timeout_seconds=5)

    assert launched == [[str(wrapper)]]
    assert result.success is False
    assert "Failed to launch custom adapter" in (result.error or "")
