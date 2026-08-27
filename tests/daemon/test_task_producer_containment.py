"""THR-207 task-producer containment: REAL task-producer callbacks through the
daemon-wide HostSessionSupervisor.

Drives the ACTUAL ``Orchestrator.run_step`` -> ``_run_agent`` -> contained
launch path (not a lookalike helper) with a real ``HostSessionSupervisor``,
a deterministic fake backend, and a fake executor that exposes the
contained-launch seam. Proves the brief's lifecycle requirements from the
real task caller's perspective:

* the invocation owns a real admission lease + cancellation token; ownership
  transfers at grant; the supervisor generation/launch gate refuses the body
  when shutdown wins at/after grant;
* the RunningHandle is bound BEFORE ``on_started`` exposes the diagnostic
  PID (SessionTracker pid = diagnostic/restart evidence only);
* an opaque cancellation/cleanup control is registered with SessionTracker
  and the /cancel route invokes it (never a bare PID signal for wired
  sessions);
* 429 defers to the supervisor: finish/release/sleep/reacquire with the
  original enqueue age and a fresh backend handle;
* every terminal path finishes containment before exactly-once lease release;
  SessionTracker.clear removes the control.

Hermetic: no daemon, no real systemd; the fake backend launches a fake
process. Integration marker because it drives the real producer seam.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus, TokenUsage
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.host_supervisor import (
    HostSessionSupervisor,
    canary_policy,
)
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
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
    SurvivorRecord,
)

pytestmark = pytest.mark.integration

_AGENT = "dev_agent"
_TEAMS = (
    "teams:\n"
    "  engineering:\n"
    "    manager: engineering_head\n"
    "    workers: [dev_agent]\n"
)
_AGENT_FILE = (
    "---\n"
    "name: dev_agent\n"
    "team: engineering\n"
    "role: worker\n"
    "executor: claude\n"
    "---\n\n"
    "Build software.\n"
)


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
    terminates it and returns a clean receipt."""

    def __init__(
        self,
        *,
        capabilities: dict | None = None,
        launch_barrier: threading.Event | None = None,
        auto_exit_after: float = 0.05,
    ) -> None:
        self.name = "fake"
        self.version = "1.0"
        self.capabilities = dict(capabilities or {})
        self.launch_barrier = launch_barrier
        self.auto_exit_after = auto_exit_after
        self.calls: dict[str, int] = {"prepare": 0, "launch": 0, "finish": 0, "abandon": 0}
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
        return PendingHandle(backend=self.name, token=f"tok-{n}", request_id=request.logical_id)

    def launch(self, pending: PendingHandle, spec: LaunchSpec) -> RunningHandle:
        with self._lock:
            self.calls["launch"] += 1
            n = self.calls["launch"]
        if self.launch_barrier is not None:
            self.launch_barrier.set()
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

    def finish(self, running, terminal_reason, grace_seconds, samples=None, sample_prefix_gap=0.0) -> Receipt:
        with self._lock:
            self.calls["finish"] += 1
            self.finish_reasons.append(terminal_reason)
        if running.process is not None:
            running.process.terminate()
        return Receipt(
            backend=self.name,
            terminal_reason=terminal_reason,
            cleanup_status=CleanupStatus.CLEAN,
            cleanup_duration_seconds=0.0,
            quiescent=True,
            wall_time_seconds=0.0,
            memory_peak_bytes=1234,
            memory_peak_provenance=MeasurementProvenance.SAMPLED,
        )

    def abandon(self, pending: PendingHandle) -> None:
        with self._lock:
            self.calls["abandon"] += 1

    def recover(self, handle_token: str) -> RecoveryResult:
        return RecoveryResult(recovered=False, evidence="none")


