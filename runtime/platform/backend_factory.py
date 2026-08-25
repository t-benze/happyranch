"""Capability-probe-based session backend selection (THR-207 Slice B).

This module is the **single selection point** that maps a host environment to
a :class:`SessionBackend`. Everything above it (the supervisor, admission,
residue accounting, receipts) branches on **capabilities**, never on OS
names; this is the one place the OS is inspected, and even here selection is
driven by an operational probe of the actual environment, not by
OS/version strings.

Selection rules (honest by construction):

1. A healthy Linux systemd/cgroup-v2 probe (real user-manager reachable,
   cgroup v2 controllers mounted, a transient probe scope created with
   applied limits, membership, counters, and empty-cgroup teardown verified)
   selects :class:`LinuxSystemdBackend`.
2. Otherwise, a healthy macOS-style probe (POSIX process-group creation/
   signaling verified AND an identity-safe census reader usable on the
   host) selects :class:`MacOSProcessGroupBackend`.
3. Anything else — unsupported platform, unhealthy probe, missing
   facilities — selects the **honest fallback** (:class:`PassthroughBackend`,
   every capability ``unavailable``). A fallback never fabricates a
   guarantee, a zero measurement, or a cleanup it did not verify.

``session_backend_for_wired_producer`` is the honest selection for the
daemon's currently-wired producer: schedule fires launch their executor
subprocess inside the executor's own Popen body (unwired in Slice B), which
no real containment backend can wrap — so the truthful backend for that
wiring is the no-enforcement passthrough. The real backends are selected
for containment-ready callers (integration suites, and the daemon once the
executor launch bodies are wired in a later slice).
"""

from __future__ import annotations

import logging
from typing import Callable

from runtime.platform.session_backend import SessionBackend

logger = logging.getLogger(__name__)


def select_session_backend(
    *,
    linux_backend: Callable[[], SessionBackend] | None = None,
    macos_backend: Callable[[], SessionBackend] | None = None,
    fallback: Callable[[], SessionBackend] | None = None,
) -> SessionBackend:
    """Probe the host and return the healthiest honest backend.

    Deterministic fakes are injectable for unit tests; production uses the
    real backend classes. Selection order: Linux systemd/cgroup v2 first,
    then macOS process-group, then the honest fallback."""
    from runtime.platform.linux_systemd import LinuxSystemdBackend
    from runtime.platform.macos_process_group import MacOSProcessGroupBackend
    from runtime.platform.passthrough_backend import PassthroughBackend

    candidates = (
        ("linux", linux_backend or LinuxSystemdBackend),
        ("macos", macos_backend or MacOSProcessGroupBackend),
    )
    for name, factory in candidates:
        try:
            backend = factory()
            report = backend.probe()
        except Exception as exc:  # noqa: BLE001 — a broken probe selects fallback
            logger.warning("host backend %r probe raised: %s", name, exc)
            continue
        if report.healthy and report.capabilities:
            logger.info(
                "selected host backend %r (healthy, %d capability level(s))",
                report.backend,
                len(report.capabilities),
            )
            return backend
        logger.info(
            "host backend %r unhealthy (%s); trying next",
            name,
            report.reason or report.evidence[:200],
        )
    fallback_backend = (fallback or PassthroughBackend)()
    logger.info(
        "selected honest fallback backend %r (all capabilities unavailable)",
        getattr(fallback_backend, "name", "passthrough"),
    )
    return fallback_backend


def session_backend_for_wired_producer() -> SessionBackend:
    """The honest backend for the daemon's currently-wired producer.

    Schedule fires (the single wired producer) perform their own subprocess
    launch inside the executor launch body; no real containment backend can
    wrap that subprocess until the executor launch bodies are wired (a
    later slice). The truthful selection for this wiring is the
    no-enforcement passthrough — never a real backend that would silently
    leave the actual subprocess uncontained while reporting guaranteed
    capabilities."""
    from runtime.platform.passthrough_backend import PassthroughBackend

    return PassthroughBackend()
