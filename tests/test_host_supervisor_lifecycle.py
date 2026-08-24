"""Adversarial unit tests for HostSessionSupervisor lifecycle (Slice A).

Every row of the lifecycle truth table is exercised against dependency-
injected backend/measurement/publisher fakes:

- nothing launches before admission; queued cancel creates no handle;
- prepare/spawn failures clean partial state;
- success / nonzero / timeout / cancel / retry each freeze the terminal
  result, finish containment, perform capability-appropriate residue
  accounting/reconciliation, close the handle, publish the bounded receipt,
  and release the lease exactly once;
- cleanup errors never replace the primary terminal reason;
- finish/cancel races are idempotent;
- the generic pre-bind terminal handoff (CANCELLED or SHUTDOWN) replays at
  running-handle bind and refuses launch before the fence — no blocking-body
  entry, first-wins reason preserved, exactly-once finish/publish/release;
- guaranteed residue blocks admission until explicit reconciliation;
- best-effort verified survivors stay censused/charged/visible and block only
  on census/measurement failure or a conservative threshold;
- survivor exit / successful re-probe / operator acknowledgement-after-
  verified-cleanup are modeled recovery inputs;
- provenance distinguishes kernel/sampled/unavailable; cleanup duration and
  sampling gaps are represented;
- policy snapshot is immutable per invocation; cleanup grace is an injected
  canary input, not a universal constant.
"""
from __future__ import annotations

import threading
import time

import pytest

from runtime.orchestrator.host_supervisor import (
    AdmissionController,
    AdmissionRequest,
    AdmissionTimeout,
    CancellationToken,
    CapPolicy,
    HostSessionSupervisor,
    LaunchResult,
    PolicySnapshot,
    ResidueAccountant,
    _AttemptContext,
    _OpaqueCancelControl,
    canary_policy,
)
from runtime.platform.session_backend import (
    BackendFinishError,
    BackendLaunchError,
    BackendPrepareError,
    Capability,
    CapabilityLevel,
    CapabilityReport,
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    PendingHandle,
    Receipt,
    RecoveryResult,
    ResourceSample,
    RunningHandle,
    SurvivorRecord,
    TerminalReason,
)


# ── fakes ────────────────────────────────────────────────────────────


class FakeProcess:
    """Minimal stand-in for subprocess.Popen used by fake backends."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._terminated = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        return self._terminated.wait(timeout)

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0
        self._terminated.set()


class FakeBackend:
    """Configurable fake SessionBackend for deterministic lifecycle tests.

    ``finish`` terminates the fake process (mimicking tree teardown
    unblocking the executor's communicate loop) and returns a canned receipt
    (optionally merging supervisor-collected samples).
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        capabilities: dict[Capability, CapabilityLevel] | None = None,
        cleanup_status: CleanupStatus = CleanupStatus.CLEAN,
        survivors: tuple[SurvivorRecord, ...] = (),
        probe_raises: bool = False,
        prepare_raises: bool = False,
        launch_raises: bool = False,
        finish_raises: bool = False,
        finish_delay: float = 0.0,
        merge_samples: bool = False,
        kernel_provenance: bool = False,
        launch_entered: threading.Event | None = None,
        launch_release: threading.Event | None = None,
    ) -> None:
        self.name = name
        self.version = "1.0"
        self.capabilities = dict(capabilities or {})
        self.cleanup_status = cleanup_status
        self.survivors = survivors
        self.probe_raises = probe_raises
        self.prepare_raises = prepare_raises
        self.launch_raises = launch_raises
        self.finish_raises = finish_raises
        self.finish_delay = finish_delay
        self.merge_samples = merge_samples
        self.kernel_provenance = kernel_provenance
        # Deterministic backend-launch barrier: ``launch_entered`` is set as
        # soon as ``launch`` begins (after the launch fence passed); ``launch``
        # then blocks until ``launch_release``, letting a test cancel in the
        # fence-to-running-bind interval and prove the pending-cancel handoff.
        self.launch_entered = launch_entered
        self.launch_release = launch_release
        self.last_running: RunningHandle | None = None
        self.calls: dict[str, int] = {
            "probe": 0, "prepare": 0, "launch": 0, "finish": 0,
            "abandon": 0, "recover": 0, "sample": 0,
        }
        self.finish_reasons: list[str] = []
        self.finish_graces: list[float] = []
        self.prepare_policies: list[PolicySnapshot] = []
        self.lock = threading.Lock()

    # ── protocol ──────────────────────────────────────────────────

    def probe(self) -> CapabilityReport:
        with self.lock:
            self.calls["probe"] += 1
        if self.probe_raises:
            raise RuntimeError("probe boom")
        return CapabilityReport(
            backend=self.name,
            backend_version=self.version,
            capabilities=self.capabilities,
            evidence="fake-evidence",
            probed_at=time.monotonic(),
        )

    def prepare(self, request: AdmissionRequest, policy: PolicySnapshot) -> PendingHandle:
        with self.lock:
            self.calls["prepare"] += 1
            n = self.calls["prepare"]
            self.prepare_policies.append(policy)
        if self.prepare_raises:
            raise BackendPrepareError("prepare boom")
        return PendingHandle(
            backend=self.name, token=f"tok-{n}", request_id=request.logical_id
        )

    def launch(self, pending: PendingHandle, spec: LaunchSpec) -> RunningHandle:
        with self.lock:
            self.calls["launch"] += 1
            n = self.calls["launch"]
        if self.launch_raises:
            raise BackendLaunchError("launch boom")
        if self.launch_entered is not None:
            self.launch_entered.set()
        if self.launch_release is not None:
            # Block inside backend.launch (after the launch fence, before the
            # running handle is bound) until the test releases the barrier.
            assert self.launch_release.wait(timeout=10)
        proc = FakeProcess(pid=9000 + n)
        running = RunningHandle(
            backend=self.name,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=proc.pid,
            start_identity="boot-1",
            process=proc,
        )
        with self.lock:
            self.last_running = running
        return running

    def sample(self, running: RunningHandle) -> ResourceSample:
        with self.lock:
            self.calls["sample"] += 1
        return ResourceSample(
            sampled_at=time.monotonic(),
            memory_peak_bytes=1000 + self.calls["sample"],
            cpu_total_seconds=float(self.calls["sample"]),
            process_count=2 + self.calls["sample"],
        )

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples=None,
    ) -> Receipt:
        with self.lock:
            self.calls["finish"] += 1
            self.finish_reasons.append(terminal_reason)
            self.finish_graces.append(grace_seconds)
        if self.finish_delay:
            time.sleep(self.finish_delay)
        if self.finish_raises:
            raise BackendFinishError("finish boom")
        if running.process is not None:
            running.process.terminate()
        provenance = (
            MeasurementProvenance.KERNEL if self.kernel_provenance
            else MeasurementProvenance.SAMPLED
        )
        memory_peak = 1234
        cpu_total = 2.5
        process_peak = 7
        sample_gaps: tuple[float, ...] = (0.9, 1.1)
        if self.merge_samples and samples:
            memory_peak = max(s.memory_peak_bytes for s in samples if s.memory_peak_bytes)
            cpu_total = samples[-1].cpu_total_seconds
            process_peak = max(s.process_count for s in samples if s.process_count)
            ordered = sorted(s.sampled_at for s in samples)
            sample_gaps = tuple(
                round(b - a, 4) for a, b in zip(ordered, ordered[1:])
            )
        return Receipt(
            backend=self.name,
            terminal_reason=terminal_reason,
            cleanup_status=self.cleanup_status,
            cleanup_duration_seconds=0.5,
            quiescent=not self.survivors,
            wall_time_seconds=1.0,
            memory_peak_bytes=memory_peak,
            memory_peak_provenance=provenance,
            cpu_total_seconds=cpu_total,
            cpu_total_provenance=provenance,
            process_peak=process_peak,
            process_peak_provenance=provenance,
            sample_gaps=sample_gaps,
            enforcement_events=(),
            survivors=self.survivors,
        )

    def abandon(self, pending: PendingHandle) -> None:
        with self.lock:
            self.calls["abandon"] += 1

    def recover(self, handle_token: str) -> RecoveryResult:
        with self.lock:
            self.calls["recover"] += 1
        return RecoveryResult(recovered=False, evidence="fake")