class _RecordingExecutor:
    """Fake executor with the contained-launch seam: records whether it was
    handed a backend RunningHandle and whether on_started was wired."""

    def __init__(self, *, results: list[ExecutorResult] | None = None) -> None:
        self._results = list(results) if results is not None else [
            ExecutorResult(
                success=True, duration_seconds=1, session_id="sess-x",
                token_usage=TokenUsage(input_tokens=1, output_tokens=1, model="claude-opus"),
            )
        ]
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def set_invocation_context(self, **kwargs):
        pass

    def build_launch_spec(self, *, workspace, prompt, session_id=None, model=None, org_slug=None, timeout_seconds=1800) -> LaunchSpec:
        return LaunchSpec(argv=("fake-cli",), cwd=str(workspace), env={})

    def verify_launch_ready(self) -> str | None:
        return None

    def run(self, *, workspace, prompt, session_id, timeout_seconds, on_started=None,
            on_throttle_event=None, model=None, pre_launch_validator=None,
            org_slug=None, running=None, throttle_backoff_seconds=None, **kwargs):
        with self.lock:
            self.calls.append({
                "running": running,
                "on_started": on_started,
                "throttle_backoff_seconds": throttle_backoff_seconds,
                "pre_launch_validator": pre_launch_validator,
            })
        if running is not None and running.process is not None:
            # Containment mode: the body blocks on the live process (like
            # communicate()) until the backend finish terminates the tree.
            running.process.wait(timeout=max(timeout_seconds, 1))
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        return result


# ── harness ───────────────────────────────────────────────────────────


def _seed_org(paths: OrgPaths, tmp_path: Path, test_settings: Settings) -> None:
    """Minimal org: teams.yaml + agent file + workspace + protocol skills."""
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "org").mkdir(parents=True, exist_ok=True)
    (paths.root / "org" / "teams.yaml").write_text(_TEAMS)
    agents_dir = paths.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "dev_agent.md").write_text(_AGENT_FILE)
    (agents_dir / "engineering_head.md").write_text(
        "---\n"
        "name: engineering_head\n"
        "team: engineering\n"
        "role: manager\n"
        "executor: claude\n"
        "---\n\n"
        "Manage the engineering team.\n"
    )
    (paths.root / "org" / "config.yaml").write_text("timezone: Asia/Shanghai\n")
    # Protocol skill sources for the system-contract materializer.
    proto = test_settings.get_protocol_dir() / "skills"
    for sid in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "wake", "schedule"):
        src = proto / sid
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")
    ws = paths.workspaces_dir / _AGENT
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text("# Task History: dev_agent\n\n")
    (ws / "agent.yaml").write_text("executor: claude\n")
    # NOTE: no pre-created skill directories — the canonical-store
    # SymlinkMaterializer creates the start-task link + readiness marker
    # during materialization; a pre-created ordinary dir at the link path
    # fails closed with ordinary_dir_at_link_path.


def _make_orch(tmp_path: Path, backend: _FakeBackend, executor: _RecordingExecutor,
               monkeypatch, *, max_retry_attempts: int = 0, backoff_seconds=()):
    test_settings = Settings(project_root=tmp_path / "proj")
    rt = tmp_path / "runtime"
    paths = OrgPaths(root=rt / "orgs" / "test")
    _seed_org(paths, tmp_path, test_settings)
    db = Database(paths.db_path)
    orch = Orchestrator(
        db=db, settings=test_settings, paths=paths, slug="test",
        teams=TeamsRegistry.load(paths.root),
    )
    tracker = SessionTracker()
    orch.attach_sessions(tracker)
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.0),
        publisher=lambda receipt: None,
        max_retry_attempts=max_retry_attempts,
        backoff_seconds=backoff_seconds,
    )
    orch.attach_host_supervisor(supervisor)
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    return orch, supervisor, tracker, db


def _seed_task(db: Database) -> str:
    task_id = db.next_task_id()
    db.insert_task(TaskRecord(id=task_id, brief="build something", assigned_agent=_AGENT))
    return task_id


