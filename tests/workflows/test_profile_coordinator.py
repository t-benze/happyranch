from __future__ import annotations

import json
import multiprocessing
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_registry import ExecutorProfile, get_registry
from runtime.orchestrator.runtime_executor_store import (
    remove_runtime_profile,
    save_runtime_profile,
)
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
    registry = get_registry()
    previous = registry.get_profile(name)
    profile = ExecutorProfile(
        name=name,
        kind="custom",
        workspace_adapter_id=workspace,
        command_adapter_id=f"custom-adapter:{name}-adapter",
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