class RecordingPublisher:
    def __init__(self) -> None:
        self.receipts: list[Receipt] = []
        self.lock = threading.Lock()

    def publish(self, receipt: Receipt) -> None:
        with self.lock:
            self.receipts.append(receipt)

    def count(self) -> int:
        with self.lock:
            return len(self.receipts)


class PausingToken(CancellationToken):
    """Test seam: deterministically pause inside ``register()``.

    A concurrent ``cancel()`` can then land in a chosen race window:
    *before* the real registration (window A: cancellation during
    registration) or *after* the real registration but before the launch
    fence (window B: cancellation between registration and launch). The
    real ``register``/``fence_launch`` logic still runs and is what is
    under test.
    """

    def __init__(
        self,
        pause_before: threading.Event | None = None,
        pause_after: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.pause_before = pause_before
        self.pause_after = pause_after
        self.entered_register = threading.Event()
        self.after_register = threading.Event()

    def register(self, control):
        self.entered_register.set()
        if self.pause_before is not None:
            assert self.pause_before.wait(timeout=10)
        result = super().register(control)
        self.after_register.set()
        if self.pause_after is not None:
            assert self.pause_after.wait(timeout=10)
        return result


# ── builders ─────────────────────────────────────────────────────────


def make_request(logical_id: str = "r1", **kw) -> AdmissionRequest:
    return AdmissionRequest(
        org="happyranch",
        invocation_kind="task",
        logical_id=logical_id,
        executor_profile="claude",
        **kw,
    )


def make_policy(
    *,
    global_session_cap: int = 8,
    cleanup_grace_seconds: float = 5.0,
    best_effort_survivor_threshold: int = 3,
) -> PolicySnapshot:
    return PolicySnapshot(
        global_session_cap=global_session_cap,
        producer_envelope=11,
        linux_shadow_cap=CapPolicy(value=11, binding=False),
        macos_binding_cap=CapPolicy(value=4, binding=True),
        cleanup_grace_seconds=cleanup_grace_seconds,
        best_effort_survivor_threshold=best_effort_survivor_threshold,
    )


def make_spec() -> LaunchSpec:
    return LaunchSpec(argv=("python", "-c", "pass"))


def ok_result(**kw) -> LaunchResult:
    base = dict(success=True, duration_seconds=1.0)
    base.update(kw)
    return LaunchResult(**base)


def _ok_body(running: RunningHandle) -> LaunchResult:
    """A launch body that returns a canned success (the running handle is
    the executor seam, so bodies must accept it)."""
    return ok_result()


def blocking_launch_body(running: RunningHandle) -> LaunchResult:
    """Blocks until the fake backend terminates the process (like the real
    communicate loop unblocking when the tree is killed)."""
    assert running.process is not None
    running.process.wait(timeout=10)
    return ok_result()


def make_supervisor(
    *,
    backend: FakeBackend,
    policy: PolicySnapshot | None = None,
    publisher: RecordingPublisher | None = None,
    cap: int | None = None,
    **kw,
) -> tuple[HostSessionSupervisor, RecordingPublisher]:
    policy = policy or make_policy()
    publisher = publisher or RecordingPublisher()
    admission = None
    if cap is not None:
        residue = ResidueAccountant(policy=policy)
        admission = AdmissionController(cap=cap, gates=(residue,))
        kw.setdefault("residue", residue)
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=policy,
        publisher=publisher.publish,
        admission=admission,
        **kw,
    )
    return supervisor, publisher


def run_in_thread(supervisor, request, **kw):
    results: list = []

    def _run():
        try:
            results.append(supervisor.run(request, launch_spec=make_spec(), **kw))
        except Exception as exc:  # pragma: no cover - test diagnostics
            results.append(exc)

    t = threading.Thread(target=_run)
    t.start()
    return t, results


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate(), "condition not reached in time"


# ── admission ordering ───────────────────────────────────────────────


def test_nothing_launches_before_admission():
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend, cap=1)

    holder_started = threading.Event()
    release_holder = threading.Event()

    def holder_body(running: RunningHandle) -> LaunchResult:
        holder_started.set()
        assert release_holder.wait(timeout=10)
        return ok_result()

    holder_t, holder_results = run_in_thread(
        supervisor, make_request("a"), launch_body=holder_body
    )
    assert holder_started.wait(timeout=5)

    # B queued behind A: nothing about B may touch the backend yet.
    t, results = run_in_thread(
        supervisor, make_request("b"), launch_body=_ok_body
    )
    _wait_until(lambda: supervisor._admission.queue_depth() == 1)
    assert backend.calls["prepare"] == 1  # only A prepared
    assert backend.calls["launch"] == 1  # only A launched
    assert publisher.count() == 0

    release_holder.set()
    holder_t.join(timeout=10)
    t.join(timeout=10)
    assert holder_results[0].terminal_reason is TerminalReason.SUCCESS
    assert results[0].terminal_reason is TerminalReason.SUCCESS
    assert backend.calls["prepare"] == 2  # B prepared only after admission


