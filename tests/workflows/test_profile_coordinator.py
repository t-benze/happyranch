from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import BrokenBarrierError

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_projection_sweep import _sweep_once
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.daemon.org_state import OrgState
from runtime.daemon.state import DaemonState
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_registry import ExecutorProfile, get_registry
from runtime.orchestrator.runtime_executor_store import (
    remove_runtime_profile,
    save_runtime_profile,
)
from runtime.runtime import RuntimeDir
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import (
    ProfileCoordinator,
    ProfileCoordinatorError,
    _ProfileOperation,
)


def _seed_org(root: Path, slug: str, executors: dict[str, str]) -> OrgState:
    paths = OrgPaths(root=root)
    paths.agents_dir.mkdir(parents=True)
    workers = sorted({*executors, "code_reviewer", "senior_dev"})
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_manager\n"
        f"    workers: [{', '.join(workers)}]\n"
    )
    definitions = {
        "engineering_manager": "claude",
        "code_reviewer": "claude",
        "senior_dev": "claude",
        **executors,
    }
    for name, executor in definitions.items():
        definition = AgentDef(
            name=name,
            team="engineering",
            role="manager" if name == "engineering_manager" else "worker",
            executor=executor,
            allow_rules=("git",),
            repos={},
            enrolled_by="founder",
            enrolled_at_task="TASK-9292",
            enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            system_prompt=f"prompt:{name}",
            description=f"description:{name}",
        )
        (paths.agents_dir / f"{name}.md").write_text(render_agent_text(definition))
    return OrgState.load(slug=slug, root=root, settings=Settings())


@contextmanager
def _registered_profile(name: str, *, workspace: str = "pi"):
    from runtime.orchestrator.adapter_store import (
        AdapterEntry,
        get_adapter,
        remove_adapter,
        save_adapter,
    )

    registry = get_registry()
    previous = registry.get_profile(name)
    adapter_id = f"{name}-adapter"
    previous_adapter = get_adapter(adapter_id)
    executable = Path(os.environ["HAPPYRANCH_DAEMON_HOME"]) / "test-adapters" / adapter_id
    executable_hash = _write_executable(executable, b"#!/bin/sh\ncat\n")
    save_adapter(
        AdapterEntry(
            id=adapter_id,
            name=adapter_id,
            executable=str(executable),
            executable_hash=executable_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter=workspace,
            status="approved",
            registered_at="2026-10-01T00:00:00+00:00",
            registered_by="test",
            approved_at="2026-10-01T00:00:00+00:00",
            approved_by="test",
        )
    )
    profile = ExecutorProfile(
        name=name,
        kind="custom",
        workspace_adapter_id=workspace,
        command_adapter_id=f"custom-adapter:{adapter_id}",
    )
    # The coordinator test owns only the process cache seam; adapter
    # eligibility is exercised independently by route tests.
    registry._profiles[name] = profile
    try:
        yield profile
    finally:
        if previous is None:
            registry._profiles.pop(name, None)
        else:
            registry._profiles[name] = previous
        if previous_adapter is None:
            remove_adapter(adapter_id)
        else:
            save_adapter(previous_adapter)


def _pointer(org: OrgState) -> tuple[int, str, int]:
    row = org.db.execute(
        "SELECT current_generation,state,profile_fence "
        "FROM workflow_authority_pointers WHERE namespace=?",
        (org.workflow_authority.namespace,),
    ).fetchone()
    assert row is not None
    return int(row["current_generation"]), str(row["state"]), int(row["profile_fence"])


def _hold_profile_lock(daemon_home: str, profile_name: str, ready, release) -> None:
    coordinator = ProfileCoordinator(daemon_home=Path(daemon_home), orgs={})
    with coordinator.profile_read(profile_name):
        ready.set()
        release.wait(10)


def _acquire_profile_lock_then_exit(
    daemon_home: str,
    profile_name: str,
    ready,
) -> None:
    coordinator = ProfileCoordinator(daemon_home=Path(daemon_home), orgs={})
    with coordinator.profile_read(profile_name):
        ready.set()
        os._exit(0)


