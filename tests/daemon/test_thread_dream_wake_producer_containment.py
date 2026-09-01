"""THR-207 thread/dream/wake producer containment: the REAL top-level thread,
dream, and wake producers through the daemon-wide ``HostSessionSupervisor``.

Drives the ACTUAL producer seams (``thread_runner.run_invocation``,
``dream_runner.run_dream``, ``wake_runner.run_wake``) with a real
``HostSessionSupervisor``, a deterministic fake backend, and fake executors
that expose the contained-launch seam — proving the brief's lifecycle
requirements from each real producer's perspective:

* every terminal path publishes exactly one bounded receipt via the
  supervisor's finish->residue->publish->lease-release ordering and releases
  the admission lease exactly once (no lease leak/deadlock);
* daemon drain (``supervisor.shutdown()``) with an active producer finishes
  containment with a durable SHUTDOWN winner and leaves the producer row for
  daemon-restart recovery (pre-wiring shutdown semantics);
* cancellation while running goes through the opaque containment handle and
  drives idempotent containment finish;
* failure before launch (no contained-launch seam / pre-launch validator) and
  after launch (backend spawn failure) fail the producer row closed;
* unsupported-capability fallback (passthrough backend) self-launches
  uncontained with the throttle's internal 429 retry disabled and honest
  ``unavailable`` receipt provenance;
* cleanup residue fail-closed: a guaranteed-cleanup INCOMPLETE receipt blocks
  admission until reconciled;
* publication failure is contained at the supervisor seam and never leaks the
  lease; the bounded ``HostSessionStore`` caps retained receipts;
* thread-specific semantics preserved: the session-not-found eviction
  fallback and the THR-071 nudge re-invoke run as additional supervised
  phases, each publishing its own honest receipt.

Hermetic: no daemon, no real systemd. Integration marker because it drives
the real producer seams.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from runtime.config import Settings
from runtime.daemon.host_session_store import HostSessionStore
from runtime.models import (
    DreamRecord,
    DreamStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    TokenUsage,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    AdmissionTimeout,
    CancellationToken,
    HostSessionSupervisor,
    canary_policy,
)
from runtime.platform.passthrough_backend import PassthroughBackend
from runtime.platform.session_backend import (
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
)

pytestmark = pytest.mark.integration

_AGENT = "dev_agent"


# ── fakes ─────────────────────────────────────────────────────────────


class _FakeProcess:
    def __init__(self, pid: int, auto_exit_after: float = 0.05) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._terminated = threading.Event()
        if auto_exit_after > 0:
            threading.Timer(auto_exit_after, self.terminate).start()

    def wait(self, timeout: float | None = None) -> bool:
        return self._terminated.wait(timeout)

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0
        self._terminated.set()

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = 137
        self._terminated.set()

    def communicate(self, input=None, timeout=None):
        self.wait(timeout)
        return "", ""


class _FakeBackend:
    """Deterministic SessionBackend: launch creates a FakeProcess; finish
    terminates it and returns a configurable receipt (clean by default)."""

    def __init__(
        self,
        *,
        capabilities: dict | None = None,
        auto_exit_after: float = 0.05,
        launch_error: str | None = None,
        finish_cleanup_status: CleanupStatus = CleanupStatus.CLEAN,
        finish_survivors: tuple = (),
        finish_raises: str | None = None,
    ) -> None:
        self.name = "fake"
        self.version = "1.0"
        self.capabilities = dict(capabilities or {})
        self.auto_exit_after = auto_exit_after
        self.launch_error = launch_error
        self.finish_cleanup_status = finish_cleanup_status
        self.finish_survivors = finish_survivors
        self.finish_raises = finish_raises
        self.calls: dict[str, int] = {
            "prepare": 0, "launch": 0, "finish": 0, "abandon": 0,
        }
        self.finish_reasons: list[str] = []
        self.requests: list = []
        self.last_running: RunningHandle | None = None
        self._lock = threading.Lock()

    def probe(self) -> CapabilityReport:
        return CapabilityReport(
            backend=self.name, backend_version=self.version,
            capabilities=self.capabilities, probed_at=time.monotonic(),
        )

    def prepare(self, request, policy) -> PendingHandle:
        with self._lock:
            self.calls["prepare"] += 1
            n = self.calls["prepare"]
            self.requests.append(request)
        return PendingHandle(
            backend=self.name, token=f"tok-{n}", request_id=request.logical_id,
        )

    def launch(self, pending: PendingHandle, spec: LaunchSpec) -> RunningHandle:
        with self._lock:
            self.calls["launch"] += 1
            n = self.calls["launch"]
        if self.launch_error is not None:
            from runtime.platform.session_backend import BackendLaunchError
            raise BackendLaunchError(self.launch_error)
        proc = _FakeProcess(pid=7000 + n, auto_exit_after=self.auto_exit_after)
        running = RunningHandle(
            backend=self.name, token=pending.token,
            request_id=pending.request_id, root_pid=proc.pid,
            start_identity="boot-1", process=proc,
        )
        with self._lock:
            self.last_running = running
        return running

    def sample(self, running: RunningHandle) -> ResourceSample:
        return ResourceSample(sampled_at=time.monotonic(), process_count=1)

    def finish(
        self, running, terminal_reason, grace_seconds,
        samples=None, sample_prefix_gap=0.0,
    ) -> Receipt:
        with self._lock:
            self.calls["finish"] += 1
            self.finish_reasons.append(terminal_reason)
        if self.finish_raises is not None:
            raise RuntimeError(self.finish_raises)
        if running.process is not None:
            running.process.terminate()
        return Receipt(
            backend=self.name,
            terminal_reason=terminal_reason,
            cleanup_status=self.finish_cleanup_status,
            cleanup_duration_seconds=0.0,
            quiescent=(
                self.finish_cleanup_status is not CleanupStatus.INCOMPLETE
                and not self.finish_survivors
            ),
            wall_time_seconds=0.0,
            memory_peak_bytes=1234,
            memory_peak_provenance=MeasurementProvenance.SAMPLED,
            survivors=self.finish_survivors,
        )

    def abandon(self, pending: PendingHandle) -> None:
        with self._lock:
            self.calls["abandon"] += 1

    def recover(self, handle_token: str) -> RecoveryResult:
        return RecoveryResult(recovered=False, evidence="none")


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, model="claude-opus")


def _result(
    *,
    success: bool = True,
    error: str | None = None,
    rate_limited: bool = False,
    token_usage: TokenUsage | None = None,
    agent_session_id: str | None = None,
) -> ExecutorResult:
    return ExecutorResult(
        success=success, duration_seconds=1, session_id="sess-x",
        error=error, rate_limited=rate_limited,
        token_usage=token_usage, agent_session_id=agent_session_id,
    )


class _RecordingExecutor:
    """Fake executor with the contained-launch seam: records whether it was
    handed a backend RunningHandle, the throttle retry seam, and the
    pre-launch validator; optionally blocks on the live process until the
    backend finish terminates it (drain/cancel tests)."""

    def __init__(
        self,
        *,
        results: list[ExecutorResult] | None = None,
        on_entered: threading.Event | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self._results = list(results) if results is not None else [
            _result(token_usage=_token_usage())
        ]
        self._on_entered = on_entered
        self._on_complete = on_complete
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def set_invocation_context(self, **kwargs):
        pass

    def build_launch_spec(
        self, *, workspace, prompt, session_id=None, model=None,
        resume_session_id=None, org_slug=None, timeout_seconds=1800,
    ) -> LaunchSpec:
        argv = ["fake-cli"]
        if resume_session_id:
            argv += ["--resume", resume_session_id]
        return LaunchSpec(argv=tuple(argv), cwd=str(workspace), env={})

    def run(
        self, *, workspace, prompt, session_id, timeout_seconds,
        on_started=None, on_throttle_event=None, model=None,
        pre_launch_validator=None, org_slug=None,
        running=None, throttle_backoff_seconds=None, **kwargs,
    ):
        with self.lock:
            self.calls.append({
                "running": running,
                "throttle_backoff_seconds": throttle_backoff_seconds,
                "pre_launch_validator": pre_launch_validator,
                "resume_session_id": kwargs.get("resume_session_id"),
            })
        if self._on_entered is not None:
            self._on_entered.set()
        if running is not None and running.process is not None:
            # Containment mode: the body blocks on the live process (like
            # communicate()) until the backend finish terminates the tree.
            running.process.wait(timeout=max(timeout_seconds, 1))
        if self._on_complete is not None:
            self._on_complete()
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        return result


class _NoSpecExecutor:
    """Fake executor WITHOUT the contained-launch seam — the producer fails
    closed before admission."""

    def run(self, *, workspace, prompt, session_id, timeout_seconds, **kwargs):
        raise AssertionError("a spec-less executor must never run")


class _CorruptingExecutor(_RecordingExecutor):
    """build_launch_spec removes the materialized skill link so the
    supervisor's pre-launch integrity validator fails deterministically
    (a real pre-launch failure, before any containment handle)."""

    def build_launch_spec(
        self, *, workspace, prompt, session_id=None, model=None,
        resume_session_id=None, org_slug=None, timeout_seconds=1800,
    ) -> LaunchSpec:
        import shutil
        link = Path(workspace) / ".claude" / "skills"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link)
        return super().build_launch_spec(
            workspace=workspace, prompt=prompt, session_id=session_id,
            model=model, resume_session_id=resume_session_id,
            org_slug=org_slug, timeout_seconds=timeout_seconds,
        )


# ── harness ───────────────────────────────────────────────────────────


def _make_supervisor(
    backend,
    *,
    publisher: Callable[[Receipt], None] | None = None,
    max_retry_attempts: int = 0,
    backoff_seconds=(),
) -> HostSessionSupervisor:
    return HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.0),
        publisher=publisher or (lambda receipt: None),
        max_retry_attempts=max_retry_attempts,
        backoff_seconds=backoff_seconds,
    )


def _seed_agent(org_state, name: str = _AGENT) -> Path:
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "You are a developer.\n"
    )
    workspace = org_state.root / "workspaces" / name
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "agent.yaml").write_text("executor: claude\n")
    return workspace


def _seed_dream(org_state, dream_id: str = "DREAM-001", local_date: str = "2026-06-09") -> None:
    now = datetime.now(timezone.utc)
    org_state.db.insert_dream(DreamRecord(
        id=dream_id,
        agent_name=_AGENT,
        local_date=local_date,
        scheduled_for=now,
        window_start=now,
        window_end=now,
    ))


def _seed_wake(org_state, work_hour_id: str = "WORKHOUR-001") -> None:
    org_state.db.work_hours.insert(WorkHourRecord(
        id=work_hour_id,
        agent_name=_AGENT,
        local_date="2026-06-11",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
        status=WorkHourStatus.PENDING,
        routine_count=1,
    ))


def _seed_thread_invocation(
    org_state, *, thread_id: str = "THR-001", agent_name: str = _AGENT,
    purpose=ThreadInvocationPurpose.BOOTSTRAP, resume_sid: str | None = None,
    last_resumed_seq: int = 0,
) -> str:
    org_state.db.insert_thread(ThreadRecord(id=thread_id, subject="x"))
    org_state.db.add_thread_participant(thread_id, agent_name, added_by="founder")
    org_state.db.append_thread_message(
        thread_id=thread_id, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    if resume_sid is not None:
        org_state.db.update_thread_session(
            thread_id, agent_name,
            agent_session_id=resume_sid, last_resumed_seq=last_resumed_seq,
        )
    inv = org_state.db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=1, purpose=purpose,
    )
    return inv.invocation_token


async def _wait_thread_event(ev: threading.Event, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not ev.is_set():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for launch body to enter")
        await asyncio.sleep(0.01)


# ── dream producer ────────────────────────────────────────────────────


async def test_dream_clean_success_receipt_and_callback(org_state):
    """Dream clean success: the executor simulates the ``dreams complete``
    callback, the row completes, one bounded receipt with terminal=success is
    published, and the lease is released exactly once."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []

    def _complete():
        org_state.db.update_dream(
            "DREAM-001", status=DreamStatus.COMPLETED,
            ended_at=datetime.now(timezone.utc), session_id="sess-dream",
        )

    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(on_complete=_complete)

    await run_dream_producer(org_state, supervisor, executor)

    assert org_state.db.get_dream("DREAM-001").status == DreamStatus.COMPLETED
    assert len(receipts) == 1
    assert receipts[0].terminal_reason == "success"
    assert receipts[0].cleanup_status is CleanupStatus.CLEAN
    assert backend.calls["finish"] == 1
    assert backend.calls["prepare"] == 1 and backend.calls["launch"] == 1
    # Contained handle handed to the executor; no internal 429 retry seam.
    assert len(executor.calls) == 1
    assert executor.calls[0]["running"] is backend.last_running
    # Exactly-once lease release — no leak.
    assert supervisor.active_count() == 0
    assert supervisor._admission.admitted_total() == 1
    assert supervisor._admission.released_total() == 1