def test_queued_cancel_creates_no_handle():
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend, cap=1)

    holder_started = threading.Event()
    release_holder = threading.Event()

    def holder_body(running: RunningHandle) -> LaunchResult:
        holder_started.set()
        assert release_holder.wait(timeout=10)
        return ok_result()

    holder_t, holder_results = run_in_thread(
        supervisor, make_request("a"), launch_body=holder_body
    )
    assert holder_started.wait(timeout=5)

    token = CancellationToken()
    t, results = run_in_thread(
        supervisor,
        make_request("b", cancellation=token),
        launch_body=_ok_body,
    )
    _wait_until(lambda: supervisor._admission.queue_depth() == 1)
    token.cancel()
    t.join(timeout=10)
    assert not t.is_alive()
    assert results[0].cancelled_while_queued is True
    assert results[0].terminal_reason is TerminalReason.CANCELLED
    # No handle, no launch, no finish, no receipt for B (A still running).
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 0
    assert publisher.count() == 0

    release_holder.set()
    holder_t.join(timeout=10)
    assert holder_results[0].terminal_reason is TerminalReason.SUCCESS
    assert backend.calls["finish"] == 1  # only A's finish
    assert publisher.count() == 1  # only A's receipt


# ── prepare / spawn failure ──────────────────────────────────────────


def test_prepare_failure_cleans_partial_state_and_releases():
    backend = FakeBackend(prepare_raises=True)
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.PREPARE_FAILURE
    assert "prepare boom" in (outcome.error or "")
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 0
    assert backend.calls["finish"] == 0
    assert backend.calls["abandon"] == 0  # no handle ever existed
    assert publisher.count() == 0
    # Lease released exactly once; capacity fully restored.
    assert supervisor._admission.active_count() == 0
    assert supervisor._admission.released_total() == 1


def test_spawn_failure_abandons_partial_state_and_releases():
    backend = FakeBackend(launch_raises=True)
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.SPAWN_FAILURE
    assert "launch boom" in (outcome.error or "")
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 1
    assert backend.calls["abandon"] == 1  # partial containment torn down
    assert backend.calls["finish"] == 0  # no live containment to finish
    assert publisher.count() == 0
    assert supervisor._admission.active_count() == 0
    assert supervisor._admission.released_total() == 1
    # The system remains usable after the failure.
    backend.launch_raises = False
    outcome2 = supervisor.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome2.terminal_reason is TerminalReason.SUCCESS


# ── terminal paths ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body_kwargs,expected",
    [
        (dict(success=True), TerminalReason.SUCCESS),
        (dict(success=False, error="exit 2"), TerminalReason.FAILURE),
        (dict(success=True, timed_out=True), TerminalReason.TIMEOUT),
    ],
    ids=["success", "nonzero", "timeout"],
)
def test_terminal_paths_finish_containment_publish_and_release(body_kwargs, expected):
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend)

    def body(running):
        return LaunchResult(duration_seconds=1.0, **body_kwargs)

    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=body)
    assert outcome.terminal_reason is expected
    assert outcome.receipt is not None
    assert outcome.receipt.terminal_reason == expected.value
    assert outcome.cleanup_status is CleanupStatus.CLEAN
    # Containment finished exactly once with the frozen reason + injected grace.
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == [expected.value]
    assert backend.finish_graces == [5.0]  # policy grace flowed through
    assert publisher.count() == 1
    assert publisher.receipts[0] is outcome.receipt
    # Lease released exactly once; capacity restored.
    assert supervisor._admission.active_count() == 0
    assert supervisor._admission.released_total() == 1
    assert supervisor.active_count() == 0


def test_on_started_publishes_diagnostic_pid_after_launch():
    backend = FakeBackend()
    supervisor, _ = make_supervisor(backend=backend)
    pids: list[int] = []
    outcome = supervisor.run(
        make_request("a", on_started=pids.append),
        launch_spec=make_spec(),
        launch_body=_ok_body,
    )
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert pids == [9001]  # the fake's first root pid


def test_cancel_mid_run_drives_backend_teardown_idempotent():
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")
    launched = threading.Event()

    def body(running):
        launched.set()
        # Mirrors real communicate: blocked until backend.finish kills the tree.
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    assert launched.wait(timeout=5)
    request.cancellation.cancel()
    t.join(timeout=10)
    assert not t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["cancelled"]
    assert publisher.count() == 1
    assert publisher.receipts[0].terminal_reason == "cancelled"
    assert supervisor._admission.released_total() == 1


def test_finish_cancel_race_is_idempotent():
    backend = FakeBackend(finish_delay=0.1)
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")
    launched = threading.Event()

    def body(running):
        launched.set()
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    assert launched.wait(timeout=5)
    # Fire cancellation concurrently with the run path's own finish.
    cancel_t = threading.Thread(target=request.cancellation.cancel)
    cancel_t.start()
    t.join(timeout=10)
    cancel_t.join(timeout=10)
    assert not t.is_alive() and not cancel_t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason in (TerminalReason.CANCELLED, TerminalReason.SUCCESS)
    # Exactly one finish, one publish, one release regardless of the winner.
    assert backend.calls["finish"] == 1
    assert publisher.count() == 1
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0


# ── atomic cancellation/registration vs launch ───────────────────────


def _bare_supervisor() -> HostSessionSupervisor:
    return HostSessionSupervisor(
        backend=FakeBackend(), policy=make_policy(), publisher=lambda r: None
    )


def test_register_replays_already_fired_token():
    """A token that fired before registration must invoke the control
    immediately (replay) and report the fired state, so the launch fence
    refuses and no launch can follow."""
    token = CancellationToken()
    token.cancel()
    ctx = _AttemptContext("r1", time.monotonic)
    control = _OpaqueCancelControl(_bare_supervisor(), ctx)
    fired = token.register(control)
    assert fired is True
    assert ctx.terminal_reason() is TerminalReason.CANCELLED
    # The fence observes the same frozen state and refuses launch.
    assert control.fence_launch() is False


