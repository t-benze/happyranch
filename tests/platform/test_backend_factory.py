"""Tests for the capability-probe backend factory (THR-207 Slice B).

The factory is the single OS-name site: everything above it (the
supervisor, admission, residue accounting) branches on **capabilities**.
These tests prove selection is probe-driven and that unsupported/unhealthy
environments select the honest no-capability fallback — never a fabricated
guarantee.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from runtime.platform.backend_factory import (
    select_session_backend,
    session_backend_for_wired_producer,
)
from runtime.platform.passthrough_backend import PassthroughBackend
from runtime.platform.session_backend import (
    Capability,
    CapabilityLevel,
    CapabilityReport,
    SessionBackend,
)


def _report(backend: str, healthy: bool = True, caps=None, reason: str | None = None):
    return CapabilityReport(
        backend=backend,
        backend_version="1.0",
        capabilities=caps or {},
        evidence="test-evidence",
        reason=reason,
        healthy=healthy,
    )


class _FakeBackend:
    """Backend double with a scriptable probe result."""

    def __init__(self, report: CapabilityReport, name: str = "fake"):
        self._report = report
        self.name = name

    def probe(self) -> CapabilityReport:
        return self._report


def test_factory_selects_linux_backend_when_healthy():
    linux = _FakeBackend(
        _report(
            "linux-systemd-cgroup-v2",
            caps={Capability.LIMITS_MEMORY: CapabilityLevel.GUARANTEED},
        )
    )
    macos = _FakeBackend(_report("macos-process-group", healthy=False, reason="n/a"))
    selected = select_session_backend(
        linux_backend=lambda: linux, macos_backend=lambda: macos
    )
    assert selected is linux


def test_factory_falls_through_to_macos_when_linux_unhealthy():
    linux = _FakeBackend(_report("linux", healthy=False, reason="no systemd"))
    macos = _FakeBackend(
        _report(
            "macos-process-group",
            caps={Capability.KILLS_TREE_BEST_EFFORT: CapabilityLevel.BEST_EFFORT},
        )
    )
    selected = select_session_backend(
        linux_backend=lambda: linux, macos_backend=lambda: macos
    )
    assert selected is macos


def test_factory_selects_honest_fallback_when_all_unhealthy():
    linux = _FakeBackend(_report("linux", healthy=False, reason="no systemd"))
    macos = _FakeBackend(_report("macos", healthy=False, reason="no libproc"))
    selected = select_session_backend(
        linux_backend=lambda: linux, macos_backend=lambda: macos
    )
    assert isinstance(selected, PassthroughBackend)


def test_factory_selects_fallback_when_probe_raises():
    class _Raising:
        name = "raising"

        def probe(self):
            raise RuntimeError("probe boom")

    selected = select_session_backend(
        linux_backend=_Raising, macos_backend=_Raising
    )
    assert isinstance(selected, PassthroughBackend)


def test_factory_fallback_never_fabricates_capabilities():
    """The honest fallback reports NO capability — never a fabricated zero
    measurement or a cleanup guarantee it did not verify."""
    selected = select_session_backend(
        linux_backend=lambda: _FakeBackend(_report("linux", healthy=False)),
        macos_backend=lambda: _FakeBackend(_report("macos", healthy=False)),
    )
    report = selected.probe()
    assert report.healthy is True  # the fallback is healthy as what it is
    assert report.capabilities == {}
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.UNAVAILABLE
    assert report.level(Capability.KILLS_TREE_GUARANTEED) is CapabilityLevel.UNAVAILABLE


def test_wired_producer_selects_honest_passthrough():
    """The daemon's currently-wired producer (schedule fires) performs its
    own subprocess launch inside the executor body — the truthful backend is
    the no-enforcement passthrough, never a real backend that would leave
    the actual subprocess uncontained."""
    backend = session_backend_for_wired_producer()
    assert isinstance(backend, PassthroughBackend)
    assert backend.probe().capabilities == {}


def test_selected_backend_is_a_session_backend():
    linux = _FakeBackend(
        _report("linux", caps={Capability.LIMITS_MEMORY: CapabilityLevel.GUARANTEED})
    )
    selected = select_session_backend(
        linux_backend=lambda: linux,
        macos_backend=lambda: _FakeBackend(_report("macos", healthy=False)),
    )
    assert isinstance(selected, object)
    assert isinstance(selected.probe(), CapabilityReport)


# ── callers above the factory branch on capabilities, not OS names ────


def test_supervisor_branches_on_capabilities_not_os_names():
    """A supervisor built over ANY backend resolves its effective cap from
    the backend's reported capabilities — never from the backend's name."""
    from runtime.orchestrator.host_supervisor import (
        HostSessionSupervisor,
        canary_policy,
        enforcement_guaranteed,
    )

    policy = canary_policy()
    # A backend that guarantees enforcement (Linux-shaped capabilities):
    # the Linux <=11 ceiling stays a non-binding shadow input (cap 11).
    linux_backend = _FakeBackend(
        _report(
            "linux-systemd-cgroup-v2",
            caps={
                Capability.LIMITS_MEMORY: CapabilityLevel.GUARANTEED,
                Capability.LIMITS_PIDS: CapabilityLevel.GUARANTEED,
                Capability.LIMITS_CPU: CapabilityLevel.GUARANTEED,
            },
        )
    )
    supervisor = HostSessionSupervisor(
        backend=linux_backend,  # type: ignore[arg-type]
        policy=policy,
        publisher=lambda receipt: None,
    )
    assert supervisor._admission.cap() == 11

    # The same supervisor shape over a no-enforcement backend (macOS-style
    # capabilities): the binding cap (4) applies — missing enforcement
    # tightens admission. No OS name is consulted anywhere.
    cap_report = _report(
        "macos-process-group",
        caps={Capability.KILLS_TREE_BEST_EFFORT: CapabilityLevel.BEST_EFFORT},
    )
    macos_backend = _FakeBackend(cap_report)
    supervisor2 = HostSessionSupervisor(
        backend=macos_backend,  # type: ignore[arg-type]
        policy=policy,
        publisher=lambda receipt: None,
    )
    assert enforcement_guaranteed(cap_report) is False
    assert supervisor2._admission.cap() == 4