async def test_dream_no_callback_fails_row(org_state):
    """Clean exit without the callback: the runner marks the dream FAILED
    (no_callback) and publishes the honest success receipt."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)

    await run_dream_producer(org_state, supervisor, _RecordingExecutor())

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in (dream.error or "")
    assert len(receipts) == 1 and receipts[0].terminal_reason == "success"
    assert supervisor.active_count() == 0


async def test_dream_timeout_receipt_and_row(org_state):
    """Executor timeout: the row transitions TIMEOUT and the receipt's frozen
    terminal reason is timeout (first-wins, cleanup preserved)."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(results=[
        _result(success=False, error="Session timed out after 1800 seconds"),
    ])

    await run_dream_producer(org_state, supervisor, executor)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.TIMEOUT
    assert len(receipts) == 1 and receipts[0].terminal_reason == "timeout"
    assert supervisor.active_count() == 0


async def test_dream_failure_receipt_and_row(org_state):
    """Nonzero/provider failure: the row transitions FAILED and the receipt
    freezes failure as the primary terminal reason."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(results=[
        _result(success=False, error="executor crashed"),
    ])

    await run_dream_producer(org_state, supervisor, executor)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "executor crashed" in (dream.error or "")
    assert len(receipts) == 1 and receipts[0].terminal_reason == "failure"
    assert supervisor.active_count() == 0


async def test_dream_pre_launch_validator_failure_no_handle(org_state):
    """A pre-launch integrity failure inside the supervisor refuses launch
    BEFORE any containment handle: the row fails closed with the durable
    reason and no prepare/launch/finish state is created."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend)

    await run_dream_producer(org_state, supervisor, _CorruptingExecutor())

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "before launch" in (dream.error or "")
    assert "pre-launch validation failed" in (dream.error or "")
    assert backend.calls["prepare"] == 0
    assert backend.calls["launch"] == 0
    assert backend.calls["finish"] == 0
    assert supervisor.active_count() == 0


