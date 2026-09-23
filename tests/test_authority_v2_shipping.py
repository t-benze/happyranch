"""THR-229 R3 — real owned-runtime shipping fixture (C2 admission + consumer).

This is the accepted R3 non-integration shipping proof.  It bootstraps a fully
OWNED ``RuntimeDir`` / isolated org containing exactly ``engineering_manager``
and ``dev_agent``, starts the REAL daemon app + uvicorn lifespan (fixture token
and loopback port only), saves+activates the immutable v2 dual-text pair through
the REAL isolated HTTP API, creates a real root task, enqueues it through
``runtime.daemon.runner.enqueue_task`` and lets the REAL ``TaskQueue`` worker
drive ``Dispatcher.run_step`` -> ``Orchestrator.run_step`` ->
``run_step_impl`` -> the actual atomic claim -> ``_run_agent``.

The ONLY task-launch double is this fixture's ``_launch_agent_with_scratch``
at the external provider-process boundary.  It invokes the supplied
pre-launch integrity/recovery validators, captures the actual
task/session/agent/full_prompt, and holds the launch until the test has:

  * asserted the real ``current_session_id``, SessionTracker, immutable v2
    session binding and both rendered v2 texts agree;
  * sent the ACTUAL shipping CLI callback as a subprocess
    ``<sys.executable> -m cli.main report-completion --org isolated-org
    --from-file <absolute-payload>`` over the real loopback HTTP route; and
  * proved exactly one durable result / attempt / admitted audit.

The launch is then released with a real ``ExecutorResult``; ``_run_agent``
reads the durable result and the real run-step consumer handles it.  The
current authority hook still fail-closes an unsupported v2 assessment, so the
documented outcome is ESCALATE with NO continuation.

Replay negatives (exact retry read-only, changed decision refusal) run through
that same real CLI/HTTP path.  A separate disposable fixture covers the
missing admitted-audit refusal so the positive path reaches the consumer
uncorrupted.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn

from runtime.daemon.app import create_app
from runtime.models import (
    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
    BlockKind,
    TaskStatus,
)

ORG = "isolated-org"
TEAM = "engineering"
MANAGER = "engineering_manager"
WORKER = "dev_agent"
CHECKOUT = Path(__file__).resolve().parents[1]

WHAT_TO = "Escalate external-contract and 产品 scope change."
WHAT_NOT = "Continue 実装, debugging and review corrections within scope."

_LAUNCH_HOLD_SECONDS = 90.0
_JOIN_SECONDS = 60.0

# R3 permits holding ONLY these unrelated periodic service entry points.  The
# actual authority/recovery/run-step seams are never disabled.
_HELD_LOOPS = (
    ("runtime.daemon.thread_queue", "thread_worker_loop"),
    ("runtime.daemon.thread_breaker_scheduler", "thread_breaker_scheduler_loop"),
    ("runtime.daemon.dream_queue", "dream_worker_loop"),
    ("runtime.daemon.dream_scheduler", "dream_scheduler_loop"),
    ("runtime.daemon.wake_queue", "wake_worker_loop"),
    ("runtime.daemon.work_hours_scheduler", "work_hours_scheduler_loop"),
    ("runtime.daemon.schedule_queue", "schedule_worker_loop"),
    ("runtime.daemon.schedule_scheduler", "schedule_scheduler_loop"),
    ("runtime.daemon.zombie_reaper", "zombie_reaper_loop"),
    ("runtime.daemon.exchange_reaper", "exchange_reaper_loop"),
    ("runtime.daemon.direct_connect_projection_sweep", "direct_connect_projection_sweep_loop"),
    ("runtime.daemon.workspace_cleanup_scheduler", "workspace_cleanup_scheduler_loop"),
)


# --------------------------------------------------------------------------
# Owned runtime bootstrap
# --------------------------------------------------------------------------


def _fixture_agents():
    from runtime.orchestrator.agent_def import AgentDef

    stamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    manager = AgentDef(
        name=MANAGER, team=TEAM, role="manager", executor="codex",
        allow_rules=(), repos={}, enrolled_by="founder", enrolled_at_task=None,
        enrolled_at=stamp, system_prompt="You perform isolated fixture work.\n",
        description="Isolated fixture",
    )
    worker = AgentDef(
        name=WORKER, team=TEAM, role="worker", executor="codex",
        allow_rules=(), repos={}, enrolled_by="founder", enrolled_at_task=None,
        enrolled_at=stamp, system_prompt="You perform isolated fixture work.\n",
        description="Isolated fixture",
    )
    return manager, worker


def _bootstrap_runtime(tmp_path: Path, slugs: tuple[str, ...] = (ORG,)):
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import render_agent_text
    from runtime.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "runtime")
    org_roots: dict[str, Path] = {}
    manager, worker = _fixture_agents()
    for slug in slugs:
        org_root = rt.orgs_dir / slug
        (org_root / "org" / "agents").mkdir(parents=True)
        for name in ("workspaces", "kb", "threads", "artifacts"):
            (org_root / name).mkdir(parents=True, exist_ok=True)
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_manager\n"
            "    workers: [dev_agent]\n"
        )
        (org_root / "org" / "config.yaml").write_text("{}\n")
        paths = OrgPaths(root=org_root)
        (paths.agents_dir / f"{MANAGER}.md").write_text(render_agent_text(manager))
        (paths.agents_dir / f"{WORKER}.md").write_text(render_agent_text(worker))
        org_roots[slug] = org_root
    return rt, org_roots, (manager, worker)


def _held_loop(stop_event: threading.Event):
    async def _held(*_args, **_kwargs):
        while not stop_event.is_set():
            await asyncio.sleep(0.05)
    return _held


class _CaptureCompletion:
    """Pure ASGI wrapper recording raw request + response bytes for completions."""

    def __init__(self, app) -> None:
        self.app = app
        self.records: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).endswith("/completion"):
            await self.app(scope, receive, send)
            return
        record: dict = {"body": b"", "response_body": b"", "status": None}
        self.records.append(record)

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.request":
                record["body"] += message.get("body", b"")
            return message

        async def observed_send(message):
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
            elif message["type"] == "http.response.body":
                record["response_body"] += message.get("body", b"")
            await send(message)

        await self.app(scope, observed_receive, observed_send)


class _OwnedServer:
    """Real uvicorn server with the real app lifespan on a retained socket."""

    def __init__(self, app, sock: socket.socket) -> None:
        self._sock = sock
        self._server = uvicorn.Server(
            uvicorn.Config(app, log_level="warning", lifespan="on"),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run, name="task8440-uvicorn", daemon=True,
        )

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))
        finally:
            # Quiesce every fixture-owned background task on THIS loop before
            # the loop is destroyed.  The real lifespan (runtime/daemon/app.py)
            # requests cancellation of its periodic loops but never awaits
            # them; cancelling-and-awaiting here is lifecycle cleanup, not
            # warning suppression and not an assertion relaxation.
            try:
                self._loop.run_until_complete(self._drain_owned_tasks())
            finally:
                self._loop.close()

    async def _drain_owned_tasks(self) -> None:
        """Cancel and await every pending task owned by this fixture loop."""
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def start(self, *, timeout: float = 60.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started and time.monotonic() < deadline:
            if not self._thread.is_alive():
                raise AssertionError("uvicorn thread died before becoming ready")
            time.sleep(0.02)
        if not self._server.started:
            raise AssertionError("uvicorn did not start within the readiness bound")

    def submit(self, coro):
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self, *, timeout: float = 60.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise AssertionError("uvicorn lifespan did not complete within the bound")


def _sanitized_env(home: Path) -> dict[str, str]:
    """Fixture-only subprocess environment; no inherited runtime selectors."""
    keep = {
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "TZ",
    }
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update({
        "HOME": str(home),
        "HAPPYRANCH_DAEMON_HOME": str(home),
        "PYTHONPATH": str(CHECKOUT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    })
    return env


class _ShippingFixture:
    """One fully owned shipping venue: owned runtime, server, queue, launch hold.

    Reusable by later continuation cases: subclasses/callers may add a
    REQUEST_CHANGES child or a historically migrated disposable DB before
    ``create_and_enqueue_root`` and swap ``_completion_body``, while this unit
    deliberately implements only the fail-closed consumer outcome (no
    fabricated Pending/enqueue continuation).
    """

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        *, seed_historical: bool = False, queue_workers: int = 1,
        dequeue_gate: bool = False, orgs: tuple[str, ...] = (ORG,),
    ) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.seed_historical = seed_historical
        self.queue_workers = queue_workers
        # TASK-8718 Part D: ONE real DaemonState may load MORE THAN ONE real
        # OrgState/Database.  Default stays exactly the single accepted org.
        self.org_slugs = tuple(orgs)
        # TASK-8698 restart venue only: install a polling dequeue gate BEFORE
        # the real workers start, so a committed tagged item can be held back
        # from dequeue until the simulated crash.  Default off: every existing
        # case keeps the unchanged real asyncio.Queue.get worker path.
        self.dequeue_gate = dequeue_gate
        self.home = tmp_path / "daemon-home"
        self.home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(self.home))
        self._stack = ExitStack()
        self.stop_event = threading.Event()
        self.release_event = threading.Event()
        self.launch_event = threading.Event()
        self.captured: dict = {}
        # THR-229 C3d4a Part A: sequential multi-launch venue.  `captured`
        # stays the LATEST launch for the older single-launch drivers, while
        # `launch_history` records every held launch so a later lifecycle case
        # can wait for a SPECIFIC task's launch (delegated child then parent
        # wake) without an elapsed-sleep inference or a shared single slot.
        self.launch_history: list[dict] = []
        self.launch_cv = threading.Condition()
        self.state = None
        self.org = None
        self.orgs: dict = {}
        self.rt = None
        self.org_root = None
        self.org_roots: dict[str, Path] = {}
        self.fixture_agents = ()
        self.sock: socket.socket | None = None
        self.server: _OwnedServer | None = None
        self.capture = None
        self.cli_env: dict[str, str] = {}
        self.port = 0
        self._results: list[dict] = []
        # Per-session release let a later case resume ONLY the reserved
        # invocation while the earlier causal launch stays held.
        self.released_sessions: set[str] = set()
        # Deterministic barrier: set when the TAGGED (reserved continuation)
        # run_step returns and when its queue item reaches task_done, so a
        # reopen never races that worker's transactions or periodic heartbeat.
        self.tagged_run_step_returned = threading.Event()
        self.reserved_invocation_done = threading.Event()
        # THR-229 C3d4b restart venue: per-owner run_step completion barrier and
        # a test-only lifecycle barrier that can refuse DEQUEUE of a committed
        # tagged generation before a simulated crash (never a writer replacement).
        self.run_step_returns: list[tuple] = []
        self.run_step_cv = threading.Condition()
        self.tagged_dequeue_blocked = False
        self.owner_generation = 0
        self._settings = None

    # -- setup ---------------------------------------------------------
    def start(self, *, defer_http: bool = False) -> "_ShippingFixture":
        from runtime.config import Settings
        from runtime.daemon import paths as paths_mod
        from runtime.orchestrator._paths import OrgPaths

        self.rt, self.org_roots, self.fixture_agents = _bootstrap_runtime(
            self.tmp_path, self.org_slugs,
        )
        self.org_root = self.org_roots[self.org_slugs[0]]
        # Everything the fixture owns must resolve inside tmp_path.
        assert self.rt.root.resolve().is_relative_to(self.tmp_path.resolve())
        for root in self.org_roots.values():
            assert root.resolve().is_relative_to(self.tmp_path.resolve())

        # Optional historical venue: reconstruct the FULL old schema and let
        # the actual current Database open/migration path converge it.  This
        # reuses the same checked-in historical fixture as the targeted
        # schema-integrity tests so later continuation cases share one venue.
        if self.seed_historical:
            from tests.authority_v2_historical_schema import (
                reconstruct_historical_database,
            )

            for root in self.org_roots.values():
                reconstruct_historical_database(OrgPaths(root=root).db_path)

        paths_mod.ensure_daemon_home()
        token = paths_mod.ensure_token()
        assert token
        self.cli_env = _sanitized_env(self.home)

        self._settings = Settings(
            project_root=CHECKOUT, queue_workers=self.queue_workers,
        )
        self._open_owner()
        if not defer_http:
            self._start_http()
        return self

    def _open_owner(self) -> "_ShippingFixture":
        """Open ONE real owning process over the persisted runtime dir.

        Reused by :meth:`start` and by the genuine restart venue: a fresh call
        after :meth:`_crash_detach` builds a NEW ``DaemonState``/``OrgState`` and
        a NEW real ``TaskQueue`` over the SAME persisted file (never a boot
        string reassigned on a live object).
        """
        from runtime.daemon.state import DaemonState
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

        import runtime.daemon.app as app_mod

        self.owner_generation += 1
        self.stop_event = threading.Event()
        self.release_event = threading.Event()
        self.launch_event = threading.Event()
        self.captured = {}
        self.launch_history = []
        self.launch_cv = threading.Condition()
        self.released_sessions = set()
        self.run_step_returns = []
        self.run_step_cv = threading.Condition()
        self.tagged_dequeue_blocked = False
        self.tagged_run_step_returned = threading.Event()
        self.reserved_invocation_done = threading.Event()

        self.monkeypatch.setattr(app_mod, "settings", self._settings)

        self.state = DaemonState.from_runtime(self.rt, self._settings)
        assert self.state.broken_orgs == {}, self.state.broken_orgs
        assert set(self.state.orgs.keys()) == set(self.org_slugs)
        self.orgs = self.state.orgs
        self.org = self.orgs[self.org_slugs[0]]

        # Fixture-owned workspace bootstrap through the supported Codex adapter
        # for EVERY loaded org (single-org default unchanged).
        for slug in self.org_slugs:
            org = self.orgs[slug]
            org_paths = OrgPaths(root=org.root)
            adapter = CodexWorkspaceAdapter(self._settings, org_paths, slug=slug)
            for agent in self.fixture_agents:
                workspace = org_paths.workspaces_dir / agent.name
                assert workspace.resolve().is_relative_to(self.tmp_path.resolve())
                adapter.ensure_workspace_ready(
                    workspace, agent.name, agent.system_prompt,
                )
                marker = org.orchestrator._readiness_marker(workspace, "codex")
                assert marker == workspace / "AGENTS.md"
                assert marker.resolve().is_relative_to(self.tmp_path.resolve())
                assert marker.is_file()
                assert agent.system_prompt.strip() in marker.read_text()

        # Hold only the unrelated periodic service entry points.
        for module_name, attr in _HELD_LOOPS:
            module = importlib.import_module(module_name)
            self.monkeypatch.setattr(module, attr, _held_loop(self.stop_event))

        # Normal isolated fixture wiring: wrap the real Dispatcher so the test
        # can await the TAGGED reserved run_step's deterministic return instead
        # of racing an active worker against reopen/close.
        self._instrument_reserved_completion()
        # Install the test-only dequeue gate BEFORE the real workers start.
        if self.dequeue_gate:
            self._install_tagged_dequeue_barrier()
        return self

    def _start_http(self) -> "_ShippingFixture":
        """Start the real app/lifespan (and therefore the real queue workers)."""
        from runtime.daemon import paths as paths_mod

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = int(self.sock.getsockname()[1])
        assert self.port != 8765
        (self.home / "daemon.port").write_text(str(self.port))

        self.capture = _CaptureCompletion(create_app(self.state))
        self.server = _OwnedServer(self.capture, self.sock)
        self.server.start()

        # Bounded readiness: the real lifespan must have wired the workers.
        token = paths_mod.read_token()
        token_header = {"Authorization": f"Bearer {token}"}
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{self.port}/api/v1/health",
                    headers=token_header, timeout=2.0,
                )
            except httpx.HTTPError:
                time.sleep(0.05)
                continue
            if response.status_code == 200 and self.state.queue.is_running():
                break
            time.sleep(0.05)
        else:
            raise AssertionError("fixture daemon did not become ready")
        return self

    def _crash_detach(self) -> None:
        """Simulate a daemon crash: drop the in-memory queue/workers and the owner.

        The detached owner's ``TaskQueue`` is dereferenced WITHOUT a drain, so
        any queued item that was never dequeued is genuinely lost.  The old
        ``Database``/``OrgState`` are closed and never reused.  No boot string is
        reassigned on a live object.
        """
        state = self.state
        server = self.server
        sock = self.sock
        if server is not None:
            server.stop()
        elif state is not None:
            async def _shutdown() -> None:
                await state.queue.stop()
                await state.close_all()

            asyncio.run(_shutdown())
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.server = None
        self.sock = None
        self.state = None
        self.org = None
        self.capture = None

    def _install_tagged_dequeue_barrier(self) -> None:
        """Test-only lifecycle barrier that refuses DEQUEUE of a tagged item.

        Installed BEFORE the real workers start, so every worker's ``get``
        observes the live flag.  While ``tagged_dequeue_blocked`` is set the
        worker sees the tagged publication at the head but does NOT remove it,
        so a committed generation can be deliberately lost with the owner at
        the simulated crash.  A polling ``get_nowait`` is used (never a parked
        ``Queue.get``) so flipping the flag after workers are parked is observed
        deterministically.  This never replaces a publication/admission/consumer
        writer and is removed by the crash itself.
        """
        queue = self.state.queue._queue
        fixture = self

        async def _gated_get():
            while True:
                if fixture.tagged_dequeue_blocked:
                    try:
                        head = queue._queue[0]
                    except IndexError:
                        head = None
                    if (
                        head is not None
                        and isinstance(head[2], dict)
                        and head[2].get("authority_v2_generation")
                    ):
                        await asyncio.sleep(0.01)
                        continue
                try:
                    return queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.005)

        queue.get = _gated_get  # type: ignore[assignment]

    def await_run_step_returns(
        self, task_id: str, count: int, *, timeout: float = 60.0,
        slug: str | None = None,
    ) -> None:
        """Deterministic barrier on completed real ``run_step`` calls.

        ``slug`` optionally scopes the count to ONE owned org (Part D uses the
        SAME textual task id in two orgs, so the un-scoped count would conflate
        them).
        """
        deadline = time.monotonic() + timeout

        def _matches(r) -> bool:
            return r[1] == task_id and (slug is None or r[0] == slug)

        with self.run_step_cv:
            while sum(1 for r in self.run_step_returns if _matches(r)) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"run_step for {slug or '*'} / {task_id} never returned "
                        f"{count} times"
                    )
                self.run_step_cv.wait(timeout=min(remaining, 0.5))

    # -- isolated API control pair ------------------------------------
    def _api(self, method: str, path: str, *, slug: str | None = None, **kwargs) -> httpx.Response:
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        return httpx.request(
            method,
            f"http://127.0.0.1:{self.port}/api/v1/orgs/{slug or self.org_slugs[0]}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
            **kwargs,
        )

    def activate_v2_pair(self, *, slug: str | None = None) -> dict:
        base = f"/agents/{MANAGER}/team-escalation-policy/v2/releases"
        body = {
            "team": TEAM, "policy_id": "engineering-dual-text",
            "title": "Dual text", "create_request_id": "ship-create-1",
            "activation_request_id": "ship-activate-1",
            "based_on_selector_id": None, "expected_selector_id": None,
            "action": "bootstrap",
            "what_to_escalate": WHAT_TO, "what_not_to_escalate": WHAT_NOT,
            "acknowledge_shared_credential_attribution": True,
        }
        response = self._api("POST", base, slug=slug, json=body)
        assert response.status_code == 201, (response.status_code, response.text)
        return response.json()

    # -- real task + enqueue ------------------------------------------
    def create_and_enqueue_root(self, *, org=None) -> str:
        from runtime.daemon.runner import enqueue_task

        target = org if org is not None else self.org
        root_id = target.orchestrator.create_task("isolated shipping brief", team=TEAM)
        task = target.db.get_task(root_id)
        assert task.status is TaskStatus.PENDING
        assert task.current_session_id is None
        enqueue_task(self.state, target.slug, root_id)
        return root_id

    # -- held launch ---------------------------------------------------
    def install_launch_hold(self, *, provider_session_id: str | None = None) -> None:
        """Hold only the external provider launch boundary.

        ``provider_session_id`` opts INTO the genuine completion-recovery venue:
        the doubled external boundary returns a CLEAN provider result whose
        ``agent_session_id`` is a real separate provider conversation identity
        (the origin turn ends without a HappyRanch callback), which is exactly
        the premise ``run_step``'s real completion-recovery claim requires.  It
        is deliberately opt-in so every ordinary single-launch case keeps its
        prior no-recovery behavior.
        """
        from runtime.orchestrator.executors import ExecutorResult

        fixture = self

        def _make_held(slug: str):
            def _held_launch(**kwargs):
                kwargs["pre_launch_integrity_validator"]()
                kwargs["recovery_launch_validator"]()
                record = dict(kwargs)
                # Part D: record which OWNED ORG launched so two orgs sharing the
                # same textual task id stay distinguishable without inspecting
                # private queue contents.
                record["_fixture_org"] = slug
                fixture.captured = record
                with fixture.launch_cv:
                    fixture.launch_history.append(dict(record))
                    fixture.launch_cv.notify_all()
                fixture.launch_event.set()
                session = kwargs["session_id"]
                deadline = time.monotonic() + _LAUNCH_HOLD_SECONDS
                while time.monotonic() < deadline:
                    if (
                        fixture.release_event.is_set()
                        or session in fixture.released_sessions
                    ):
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError("held launch was never released")
                return ExecutorResult(
                    success=True, duration_seconds=1, session_id=session,
                    agent_session_id=provider_session_id,
                )
            return _held_launch

        targets = self.orgs.values() if self.orgs else (self.org,)
        for org in targets:
            self.monkeypatch.setattr(
                org.orchestrator, "_launch_agent_with_scratch",
                _make_held(org.slug),
            )

    def wait_for_launch_for(
        self, task_id: str, *, after: int = 0, timeout: float = _LAUNCH_HOLD_SECONDS,
        org: str | None = None,
    ) -> dict:
        """Wait (condition-barrier, no elapsed-sleep proof) for a launch of
        ``task_id`` recorded after index ``after``.  Returns the launch kwargs.

        ``org`` optionally scopes to one owned org slug so the same textual task
        id in two loaded orgs stays distinguishable (Part D)."""
        deadline = time.monotonic() + timeout
        with self.launch_cv:
            while True:
                for index in range(after, len(self.launch_history)):
                    entry = self.launch_history[index]
                    if entry.get("task_id") == task_id and (
                        org is None or entry.get("_fixture_org") == org
                    ):
                        return entry
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"no held launch recorded for {task_id} after {after}"
                    )
                self.launch_cv.wait(timeout=min(remaining, 0.5))

    def await_delegated_launch(
        self, task_id: str, *, timeout: float = _LAUNCH_HOLD_SECONDS,
    ) -> dict:
        """Deterministic quiescence barrier for a REAL delegated child launch.

        A delegated child enqueued through the real queue reaches
        ``_launch_agent_with_scratch`` only AFTER its durable ``session_start``
        audit and initial heartbeat commit, then parks in the held launch (no
        further writes until released). Awaiting that recorded launch makes a
        later full-DB byte-identity replay comparison immune to the child's
        asynchronous startup writes, using the launch condition barrier (never
        elapsed sleep or a child-row count).
        """
        return self.wait_for_launch_for(task_id, timeout=timeout)

    def launch_count(self) -> int:
        with self.launch_cv:
            return len(self.launch_history)

    def wait_for_launch(self, *, timeout: float = _LAUNCH_HOLD_SECONDS) -> dict:
        assert self.launch_event.wait(timeout=timeout), "agent launch was never reached"
        return self.captured

    def release_launch(self) -> None:
        self.release_event.set()

    def release_session(self, session_id: str) -> None:
        """Resume ONLY one held invocation (later reserved-invocation cases)."""
        self.released_sessions.add(session_id)

    def _instrument_reserved_completion(self) -> None:
        """Deterministic barrier for the TAGGED reserved invocation.

        Two real signals are required: the tagged ``run_step`` returned, and its
        queue item reached ``task_done`` (heartbeat cancelled).  Waiting on both
        means a reopen never races the worker's transactions or its periodic
        ``last_heartbeat`` write, and never uses elapsed sleep as proof.
        """
        from runtime.daemon.dispatcher import Dispatcher

        fixture = self
        original = Dispatcher.run_step

        def _wrapped(dispatcher_self, slug, task_id, metadata=None):
            try:
                return original(dispatcher_self, slug, task_id, metadata)
            finally:
                with fixture.run_step_cv:
                    fixture.run_step_returns.append((slug, task_id, metadata))
                    fixture.run_step_cv.notify_all()
                if (
                    isinstance(metadata, dict)
                    and metadata.get("authority_v2_generation")
                ):
                    fixture.tagged_run_step_returned.set()

        self.monkeypatch.setattr(Dispatcher, "run_step", _wrapped)

        queue = self.state.queue._queue
        original_done = queue.task_done

        def _task_done():
            original_done()
            # Only the tagged reserved item can finish task_done while the
            # causal launch is still held; require the tagged run_step marker so
            # an unrelated completion can never satisfy this barrier early.
            if fixture.tagged_run_step_returned.is_set():
                fixture.reserved_invocation_done.set()

        self.monkeypatch.setattr(queue, "task_done", _task_done)

    def await_reserved_invocation_done(self, *, timeout: float = 60.0) -> None:
        """Deterministic old-invocation quiescence barrier (no elapsed sleep)."""
        assert self.tagged_run_step_returned.wait(timeout=timeout), (
            "tagged reserved invocation never returned"
        )
        assert self.reserved_invocation_done.wait(timeout=timeout), (
            "tagged reserved queue item never finished"
        )

    def join_workers(self, *, timeout: float = _JOIN_SECONDS) -> None:
        if self.server is None:
            return
        future = self.server.submit(self.state.queue._queue.join())
        future.result(timeout=timeout)

    # -- shipping CLI subprocess --------------------------------------
    def write_payload(self, body: dict, name: str = "completion.json") -> Path:
        path = self.tmp_path / name
        path.write_text(json.dumps(body))
        assert path.is_absolute()
        return path

    def run_cli(self, payload: Path, *, org: str | None = None) -> subprocess.CompletedProcess:
        command = [
            sys.executable, "-m", "cli.main", "report-completion",
            "--org", org or self.org_slugs[0], "--from-file", str(payload),
        ]
        result = subprocess.run(
            command, cwd=str(CHECKOUT), env=self.cli_env,
            capture_output=True, text=True, timeout=60,
        )
        self._results.append({
            "command": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        return result

    def last_http(self) -> dict:
        assert self.capture is not None
        assert self.capture.records
        return self.capture.records[-1]

    # -- teardown ------------------------------------------------------
    def stop(self) -> None:
        # 1. Release every held launch, let the run-step/queue drain, then stop
        #    the owned workers BEFORE the DB is closed.
        self.release_event.set()
        self.stop_event.set()
        try:
            if self.state is not None and self.server is not None:
                try:
                    self.join_workers(timeout=_JOIN_SECONDS)
                except Exception:
                    pass
            # 2. Real lifespan completion: queue stop, supervisor drain,
            #    projection cancel, state close.
            if self.server is not None:
                self.server.stop()
        finally:
            # 3. Close the owned listener and remaining owned stores.
            if self.sock is not None:
                self.sock.close()
            if self.state is not None:
                try:
                    self.state.metrics_store.close()
                except Exception:
                    pass
            self._stack.close()
        # 4. No orphan thread / worker / listener may remain.
        if self.server is not None:
            assert not self.server._thread.is_alive()
        if self.state is not None:
            assert self.state.queue.is_running() is False
        if self.sock is not None:
            assert self.sock.fileno() == -1
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", self.port)) != 0
        finally:
            probe.close()


@pytest.fixture
def shipping(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch)
    fixture.start()
    try:
        yield fixture
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _v2_evaluation(binding: dict, task_id: str) -> dict:
    return {
        "activation_epoch": binding["selector_epoch"],
        "activation_id": binding["activation_id"],
        "contract_digest": binding["contract_digest"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "executor_kind": binding["executor_kind"],
        "manager_session_id": binding["session_id"],
        "model_id": binding["model_id"],
        "policy_digest": binding["policy_digest"],
        "policy_version": binding["policy_version"],
        "provider_id": binding["provider_id"],
        "release_id": binding["release_id"],
        "root_task_id": task_id,
        "what_not_to_escalate": {
            "applicability": "applies", "confidence": 90, "uncertainty_codes": [],
        },
        "what_to_escalate": {
            "applicability": "does_not_apply", "confidence": 90,
            "uncertainty_codes": [],
        },
    }


def _completion_body(binding: dict, task_id: str) -> dict:
    return {
        "task_id": task_id,
        "session_id": binding["session_id"],
        "agent": MANAGER,
        "status": "completed",
        "confidence": 90,
        "summary": "isolated shipping escalation",
        "decision": {
            "action": "escalate",
            "reason": "external contract change requires founder decision",
        },
        "manager_self_evaluation": _v2_evaluation(binding, task_id),
    }


def _binding(fixture: _ShippingFixture, task_id: str, session_id: str) -> dict:
    from runtime.orchestrator.active_authority_policy import load_session_policy_binding

    return load_session_policy_binding(
        db=fixture.org.db, task_id=task_id, session_id=session_id,
        agent_name=MANAGER,
    )


def _admission_counts(fixture: _ShippingFixture, task_id: str) -> tuple:
    results = fixture.org.db.get_task_results(task_id)
    attempt = None
    if results:
        attempt = fixture.org.db.get_authority_policy_v2_attempt_for_result(results[-1]["id"])
    audits = fixture.org.db.list_authority_policy_v2_result_stage_audits(
        root_task_id=task_id, manager_agent=MANAGER,
    )
    return results, attempt, audits


def _provenance(fixture: _ShippingFixture) -> dict:
    script = (
        "import json, sys, cli.main, runtime;"
        "print(json.dumps({'executable': sys.executable,"
        " 'cli_main': cli.main.__file__, 'runtime': runtime.__file__,"
        " 'version': sys.version}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(CHECKOUT),
        env=fixture.cli_env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# Item 1-3: real launch -> subprocess CLI -> durable admission -> consumer
# --------------------------------------------------------------------------


def _run_positive_core(fixture: _ShippingFixture) -> dict:
    """The accepted real launch -> CLI -> admission -> AUTOMATIC continuation.

    Shared by the fresh and the historically migrated venues.  The v2 authority
    hook is NOT bypassed: the real run-step consumer runs it, the accepted
    pre-final/final stages commit, the exact receipt settles, the authenticated
    publisher publishes ONE tagged generation, the real TaskQueue/Dispatcher
    admits it exactly once (held at the external launch boundary), and a real
    next-result CLI callback spends the envelope and applies the reserved
    decision once.  The provider launch remains the sole external double."""
    receipt = fixture.activate_v2_pair()
    assert receipt.get("family") == "v2" or receipt.get("activation_id")
    # Production owner bindings: the trusted daemon-process boot identity and the
    # server-owned permission-surface reader.  The real daemon entry point binds
    # these once per live OrgState; the isolated fixture binds them explicitly.
    fixture.org.bind_authority_v2_owner()

    # Install the sole launch hold BEFORE enqueue so the real queue cannot win
    # the race and launch the provider process before the double is installed.
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()

    session_id = captured["session_id"]
    assert captured["task_id"] == root_id
    assert captured["agent_name"] == MANAGER

    # The atomic claim really happened before `_run_agent`: run_step_impl
    # incremented the persisted step count and left the task live/unblocked.
    claimed = fixture.org.db.get_task(root_id)
    assert claimed.status is TaskStatus.IN_PROGRESS
    assert claimed.orchestration_step_count == 1
    assert claimed.block_kind is None

    # Real persisted binding + tracker must agree with the captured launch.
    task = fixture.org.db.get_task(root_id)
    assert task.current_session_id == session_id
    assert fixture.org.sessions.get_active(root_id, MANAGER) == session_id
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"
    assert binding["release_id"]
    assert binding["provider_id"] == "codex"
    # Both rendered texts are present in the actual full prompt.
    assert WHAT_TO in captured["full_prompt"]
    assert WHAT_NOT in captured["full_prompt"]

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    # Durable admission: exactly one sanitized evaluation/result/attempt/audit.
    received = json.loads(fixture.last_http()["body"])
    assert received["manager_self_evaluation"] == body["manager_self_evaluation"]
    results, attempt, audits = _admission_counts(fixture, root_id)
    assert len(results) == 1
    assert attempt is not None
    assert attempt.stage == "admitted"
    assert attempt.finalization_state == "unfinalized"
    assert attempt.release_id == binding["release_id"]
    assert [a["payload"]["stage"] for a in audits] == ["admitted"]
    assert audits[0]["payload"]["result_id"] == results[0]["id"]
    row = fixture.org.db.get_latest_task_result(root_id, MANAGER, session_id)
    assert row is not None
    assert fixture.org.db.get_authority_policy_v2_attempt_for_result(row["id"]) is not None
    # The ordinary (non-recovery) path has no applicable callback receipt.
    assert fixture.org.db.get_accepted_task_completion_recovery_result(
        task_id=root_id, agent=MANAGER,
    ) is None

    # Exact retry is read-only before release too (tracker already cleared).
    before = (len(results), results[0]["id"], attempt.owner_attempt_id,
              len(audits), audits[0]["id"])
    retry = fixture.run_cli(payload)
    assert retry.returncode == 0, retry.stderr
    assert fixture.last_http()["status"] == 200
    after_results, after_attempt, after_audits = _admission_counts(fixture, root_id)
    assert (len(after_results), after_results[0]["id"], after_attempt.owner_attempt_id,
            len(after_audits), after_audits[0]["id"]) == before

    # Changed decision refuses with no allocation.
    changed = dict(body)
    changed["decision"] = {"action": "escalate", "reason": "changed decision text"}
    changed_payload = fixture.write_payload(changed, "changed.json")
    refused = fixture.run_cli(changed_payload)
    assert refused.returncode != 0
    assert fixture.last_http()["status"] == 409
    detail = json.loads(fixture.last_http()["response_body"])["detail"]
    assert detail["code"] == "v2_attempt_payload_mismatch", detail
    after_results, after_attempt, after_audits = _admission_counts(fixture, root_id)
    assert (len(after_results), after_results[0]["id"], after_attempt.owner_attempt_id,
            len(after_audits), after_audits[0]["id"]) == before

    # ---- THR-229 C3d5a: the REAL automatic v2 continuation ----
    # Resume ONLY the causal invocation.  Its real run-step consumer reaches the
    # authority hook, which selects the v2 family from the AUTHENTICATED launch
    # binding and runs the accepted pre-final stages + final continuation +
    # settlement + publication.  The reserved generation is published through
    # the real TaskQueue, admitted exactly once and launches ONE reserved
    # session that stays held at the external launch boundary.  No manual public
    # stage staging, no mocked hook, no fake policy outcome and no synthetic
    # positive receipt is used anywhere in this acceptance path.
    causal_result_id = results[0]["id"]
    enqueue_calls: list[tuple] = []
    real_put_nowait = fixture.state.queue.put_nowait

    def _counting_put_nowait(slug, task_id, *, metadata=None):
        enqueue_calls.append((slug, task_id, metadata))
        return real_put_nowait(slug, task_id, metadata=metadata)

    fixture.state.queue.put_nowait = _counting_put_nowait  # type: ignore[assignment]

    fixture.release_session(session_id)
    reserved_launch = fixture.wait_for_launch_for(root_id, after=1)
    reserved = reserved_launch["session_id"]
    assert reserved != session_id

    db = fixture.org.db

    def _count(table: str) -> int:
        return db._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    # The causal attempt advanced through the complete accepted stage sequence
    # exactly once under the original uninterrupted owner.
    raw = db._conn.execute(
        "SELECT stage, finalization_state FROM authority_policy_v2_attempts "
        "WHERE result_id=?", (causal_result_id,),
    ).fetchone()
    assert raw["stage"] == "consumed_audited", raw["stage"]
    assert raw["finalization_state"] == "continued"
    # One candidate/evaluation/envelope/generation.
    assert _count("authority_policy_v2_candidates") == 1
    assert _count("authority_policy_v2_evaluations") == 1
    assert _count("authority_policy_v2_continue_envelopes") == 1
    assert _count("authority_policy_v2_recovery_notifications") == 1
    envelope = db.get_authority_policy_v2_continue_envelope_for_root(root_id)
    assert envelope is not None and envelope.lifecycle_state == "active"
    notification = db.get_authority_policy_v2_recovery_notification_for_envelope(
        envelope.envelope_id,
    )
    assert notification is not None
    assert notification.state in ("admitted", "settled")
    assert notification.next_session_id == reserved
    # Exactly one admission/step/session: the root is in_progress under the
    # single reserved causal owner/session.
    current = db.get_task(root_id)
    assert current.status is TaskStatus.IN_PROGRESS
    assert current.current_session_id == reserved
    assert current.block_kind is None
    assert current.orchestration_step_count == 2
    # Exactly ONE tagged generation publication on the real queue.
    tagged = [
        call for call in enqueue_calls
        if isinstance(call[2], dict) and call[2].get("authority_v2_generation")
    ]
    assert len(tagged) == 1, enqueue_calls
    assert tagged[0][2]["authority_v2_generation"] == notification.notification_id
    # Exactly one closed ``continued`` result-stage event.
    stages = [
        audit["payload"].get("stage")
        for audit in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("continued") == 1
    # The authority hook really ran on the v2-bound escalation and continued.
    hook_rows = [
        audit for audit in db.get_audit_logs(root_id)
        if audit["action"] == "authority_hook"
    ]
    assert hook_rows, "authority hook outcome was not recorded"
    assert hook_rows[-1]["payload"]["outcome"] == "continued_same_root"
    # The ordinary escalation path never ran.
    assert not [
        audit for audit in db.get_audit_logs(root_id)
        if audit["action"] == "escalation"
    ]

    # ---- Real next-result CLI callback -> existing spend/claim/done/applied ----
    reserved_binding = _binding(fixture, root_id, reserved)
    assert reserved_binding is not None and reserved_binding["mode"] == "v2"
    reserved_payload = fixture.write_payload(
        _reserved_decision_body(reserved_binding, root_id, "done"),
        name="completion-reserved-auto.json",
    )
    reserved_result = fixture.run_cli(reserved_payload)
    assert reserved_result.returncode == 0, reserved_result.stderr
    assert fixture.last_http()["status"] == 200
    r2_rows = db.get_task_results(root_id)
    r2 = r2_rows[-1]["id"]
    assert r2_rows[-1]["session_id"] == reserved
    assert r2 != causal_result_id

    fixture.release_session(reserved)
    fixture.join_workers()

    # The winning continuation spent the envelope and the reserved decision was
    # applied exactly once: the normal done effect committed and no successor,
    # second envelope or second admission was manufactured.
    spent = db.get_authority_policy_v2_continue_envelope(envelope.envelope_id)
    assert spent.lifecycle_state == "consumed"
    assert spent.spending_result_id == r2
    assert spent.decision_state == "applied"
    final = db.get_task(root_id)
    assert final.status is TaskStatus.COMPLETED
    assert db.get_active_authority_continue_envelope(root_id) is None
    assert _count("authority_policy_v2_continue_envelopes") == 1
    assert _count("authority_policy_v2_recovery_notifications") == 1
    return {
        "root_id": root_id, "session_id": session_id, "reserved": reserved,
        "binding": binding, "results": results,
    }


def test_shipping_real_launch_cli_admission_and_automatic_continuation(shipping):
    fixture = shipping
    provenance = _provenance(fixture)
    assert Path(provenance["executable"]).resolve() == Path(sys.executable).resolve()
    assert Path(provenance["cli_main"]).resolve().is_relative_to(CHECKOUT)
    assert Path(provenance["runtime"]).resolve().is_relative_to(CHECKOUT)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(CHECKOUT),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert len(head) == 40 and all(c in "0123456789abcdef" for c in head)

    _run_positive_core(fixture)


def test_shipping_historically_migrated_schema_automatic_continuation(
    tmp_path, monkeypatch,
):
    """The SAME real venue over a FULL historical schema migrated forward.

    Proves the fixture wires into the owned R3 shipping venue and that the
    automatic v2 continuation holds on a migrated DB.
    """
    from runtime.orchestrator.authority import (
        _V2_MIGRATED_TABLE_CREATE_SQL,
        _v2_build_reference_inventories,
        _v2_capture_inventory,
    )

    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        inventory = _v2_capture_inventory(fixture.org.db._conn)
        references = _v2_build_reference_inventories()
        # The venue really ran on the accepted migrated layout, not a fresh one.
        assert inventory["tables"]["threads"]["xinfo"] == (
            references[1]["tables"]["threads"]["xinfo"]
        )
        assert inventory["tables"]["threads"]["xinfo"] != (
            references[0]["tables"]["threads"]["xinfo"]
        )
        assert inventory["tables"]["thread_messages"]["sql"] == (
            _V2_MIGRATED_TABLE_CREATE_SQL["thread_messages"]
        )
        _run_positive_core(fixture)
    finally:
        fixture.stop()


def test_shipping_missing_admitted_audit_refuses_without_repair(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch)
    fixture.start()
    try:
        fixture.activate_v2_pair()
        fixture.install_launch_hold()
        root_id = fixture.create_and_enqueue_root()
        captured = fixture.wait_for_launch()
        session_id = captured["session_id"]
        binding = _binding(fixture, root_id, session_id)
        assert binding is not None and binding["mode"] == "v2"
        payload = fixture.write_payload(_completion_body(binding, root_id))
        first = fixture.run_cli(payload)
        assert first.returncode == 0, first.stderr
        assert fixture.last_http()["status"] == 200

        results, attempt, audits = _admission_counts(fixture, root_id)
        assert len(results) == 1 and len(audits) == 1

        # Corrupt ONLY this disposable fixture's admitted-audit evidence.
        from runtime.infrastructure.database import (
            AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
        )

        fixture.org.db.execute(
            "DELETE FROM audit_log WHERE task_id=? AND agent=? AND action=?",
            (root_id, MANAGER, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION),
        )
        assert fixture.org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        ) == []

        refused = fixture.run_cli(payload)
        assert refused.returncode != 0
        assert fixture.last_http()["status"] == 409
        detail = json.loads(fixture.last_http()["response_body"])["detail"]
        assert detail["code"] == "v2_attempt_missing", detail
        # No repair, no allocation.
        after_results, after_attempt, after_audits = _admission_counts(fixture, root_id)
        assert len(after_results) == 1
        assert after_results[0]["id"] == results[0]["id"]
        assert after_audits == []
        assert fixture.org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        ) == []
    finally:
        fixture.stop()


def test_shipping_fixture_paths_and_markers_are_owned(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch)
    fixture.start()
    try:
        from runtime.orchestrator._paths import OrgPaths

        paths = OrgPaths(root=fixture.org.root)
        assert paths.workspaces_dir.resolve().is_relative_to(tmp_path.resolve())
        for agent in (MANAGER, WORKER):
            workspace = paths.workspaces_dir / agent
            marker = workspace / "AGENTS.md"
            assert marker.is_file()
            assert marker.resolve().is_relative_to(tmp_path.resolve())
            assert "You perform isolated fixture work." in marker.read_text()
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# C3b: the real admitted result drives the callable claim/claim-audit seam
# --------------------------------------------------------------------------


def _drive_c3b_claim(
    fixture: _ShippingFixture, *, retained_eligibility_negative: bool = False,
) -> tuple[str, str]:
    """Real launch -> subprocess CLI admission -> callable K/P claim + a1 audit.

    Shared by the fresh and the historically migrated venues.  The provider
    launch is held only at the external process boundary, so the durable
    admitted result is genuine and the claim seam runs against it.

    ``retained_eligibility_negative`` stops the run at the minimal red case for
    the C3c correction: after a genuine claim+claim-audit, a task becoming
    mechanically ineligible (non-null ``active_chain``) must refuse the next
    callable stage from the REAL persisted evidence, with the exact prior
    residue retained.
    """
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    assert attempt.stage == "admitted"
    row_id = results[0]["id"]
    db = fixture.org.db

    # Bind the narrowly scoped server-side permission reader (the exact
    # orchestration seam the later continuation consumer uses).  It reads the
    # fixture's live org config/agent definition inside the server process; a
    # read failure raises and the claim refuses fail-closed.
    from runtime.orchestrator.authority import _strict_permission_surface_digest

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )

    # Real admitted result -> callable seam: one atomic K/P/J claimed commit.
    claimed = db.claim_authority_policy_v2_candidate(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert claimed.status == "claimed", claimed
    candidate = db.get_authority_policy_v2_candidate_for_result(row_id)
    assert candidate is not None and candidate.candidate_id == claimed.candidate_id
    assert db.get_authority_policy_v2_pin(candidate.candidate_id) is not None
    assert db.list_authority_policy_v2_candidate_audits(candidate.candidate_id) == []
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "claimed"
    _results, after_attempt, after_audits = _admission_counts(fixture, root_id)
    assert [a["payload"]["stage"] for a in after_audits] == ["admitted"]
    assert after_attempt.owner_attempt_id == attempt.owner_attempt_id

    # Exact CLI/HTTP retry after legitimate progression stays read-only success.
    before = (len(_admission_counts(fixture, root_id)[0]),
              len(_admission_counts(fixture, root_id)[2]),
              _admission_counts(fixture, root_id)[1].owner_attempt_id)
    retry = fixture.run_cli(payload)
    assert retry.returncode == 0, retry.stderr
    assert fixture.last_http()["status"] == 200
    after = _admission_counts(fixture, root_id)
    assert (len(after[0]), len(after[2]), after[1].owner_attempt_id) == before

    # Separate second transaction: exactly one a1 + claim_audited evidence.
    audited = db.audit_authority_policy_v2_candidate_claim(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert audited.status == "claim_audited", audited
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "claim_audited"
    assert len(db.list_authority_policy_v2_candidate_audits(candidate.candidate_id)) == 1
    assert [
        a["payload"]["stage"]
        for a in _admission_counts(fixture, root_id)[2]
    ] == ["admitted", "claim_audited"]

    if retained_eligibility_negative:
        # C3c callable-stage evidence: retained mechanical eligibility is
        # re-derived from the REAL persisted task at the next stage boundary.
        # Valid-at-claim is insufficient: a later non-null active_chain refuses
        # with no new V/audit/advancement and the exact prior residue retained.
        db._conn.execute(
            "UPDATE tasks SET active_chain=? WHERE id=?", ("[]", root_id)
        )
        db._conn.commit()
        ineligible = db.evaluate_authority_policy_v2_candidate(
            root_task_id=root_id, manager_agent=MANAGER,
            manager_session_id=session_id, result_id=row_id,
            origin_boot_id=attempt.origin_boot_id,
            owner_attempt_id=attempt.owner_attempt_id,
        )
        assert ineligible.status == "refused", ineligible
        assert ineligible.refusal_code == "claim_failed", ineligible
        assert db.get_authority_policy_v2_candidate(
            candidate.candidate_id
        ).lifecycle_stage == "created"
        assert db.get_authority_policy_v2_evaluation(candidate.candidate_id) is None
        assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "claim_audited"
        assert [
            a["payload"]["stage"]
            for a in _admission_counts(fixture, root_id)[2]
        ] == ["admitted", "claim_audited"]
        # THR-229 C3d5a isolated stage barrier: the causal launch stays HELD, so
        # the real consumer never runs the now-automatic v2 hook inside this
        # retained-eligibility driver.  Its public-boundary assertion is the
        # callable-stage refusal with the exact prior residue.
        assert db.get_active_authority_continue_envelope(root_id) is None
        return root_id, candidate.candidate_id

    # C3c: the four callable pre-final stage methods against the same genuine
    # persisted transport evidence, still while the external launch is held.
    # This proves the stages consume real result/assessment/binding evidence.
    # THR-229 C3d5a: the causal launch is deliberately kept HELD at the end of
    # this driver (an explicit isolated stage barrier) so these staged-writer
    # public-boundary assertions do not depend on the now-automatic shipping
    # hook; the automatic continuation path is proven by the dedicated
    # ``..._automatic_continuation`` acceptance cases.
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    evaluated = db.evaluate_authority_policy_v2_candidate(**stage_kwargs)
    assert evaluated.status == "evaluated", evaluated
    evaluation = db.get_authority_policy_v2_evaluation(candidate.candidate_id)
    assert evaluation is not None
    assert evaluation.evaluation_id == candidate.candidate_id
    assert evaluation.outcome == "continue_applies"
    assert evaluation.diagnostic_code is None
    assert evaluation.assessment_digest == attempt.assessment_digest
    assert db.get_authority_policy_v2_candidate(candidate.candidate_id).lifecycle_stage == "evaluated"
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "evaluated"

    evaluation_audited = db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs)
    assert evaluation_audited.status == "evaluation_audited", evaluation_audited
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "evaluation_audited"

    consumed = db.consume_authority_policy_v2_candidate(**stage_kwargs)
    assert consumed.status == "consumed", consumed
    assert db.get_authority_policy_v2_candidate(candidate.candidate_id).lifecycle_stage == "consumed"
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "consumed"

    consumed_audited = db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs)
    assert consumed_audited.status == "consumed_audited", consumed_audited
    assert db.get_authority_policy_v2_attempt_for_result(row_id).stage == "consumed_audited"
    assert db.get_authority_policy_v2_evaluation(candidate.candidate_id) == evaluation
    assert [
        a["event"] for a in db.list_authority_policy_v2_candidate_audits(
            candidate.candidate_id
        )
    ] == ["claimed", "evaluated", "consumed"]
    assert [
        a["payload"]["stage"]
        for a in _admission_counts(fixture, root_id)[2]
    ] == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
    ]
    # Exactly one evaluation, one result, one attempt and no envelope.
    assert len(db.list_authority_policy_v2_evaluations(
        root_task_id=root_id, manager_agent=MANAGER,
    )) == 1

    retry2 = fixture.run_cli(payload)
    assert retry2.returncode == 0, retry2.stderr
    assert fixture.last_http()["status"] == 200
    changed = dict(body)
    changed["decision"] = {"action": "escalate", "reason": "changed after claim"}
    refused = fixture.run_cli(fixture.write_payload(changed, "changed-c3b.json"))
    assert refused.returncode != 0
    assert fixture.last_http()["status"] == 409

    # Real task/current session/receipt preserved; no continuation manufactured.
    task = db.get_task(root_id)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.current_session_id == session_id
    # THR-229 C3d5a isolated stage barrier: the causal launch stays HELD so this
    # staged-writer driver keeps its callable-stage public-boundary assertions and
    # does not depend on the now-automatic shipping hook.  The automatic
    # continuation path is proven by the dedicated acceptance cases.
    assert db.get_active_authority_continue_envelope(root_id) is None
    return root_id, candidate.candidate_id


def test_shipping_real_admitted_result_drives_claim_and_claim_audit(shipping):
    _drive_c3b_claim(shipping)


def test_shipping_historically_migrated_claim_and_claim_audit(tmp_path, monkeypatch):
    """The SAME real venue over a FULL historical schema migrated forward."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3b_claim(fixture)
    finally:
        fixture.stop()


