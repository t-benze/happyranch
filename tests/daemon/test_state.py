from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import os

import pytest
import yaml

from runtime.config import Settings
from runtime.daemon import paths, runtimes
from runtime.daemon.__main__ import _build_state
from runtime.daemon.state import DaemonState
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_registry import (
    get_registry,
    reset_registry,
)
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()


def test_from_runtime_loads_all_orgs(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    _seed_org(rt.orgs_dir / "_pending")  # reserved, must be skipped
    state = DaemonState.from_runtime(rt, Settings())
    assert sorted(state.orgs.keys()) == ["alpha", "beta"]


def test_get_org_unknown_raises(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    with pytest.raises(KeyError):
        state.get_org("does-not-exist")


@pytest.mark.asyncio
async def test_add_org_idempotent(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    org_a1 = await state.add_org("alpha")
    org_a2 = await state.add_org("alpha")
    assert org_a1 is org_a2  # same instance, not reloaded


def _seed_drifted_org(org_root: Path) -> None:
    """Org with an active manager that teams.yaml doesn't know — the family-org bug."""
    _seed_org(org_root)
    paths = OrgPaths(root=org_root)
    manager = AgentDef(
        name="solo_manager",
        team="missing_team",
        role="manager",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt="You are solo.\n",
        description="Solo",
    )
    (paths.agents_dir / "solo_manager.md").write_text(render_agent_text(manager))


def test_from_runtime_skips_broken_org(tmp_path: Path) -> None:
    """One broken org must not crash daemon startup. Others stay loaded."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    assert "alpha" in state.orgs
    assert "broken" not in state.orgs
    assert "broken" in state.broken_orgs
    assert "missing_team" in state.broken_orgs["broken"]


@pytest.mark.asyncio
async def test_add_org_propagates_consistency_error(tmp_path: Path) -> None:
    """Explicit add (e.g. founder action) must surface the error, not swallow."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    # The from_runtime path skipped it — broken_orgs holds the error.
    assert "broken" in state.broken_orgs
    # Direct add_org must surface it.
    with pytest.raises(OrgConsistencyError):
        await state.add_org("broken")


@pytest.mark.asyncio
async def test_add_org_clears_broken_on_success(tmp_path: Path) -> None:
    """After fixing teams.yaml on disk, a successful add_org clears the broken entry."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    assert "broken" in state.broken_orgs
    # Founder fixes teams.yaml
    (rt.orgs_dir / "broken" / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  missing_team:\n"
        "    manager: solo_manager\n"
        "    workers: []\n"
    )
    org = await state.add_org("broken")
    assert org.slug == "broken"
    assert "broken" not in state.broken_orgs
    assert "broken" in state.orgs


def test_from_runtime_loads_good_profile_after_bad_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """A bad persisted profile must not prevent a valid later one from
    loading at daemon startup. This is the durable-store read/write-
    symmetry invariant: a profile that registered successfully at
    write time must not silently disappear because of an unrelated
    sibling."""
    # Seed daemon home with a bad-then-good executor_profiles.yaml
    daemon_home = tmp_path / ".happyranch"
    daemon_home.mkdir(parents=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

    store_path = daemon_home / "executor_profiles.yaml"
    # Bad profile first (command never on PATH), good profile second
    store_path.write_text(yaml.safe_dump({
        "bad-profile": {
            "command": "no-such-command-on-any-machine-xyzzy",
            "argv_template": ["no-such-command-on-any-machine-xyzzy", "{prompt}"],
            "adapter": "pi",
        },
        "good-profile": {
            "command": "python3",
            "argv_template": ["python3", "{prompt}"],
            "adapter": "pi",
        },
    }))

    # Ensure clean registry state
    reset_registry()
    try:
        rt = RuntimeDir.init(tmp_path / "rt")
        _seed_org(rt.orgs_dir / "alpha")
        state = DaemonState.from_runtime(rt, Settings())

        # Org loaded normally
        assert "alpha" in state.orgs

        # Good profile is registered — the bad earlier profile did NOT
        # prevent it from loading.
        registry = get_registry()
        assert registry.is_registered("good-profile"), (
            "valid profile after a bad one must still be registered"
        )
        assert not registry.is_registered("bad-profile"), (
            "bad profile must not be registered"
        )
    finally:
        reset_registry()


# ---------------------------------------------------------------------------
# _build_state auto-provision tests (THR-088 / TASK-2694)
# ---------------------------------------------------------------------------


class TestBuildStateAutoProvision:
    """When no active runtime is registered, _build_state must auto-provision
    a default runtime instead of going idle."""

    def test_empty_registry_auto_creates_default_runtime(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Empty registry (no runtimes.yaml) -> _build_state creates a default
        runtime at daemon_home/runtime, registers + activates it, and returns
        a runtime-backed state."""
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

        # Ensure clean state: no runtimes.yaml exists
        runtimes_path = daemon_home / "runtimes.yaml"
        assert not runtimes_path.exists(), "precondition: no runtimes.yaml"

        state = _build_state(Settings())

        # State is NOT idle — runtime is set
        assert state.runtime is not None, (
            "_build_state must auto-provision a runtime instead of going idle"
        )
        assert not state.is_idle

        # A default runtime dir was created at daemon_home/runtime
        default_path = daemon_home / "runtime"
        assert default_path.is_dir(), (
            f"expected default runtime at {default_path}"
        )
        assert (default_path / "orgs").is_dir()
        assert (default_path / "happyranch.yaml").is_file()

        # The marker has schema_version 2
        import yaml as _yaml
        marker = _yaml.safe_load((default_path / "happyranch.yaml").read_text())
        assert marker["schema_version"] == 2
        assert marker["type"] == "multi-org-runtime"

        # runtimes.yaml now exists and lists the default runtime as active
        assert runtimes_path.exists(), "runtimes.yaml must be written"
        reg = runtimes.load()
        assert reg.active is not None
        assert reg.active.resolve() == default_path.resolve()
        assert default_path.resolve() in [p.resolve() for p in reg.registered]

    def test_registered_but_no_active_activates_existing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When runtimes.yaml has registered runtimes but active is None,
        _build_state activates the first registered one without creating
        a new directory."""
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

        # Create an existing runtime at a non-default path
        existing_path = tmp_path / "my-custom-runtime"
        RuntimeDir.init(existing_path)

        # Write runtimes.yaml with the existing runtime registered but NO active
        runtimes_path = daemon_home / "runtimes.yaml"
        runtimes_path.write_text(yaml.dump({
            "active": None,
            "registered": [str(existing_path.resolve())],
        }))

        # Record what directories exist before _build_state
        dirs_before = {p.name for p in daemon_home.iterdir() if p.is_dir()}

        state = _build_state(Settings())

        # State is NOT idle
        assert state.runtime is not None
        assert not state.is_idle

        # The active runtime is the existing one, NOT a new default
        reg = runtimes.load()
        assert reg.active is not None
        assert reg.active.resolve() == existing_path.resolve()

        # No new runtime directory was created under daemon_home
        default_path = daemon_home / "runtime"
        assert not default_path.exists(), (
            "must not create a duplicate default runtime when a registered "
            "one exists — activate the existing one instead"
        )

    def test_already_active_startup_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When an active runtime is already set, _build_state loads it
        normally and does NOT change anything."""
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

        # Create a runtime and register + activate it (normal state)
        rt_path = tmp_path / "my-runtime"
        RuntimeDir.init(rt_path)
        runtimes.register(rt_path)  # both registers and activates

        # Confirm precondition: active is set
        reg_before = runtimes.load()
        assert reg_before.active is not None
        assert reg_before.active.resolve() == rt_path.resolve()

        state = _build_state(Settings())

        # State is NOT idle
        assert state.runtime is not None
        assert not state.is_idle
        assert state.runtime.root.resolve() == rt_path.resolve()

        # No new directory was created
        default_path = daemon_home / "runtime"
        assert not default_path.exists(), (
            "must not create a default runtime when one is already active"
        )

        # Active pointer unchanged
        reg_after = runtimes.load()
        assert reg_after.active.resolve() == rt_path.resolve()


# ── THR-107: one-shot migration of legacy per-org executor_profiles ─────


def _write_org_config(org_root: Path, body: str) -> None:
    (org_root / "org" / "config.yaml").write_text(body)


def _agent_with_executor(org_root: Path, executor: str) -> None:
    """Seed an engineering team + an active agent declaring ``executor``."""
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_manager\n"
    )
    agent = AgentDef(
        name="dev_agent",
        team="engineering",
        role="worker",
        executor=executor,
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        system_prompt="You are the dev agent.\n",
        description="Dev agent",
    )
    (OrgPaths(root=org_root).agents_dir / "dev_agent.md").write_text(
        render_agent_text(agent)
    )


def test_from_runtime_migrates_legacy_block_and_registers_same_boot(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """THR-107 upgrade-boot guarantee: an org whose config.yaml still
    carries a legacy executor_profiles block AND whose agents declare that
    executor must load on the FIRST post-upgrade boot. The migration lifts
    the block into the machine-global runtime store BEFORE the store is
    loaded, so the profile registers in the same boot."""
    import logging

    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    reset_registry()
    try:
        rt = RuntimeDir.init(tmp_path / "rt")
        org_root = rt.orgs_dir / "alpha"
        _seed_org(org_root)
        _write_org_config(org_root, """
executor_profiles:
  openclaw:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "{prompt}"
""")
        _agent_with_executor(org_root, "openclaw")

        with caplog.at_level(logging.WARNING):
            state = DaemonState.from_runtime(rt, Settings())

        # Org loaded on the upgrade boot — NOT broken.
        assert "alpha" in state.orgs, state.broken_orgs

        # Profile registered in the same boot (via the runtime store).
        assert get_registry().is_registered("openclaw")

        # Durably lifted into the machine-global runtime store.
        store_path = daemon_home / "executor_profiles.yaml"
        assert store_path.exists()
        store = yaml.safe_load(store_path.read_text())
        assert store["openclaw"]["command"] == "echo"

        # Loud deprecation warning names the org and the migrated entry.
        assert "openclaw" in caplog.text
        assert "alpha" in caplog.text
        assert (
            "deprecat" in caplog.text.lower()
            or "removed" in caplog.text.lower()
        )
    finally:
        reset_registry()


def test_from_runtime_migration_collision_logs_skips_and_boots_both_orgs(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """THR-107 collision edge: two orgs carry the SAME legacy profile name
    with DIFFERENT definitions. The first lift wins; the conflicting one
    is logged + skipped; the daemon still boots BOTH orgs (no crash, no
    broken org)."""
    import logging

    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    reset_registry()
    try:
        rt = RuntimeDir.init(tmp_path / "rt")
        for slug, command in (("alpha", "echo"), ("beta", "printf")):
            org_root = rt.orgs_dir / slug
            _seed_org(org_root)
            _write_org_config(org_root, f"""
executor_profiles:
  shared:
    command: {command}
    adapter: pi
    argv_template:
      - {command}
      - "{{prompt}}"
""")

        with caplog.at_level(logging.WARNING):
            state = DaemonState.from_runtime(rt, Settings())

        # BOTH orgs boot — the collision never crashes an org load.
        assert sorted(state.orgs.keys()) == ["alpha", "beta"]
        assert state.broken_orgs == {}

        # Exactly one definition wins (first lift in iteration order);
        # the other is skipped with a warning.
        store = yaml.safe_load(
            (daemon_home / "executor_profiles.yaml").read_text()
        )
        assert store["shared"]["command"] in ("echo", "printf")
        registered = get_registry().get_profile("shared")
        assert registered is not None
        assert registered.command == store["shared"]["command"]
        assert "shared" in caplog.text
        assert "skip" in caplog.text.lower()
    finally:
        reset_registry()


def test_add_org_lifts_legacy_block_without_registering(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """THR-107 strand-proofing for orgs attached AFTER daemon boot: add_org
    lifts a legacy block into the runtime store (loud, durable) but does
    NOT register it into the process registry — the store load at next
    daemon startup (or an explicit runtime registration) activates it."""
    import asyncio
    import logging

    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    reset_registry()
    try:
        rt = RuntimeDir.init(tmp_path / "rt")
        state = DaemonState.from_runtime(rt, Settings())

        # Org appears on disk AFTER boot (init/re-attach path).
        org_root = rt.orgs_dir / "lateorg"
        _seed_org(org_root)
        _write_org_config(org_root, """
executor_profiles:
  lateclaw:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "{prompt}"
""")

        with caplog.at_level(logging.WARNING):
            asyncio.run(state.add_org("lateorg"))

        assert "lateorg" in state.orgs
        # Lifted durably…
        store = yaml.safe_load(
            (daemon_home / "executor_profiles.yaml").read_text()
        )
        assert "lateclaw" in store
        # …but NOT registered by the org path (store load owns that).
        assert not get_registry().is_registered("lateclaw")
        assert "lateclaw" in caplog.text
    finally:
        reset_registry()