async def test_dream_no_containment_seam_fails_closed_before_admission(org_state):
    """An executor without ``build_launch_spec`` fails closed: the row is
    FAILED and no admission/containment state is created."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend)

    await run_dream_producer(org_state, supervisor, _NoSpecExecutor())

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "contained launch" in (dream.error or "")
    assert backend.calls["prepare"] == 0 and backend.calls["launch"] == 0
    assert supervisor.active_count() == 0


async def test_dream_spawn_failure_fails_row(org_state):
    """Backend launch failure (after prepare): SPAWN_FAILURE, partial
    containment abandoned, row FAILED, no receipt (no live handle)."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    backend = _FakeBackend(launch_error="scope launch exploded")
    supervisor = _make_supervisor(backend)

    await run_dream_producer(org_state, supervisor, _RecordingExecutor())

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "spawn_failure" in (dream.error or "")
    assert backend.calls["prepare"] == 1
    assert backend.calls["finish"] == 0
    assert supervisor.active_count() == 0


async def test_dream_daemon_drain_with_active_producer(org_state):
    """Daemon drain while a dream is running: the durable SHUTDOWN winner
    finishes containment exactly once and the RUNNING row is left for
    daemon-restart recovery — the pre-wiring shutdown semantics."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    entered = threading.Event()
    backend = _FakeBackend(auto_exit_after=0.0)  # never exits on its own
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(on_entered=entered)

    task = asyncio.create_task(run_dream_producer(org_state, supervisor, executor))
    await _wait_thread_event(entered)
    supervisor.shutdown()
    await asyncio.wait_for(task, 10)

    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["shutdown"]
    assert len(receipts) == 1 and receipts[0].terminal_reason == "shutdown"
    # Interrupted: the row is left RUNNING for restart recovery.
    assert org_state.db.get_dream("DREAM-001").status == DreamStatus.RUNNING
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_dream_passthrough_unsupported_capability_fallback(org_state):
    """Unsupported-capability fallback: the honest passthrough backend yields
    an uncontained self-launch (running=None) with the throttle's internal 429
    retry disabled and unavailable receipt provenance — never a fabricated
    value."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    backend = PassthroughBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(results=[
        _result(success=True),  # no callback -> no_callback FAILED
    ])

    await run_dream_producer(org_state, supervisor, executor)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in (dream.error or "")
    assert len(executor.calls) == 1
    assert executor.calls[0]["running"] is None
    assert executor.calls[0]["throttle_backoff_seconds"] == ()
    assert len(receipts) == 1
    assert receipts[0].memory_peak_provenance is MeasurementProvenance.UNAVAILABLE
    assert supervisor.active_count() == 0