def test_shipping_callable_stage_retained_eligibility_negative(shipping):
    """C3c: the real persisted venue drives a retained-eligibility refusal.

    This is callable-stage evidence: the shipping hook stays fail-closed and no
    actual same-root continuation is claimed yet.
    """
    _drive_c3b_claim(shipping, retained_eligibility_negative=True)


def test_shipping_historically_migrated_callable_stage_negative(tmp_path, monkeypatch):
    """The retained-eligibility callable-stage negative over a migrated DB."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3b_claim(fixture, retained_eligibility_negative=True)
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# C3d1: the real admitted result drives the callable refusal-housekeeping seam
# --------------------------------------------------------------------------


def _drive_c3d1_refusal(fixture: _ShippingFixture) -> str:
    """Real launch -> subprocess CLI admission -> callable refusal housekeeping.

    The provider launch is held only at the external process boundary, so the
    durable admitted result is genuine and the refusal seam runs against real
    persisted evidence.  The attempt is treated as an old-boot/interrupted
    pre-final attempt (the trusted current process identity differs), so
    housekeeping may safely refuse it.  This is callable-housekeeping evidence:
    the shipping hook stays fail-closed and no actual continuation/Pending/
    enqueue is claimed.
    """
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    assert attempt.stage == "admitted"
    row_id = results[0]["id"]
    db = fixture.org.db

    # Trusted current daemon-process identity differs from the attempt's origin
    # boot: this is an old-boot/interrupted pre-final attempt.
    db.bind_authority_policy_v2_process_boot_id("fixture-new-daemon-boot")
    outcome = db.finalize_authority_policy_v2_attempt_refusal(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, refusal_code="interrupted_pre_final",
    )
    assert outcome.status == "refused", outcome
    assert outcome.finalization_state == "refused"
    assert outcome.refusal_code == "interrupted_pre_final"
    final = db.get_authority_policy_v2_attempt_for_result(row_id)
    assert final is not None
    assert final.finalization_state == "refused"
    assert final.stage == "admitted"  # greatest committed stage retained

    task = db.get_task(root_id)
    assert task.status is TaskStatus.ESCALATED
    assert task.block_kind is None
    assert "authority_v2_refusal" in (task.note or "")

    stage_audits = db.list_authority_policy_v2_result_stage_audits(
        root_task_id=root_id, manager_agent=MANAGER,
    )
    assert [a["payload"]["stage"] for a in stage_audits] == ["admitted", "refused"]
    refusals = [
        a for a in db.get_audit_logs(root_id)
        if a["action"] == "completion_report"
        and isinstance(a["payload"], dict)
        and a["payload"].get("attempt_id") == attempt.attempt_id
    ]
    assert len(refusals) == 1
    assert refusals[0]["payload"]["refusal_code"] == "interrupted_pre_final"
    # No continuation was manufactured.
    assert db.get_active_authority_continue_envelope(root_id) is None

    # Exact CLI/HTTP retry stays read-only success with authenticated terminal
    # evidence: the durable result/attempt/audit counts and IDs are unchanged.
    before = (len(results), row_id, len(stage_audits), attempt.owner_attempt_id)
    retry = fixture.run_cli(payload)
    assert retry.returncode == 0, retry.stderr
    assert fixture.last_http()["status"] == 200
    after_results, after_attempt, after_audits = _admission_counts(fixture, root_id)
    assert (len(after_results), after_results[0]["id"], len(after_audits),
            after_attempt.owner_attempt_id) == before

    # Changed-body replay still refuses after the terminal refusal.
    changed = dict(body)
    changed["decision"] = {"action": "escalate", "reason": "changed after refusal"}
    refused = fixture.run_cli(fixture.write_payload(changed, "changed-c3d1.json"))
    assert refused.returncode != 0
    assert fixture.last_http()["status"] == 409

    # Read-only exact replay of the terminal refusal: never a second audit.
    replay = db.finalize_authority_policy_v2_attempt_refusal(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, refusal_code="interrupted_pre_final",
    )
    assert replay.status == "already_refused", replay
    assert len(db.list_authority_policy_v2_result_stage_audits(
        root_task_id=root_id, manager_agent=MANAGER,
    )) == 2

    # Discovery no longer lists the now-finalized attempt.
    assert all(
        t.attempt_id != attempt.attempt_id
        for t in db.list_authority_policy_v2_unfinalized_attempts()
    )

    # A genuine callable refusal, not a fabricated continuation.
    fixture.release_launch()
    fixture.join_workers()
    assert db.get_task(root_id).status is TaskStatus.ESCALATED
    assert db.get_active_authority_continue_envelope(root_id) is None
    return root_id


def test_shipping_real_callable_refusal_housekeeping(shipping):
    _drive_c3d1_refusal(shipping)


def test_shipping_historically_migrated_callable_refusal(tmp_path, monkeypatch):
    """The SAME real venue over a FULL historical schema migrated forward."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3d1_refusal(fixture)
    finally:
        fixture.stop()


