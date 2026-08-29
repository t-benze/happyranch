"""Daemon construction path for the host-resource supervisor (THR-207 Slice B).

The daemon-wide supervisor's backend is selected through the capability
factory: the currently-wired producer (schedule fires) performs its own
subprocess launch inside the executor launch body, so the truthful backend
for that wiring is the honest no-enforcement passthrough — the factory is
the single selection point that later slices use when the executor launch
bodies are wired.
"""
from __future__ import annotations

import pytest

from runtime.platform.session_backend import Capability, CapabilityLevel


def test_build_default_host_supervisor_selects_factory_backend():
    from runtime.orchestrator.host_supervisor import (
        build_default_host_supervisor,
    )

    supervisor = build_default_host_supervisor()
    report = supervisor.probe()
    # The wired producer's launch body cannot be contained until the executor
    # bodies are wired — the honest selection reports NO capability (missing
    # enforcement tightens admission via the binding macOS canary cap of 4).
    assert report.capabilities == {}
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.UNAVAILABLE
    assert report.level(Capability.KILLS_TREE_GUARANTEED) is CapabilityLevel.UNAVAILABLE
    assert supervisor._admission.cap() == 4  # binding cap applies (no enforcement)


def test_build_default_host_supervisor_accepts_explicit_backend():
    from runtime.orchestrator.host_supervisor import (
        build_default_host_supervisor,
        canary_policy,
    )
    from runtime.platform.passthrough_backend import PassthroughBackend

    backend = PassthroughBackend()
    supervisor = build_default_host_supervisor(backend=backend)
    assert supervisor._backend is backend
    # The default policy snapshot is the canary inputs (ignoring the
    # per-construction timestamp).
    assert supervisor._policy.global_session_cap == canary_policy().global_session_cap
    assert supervisor._policy.macos_binding_cap == canary_policy().macos_binding_cap


def test_state_from_runtime_constructs_supervisor(tmp_path, monkeypatch):
    """The daemon's state construction wires the daemon-wide supervisor with
    the capability-factory-selected backend and the configured 429 retry
    schedule (THR-207 task-producer wiring)."""
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.runtime import RuntimeDir

    # Deterministic backend selection: the honest no-enforcement passthrough
    # (a real Linux/macOS backend is selected on capable hosts; the wiring
    # contract is that the factory choice flows through unchanged).
    import runtime.platform.backend_factory as backend_factory_mod

    monkeypatch.setattr(
        backend_factory_mod, "select_session_backend", PassthroughBackend,
    )
    runtime = RuntimeDir.init(tmp_path / "rt")
    settings = Settings()
    state = DaemonState.from_runtime(runtime, settings)
    assert state.host_supervisor is not None
    assert isinstance(state.host_supervisor._backend, PassthroughBackend)
    # The configured 429 schedule is wired as the supervisor-level retry
    # (finish/release/sleep/reacquire with original age + fresh handle).
    assert state.host_supervisor._max_retry_attempts == len(
        settings.executor_rate_limit_backoff_seconds
    )
    assert state.host_supervisor._backoff_seconds == tuple(
        settings.executor_rate_limit_backoff_seconds
    )
    assert state.host_supervisor._policy.global_session_cap == 13
    assert state.host_supervisor._policy.producer_envelope == 13
    # Settings are startup snapshots. Mutating this test instance cannot
    # hot-resize the already-constructed admission controller.
    settings.host_global_session_cap = 11
    settings.queue_workers = 4
    assert state.host_supervisor._admission.cap() == 4  # fallback stays conservative
    assert state.host_supervisor._policy.global_session_cap == 13
    # Every loaded org's orchestrator is wired to the daemon-wide supervisor.
    for org in state.orgs.values():
        assert org.orchestrator._host_supervisor is state.host_supervisor


def test_state_capacity_override_supports_clean_rollback(tmp_path, monkeypatch):
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.runtime import RuntimeDir
    import runtime.platform.backend_factory as backend_factory_mod

    monkeypatch.setattr(backend_factory_mod, "select_session_backend", PassthroughBackend)
    state = DaemonState.from_runtime(
        RuntimeDir.init(tmp_path / "rt"),
        Settings(queue_workers=4, host_global_session_cap=11),
    )
    assert state.host_supervisor._policy.producer_envelope == 11
    assert state.host_supervisor._policy.global_session_cap == 11
    assert state.host_supervisor._admission.cap() == 4
