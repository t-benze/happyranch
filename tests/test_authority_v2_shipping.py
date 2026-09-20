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
from runtime.models import AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION, TaskStatus

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


def _bootstrap_runtime(tmp_path: Path):
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import render_agent_text
    from runtime.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / ORG
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
    manager, worker = _fixture_agents()
    paths = OrgPaths(root=org_root)
    (paths.agents_dir / f"{MANAGER}.md").write_text(render_agent_text(manager))
    (paths.agents_dir / f"{WORKER}.md").write_text(render_agent_text(worker))
    return rt, org_root, (manager, worker)


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
            self._loop.close()

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
    ) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.seed_historical = seed_historical
        self.queue_workers = queue_workers
        self.home = tmp_path / "daemon-home"
        self.home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(self.home))
        self._stack = ExitStack()
        self.stop_event = threading.Event()
        self.release_event = threading.Event()
        self.launch_event = threading.Event()
        self.captured: dict = {}
        self.state = None
        self.org = None
        self.rt = None
        self.org_root = None
        self.fixture_agents = ()
        self.sock: socket.socket | None = None
        self.server: _OwnedServer | None = None
        self.capture = None
        self.cli_env: dict[str, str] = {}
        self.port = 0
        self._results: list[dict] = []

    # -- setup ---------------------------------------------------------
    def start(self) -> "_ShippingFixture":
        from runtime.config import Settings
        from runtime.daemon import paths as paths_mod
        from runtime.daemon.state import DaemonState
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

        import runtime.daemon.app as app_mod

        self.rt, self.org_root, self.fixture_agents = _bootstrap_runtime(self.tmp_path)
        # Everything the fixture owns must resolve inside tmp_path.
        assert self.rt.root.resolve().is_relative_to(self.tmp_path.resolve())
        assert self.org_root.resolve().is_relative_to(self.tmp_path.resolve())

        # Optional historical venue: reconstruct the FULL old schema and let
        # the actual current Database open/migration path converge it.  This
        # reuses the same checked-in historical fixture as the targeted
        # schema-integrity tests so later continuation cases share one venue.
        if self.seed_historical:
            from tests.authority_v2_historical_schema import (
                reconstruct_historical_database,
            )

            reconstruct_historical_database(
                OrgPaths(root=self.org_root).db_path
            )

        paths_mod.ensure_daemon_home()
        token = paths_mod.ensure_token()
        assert token
        self.cli_env = _sanitized_env(self.home)

        fixture_settings = Settings(project_root=CHECKOUT, queue_workers=self.queue_workers)
        self.monkeypatch.setattr(app_mod, "settings", fixture_settings)

        self.state = DaemonState.from_runtime(self.rt, fixture_settings)
        assert self.state.broken_orgs == {}, self.state.broken_orgs
        assert set(self.state.orgs.keys()) == {ORG}
        self.org = self.state.orgs[ORG]

        # Fixture-owned workspace bootstrap through the supported Codex adapter.
        org_paths = OrgPaths(root=self.org.root)
        adapter = CodexWorkspaceAdapter(fixture_settings, org_paths, slug=ORG)
        for agent in self.fixture_agents:
            workspace = org_paths.workspaces_dir / agent.name
            assert workspace.resolve().is_relative_to(self.tmp_path.resolve())
            adapter.ensure_workspace_ready(
                workspace, agent.name, agent.system_prompt,
            )
            marker = self.org.orchestrator._readiness_marker(workspace, "codex")
            assert marker == workspace / "AGENTS.md"
            assert marker.resolve().is_relative_to(self.tmp_path.resolve())
            assert marker.is_file()
            assert agent.system_prompt.strip() in marker.read_text()

        # Hold only the unrelated periodic service entry points.
        for module_name, attr in _HELD_LOOPS:
            module = importlib.import_module(module_name)
            self.monkeypatch.setattr(module, attr, _held_loop(self.stop_event))

        # Retain a socket bound to 127.0.0.1:0 and keep the real lifespan.
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

    # -- isolated API control pair ------------------------------------
    def _api(self, method: str, path: str, **kwargs) -> httpx.Response:
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        return httpx.request(
            method,
            f"http://127.0.0.1:{self.port}/api/v1/orgs/{ORG}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
            **kwargs,
        )

    def activate_v2_pair(self) -> dict:
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
        response = self._api("POST", base, json=body)
        assert response.status_code == 201, (response.status_code, response.text)
        return response.json()

    # -- real task + enqueue ------------------------------------------
    def create_and_enqueue_root(self) -> str:
        from runtime.daemon.runner import enqueue_task

        root_id = self.org.orchestrator.create_task("isolated shipping brief", team=TEAM)
        task = self.org.db.get_task(root_id)
        assert task.status is TaskStatus.PENDING
        assert task.current_session_id is None
        enqueue_task(self.state, ORG, root_id)
        return root_id

    # -- held launch ---------------------------------------------------
    def install_launch_hold(self) -> None:
        from runtime.orchestrator.executors import ExecutorResult

        fixture = self

        def _held_launch(**kwargs):
            kwargs["pre_launch_integrity_validator"]()
            kwargs["recovery_launch_validator"]()
            fixture.captured = dict(kwargs)
            fixture.launch_event.set()
            if not fixture.release_event.wait(timeout=_LAUNCH_HOLD_SECONDS):
                raise AssertionError("held launch was never released")
            return ExecutorResult(
                success=True, duration_seconds=1, session_id=kwargs["session_id"],
            )

        self.monkeypatch.setattr(
            self.org.orchestrator, "_launch_agent_with_scratch", _held_launch,
        )

    def wait_for_launch(self, *, timeout: float = _LAUNCH_HOLD_SECONDS) -> dict:
        assert self.launch_event.wait(timeout=timeout), "agent launch was never reached"
        return self.captured

    def release_launch(self) -> None:
        self.release_event.set()

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

    def run_cli(self, payload: Path) -> subprocess.CompletedProcess:
        command = [
            sys.executable, "-m", "cli.main", "report-completion",
            "--org", ORG, "--from-file", str(payload),
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
    """The accepted R3 real launch -> CLI -> admission -> fail-closed consumer
    flow.  Shared by the fresh and the historically migrated venues."""
    receipt = fixture.activate_v2_pair()
    assert receipt.get("family") == "v2" or receipt.get("activation_id")

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

    # Release the held launch: `_run_agent` reads the durable result and the
    # actual run-step consumer handles it.  The current hook fail-closes the
    # unsupported v2 assessment, so the root ESCALATES with no continuation.
    fixture.release_launch()
    fixture.join_workers()

    settled = fixture.org.db.get_task(root_id)
    assert settled.status is TaskStatus.ESCALATED, settled.status
    assert fixture.org.db.get_active_authority_continue_envelope(root_id) is None
    # No continuation work was manufactured.
    assert fixture.org.db.get_task(root_id).current_session_id == session_id
    assert fixture.org.db.get_latest_task_result(root_id, MANAGER, session_id)["id"] == results[0]["id"]
    # The authority hook really ran on the v2-bound escalation and fail-closed:
    # its recorded outcome is never a same-root continuation.
    hook_rows = [
        audit for audit in fixture.org.db.get_audit_logs(root_id)
        if audit["action"] == "authority_hook"
    ]
    assert hook_rows, "authority hook outcome was not recorded"
    assert hook_rows[-1]["payload"]["outcome"] != "continued_same_root"
    # Consumer reached the documented terminal fail-closed v2 state.
    assert settled.block_kind is None
    return {
        "root_id": root_id, "session_id": session_id, "binding": binding,
        "results": results,
    }


def test_shipping_real_launch_cli_admission_and_fail_closed_consumer(shipping):
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


def test_shipping_historically_migrated_schema_fail_closed_consumer(
    tmp_path, monkeypatch,
):
    """The SAME real venue over a FULL historical schema migrated forward.

    Proves the fixture wires into the owned R3 shipping venue and that the
    documented fail-closed v2 consumer outcome holds on a migrated DB.
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
        fixture.release_launch()
        fixture.join_workers()
        assert db.get_task(root_id).status is TaskStatus.ESCALATED
        assert db.get_active_authority_continue_envelope(root_id) is None
        return root_id, candidate.candidate_id

    # C3c: the four callable pre-final stage methods against the same genuine
    # persisted transport evidence, still while the external launch is held.
    # This proves the stages consume real result/assessment/binding evidence; it
    # does NOT prove shipping-hook continuation or an actual Pending/enqueue
    # (those remain later finalization/refusal/recovery work).
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
    fixture.release_launch()
    fixture.join_workers()
    settled = db.get_task(root_id)
    assert settled.status is TaskStatus.ESCALATED
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
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_continue_envelopes "
        "WHERE spending_result_id=?", (r2,),
    ).fetchone()[0] == 1

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