def test_launch_fence_refuses_once_cancellation_wins():
    token = CancellationToken()
    ctx = _AttemptContext("r1", time.monotonic)
    control = _OpaqueCancelControl(_bare_supervisor(), ctx)
    assert token.register(control) is False  # not yet fired
    token.cancel()
    # Cancellation won before launch: the fence refuses (one-shot).
    assert control.fence_launch() is False
    assert control.fence_launch() is False


def test_launch_fence_allows_when_no_cancellation():
    """When launch wins the race the fence permits it; a post-launch
    cancellation still freezes CANCELLED through the control (teardown
    idempotence covered by test_cancel_mid_run_*)."""
    token = CancellationToken()
    ctx = _AttemptContext("r1", time.monotonic)
    control = _OpaqueCancelControl(_bare_supervisor(), ctx)
    assert token.register(control) is False
    assert control.fence_launch() is True
    token.cancel()
    assert ctx.terminal_reason() is TerminalReason.CANCELLED


def test_cancel_during_registration_never_launches():
    """Race window A: cancellation fires while registration is in progress.

    The register handshake must replay the already-fired token so the
    launch fence refuses and no backend handle ever launches."""
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend)
    token = PausingToken(pause_before=threading.Event())
    t, results = run_in_thread(
        supervisor, make_request("a", cancellation=token), launch_body=_ok_body
    )
    # The run path is inside register(), paused before the real registration.
    assert token.entered_register.wait(timeout=5)
    token.cancel()  # cancellation wins while registration is in progress
    token.pause_before.set()
    t.join(timeout=10)
    assert not t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert outcome.cancelled_while_queued is False
    # No backend handle launched; the pending handle was abandoned.
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 0
    assert backend.calls["abandon"] == 1
    assert backend.calls["finish"] == 0
    assert publisher.count() == 0
    # Lease released exactly once.
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0


def test_cancel_between_registration_and_launch_never_launches():
    """Race window B: cancellation fires after registration but before the
    launch fence — the fence must observe the frozen CANCELLED and abandon
    the pending handle without launching."""
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend)
    token = PausingToken(pause_after=threading.Event())
    t, results = run_in_thread(
        supervisor, make_request("a", cancellation=token), launch_body=_ok_body
    )
    # The real registration completed (token not fired); the run path is
    # paused before the launch fence.
    assert token.after_register.wait(timeout=5)
    token.cancel()  # cancellation wins between registration and the fence
    token.pause_after.set()
    t.join(timeout=10)
    assert not t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert outcome.cancelled_while_queued is False
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 0
    assert backend.calls["abandon"] == 1
    assert backend.calls["finish"] == 0
    assert publisher.count() == 0
    assert supervisor._admission.released_total() == 1


def test_cancel_during_backend_launch_finishes_upon_bind_exactly_once():
    """Race window C: cancellation fires after the launch fence but before
    backend.launch returns (the fence-to-running-bind interval).

    The pending cancellation must be durably observed immediately upon bind:
    the blocking launch body is never entered, teardown (finish) runs exactly
    once with the cancelled reason, the receipt publishes exactly once, and
    the lease releases exactly once — the fixed terminal ordering holds."""
    backend = FakeBackend(
        launch_entered=threading.Event(), launch_release=threading.Event()
    )
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")
    body_entered = threading.Event()

    def body(running):
        body_entered.set()
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    # The run path passed the launch fence and is blocked inside backend.launch
    # (the running handle is not yet bound).
    assert backend.launch_entered.wait(timeout=5)
    request.cancellation.cancel()  # cancellation wins in the fence->bind window
    backend.launch_release.set()  # launch completes -> running handle binds
    t.join(timeout=15)
    assert not t.is_alive()
    # The invocation never entered the blocking body uncontained.
    assert not body_entered.is_set()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert outcome.cancelled_while_queued is False
    # Exactly-once teardown + publish + release, in the fixed order.
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["cancelled"]
    assert publisher.count() == 1
    assert publisher.receipts[0].terminal_reason == "cancelled"
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0


def test_cancel_during_launch_normal_completion_race_stays_idempotent():
    """Adversarial variant: launch completes normally while cancellation fires
    concurrently in the fence-to-bind window (cancel/normal-completion race).

    Whichever side wins the interleaving, exactly one finish terminates the
    tree, one receipt publishes, and one lease releases — the invocation
    cannot remain blocked in the body uncontained (finish terminates the fake
    process, which is what unblocks a body that did start)."""
    backend = FakeBackend(
        launch_entered=threading.Event(), launch_release=threading.Event()
    )
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")

    def body(running):
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    assert backend.launch_entered.wait(timeout=5)
    # Release the launch and cancel back-to-back: the bind and the freeze race.
    backend.launch_release.set()
    request.cancellation.cancel()
    t.join(timeout=15)
    assert not t.is_alive()
    outcome = results[0]
    # The body cannot complete before its tree is terminated, so the cancel
    # always freezes first and wins the primary reason.
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    # Exactly-once finish/publish/release regardless of the interleaving winner.
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["cancelled"]
    assert publisher.count() == 1
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    # The launched tree was terminated (contained), not left live.
    assert backend.last_running is not None
    assert backend.last_running.process.returncode is not None


def test_cancel_during_launch_finish_race_stays_idempotent():
    """Adversarial variant: a second cancellation lands while the cancellation-
    driven finish (driven immediately upon bind) is already in flight
    (cancel/finish race). finish_once blocks stragglers and returns the same
    receipt; finalize_once publishes once — the replay composes with the
    exactly-once guards."""
    backend = FakeBackend(
        launch_entered=threading.Event(),
        launch_release=threading.Event(),
        finish_delay=0.2,
    )
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")

    def body(running):
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    assert backend.launch_entered.wait(timeout=5)
    request.cancellation.cancel()  # wins in the fence->bind window
    backend.launch_release.set()  # bind -> finish driven (in flight 0.2s)
    # Second cancellation while the replay-driven finish is in flight.
    _wait_until(lambda: backend.calls["finish"] == 1)
    request.cancellation.cancel()
    t.join(timeout=15)
    assert not t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["cancelled"]
    assert publisher.count() == 1
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0