def _drive_c3d1_refusal_failure(fixture: _ShippingFixture) -> str:
    """Real launch -> subprocess CLI admission -> failed refusal -> recovery.

    The provider launch is held only at the external process boundary, so the
    genuine admitted result/attempt share this in-process Database (the real
    live-owner token).  An injected task-CAS write failure rolls the WHOLE
    terminal transaction back, poisons the authentic owner token, prohibits any
    later policy claim/advancement, and leaves only later successful
    housekeeping plus read-only replay.  This is callable-housekeeping evidence:
    the shipping hook stays fail-closed and no continuation/Pending/enqueue is
    claimed.
    """
    from tests.test_authority_v2_refusal_housekeeping import _FailingConn

    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    assert attempt.stage == "admitted"
    row_id = results[0]["id"]
    db = fixture.org.db
    # The genuine in-process live-owner token is present: this is the authentic
    # uninterrupted owner, not an old-boot/second-connection contender.
    assert db._v2_live_attempt_owners.get(attempt.attempt_id) == attempt.owner_attempt_id

    real = db._conn
    db._conn = _FailingConn(real, "UPDATE tasks SET status")
    try:
        with pytest.raises(RuntimeError):
            db.finalize_authority_policy_v2_attempt_refusal(
                root_task_id=root_id, manager_agent=MANAGER,
                manager_session_id=session_id, result_id=row_id,
                refusal_code="interrupted_pre_final",
                owner_attempt_id=attempt.owner_attempt_id,
            )
    finally:
        db._conn = real

    # Exact retained prior rows after the rollback: J unfinalized, task
    # in-progress, only the admitted stage audit, and one result/attempt.
    retained = db.get_authority_policy_v2_attempt_for_result(row_id)
    assert retained is not None and retained.finalization_state == "unfinalized"
    assert db.get_task(root_id).status is TaskStatus.IN_PROGRESS
    assert [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ] == ["admitted"]

    # Refusal-only failure: the poisoned owner cannot claim/evaluate/consume and
    # allocates NO new candidate K.
    blocked = db.claim_authority_policy_v2_candidate(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id, max_revise_rounds=0,
    )
    assert blocked.status == "refused", blocked
    assert blocked.refusal_code == "owner_lost", blocked
    assert db.get_authority_policy_v2_candidate_for_result(row_id) is None

    # Only later successful housekeeping may commit, exactly once.
    outcome = db.finalize_authority_policy_v2_attempt_refusal(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row_id,
        refusal_code="interrupted_pre_final",
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert outcome.status == "refused", outcome
    assert [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ] == ["admitted", "refused"]
    assert db.get_task(root_id).status is TaskStatus.ESCALATED

    # Read-only exact replay, and the real CLI retry stays transport-success.
    replay = db.finalize_authority_policy_v2_attempt_refusal(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row_id,
        refusal_code="interrupted_pre_final",
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert replay.status == "already_refused", replay
    assert len(db.list_authority_policy_v2_result_stage_audits(
        root_task_id=root_id, manager_agent=MANAGER,
    )) == 2
    retry = fixture.run_cli(payload)
    assert retry.returncode == 0, retry.stderr
    assert fixture.last_http()["status"] == 200
    assert db.get_task(root_id).status is TaskStatus.ESCALATED
    assert db.get_active_authority_continue_envelope(root_id) is None

    fixture.release_launch()
    fixture.join_workers()
    assert db.get_task(root_id).status is TaskStatus.ESCALATED
    return root_id


def test_shipping_real_failed_refusal_only_housekeeping(shipping):
    _drive_c3d1_refusal_failure(shipping)


def test_shipping_historically_migrated_failed_refusal(tmp_path, monkeypatch):
    """The SAME real venue over a FULL historical schema migrated forward."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3d1_refusal_failure(fixture)
    finally:
        fixture.stop()


def _drive_c3d2_finalization(fixture: _ShippingFixture) -> str:
    """Real launch -> subprocess CLI admission -> real pre-final stages ->
    callable final continuation -> exact recovery settlement.

    The external provider launch is held only at the process boundary, so the
    durable admitted result/attempt/binding are genuine and the finalize/settle
    seams run against that real persisted evidence.  The fixture deliberately
    holds result CONSUMPTION (the real ordinary run-step consumer does not run
    while the launch is held) so the intermediate Pending/E/N/D state can be
    asserted; that limit is explicit.  This is callable finalization/settlement
    proof, NOT live consumer continuation, an actual enqueue, or full C04, and
    the shipping authority hook is NOT patched to claim Pending/enqueue.
    """
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    row_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import _strict_permission_surface_digest

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"

    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    candidate = db.get_authority_policy_v2_candidate_for_result(row_id)
    envelope = db.get_authority_policy_v2_continue_envelope_for_candidate(
        candidate.candidate_id
    )
    assert envelope is not None and envelope.lifecycle_state == "active"
    notification = db.get_authority_policy_v2_recovery_notification_for_envelope(
        envelope.envelope_id
    )
    assert notification is not None and notification.state == "needed"
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    assert dispatch is not None and dispatch.state == "pending"
    assert dispatch.generation_id == notification.notification_id
    task = db.get_task(root_id)
    assert task.status is TaskStatus.PENDING
    assert task.block_kind is None
    assert task.assigned_agent == MANAGER
    assert task.current_session_id == session_id
    assert [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ] == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
        "continued",
    ]
    assert db.get_active_authority_continue_envelope(root_id) is None
    active_v2 = db.get_authority_policy_v2_continue_envelope_for_root(root_id)
    assert active_v2 is not None and active_v2.envelope_id == envelope.envelope_id

    # Exact successful causal replay is read-only and allocates no second E/N/D.
    replay = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert replay.status == "already_continued", replay
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_recovery_notifications"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_root_dispatch"
    ).fetchone()[0] == 1

    # The REAL ordinary completion producer (Orchestrator._log_step_result)
    # writes the v2 result/session-attributed completion audit for THIS result;
    # ordinary settlement then authenticates it read-only with no Q and no
    # writes, proving the current event is scoped against prior manager history.
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (row_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=row_id,
    )
    produced = [
        row for row in db.get_audit_logs(root_id)
        if row["action"] == "completion_report"
        and isinstance(row["payload"], dict)
        and row["payload"].get("_result_row_id") == row_id
    ]
    assert len(produced) == 1
    assert produced[0]["payload"]["_result_session_id"] == session_id
    ordinary_before = db._conn.execute(
        "SELECT COUNT(*) FROM audit_log"
    ).fetchone()[0]
    ordinary = db.settle_authority_policy_v2_continuation_receipt(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id,
    )
    assert ordinary.status == "settled", ordinary
    assert ordinary.recovery is False and ordinary.receipt_settled is False
    assert db._conn.execute(
        "SELECT COUNT(*) FROM audit_log"
    ).fetchone()[0] == ordinary_before

    # Genuine recovery settlement against the REAL exact receipt identity.
    db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (root_id, MANAGER, "sess-origin", session_id, "provider-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_accepted", row_id, session_id),
    )
    db._conn.commit()
    settled = db.settle_authority_policy_v2_continuation_receipt(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, recovery_session_id=session_id,
        accepted_result_id=row_id, accepted_result_session_id=session_id,
    )
    assert settled.status == "settled", settled
    assert settled.receipt_settled is True
    receipt = db._conn.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone()
    assert receipt["state"] == "callback_consumed"
    retry_settle = db.settle_authority_policy_v2_continuation_receipt(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, recovery_session_id=session_id,
        accepted_result_id=row_id, accepted_result_session_id=session_id,
    )
    assert retry_settle.status == "already_settled_exact", retry_settle
    assert db.get_task(root_id).status is TaskStatus.PENDING

    # The unchanged CLI retry is still transport-success; a changed causal
    # payload still refuses.  Neither spends the envelope or mints another
    # candidate.
    retry = fixture.run_cli(payload)
    assert retry.returncode == 0, retry.stderr
    assert fixture.last_http()["status"] == 200
    changed = dict(body)
    changed["decision"] = {"action": "escalate", "reason": "changed after final"}
    refused = fixture.run_cli(fixture.write_payload(changed, "changed-c3d2.json"))
    assert refused.returncode != 0
    assert fixture.last_http()["status"] == 409
    assert len(db.get_task_results(root_id)) == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidates"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1

    # Release the held launch.  The ordinary un-wired consumer still fail-closes
    # (ESCALATE); this driver makes no claim about live continuation/enqueue.
    fixture.release_launch()
    fixture.join_workers()
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    return root_id


def test_shipping_real_callable_finalization_and_settlement(shipping):
    _drive_c3d2_finalization(shipping)


def test_shipping_historically_migrated_callable_finalization(tmp_path, monkeypatch):
    """The SAME real venue over a FULL historical schema migrated forward."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3d2_finalization(fixture)
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# C3d3a: the real finalized+settled venue drives publication bookkeeping
# --------------------------------------------------------------------------


def _drive_c3d3a_publication(fixture: _ShippingFixture) -> str:
    """Real launch -> CLI admission -> callable finalize/settle -> publication.

    The external provider launch is held only at the process boundary, so the
    durable admitted result/attempt/binding and the settlement evidence are
    genuine and the publication discovery/claim/acknowledgement seams run
    against that real persisted evidence.  The publication methods stay DARK:
    no queue call, generation admission, launch or continuation is claimed, and
    the fixture deliberately holds result consumption.  The provider launch
    remains the sole external-launch double.
    """
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    row_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import _strict_permission_surface_digest

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized

    notification = db.get_authority_policy_v2_recovery_notification(
        finalized.notification_id
    )
    assert notification is not None and notification.state == "needed"

    pub_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row_id,
    )
    # Bind the trusted daemon-process publisher identity up front; publication
    # claim uses it as the retained publisher boot, never a caller boolean.
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    # Missing settlement evidence refuses with no publication write.
    missing = db.claim_authority_policy_v2_notification_publication(**pub_kwargs)
    assert missing.status == "publication_pending", missing
    assert missing.reason == "evidence_drift"
    assert db.get_authority_policy_v2_recovery_notification(
        notification.notification_id
    ).state == "needed"
    assert [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ] == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
        "continued",
    ]

    # The REAL ordinary completion producer writes the attributed audit; the
    # real ordinary settlement then authenticates it read-only (no Q, no write).
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (row_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=row_id,
    )
    ordinary = db.settle_authority_policy_v2_continuation_receipt(**pub_kwargs)
    assert ordinary.status == "settled", ordinary
    assert ordinary.recovery is False and ordinary.receipt_settled is False

    # Real exact recovery settlement against the durable Q identity.
    db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (root_id, MANAGER, "sess-origin", session_id, "provider-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_accepted", row_id, session_id),
    )
    db._conn.commit()
    settled = db.settle_authority_policy_v2_continuation_receipt(
        **pub_kwargs, recovery_session_id=session_id,
        accepted_result_id=row_id, accepted_result_session_id=session_id,
    )
    assert settled.status == "settled", settled
    assert settled.receipt_settled is True

    # Discovery lists the finalized+settled generation (read-only).
    targets = db.list_authority_policy_v2_publication_targets()
    assert any(
        t.notification_id == notification.notification_id for t in targets
    ), targets

    # Claim once under the bound daemon-process publisher identity.
    claimed = db.claim_authority_policy_v2_notification_publication(**pub_kwargs)
    assert claimed.status == "claimed", claimed
    assert claimed.publication_attempt == 1
    assert claimed.publisher_boot_id == attempt.origin_boot_id
    assert db.get_authority_policy_v2_recovery_notification(
        notification.notification_id
    ).state == "publishing"
    assert len([
        a for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        ) if a["payload"]["stage"] == "publish_claimed"
    ]) == 1

    ack = db.acknowledge_authority_policy_v2_notification_publication(
        **pub_kwargs, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "published", ack
    retry_ack = db.acknowledge_authority_policy_v2_notification_publication(
        **pub_kwargs, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert retry_ack.status == "published", retry_ack
    assert db.get_authority_policy_v2_recovery_notification(
        notification.notification_id
    ).state == "published"

    # Missing retained claim evidence refuses acknowledgement with no repair and
    # no state regression.
    db._conn.execute(
        "DELETE FROM audit_log WHERE task_id=? AND action=? "
        "AND json_extract(payload,'$.stage')='publish_claimed'",
        (root_id, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION),
    )
    db._conn.commit()
    before = db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    broken = db.acknowledge_authority_policy_v2_notification_publication(
        **pub_kwargs, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert broken.status == "ack_pending", broken
    assert broken.reason == "evidence_drift"
    assert db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before
    assert db.get_authority_policy_v2_recovery_notification(
        notification.notification_id
    ).state == "published"

    # Release the held launch.  The ordinary un-wired consumer still fail-closes;
    # this driver makes no claim about live continuation or enqueue.
    fixture.release_launch()
    fixture.join_workers()
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    return root_id


def test_shipping_real_callable_publication_bookkeeping(shipping):
    _drive_c3d3a_publication(shipping)


def test_shipping_historically_migrated_callable_publication(tmp_path, monkeypatch):
    """The SAME publication-stage venue over a FULL historical migrated DB."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start()
    try:
        _drive_c3d3a_publication(fixture)
    finally:
        fixture.stop()


# C3d3b: the real finalized+settled venue drives the ACTUAL publisher into the
# REAL TaskQueue -> Dispatcher/run_step -> tagged generation admission ->
# reserved-session launch at the held external boundary.  This is
# publication/admission-stage fixture proof with the earlier consumer staged;
# the provider launch remains the sole external-launch double.  It makes no
# claim about a real authority-consumer REQUEST_CHANGES/next-result spend.
def _delete_result_stage_audit_rows(db, root_id: str, stage: str) -> None:
    """Fixture-level removal of the exact retained result-stage audit rows."""
    from runtime.models import AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION

    for audit in db.get_audit_logs(root_id):
        payload = audit.get("payload")
        if (
            audit.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and isinstance(payload, dict) and payload.get("stage") == stage
        ):
            db._conn.execute("DELETE FROM audit_log WHERE id=?", (audit["id"],))
    db._conn.commit()


def _delete_ordinary_completion_audit_rows(db, root_id: str, result_id: int) -> None:
    """Fixture-level removal of the exact ordinary completion evidence."""
    for audit in db.get_audit_logs(root_id):
        payload = audit.get("payload")
        if (
            audit.get("action") == "completion_report"
            and isinstance(payload, dict)
            and "_recovery_session_id" not in payload
            and payload.get("_result_row_id") == result_id
        ):
            db._conn.execute("DELETE FROM audit_log WHERE id=?", (audit["id"],))
    db._conn.commit()


def _delete_recovery_settled_audit_rows(db, root_id: str) -> None:
    """Fixture-level removal of the exact recovery-settled evidence."""
    from runtime.models import AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION

    for audit in db.get_audit_logs(root_id):
        if audit.get("action") == AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION:
            db._conn.execute("DELETE FROM audit_log WHERE id=?", (audit["id"],))
    db._conn.commit()


def _install_admission_negative(
    db, root_id: str, result_id: int, negative: str, done: threading.Event,
    publisher_done: threading.Event,
) -> tuple[str, ...]:
    """Corrupt ONE prerequisite at the EXACT real run-step boundary.

    The wrapper is installed on the SAME Database instance the real
    Dispatcher/run_step consumes and then delegates to the UNCHANGED production
    method, so the negative exercises the genuine production evidence reader
    (no copied logic and no replacement of the shipping readers/writers).
    ``publisher_done`` is set by the caller only after the REAL publisher has
    fully returned, so the fixture's own corruption can never race (and thereby
    invalidate) the publisher acknowledgement; ``done`` fires only after the
    real method has returned.
    """
    if negative == "missing_publication":
        original = db.try_claim_v2_continuation_generation

        def _corrupting_claim(**kwargs):
            try:
                publisher_done.wait(timeout=30.0)
                _delete_result_stage_audit_rows(db, root_id, "publish_claimed")
                return original(**kwargs)
            finally:
                done.set()

        db.try_claim_v2_continuation_generation = _corrupting_claim
        return ("try_claim_v2_continuation_generation",)
    if negative == "missing_settlement_proof":
        original = db.settle_v2_continuation_generation_admission

        def _corrupting_settle(**kwargs):
            try:
                publisher_done.wait(timeout=30.0)
                _delete_ordinary_completion_audit_rows(db, root_id, result_id)
                _delete_recovery_settled_audit_rows(db, root_id)
                return original(**kwargs)
            finally:
                done.set()

        db.settle_v2_continuation_generation_admission = _corrupting_settle
        return ("settle_v2_continuation_generation_admission",)
    if negative == "ack_corruption":
        # Deterministic admitted/settled acknowledgement corruption: the REAL
        # publisher's acknowledgement is gated at a synchronization barrier
        # until the REAL consumer has genuinely admitted the generation; only
        # then is one conflicting related ``publish_returned`` observation
        # appended (fixture-level, never a production write) and the real
        # acknowledgement allowed to run.  The consumer's own settlement is
        # gated behind that corruption so the ordering cannot race.  No copied
        # production reader/writer/ack logic and no elapsed sleeps.
        original_ack = db.acknowledge_authority_policy_v2_notification_publication
        original_settle = db.settle_v2_continuation_generation_admission
        corrupted = threading.Event()

        def _await_admitted() -> str:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
                if dispatch is not None and dispatch.generation_id:
                    notification = db.get_authority_policy_v2_recovery_notification(
                        dispatch.generation_id
                    )
                    if (
                        notification is not None
                        and notification.state in ("admitted", "settled")
                    ):
                        return notification.notification_id
                time.sleep(0.02)
            raise AssertionError("the generation was never admitted")

        def _corrupting_ack(**kwargs):
            try:
                generation = _await_admitted()
                _append_conflicting_publish_returned(db, root_id, generation)
                corrupted.set()
                return original_ack(**kwargs)
            finally:
                done.set()

        def _gated_settle(**kwargs):
            corrupted.wait(timeout=30.0)
            return original_settle(**kwargs)

        db.acknowledge_authority_policy_v2_notification_publication = _corrupting_ack
        db.settle_v2_continuation_generation_admission = _gated_settle
        return (
            "acknowledge_authority_policy_v2_notification_publication",
            "settle_v2_continuation_generation_admission",
        )
    raise AssertionError(f"unknown negative: {negative}")


def _append_conflicting_publish_returned(db, root_id: str, generation: str) -> None:
    """Fixture-level ONE corrupt related ``publish_returned`` observation.

    Built from the ONE authentic retained ``publish_claimed`` event for this
    exact generation (so every causal reference is an exact match -- it is
    RELATED, never unrelated history) but carrying a null publisher boot.  The
    malformed related observation must make BOTH the settlement retained-evidence
    reader and the acknowledgement reader refuse, so the scenario is
    deterministic and no continuation launch ever follows.
    """
    import json as _json

    from runtime.models import AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION

    claim = None
    for audit in db.get_audit_logs(root_id):
        payload = audit.get("payload")
        if (
            audit.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and isinstance(payload, dict) and payload.get("stage") == "publish_claimed"
        ):
            claim = payload
    assert claim is not None, "no retained publish_claimed event"
    conflict = dict(claim)
    conflict["stage"] = "publish_returned"
    conflict["publisher_boot_id"] = None
    db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?,?,?,?,?)",
        (root_id, MANAGER, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
         _json.dumps(conflict), "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()


def _assert_admission_negative(
    fixture: _ShippingFixture, db, root_id: str, generation: str, negative: str,
    attr, done: threading.Event, original_session: str,
) -> str:
    """Assert one corrupted-prerequisite negative through the REAL run-step.

    No launch, preserved durable residue and no ordinary fallback: the held
    external boundary still carries the original manager session and the
    degraded durable state is exactly the genuine pre-transition evidence.
    """
    try:
        assert done.wait(timeout=30.0), "the real run-step never reached the boundary"
    finally:
        for name in attr if isinstance(attr, tuple) else (attr,):
            try:
                delattr(db, name)
            except AttributeError:
                pass
    notification = db.get_authority_policy_v2_recovery_notification(generation)
    assert notification is not None
    task = db.get_task(root_id)
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    if negative == "missing_publication":
        assert notification.state == "published", notification.state
        assert task.status is TaskStatus.PENDING, task.status
        assert dispatch is not None and dispatch.state == "pending"
        assert stages.count("generation_claimed") == 0
        assert stages.count("notification_settled") == 0
        assert fixture.captured["session_id"] == original_session
    else:
        assert notification.state == "admitted", notification.state
        assert task.status is TaskStatus.IN_PROGRESS, task.status
        assert task.current_session_id == notification.next_session_id
        assert dispatch is not None and dispatch.state == "admitted"
        assert stages.count("generation_claimed") == 1
        assert stages.count("notification_settled") == 0
        # The settlement refusal held launch: the held external boundary still
        # carries the ORIGINAL manager session, never the reserved next session.
        assert fixture.captured["session_id"] == original_session
        assert notification.next_session_id != original_session
        if negative == "ack_corruption":
            # The real publisher acknowledgement refused: exactly the ONE
            # fixture-injected conflicting observation remains and no fabricated
            # acknowledgement observation was appended.
            assert stages.count("publish_returned") == 1
    # The durable generation count is unchanged: no second admission/allocation.
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    fixture.release_launch()
    fixture.join_workers()
    return root_id


def _drive_c3d3b_admission(
    fixture: _ShippingFixture, *, negative: str | None = None,
) -> str:
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    row_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import (
        _strict_permission_surface_digest,
        publish_authority_policy_v2_notifications,
    )

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id

    # Real ordinary completion evidence + exact recovery settlement (identical
    # to the C3d3a venue) so the generation is genuinely settled.
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (row_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=row_id,
    )
    pub_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row_id,
    )
    assert db.settle_authority_policy_v2_continuation_receipt(**pub_kwargs).status == "settled"
    db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (root_id, MANAGER, "sess-origin", session_id, "provider-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_accepted", row_id, session_id),
    )
    db._conn.commit()
    settled = db.settle_authority_policy_v2_continuation_receipt(
        **pub_kwargs, recovery_session_id=session_id,
        accepted_result_id=row_id, accepted_result_session_id=session_id,
    )
    assert settled.status == "settled" and settled.receipt_settled is True

    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    negative_done = threading.Event()
    publisher_done = threading.Event()
    negative_attr: str | None = None
    if negative is not None:
        negative_attr = _install_admission_negative(
            db, root_id, row_id, negative, negative_done, publisher_done,
        )

    # The REAL publisher discovers, claims, calls the REAL TaskQueue with the
    # tagged generation metadata and acknowledges the exact claim.
    receipts = publish_authority_policy_v2_notifications(
        fixture.org.orchestrator, fixture.state.queue,
    )
    # Release the gated negative corruption only AFTER the publisher has fully
    # returned, so the fixture's own evidence deletion can never race the
    # publisher's acknowledgement (the healthy path has no gate and exercises
    # the real concurrent consumer on purpose).
    publisher_done.set()
    # The REAL second worker consumes the tagged item concurrently, so BOTH
    # orderings are legitimate: a winning acknowledgement reports ``published``
    # and the durable state may still be ``published``; the documented
    # consumer-outruns-acknowledgement shape reports ``publish_returned`` and
    # never regresses state, leaving ``admitted``/``settled``.  The substantive
    # one-admission proof is the deterministic durable assertions below (and in
    # ``_assert_admission_negative``), never this racy intermediate read.
    # The acknowledgement-corruption negative is deterministic: the barrier
    # waits for real admission first, so the publisher reports the refused
    # ``ack_pending`` and never appends an observation.
    expected_receipt = (
        ("ack_pending",) if negative == "ack_corruption"
        else ("published", "publish_returned")
    )
    assert receipts and receipts[0]["status"] in expected_receipt, receipts
    notification = db.get_authority_policy_v2_recovery_notification(generation)
    assert notification is not None and notification.state in (
        "published", "admitted", "settled",
    ), notification.state

    if negative is not None:
        return _assert_admission_negative(
            fixture, db, root_id, generation, negative, negative_attr,
            negative_done, session_id,
        )

    # The tagged item is now in the REAL TaskQueue.  A second real worker
    # consumes it (the first invocation is still held at the external boundary,
    # exactly the real "manager callback while the session runs" shape), so the
    # tagged generation admission -> settlement -> held launch happens on the
    # real Dispatcher/run_step path.  Poll the durable evidence, never a mock.
    reserved = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    assert reserved != session_id
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    assert dispatch is not None and dispatch.state == "admitted"
    assert dispatch.generation_id == generation
    task = db.get_task(root_id)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.assigned_agent == MANAGER
    assert task.current_session_id == reserved
    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("generation_claimed") == 1
    assert stages.count("notification_settled") == 1
    # The held external boundary received the EXACT reserved runtime session.
    assert fixture.captured["session_id"] == reserved
    # Exactly one durable generation admission (no duplicate from replay).
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    # Release both held invocations and drain before the fixture stops.
    fixture.release_launch()
    fixture.join_workers()
    return root_id


def _dump_owned_rows(db, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in db._conn.execute(sql, params).fetchall()]


def _drive_c3d4b_corrupt_settlement(
    fixture: _ShippingFixture, *, root_id: str, recovery_session: str,
    row_id: int, generation: str, raw_puts: list, tagged_puts: list,
) -> str:
    """Counter-based corrupt-refusal over the GENUINE accepted recovery.

    C1/C2 preconditions are real (a genuine accepted recovery R/Q, a finalized
    continuation G).  The ONLY settlement evidence -- the exact accepted Q row
    -- is then REMOVED and the REAL recovered consumer is exercised.  It must
    return a bounded refusal, never raise, and make ZERO raw/accepted queue
    calls, ZERO generation claims, ZERO external launches and no ordinary
    decision body while preserving the final E/N/D residue.  The exact row is
    then restored and the permitted retry publishes exactly once.
    """
    from runtime.models import (
        AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION,
    )
    from runtime.orchestrator.authority import (
        POST_FINAL_SETTLEMENT_REFUSED,
        reconcile_authority_policy_v2_post_final,
    )

    db = fixture.org.db
    q_before = _dump_owned_rows(
        db, "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    )
    assert len(q_before) == 1 and q_before[0]["state"] == "callback_accepted"
    final_before = {
        "envelopes": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE root_task_id=?", (root_id,)),
        "notifications": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE root_task_id=?", (root_id,)),
        "dispatch": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_root_dispatch "
                "WHERE root_task_id=?", (root_id,)),
        "task": _dump_owned_rows(
            db, "SELECT * FROM tasks WHERE id=?", (root_id,)),
        "attempts": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_attempts WHERE root_task_id=?",
            (root_id,)),
        "candidates": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_candidates WHERE root_task_id=?",
            (root_id,)),
        "evaluations": _dump_owned_rows(
            db,
            "SELECT e.* FROM authority_policy_v2_evaluations AS e "
            "JOIN authority_policy_v2_candidates AS c "
            "ON c.candidate_id = e.candidate_id WHERE c.root_task_id=?",
            (root_id,)),
    }
    put_count = len(raw_puts)
    tagged_count = len(tagged_puts)
    launch_before = fixture.launch_count()

    # REMOVE the only genuine settlement evidence.
    db._conn.execute(
        "DELETE FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    )
    db._conn.commit()

    # The REAL recovered consumer must return a bounded refusal (never raise).
    status = reconcile_authority_policy_v2_post_final(
        fixture.org.orchestrator, root_task_id=root_id,
    )
    assert status == POST_FINAL_SETTLEMENT_REFUSED, status

    # Zero raw/accepted queue calls, claims, launches and normal decision body.
    assert len(raw_puts) == put_count
    assert len(tagged_puts) == tagged_count
    assert fixture.launch_count() == launch_before
    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("publish_claimed") == 0
    assert stages.count("generation_claimed") == 0
    assert stages.count("notification_settled") == 0
    actions = [a["action"] for a in db.get_audit_logs(root_id)]
    assert actions.count(AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION) == 0
    assert db.get_task(root_id).status is TaskStatus.PENDING
    # The committed final E/N/D residue is byte-identical.
    final_after = {
        "envelopes": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE root_task_id=?", (root_id,)),
        "notifications": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE root_task_id=?", (root_id,)),
        "dispatch": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_root_dispatch "
                "WHERE root_task_id=?", (root_id,)),
        "task": _dump_owned_rows(
            db, "SELECT * FROM tasks WHERE id=?", (root_id,)),
        "attempts": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_attempts WHERE root_task_id=?",
            (root_id,)),
        "candidates": _dump_owned_rows(
            db, "SELECT * FROM authority_policy_v2_candidates WHERE root_task_id=?",
            (root_id,)),
        "evaluations": _dump_owned_rows(
            db,
            "SELECT e.* FROM authority_policy_v2_evaluations AS e "
            "JOIN authority_policy_v2_candidates AS c "
            "ON c.candidate_id = e.candidate_id WHERE c.root_task_id=?",
            (root_id,)),
    }
    assert final_after == final_before

    # Remove the injection: restore the EXACT genuine accepted Q and execute the
    # permitted retry -- it settles and publishes exactly once.
    db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (id, task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id, settled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            q_before[0]["id"], q_before[0]["task_id"], q_before[0]["agent"],
            q_before[0]["origin_session_id"], q_before[0]["recovery_session_id"],
            q_before[0]["provider_session_id"], q_before[0]["claimed_at"],
            q_before[0]["expires_at"], q_before[0]["state"],
            q_before[0]["accepted_result_id"],
            q_before[0]["accepted_result_session_id"],
            q_before[0]["settled_at"],
        ),
    )
    db._conn.commit()
    retry = reconcile_authority_policy_v2_post_final(
        fixture.org.orchestrator, root_task_id=root_id,
    )
    assert retry in ("reconciled", "settlement_refused"), retry
    if retry == "reconciled":
        assert len(tagged_puts) == tagged_count + 1
        assert tagged_puts[-1]["authority_v2_generation"] == generation
    fixture.release_launch()
    fixture.join_workers()
    return root_id


def _drive_c3d4b_post_final_recovery(
    fixture: _ShippingFixture, *, negative: str | None = None,
) -> str:
    """C3d4b GENUINE current recovery -> post-final settlement -> dispatch.

    Unlike the earlier ordinary-callback premise, the origin Codex turn returns
    CLEANLY without a callback and carries a real provider conversation identity
    separate from its daemon runtime session, so ``run_step``'s REAL
    completion-recovery claim + launch binding run: a DISTINCT recovery runtime
    session is claimed and the provider resume id is the origin provider
    conversation -- never the runtime session.  The ACTUAL shipping CLI
    subprocess then drives the real HTTP callback route into a persisted
    accepted recovery R/Q (state ``callback_accepted``) under the real writers.
    Only the external provider launch boundary is doubled.

    The automatic pre-final v2 hook remains DARK in this unit, so the accepted
    pre-final stages (claim/evaluate/consume/finalize) are staged through the
    REAL public writers and are explicitly labelled as staged.  The post-final
    settlement, tagged publication and generation admission then run through the
    ACTUAL production ``_consume_accepted_completion_recovery`` ->
    ``reconcile_authority_policy_v2_post_final`` seam and the real
    TaskQueue/Dispatcher/run_step path.
    """
    from runtime.models import AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION

    fixture.activate_v2_pair()
    provider_resume_id = "provider-resume-c3d4b"
    fixture.install_launch_hold(provider_session_id=provider_resume_id)
    root_id = fixture.create_and_enqueue_root()

    origin = fixture.wait_for_launch()
    origin_session = origin["session_id"]
    assert origin_session

    # Count the REAL queue attempts separately from ACCEPTED (tagged) puts.
    queue = fixture.state.queue
    raw_puts: list[dict] = []
    tagged_puts: list[dict] = []
    original_put = queue.put_nowait

    def _counting_put(slug, task_id, *, metadata=None):
        raw_puts.append({"slug": slug, "task_id": task_id, "metadata": metadata})
        if isinstance(metadata, dict) and metadata.get("authority_v2_generation"):
            tagged_puts.append(dict(metadata))
        return original_put(slug, task_id, metadata=metadata)

    fixture.monkeypatch.setattr(queue, "put_nowait", _counting_put)

    # Release ONLY the origin turn.  It returns success with a separate provider
    # conversation id and NO callback -- exactly the real premise for run_step's
    # completion-recovery claim.
    fixture.release_session(origin_session)
    recovery_launch = fixture.wait_for_launch_for(root_id, after=1)
    recovery_session = recovery_launch["session_id"]
    assert recovery_session and recovery_session != origin_session
    assert recovery_launch.get("resume_session_id") == provider_resume_id
    assert recovery_launch.get("recovery") is True

    binding = _binding(fixture, root_id, recovery_session)
    assert binding is not None and binding["mode"] == "v2"

    # ACTUAL shipping CLI subprocess -> real HTTP route -> persisted accepted
    # recovery R/Q under the real writers.
    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    cli = fixture.run_cli(payload)
    assert cli.returncode == 0, cli.stderr
    assert fixture.last_http()["status"] == 200
    callback_status = fixture.last_http()["status"]
    callback_request = fixture.last_http()["body"]
    callback_response = fixture.last_http()["response_body"]

    db = fixture.org.db
    receipt = dict(db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone())
    assert receipt["origin_session_id"] == origin_session
    assert receipt["recovery_session_id"] == recovery_session
    assert receipt["provider_session_id"] == provider_resume_id
    assert receipt["state"] == "callback_accepted"
    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    row_id = results[0]["id"]
    assert receipt["accepted_result_id"] == row_id
    assert receipt["accepted_result_session_id"] == recovery_session
    assert db._conn.execute(
        "SELECT session_id FROM task_results WHERE id=?", (row_id,)
    ).fetchone()["session_id"] == recovery_session
    # Actual callback HTTP status/body + durable result provenance: the persisted
    # R carries exactly the transport summary/status/session under the real route.
    assert callback_status == 200
    assert json.loads(callback_response) == {"ok": True}
    assert json.loads(callback_request)["session_id"] == recovery_session
    assert (
        json.loads(callback_request)["manager_self_evaluation"]
        == body["manager_self_evaluation"]
    )
    persisted_result = dict(db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (row_id,)
    ).fetchone())
    assert persisted_result["agent"] == MANAGER
    assert persisted_result["session_id"] == recovery_session
    assert persisted_result["status"] == "completed"
    assert persisted_result["output_summary"] == body["summary"]

    # Exact callback replay while the recovery generation is still the active
    # owner: a bounded read-only success with no second result/attempt/audit.
    replay = fixture.run_cli(payload)
    assert replay.returncode == 0, replay.stderr
    replay_status = fixture.last_http()["status"]
    replay_response = fixture.last_http()["response_body"]
    assert replay_status == 200
    assert json.loads(replay_response) == {"ok": True}
    after_results, after_attempt, after_audits = _admission_counts(fixture, root_id)
    assert len(after_results) == 1 and after_results[0]["id"] == row_id
    assert after_attempt.owner_attempt_id == attempt.owner_attempt_id
    assert [a["id"] for a in after_audits] == [a["id"] for a in _audits]

    # The REAL production owner binding (never a test boot string).
    fixture.org.bind_authority_v2_owner()
    assert db._v2_process_boot_id == fixture.org.authority_v2_origin_boot_id
    assert attempt.origin_boot_id == fixture.org.authority_v2_origin_boot_id

    # LABELLED STAGING through REAL public writers: the automatic pre-final v2
    # hook is dark in this unit, so the accepted pre-final stages are driven
    # explicitly with the GENUINE recovery identity (never a fabricated Q).
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=recovery_session,
        result_id=row_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id

    # The SAME root is preserved; finalization returns it to Pending (never a new
    # root / successor) with the durable pointer naming exactly this G.
    assert db.get_task(root_id).id == root_id
    assert db.get_task(root_id).status is TaskStatus.PENDING
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "pending"
    assert db.get_authority_policy_v2_root_dispatch(root_id).generation_id == generation
    assert db.get_authority_policy_v2_recovery_notification(generation).state == "needed"
    assert not tagged_puts and not raw_puts
    assert fixture.launch_count() == 2

    if negative == "corrupt_settlement":
        return _drive_c3d4b_corrupt_settlement(
            fixture, root_id=root_id, recovery_session=recovery_session,
            row_id=row_id, generation=generation, raw_puts=raw_puts,
            tagged_puts=tagged_puts,
        )

    # Release ONLY the recovery turn so the REAL run_step consumer reads the
    # durable accepted result, classifies it causal, and settles + publishes.
    fixture.release_session(recovery_session)
    reserved = None
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    assert reserved != recovery_session and reserved != origin_session
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    assert dispatch.state == "admitted" and dispatch.generation_id == generation

    # Q consumed by the ACTUAL settlement writer (never by this test).
    receipt = dict(db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone())
    assert receipt["state"] == "callback_consumed"
    assert receipt["accepted_result_id"] == row_id

    task = db.get_task(root_id)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.assigned_agent == MANAGER
    assert task.current_session_id == reserved
    assert task.orchestration_step_count == 2
    assert fixture.captured["session_id"] == reserved

    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("publish_claimed") == 1
    assert stages.count("published") == 1
    assert stages.count("generation_claimed") == 1
    assert stages.count("notification_settled") == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_evaluations"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidates"
    ).fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidate_audit"
    ).fetchone()[0] == 4
    audits = db.get_audit_logs(root_id)
    actions = [a["action"] for a in audits]
    assert actions.count(AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION) == 1
    # Both required settlement audits: the legitimate ordinary producer audit
    # and the recovery-owned settlement completion audit coexist.
    recovery_completions = [
        a for a in audits
        if a["action"] == "completion_report"
        and isinstance(a.get("payload"), dict)
        and a["payload"].get("_recovery_session_id") == recovery_session
    ]
    assert len(recovery_completions) == 1
    assert actions.count("completion_report") >= 1

    # One raw queue attempt, one ACCEPTED tagged publication with the exact G/P
    # metadata, one claim and exactly one reserved external launch.
    assert len(raw_puts) == 1 and len(tagged_puts) == 1
    assert tagged_puts[0]["authority_v2_generation"] == generation
    assert tagged_puts[0]["publication_attempt"] == 1
    assert fixture.launch_count() == 3

    # Exact post-final replay: the production seam is READ-ONLY after settlement
    # -- no second admission/evaluation/remint/ordinary decision/queue call.
    from runtime.orchestrator.authority import reconcile_authority_policy_v2_post_final

    reconcile_authority_policy_v2_post_final(
        fixture.org.orchestrator, root_task_id=root_id,
    )
    assert len(raw_puts) == 1 and len(tagged_puts) == 1
    assert fixture.launch_count() == 3
    assert [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ].count("generation_claimed") == 1

    fixture._c3d4b_callback_evidence = {
        "callback_status": callback_status,
        "callback_body": callback_response,
        "replay_status": replay_status,
        "replay_body": replay_response,
        "origin_session_id": origin_session,
        "recovery_session_id": recovery_session,
        "provider_resume_id": provider_resume_id,
        "generation": generation,
        "reserved_session_id": reserved,
    }

    fixture.release_launch()
    fixture.join_workers()
    return root_id

def test_shipping_real_publication_and_generation_admission(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_publication_admission(tmp_path, monkeypatch):
    """The SAME publication/admission venue over a FULL historical migrated DB."""
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture)
    finally:
        fixture.stop()


# C3d4b: the ACTUAL accepted-recovery seam owns settlement + tagged publication
# through the same owned-runtime venue, fresh AND full historical-migrated,
# with a corrupt-settlement refusal.

def test_shipping_real_post_final_recovery_seam(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d4b_post_final_recovery(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_post_final_recovery(tmp_path, monkeypatch):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d4b_post_final_recovery(fixture)
    finally:
        fixture.stop()


def test_shipping_post_final_corrupt_settlement_refuses(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d4b_post_final_recovery(fixture, negative="corrupt_settlement")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_corrupt_settlement_refuses(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d4b_post_final_recovery(fixture, negative="corrupt_settlement")
    finally:
        fixture.stop()


# C3d3b correction: representative missing-prerequisite negatives through the
# SAME real publisher -> TaskQueue -> Dispatcher/run_step -> held external
# launch venue, fresh AND full historical-migrated.


def test_shipping_missing_publication_prerequisite_refuses_admission(
    tmp_path, monkeypatch,
):
    """A deleted retained publication claim refuses admission with no launch."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="missing_publication")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_missing_publication_prerequisite(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="missing_publication")
    finally:
        fixture.stop()


def test_shipping_missing_settlement_proof_holds_launch(tmp_path, monkeypatch):
    """A deleted ordinary completion holds settlement and the external launch."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="missing_settlement_proof")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_missing_settlement_proof(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="missing_settlement_proof")
    finally:
        fixture.stop()


# C3d3b correction (TASK-8555): the deterministic admitted/settled
# acknowledgement-corruption scenario through the SAME real publisher ->
# TaskQueue -> Dispatcher/run_step -> held external launch venue, fresh AND
# full historical-migrated.  A barrier establishes actual admission BEFORE the
# one conflicting related observation is injected and the REAL publisher
# acknowledgement is allowed to run, so the refusal is deterministic (no
# elapsed sleeps) and the healthy consumer-outruns-ack positive is retained.


def test_shipping_admitted_acknowledgement_corruption_refuses(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="ack_corruption")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_ack_corruption_refuses(tmp_path, monkeypatch):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3b_admission(fixture, negative="ack_corruption")
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# C3d3c1: the ACTUAL reserved next invocation + real R2 callback admission,
# then an explicit invocation of the real public spend-to-ready writer.
#
# This is stage proof with a direct storage handoff -- explicitly NOT
# common-consumer integration, NOT final CLI->hook->Pending/enqueue acceptance.
# The public spend writer stays DARK (no automatic caller).  The provider launch
# remains the sole external-launch double.
# --------------------------------------------------------------------------


class _SpendFailingConn:
    """Test-only connection wrapper injecting one exact spend-audit failure."""

    def __init__(self, real, *, audit_stage: str) -> None:
        self._real = real
        self._audit_stage = audit_stage

    def execute(self, sql, *args, **kwargs):
        params = args[0] if args else None
        if (
            "INSERT INTO audit_log" in sql
            and isinstance(params, (tuple, list)) and len(params) >= 4
            and isinstance(params[3], str) and self._audit_stage in params[3]
        ):
            raise RuntimeError("injected spend audit failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _spend_call(db, *, root_id, session_id, causal_id, generation, reserved, r2):
    return db.spend_authority_policy_v2_continue_envelope(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=causal_id,
        generation_id=generation, next_session_id=reserved,
        spending_result_id=r2,
    )


def _drive_c3d3c1_spend(fixture: _ShippingFixture) -> str:
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body)
    result = fixture.run_cli(payload)
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    causal_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import (
        _strict_permission_surface_digest,
        publish_authority_policy_v2_notifications,
    )

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=causal_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id

    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (causal_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=causal_id,
    )
    pub_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=causal_id,
    )
    assert db.settle_authority_policy_v2_continuation_receipt(**pub_kwargs).status == "settled"
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    # The REAL publisher -> REAL TaskQueue -> Dispatcher/run_step -> tagged
    # generation admission reserves the next session and holds its launch.
    receipts = publish_authority_policy_v2_notifications(
        fixture.org.orchestrator, fixture.state.queue,
    )
    assert receipts and receipts[0]["status"] in ("published", "publish_returned"), receipts

    reserved = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    assert reserved != session_id
    assert fixture.captured["session_id"] == reserved

    # The reserved invocation is STILL HELD at the real external launch
    # boundary: produce its REAL persisted result R2 through the actual
    # shipping subprocess CLI -> HTTP callback route (a genuine new R2
    # authority attempt J2), with the task still in_progress and the decision
    # not yet applied.
    reserved_binding = _binding(fixture, root_id, reserved)
    assert reserved_binding is not None and reserved_binding["mode"] == "v2"
    reserved_body = _completion_body(reserved_binding, root_id)
    reserved_payload = fixture.write_payload(
        reserved_body, name="completion-reserved.json",
    )
    reserved_result = fixture.run_cli(reserved_payload)
    assert reserved_result.returncode == 0, reserved_result.stderr
    assert fixture.last_http()["status"] == 200

    rows = db.get_task_results(root_id)
    r2_row = rows[-1]
    assert r2_row["session_id"] == reserved
    r2 = r2_row["id"]
    assert r2 != causal_id
    # A genuine NEW R2 authority attempt exists and is entirely untouched by
    # the spend writer.
    r2_attempt = db.get_authority_policy_v2_attempt_for_result(r2)
    assert r2_attempt is not None and r2_attempt.result_id == r2
    before_task = db.get_task(root_id)
    assert before_task.status is TaskStatus.IN_PROGRESS
    assert before_task.current_session_id == reserved

    # Deterministic spend-audit failure: E active / D admitted / R2 retained.
    real = db._conn
    db._conn = _SpendFailingConn(real, audit_stage="spent")
    try:
        failed = _spend_call(
            db, root_id=root_id, session_id=session_id, causal_id=causal_id,
            generation=generation, reserved=reserved, r2=r2,
        )
    finally:
        db._conn = real
    assert failed.status == "spend_pending", failed
    assert failed.reason == "spend_failed", failed
    active = db.get_authority_policy_v2_continue_envelope(finalized.envelope_id)
    assert active.lifecycle_state == "active", active
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "admitted"
    assert db._conn.execute(
        "SELECT COUNT(*) FROM task_results WHERE id=?", (r2,),
    ).fetchone()[0] == 1

    # Exact retry of the SAME spend transaction succeeds.
    spend = _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    )
    assert spend.status == "spent", spend
    assert spend.decision_state == "ready"

    consumed = db.get_authority_policy_v2_continue_envelope(finalized.envelope_id)
    assert consumed.lifecycle_state == "consumed"
    assert consumed.spending_result_id == r2
    assert consumed.decision_state == "ready"
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    assert dispatch.state == "retired" and dispatch.generation_id == generation
    notification = db.get_authority_policy_v2_recovery_notification(generation)
    assert notification.state == "settled"
    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("spent") == 1
    # No normal decision/child/enqueue effect and no task-status change.
    after_task = db.get_task(root_id)
    assert after_task.status is TaskStatus.IN_PROGRESS
    assert after_task.current_session_id == reserved
    assert after_task.orchestration_step_count == before_task.orchestration_step_count
    assert db.get_authority_policy_v2_attempt_for_result(r2).attempt_id == r2_attempt.attempt_id

    # Read-only exact replay: no remint, no second receipt/audit, no dispatch.
    replay = _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    )
    assert replay.status == "already_spent_exact", replay
    assert replay.report_digest
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes "
        "WHERE spending_result_id=?", (r2,),
    ).fetchone()[0] == 1

    # ------------------------------------------------------------------
    # A/B negatives AT THE ACTUAL SPEND BOUNDARY over the genuinely admitted
    # R2.  These are deliberately labelled FIXTURE CORRUPTION of retained
    # evidence AFTER genuine shipping admission; the healthy control above
    # (successful spend + exact replay) is preserved and re-asserted after
    # each corruption is removed.
    # ------------------------------------------------------------------
    candidate = db.get_authority_policy_v2_candidate_for_result(causal_id)
    related_identities = {
        "attempt_id": attempt.attempt_id,
        "candidate_id": candidate.candidate_id,
        "result_id": causal_id,
        "envelope_id": finalized.envelope_id,
        "notification_id": generation,
        "generation_id": generation,
        "next_session_id": reserved,
        "spending_result_id": r2,
    }

    def _spent_stage_count() -> int:
        return sum(
            1 for audit in db.list_authority_policy_v2_result_stage_audits(
                root_task_id=root_id, manager_agent=MANAGER,
            )
            if audit["payload"].get("stage") == "spent"
        )

    def _insert_raw_stage(payload: dict) -> None:
        db._conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
            "VALUES (?,?,?,?,?)",
            (root_id, MANAGER, "authority_policy_v2_result_stage",
             json.dumps(payload), "2026-09-21T00:00:00+00:00"),
        )
        db._conn.commit()

    def _delete_raw_stage(payload: dict) -> None:
        cursor = db._conn.execute(
            "DELETE FROM audit_log WHERE task_id=? AND agent=? AND action=? "
            "AND payload=?",
            (root_id, MANAGER, "authority_policy_v2_result_stage",
             json.dumps(payload)),
        )
        assert cursor.rowcount == 1
        db._conn.commit()

    # A-negative: an exact-related spent-shaped audit whose discriminator is
    # null is NOT unrelatedness -> the read-only replay refuses with the whole
    # spent residue preserved.
    a_payload = {**related_identities, "stage": None}
    _insert_raw_stage(a_payload)
    assert db._conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE task_id=? AND agent=? "
        "AND action=? AND payload=?",
        (root_id, MANAGER, "authority_policy_v2_result_stage",
         json.dumps(a_payload)),
    ).fetchone()[0] == 1
    envelope_before = db.get_authority_policy_v2_continue_envelope(
        finalized.envelope_id
    )
    a_refused = _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    )
    assert a_refused.status == "spend_pending", a_refused
    assert a_refused.reason == "receipt_conflict", a_refused
    assert (
        db.get_authority_policy_v2_continue_envelope(finalized.envelope_id)
        .model_dump()
    ) == envelope_before.model_dump()
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "retired"
    assert _spent_stage_count() == 1
    # Removing the fixture corruption restores the healthy exact replay.
    _delete_raw_stage(a_payload)
    assert _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    ).status == "already_spent_exact"

    # B-negative: FIXTURE CORRUPTION of the retained R2 report body after
    # genuine admission.  The bound report digest no longer matches, so the
    # read-only replay refuses without any write; restoring the exact admitted
    # body restores the healthy exact replay.
    original_decision = db._conn.execute(
        "SELECT decision_json FROM task_results WHERE id=?", (r2,),
    ).fetchone()["decision_json"]
    assert original_decision
    db._conn.execute(
        "UPDATE task_results SET decision_json=? WHERE id=?",
        ('{"action":"delegate","agent":"dev_agent","prompt":"changed"}', r2),
    )
    db._conn.commit()
    b_refused = _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    )
    assert b_refused.status == "spend_pending", b_refused
    assert b_refused.reason == "receipt_conflict", b_refused
    consumed = db.get_authority_policy_v2_continue_envelope(finalized.envelope_id)
    assert consumed.lifecycle_state == "consumed"
    assert consumed.spending_result_id == r2
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "retired"
    assert _spent_stage_count() == 1
    db._conn.execute(
        "UPDATE task_results SET decision_json=? WHERE id=?",
        (original_decision, r2),
    )
    db._conn.commit()
    restored = _spend_call(
        db, root_id=root_id, session_id=session_id, causal_id=causal_id,
        generation=generation, reserved=reserved, r2=r2,
    )
    assert restored.status == "already_spent_exact", restored
    assert restored.report_digest == replay.report_digest

    fixture.release_launch()
    fixture.join_workers()
    return root_id


def test_shipping_real_reserved_invocation_and_atomic_spend(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c1_spend(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_reserved_invocation_and_spend(
    tmp_path, monkeypatch,
):
    """The SAME reserved-invocation + atomic spend venue over a FULL historical
    migrated DB."""
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c1_spend(fixture)
    finally:
        fixture.stop()


# ==========================================================================
# C3d3c2: the reserved invocation's REAL common consumer runs the existing
# spend -> claim -> real normal decision effect -> applied.  The explicit
# storage spend handoff is deliberately replaced by the actual run-step guard.
# ==========================================================================


def _reserved_decision_body(
    binding: dict, task_id: str, action: str, *, job_id: str | None = None,
) -> dict:
    body = _completion_body(binding, task_id)
    if action == "done":
        body["decision"] = {
            "action": "done",
            "summary": "isolated shipping continuation done",
        }
    elif action == "delegate":
        body["decision"] = {
            "action": "delegate", "agent": WORKER,
            "prompt": "isolated shipping continuation delegate",
        }
    else:
        # A genuine ``blocked`` completion report: the reserved continuation
        # result parks the task on its own submitted job (in_progress with
        # ``block_kind=blocked_on_job``), the spec's in-place block branch.
        body["status"] = "blocked"
        body["summary"] = "isolated shipping continuation blocked on job"
        body["waiting_on_job_ids"] = [job_id]
    return body


def _insert_pending_job(fixture: _ShippingFixture, task_id: str) -> str:
    """A real non-terminal job row so a blocked report parks on it."""
    from uuid import uuid4

    from runtime.models import JobInterpreter, JobRecord, JobStatus

    job_id = f"JOB-{uuid4().hex[:12]}"
    fixture.org.db.insert_job(JobRecord(
        id=job_id, task_id=task_id, agent_name=MANAGER,
        title="isolated blocked shipping job", rationale="blocked case",
        script_text="true", interpreter=JobInterpreter.BASH,
        status=JobStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
    return job_id


def _reopen_owned_db(db, *, origin_boot_id: str, expect_envelope_id: str):
    """A genuinely NEW Database over the SAME persisted owned-RuntimeDir file.

    Asserts the reopened object/connection are distinct from the fixture's live
    pair while the persisted path is identical, rebinds the protected
    process/boot context through normal isolated fixture wiring, and proves the
    committed durable state reconstructs from the NEW connection.
    """
    from runtime.infrastructure.database import Database

    old_conn = db._conn
    reopened = Database(db.db_path)
    assert reopened is not db
    assert reopened.db_path == db.db_path
    assert reopened._conn is not old_conn
    assert reopened._conn is not None
    original = db.get_authority_policy_v2_continue_envelope(expect_envelope_id)
    reconstructed = reopened.get_authority_policy_v2_continue_envelope(
        expect_envelope_id,
    )
    assert original is not None and reconstructed is not None
    assert reconstructed.envelope_id == original.envelope_id
    assert reconstructed.spending_result_id == original.spending_result_id
    reopened.bind_authority_policy_v2_process_boot_id(f"restart-{origin_boot_id}")
    return reopened


def _drive_c3d3c2_dispatch(fixture: _ShippingFixture, *, action="done", mode="healthy"):
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    result = fixture.run_cli(fixture.write_payload(body))
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    causal_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import (
        _strict_permission_surface_digest,
        publish_authority_policy_v2_notifications,
    )

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=causal_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id

    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (causal_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=causal_id,
    )
    pub_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=causal_id,
    )
    assert db.settle_authority_policy_v2_continuation_receipt(**pub_kwargs).status == "settled"
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    receipts = publish_authority_policy_v2_notifications(
        fixture.org.orchestrator, fixture.state.queue,
    )
    assert receipts and receipts[0]["status"] in ("published", "publish_returned"), receipts

    reserved = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    assert fixture.captured["session_id"] == reserved

    # Real R2 through the ACTUAL shipping subprocess CLI -> HTTP -> persisted
    # result, while the reserved invocation is still held at the launch.
    reserved_binding = _binding(fixture, root_id, reserved)
    assert reserved_binding is not None and reserved_binding["mode"] == "v2"
    blocked_job_id = (
        _insert_pending_job(fixture, root_id) if action == "blocked" else None
    )
    reserved_body = _reserved_decision_body(
        reserved_binding, root_id, action, job_id=blocked_job_id,
    )
    reserved_payload = fixture.write_payload(
        reserved_body, name=f"completion-reserved-{action}.json",
    )
    reserved_result = fixture.run_cli(reserved_payload)
    assert reserved_result.returncode == 0, reserved_result.stderr
    assert fixture.last_http()["status"] == 200
    r2_row = db.get_task_results(root_id)[-1]
    assert r2_row["session_id"] == reserved
    r2 = r2_row["id"]
    assert r2 != causal_id
    envelope_id = finalized.envelope_id
    assert (
        db.get_authority_policy_v2_continue_envelope(envelope_id).lifecycle_state
        == "active"
    )

    def _stages() -> list[str]:
        return [
            a["payload"].get("stage")
            for a in db.list_authority_policy_v2_result_stage_audits(
                root_task_id=root_id, manager_agent=MANAGER,
            )
        ]

    from runtime.infrastructure.database import Database as _Database
    from tests.test_authority_v2_envelope_spend import _BoundaryFailingConn

    # The REAL spend/claim/ack writers are invoked; only one EXACT SQL/audit
    # boundary is injected on the shared connection, and the fired signal is
    # awaited deterministically (never an elapsed-sleep inference).
    conn_wrapper = None
    real_conn = db._conn
    if mode == "spend_failure":
        conn_wrapper = _BoundaryFailingConn(real_conn, audit_stage="spent")
    elif mode == "ack_failure":
        conn_wrapper = _BoundaryFailingConn(
            real_conn, audit_stage="decision_applied",
        )
    elif mode == "claim_failure":
        conn_wrapper = _BoundaryFailingConn(
            real_conn, audit_stage="decision_claimed",
        )
    if conn_wrapper is not None:
        db._conn = conn_wrapper

    # Observe ACTUAL enqueue calls on the real queue (never a child-row count):
    # installed immediately before the reserved invocation resumes so it counts
    # only that invocation's real effects.
    enqueue_calls: list[tuple] = []
    real_put_nowait = fixture.state.queue.put_nowait

    def _counting_put_nowait(slug, task_id, *, metadata=None):
        enqueue_calls.append((slug, task_id, metadata))
        return real_put_nowait(slug, task_id, metadata=metadata)

    fixture.state.queue.put_nowait = _counting_put_nowait  # type: ignore[assignment]

    # Resume ONLY the reserved invocation: its REAL run_step common consumer
    # performs the existing spend -> claim -> real normal effect -> applied.
    # No explicit storage spend handoff.
    fixture.release_session(reserved)
    state = None
    if mode == "spend_failure":
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and conn_wrapper.fired is None:
            time.sleep(0.05)
        assert conn_wrapper.fired == "audit:spent", conn_wrapper.fired
    elif mode in ("ack_failure", "claim_failure"):
        stage = "decision_applied" if mode == "ack_failure" else "decision_claimed"
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if conn_wrapper.fired == f"audit:{stage}":
                break
            time.sleep(0.05)
        assert conn_wrapper.fired == f"audit:{stage}", conn_wrapper.fired
        state = db.get_authority_policy_v2_continue_envelope(
            envelope_id
        ).decision_state
        assert state == ("claimed" if mode == "ack_failure" else "ready"), state
    else:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            state = db.get_authority_policy_v2_continue_envelope(
                envelope_id
            ).decision_state
            if state in ("applied", "refused"):
                break
            time.sleep(0.05)

    try:
        if mode == "spend_failure":
            # Failed spend yields ZERO consumer entry: E stays active with no
            # spending receipt, no decision event and no real effect.
            active = db.get_authority_policy_v2_continue_envelope(envelope_id)
            assert active.lifecycle_state == "active"
            assert active.spending_result_id is None
            assert active.decision_state is None
            assert _stages().count("decision_claimed") == 0
            assert _stages().count("decision_applied") == 0
            assert db.get_task(root_id).status is not TaskStatus.COMPLETED
            return root_id

        if mode == "claim_failure":
            # The REAL spend committed but the claim-audit boundary failed: the
            # receipt stays discoverably ``ready`` with ZERO consumer entry and
            # no real effect (an exact retry may still claim later).
            assert state == "ready", state
            assert _stages().count("spent") == 1
            assert _stages().count("decision_claimed") == 0
            assert _stages().count("decision_applied") == 0
            assert db.get_task(root_id).status is not TaskStatus.COMPLETED
            return root_id

        if mode == "ack_failure":
            # The consumer effect committed but the acknowledgement failed:
            # the exact receipt stays discoverably ``claimed``.
            assert state == "claimed", state
            stages = _stages()
            assert stages.count("spent") == 1
            assert stages.count("decision_claimed") == 1
            assert stages.count("decision_applied") == 0
            if action == "done":
                assert db.get_task(root_id).status is TaskStatus.COMPLETED
                assert enqueue_calls == [], enqueue_calls
            else:
                children = [dict(row) for row in db._conn.execute(
                    "SELECT * FROM tasks WHERE parent_task_id=?", (root_id,),
                ).fetchall()]
                assert len(children) == 1, children
                child_id = children[0]["id"]
                # The delegate+ack_failure branch: the real normal effect
                # enqueued the child EXACTLY once before the fault.
                assert len(enqueue_calls) == 1, enqueue_calls
                assert enqueue_calls[0][1] == child_id
            enqueues_before_reopen = len(enqueue_calls)
            # A reopen refuses exactly once with the same causal identity and
            # never re-runs the consumer or regresses the committed effect.  The
            # REAL refusal writer runs (the injected boundary only affects the
            # ``decision_applied`` audit, never the interruption audit).
            # ACTUAL reopen: establish deterministic old-invocation/transaction
            # quiescence WITHOUT releasing the held causal/child launches — the
            # tagged reserved invocation has already returned and its queue item
            # reached task_done, and the delegated child (if any) is awaited into
            # its own held launch (session_start/initial heartbeat committed).
            # Then open a genuinely distinct Database over the SAME persisted
            # owned-RuntimeDir database and run the REAL rebound common consumer
            # there.  No active fixture worker races replacement/close and no
            # elapsed sleep stands in for the barrier.
            db._conn = real_conn
            fixture.await_reserved_invocation_done()
            if action == "delegate":
                # Quiesce the REAL delegated child's asynchronous startup writes
                # before the byte-identity replay comparison.
                fixture.await_delegated_launch(child_id)
            reopened = _reopen_owned_db(
                db, origin_boot_id=attempt.origin_boot_id,
                expect_envelope_id=envelope_id,
            )
            rebound = types.SimpleNamespace(_db=reopened)
            r2_dict = dict(reopened._conn.execute(
                "SELECT * FROM task_results WHERE id=?", (r2,)
            ).fetchone())
            r2_report = completion_report_from_result_row(
                root_id, r2_dict, fallback_agent=MANAGER,
            )
            from unittest.mock import patch as _patch
            from runtime.orchestrator import run_step as _run_step

            entries: list[dict] = []
            with _patch.object(
                _run_step, "_consume_completion_report_body",
                lambda *a, **kw: entries.append(kw),
            ):
                _run_step._consume_completion_report(
                    rebound, root_id, r2_report, result_row_id=r2,
                )
            # ZERO consumer entry: the reopened path performs only the audited
            # interruption refusal, using the REAL refusal writer.
            assert entries == []
            refreshed = reopened.get_authority_policy_v2_continue_envelope(
                envelope_id
            )
            assert refreshed.decision_state == "refused"
            reopened_stages = [
                a["payload"].get("stage")
                for a in reopened.list_authority_policy_v2_result_stage_audits(
                    root_task_id=root_id, manager_agent=MANAGER,
                )
            ]
            assert reopened_stages.count("decision_dispatch_interrupted") == 1
            assert reopened_stages.count("decision_applied") == 0
            if action == "done":
                assert reopened.get_task(root_id).status is TaskStatus.COMPLETED
            else:
                children = [dict(row) for row in reopened._conn.execute(
                    "SELECT * FROM tasks WHERE parent_task_id=?", (root_id,),
                ).fetchall()]
                assert len(children) == 1, children
            # Exact read-only replay on the SAME reopened connection: no second
            # consumer entry and byte-identical residue.
            before_replay = "\n".join(reopened._conn.iterdump())
            with _patch.object(
                _run_step, "_consume_completion_report_body",
                lambda *a, **kw: entries.append(kw),
            ):
                _run_step._consume_completion_report(
                    rebound, root_id, r2_report, result_row_id=r2,
                )
            assert entries == []
            assert "\n".join(reopened._conn.iterdump()) == before_replay
            # No repeated real enqueue (or body entry) across reopen/refusal/
            # replay; the committed child/effect is preserved.
            assert len(enqueue_calls) == enqueues_before_reopen, enqueue_calls
            reopened.close()
            return root_id

        # Healthy: exactly one spend, claim, real normal decision effect and
        # acknowledgement, and no interruption.
        assert state == "applied", state
        stages = _stages()
        assert stages.count("spent") == 1
        assert stages.count("decision_claimed") == 1
        assert stages.count("decision_applied") == 1
        assert stages.count("decision_dispatch_interrupted") == 0
        if action == "done":
            assert db.get_task(root_id).status is TaskStatus.COMPLETED
        elif action == "blocked":
            # The admitted reserved result's REAL normal effect is the spec's
            # in-place block branch: the task parks on its own submitted job
            # (in_progress + blocked_on_job) with no child/enqueue.
            task = db.get_task(root_id)
            assert task.status is TaskStatus.IN_PROGRESS
            assert task.block_kind == BlockKind.BLOCKED_ON_JOB
            assert json.loads(task.blocked_on_job_ids or "[]") == [blocked_job_id]
            assert db._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE parent_task_id=?", (root_id,),
            ).fetchone()[0] == 0
        else:
            children = [dict(row) for row in db._conn.execute(
                "SELECT * FROM tasks WHERE parent_task_id=?", (root_id,),
            ).fetchall()]
            assert len(children) == 1, children
            assert children[0]["assigned_agent"] == WORKER
            child_id = children[0]["id"]

        # Reopen/duplicate sees the terminal exact receipt: never a second
        # consumer, never a second child/enqueue, no new audit.
        before = db._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE parent_task_id=?", (root_id,),
        ).fetchone()[0]
        # ACTUAL reopen for the healthy exact-R2 replay too: quiesce the old
        # invocations/transactions, then replay the terminal receipt through a
        # genuinely distinct Database over the SAME persisted file.
        db._conn = real_conn
        fixture.await_reserved_invocation_done()
        if action == "delegate":
            # Quiesce the REAL delegated child's asynchronous startup writes
            # before the byte-identity replay comparison.
            fixture.await_delegated_launch(child_id)
        reopened = _reopen_owned_db(
            db, origin_boot_id=attempt.origin_boot_id,
            expect_envelope_id=envelope_id,
        )
        r2_dict = dict(reopened._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (r2,)
        ).fetchone())
        r2_report = completion_report_from_result_row(
            root_id, r2_dict, fallback_agent=MANAGER,
        )
        before_replay = "\n".join(reopened._conn.iterdump())
        from runtime.orchestrator.run_step import _v2_decision_dispatch_gate

        gate = _v2_decision_dispatch_gate(
            types.SimpleNamespace(_db=reopened), root_id, r2_report, r2, MANAGER,
        )
        assert gate.kind == "skip", gate
        assert "\n".join(reopened._conn.iterdump()) == before_replay
        assert reopened._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE parent_task_id=?", (root_id,),
        ).fetchone()[0] == before
        reopened_stages = [
            a["payload"].get("stage")
            for a in reopened.list_authority_policy_v2_result_stage_audits(
                root_task_id=root_id, manager_agent=MANAGER,
            )
        ]
        assert reopened_stages.count("decision_applied") == 1
        reopened.close()
        return root_id
    finally:
        if conn_wrapper is not None:
            db._conn = real_conn
        fixture.release_launch()
        fixture.join_workers()


def test_shipping_real_reserved_invocation_common_consumer_done(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done")
    finally:
        fixture.stop()


def test_shipping_real_reserved_invocation_common_consumer_delegate(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="delegate")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_done(tmp_path, monkeypatch):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done")
    finally:
        fixture.stop()


def test_shipping_real_common_consumer_spend_failure_zero_entry(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="spend_failure")
    finally:
        fixture.stop()


def test_shipping_real_common_consumer_ack_failure_then_reopen(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="ack_failure")
    finally:
        fixture.stop()


def test_shipping_real_common_consumer_delegate_ack_failure_then_reopen(
    tmp_path, monkeypatch,
):
    """Delegate + failed acknowledgement, fresh venue, with reopen refusal.

    The real spend/claim -> real delegate child + observed enqueue -> real
    ack-audit fault -> ``claimed`` -> quiescence -> distinct Database over the
    SAME persisted file -> real interruption refusal -> exact read-only replay,
    with the committed child preserved and the actual enqueue/body-entry
    counters unchanged across reopen/refusal/replay.
    """
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="delegate", mode="ack_failure")
    finally:
        fixture.stop()


def test_shipping_real_common_consumer_claim_failure_zero_entry(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="claim_failure")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_delegate(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="delegate")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_spend_failure(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="spend_failure")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_claim_failure(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="claim_failure")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_ack_failure_then_reopen(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="done", mode="ack_failure")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_delegate_ack_failure_then_reopen(
    tmp_path, monkeypatch,
):
    """Delegate + failed acknowledgement over the full historical-migrated DB."""
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="delegate", mode="ack_failure")
    finally:
        fixture.stop()


def test_shipping_real_reserved_invocation_common_consumer_blocked_result(
    tmp_path, monkeypatch,
):
    """An admitted reserved result whose report is genuinely ``blocked`` runs
    the REAL common consumer exactly once and parks the task on its own job."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2)
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="blocked")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_common_consumer_blocked_result(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
    )
    fixture.start()
    try:
        _drive_c3d3c2_dispatch(fixture, action="blocked")
    finally:
        fixture.stop()


# --------------------------------------------------------------------------
# Part A (THR-229 C3d4a): real later lifecycle after terminal generation A
# --------------------------------------------------------------------------


def _stage_generation_a(fixture: _ShippingFixture, *, action: str = "delegate") -> dict:
    """Drive a REAL v2 generation A to its admitted state (causal launch held).

    The same real staging calls as ``_drive_c3d3c2_dispatch`` -- real isolated
    API pair activation, real enqueue, real subprocess CLI/HTTP admission, the
    real Database stage writers, real publication and real generation
    admission -- but this helper does NOT release the causal launch so a
    later-lifecycle driver can continue the venue.
    """
    fixture.activate_v2_pair()
    fixture.install_launch_hold()
    root_id = fixture.create_and_enqueue_root()
    captured = fixture.wait_for_launch()
    session_id = captured["session_id"]
    binding = _binding(fixture, root_id, session_id)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    result = fixture.run_cli(fixture.write_payload(body))
    assert result.returncode == 0, result.stderr
    assert fixture.last_http()["status"] == 200

    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    causal_id = results[0]["id"]
    db = fixture.org.db

    from runtime.orchestrator.authority import (
        _strict_permission_surface_digest,
        publish_authority_policy_v2_notifications,
    )

    db.bind_authority_policy_v2_permission_surface_reader(
        lambda agent: _strict_permission_surface_digest(
            fixture.org.orchestrator, agent,
        )
    )
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=causal_id, origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id

    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    result_row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (causal_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        root_id, dict(result_row), fallback_agent=MANAGER,
    )
    fixture.org.orchestrator._log_step_result(
        root_id, types.SimpleNamespace(session_id=session_id), report,
        result_row_id=causal_id,
    )
    pub_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=causal_id,
    )
    assert db.settle_authority_policy_v2_continuation_receipt(**pub_kwargs).status == "settled"
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    receipts = publish_authority_policy_v2_notifications(
        fixture.org.orchestrator, fixture.state.queue,
    )
    assert receipts and receipts[0]["status"] in ("published", "publish_returned"), receipts

    reserved = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    assert fixture.captured["session_id"] == reserved

    reserved_binding = _binding(fixture, root_id, reserved)
    assert reserved_binding is not None and reserved_binding["mode"] == "v2"
    reserved_body = _reserved_decision_body(reserved_binding, root_id, action)
    reserved_payload = fixture.write_payload(
        reserved_body, name=f"completion-reserved-{action}.json",
    )
    reserved_result = fixture.run_cli(reserved_payload)
    assert reserved_result.returncode == 0, reserved_result.stderr
    assert fixture.last_http()["status"] == 200
    r2_row = db.get_task_results(root_id)[-1]
    assert r2_row["session_id"] == reserved
    assert r2_row["id"] != causal_id
    return {
        "root_id": root_id, "session_id": session_id, "causal_id": causal_id,
        "attempt": attempt, "generation": generation, "reserved": reserved,
        "envelope_id": finalized.envelope_id, "r2": r2_row["id"],
    }