async def test_dream_residue_fail_closed_blocks_admission(org_state):
    """Guaranteed-cleanup INCOMPLETE residue is fail-closed: the accountant
    records a measurement failure that blocks further admission until
    reconciled (an honest bound, never a silently-clean release)."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    backend = _FakeBackend(
        capabilities={Capability.KILLS_TREE_GUARANTEED: CapabilityLevel.GUARANTEED},
        finish_cleanup_status=CleanupStatus.INCOMPLETE,
    )
    supervisor = _make_supervisor(backend)

    await run_dream_producer(org_state, supervisor, _RecordingExecutor())

    assert backend.calls["finish"] == 1
    snapshot = supervisor.health_snapshot()
    assert snapshot["residue"]["admission_blocked"] is True
    assert snapshot["residue"]["block_reason"] == "measurement_unhealthy"
    # A subsequent attempt stalls at admission (no launch) — bounded wait.
    request = AdmissionRequest(
        org="test", invocation_kind="dream", logical_id="DREAM-002",
        executor_profile="claude", enqueued_at=time.monotonic(),
    )
    with pytest.raises(AdmissionTimeout):
        supervisor.run(
            request, launch_spec=LaunchSpec(argv=("x",)),
            launch_body=lambda running: None, timeout=0.2,
        )
    assert supervisor.active_count() == 0


async def test_dream_cancellation_while_running_producer_shaped(org_state):
    """Cancellation while running goes through the opaque containment handle
    (never a bare PID signal): the durable CANCELLED winner drives idempotent
    containment finish exactly once and the lease releases."""
    entered = threading.Event()
    backend = _FakeBackend(auto_exit_after=0.0)
    receipts: list[Receipt] = []
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(on_entered=entered)
    token = CancellationToken()

    # The exact request + launch-body shape the dream producer uses.
    launch_spec = LaunchSpec(argv=("fake-cli",), cwd=".", env={})
    outcome_holder: dict = {}

    def _run():
        outcome_holder["outcome"] = supervisor.run(
            AdmissionRequest(
                org="test", invocation_kind="dream", logical_id="DREAM-001",
                executor_profile="claude", enqueued_at=time.monotonic(),
                cancellation=token,
            ),
            launch_spec=launch_spec,
            launch_body=lambda running: _launch_body_dream(
                executor, running, "DREAM-001",
            ),
        )

    thread = threading.Thread(target=_run)
    thread.start()
    await _wait_thread_event(entered)
    token.cancel()
    thread.join(timeout=10)
    assert not thread.is_alive()

    outcome = outcome_holder["outcome"]
    assert outcome.terminal_reason.value == "cancelled"
    assert backend.calls["finish"] == 1
    assert len(receipts) == 1 and receipts[0].terminal_reason == "cancelled"
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


# ── wake producer ─────────────────────────────────────────────────────


async def test_wake_clean_success_receipt_and_callback(org_state):
    """Wake clean success with the spawn callback simulated: the row
    completes, token usage lands under the work_hour scope, and one success
    receipt is published with exactly-once lease release."""
    _seed_agent(org_state)
    _seed_wake(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)

    def _spawn():
        org_state.db.work_hours.update(
            "WORKHOUR-001", status=WorkHourStatus.COMPLETED,
        )

    executor = _RecordingExecutor(on_complete=_spawn)

    await run_wake_producer(org_state, supervisor, executor)

    wh = org_state.db.work_hours.get("WORKHOUR-001")
    assert wh.status == WorkHourStatus.COMPLETED
    rows = org_state.db.list_session_token_usage(scope_type="work_hour")
    assert len(rows) == 1 and rows[0]["scope_id"] == "WORKHOUR-001"
    assert len(receipts) == 1 and receipts[0].terminal_reason == "success"
    assert len(executor.calls) == 1
    assert executor.calls[0]["running"] is backend.last_running
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_wake_no_callback_fails_row(org_state):
    """Wake clean exit without the spawn callback: FAILED/no_callback with an
    honest success receipt."""
    _seed_agent(org_state)
    _seed_wake(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)

    await run_wake_producer(org_state, supervisor, _RecordingExecutor())

    wh = org_state.db.work_hours.get("WORKHOUR-001")
    assert wh.status == WorkHourStatus.FAILED
    assert "no_callback" in (wh.error or "")
    assert len(receipts) == 1 and receipts[0].terminal_reason == "success"
    assert supervisor.active_count() == 0


async def test_wake_daemon_drain_with_active_producer(org_state):
    """Daemon drain while a wake is running: the durable SHUTDOWN winner
    finishes containment exactly once and the RUNNING row is left for
    daemon-restart recovery (``work_hours.recover_running``)."""
    _seed_agent(org_state)
    _seed_wake(org_state)
    entered = threading.Event()
    receipts: list[Receipt] = []
    backend = _FakeBackend(auto_exit_after=0.0)
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(on_entered=entered)

    task = asyncio.create_task(run_wake_producer(
        org_state, supervisor, executor,
    ))
    await _wait_thread_event(entered)
    supervisor.shutdown()
    await asyncio.wait_for(task, 10)

    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["shutdown"]
    assert len(receipts) == 1 and receipts[0].terminal_reason == "shutdown"
    assert org_state.db.work_hours.get("WORKHOUR-001").status == WorkHourStatus.RUNNING
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


# ── thread producer ───────────────────────────────────────────────────


async def test_thread_no_callback_nudge_runs_second_contained_phase(org_state, monkeypatch):
    """Thread clean exit without the terminal callback: the THR-071 nudge
    re-invoke runs as a SECOND supervised phase (its own admission lease +
    receipt), and the row settles FAILED/no_callback_after_reprompt."""
    _seed_agent(org_state)
    token = _seed_thread_invocation(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(results=[
        _result(success=True),
        _result(success=True),
    ])

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: executor,
    )

    await run_invocation_producer(
        org_state, token, supervisor, settings=Settings(),
    )

    after = org_state.db.get_invocation_any_status(token)
    assert after.status.value in {"failed", "timeout"}
    assert "no_callback" in (after.decline_reason or "")
    # Two contained phases: initial + nudge; each published one honest receipt.
    assert len(executor.calls) == 2
    assert len(receipts) == 2
    assert all(r.terminal_reason == "success" for r in receipts)
    assert backend.calls["finish"] == 2
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_thread_session_not_found_fallback_uses_full_prompt_phase(org_state, monkeypatch):
    """Thread resume eviction: the session-not-found fallback re-runs the
    invocation as a second supervised phase WITHOUT the resume session, and
    the final result drives the row classification."""
    _seed_agent(org_state)
    token = _seed_thread_invocation(
        org_state, resume_sid="sess-stale", last_resumed_seq=1,
    )
    org_state.db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="new since resume",
    )
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    session_not_found = _result(
        success=False, error="No conversation found with session ID: sess-stale",
    )
    session_not_found.returncode = 1
    session_not_found.stderr_tail = (
        "No conversation found with session ID: sess-stale"
    )
    executor = _RecordingExecutor(results=[
        session_not_found,
        _result(success=True),
    ])

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: executor,
    )

    await run_invocation_producer(
        org_state, token, supervisor, settings=Settings(),
    )

    after = org_state.db.get_invocation_any_status(token)
    assert after.status.value in {"failed", "timeout"}
    assert "no_callback" in (after.decline_reason or "")
    # Phase 1 carried the stale resume; phase 2 (fallback) did not.
    assert len(executor.calls) == 2
    assert executor.calls[0]["resume_session_id"] == "sess-stale"
    assert executor.calls[1]["resume_session_id"] is None
    assert org_state.db.get_thread_session("THR-001", _AGENT) == (None, 1)
    eviction_audits = [
        row for row in org_state.db.get_audit_logs("THR-001")
        if row["action"] == "agent_session_evicted_fallback"
    ]
    assert len(eviction_audits) == 1
    assert eviction_audits[0]["payload"]["stale_session_id"] == "sess-stale"
    assert len(receipts) == 2
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_thread_daemon_drain_with_active_producer(org_state, monkeypatch):
    """Daemon drain while a thread invocation is running: the durable SHUTDOWN
    winner finishes containment exactly once and the still-pending invocation
    row is left for daemon-restart recovery."""
    _seed_agent(org_state)
    token = _seed_thread_invocation(org_state)
    entered = threading.Event()
    receipts: list[Receipt] = []
    backend = _FakeBackend(auto_exit_after=0.0)
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    executor = _RecordingExecutor(on_entered=entered)

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: executor,
    )

    task = asyncio.create_task(run_invocation_producer(
        org_state, token, supervisor, settings=Settings(),
    ))
    await _wait_thread_event(entered)
    supervisor.shutdown()
    await asyncio.wait_for(task, 10)

    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["shutdown"]
    assert len(receipts) == 1 and receipts[0].terminal_reason == "shutdown"
    # Interrupted: the row is left pending for restart recovery.
    after = org_state.db.get_invocation_any_status(token)
    assert after.status == ThreadInvocationStatus.PENDING
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_thread_no_containment_seam_settles_row_failed(org_state, monkeypatch):
    """Thread producer, executor without the contained-launch seam: the row
    settles FAILED with the durable reason (no handle, no lease leak)."""
    _seed_agent(org_state)
    token = _seed_thread_invocation(org_state)
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend)

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _NoSpecExecutor(),
    )

    await run_invocation_producer(
        org_state, token, supervisor, settings=Settings(),
    )

    after = org_state.db.get_invocation_any_status(token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert "before launch" in (after.decline_reason or "")
    assert "contained launch" in (after.decline_reason or "")
    assert backend.calls["prepare"] == 0 and backend.calls["launch"] == 0
    assert supervisor.active_count() == 0


async def test_thread_429_retry_owned_by_supervisor(org_state, monkeypatch):
    """429 retry ownership: a rate-limited contained attempt fully finishes,
    releases the lease, sleeps without capacity, and reacquires with a fresh
    backend handle (the supervisor's retry loop, not the executor throttle)."""
    _seed_agent(org_state)
    token = _seed_thread_invocation(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(
        backend, publisher=receipts.append,
        max_retry_attempts=1, backoff_seconds=(0.0,),
    )
    executor = _RecordingExecutor(results=[
        _result(success=False, rate_limited=True, error="rate limit hit"),
        _result(success=True),
        _result(success=True),
    ])

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: executor,
    )

    await run_invocation_producer(
        org_state, token, supervisor, settings=Settings(),
    )

    after = org_state.db.get_invocation_any_status(token)
    assert after.status.value in {"failed", "timeout"}
    # Two attempts plus the THR-071 nudge phase: each ran its own contained
    # lease, containment finish, and honest receipt.
    assert len(executor.calls) == 3
    assert len(receipts) == 3
    assert receipts[0].terminal_reason == "rate_limited"
    assert receipts[1].terminal_reason == "success"
    assert receipts[2].terminal_reason == "success"
    assert backend.calls["finish"] == 3
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


# ── shared lifecycle invariants ───────────────────────────────────────


async def test_publication_failure_contained_no_lease_leak(org_state):
    """A raising receipt publisher is contained at the supervisor seam: it
    never replaces the primary terminal reason, never disrupts the cleanup
    ordering, and never leaks the admission lease."""
    _seed_agent(org_state)
    _seed_dream(org_state)
    backend = _FakeBackend()
    raised: list[str] = []

    def _raising_publisher(receipt):
        raised.append(receipt.terminal_reason)
        raise RuntimeError("store exploded")

    supervisor = _make_supervisor(backend, publisher=_raising_publisher)

    await run_dream_producer(org_state, supervisor, _RecordingExecutor())

    assert len(raised) == 1
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED  # no_callback primary reason intact
    assert backend.calls["finish"] == 1
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_bounded_publication_via_host_session_store(org_state):
    """Receipts land in the bounded in-memory HostSessionStore (at most
    ``max_receipts`` retained) across multiple producer invocations."""
    _seed_agent(org_state)
    backend = _FakeBackend()
    store = HostSessionStore(max_receipts=4)
    supervisor = _make_supervisor(backend, publisher=store.publish)

    for i in range(6):
        dream_id = f"DREAM-{i:03d}"
        _seed_dream(
            org_state, dream_id=dream_id, local_date=f"2026-06-{10 + i:02d}",
        )
        await run_dream_producer(
            org_state, supervisor, _RecordingExecutor(), dream_id=dream_id,
        )

    snapshot = store.snapshot()
    assert snapshot["window_size"] == 4  # bounded, oldest dropped
    assert snapshot["published_total"] == 6
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()


async def test_task_session_and_dream_share_supervisor_with_session_tracker(org_state, monkeypatch):
    """Daemon-lifespan/SessionTracker integration: a task session (with its
    SessionTracker opaque cancel control) and a dream producer run through the
    SAME supervisor without lease corruption; the drain finishes both and the
    tracker is cleared after the task's finalization."""
    from runtime.daemon.sessions import SessionTracker

    _seed_agent(org_state)
    _seed_dream(org_state)
    receipts: list[Receipt] = []
    backend = _FakeBackend()
    supervisor = _make_supervisor(backend, publisher=receipts.append)
    tracker = SessionTracker()

    # A task-shaped session registers an opaque cancel control with
    # SessionTracker exactly as the task producer does, and clears it on the
    # final terminal path via the supervisor's on_terminal hook.
    token = CancellationToken()
    tracker.set_cancel_control("TASK-1", _AGENT, "sess-task", token.cancel)

    def _clear_tracker():
        tracker.clear_if_active_session("TASK-1", _AGENT, "sess-task")

    entered = threading.Event()
    executor = _RecordingExecutor(on_entered=entered)
    dream_task = asyncio.create_task(run_dream_producer(
        org_state, supervisor, executor,
    ))
    await _wait_thread_event(entered)

    # Concurrent task-shaped run through the same supervisor.
    task_outcome_holder: dict = {}

    def _run_task():
        task_outcome_holder["outcome"] = supervisor.run(
            AdmissionRequest(
                org="test", invocation_kind="task", logical_id="TASK-1",
                executor_profile="claude", enqueued_at=time.monotonic(),
                cancellation=token,
            ),
            launch_spec=LaunchSpec(argv=("fake-cli",), cwd=".", env={}),
            launch_body=lambda running: _launch_body_generic(executor, running),
            on_terminal=lambda _outcome: _clear_tracker(),
        )

    thread = threading.Thread(target=_run_task)
    thread.start()
    while supervisor.active_count() < 2:
        await asyncio.sleep(0.01)

    supervisor.shutdown()
    thread.join(timeout=10)
    await asyncio.wait_for(dream_task, 10)

    assert not thread.is_alive()
    assert task_outcome_holder["outcome"].terminal_reason.value == "shutdown"
    # Both producers finished containment; leases all released exactly once.
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == supervisor._admission.admitted_total()
    assert backend.calls["finish"] == 2
    assert len(receipts) == 2
    # The task session's SessionTracker binding is cleared by the terminal
    # hook AFTER the supervisor finalized/reconciled and BEFORE lease release.
    assert tracker.get_cancel_control("TASK-1", _AGENT) is None
    assert tracker.get_active("TASK-1", _AGENT) is None


# ── producers (real seams, thin wrappers for readability) ─────────────


async def run_dream_producer(org_state, supervisor, executor, dream_id: str = "DREAM-001"):
    from runtime.daemon.dream_runner import run_dream
    await run_dream(
        org_state=org_state, dream_id=dream_id, settings=Settings(),
        executor_factory=lambda name, settings, paths: executor,
        host_supervisor=supervisor,
    )


async def run_wake_producer(org_state, supervisor, executor, work_hour_id: str = "WORKHOUR-001"):
    from runtime.daemon.wake_runner import run_wake
    await run_wake(
        org_state=org_state, work_hour_id=work_hour_id, settings=Settings(),
        executor_factory=lambda name, settings, paths: executor,
        host_supervisor=supervisor,
    )


async def run_invocation_producer(org_state, invocation_token, supervisor, *, settings):
    from runtime.daemon.thread_runner import run_invocation
    await run_invocation(
        org_state=org_state, invocation_token=invocation_token,
        settings=settings, host_supervisor=supervisor,
    )


def _launch_body_dream(executor, running, dream_id) -> "object":
    """The exact launch-body shape the dream producer wires (thin re-declared
    here so the cancellation test drives the producer's containment shape)."""
    from runtime.orchestrator.host_supervisor import LaunchResult
    contained = running.process is not None
    res = executor.run(
        workspace=".", prompt="prompt", session_id="sess-x",
        timeout_seconds=1800,
        pre_launch_validator=None if contained else (lambda: None),
        org_slug="test", model=None,
        running=running if contained else None,
        throttle_backoff_seconds=() if not contained else None,
    )
    return LaunchResult(
        success=res.success, duration_seconds=1, returncode=None,
        error=getattr(res, "error", None),
        rate_limited=bool(getattr(res, "rate_limited", False)),
        timed_out=False, payload=res,
    )


def _launch_body_generic(executor, running) -> "object":
    from runtime.orchestrator.host_supervisor import LaunchResult
    contained = running.process is not None
    res = executor.run(
        workspace=".", prompt="prompt", session_id="sess-x",
        timeout_seconds=1800,
        pre_launch_validator=None if contained else (lambda: None),
        org_slug="test", model=None,
        running=running if contained else None,
        throttle_backoff_seconds=() if not contained else None,
    )
    return LaunchResult(
        success=res.success, duration_seconds=1, returncode=None,
        error=getattr(res, "error", None),
        rate_limited=bool(getattr(res, "rate_limited", False)),
        timed_out=False, payload=res,
    )