def test_shutdown_during_backend_launch_finishes_upon_bind_exactly_once():
    """Daemon shutdown in the fence-to-running-bind window (TASK-5596 [HIGH]
    reproduction): the drain freezes SHUTDOWN while ``backend.launch`` is
    still in flight, so the running handle is not yet bound. The frozen
    first-wins reason must be replayed durably on bind — the blocking launch
    body is never entered, finish runs exactly once with the shutdown
    reason, the receipt publishes exactly once, and the lease releases
    exactly once."""
    backend = FakeBackend(
        launch_entered=threading.Event(), launch_release=threading.Event()
    )
    supervisor, publisher = make_supervisor(backend=backend)
    request = make_request("a")
    body_entered = threading.Event()

    def body(running):
        body_entered.set()
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    # The run path passed the launch fence and is blocked inside backend.launch
    # (the running handle is not yet bound).
    assert backend.launch_entered.wait(timeout=5)
    supervisor.shutdown()  # drain freezes SHUTDOWN in the fence->bind window
    backend.launch_release.set()  # launch completes -> running handle binds
    t.join(timeout=15)
    assert not t.is_alive()
    # The invocation never entered the blocking body uncontained.
    assert not body_entered.is_set()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.SHUTDOWN
    assert outcome.cancelled_while_queued is False
    # Exactly-once teardown + publish + release, in the fixed order.
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["shutdown"]
    assert publisher.count() == 1
    assert publisher.receipts[0].terminal_reason == "shutdown"
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0


def test_shutdown_before_launch_fence_never_launches():
    """A daemon shutdown that freezes before the launch fence must refuse
    launch: the fence observes the generic pre-bind terminal winner, the
    prepared handle is abandoned, and the lease releases without any
    launch/finish/publish — SHUTDOWN stays the frozen first-wins reason."""
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend)
    token = PausingToken(pause_after=threading.Event())
    t, results = run_in_thread(
        supervisor, make_request("a", cancellation=token), launch_body=_ok_body
    )
    # The real registration completed (token not fired); the run path is
    # paused before the launch fence.
    assert token.after_register.wait(timeout=5)
    supervisor.shutdown()  # drain freezes SHUTDOWN while the fence is pending
    token.pause_after.set()
    t.join(timeout=10)
    assert not t.is_alive()
    outcome = results[0]
    assert outcome.terminal_reason is TerminalReason.SHUTDOWN
    assert outcome.cancelled_while_queued is False
    # No launch commitment: the prepared handle was abandoned, nothing ran.
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 0
    assert backend.calls["finish"] == 0
    assert backend.calls["abandon"] == 1
    assert publisher.count() == 0
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0


def _stage_pre_bind_terminal_row(source: str, window: str):
    """Deterministically stage one (source, window) row of the pre-bind
    terminal transition matrix and run it to a settled terminal state.

    Returns ``(supervisor, publisher, backend, results, body_entered)`` with
    the run thread joined and terminal state settled."""
    backend = FakeBackend(
        launch_entered=threading.Event(), launch_release=threading.Event()
    )
    supervisor, publisher = make_supervisor(backend=backend)
    body_entered = threading.Event()

    def body(running):
        body_entered.set()
        running.process.wait(timeout=10)
        return ok_result()

    request = make_request("a")
    if window == "during_registration":
        request = make_request(
            "a", cancellation=PausingToken(pause_before=threading.Event())
        )
    elif window in ("between_registration_and_fence", "before_fence"):
        request = make_request(
            "a", cancellation=PausingToken(pause_after=threading.Event())
        )
    t, results = run_in_thread(supervisor, request, launch_body=body)

    if window == "during_registration":
        # Cancellation wins while registration is paused before the real
        # register (window A: the register handshake replays the fired token).
        assert request.cancellation.entered_register.wait(timeout=5)
        request.cancellation.cancel()
        request.cancellation.pause_before.set()
    elif window in ("between_registration_and_fence", "before_fence"):
        # Real registration done; the run path is paused before the fence.
        assert request.cancellation.after_register.wait(timeout=5)
        if source == "cancel":
            request.cancellation.cancel()
        else:
            supervisor.shutdown()  # drain freezes SHUTDOWN pre-fence
        request.cancellation.pause_after.set()
    elif window == "fence_to_bind":
        # The run path passed the fence and is blocked inside backend.launch
        # (the running handle is not yet bound).
        assert backend.launch_entered.wait(timeout=5)
        if source == "cancel":
            request.cancellation.cancel()
        elif source == "shutdown":
            supervisor.shutdown()  # drain freezes SHUTDOWN fence->bind
        else:  # shutdown_cancel_race — fire both back-to-back (first wins)
            supervisor.shutdown()
            request.cancellation.cancel()
        backend.launch_release.set()  # launch completes -> running handle binds
    else:  # pragma: no cover - test wiring
        raise AssertionError(f"unknown window {window!r}")

    t.join(timeout=15)
    assert not t.is_alive()
    return supervisor, publisher, backend, results, body_entered


@pytest.mark.parametrize(
    "source,window",
    [
        ("cancel", "during_registration"),            # truth table: cancellation
        ("cancel", "between_registration_and_fence"),  # truth table: cancellation
        ("shutdown", "before_fence"),                  # truth table: daemon shutdown
        ("cancel", "fence_to_bind"),                   # window C (retained)
        ("shutdown", "fence_to_bind"),                 # TASK-5596 [HIGH] repro
        ("shutdown_cancel_race", "fence_to_bind"),     # first-wins race
    ],
)
def test_pre_bind_terminal_transition_matrix(source, window):
    """Pre-bind terminal transition matrix (TASK-5596 [HIGH] fix).

    Every terminal source the governing truth table allows to win before the
    running handle binds — user/task cancellation and daemon shutdown — in
    every pre-bind window (during registration, between registration and the
    launch fence, and between the fence and the running-handle bind), plus
    their first-wins race. Per row: the frozen first-wins terminal reason is
    preserved in the outcome; the blocking launch body is never entered (no
    uncontained execution); a launch committed at the fence is finished,
    published, and released exactly once. Post-bind terminal rows (cancel
    mid-body, shutdown with a bound handle, clean/nonzero/timeout/retry
    completion, and finish races) are proven by the dedicated tests above."""
    supervisor, publisher, backend, results, body_entered = (
        _stage_pre_bind_terminal_row(source, window)
    )
    outcome = results[0]
    if source == "cancel":
        assert outcome.terminal_reason is TerminalReason.CANCELLED
        finish_reason = "cancelled"
    elif source == "shutdown":
        assert outcome.terminal_reason is TerminalReason.SHUTDOWN
        finish_reason = "shutdown"
    else:  # shutdown_cancel_race — first-wins freeze preserves the winner
        assert outcome.terminal_reason in (
            TerminalReason.SHUTDOWN,
            TerminalReason.CANCELLED,
        )
        finish_reason = outcome.terminal_reason.value
    assert outcome.cancelled_while_queued is False
    # A pre-bind terminal winner never enters the blocking body uncontained.
    assert not body_entered.is_set()
    assert supervisor.active_count() == 0
    if window == "fence_to_bind":
        # Launch was committed at the fence; finish/publish/release run
        # exactly once with the preserved first-wins reason.
        assert backend.calls["launch"] == 1
        assert backend.calls["finish"] == 1
        assert backend.finish_reasons == [finish_reason]
        assert publisher.count() == 1
        assert publisher.receipts[0].terminal_reason == finish_reason
    else:
        # Pre-fence winners abandon the prepared handle; nothing launches.
        assert backend.calls["launch"] == 0
        assert backend.calls["finish"] == 0
        assert backend.calls["abandon"] == 1
        assert publisher.count() == 0
    # The staged lease released exactly once; the admission registry is empty.
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0


