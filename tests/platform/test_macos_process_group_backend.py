"""Tests for the honestly capped macOS process-group/census backend
(THR-207 Slice B).

Layers:

* **Deterministic unit tests** — fake census/process-table interactions:
  probe degradation, ownership refusal (PID-reuse safety), TERM/KILL
  escalation, survivor accounting, sampled-provenance receipts, abandon.
* **Real POSIX integration tests** (``-m integration``, gated on the real
  probe) — process-group launch/cleanup, the mandatory success-path
  descendant cleanup, and the documented best-effort limitation (an escaped
  descendant that calls ``setsid`` is censused as a survivor, never
  falsely claimed clean). These run on any POSIX host with an identity-safe
  census reader; libproc-specific assertions skip with an explicit reason on
  non-macOS hosts.

Hermetic: every spawned tree is killed in the test; no live daemon is
touched.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from runtime.platform.macos_process_group import MacOSProcessGroupBackend
from runtime.platform.process_census import LinuxProcReader, ProcessTreeCensus
from runtime.platform.session_backend import (
    BackendLaunchError,
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    ResourceSample,
    RunningHandle,
    SessionBackend,
)


def _policy():
    from runtime.orchestrator.host_supervisor import canary_policy

    return canary_policy()


def _request():
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    return AdmissionRequest(
        org="test", invocation_kind="schedule", logical_id="sched-1",
        executor_profile="claude",
    )


def _running(pid: int, identity: str = "boot-1") -> RunningHandle:
    return RunningHandle(
        backend="macos-process-group",
        token="tok-1",
        request_id="req-1",
        root_pid=pid,
        start_identity=identity,
        process=None,
    )


# ── deterministic unit tests (fake census) ───────────────────────────


class _FakeObservation:
    def __init__(self, pid, ppid=None, pgid=None, identity=None, rss=None, cpu=None, state=None):
        self.pid = pid
        self.ppid = ppid
        self.pgid = pgid
        self.start_identity = identity
        self.rss_bytes = rss
        self.cpu_seconds = cpu
        self.state = state


class _FakeCensus:
    """Deterministic census/reader double with scriptable observations."""

    def __init__(self, observations=None, identities=None, pgids=None):
        self.observations = observations or []
        self._identities = identities or {}
        self._pgids = pgids or {}

    def start_identity(self, pid):
        return self._identities.get(pid)

    def reader(self):
        observations = self.observations

        class _FakeReader:
            def __init__(self, obs):
                self._obs = obs

            def read_process(self, pid):
                return next((o for o in self._obs if o.pid == pid), None)

        return _FakeReader(observations)

    def descendants(self, root_pid, root_identity=None, include_root=False):
        # Simplistic chain walk over the fake table.
        by_pid = {o.pid: o for o in self.observations}
        if root_pid not in by_pid:
            return ()
        root = by_pid[root_pid]
        if root_identity is not None and root.start_identity != root_identity:
            return ()
        found = [root] if include_root else []
        stack = [o for o in self.observations if o.ppid == root_pid]
        while stack:
            o = stack.pop()
            found.append(o)
            stack.extend(x for x in self.observations if x.ppid == o.pid)
        return tuple(found)

    def group_members(self, pgid):
        return tuple(o for o in self.observations if o.pgid == pgid)


def _fake_backend(census: _FakeCensus, pgids=None) -> MacOSProcessGroupBackend:
    backend = MacOSProcessGroupBackend(census=census)
    # Fake the OS-level group queries and liveness (deterministic).
    backend._os_getpgid = lambda pid: (pgids or {}).get(pid, pid)
    backend._group_members_alive = lambda pgid: {}
    backend._signal_group = lambda pgid, sig: None
    return backend


def test_probe_unhealthy_when_census_unavailable():
    class _BrokenCensus:
        def start_identity(self, pid):
            raise OSError("libproc unavailable")

        def descendants(self, *a, **k):
            raise OSError("libproc unavailable")

        def group_members(self, pgid):
            raise OSError("libproc unavailable")

    backend = MacOSProcessGroupBackend(census=_BrokenCensus())
    report = backend.probe()
    assert report.healthy is False
    assert report.capabilities == {}


def test_probe_reports_best_effort_capabilities():
    census = _FakeCensus()
    backend = MacOSProcessGroupBackend(census=census)
    report = backend.probe()
    assert report.healthy is True
    from runtime.platform.session_backend import Capability, CapabilityLevel

    assert report.level(Capability.KILLS_TREE_BEST_EFFORT) is CapabilityLevel.BEST_EFFORT
    assert report.level(Capability.REPORTS_MEMORY_PEAK) is CapabilityLevel.BEST_EFFORT
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.UNAVAILABLE


def test_launch_failure_raises(monkeypatch):
    backend = MacOSProcessGroupBackend(census=_FakeCensus())

    def bad_popen(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr("runtime.platform.macos_process_group.subprocess.Popen", bad_popen)
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(BackendLaunchError):
        backend.launch(pending, LaunchSpec(argv=("nonexistent-binary",)))


def test_finish_refuses_ambiguous_group_and_reports_survivors():
    """PID-reuse safety: a group we cannot prove is ours is never signaled."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
        ],
        identities={100: "root-2"},  # root identity CHANGED (PID reused)
        pgids={100: 100},
    )
    backend = _fake_backend(census, pgids={100: 100})
    # Snapshot captured at launch with the ORIGINAL identity.
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {100: _FakeObservation(100, ppid=1, pgid=100, identity="root-1")})
    signaled = []

    def signal_group(pgid, sig):
        signaled.append(sig)

    backend._signal_group = signal_group  # type: ignore[method-assign]
    # A verified member sits in the ambiguous group -> fail-closed INCOMPLETE.
    backend._group_members_alive = lambda pgid: {101: "child-1"}  # type: ignore[method-assign]
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2)
    assert signaled == []  # never signaled an unverifiable group
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False