def _apply_generation_a_delegate(fixture: _ShippingFixture, staged: dict) -> str:
    """Release ONLY the reserved generation-A invocation and return the child.

    Its REAL ``run_step`` consumes the persisted R2 through the real common
    consumer, spends/claims/applies the decision and performs the delegate
    effect: a real child row plus a real enqueue."""
    db = fixture.org.db
    fixture.release_session(staged["reserved"])
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        envelope = db.get_authority_policy_v2_continue_envelope(staged["envelope_id"])
        if envelope.decision_state == "applied":
            break
        time.sleep(0.05)
    envelope = db.get_authority_policy_v2_continue_envelope(staged["envelope_id"])
    assert envelope.decision_state == "applied", envelope
    # Generation A is terminal: the spend retired the old admitted pointer, so a
    # later legitimate enqueue must not be blanket-blocked.
    dispatch = db.get_authority_policy_v2_root_dispatch(staged["root_id"])
    assert dispatch.state == "retired", dispatch
    children = [dict(row) for row in db._conn.execute(
        "SELECT * FROM tasks WHERE parent_task_id=?", (staged["root_id"],),
    ).fetchall()]
    assert len(children) == 1, children
    assert children[0]["assigned_agent"] == WORKER
    return children[0]["id"]


def _count_inner_enqueues(fixture: _ShippingFixture) -> list[tuple]:
    """Count EVERY real enqueue exactly once at the inner asyncio queue.

    Both ``TaskQueue.enqueue`` and ``TaskQueue.put_nowait`` funnel through the
    inner ``asyncio.Queue.put_nowait``, so wrapping that one hop counts the
    actual producer/queue calls without double counting or recursion."""
    calls: list[tuple] = []
    inner = fixture.state.queue._queue
    real_put = inner.put_nowait

    def _counting(item):
        calls.append(tuple(item))
        return real_put(item)

    fixture.monkeypatch.setattr(inner, "put_nowait", _counting)
    return calls