def test_pre_bind_terminal_release_wakes_queued_waiter():
    """A pre-bind terminal release is a normal lease release: a request
    queued behind the staged attempt is woken into admission and completes,
    so no wake is lost on the cancellation path. (The shutdown drain's
    queued-cancellation wake is proven by
    ``test_supervisor_shutdown_stops_admission_and_finishes_active``.)"""
    backend = FakeBackend(
        launch_entered=threading.Event(), launch_release=threading.Event()
    )
    supervisor, publisher = make_supervisor(backend=backend, cap=1)
    request = make_request("a")

    def body(running):
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, request, launch_body=body)
    assert backend.launch_entered.wait(timeout=5)
    probe_t, probe_results = run_in_thread(
        supervisor, make_request("b"), launch_body=_ok_body
    )
    _wait_until(lambda: supervisor._admission.queue_depth() == 1)
    request.cancellation.cancel()  # cancel wins in the fence->bind window
    backend.launch_release.set()
    t.join(timeout=15)
    probe_t.join(timeout=15)
    assert not t.is_alive() and not probe_t.is_alive()
    assert results[0].terminal_reason is TerminalReason.CANCELLED
    # B was woken into admission once A's pre-bind terminal release landed.
    assert probe_results[0].terminal_reason is TerminalReason.SUCCESS
    assert backend.finish_reasons == ["cancelled", "success"]
    assert supervisor._admission.released_total() == 2
    assert supervisor._admission.active_count() == 0


def test_retry_finishes_attempt_then_reacquires_with_original_age():
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(
        backend=backend, max_retry_attempts=1, backoff_seconds=(0.0,)
    )
    enqueued_at = 42.0

    def body(running) -> LaunchResult:
        # The supervisor passes the per-attempt request into prepare, but the
        # launch body observes the attempt via the running handle only; use a
        # module-level counter to make attempt 1 rate-limited, attempt 2 ok.
        n = backend.calls["launch"]
        if n == 1:
            return LaunchResult(
                success=False, duration_seconds=1.0,
                rate_limited=True, error="429",
            )
        return ok_result()

    outcome = supervisor.run(
        make_request("a", enqueued_at=enqueued_at),
        launch_spec=make_spec(),
        launch_body=body,
    )
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.attempt == 1
    # Attempt 1 finished fully (containment + receipt) before re-admission.
    assert backend.calls["prepare"] == 2  # fresh handle per attempt
    assert backend.calls["finish"] == 2
    assert backend.finish_reasons[0] == "rate_limited"
    assert backend.finish_reasons[1] == "success"
    assert publisher.count() == 2
    assert publisher.receipts[0].terminal_reason == "rate_limited"
    assert publisher.receipts[1].terminal_reason == "success"
    # Lease released exactly once per attempt; no capacity held during sleep.
    assert supervisor._admission.released_total() == 2
    assert supervisor._admission.admitted_total() == 2
    # The retry re-entered with the ORIGINAL enqueue age.
    assert backend.prepare_policies[0] is backend.prepare_policies[1]


def test_retry_exhaustion_preserves_rate_limited_result():
    backend = FakeBackend()
    supervisor, _ = make_supervisor(backend=backend, max_retry_attempts=1, backoff_seconds=(0.0,))

    def body(running):
        return LaunchResult(success=False, duration_seconds=1.0, rate_limited=True, error="429")

    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=body)
    assert outcome.terminal_reason is TerminalReason.RATE_LIMITED
    assert outcome.attempt == 1
    assert backend.calls["finish"] == 2  # both attempts finished


# ── cleanup error discipline ─────────────────────────────────────────


def test_cleanup_error_never_replaces_primary_terminal_reason():
    backend = FakeBackend(finish_raises=True)
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    # Primary reason preserved; cleanup failure travels alongside.
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.cleanup_error is not None
    assert "finish boom" in outcome.cleanup_error
    assert outcome.receipt is None
    assert publisher.count() == 0  # no receipt to publish
    # Teardown verification failed -> admission tightens (fail-closed).
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._admission.active_count() == 0
    assert supervisor._admission.released_total() == 1


def test_nonzero_with_cleanup_error_keeps_failure_primary():
    backend = FakeBackend(finish_raises=True)
    supervisor, _ = make_supervisor(backend=backend)

    def body(running):
        return LaunchResult(success=False, duration_seconds=1.0, error="exit 3")

    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=body)
    assert outcome.terminal_reason is TerminalReason.FAILURE
    assert outcome.cleanup_error is not None


# ── residue: guaranteed vs best-effort ───────────────────────────────


def _survivor(pid: int, start: str = "boot-1") -> SurvivorRecord:
    return SurvivorRecord(
        pid=pid, start_identity=start, backend="fake",
        discovered_at=1.0, last_seen_at=2.0,
    )


GUARANTEED_CAPS = {
    Capability.LIMITS_MEMORY: CapabilityLevel.GUARANTEED,
    Capability.LIMITS_PIDS: CapabilityLevel.GUARANTEED,
    Capability.LIMITS_CPU: CapabilityLevel.GUARANTEED,
    Capability.KILLS_TREE_GUARANTEED: CapabilityLevel.GUARANTEED,
}

BEST_EFFORT_CAPS = {
    Capability.KILLS_TREE_BEST_EFFORT: CapabilityLevel.GUARANTEED,
}