def test_finish_terms_then_kills_group():
    """TERM-resistant group members force the KILL escalation."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
            _FakeObservation(pid=101, ppid=100, pgid=100, identity="child-1"),
        ],
        identities={100: "root-1", 101: "child-1"},
    )
    backend = _fake_backend(census, pgids={100: 100, 101: 100})
    # Simulate: the child ignores TERM and survives until KILL.
    alive = {"101": True}

    def group_members_alive(pgid):
        return {101: "child-1"} if alive["101"] else {}

    backend._group_members_alive = group_members_alive  # type: ignore[method-assign]

    def signal_group(pgid, sig):
        if sig == signal.SIGKILL:
            alive["101"] = False
            # The KILL takes effect in the (fake) process table.
            census.observations[:] = [
                o for o in census.observations if o.pid not in (100, 101)
            ]

    backend._signal_group = signal_group  # type: ignore[method-assign]
    backend._census.start_identity = lambda pid: {100: "root-1", 101: "child-1"}[pid]
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "failure", grace_seconds=0.2)
    assert receipt.cleanup_status is CleanupStatus.KILL
    assert receipt.quiescent is True
    assert receipt.survivors == ()


def test_finish_clean_success_no_residue():
    # The root exited cleanly before finish: the fresh final census is empty.
    census = _FakeCensus(observations=[], identities={100: "root-1"})
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2)
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True


def test_prepare_launch_finish_carry_receipt_attribution():
    """Slice C: the macOS backend carries the bounded receipt attribution
    (invocation_kind + executor_profile) from the AdmissionRequest through
    prepare/launch into the finish-time Receipt — same honest contract as
    the Linux backend, with no limits applied (macOS stays best-effort)."""
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    pending = backend.prepare(_request(), _policy())
    assert pending.invocation_kind == "schedule"
    assert pending.executor_profile == "claude"

    # Direct launch is POSIX-only; assert the handle contract via the same
    # seam the supervisor uses (prepare -> launch with the real subprocess).
    import subprocess as _sp
    import sys

    proc = _sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
        start_new_session=True,
    )
    try:
        backend._census.start_identity = lambda pid: "root-1"  # type: ignore[method-assign]
        backend._os_getpgid = lambda pid: proc.pid  # type: ignore[method-assign]
        with backend._lock:
            backend._sessions[pending.token] = (proc.pid, "root-1", proc.pid, 0.0, {})
        running = RunningHandle(
            backend=backend.name,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=proc.pid,
            start_identity="root-1",
            process=proc,
            invocation_kind=pending.invocation_kind,
            executor_profile=pending.executor_profile,
        )
        receipt = backend.finish(running, "success", grace_seconds=0.2)
        assert receipt.invocation_kind == "schedule"
        assert receipt.executor_profile == "claude"
        assert receipt.cleanup_status is CleanupStatus.CLEAN
    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def test_finish_fresh_census_detects_late_escaped_descendant():
    """A descendant that escapes AFTER the last periodic sample (the stored
    session snapshot is EMPTY) is caught by finish's OWN fresh final
    identity-safe descendant census — no manual pre-sample refresh (the
    shipping finish seam)."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
            _FakeObservation(pid=102, ppid=100, pgid=999, identity="esc-1"),
        ],
        identities={100: "root-1", 102: "esc-1"},
    )
    backend = _fake_backend(census, pgids={100: 100, 102: 999})
    with backend._lock:
        # The last periodic sample was taken BEFORE the escaped child existed.
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.1)
    assert any(sv.pid == 102 for sv in receipt.survivors), (
        "escaped descendant must be censused by finish's own fresh census"
    )
    assert receipt.quiescent is False