def _snapshot_generation_a(db, staged: dict) -> dict:
    """Byte-for-byte snapshot of the RETAINED generation-A v2 evidence.

    Called with the SAME ``staged`` identity twice: once BEFORE the later R3
    root result is persisted and once AFTER the real later root effect and
    every gate-level replay.  ``after == before`` therefore proves the terminal
    generation-A evidence was not rewritten by the later ordinary lifecycle.

    Every query is scoped to the exact generation-A identity so the ONLY
    excluded later rows are R3's own unrelated later-context admission:

    * ``task_results`` is scoped to the causal R1 and the reserved R2 spending
      result; the later R3 row is excluded.
    * ``authority_policy_v2_attempts`` is scoped to the result ids R1/R2 (the
      causal and reserved generation-A attempts); the R3 callback's own new
      attempt is excluded.
    * ``authority_policy_v2_result_stage`` audit rows are scoped to a payload
      ``result_id`` of R1/R2 (the causal/reserved generation-A stages); R3's
      own later-context ``admitted`` stage row is excluded.  The scoped rows
      are compared in full (all ``audit_log`` columns).
    * candidate/pin/evaluation/candidate-audit/envelope/notification/dispatch
      are unique to generation A and are compared in full.

    ``dict(row)``-style values are captured as ordered column tuples so any
    column change is a byte-for-byte mismatch.
    """
    root_id = staged["root_id"]
    causal_id = staged["causal_id"]
    r2_id = staged["r2"]
    envelope_id = staged["envelope_id"]
    generation = staged["generation"]
    envelope_row = db._conn.execute(
        "SELECT candidate_id FROM authority_policy_v2_continue_envelopes "
        "WHERE envelope_id=?",
        (envelope_id,),
    ).fetchone()
    assert envelope_row is not None
    candidate_id = envelope_row["candidate_id"]
    generation_result_ids = (causal_id, r2_id)

    def _rows(sql: str, params: tuple = ()) -> tuple:
        return tuple(tuple(row) for row in db._conn.execute(sql, params).fetchall())

    stage_rows = []
    excluded_result_ids = set()
    for row in db._conn.execute(
        "SELECT * FROM audit_log WHERE action=? AND task_id=? AND agent=? ORDER BY id",
        (AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION, root_id, MANAGER),
    ).fetchall():
        payload = json.loads(row["payload"])
        result_id = payload.get("result_id")
        if result_id in generation_result_ids:
            stage_rows.append(tuple(row))
        else:
            # Only the later R3 result's own later-context ``admitted`` stage
            # may fall outside the causal/reserved generation-A result ids.
            assert result_id is not None
            excluded_result_ids.add(result_id)
    assert not (excluded_result_ids & set(generation_result_ids))

    return {
        "root_dispatch": _rows(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            (root_id,),
        ),
        "envelopes": _rows(
            "SELECT * FROM authority_policy_v2_continue_envelopes WHERE envelope_id=?",
            (envelope_id,),
        ),
        "notifications": _rows(
            "SELECT * FROM authority_policy_v2_recovery_notifications "
            "WHERE notification_id=?",
            (generation,),
        ),
        "attempts": _rows(
            "SELECT * FROM authority_policy_v2_attempts "
            "WHERE result_id IN (?,?) ORDER BY result_id",
            (causal_id, r2_id),
        ),
        "candidates": _rows(
            "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
            (candidate_id,),
        ),
        "pins": _rows(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate_id,),
        ),
        "evaluations": _rows(
            "SELECT * FROM authority_policy_v2_evaluations WHERE candidate_id=?",
            (candidate_id,),
        ),
        "candidate_audit": _rows(
            "SELECT * FROM authority_policy_v2_candidate_audit "
            "WHERE candidate_id=? ORDER BY id",
            (candidate_id,),
        ),
        "result_stage_audits": tuple(stage_rows),
        "causal_result": _rows(
            "SELECT * FROM task_results WHERE id=?", (causal_id,),
        ),
        "reserved_result": _rows(
            "SELECT * FROM task_results WHERE id=?", (r2_id,),
        ),
    }