def test_guaranteed_residue_blocks_admission_until_reconciliation():
    backend = FakeBackend(
        capabilities=GUARANTEED_CAPS, survivors=(_survivor(11),)
    )
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert publisher.receipts[0].survivors  # residue visible in the receipt
    # Guaranteed-cleanup residue is an anomaly: admission blocks.
    assert supervisor._residue.census() == (_survivor(11),)
    assert supervisor._residue.evaluate().admit is False

    # A new admission stalls at the pressure gate (still queued, aged).
    t, results = run_in_thread(
        supervisor, make_request("b"), launch_body=_ok_body, timeout=0.3
    )
    t.join(timeout=10)
    assert isinstance(results[0], AdmissionTimeout)

    # Explicit reconciliation: survivor exit clears the block.
    result = supervisor._residue.handle_survivor_exit(11, "boot-1")
    assert result.accepted is True and result.blocked is False
    assert supervisor._residue.evaluate().admit is True
    outcome2 = supervisor.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome2.terminal_reason is TerminalReason.SUCCESS


def test_best_effort_survivors_stay_censused_but_do_not_block():
    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS, survivors=(_survivor(11),)
    )
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    # Survivor stays censused + visible; presence alone does not block.
    assert supervisor._residue.census() == (_survivor(11),)
    assert publisher.receipts[0].survivors
    assert supervisor._residue.evaluate().admit is True
    # A follow-up admission proceeds normally.
    outcome2 = supervisor.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome2.terminal_reason is TerminalReason.SUCCESS


def test_best_effort_survivor_threshold_blocks_admission():
    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS,
        survivors=tuple(_survivor(pid) for pid in (11, 12, 13, 14)),  # 4 > 3
    )
    supervisor, _ = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "survivor_threshold_exceeded"

    t, results = run_in_thread(
        supervisor, make_request("b"), launch_body=_ok_body, timeout=0.3
    )
    t.join(timeout=10)
    assert isinstance(results[0], AdmissionTimeout)


def test_measurement_failure_blocks_best_effort_admission():
    class BoomSampler:
        def __call__(self, running):
            raise RuntimeError("census boom")

    backend = FakeBackend(capabilities=BEST_EFFORT_CAPS)
    supervisor, _ = make_supervisor(
        backend=backend, sampler=BoomSampler(), sample_interval_seconds=0.001
    )

    def body(running):
        time.sleep(0.05)
        return ok_result()

    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    # Census/measurement failure tightens admission (missing enforcement).
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "measurement_unhealthy"


# ── incomplete cleanup: capability-conditional measurement health ────


def test_best_effort_incomplete_with_below_threshold_survivors_stays_admissible():
    """Exact review regression: a best-effort backend reporting INCOMPLETE
    cleanup with verified below-threshold survivors is an expected outcome
    — residue stays censused/charged/visible in the bounded receipt and
    admission stays open (no macOS self-DoS)."""
    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS,
        cleanup_status=CleanupStatus.INCOMPLETE,
        survivors=(_survivor(11),),
    )
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.cleanup_status is CleanupStatus.INCOMPLETE
    # Survivor stays censused + visible in the bounded receipt.
    assert supervisor._residue.census() == (_survivor(11),)
    assert publisher.receipts[0].survivors == (_survivor(11),)
    # Below the conservative threshold: admissible, no measurement-unhealthy.
    assert supervisor._residue.evaluate().admit is True
    assert supervisor._residue.evaluate().reason is None
    # A follow-up admission proceeds normally (no self-DoS).
    outcome2 = supervisor.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome2.terminal_reason is TerminalReason.SUCCESS


def test_guaranteed_incomplete_cleanup_marks_measurement_unhealthy():
    """INCOMPLETE on a guaranteed-cleanup backend with no verified census is
    an anomaly: teardown could not be verified, so admission tightens
    (fail-closed)."""
    backend = FakeBackend(
        capabilities=GUARANTEED_CAPS,
        cleanup_status=CleanupStatus.INCOMPLETE,
        survivors=(),
    )
    supervisor, _ = make_supervisor(backend=backend)
    supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "measurement_unhealthy"


def test_best_effort_incomplete_above_threshold_still_blocks():
    """A conservative survivor-threshold breach blocks even when cleanup
    reports INCOMPLETE on a best-effort backend."""
    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS,
        cleanup_status=CleanupStatus.INCOMPLETE,
        survivors=tuple(_survivor(pid) for pid in (11, 12, 13, 14)),  # 4 > 3
    )
    supervisor, _ = make_supervisor(backend=backend)
    supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "survivor_threshold_exceeded"


def test_best_effort_incomplete_with_census_failure_still_blocks():
    """Unavailable census/measurement (sampler raises) tightens admission
    even when cleanup reports INCOMPLETE on a best-effort backend."""

    class BoomSampler:
        def __call__(self, running):
            raise RuntimeError("census boom")

    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS, cleanup_status=CleanupStatus.INCOMPLETE
    )
    supervisor, _ = make_supervisor(
        backend=backend, sampler=BoomSampler(), sample_interval_seconds=0.001
    )

    def body(running):
        time.sleep(0.05)
        return ok_result()

    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=body)
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "measurement_unhealthy"


def test_best_effort_incomplete_with_teardown_failure_still_blocks():
    """A genuine teardown failure (backend.finish raises) remains fail-closed
    even when the backend is best-effort and cleanup reports INCOMPLETE."""
    backend = FakeBackend(
        capabilities=BEST_EFFORT_CAPS,
        cleanup_status=CleanupStatus.INCOMPLETE,
        finish_raises=True,
    )
    supervisor, publisher = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.cleanup_error is not None
    assert supervisor._residue.evaluate().admit is False
    assert supervisor._residue.evaluate().reason == "cleanup_failed"


# ── reconciliation inputs ────────────────────────────────────────────


