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
    """The daemon's state construction wires the daemon-wide supervisor."""
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir

    runtime = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(runtime, Settings())
    assert state.host_supervisor is not None
    report = state.host_supervisor.probe()
    assert report.capabilities == {}  # honest fallback for the wired producer