def _drive_later_lifecycle(fixture: _ShippingFixture) -> dict:
    """Part A: a legitimate normal lifecycle AFTER terminal generation A.

    Generation A (delegate) applies through the real spend/claim/apply path,
    producing a real v1 delegated child.  The child then runs the full real
    supported lifecycle -- producer -> queue -> ``Dispatcher.run_step`` ->
    ``_run_agent`` session publication -> real subprocess CLI/HTTP completion
    -> persisted result -> real common consumer -> real normal effect (the
    parent-wake enqueue).  The root is then relaunched through the ordinary
    producer path, proving a RETIRED generation-A pointer no longer blocks
    legitimate later work.  No owner/session/task status is patched by hand and
    the normal effect is not substituted.
    """
    staged = _stage_generation_a(fixture, action="delegate")
    db = fixture.org.db
    child_id = _apply_generation_a_delegate(fixture, staged)

    # The real delegated child is launched through the ordinary queue/run_step
    # path and `_run_agent` publishes its durable invocation identity.
    child_launch = fixture.wait_for_launch_for(child_id)
    child_session = child_launch["session_id"]
    published = db.get_task(child_id)
    assert published.current_session_id == child_session
    assert fixture.org.sessions.get_active(child_id, WORKER) == child_session
    # A legitimately later v1 invocation carries NO v2 launch binding.
    assert _binding(fixture, child_id, child_session) is None

    calls = _count_inner_enqueues(fixture)
    child_body = {
        "task_id": child_id, "session_id": child_session, "agent": WORKER,
        "status": "completed", "confidence": 90,
        "summary": "later child completion", "decision": {"action": "done"},
    }
    ran = fixture.run_cli(fixture.write_payload(child_body, name="child-later.json"))
    assert ran.returncode == 0, ran.stderr
    assert fixture.last_http()["status"] == 200
    child_results = db.get_task_results(child_id)
    assert len(child_results) == 1, child_results
    child_result_id = child_results[0]["id"]

    # A CHANGED report for the same invocation adds ZERO durable/consumer
    # effect: the first admitted result remains the only one, no second child
    # is created and no enqueue happens (the route is idempotent per session).
    changed_body = dict(child_body)
    changed_body["summary"] = "changed later completion body"
    changed = fixture.run_cli(
        fixture.write_payload(changed_body, name="child-later-changed.json")
    )
    assert fixture.last_http()["status"] == 200
    assert len(db.get_task_results(child_id)) == 1
    assert [c for c in calls if c[1] == staged["root_id"]] == []

    # Release ONLY the child invocation: its REAL run_step consumes the
    # persisted later result and performs the prescribed normal effect once.
    before_launches = fixture.launch_count()
    fixture.release_session(child_session)
    # Deterministic quiescence (no elapsed sleep): the child's normal effect
    # enqueues the parent wake and the real producer then records the root's
    # held relaunch, so waiting on that recorded launch (condition barrier)
    # proves the child run_step committed its parent-wake enqueue.
    parent_launch = fixture.wait_for_launch_for(
        staged["root_id"], after=before_launches,
    )
    assert db.get_task(child_id).status is TaskStatus.COMPLETED
    wake = [c for c in calls if c[1] == staged["root_id"]]
    assert len(wake) == 1, calls
    # The exact consumed later result is retained; the retired pointer is
    # unchanged and no v2 evidence row was rewritten.
    assert db.get_authority_policy_v2_root_dispatch(staged["root_id"]).state == "retired"

    # The real producer re-launched the root through the ordinary path (a new
    # session published by `_run_agent`), not a manually patched owner.
    assert parent_launch["session_id"] != staged["session_id"]
    assert parent_launch["session_id"] != staged["reserved"]
    assert db.get_task(staged["root_id"]).current_session_id == parent_launch["session_id"]

    # ── Actual ROOT terminal-v2 ``later`` lifecycle (TASK-8575 Part B) ──
    # Everything above completes a v1 CHILD (whose gate is ``no_v2``).  The
    # relaunched ROOT is the real terminal-v2 lineage context: generation A is
    # applied and its pointer retired, so this NEW root invocation is the
    # ``later`` continuation context guarded by the report-binding check.
    root_id = staged["root_id"]
    root_session = parent_launch["session_id"]
    # The supported launch published the session; nothing was patched by hand.
    assert db.get_task(root_id).current_session_id == root_session
    assert db.get_task(root_id).assigned_agent == MANAGER
    assert fixture.org.sessions.get_active(root_id, MANAGER) == root_session
    # A legitimately later ordinary invocation carries the ACTIVE v2 policy's
    # launch binding -- the dual-text policy family, NOT a THR-229 continuation
    # generation binding.  It is classified by the terminal-v2 ``later`` gate.
    later_binding = _binding(fixture, root_id, root_session)
    assert later_binding is not None, later_binding
    assert later_binding.get("mode") == "v2", later_binding.get("mode")

    # ── Deterministic counts for the ONE real normal done effect ──
    # The root's terminal-v2 ``later`` branch runs the EXISTING normal body
    # once.  Wrap (CALL THROUGH, never replace) the two real run_step-module
    # functions so the exact-once semantics are measured on the actual code
    # path: the normal-body entry and the successful ``_complete`` done
    # transition.  ``root_body_returned`` is the deterministic quiescence
    # signal for the final snapshot (the untagged ordinary root re-launch has
    # no fixture event).
    from runtime.orchestrator import run_step as _run_step

    root_body_calls: list[str] = []
    root_done_effects: list[str] = []
    root_body_returned = threading.Event()
    _real_body = _run_step._consume_completion_report_body
    _real_complete = _run_step._complete

    def _counting_body(orch, task_id, report, **kwargs):
        root_body_calls.append(task_id)
        try:
            return _real_body(orch, task_id, report, **kwargs)
        finally:
            if task_id == root_id:
                root_body_returned.set()

    def _counting_complete(orch, task_id, **kwargs):
        completed = _real_complete(orch, task_id, **kwargs)
        if completed:
            root_done_effects.append(task_id)
        return completed

    fixture.monkeypatch.setattr(
        _run_step, "_consume_completion_report_body", _counting_body,
    )
    fixture.monkeypatch.setattr(_run_step, "_complete", _counting_complete)

    # Snapshot the retained generation-A evidence BEFORE the later R3 root
    # result is persisted by the CLI below.
    generation_a_before = _snapshot_generation_a(db, staged)
    assert generation_a_before["root_dispatch"]
    assert generation_a_before["envelopes"]
    assert generation_a_before["notifications"]
    assert len(generation_a_before["attempts"]) == 2
    assert generation_a_before["candidates"]
    assert generation_a_before["pins"]
    assert generation_a_before["evaluations"]
    assert generation_a_before["candidate_audit"]
    assert generation_a_before["result_stage_audits"]
    assert generation_a_before["causal_result"]
    assert generation_a_before["reserved_result"]
    # No normal-body entry or done effect yet for the root after generation A
    # was applied (its reserved delegate body ran BEFORE these counters).
    assert root_body_calls.count(root_id) == 0
    assert root_done_effects.count(root_id) == 0

    root_enqueues_before = len([c for c in calls if c[1] == root_id])
    root_body = _reserved_decision_body(later_binding, root_id, "done")
    root_run = fixture.run_cli(
        fixture.write_payload(root_body, name="root-later.json")
    )
    assert root_run.returncode == 0, root_run.stderr
    assert fixture.last_http()["status"] == 200
    root_rows = db.get_task_results(root_id)
    assert root_rows[-1]["session_id"] == root_session
    r3 = root_rows[-1]
    assert r3["id"] > staged["r2"]

    # The REAL root gate genuinely classifies the retained R3 as ``later``
    # BEFORE any effect -- the terminal-v2 ROOT context, not the child's no_v2.
    ctx = db.authority_policy_v2_completion_dispatch_context(
        root_task_id=root_id, result_row_id=r3["id"],
    )
    assert ctx.kind == "later", ctx

    # The ONLY generation-A-vs-later exclusion in the snapshot is R3's own
    # later-context admission: exactly one ``authority_policy_v2_result_stage``
    # ``admitted`` row for the R3 result (plus R3's own attempt/result rows,
    # also excluded by exact result-id scoping).
    r3_stage_audits = [
        a for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
        if a["payload"].get("result_id") == r3["id"]
    ]
    assert len(r3_stage_audits) == 1, r3_stage_audits
    assert r3_stage_audits[0]["payload"]["stage"] == "admitted"

    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    genuine_report = completion_report_from_result_row(
        root_id, dict(r3), fallback_agent=MANAGER,
    )
    changed_row = dict(r3)
    changed_row["output_summary"] = "changed later root body"
    changed_report = completion_report_from_result_row(
        root_id, changed_row, fallback_agent=MANAGER,
    )
    before_status = db.get_task(root_id).status
    # Common-consumer report-binding check (genuine persisted R3 + CHANGED
    # supplied report) refuses at the REAL gate with ZERO normal-body effect.
    _run_step._consume_completion_report(
        fixture.org.orchestrator, root_id, changed_report, result_row_id=r3["id"],
    )
    assert db.get_task(root_id).status is before_status is TaskStatus.IN_PROGRESS
    assert db.get_task(root_id).current_session_id == root_session
    assert len(db.get_task_results(root_id)) == len(root_rows)
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
    # The changed-supplied-report refusal never entered the normal body and
    # never performed a done effect.
    assert root_body_calls.count(root_id) == 0
    assert root_done_effects.count(root_id) == 0

    # HTTP replay of the SAME later session while the invocation is still held
    # is suppressed at the ROUTE's own session/idempotency seam -- distinct
    # from the common-consumer report-binding check above -- with no extra
    # result/effect.
    http_replay = fixture.run_cli(
        fixture.write_payload(root_body, name="root-later-replay.json")
    )
    assert len(db.get_task_results(root_id)) == len(root_rows)
    assert db.get_task(root_id).status is TaskStatus.IN_PROGRESS
    assert fixture.last_http()["status"] in (200, 409)
    assert http_replay.returncode == 0 or fixture.last_http()["status"] == 409
    # HTTP idempotency is a route-level suppression: no enqueue, no normal
    # body entry and no done effect.
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
    assert root_body_calls.count(root_id) == 0
    assert root_done_effects.count(root_id) == 0

    # Actual-ROOT invalid provenance at the REAL route seam: a never-spawned
    # session (unbound), a wrong agent, and a wrong/other session are all
    # refused with ZERO result/enqueue effect.  This is root-later negative
    # proof, not an invalid-child-only HTTP proof.
    for bad_session, bad_agent in (
        ("sess-never-spawned", MANAGER),
        (root_session, WORKER),
        ("sess-other-valid-looking", MANAGER),
    ):
        bad = fixture.run_cli(fixture.write_payload(
            {
                "task_id": root_id, "session_id": bad_session, "agent": bad_agent,
                "status": "completed", "confidence": 90,
                "summary": "invalid root later completion",
                "decision": {"action": "done", "summary": "must not apply"},
            },
            name=f"root-later-bad-{bad_agent}-{bad_session}.json",
        ))
        assert bad.returncode != 0
        assert fixture.last_http()["status"] in (400, 409)
        assert len(db.get_task_results(root_id)) == len(root_rows)
        assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
        # Each invalid-provenance refusal adds no body entry/done effect.
        assert root_body_calls.count(root_id) == 0
        assert root_done_effects.count(root_id) == 0
    assert db.get_task(root_id).status is TaskStatus.IN_PROGRESS

    # Actual-ROOT stale/unbound identities at the REAL classifier: the causal
    # R1 and the receipt R2 are never ordinary permission, and an absent row is
    # ``foreign``.  Consuming with a stale receipt identity adds no effect.
    assert db.authority_policy_v2_completion_dispatch_context(
        root_task_id=root_id, result_row_id=staged["causal_id"],
    ).kind == "causal"
    assert db.authority_policy_v2_completion_dispatch_context(
        root_task_id=root_id, result_row_id=staged["r2"],
    ).kind == "receipt"
    assert db.authority_policy_v2_completion_dispatch_context(
        root_task_id=root_id, result_row_id=10**9,
    ).kind == "foreign"
    _run_step._consume_completion_report(
        fixture.org.orchestrator, root_id, genuine_report,
        result_row_id=staged["r2"],
    )
    assert db.get_task(root_id).status is TaskStatus.IN_PROGRESS
    assert len(db.get_task_results(root_id)) == len(root_rows)
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
    # The stale-identity consumption skipped at the real gate: no enqueue, no
    # normal body entry, no done effect.
    assert root_body_calls.count(root_id) == 0
    assert root_done_effects.count(root_id) == 0

    # Release ONLY the root invocation: the REAL run_step consumes R3 through
    # the terminal-v2 ``later`` branch and runs the EXISTING normal body (done)
    # exactly once.
    fixture.release_session(root_session)
    # The root's later invocation is an ORDINARY (untagged) re-launch, so no
    # fixture tagged/reserved event covers it.  The deterministic quiescence
    # signal is the call-through wrapper's Event, set when the root's real
    # normal body returns; the final snapshot is taken after that proven
    # return (never after an elapsed sleep).
    assert root_body_returned.wait(timeout=_JOIN_SECONDS), (
        "root later normal body never returned"
    )
    assert db.get_task(root_id).status is TaskStatus.COMPLETED
    # Exactly one normal body entry and exactly one actual done effect for the
    # root across the whole later lifecycle.
    assert root_body_calls.count(root_id) == 1
    assert root_done_effects.count(root_id) == 1
    # No extra enqueue beyond the child's single parent wake.
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before

    # A post-terminal HTTP replay is refused by the route's task-active gate
    # with no new result/effect.
    after_http = fixture.run_cli(
        fixture.write_payload(root_body, name="root-later-post-terminal.json")
    )
    assert len(db.get_task_results(root_id)) == len(root_rows)
    assert db.get_task(root_id).status is TaskStatus.COMPLETED
    assert after_http.returncode != 0
    assert fixture.last_http()["status"] == 409
    # Post-terminal replay adds no enqueue, body entry or done effect.
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
    assert root_body_calls.count(root_id) == 1
    assert root_done_effects.count(root_id) == 1

    # A common-consumer replay of the SAME genuine report after terminal state
    # adds no effect (the lineage is no longer ordinary-capable).
    _run_step._consume_completion_report(
        fixture.org.orchestrator, root_id, genuine_report, result_row_id=r3["id"],
    )
    assert db.get_task(root_id).status is TaskStatus.COMPLETED
    assert len(db.get_task_results(root_id)) == len(root_rows)
    assert len([c for c in calls if c[1] == root_id]) == root_enqueues_before
    # The duplicate common-consumer replay skipped: still exactly one body
    # entry and one done effect.
    assert root_body_calls.count(root_id) == 1
    assert root_done_effects.count(root_id) == 1

    # Old generation-A evidence is unchanged: the retained generation-A
    # rows/audits captured BEFORE the later R3 result are byte-for-byte
    # identical AFTER the real later root effect and every replay above.
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "retired"
    envelope = db.get_authority_policy_v2_continue_envelope(staged["envelope_id"])
    assert envelope.decision_state == "applied"
    generation_a_after = _snapshot_generation_a(db, staged)
    assert generation_a_after == generation_a_before

    return {
        "root_id": root_id, "child_id": child_id,
        "child_result_id": child_result_id,
        "root_result_id": r3["id"],
        "parent_session": root_session,
    }


def _drive_later_lifecycle_invalid(fixture: _ShippingFixture) -> None:
    """Invalid later provenance (wrong owner/session) has ZERO effects.

    After terminal generation A and the real delegated child launch, a
    completion claiming a session the daemon never spawned is refused and
    leaves the child, its results and every enqueue counter untouched.
    """
    staged = _stage_generation_a(fixture, action="delegate")
    db = fixture.org.db
    child_id = _apply_generation_a_delegate(fixture, staged)
    child_launch = fixture.wait_for_launch_for(child_id)
    assert child_launch["session_id"]
    calls = _count_inner_enqueues(fixture)

    before_results = len(db.get_task_results(child_id))
    bad_body = {
        "task_id": child_id, "session_id": "sess-never-spawned",
        "agent": WORKER, "status": "completed", "confidence": 90,
        "summary": "invalid later completion", "decision": {"action": "done"},
    }
    refused = fixture.run_cli(fixture.write_payload(bad_body, name="child-bad.json"))
    assert refused.returncode != 0
    assert fixture.last_http()["status"] == 409
    # ZERO consumer/task/child/enqueue effects.
    assert len(db.get_task_results(child_id)) == before_results == 0
    assert db.get_task(child_id).status is TaskStatus.IN_PROGRESS
    assert [c for c in calls if c[1] == staged["root_id"]] == []