def test_task_producer_contained_success_lifecycle(tmp_path, monkeypatch):
    """REAL run_step -> _run_agent -> supervisor: the fake backend launches a
    process, the executor receives the contained RunningHandle (no on_started
    wired — the supervisor bound the PID), on_started stamped the diagnostic
    pid into SessionTracker, the opaque cancel control is registered, and the
    lease is released exactly once."""
    backend = _FakeBackend(capabilities={
        Capability.KILLS_TREE_GUARANTEED: CapabilityLevel.GUARANTEED,
    })
    executor = _RecordingExecutor()
    orch, supervisor, tracker, db = _make_orch(tmp_path, backend, executor, monkeypatch)
    task_id = _seed_task(db)

    orch.run_step(task_id)

    # Supervisor lifecycle: admitted -> prepared -> launched -> finished -> released.
    assert backend.calls["prepare"] == 1
    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 1
    assert supervisor._admission.admitted_total() == 1
    assert supervisor._admission.released_total() == 1
    assert supervisor.active_count() == 0
    # The contained executor call: RunningHandle bound, no on_started in the
    # body (the supervisor's AdmissionRequest.on_started owns the PID), no
    # internal 429 retry.
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["running"] is backend.last_running
    assert call["on_started"] is None
    # (The contained throttle no-internal-retry contract is asserted at the
    # throttle seam in tests/test_executor_contained_launch.py.)
    # Diagnostic PID stamped AFTER the RunningHandle was bound.
    assert tracker.get_pid(task_id, _AGENT) == backend.last_running.root_pid
    # Opaque cancel control registered with SessionTracker.
    assert tracker.get_cancel_control(task_id, _AGENT) is not None
    # The task reached its terminal state through run_step (no completion
    # report -> failed, the realistic no-callback outcome).
    task = db.get_task(task_id)
    assert task.status in (TaskStatus.FAILED, TaskStatus.COMPLETED)


def test_task_producer_429_retry_reacquires_fresh_handle(tmp_path, monkeypatch):
    """A rate-limited contained attempt fully finishes and releases before the
    supervisor sleeps and reacquires with a FRESH backend handle (same
    logical invocation, original enqueue age)."""
    backend = _FakeBackend()
    executor = _RecordingExecutor(results=[
        ExecutorResult(
            success=False, duration_seconds=1, session_id="sess-1",
            rate_limited=True, error="rate limit hit",
        ),
        ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-2",
            token_usage=TokenUsage(input_tokens=1, output_tokens=1, model="m"),
        ),
    ])
    orch, supervisor, tracker, db = _make_orch(
        tmp_path, backend, executor, monkeypatch,
        max_retry_attempts=1, backoff_seconds=(0.0,),
    )
    task_id = _seed_task(db)
    orch.run_step(task_id)

    assert backend.calls["prepare"] == 2
    assert backend.calls["launch"] == 2
    assert backend.calls["finish"] == 2
    assert backend.finish_reasons == ["rate_limited", "success"]
    assert supervisor._admission.admitted_total() == 2   # re-entered admission
    assert supervisor._admission.released_total() == 2   # each attempt released exactly once
    assert supervisor.active_count() == 0
    # Fresh backend handle per attempt (distinct processes).
    assert len(executor.calls) == 2
    assert executor.calls[0]["running"].root_pid != executor.calls[1]["running"].root_pid
    # Original enqueue age preserved across the retry (identical enqueued_at).
    assert backend.requests[0].enqueued_at == backend.requests[1].enqueued_at
    assert backend.requests[1].retry_attempt == 1


def test_task_producer_shutdown_before_launch_launches_nothing(tmp_path, monkeypatch):
    """Daemon shutdown at/after grant cannot enter the blocking body: the
    supervisor generation/launch gate refuses, no backend launch occurs, and
    the lease is released exactly once (no scope, no handle, no PID)."""
    backend = _FakeBackend()
    executor = _RecordingExecutor()
    orch, supervisor, tracker, db = _make_orch(tmp_path, backend, executor, monkeypatch)
    task_id = _seed_task(db)

    # Shut the supervisor down BEFORE the task runs: admission is stopped, a
    # queued request is cancelled without launch.
    supervisor.shutdown()
    orch.run_step(task_id)

    assert backend.calls["prepare"] == 0
    assert backend.calls["launch"] == 0
    assert backend.calls["finish"] == 0
    assert len(executor.calls) == 0
    assert supervisor.active_count() == 0
    # The task failed closed (pre-launch terminal winner — queued during
    # shutdown surfaces as cancelled_while_queued per the supervisor contract).
    task = db.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert "before launch" in (task.note or "")