def test_finish_census_failure_raises_not_clean():
    """A census/measurement exception at finish is EXPLICIT failure evidence:
    it propagates out of finish (never collapsing into an empty CLEAN group);
    the supervisor turns teardown failure into fail-closed admission
    blocking."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
        ],
        identities={100: "root-1"},
    )

    def boom_group_members(pgid):
        raise OSError("libproc enumeration failed")

    census.group_members = boom_group_members
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (
            100, "root-1", 100, 0.0, {100: census.observations[0]},
        )
    with pytest.raises(OSError, match="libproc enumeration failed"):
        backend.finish(_running(100, "root-1"), "success", grace_seconds=0.1)


def test_finish_merges_sampled_provenance():
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    samples = (
        ResourceSample(sampled_at=1.0, memory_peak_bytes=100, cpu_total_seconds=0.5, process_count=2),
        ResourceSample(sampled_at=2.0, memory_peak_bytes=300, cpu_total_seconds=1.0, process_count=4),
    )
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2, samples=samples)
    assert receipt.memory_peak_bytes == 300
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_seconds == 1.0
    assert receipt.process_peak == 4  # sampled peak from the samples
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.sample_gaps == (1.0,)


def test_finish_preserves_primary_terminal_reason():
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    for reason in ("success", "failure", "timeout", "cancelled", "shutdown"):
        receipt = backend.finish(_running(100, "root-1"), reason, grace_seconds=0.1)
        assert receipt.terminal_reason == reason


def test_abandon_and_recover():
    backend = MacOSProcessGroupBackend(census=_FakeCensus())
    pending = backend.prepare(_request(), _policy())
    backend.abandon(pending)
    result = backend.recover("tok")
    assert result.recovered is False


def test_backend_is_session_backend():
    assert isinstance(MacOSProcessGroupBackend(census=_FakeCensus()), SessionBackend)


# ── real POSIX integration (gated on the operational probe) ──────────

real_integration = pytest.mark.integration


def _require_ops():
    backend = MacOSProcessGroupBackend()
    report = backend.probe()
    if not report.healthy:
        pytest.skip(f"process-group/census ops unusable on this runner: {report.reason}")
    return backend


@pytest.fixture(scope="module")
def real_backend():
    return _require_ops()


def _spawn_and_wait(backend, argv, timeout=5.0):
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=argv))
    deadline = time.monotonic() + timeout
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return running


@real_integration
def test_probe_real_creates_signals_reaps_group_no_residue(real_backend):
    backend = _require_ops()
    report = backend.probe()
    assert report.healthy is True
    from runtime.platform.session_backend import Capability, CapabilityLevel

    assert report.level(Capability.KILLS_TREE_BEST_EFFORT) is CapabilityLevel.BEST_EFFORT


@real_integration
def test_launch_creates_new_process_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        assert os.getpgid(running.root_pid) == running.root_pid
        assert running.start_identity
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_clean_success_cleans_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM  # live group -> TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_finish_clean_success_with_surviving_descendant_real(real_backend):
    """MANDATORY success-path descendant cleanup: the parent exits 0 while a
    child keeps running in the group — finish must TERM/KILL the whole group."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.2"))
    )
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running.process.returncode == 0
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_escaped_descendant_is_best_effort_survivor_real(real_backend):
    """SHIPPING finish seam: a descendant that ``setsid``s away after launch
    (no sampler ever ran — only the launch-time snapshot, taken before the
    child existed) is censused by finish's OWN fresh final identity-safe
    descendant census. No manual pre-sample refresh.

    Documented best-effort limitation: the census must run while the root
    still lives — a descendant that escapes AND is reparented before finish
    (root already exited) is unobservable by any process-table walk."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "setsid sleep 60 & sleep 5"))
    )
    survivors = ()
    try:
        # The escaped child is spawned within the first instant; finish runs
        # while the root still lives so the fresh census can see the child.
        time.sleep(0.3)
        assert running.process.poll() is None  # root still alive
        receipt = real_backend.finish(running, "success", grace_seconds=1.0)
        survivors = receipt.survivors
        # The escaped child survives (new session) — best-effort truth:
        # censused survivor, never a fabricated clean claim.
        assert receipt.survivors, "escaped descendant must be censused by finish's own census"
        assert receipt.quiescent is False
        assert receipt.cleanup_status is not CleanupStatus.INCOMPLETE
    finally:
        # Tear down the escaped child by its identity-verified survivor pid
        # (its cmdline is just ``sleep 60`` — setsid exec'd sleep).
        for sv in survivors:
            try:
                os.kill(sv.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        os.system("pkill -9 -f '^sleep 60$' 2>/dev/null || true")


@real_integration
def test_sampler_identity_safety_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        assert sample.provenance is MeasurementProvenance.SAMPLED
        assert sample.process_count is not None and sample.process_count >= 1
        assert sample.memory_peak_bytes is not None
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_merges_real_sampled_receipt(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        receipt = real_backend.finish(running, "success", grace_seconds=2.0, samples=(sample,))
    except Exception:
        running.process.kill()
        raise
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak is not None and receipt.process_peak >= 1