def test_shipping_real_later_lifecycle_after_terminal_generation(tmp_path, monkeypatch):
    """Fresh venue: real post-terminal later lifecycle (Part A)."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=3)
    fixture.start()
    try:
        result = _drive_later_lifecycle(fixture)
        assert result["child_result_id"] > 0
        assert result["root_result_id"] > 0
        assert result["parent_session"]
    finally:
        fixture.stop()


def test_shipping_historically_migrated_later_lifecycle(tmp_path, monkeypatch):
    """Full historical-migrated venue: same real later lifecycle (Part A)."""
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=3,
    )
    fixture.start()
    try:
        result = _drive_later_lifecycle(fixture)
        assert result["child_result_id"] > 0
        assert result["root_result_id"] > 0
        assert result["parent_session"]
    finally:
        fixture.stop()


def test_shipping_real_later_lifecycle_invalid_has_zero_effects(tmp_path, monkeypatch):
    """Invalid later provenance (unbound/wrong-owner session) is a no-op."""
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=3)
    fixture.start()
    try:
        _drive_later_lifecycle_invalid(fixture)
    finally:
        fixture.stop()


# ══════════════════════════════════════════════════════════════════════════
# TASK-8698 — C3d4b genuine shipping RESTART (lost queue) and C4 actual
# startup-caller fault/refusal cases over BOTH fresh and full
# historical-migrated owned venues.
#
# Reuses the TASK-8663 genuine completion-recovery claim (distinct runtime
# session + separate provider resume id + ACTUAL subprocess CLI -> HTTP ->
# persisted accepted R/Q), labels the still-dark pre-final staging through the
# REAL public writers, then establishes a COMMITTED publication whose tagged
# in-memory queue item is deliberately LOST before admission.  A test-only
# lifecycle barrier refuses dequeue; the owner is then detached and a DISTINCT
# Database + real OrgState is opened over the SAME persisted file with a new
# server-owned boot.  The ACTUAL `_sweep_on_startup` and
# `_publish_v2_generations_on_startup` run in production order, then the real
# TaskQueue/Dispatcher/Orchestrator/run_step/_run_agent path admits the
# generation with ONLY the external provider launch doubled.
# ══════════════════════════════════════════════════════════════════════════


def _restart_durable_snapshot(db, root_id: str, generation: str) -> str:
    """Byte-stable projection of the v2 causal rows for before/after compare."""
    def _rows(sql, params=()):
        return [dict(r) for r in db._conn.execute(sql, params).fetchall()]

    stages = [
        {"stage": a["payload"].get("stage"), "id": a["id"]}
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    audits = sorted(
        (a["id"], a["action"]) for a in db.get_audit_logs(root_id)
    )
    return json.dumps(
        {
            "receipts": _rows(
                "SELECT * FROM task_completion_recoveries "
                "WHERE task_id=? AND agent=?", (root_id, MANAGER)),
            "notifications": _rows(
                "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE root_task_id=?", (root_id,)),
            "dispatch": _rows(
                "SELECT * FROM authority_policy_v2_root_dispatch "
                "WHERE root_task_id=?", (root_id,)),
            "envelopes": _rows(
                "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE root_task_id=?", (root_id,)),
            "candidates": _rows(
                "SELECT * FROM authority_policy_v2_candidates "
                "WHERE root_task_id=?", (root_id,)),
            "evaluations": _rows(
                "SELECT * FROM authority_policy_v2_evaluations"),
            "tasks": _rows(
                "SELECT id, status, assigned_agent, current_session_id, "
                "orchestration_step_count, block_kind FROM tasks WHERE id=?",
                (root_id,)),
            "stages": stages,
            "audits": audits,
        },
        sort_keys=True, default=str,
    )


def _restart_genuine_committed_publication(fixture: _ShippingFixture) -> dict:
    """C1/C2 genuine recovery through a COMMITTED publication, queue item lost.

    Returns the exact identities plus the separately counted raw/accepted queue
    calls observed on this (old) owner.
    """
    fixture.activate_v2_pair()
    provider_resume_id = "provider-resume-restart"
    fixture.install_launch_hold(provider_session_id=provider_resume_id)
    root_id = fixture.create_and_enqueue_root()
    origin = fixture.wait_for_launch()
    origin_session = origin["session_id"]
    assert origin_session

    queue = fixture.state.queue
    raw_puts: list[dict] = []
    tagged_puts: list[dict] = []
    original_put = queue.put_nowait

    def _counting_put(slug, task_id, *, metadata=None):
        raw_puts.append({"slug": slug, "task_id": task_id, "metadata": metadata})
        if isinstance(metadata, dict) and metadata.get("authority_v2_generation"):
            tagged_puts.append(dict(metadata))
        return original_put(slug, task_id, metadata=metadata)

    fixture.monkeypatch.setattr(queue, "put_nowait", _counting_put)

    # Test-only lifecycle barrier: a committed tagged item must NOT be dequeued
    # before the simulated crash, so it can be genuinely lost with the owner.
    fixture.tagged_dequeue_blocked = True
    fixture._install_tagged_dequeue_barrier()

    # Release ONLY the origin turn: it returns success with a separate provider
    # conversation and NO callback -- the real completion-recovery premise.
    fixture.release_session(origin_session)
    recovery_launch = fixture.wait_for_launch_for(root_id, after=1)
    recovery_session = recovery_launch["session_id"]
    assert recovery_session and recovery_session != origin_session
    assert recovery_launch.get("resume_session_id") == provider_resume_id
    assert recovery_launch.get("recovery") is True

    binding = _binding(fixture, root_id, recovery_session)
    assert binding is not None and binding["mode"] == "v2"

    body = _completion_body(binding, root_id)
    payload = fixture.write_payload(body, name="completion-restart.json")
    cli = fixture.run_cli(payload)
    assert cli.returncode == 0, cli.stderr
    assert fixture.last_http()["status"] == 200

    db = fixture.org.db
    receipt = dict(db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone())
    assert receipt["state"] == "callback_accepted"
    results, attempt, _audits = _admission_counts(fixture, root_id)
    assert len(results) == 1 and attempt is not None
    row_id = results[0]["id"]
    assert receipt["accepted_result_id"] == row_id
    assert receipt["accepted_result_session_id"] == recovery_session

    # REAL production owner binding (never a test boot string).
    fixture.org.bind_authority_v2_owner()
    assert attempt.origin_boot_id == fixture.org.authority_v2_origin_boot_id

    # LABELLED STAGING through REAL public writers (automatic pre-final hook is
    # dark in this unit).
    stage_kwargs = dict(
        root_task_id=root_id, manager_agent=MANAGER,
        manager_session_id=recovery_session, result_id=row_id,
        origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    assert db.evaluate_authority_policy_v2_candidate(**stage_kwargs).status == "evaluated"
    assert db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id
    assert db.get_task(root_id).status is TaskStatus.PENDING
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "pending"
    assert db.get_authority_policy_v2_recovery_notification(generation).state == "needed"
    assert not raw_puts and not tagged_puts
    assert fixture.launch_count() == 2

    # Release ONLY the recovery turn: the REAL run_step consumer reads the
    # durable accepted result and settles the Q + publishes the tagged token.
    fixture.release_session(recovery_session)
    fixture.await_run_step_returns(root_id, 1)

    receipt = dict(db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone())
    assert receipt["state"] == "callback_consumed"
    assert receipt["accepted_result_id"] == row_id
    assert db.get_authority_policy_v2_recovery_notification(generation).state == "published"
    assert db.get_authority_policy_v2_root_dispatch(root_id).state == "pending"
    assert len(raw_puts) == 1 and len(tagged_puts) == 1
    assert tagged_puts[0]["authority_v2_generation"] == generation
    assert tagged_puts[0]["publication_attempt"] == 1
    assert fixture.launch_count() == 2

    # Deterministic quiescence of the ORIGINAL recovery worker: its queue item
    # reached ``task_done`` (the only remaining unfinished item is the lost
    # tagged publication), so the old owner is quiet before the crash.
    deadline = time.monotonic() + 10.0
    while (
        time.monotonic() < deadline
        and fixture.state.queue._queue._unfinished_tasks != 1
    ):
        time.sleep(0.01)
    assert fixture.state.queue._queue._unfinished_tasks == 1

    # The committed tagged item is still IN the old in-memory queue -- never
    # dequeued, never admitted on the old owner.
    pending = [
        item for item in fixture.state.queue._queue._queue
        if isinstance(item[2], dict) and item[2].get("authority_v2_generation")
    ]
    assert len(pending) == 1
    assert pending[0][2]["authority_v2_generation"] == generation

    return {
        "root_id": root_id, "generation": generation, "row_id": row_id,
        "origin_session": origin_session, "recovery_session": recovery_session,
        "provider_resume_id": provider_resume_id,
        "old_boot": fixture.org.authority_v2_origin_boot_id,
        "old_step": db.get_task(root_id).orchestration_step_count,
        "raw_puts": raw_puts, "tagged_puts": tagged_puts, "payload": payload,
    }


def _restart_open_owner(fixture: _ShippingFixture) -> object:
    """Open a genuine NEW owner over the SAME persisted file and bind it."""
    fixture._open_owner()
    org = fixture.org
    org.bind_authority_v2_owner()
    return org


def _restart_observe_puts(fixture: _ShippingFixture):
    queue = fixture.state.queue
    raw: list[dict] = []
    tagged: list[dict] = []
    real_put = queue.put_nowait

    def _count(slug, task_id, *, metadata=None):
        raw.append({"slug": slug, "task_id": task_id, "metadata": metadata})
        if isinstance(metadata, dict) and metadata.get("authority_v2_generation"):
            tagged.append(dict(metadata))
        return real_put(slug, task_id, metadata=metadata)

    fixture.monkeypatch.setattr(queue, "put_nowait", _count)
    return raw, tagged


def _restart_assert_retained(db, root_id: str, generation: str, row_id: int) -> None:
    receipt = dict(db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (root_id, MANAGER),
    ).fetchone())
    assert receipt["state"] == "callback_consumed"
    assert receipt["accepted_result_id"] == row_id
    assert db.get_authority_policy_v2_root_dispatch(root_id).generation_id == generation
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes "
        "WHERE root_task_id=?", (root_id,),
    ).fetchone()[0] == 1


def _restart_drain_and_assert(
    fixture: _ShippingFixture, db, *, root_id: str, generation: str,
    old_step: int, tag_holds,
) -> dict:
    """Start the new owner's real workers and assert ONE winning admission."""
    # The ONLY external-launch double must be installed BEFORE the real workers
    # start, exactly as in the C1/C2 venue.
    fixture.install_launch_hold()
    fixture._start_http()
    reserved = None
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        admitted = db.get_authority_policy_v2_recovery_notification(generation)
        if (
            admitted is not None and admitted.state == "settled"
            and admitted.next_session_id
        ):
            reserved = admitted.next_session_id
            if fixture.captured.get("session_id") == reserved:
                break
        time.sleep(0.05)
    assert reserved is not None, "generation was never admitted"
    dispatch = db.get_authority_policy_v2_root_dispatch(root_id)
    assert dispatch.state == "admitted" and dispatch.generation_id == generation
    task = db.get_task(root_id)
    assert task.id == root_id
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.assigned_agent == MANAGER
    assert task.current_session_id == reserved
    # exactly one winning admission step increment
    assert task.orchestration_step_count == old_step + 1
    assert fixture.captured["session_id"] == reserved
    # exactly one held external launch on the NEW owner
    assert fixture.launch_count() == 1
    stages = [
        a["payload"]["stage"]
        for a in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=root_id, manager_agent=MANAGER,
        )
    ]
    assert stages.count("generation_claimed") == 1
    assert stages.count("notification_settled") == 1
    # no remint / re-evaluation / second candidate / spend of the envelope
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_evaluations").fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidates").fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes").fetchone()[0] == 1
    envelope = db.get_authority_policy_v2_continue_envelope_for_root(root_id)
    assert envelope.lifecycle_state == "active"
    # no ordinary fallback enqueue happened (all raw puts were tagged)
    assert tag_holds["raw"] == tag_holds["tagged"]

    # After-admission restart replay while the reserved invocation is still
    # held: the SETTLED generation is READ-ONLY -- no republish/reclaim/second
    # admission/launch and byte-identical durable residue.  (The
    # admitted-but-unsettled bookkeeping-only settle-once case stays the
    # existing caller-unit
    # ``test_reopened_orgstate_admitted_generation_settles_once`` matrix.)
    from runtime.orchestrator.authority import (
        reconcile_authority_policy_v2_post_final,
    )

    launches_before = fixture.launch_count()
    snapshot_before_replay = _restart_durable_snapshot(db, root_id, generation)
    reconcile_authority_policy_v2_post_final(
        fixture.org.orchestrator, root_task_id=root_id,
    )
    assert fixture.launch_count() == launches_before
    assert _restart_durable_snapshot(
        db, root_id, generation
    ) == snapshot_before_replay
    assert db.get_authority_policy_v2_recovery_notification(
        generation
    ).state == "settled"

    fixture.release_launch()
    fixture.join_workers()
    return {"reserved": reserved}


def _drive_restart_lost_queue(
    fixture: _ShippingFixture, *, negative: str | None = None,
) -> dict:
    ctx = _restart_genuine_committed_publication(fixture)
    root_id = ctx["root_id"]
    generation = ctx["generation"]
    old_db = fixture.org.db
    old_conn = old_db._conn
    _restart_assert_retained(old_db, root_id, generation, ctx["row_id"])

    # Crash: the committed tagged in-memory item is lost with the old owner.
    fixture._crash_detach()

    # DISTINCT Database + real OrgState over the SAME persisted file.
    new_org = _restart_open_owner(fixture)
    new_db = new_org.db
    assert new_db is not old_db
    assert new_db._conn is not old_conn
    assert new_db.db_path == old_db.db_path
    assert new_org.authority_v2_origin_boot_id != ctx["old_boot"]
    # Real server-owned boot + permission-digest binding (never a fixture lambda
    # and never a boot string reassigned on a live object).
    assert new_db._v2_process_boot_id == new_org.authority_v2_origin_boot_id
    assert callable(new_db._v2_permission_surface_reader)
    _restart_assert_retained(new_db, root_id, generation, ctx["row_id"])
    assert new_db.get_authority_policy_v2_recovery_notification(
        generation
    ).state == "published"

    if negative == "corrupt_receipt":
        # Corrupt the carried genuine settlement evidence: delete the exact
        # accepted recovery receipt the publisher must re-authenticate.
        new_db._conn.execute(
            "DELETE FROM task_completion_recoveries WHERE task_id=? AND agent=?",
            (root_id, MANAGER),
        )
        new_db._conn.commit()

    raw, tagged = _restart_observe_puts(fixture)
    snapshot_before = _restart_durable_snapshot(new_db, root_id, generation)

    from runtime.daemon.__main__ import (
        _publish_v2_generations_on_startup,
        _sweep_on_startup,
    )

    # Production order: startup sweep, then the per-org startup publication.
    _sweep_on_startup(new_db, fixture.state.queue, ORG, new_org.orchestrator)
    _publish_v2_generations_on_startup(new_org, fixture.state.queue)

    if negative == "corrupt_receipt":
        # Bounded refusal: zero raw/accepted puts, zero claims/launches, no
        # ordinary decision effect and byte-identical residue.
        assert raw == [] and tagged == []
        assert fixture.launch_count() == 0
        assert _restart_durable_snapshot(new_db, root_id, generation) == snapshot_before
        assert new_db.get_authority_policy_v2_recovery_notification(
            generation
        ).state == "published"
        assert new_db.get_authority_policy_v2_root_dispatch(root_id).state == "pending"
        assert new_db.get_task(root_id).status is TaskStatus.PENDING
        stages = [
            a["payload"]["stage"]
            for a in new_db.list_authority_policy_v2_result_stage_audits(
                root_task_id=root_id, manager_agent=MANAGER,
            )
        ]
        assert stages.count("generation_claimed") == 0
        assert stages.count("notification_settled") == 0
        fixture._crash_detach()
        return {"root_id": root_id, "generation": generation, "refused": True}

    # Already-consumed Q did NOT suppress independent G discovery: exactly ONE
    # reclaimed tagged publication with the permitted NEW boot and P.
    assert len(raw) == 1 and len(tagged) == 1
    assert tagged[0]["authority_v2_generation"] == generation
    assert tagged[0]["publication_attempt"] == 2
    notification = new_db.get_authority_policy_v2_recovery_notification(generation)
    assert notification.state == "published"
    assert notification.publisher_boot_id == new_org.authority_v2_origin_boot_id

    _restart_drain_and_assert(
        fixture, new_db, root_id=root_id, generation=generation,
        old_step=ctx["old_step"], tag_holds={"raw": len(raw), "tagged": len(tagged)},
    )
    _restart_assert_retained(new_db, root_id, generation, ctx["row_id"])
    return {"root_id": root_id, "generation": generation, "tagged": tagged}


def _drive_restart_startup_fault(
    fixture: _ShippingFixture, *, fault: str,
) -> dict:
    ctx = _restart_genuine_committed_publication(fixture)
    old_db = fixture.org.db
    old_conn = old_db._conn
    fixture._crash_detach()

    owner2 = _restart_open_owner(fixture)
    db2 = owner2.db
    assert db2 is not old_db and db2._conn is not old_conn
    assert db2.db_path == old_db.db_path
    boot2 = owner2.authority_v2_origin_boot_id
    assert boot2 != ctx["old_boot"]

    calls = {"n": 0}
    granted = {"on": True}
    real_claim = db2.claim_authority_policy_v2_notification_publication
    real_ack = db2.acknowledge_authority_policy_v2_notification_publication
    if fault == "claim":
        def _claim_boom(**kwargs):
            calls["n"] += 1
            if granted["on"]:
                raise RuntimeError("injected publication-claim failure")
            return real_claim(**kwargs)

        fixture.monkeypatch.setattr(
            db2, "claim_authority_policy_v2_notification_publication", _claim_boom,
        )
    else:
        def _ack_boom(**kwargs):
            calls["n"] += 1
            if granted["on"]:
                raise RuntimeError("injected acknowledgement failure")
            return real_ack(**kwargs)

        fixture.monkeypatch.setattr(
            db2, "acknowledge_authority_policy_v2_notification_publication",
            _ack_boom,
        )

    # Call-through recorder for the bounded receipts at the REAL publisher.
    import runtime.orchestrator.authority as authority_mod

    receipts_seen: list[list] = []
    real_pub = authority_mod.publish_authority_policy_v2_notifications

    def _rec(*args, **kwargs):
        out = real_pub(*args, **kwargs)
        receipts_seen.append(out)
        return out

    fixture.monkeypatch.setattr(
        authority_mod, "publish_authority_policy_v2_notifications", _rec,
    )

    raw, tagged = _restart_observe_puts(fixture)
    snapshot_before = _restart_durable_snapshot(
        db2, ctx["root_id"], ctx["generation"],
    )

    from runtime.daemon.__main__ import (
        _publish_v2_generations_on_startup,
        _sweep_on_startup,
    )

    _sweep_on_startup(db2, fixture.state.queue, ORG, owner2.orchestrator)
    _publish_v2_generations_on_startup(owner2, fixture.state.queue)

    assert calls["n"] >= 1, "injection never fired"
    assert receipts_seen, "publisher never returned receipts"
    flat = [r for batch in receipts_seen for r in batch]

    if fault == "claim":
        # RETURNED bounded refusal (never a thrown exception out of the caller).
        assert flat and all(
            r.get("status") == "publication_claim_failed" for r in flat
        ), flat
        assert raw == [] and tagged == []
        assert fixture.launch_count() == 0
        assert _restart_durable_snapshot(
            db2, ctx["root_id"], ctx["generation"],
        ) == snapshot_before
        assert db2.get_authority_policy_v2_recovery_notification(
            ctx["generation"]
        ).state == "published"
        assert db2.get_task(ctx["root_id"]).status is TaskStatus.PENDING

        # Remove the injection (call-through resumes) and run the permitted
        # retry on the SAME permitted new boot -- one reclaimed publication.
        granted["on"] = False
        _publish_v2_generations_on_startup(owner2, fixture.state.queue)
        assert len(raw) == 1 and len(tagged) == 1
        assert tagged[0]["authority_v2_generation"] == ctx["generation"]
        assert tagged[0]["publication_attempt"] == 2
        assert db2.get_authority_policy_v2_recovery_notification(
            ctx["generation"]
        ).publisher_boot_id == boot2
        _restart_drain_and_assert(
            fixture, db2, root_id=ctx["root_id"], generation=ctx["generation"],
            old_step=ctx["old_step"], tag_holds={"raw": len(raw), "tagged": len(tagged)},
        )
        _restart_assert_retained(db2, ctx["root_id"], ctx["generation"], ctx["row_id"])
        return {"root_id": ctx["root_id"], "generation": ctx["generation"]}

    # Post-put acknowledgement failure: a LEGITIMATE tagged put exists with a
    # replayable lease/evidence -- never an invented zero-put claim.  The sweep
    # publishes first (ack failure); the later startup publication then observes
    # the retained live lease as a bounded pending refusal (no second put).
    statuses = [r.get("status") for r in flat]
    assert "publication_acknowledgement_failed" in statuses, flat
    assert not any(
        s in ("published", "published_exact", "publication_claim_failed")
        for s in statuses
    ), flat
    assert len(raw) == 1 and len(tagged) == 1
    assert tagged[0]["authority_v2_generation"] == ctx["generation"]
    assert tagged[0]["publication_attempt"] == 2
    notification = db2.get_authority_policy_v2_recovery_notification(ctx["generation"])
    assert notification.state == "publishing"
    assert notification.publisher_boot_id == boot2
    assert notification.lease_deadline is not None
    stages2 = [
        a["payload"] for a in db2.list_authority_policy_v2_result_stage_audits(
            root_task_id=ctx["root_id"], manager_agent=MANAGER,
        )
    ]
    claimed_attempts = [
        p.get("publication_attempt") for p in stages2
        if p.get("stage") == "publish_claimed"
    ]
    assert 2 in claimed_attempts, claimed_attempts
    assert db2.get_authority_policy_v2_root_dispatch(ctx["root_id"]).state == "pending"

    # Crash the failed owner (its in-memory tagged item is lost) and recover on
    # a genuinely NEW boot: exactly one admission/launch despite the queued and
    # replayed tokens.
    fixture._crash_detach()
    owner3 = _restart_open_owner(fixture)
    db3 = owner3.db
    assert db3 is not db2
    assert owner3.authority_v2_origin_boot_id not in (boot2, ctx["old_boot"])
    raw3, tagged3 = _restart_observe_puts(fixture)
    _sweep_on_startup(db3, fixture.state.queue, ORG, owner3.orchestrator)
    _publish_v2_generations_on_startup(owner3, fixture.state.queue)
    assert len(raw3) == 1 and len(tagged3) == 1
    assert tagged3[0]["authority_v2_generation"] == ctx["generation"]
    assert tagged3[0]["publication_attempt"] == 3
    recovered = db3.get_authority_policy_v2_recovery_notification(ctx["generation"])
    assert recovered.state == "published"
    assert recovered.publisher_boot_id == owner3.authority_v2_origin_boot_id
    _restart_drain_and_assert(
        fixture, db3, root_id=ctx["root_id"], generation=ctx["generation"],
        old_step=ctx["old_step"],
        tag_holds={"raw": len(raw3), "tagged": len(tagged3)},
    )
    _restart_assert_retained(db3, ctx["root_id"], ctx["generation"], ctx["row_id"])
    return {"root_id": ctx["root_id"], "generation": ctx["generation"]}


# ── C3: genuine shipping restart / lost queue (fresh + historical) ────────


def test_shipping_restart_lost_queue_generation_drains(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2, dequeue_gate=True)
    fixture.start()
    try:
        result = _drive_restart_lost_queue(fixture)
        assert result["tagged"][0]["publication_attempt"] == 2
    finally:
        fixture.stop()


def test_shipping_historically_migrated_restart_lost_queue_generation_drains(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
        dequeue_gate=True,
    )
    fixture.start()
    try:
        result = _drive_restart_lost_queue(fixture)
        assert result["tagged"][0]["publication_attempt"] == 2
    finally:
        fixture.stop()


# ── C4: actual startup caller claim/ack failure (fresh + historical) ─────


def test_shipping_restart_startup_claim_failure_then_retry(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2, dequeue_gate=True)
    fixture.start()
    try:
        _drive_restart_startup_fault(fixture, fault="claim")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_startup_claim_failure_then_retry(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
        dequeue_gate=True,
    )
    fixture.start()
    try:
        _drive_restart_startup_fault(fixture, fault="claim")
    finally:
        fixture.stop()