def test_reconciliation_inputs_modeled():
    accountant = ResidueAccountant(policy=make_policy())
    sv = _survivor(7, start="s9")
    accountant.account(
        Receipt(
            backend="fake", terminal_reason="success",
            cleanup_status=CleanupStatus.TERM, cleanup_duration_seconds=0.5,
            quiescent=False, wall_time_seconds=1.0, survivors=(sv,),
        ),
        capabilities=(Capability.KILLS_TREE_GUARANTEED,),
    )
    assert accountant.evaluate().admit is False

    # 1) Survivor exit reconciles.
    r = accountant.handle_survivor_exit(7, "s9")
    assert r.accepted is True and r.blocked is False
    assert accountant.evaluate().admit is True

    # 2) Successful re-probe reconciles.
    sv2 = _survivor(8, start="s10")
    accountant.account(
        Receipt(
            backend="fake", terminal_reason="success",
            cleanup_status=CleanupStatus.TERM, cleanup_duration_seconds=0.5,
            quiescent=False, wall_time_seconds=1.0, survivors=(sv2,),
        ),
        capabilities=(Capability.KILLS_TREE_GUARANTEED,),
    )
    assert accountant.evaluate().admit is False
    r = accountant.handle_successful_reprobe(
        CapabilityReport(backend="fake", backend_version="1.0", healthy=True)
    )
    assert r.accepted is True and r.blocked is False

    # 3) Operator acknowledgement after verified cleanup reconciles;
    #    an unverified acknowledgement is rejected fail-closed.
    sv3 = _survivor(9, start="s11")
    accountant.account(
        Receipt(
            backend="fake", terminal_reason="success",
            cleanup_status=CleanupStatus.TERM, cleanup_duration_seconds=0.5,
            quiescent=False, wall_time_seconds=1.0, survivors=(sv3,),
        ),
        capabilities=(Capability.KILLS_TREE_GUARANTEED,),
    )
    unverified = accountant.handle_operator_acknowledgement(
        9, "s11", verified_cleanup_evidence="  "
    )
    assert unverified.accepted is False and unverified.blocked is True
    assert unverified.reason == "operator_ack_requires_verified_cleanup_evidence"
    assert accountant.evaluate().admit is False  # still blocked fail-closed
    verified = accountant.handle_operator_acknowledgement(
        9, "s11", verified_cleanup_evidence="verified: cgroup.procs empty"
    )
    assert verified.accepted is True and verified.blocked is False


# ── measurement provenance & receipt shape ───────────────────────────


def test_provenance_distinguishes_kernel_sampled_unavailable():
    # Kernel-provenance backend (Linux-style cgroup counters).
    kernel_backend = FakeBackend(capabilities=GUARANTEED_CAPS, kernel_provenance=True)
    supervisor, publisher = make_supervisor(backend=kernel_backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    receipt = outcome.receipt
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert receipt.memory_peak_bytes == 1234
    assert publisher.count() == 1

    # Sampled-provenance backend carries sample gaps, not authoritative claims.
    sampled_backend = FakeBackend(capabilities=BEST_EFFORT_CAPS, merge_samples=True)
    supervisor2, _ = make_supervisor(
        backend=sampled_backend, sample_interval_seconds=0.001
    )

    def body(running):
        time.sleep(0.05)
        return ok_result()

    outcome2 = supervisor2.run(
        make_request("b"),
        launch_spec=make_spec(),
        launch_body=body,
    )
    receipt2 = outcome2.receipt
    assert receipt2.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt2.sample_gaps  # sampling cadence/gaps recorded
    # The peak is the max of the portable samples (>= the first sample).
    assert receipt2.memory_peak_bytes >= 1001


def test_cleanup_duration_and_sampling_gaps_represented():
    backend = FakeBackend()
    supervisor, _ = make_supervisor(backend=backend)
    outcome = supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert outcome.receipt.cleanup_duration_seconds == 0.5
    assert outcome.receipt.sample_gaps == (0.9, 1.1)
    assert outcome.receipt.quiescent is True


# ── policy immutability & injected grace ─────────────────────────────


def test_policy_snapshot_immutable_per_invocation():
    backend = FakeBackend()
    policy = make_policy(cleanup_grace_seconds=5.0)
    supervisor, _ = make_supervisor(backend=backend, policy=policy)
    with pytest.raises(AttributeError):
        policy.global_session_cap = 99  # type: ignore[misc]
    supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    supervisor.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    # Every prepare received the exact same immutable snapshot object.
    assert len(backend.prepare_policies) == 2
    assert backend.prepare_policies[0] is policy
    assert backend.prepare_policies[1] is policy


def test_cleanup_grace_is_injected_canary_input_not_constant():
    backend = FakeBackend()
    policy = make_policy(cleanup_grace_seconds=3.0)
    supervisor, _ = make_supervisor(backend=backend, policy=policy)
    supervisor.run(make_request("a"), launch_spec=make_spec(), launch_body=_ok_body)
    assert backend.finish_graces == [3.0]

    backend2 = FakeBackend()
    policy2 = make_policy(cleanup_grace_seconds=7.0)
    supervisor2, _ = make_supervisor(backend=backend2, policy=policy2)
    supervisor2.run(make_request("b"), launch_spec=make_spec(), launch_body=_ok_body)
    assert backend2.finish_graces == [7.0]


def test_effective_cap_derived_from_backend_capabilities_not_host():
    # Linux-style backend (enforcement guaranteed): cap = producer envelope.
    linux = FakeBackend(capabilities=GUARANTEED_CAPS)
    supervisor_linux, _ = make_supervisor(backend=linux, policy=canary_policy())
    assert supervisor_linux._admission.cap() == 11

    # macOS-style backend (no enforcement): binding cap 4 applies.
    macos = FakeBackend(capabilities=BEST_EFFORT_CAPS)
    supervisor_macos, _ = make_supervisor(backend=macos, policy=canary_policy())
    assert supervisor_macos._admission.cap() == 4


# ── shutdown / drain ─────────────────────────────────────────────────


def test_supervisor_shutdown_stops_admission_and_finishes_active():
    backend = FakeBackend()
    supervisor, publisher = make_supervisor(backend=backend, cap=1)

    holder_started = threading.Event()

    def body(running):
        holder_started.set()
        running.process.wait(timeout=10)
        return ok_result()

    t, results = run_in_thread(supervisor, make_request("a"), launch_body=body)
    assert holder_started.wait(timeout=5)

    # A second request queues behind A.
    t2, results2 = run_in_thread(supervisor, make_request("b"), launch_body=_ok_body)
    _wait_until(lambda: supervisor._admission.queue_depth() == 1)

    supervisor.shutdown()
    t.join(timeout=10)
    t2.join(timeout=10)
    assert not t.is_alive() and not t2.is_alive()
    # Active handle finished with SHUTDOWN (frozen first); queued cancelled.
    assert results[0].terminal_reason is TerminalReason.SHUTDOWN
    assert backend.finish_reasons == ["shutdown"]
    assert results2[0].cancelled_while_queued is True
    assert publisher.count() == 1
    assert supervisor._admission.active_count() == 0
    # B was never admitted (cancelled while queued), so only A's lease
    # was released — exactly once.
    assert supervisor._admission.released_total() == 1