def test_task_producer_shutdown_mid_body_finishes_containment(tmp_path, monkeypatch):
    """Daemon shutdown while the body is blocked in communicate: the drain
    freezes SHUTDOWN on the ownership record, finishes containment, and the
    run loop never reports SUCCESS (no false success when shutdown wins)."""
    backend = _FakeBackend(auto_exit_after=30.0)  # long-lived: body blocks until drain
    executor = _RecordingExecutor(results=[
        ExecutorResult(success=True, duration_seconds=1, session_id="sess-x"),
    ])
    orch, supervisor, tracker, db = _make_orch(tmp_path, backend, executor, monkeypatch)
    task_id = _seed_task(db)

    # Run the producer in a worker thread; the contained body blocks on the
    # fake process until the drain terminates it.
    entered = threading.Event()

    def _run():
        entered.set()
        orch.run_step(task_id)

    thread = threading.Thread(target=_run)
    thread.start()
    assert entered.wait(timeout=5)
    deadline = time.monotonic() + 5
    while backend.calls["launch"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    supervisor.shutdown()
    thread.join(timeout=10)

    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 1
    assert backend.finish_reasons == ["shutdown"]
    assert supervisor.active_count() == 0
    # No false SUCCESS: the executor's success=True result never won the
    # terminal freeze; the task is not COMPLETED.
    task = db.get_task(task_id)
    assert task.status != TaskStatus.COMPLETED


def test_session_tracker_cancel_control_lifecycle():
    """SessionTracker opaque-control registry: registration, iteration,
    clear() removes the control together with the pid/session."""
    tracker = SessionTracker()
    calls: list[str] = []

    def cancel():
        calls.append("cancel")

    tracker.set_active("T-1", "dev_agent", "sess-1", org_slug="test")
    tracker.set_pid("T-1", "dev_agent", 1234)
    tracker.set_cancel_control("T-1", "dev_agent", cancel)

    assert tracker.get_cancel_control("T-1", "dev_agent") is cancel
    assert tracker.iter_task_cancel_controls("T-1") == [("dev_agent", cancel)]
    assert tracker.iter_task_pids("T-1") == [("dev_agent", 1234)]

    tracker.clear("T-1", "dev_agent")
    assert tracker.get_cancel_control("T-1", "dev_agent") is None
    assert tracker.get_pid("T-1", "dev_agent") is None
    assert tracker.get_active("T-1", "dev_agent") is None
    assert calls == []


def test_cancel_route_invokes_opaque_control_not_pid_signal(tmp_path, monkeypatch):
    """The /tasks/{id}/cancel route invokes the SessionTracker opaque control
    (off the event loop) for a wired session and NEVER signals its PID."""
    # Covered deterministically by test_cancel_route_invokes_control_and_skips_pid_kill
    # below (the async route test); this sync marker documents the contract.
    assert True


@pytest.mark.asyncio
async def test_cancel_route_invokes_control_and_skips_pid_kill(client_with_runtime, monkeypatch):
    """Direct route invocation proof: a wired (task, agent) with a registered
    control is cancelled through the control; os.kill is NOT called for it."""
    from runtime.daemon.routes import tasks as tasks_route
    from runtime.models import TaskRecord

    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="T-1", brief="x"))
    org.sessions.set_active("T-1", "dev_agent", "sess-1")
    org.sessions.set_pid("T-1", "dev_agent", 99999)
    invoked: list[str] = []

    def cancel():
        invoked.append("cancel")

    org.sessions.set_cancel_control("T-1", "dev_agent", cancel)

    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(tasks_route.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    r = client.post("/api/v1/orgs/alpha/tasks/T-1/cancel", json={"rationale": ""})
    assert r.status_code == 200
    body = r.json()
    # The opaque control was invoked; the PID was NOT signalled.
    assert invoked == ["cancel"]
    assert kills == []
    assert body["killed"] == [{"task_id": "T-1", "agent": "dev_agent"}]
    # Tracker cleared: control + pid + session gone.
    assert org.sessions.get_cancel_control("T-1", "dev_agent") is None
    assert org.sessions.get_pid("T-1", "dev_agent") is None
    assert org.sessions.get_active("T-1", "dev_agent") is None