def test_shipping_restart_startup_ack_failure_then_new_boot(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2, dequeue_gate=True)
    fixture.start()
    try:
        _drive_restart_startup_fault(fixture, fault="ack")
    finally:
        fixture.stop()


def test_shipping_historically_migrated_startup_ack_failure_then_new_boot(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
        dequeue_gate=True,
    )
    fixture.start()
    try:
        _drive_restart_startup_fault(fixture, fault="ack")
    finally:
        fixture.stop()


# ── C4: corrupt startup negative paired with the healthy C3 control ──────


def test_shipping_restart_corrupt_settlement_refuses(tmp_path, monkeypatch):
    fixture = _ShippingFixture(tmp_path, monkeypatch, queue_workers=2, dequeue_gate=True)
    fixture.start()
    try:
        result = _drive_restart_lost_queue(fixture, negative="corrupt_receipt")
        assert result["refused"] is True
    finally:
        fixture.stop()


def test_shipping_historically_migrated_restart_corrupt_settlement_refuses(
    tmp_path, monkeypatch,
):
    fixture = _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=True, queue_workers=2,
        dequeue_gate=True,
    )
    fixture.start()
    try:
        result = _drive_restart_lost_queue(fixture, negative="corrupt_receipt")
        assert result["refused"] is True
    finally:
        fixture.stop()


# ══════════════════════════════════════════════════════════════════════════
# TASK-8718 — C3d4b Part D: ONE real DaemonState loading TWO real
# OrgState/Database venues over one owned RuntimeDir, with the SAME textual
# root task id in both orgs.  Real startup/enqueue publication -> real
# TaskQueue worker -> Dispatcher -> Orchestrator/run_step -> the atomic
# generation fence -> `_run_agent`, with ONLY the external provider launch
# doubled (the shipping launch hold).  Generation staging is labelled: it uses
# the accepted public writers while the automatic pre-final hook stays dark.
# ══════════════════════════════════════════════════════════════════════════

TWO_ORG_A = "isolated-org-a"
TWO_ORG_B = "isolated-org-b"
DUAL_ROOT_ID = "TASK-C3D4B-DUAL"


def _two_org_fixture(
    tmp_path, monkeypatch, *, seed_historical: bool = False,
    queue_workers: int = 3,
) -> _ShippingFixture:
    return _ShippingFixture(
        tmp_path, monkeypatch, seed_historical=seed_historical,
        queue_workers=queue_workers, orgs=(TWO_ORG_A, TWO_ORG_B),
    )


def _bind_two_orgs(fixture: _ShippingFixture) -> None:
    """Bind the REAL server-owned boot identity + permission reader per org."""
    for slug in fixture.org_slugs:
        fixture.orgs[slug].bind_authority_v2_owner()


def _prewarm_two_org_skills(fixture: _ShippingFixture) -> None:
    """Sequentially build the shared canonical skill packages for both orgs.

    The canonical store lives under the ONE daemon home, but its per-workspace
    materialization lock is workspace-scoped; two orgs' FIRST concurrent launch
    would otherwise race on the store's predictable ``.tmp.<hash>`` build path
    (an out-of-radius canonical-store concern, recorded in the handoff, not
    fixed here).  This helper deterministically pre-builds the same packages
    through the REAL production materializer before any worker starts, so the
    shipping launches below only exercise the supported reuse path.
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import materialize_workspace_skills

    skills_root = fixture._settings.project_root / "runtime" / "skills"
    for slug in fixture.org_slugs:
        org = fixture.orgs[slug]
        org_paths = OrgPaths(root=org.root)
        for agent_name in (MANAGER, WORKER):
            materialize_workspace_skills(
                org_paths.workspaces_dir / agent_name, fixture._settings,
                slug=slug, context="task", provider="codex",
                agent_name=agent_name, team=TEAM, skills_root=skills_root,
                org_root=org.root, db=org.db,
            )


def _stage_pending_generation(
    org, *, task_id: str, session_id: str, confidence: int = 90,
) -> dict:
    """LABELLED pre-final staging of ONE authentic pending-v2 generation.

    Every step is an existing REAL public writer on the shipping org's own
    ``Database`` (activate selector -> normalized callback admission -> claim /
    claim-audit / evaluate / evaluation-audit / consume / consumed-audit ->
    finalize -> ordinary completion evidence -> exact receipt settlement).  The
    automatic production pre-final hook stays dark; no synthetic evaluator,
    authenticator, receipt or generation is manufactured.  The org's
    server-owned boot/permission bindings are the ones bound by
    :func:`_bind_two_orgs`, never a fixture constant.
    """
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    from tests.test_authority_v2_attempt_admission import (
        _admit,
        _assessment,
        _seed_bound_task,
    )
    from tests.test_authority_v2_finalization_settlement import (
        _ProducerStub,
        _result_row,
    )
    import hashlib
    import types as _types

    from runtime.models import authority_policy_v2_canonical_json_bytes
    from runtime.orchestrator.orchestrator import (
        Orchestrator,
        completion_report_from_result_row,
    )

    store = AuthorityPolicyStore(org.db)
    binding = _seed_bound_task(store, task_id=task_id, session_id=session_id)
    assert binding is not None and binding["mode"] == "v2"
    # Build the normalized carrier for THIS root/session (the shared seed
    # helper hardcodes its own root id; this venue uses its own textual id).
    carrier = {
        "activation_epoch": binding["selector_epoch"],
        "activation_id": binding["activation_id"],
        "contract_digest": binding["contract_digest"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "executor_kind": binding["executor_kind"],
        "manager_session_id": binding["session_id"],
        "model_id": binding["model_id"],
        "policy_digest": binding["policy_digest"],
        "policy_version": binding["policy_version"],
        "provider_id": binding["provider_id"],
        "release_id": binding["release_id"],
        "root_task_id": task_id,
        **_assessment(confidence=confidence),
    }
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission = {
        "team": binding["team"],
        "binding_id": binding["binding_id"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "contract_digest": binding["contract_digest"],
        "release_id": binding["release_id"],
        "activation_id": binding["activation_id"],
        "activation_epoch": binding["selector_epoch"],
        "selector_id": binding["selector_id"],
        "assessment_digest": hashlib.sha256(canonical).hexdigest(),
        "assessment_canonical_json": canonical.decode("utf-8"),
        # The real server-owned per-org daemon-process boot identity.
        "origin_boot_id": org.db._v2_process_boot_id,
    }
    assert _admit(
        store, carrier, admission, task_id=task_id, session_id=session_id,
    ) is True
    row = org.db.get_latest_task_result(task_id, MANAGER, session_id)
    assert row is not None
    attempt = org.db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None

    stage_kwargs = dict(
        root_task_id=task_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row["id"],
        origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert org.db.claim_authority_policy_v2_candidate(**stage_kwargs).status == "claimed"
    assert org.db.audit_authority_policy_v2_candidate_claim(**stage_kwargs).status == "claim_audited"
    _evaluated = org.db.evaluate_authority_policy_v2_candidate(**stage_kwargs)
    assert _evaluated.status == "evaluated", _evaluated
    assert org.db.audit_authority_policy_v2_candidate_evaluation(**stage_kwargs).status == "evaluation_audited"
    assert org.db.consume_authority_policy_v2_candidate(**stage_kwargs).status == "consumed"
    assert org.db.audit_authority_policy_v2_candidate_consumption(**stage_kwargs).status == "consumed_audited"
    finalized = org.db.finalize_authority_policy_v2_continuation(**stage_kwargs)
    assert finalized.status == "continued", finalized
    # Ordinary completion evidence through the REAL producer seam, bound to
    # THIS root (the shared seed helper hardcodes its own root id).
    result_row = _result_row(store, row["id"])
    report = completion_report_from_result_row(
        task_id, dict(result_row), fallback_agent=MANAGER,
    )
    Orchestrator._log_step_result(
        _ProducerStub(store), task_id,
        _types.SimpleNamespace(session_id=session_id), report,
        result_row_id=row["id"],
    )
    settled = org.db.settle_authority_policy_v2_continuation_receipt(
        root_task_id=task_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row["id"],
    )
    assert settled.status == "settled", settled

    generation = finalized.notification_id
    assert org.db.get_authority_policy_v2_recovery_notification(
        generation
    ).state == "needed"
    dispatch = org.db.get_authority_policy_v2_root_dispatch(task_id)
    assert dispatch.state == "pending" and dispatch.generation_id == generation
    assert org.db.get_task(task_id).status is TaskStatus.PENDING
    return {
        "generation": generation, "result_id": row["id"], "session_id": session_id,
        "origin_boot_id": attempt.origin_boot_id,
    }


def _part_d_observe_enqueues(fixture: _ShippingFixture):
    """Call-through observation of the REAL queue writer (raw vs tagged puts).

    Wraps the primitive ``TaskQueue.enqueue`` (``put_nowait`` delegates to it),
    so both the ordinary enqueue and the authenticated publisher are observed
    without replacing the writer.
    """
    queue = fixture.state.queue
    raw: list[dict] = []
    tagged: list[dict] = []

    real = queue.enqueue

    def _count(slug, task_id, *, metadata=None):
        raw.append({"slug": slug, "task_id": task_id, "metadata": metadata})
        if isinstance(metadata, dict) and metadata.get("authority_v2_generation"):
            tagged.append({"slug": slug, "task_id": task_id, **dict(metadata)})
        return real(slug, task_id, metadata=metadata)

    fixture.monkeypatch.setattr(queue, "enqueue", _count)
    return raw, tagged


def _v2_evidence_counts(org) -> dict:
    db = org.db
    names = (
        "authority_policy_v2_attempts",
        "authority_policy_v2_candidates",
        "authority_policy_v2_pins",
        "authority_policy_v2_candidate_audit",
        "authority_policy_v2_evaluations",
        "authority_policy_v2_continue_envelopes",
        "authority_policy_v2_recovery_notifications",
        "authority_policy_v2_root_dispatch",
    )
    return {
        name: db._conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in names
    }


def _claims_for(org, generation: str) -> int:
    """Count ``generation_claimed`` stage events for THIS exact generation."""
    return sum(
        1
        for a in org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=DUAL_ROOT_ID, manager_agent=MANAGER,
        )
        if a["payload"].get("stage") == "generation_claimed"
        and a["payload"].get("generation_id") == generation
    )


def _await_generation_admission(
    fixture: _ShippingFixture, org, generation: str, *, task_id: str = DUAL_ROOT_ID,
    timeout: float = 60.0,
) -> str:
    """Deterministic barrier: this org admitted ITS OWN generation and launched."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        notification = org.db.get_authority_policy_v2_recovery_notification(generation)
        if (
            notification is not None and notification.state == "settled"
            and notification.next_session_id
        ):
            reserved = notification.next_session_id
            task = org.db.get_task(task_id)
            if task.status is TaskStatus.IN_PROGRESS and task.current_session_id == reserved:
                with fixture.launch_cv:
                    launched = any(
                        e.get("_fixture_org") == org.slug
                        and e.get("task_id") == task_id
                        and e.get("session_id") == reserved
                        for e in fixture.launch_history
                    )
                if launched:
                    return reserved
        time.sleep(0.02)
    notification = org.db.get_authority_policy_v2_recovery_notification(generation)
    task = org.db.get_task(task_id)
    stages = [
        a["payload"].get("stage")
        for a in org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=task_id, manager_agent=MANAGER,
        )
    ]
    launches = [
        {"org": e.get("_fixture_org"), "task_id": e.get("task_id"),
         "session_id": e.get("session_id")}
        for e in fixture.launch_history
    ]
    audits = [
        (a.get("action"), str(a.get("payload"))[:160])
        for a in org.db.get_audit_logs(task_id)
    ][-6:]
    raise AssertionError(
        f"org {org.slug} never admitted generation {generation}: "
        f"notification={getattr(notification, 'state', None)} "
        f"reserved={getattr(notification, 'next_session_id', None)} "
        f"task={task.status}/{task.current_session_id} note={getattr(task, 'note', None)} "
        f"stages={stages} "
        f"launches={launches} release={fixture.release_event.is_set()} "
        f"run_steps={list(fixture.run_step_returns)} audits={audits}"
    )


# ── D1: authentic pending-v2 org A + ordinary org B, same root id ─────────


def _drive_dual_org_mixed(fixture: _ShippingFixture) -> dict:
    from runtime.daemon import runner
    from runtime.daemon.__main__ import _publish_v2_generations_on_startup
    from runtime.models import TaskRecord

    org_a = fixture.orgs[TWO_ORG_A]
    org_b = fixture.orgs[TWO_ORG_B]
    _bind_two_orgs(fixture)
    _prewarm_two_org_skills(fixture)

    staged = _stage_pending_generation(
        org_a, task_id=DUAL_ROOT_ID, session_id="sess-dual-a",
    )
    # Org B owns the SAME textual root id as an ordinary row on its own DB.
    org_b.db.insert_task(TaskRecord(
        id=DUAL_ROOT_ID, brief="ordinary dual-root", team=TEAM,
        assigned_agent=MANAGER,
    ))
    assert org_b.db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        DUAL_ROOT_ID
    ).kind == "absent"

    fixture.install_launch_hold()
    raw, tagged = _part_d_observe_enqueues(fixture)

    # ACTUAL startup publication for org A; ordinary enqueue for org B.
    _publish_v2_generations_on_startup(org_a, fixture.state.queue)
    assert runner.enqueue_task(fixture.state, TWO_ORG_B, DUAL_ROOT_ID) is None

    assert len(raw) == 2, raw
    assert len(tagged) == 1, tagged
    assert tagged[0]["slug"] == TWO_ORG_A
    assert tagged[0]["authority_v2_generation"] == staged["generation"]
    assert tagged[0]["publication_attempt"] == 1
    assert org_a.db.get_authority_policy_v2_recovery_notification(
        staged["generation"]
    ).state == "published"
    # Org B's ordinary enqueue is genuinely untagged and B holds NO v2 evidence.
    assert [r["metadata"] for r in raw if r["slug"] == TWO_ORG_B] == [None]
    assert set(_v2_evidence_counts(org_b).values()) == {0}

    fixture._start_http()
    reserved_a = _await_generation_admission(
        fixture, org_a, staged["generation"], task_id=DUAL_ROOT_ID,
    )
    launch_b = fixture.wait_for_launch_for(
        DUAL_ROOT_ID, org=TWO_ORG_B, timeout=_LAUNCH_HOLD_SECONDS,
    )

    task_a = org_a.db.get_task(DUAL_ROOT_ID)
    task_b = org_b.db.get_task(DUAL_ROOT_ID)
    # Same textual root id in both orgs, each admitted under its OWN session.
    assert task_a.id == task_b.id == DUAL_ROOT_ID
    assert task_a.status is TaskStatus.IN_PROGRESS
    assert task_a.current_session_id == reserved_a
    assert task_b.status is TaskStatus.IN_PROGRESS
    assert task_b.current_session_id != reserved_a
    assert task_b.orchestration_step_count == 1
    assert task_a.orchestration_step_count == 2
    assert launch_b["_fixture_org"] == TWO_ORG_B
    assert launch_b["session_id"] != reserved_a

    # Exactly one generation claim on A; NONE on B (ordinary path stayed
    # ordinary; no v2 evidence leaked).
    assert _claims_for(org_a, staged["generation"]) == 1
    assert _claims_for(org_b, staged["generation"]) == 0
    assert set(_v2_evidence_counts(org_b).values()) == {0}
    assert org_b.db.get_authority_policy_v2_root_dispatch(DUAL_ROOT_ID) is None
    assert fixture.launch_count() == 2

    fixture.release_launch()
    fixture.join_workers()
    return {"reserved_a": reserved_a, "task_b_session": task_b.current_session_id}


# ── D2: both orgs authentic DISTINCT pending generations, same root id ────


def _drive_dual_org_generations(fixture: _ShippingFixture) -> dict:
    from runtime.daemon.__main__ import _publish_v2_generations_on_startup

    org_a = fixture.orgs[TWO_ORG_A]
    org_b = fixture.orgs[TWO_ORG_B]
    _bind_two_orgs(fixture)
    _prewarm_two_org_skills(fixture)

    staged_a = _stage_pending_generation(
        org_a, task_id=DUAL_ROOT_ID, session_id="sess-dual-a",
    )
    staged_b = _stage_pending_generation(
        org_b, task_id=DUAL_ROOT_ID, session_id="sess-dual-b",
    )
    assert staged_a["generation"] != staged_b["generation"]

    fixture.install_launch_hold()
    raw, tagged = _part_d_observe_enqueues(fixture)
    _publish_v2_generations_on_startup(org_a, fixture.state.queue)
    _publish_v2_generations_on_startup(org_b, fixture.state.queue)

    assert len(raw) == 2 and len(tagged) == 2
    assert {t["slug"] for t in tagged} == {TWO_ORG_A, TWO_ORG_B}
    assert {t["authority_v2_generation"] for t in tagged} == {
        staged_a["generation"], staged_b["generation"],
    }

    fixture._start_http()
    reserved_a = _await_generation_admission(
        fixture, org_a, staged_a["generation"], task_id=DUAL_ROOT_ID,
    )
    reserved_b = _await_generation_admission(
        fixture, org_b, staged_b["generation"], task_id=DUAL_ROOT_ID,
    )
    assert reserved_a != reserved_b

    # Each org admits exactly its OWN G, one step increment, one launch.
    assert org_a.db.get_authority_policy_v2_root_dispatch(
        DUAL_ROOT_ID
    ).generation_id == staged_a["generation"]
    assert org_b.db.get_authority_policy_v2_root_dispatch(
        DUAL_ROOT_ID
    ).generation_id == staged_b["generation"]
    assert _claims_for(org_a, staged_a["generation"]) == 1
    assert _claims_for(org_a, staged_b["generation"]) == 0
    assert _claims_for(org_b, staged_b["generation"]) == 1
    assert _claims_for(org_b, staged_a["generation"]) == 0
    assert org_a.db.get_task(DUAL_ROOT_ID).orchestration_step_count == 2
    assert org_b.db.get_task(DUAL_ROOT_ID).orchestration_step_count == 2
    assert fixture.launch_count() == 2

    # No remint / re-evaluation / spend: each org keeps one active envelope.
    for org in (org_a, org_b):
        counts = _v2_evidence_counts(org)
        assert counts["authority_policy_v2_candidates"] == 1
        assert counts["authority_policy_v2_evaluations"] == 1
        assert counts["authority_policy_v2_continue_envelopes"] == 1
        envelope = org.db.get_authority_policy_v2_continue_envelope_for_root(
            DUAL_ROOT_ID
        )
        assert envelope.lifecycle_state == "active"

    fixture.release_launch()
    fixture.join_workers()
    return {"reserved_a": reserved_a, "reserved_b": reserved_b}


# ── D3: wrong-org / duplicate / stale / malformed tokens + control ────────


def _drive_dual_org_negative(fixture: _ShippingFixture) -> dict:
    from runtime.daemon.__main__ import _publish_v2_generations_on_startup

    org_a = fixture.orgs[TWO_ORG_A]
    org_b = fixture.orgs[TWO_ORG_B]
    _bind_two_orgs(fixture)
    _prewarm_two_org_skills(fixture)

    staged_a = _stage_pending_generation(
        org_a, task_id=DUAL_ROOT_ID, session_id="sess-dual-a",
    )
    staged_b = _stage_pending_generation(
        org_b, task_id=DUAL_ROOT_ID, session_id="sess-dual-b",
    )
    gen_a, gen_b = staged_a["generation"], staged_b["generation"]

    snapshot_a = _restart_durable_snapshot(org_a.db, DUAL_ROOT_ID, gen_a)
    snapshot_b = _restart_durable_snapshot(org_b.db, DUAL_ROOT_ID, gen_b)
    counts_a = _v2_evidence_counts(org_a)
    counts_b = _v2_evidence_counts(org_b)
    assert _claims_for(org_a, gen_a) == 0 and _claims_for(org_b, gen_b) == 0

    fixture.install_launch_hold()
    fixture._start_http()

    queue = fixture.state.queue
    # (a) WRONG-ORG G tokens in BOTH directions, a STALE/unknown shape-valid
    # token, and a malformed present token, all through the REAL queue writer.
    queue.put_nowait(TWO_ORG_A, DUAL_ROOT_ID, metadata={
        "authority_v2_generation": gen_b, "publication_attempt": 1,
    })
    queue.put_nowait(TWO_ORG_B, DUAL_ROOT_ID, metadata={
        "authority_v2_generation": gen_a, "publication_attempt": 1,
    })
    queue.put_nowait(TWO_ORG_A, DUAL_ROOT_ID, metadata={
        "authority_v2_generation": "APV2N-" + "f" * 64, "publication_attempt": 1,
    })
    queue.put_nowait(TWO_ORG_A, DUAL_ROOT_ID, metadata={
        "authority_v2_generation": "", "publication_attempt": 1,
    })

    # Negative completion requires ACTUAL worker/run_step barriers.
    fixture.await_run_step_returns(DUAL_ROOT_ID, 3, slug=TWO_ORG_A)
    fixture.await_run_step_returns(DUAL_ROOT_ID, 1, slug=TWO_ORG_B)
    fixture.join_workers()

    # ZERO effects: no foreign admission, no ordinary fallback, no launch, no
    # remint/evaluation/spend, and byte-identical retained task/G/evidence.
    assert fixture.launch_count() == 0
    assert org_a.db.get_task(DUAL_ROOT_ID).status is TaskStatus.PENDING
    assert org_b.db.get_task(DUAL_ROOT_ID).status is TaskStatus.PENDING
    assert _restart_durable_snapshot(org_a.db, DUAL_ROOT_ID, gen_a) == snapshot_a
    assert _restart_durable_snapshot(org_b.db, DUAL_ROOT_ID, gen_b) == snapshot_b
    assert _v2_evidence_counts(org_a) == counts_a
    assert _v2_evidence_counts(org_b) == counts_b
    assert _claims_for(org_a, gen_a) == 0 and _claims_for(org_b, gen_b) == 0

    # Healthy control: the genuine generations then admit EXACTLY once each.
    raw, tagged = _part_d_observe_enqueues(fixture)
    _publish_v2_generations_on_startup(org_a, queue)
    _publish_v2_generations_on_startup(org_b, queue)
    assert len(raw) == 2 and len(tagged) == 2
    assert {t["authority_v2_generation"] for t in tagged} == {gen_a, gen_b}
    reserved_a = _await_generation_admission(
        fixture, org_a, gen_a, task_id=DUAL_ROOT_ID,
    )
    reserved_b = _await_generation_admission(
        fixture, org_b, gen_b, task_id=DUAL_ROOT_ID,
    )
    assert fixture.launch_count() == 2

    # (b) DUPLICATE of the genuine admitted token launches nothing new.
    # The genuine control run_steps are still parked in the held launch, so
    # bound the wait by the CURRENT count rather than an absolute total.
    with fixture.run_step_cv:
        before_a = sum(
            1 for r in fixture.run_step_returns
            if r[0] == TWO_ORG_A and r[1] == DUAL_ROOT_ID
        )
    queue.put_nowait(TWO_ORG_A, DUAL_ROOT_ID, metadata={
        "authority_v2_generation": gen_a, "publication_attempt": 1,
    })
    fixture.await_run_step_returns(DUAL_ROOT_ID, before_a + 1, slug=TWO_ORG_A)
    assert fixture.launch_count() == 2
    assert _claims_for(org_a, gen_a) == 1
    assert org_a.db.get_task(DUAL_ROOT_ID).current_session_id == reserved_a
    assert org_b.db.get_task(DUAL_ROOT_ID).current_session_id == reserved_b

    fixture.release_launch()
    fixture.join_workers()
    return {"reserved_a": reserved_a, "reserved_b": reserved_b}


# ── Fresh + full historical-migrated venues ───────────────────────────────


def test_shipping_dual_org_mixed_v2_and_ordinary(tmp_path, monkeypatch):
    fixture = _two_org_fixture(tmp_path, monkeypatch)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_mixed(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_dual_org_mixed(tmp_path, monkeypatch):
    fixture = _two_org_fixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_mixed(fixture)
    finally:
        fixture.stop()


def test_shipping_dual_org_distinct_generations(tmp_path, monkeypatch):
    fixture = _two_org_fixture(tmp_path, monkeypatch)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_generations(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_dual_org_distinct_generations(
    tmp_path, monkeypatch,
):
    fixture = _two_org_fixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_generations(fixture)
    finally:
        fixture.stop()


def test_shipping_dual_org_foreign_and_duplicate_tokens(tmp_path, monkeypatch):
    fixture = _two_org_fixture(tmp_path, monkeypatch)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_negative(fixture)
    finally:
        fixture.stop()


def test_shipping_historically_migrated_dual_org_foreign_tokens(
    tmp_path, monkeypatch,
):
    fixture = _two_org_fixture(tmp_path, monkeypatch, seed_historical=True)
    fixture.start(defer_http=True)
    try:
        _drive_dual_org_negative(fixture)
    finally:
        fixture.stop()