def _write_profile_after_shared_read(
    daemon_home: str,
    profile_name: str,
    barrier,
) -> None:
    """Force old read/merge/replace writers to share one predecessor read."""
    from runtime.orchestrator import runtime_executor_store

    os.environ["HAPPYRANCH_DAEMON_HOME"] = daemon_home
    original_load = runtime_executor_store.load_runtime_profiles

    def synchronized_load():
        current = original_load()
        try:
            barrier.wait(timeout=0.5)
        except BrokenBarrierError:
            pass
        return current

    runtime_executor_store.load_runtime_profiles = synchronized_load
    coordinator = ProfileCoordinator(daemon_home=Path(daemon_home), orgs={})
    with coordinator.operation(
        [profile_name], operation_kind="register", publisher=f"store-race:{profile_name}",
    ):
        runtime_executor_store.save_runtime_profile(
            profile_name,
            {
                "workspace_adapter_id": "pi",
                "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
            },
        )


class _BusyOnceCoordinator:
    def __init__(self, delegate: ProfileCoordinator) -> None:
        self.delegate = delegate
        self.calls = 0

    @contextmanager
    def operation(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ProfileCoordinatorError("profile_coordinator_busy")
        with self.delegate.operation(*args, **kwargs):
            yield


def _write_executable(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o700)
    return hashlib.sha256(body).hexdigest()


def _direct_connect_route_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, DaemonState, str, _BusyOnceCoordinator]:
    daemon_home = tmp_path / "daemon"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    assert state.direct_connect_authority_store is not None
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(
        tmp_path / "direct.db", runtime_root=daemon_home,
    )
    busy_once = _BusyOnceCoordinator(
        ProfileCoordinator(daemon_home=daemon_home, orgs={}),
    )
    state.profile_coordinator = busy_once  # type: ignore[assignment]

    from runtime.daemon.routes import auth, direct_connect
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(
        direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"},
    )

    def fake_probe(executable, adapter_id, **_kwargs):
        return AdapterOutput.model_validate(
            {
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
            }
        )

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    client = TestClient(create_app(state))
    client.headers.update({"Authorization": f"Bearer {paths.read_token()}"})
    minted = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": "codex",
        },
    )
    assert minted.status_code == 200
    token = minted.json()["token"]
    authority = state.direct_connect_authority_store.get_for_token(token)
    assert authority is not None
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child, b"#!/bin/sh\nexit 0\n")
    connected = client.post(
        "/api/v1/runtime/custom-cli/connect",
        json={
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
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connected.status_code == 201
    return client, state, connected.json()["operation_id"], busy_once


def test_startup_reconcile_tracks_each_real_agent_consumer_and_publishes_profile_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "u2b-startup"
    save_runtime_profile(
        profile_name,
        {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
        },
    )
    with _registered_profile(profile_name):
        org = _seed_org(
            tmp_path / "orgs" / "alpha",
            "alpha",
            {"worker_a": profile_name, "worker_b": profile_name},
        )
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()

        dependencies = [
            tuple(row)
            for row in org.db.execute(
                "SELECT profile_name,consumer_identity,bound_generation,state "
                "FROM workflow_profile_dependencies ORDER BY consumer_identity"
            ).fetchall()
        ]
        assert dependencies == [
            (profile_name, "worker_a", 1, "active"),
            (profile_name, "worker_b", 1, "active"),
        ]
        ready = org.workflow_authority.verify_admission_ready()
        snapshot = json.loads(ready.snapshot_bytes)
        assert snapshot["machine_global_profiles"] == [
            {
                "consumers": ["worker_a", "worker_b"],
                "generation": 1,
                "profile_digest": coordinator.profile_digest(profile_name),
                "profile_name": profile_name,
            }
        ]
        org.close()


def test_profile_operation_fences_and_republishes_only_dependent_orgs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "u2b-shared"
    initial = {
        "workspace_adapter_id": "pi",
        "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
    }
    save_runtime_profile(profile_name, initial)
    with _registered_profile(profile_name):
        alpha = _seed_org(tmp_path / "orgs" / "alpha", "alpha", {"a": profile_name})
        beta = _seed_org(tmp_path / "orgs" / "beta", "beta", {"b": profile_name})
        gamma = _seed_org(tmp_path / "orgs" / "gamma", "gamma", {"c": "claude"})
        coordinator = ProfileCoordinator(
            daemon_home=daemon_home,
            orgs={"alpha": alpha, "beta": beta, "gamma": gamma},
        )
        coordinator.reconcile_startup()
        before = {slug: _pointer(org) for slug, org in coordinator.orgs.items()}

        with coordinator.operation(
            [profile_name], operation_kind="rebind", publisher="test-rebind",
        ):
            assert _pointer(alpha)[1:] == ("fenced", before["alpha"][2] + 1)
            assert _pointer(beta)[1:] == ("fenced", before["beta"][2] + 1)
            assert _pointer(gamma) == before["gamma"]

        after = {slug: _pointer(org) for slug, org in coordinator.orgs.items()}
        assert after["alpha"] == (before["alpha"][0] + 1, "ready", before["alpha"][2] + 1)
        assert after["beta"] == (before["beta"][0] + 1, "ready", before["beta"][2] + 1)
        assert after["gamma"] == before["gamma"]
        for org in (alpha, beta, gamma):
            org.close()


def test_changed_profile_advances_mirror_and_authority_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "u2b-changed"
    initial = {
        "workspace_adapter_id": "pi",
        "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
    }
    save_runtime_profile(profile_name, initial)
    with _registered_profile(profile_name):
        org = _seed_org(tmp_path / "orgs" / "alpha", "alpha", {"a": profile_name})
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()
        before = _pointer(org)
        replacement = ExecutorProfile(
            name=profile_name,
            kind="custom",
            workspace_adapter_id="codex",
            command_adapter_id=f"custom-adapter:{profile_name}-adapter",
        )
        with coordinator.operation(
            [profile_name], operation_kind="rebind", publisher="test-change",
        ):
            save_runtime_profile(
                profile_name,
                {
                    "workspace_adapter_id": "codex",
                    "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
                },
            )
            get_registry().replace_custom_profile(replacement)

        assert _pointer(org) == (before[0] + 1, "ready", before[2] + 1)
        store = org.db.execute(
            "SELECT generation,profile_digest,state FROM workflow_profile_store "
            "WHERE profile_name=?",
            (profile_name,),
        ).fetchone()
        registry = org.db.execute(
            "SELECT published_generation FROM workflow_profile_registry "
            "WHERE profile_name=?",
            (profile_name,),
        ).fetchone()
        assert store is not None and tuple(store) == (
            2, coordinator.profile_digest(profile_name), "active",
        )
        assert registry is not None and registry["published_generation"] == 2
        org.close()


def test_live_profile_owner_is_busy_and_release_allows_next_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "u2b-contention"
    coordinator_a = ProfileCoordinator(daemon_home=daemon_home, orgs={})
    coordinator_b = ProfileCoordinator(daemon_home=daemon_home, orgs={})

    with coordinator_a.profile_read(profile_name):
        with pytest.raises(ProfileCoordinatorError, match="profile_coordinator_busy"):
            with coordinator_b.profile_read(profile_name):
                pass
    with coordinator_b.profile_read(profile_name):
        pass


def test_independent_process_live_owner_is_busy_and_dead_owner_is_reclaimed(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    daemon_home = tmp_path / "daemon-home"
    profile_name = "u2b-process-owner"
    live_ready = context.Event()
    live_release = context.Event()
    live = context.Process(
        target=_hold_profile_lock,
        args=(str(daemon_home), profile_name, live_ready, live_release),
    )
    live.start()
    assert live_ready.wait(10)
    contender = ProfileCoordinator(daemon_home=daemon_home, orgs={})
    with pytest.raises(ProfileCoordinatorError, match="profile_coordinator_busy"):
        with contender.profile_read(profile_name):
            pass
    live_release.set()
    live.join(10)
    assert live.exitcode == 0

    dead_ready = context.Event()
    dead = context.Process(
        target=_acquire_profile_lock_then_exit,
        args=(str(daemon_home), profile_name, dead_ready),
    )
    dead.start()
    assert dead_ready.wait(10)
    dead.join(10)
    assert dead.exitcode == 0
    with contender.profile_read(profile_name):
        pass


def test_different_profile_processes_serialize_shared_runtime_profile_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    writers = [
        context.Process(
            target=_write_profile_after_shared_read,
            args=(str(daemon_home), profile_name, barrier),
        )
        for profile_name in ("alpha", "beta")
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(10)
        assert writer.exitcode == 0

    from runtime.orchestrator.runtime_executor_store import load_runtime_profiles

    assert sorted(load_runtime_profiles()) == ["alpha", "beta"]


@pytest.mark.parametrize("recovery_path", ["route", "sweep"])
def test_planned_direct_connect_contention_is_retried_to_one_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_path: str,
) -> None:
    from runtime.orchestrator.executor_registry import reset_registry

    reset_registry()
    client, state, operation_id, busy_once = _direct_connect_route_fixture(
        tmp_path, monkeypatch,
    )
    first = client.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
    assert first.status_code == 409
    assert first.json()["detail"] == {"code": "profile_coordinator_busy"}
    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None and projection.state == "planned"

    if recovery_path == "route":
        recovered = client.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
        assert recovered.status_code == 200
        assert recovered.json()["profile_state"] == "committed"
    else:
        _sweep_once(state.direct_connect_authority_store, state.profile_coordinator)

    projection = state.direct_connect_authority_store.get_projection(operation_id)
    assert projection is not None and projection.state == "committed"
    with state.direct_connect_authority_store._lock:
        committed_events = state.direct_connect_authority_store._conn.execute(
            "SELECT COUNT(*) FROM direct_connect_events "
            "WHERE operation_id=? AND event_type='committed'",
            (operation_id,),
        ).fetchone()[0]
    assert committed_events == 1
    assert busy_once.calls == 2
    reset_registry()


def test_generic_adapter_route_fences_registry_bound_profile_when_it_becomes_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput
    from runtime.orchestrator.adapter_store import AdapterEntry, save_adapter
    from runtime.orchestrator.executor_registry import reset_registry

    reset_registry()
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    paths.ensure_daemon_home()
    paths.ensure_token()
    adapter_executable = tmp_path / "bin" / "linked-adapter"
    adapter_hash = _write_executable(adapter_executable, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    child_hash = _write_executable(child, b"#!/bin/sh\nexit 0\n")
    adapter_id = custom_adapter_registry.generate_adapter_id(adapter_executable.name)
    save_adapter(
        AdapterEntry(
            id=adapter_id,
            name=adapter_executable.name,
            executable=str(adapter_executable),
            executable_hash=adapter_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="approved",
            registered_at="2026-10-01T00:00:00+00:00",
            registered_by="test",
            approved_at="2026-10-01T00:00:00+00:00",
            approved_by="test",
            dependency_manifest_version=1,
            dependencies=[{"executable": str(child), "sha256": child_hash}],
        )
    )
    profile_name = "route-bound-profile"
    config = {
        "workspace_adapter_id": "pi",
        "command_adapter_id": f"custom-adapter:{adapter_id}",
    }
    save_runtime_profile(profile_name, config)
    profile = get_registry().validate_custom_profile_config(profile_name, config)
    get_registry().register_custom_profile(profile)
    org = _seed_org(tmp_path / "orgs" / "alpha", "alpha", {"worker": profile_name})
    coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
    coordinator.reconcile_startup()
    before = _pointer(org)

    # Emulate the supported writer's durable/cache split at the exact closure
    # boundary: the registry still identifies the adapter-bound profile while
    # the YAML row is unavailable. The route must still capture its dependent
    # org and may not publish readiness after downgrading the adapter.
    remove_runtime_profile(profile_name)

    def fake_probe(executable, probed_adapter_id, **_kwargs):
        return AdapterOutput.model_validate(
            {
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "",
                "stderr_tail": "",
                "adapter_metadata": {
                    "adapter": probed_adapter_id,
                    "adapter_version": "2.0.0",
                    "contract_version": 1,
                },
            }
        )

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    state = DaemonState.idle(Settings())
    state.orgs = {"alpha": org}
    state.profile_coordinator = coordinator
    org._profile_coordinator = coordinator
    client = TestClient(create_app(state))
    response = client.post(
        "/api/v1/runtime/adapters/register",
        headers={"Authorization": f"Bearer {paths.read_token()}"},
        json={
            "executable": str(adapter_executable),
            "version": "2.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(child), "sha256": child_hash}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert _pointer(org) == (before[0], "fenced", before[2] + 1)
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        org.workflow_authority.verify_admission_ready()
    org.close()
    reset_registry()


def test_executor_remove_route_fences_real_dependent_org(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.orchestrator.executor_registry import reset_registry

    reset_registry()
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    paths.ensure_daemon_home()
    paths.ensure_token()
    profile_name = "executor-route-profile"
    save_runtime_profile(
        profile_name,
        {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
        },
    )
    with _registered_profile(profile_name):
        org = _seed_org(
            tmp_path / "orgs" / "alpha", "alpha", {"worker": profile_name},
        )
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()
        before = _pointer(org)
        state = DaemonState.idle(Settings())
        state.orgs = {"alpha": org}
        state.profile_coordinator = coordinator
        org._profile_coordinator = coordinator
        client = TestClient(create_app(state))
        response = client.delete(
            f"/api/v1/executors/runtime/profiles/{profile_name}",
            headers={"Authorization": f"Bearer {paths.read_token()}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"name": profile_name, "removed": True}
        assert _pointer(org) == (before[0], "fenced", before[2] + 1)
        with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
            org.workflow_authority.verify_admission_ready()
        org.close()
    reset_registry()


def test_daemon_state_startup_recovers_interrupted_profile_operation_once_before_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "startup-shipping-profile"
    save_runtime_profile(
        profile_name,
        {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
        },
    )
    with _registered_profile(profile_name):
        runtime = RuntimeDir.init(tmp_path / "runtime")
        root = runtime.orgs_dir / "alpha"
        org = _seed_org(root, "alpha", {"worker": profile_name})
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()
        operation = _ProfileOperation(
            operation_id="WPO-startup-shipping",
            profile_name=profile_name,
            operation_kind="rebind",
            members=("alpha",),
            target_generation=2,
            prior=coordinator._effective_profile(profile_name),
        )
        invocation = "startup-shipping"
        coordinator._insert_operation_rows(
            operation, coordinator_invocation=invocation,
        )
        with org.workflow_authority.profile_change_interval(
            reason="test:startup-shipping",
            coordinator_invocation=invocation,
        ):
            coordinator._set_operation_state(operation, "fenced")
        org.close()

        first = DaemonState.from_runtime(runtime, Settings())
        first_org = first.orgs["alpha"]
        first_ready = first_org.workflow_authority.verify_admission_ready()
        first_pointer = _pointer(first_org)
        row = first_org.db.execute(
            "SELECT state FROM workflow_profile_operations WHERE id=?",
            (operation.operation_id,),
        ).fetchone()
        assert row is not None and row["state"] == "aborted"
        assert first_ready.generation == first_pointer[0]
        asyncio.run(first.close_all())

        second = DaemonState.from_runtime(runtime, Settings())
        second_org = second.orgs["alpha"]
        second_org.workflow_authority.verify_admission_ready()
        assert _pointer(second_org) == first_pointer
        row = second_org.db.execute(
            "SELECT state FROM workflow_profile_operations WHERE id=?",
            (operation.operation_id,),
        ).fetchone()
        assert row is not None and row["state"] == "aborted"
        asyncio.run(second.close_all())


@pytest.mark.parametrize("first_owner", ["profile", "org"])
def test_profile_and_org_writer_contention_preserves_lock_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_owner: str,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = f"order-{first_owner}"
    save_runtime_profile(
        profile_name,
        {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
        },
    )
    with _registered_profile(profile_name):
        org = _seed_org(
            tmp_path / "orgs" / "alpha", "alpha", {"worker": profile_name},
        )
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()
        release = threading.Event()
        errors: list[BaseException] = []

        if first_owner == "profile":
            entered = threading.Event()

            def hold_profile() -> None:
                try:
                    with coordinator.operation(
                        [profile_name],
                        operation_kind="rebind",
                        publisher="order-profile-first",
                    ):
                        entered.set()
                        release.wait(5)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=hold_profile)
            worker.start()
            assert entered.wait(5)
            dependent_done = threading.Event()

            def dependent_after_profile() -> None:
                try:
                    with coordinator.dependency_writer([profile_name]):
                        dependent_done.set()
                except BaseException as exc:
                    errors.append(exc)

            dependent = threading.Thread(target=dependent_after_profile)
            dependent.start()
            assert not dependent_done.wait(0.1)
            release.set()
            worker.join(5)
            dependent.join(5)
            assert dependent_done.is_set()
        else:
            org_entered = threading.Event()

            def hold_org() -> None:
                try:
                    with org.workflow_authority.writer_interval(
                        publisher="order-org-first",
                    ):
                        org_entered.set()
                        release.wait(5)
                except BaseException as exc:
                    errors.append(exc)

            def profile_after_org() -> None:
                try:
                    with coordinator.operation(
                        [profile_name],
                        operation_kind="rebind",
                        publisher="order-profile-after-org",
                    ):
                        pass
                except BaseException as exc:
                    errors.append(exc)

            org_worker = threading.Thread(target=hold_org)
            profile_worker = threading.Thread(target=profile_after_org)
            org_worker.start()
            assert org_entered.wait(5)
            profile_worker.start()
            deadline = time.monotonic() + 5
            while True:
                active = org.db.execute(
                    "SELECT state FROM workflow_profile_operations "
                    "WHERE profile_name=? AND state NOT IN ('published','aborted')",
                    (profile_name,),
                ).fetchone()
                if active is not None:
                    break
                if time.monotonic() >= deadline:
                    pytest.fail("profile writer never acquired the profile lease")
                time.sleep(0.01)
            assert profile_worker.is_alive()
            release.set()
            org_worker.join(5)
            profile_worker.join(5)
            assert not org_worker.is_alive() and not profile_worker.is_alive()

        assert not errors
        org.workflow_authority.verify_admission_ready()
        org.close()


@pytest.mark.parametrize(
    ("boundary", "expected_terminal"),
    [
        ("captured", "aborted"),
        ("fenced", "aborted"),
        ("store_committed", "published"),
        ("registry_committed", "published"),
        ("canonical_published", "published"),
    ],
)
def test_cold_reconcile_is_exactly_once_at_each_durable_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_terminal: str,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = f"u2b-recovery-{boundary}"
    config = {
        "workspace_adapter_id": "pi",
        "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
    }
    save_runtime_profile(profile_name, config)
    with _registered_profile(profile_name):
        root = tmp_path / "orgs" / "alpha"
        org = _seed_org(root, "alpha", {"a": profile_name})
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()
        before = _pointer(org)
        operation = _ProfileOperation(
            operation_id=f"WPO-{boundary}",
            profile_name=profile_name,
            operation_kind="rebind",
            members=("alpha",),
            target_generation=2,
            prior=coordinator._effective_profile(profile_name),
        )
        invocation = f"recovery-{boundary}"
        coordinator._insert_operation_rows(
            operation, coordinator_invocation=invocation,
        )
        fence_span = None
        binding = None
        if boundary != "captured":
            fence_span = org.workflow_authority.profile_change_interval(
                reason=f"test:{boundary}", coordinator_invocation=invocation,
            )
            binding = fence_span.__enter__()
            coordinator._set_operation_state(operation, "fenced")
        if boundary in {
            "store_committed", "registry_committed", "canonical_published",
        }:
            config = {**config, "workspace_adapter_id": "codex"}
            save_runtime_profile(profile_name, config)
            get_registry().replace_custom_profile(ExecutorProfile(
                name=profile_name,
                kind="custom",
                workspace_adapter_id="codex",
                command_adapter_id=f"custom-adapter:{profile_name}-adapter",
            ))
            effective = coordinator._effective_profile(profile_name)
            coordinator._apply_store_state(operation, effective)
            if boundary in {"registry_committed", "canonical_published"}:
                assert coordinator._apply_registry_state(operation, effective)
            if boundary == "canonical_published":
                assert binding is not None
                org.workflow_authority.publish_profile_change(
                    publisher="test-crash-after-canonical", binding=binding,
                )
        if fence_span is not None:
            fence_span.__exit__(None, None, None)
        org.close()

        cold_org = OrgState.load(slug="alpha", root=root, settings=Settings())
        cold = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": cold_org})
        cold.reconcile_startup()
        after_once = _pointer(cold_org)
        row = cold_org.db.execute(
            "SELECT state FROM workflow_profile_operations WHERE id=?",
            (operation.operation_id,),
        ).fetchone()
        assert row is not None and row["state"] == expected_terminal
        assert after_once[1] == "ready"
        assert after_once[0] == before[0] + (boundary != "captured")

        ProfileCoordinator(
            daemon_home=daemon_home, orgs={"alpha": cold_org},
        ).reconcile_startup()
        assert _pointer(cold_org) == after_once
        cold_org.close()


def test_publication_failure_leaves_dependent_org_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    profile_name = "u2b-failure"
    save_runtime_profile(
        profile_name,
        {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{profile_name}-adapter",
        },
    )
    with _registered_profile(profile_name):
        org = _seed_org(tmp_path / "orgs" / "alpha", "alpha", {"a": profile_name})
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()

        def fail_profile_publish(*_args, **_kwargs):
            raise OSError("injected profile publication failure")

        monkeypatch.setattr(org.workflow_authority, "publish_profile_change", fail_profile_publish)
        with coordinator.operation(
            [profile_name], operation_kind="rebind", publisher="test-failure",
        ):
            pass

        assert _pointer(org)[1] == "fenced"
        with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
            org.workflow_authority.verify_admission_ready()
        operation = org.db.execute(
            "SELECT state FROM workflow_profile_operations ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert operation is not None
        assert operation["state"] == "forward_recovery_required"
        org.close()


def test_dependency_rebind_changes_only_one_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    source = "u2b-source"
    target = "u2b-target"
    for name in (source, target):
        save_runtime_profile(
            name,
            {
                "workspace_adapter_id": "pi",
                "command_adapter_id": f"custom-adapter:{name}-adapter",
            },
        )
    with _registered_profile(source), _registered_profile(target):
        org = _seed_org(
            tmp_path / "orgs" / "alpha",
            "alpha",
            {"worker_a": source, "worker_b": source},
        )
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()

        with coordinator.dependency_writer([source, target]):
            coordinator.rebind_consumer(
                org=org,
                consumer_identity="worker_a",
                from_profile=source,
                to_profile=target,
            )

        rows = [
            tuple(row)
            for row in org.db.execute(
                "SELECT profile_name,consumer_identity,state "
                "FROM workflow_profile_dependencies ORDER BY consumer_identity,profile_name"
            ).fetchall()
        ]
        assert rows == [
            (source, "worker_a", "removed"),
            (target, "worker_a", "active"),
            (source, "worker_b", "active"),
        ]
        org.close()


def test_removed_profile_stays_fenced_until_explicit_consumer_rebind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    source = "u2b-removed-source"
    target = "u2b-live-target"
    for name in (source, target):
        save_runtime_profile(name, {
            "workspace_adapter_id": "pi",
            "command_adapter_id": f"custom-adapter:{name}-adapter",
        })
    with _registered_profile(source), _registered_profile(target):
        root = tmp_path / "orgs" / "alpha"
        org = _seed_org(root, "alpha", {"worker_a": source})
        coordinator = ProfileCoordinator(daemon_home=daemon_home, orgs={"alpha": org})
        coordinator.reconcile_startup()

        with coordinator.operation(
            [source], operation_kind="remove", publisher="test-remove",
        ):
            remove_runtime_profile(source)
            get_registry().unregister_custom_profile(source)

        assert _pointer(org)[1] == "fenced"
        outstanding = org.db.execute(
            "SELECT state FROM workflow_profile_dependencies "
            "WHERE profile_name=? AND consumer_identity='worker_a'",
            (source,),
        ).fetchone()
        assert outstanding is not None and outstanding["state"] == "unbound"

        paths = OrgPaths(root=root)
        current = prompt_loader.load_agent(paths, "worker_a")
        assert current is not None
        replacement = AgentDef(
            name=current.name,
            team=current.team,
            role=current.role,
            executor=target,
            allow_rules=current.allow_rules,
            repos=current.repos,
            enrolled_by=current.enrolled_by,
            enrolled_at_task=current.enrolled_at_task,
            enrolled_at=current.enrolled_at,
            system_prompt=current.system_prompt,
            description=current.description,
        )
        with coordinator.dependency_writer([source, target]):
            with org.workflow_authority.writer_interval(
                publisher="test-explicit-rebind",
            ) as authority_change:
                with authority_change.canonical_change():
                    (paths.agents_dir / "worker_a.md").write_text(
                        render_agent_text(replacement)
                    )
                    coordinator.rebind_consumer(
                        org=org,
                        consumer_identity="worker_a",
                        from_profile=source,
                        to_profile=target,
                    )

        assert _pointer(org)[1] == "ready"
        rows = [
            tuple(row)
            for row in org.db.execute(
                "SELECT profile_name,state FROM workflow_profile_dependencies "
                "WHERE consumer_identity='worker_a' ORDER BY profile_name"
            ).fetchall()
        ]
        assert rows == [(target, "active"), (source, "removed")]
        org.close()
