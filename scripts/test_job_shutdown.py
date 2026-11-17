"""Focused Fix 3 shutdown checks; synthetic orgs, no server or providers."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_agent_retirement import fixture
from runtime.config import Settings
from runtime.daemon import app as daemon_app, jobs_runner
from runtime.daemon.routes import jobs
from runtime.daemon.state import DaemonState
from runtime.daemon.org_state import OrgState
from runtime.models import BlockKind, JobInterpreter, JobRecord, TaskRecord, TaskStatus

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(jobs_runner, "_JOBS_SHUTTING_DOWN", False))
        stack.enter_context(patch.object(jobs_runner, "_ORCH_RESOLVER", None))
        stack.enter_context(patch.object(jobs_runner, "_RESUME_MAIN_LOOP", None))
        for name in ("_INFLIGHT", "_RUNNER_TASKS", "_KILL_REASON_OVERRIDE"):
            stack.enter_context(patch.dict(getattr(jobs_runner, name), clear=True))

    async def launch(self, org: OrgState, script: str) -> jobs_runner._JobRegistration:
        org.db.insert_task(TaskRecord(id="TASK-901", brief="Synthetic shutdown waiter",
            assigned_agent="worker", team="retiring", status=TaskStatus.IN_PROGRESS,
            block_kind=BlockKind.BLOCKED_ON_JOB, blocked_on_job_ids='["JOB-901"]',
            current_session_id="sess-synthetic-shutdown"))
        org.db.update_task("TASK-901", blocked_on_job_ids='["JOB-901"]')
        org.db.insert_job(JobRecord(id="JOB-901", task_id="TASK-901", agent_name="worker",
            title="Synthetic shutdown job", rationale="Focused regression", script_text=script,
            interpreter=JobInterpreter.PYTHON3, created_at="2026-11-17T00:00:00+00:00"))
        await jobs._run_job_core(org, job_id="JOB-901", cwd_override=None, timeout_override=None)
        registration = jobs_runner._RUNNER_TASKS[(org.db, "JOB-901")]
        self.addAsyncCleanup(self.reap, registration)
        return registration

    async def reap(self, registration: jobs_runner._JobRegistration) -> None:
        proc = registration.process
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        if not registration.task.done():
            registration.task.cancel()
        await asyncio.gather(registration.task, return_exceptions=True)
        await asyncio.sleep(0)

    async def resistant_job(self, org: OrgState) -> jobs_runner._JobRegistration:
        ready = asyncio.Event()
        publish = org.event_bus.publish

        async def observe(topic: str, event: dict) -> None:
            if "shutdown-ready" in str(event):
                ready.set()
            return await publish(topic, event)

        with patch.object(org.event_bus, "publish", side_effect=observe):
            registration = await self.launch(org,
                "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "print('shutdown-ready',flush=True)\nwhile True: time.sleep(1)\n")
            await asyncio.wait_for(ready.wait(), 10)
        return registration

    async def test_lifespan_terms_before_runner_drain_and_joins_before_close(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, _client):
                registration = await self.resistant_job(org)
                state = DaemonState.idle(Settings())
                state.host_supervisor = SimpleNamespace(shutdown=lambda: events.append("host"))
                real_signal = os.killpg
                real_finish = jobs_runner.terminate_all_inflight
                real_close = state.close_all
                wire = jobs_runner.attach_jobs_resume_main_loop

                def signal_job(pid: int, sig: int) -> None:
                    self.assertEqual(pid, registration.process.pid)
                    events.append("TERM" if sig == signal.SIGTERM else "KILL")
                    real_signal(pid, sig)

                async def worker(*_args: object) -> None:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        events.append("runner-drain")

                async def finish(**kwargs: int) -> None:
                    self.assertEqual(kwargs["grace_seconds"], 30)
                    events.append("final-phase")
                    # The timing policy has a separate test; do not spend real
                    # shutdown grace while observing this cancellation ordering.
                    await real_finish(grace_seconds=0)
                    self.assertTrue(registration.task.done())
                    self.assertEqual(org.db.get_job("JOB-901").reason, "daemon_shutdown")
                    events.append("finalizers-joined")

                async def close() -> None:
                    events.append("close")
                    self.assertTrue(registration.task.done())
                    await real_close()

                loops = (
                    "thread_queue.thread_worker_loop", "dream_queue.dream_worker_loop",
                    "wake_queue.wake_worker_loop", "schedule_queue.schedule_worker_loop",
                    "thread_breaker_scheduler.thread_breaker_scheduler_loop",
                    "dream_scheduler.dream_scheduler_loop", "work_hours_scheduler.work_hours_scheduler_loop",
                    "schedule_scheduler.schedule_scheduler_loop", "zombie_reaper.zombie_reaper_loop",
                    "disk_sweeper.disk_sweeper_loop", "exchange_reaper.exchange_reaper_loop",
                    "direct_connect_projection_sweep.direct_connect_projection_sweep_loop",
                    "workspace_cleanup_scheduler.workspace_cleanup_scheduler_loop",
                )
                with ExitStack() as stack:
                    for target in loops:
                        stack.enter_context(patch("runtime.daemon." + target, side_effect=worker))
                    stack.enter_context(patch.object(daemon_app, "_wire_then_start_workers"))
                    stack.enter_context(patch.object(jobs_runner, "attach_jobs_resume_main_loop",
                        side_effect=lambda loop, resolver: wire(loop, lambda slug: org.orchestrator)))
                    stack.enter_context(patch.object(jobs_runner, "terminate_all_inflight", side_effect=finish))
                    stack.enter_context(patch.object(os, "killpg", side_effect=signal_job))
                    stack.enter_context(patch.object(state, "close_all", side_effect=close))
                    async with daemon_app._lifespan(SimpleNamespace(state=SimpleNamespace(daemon=state))):
                        await asyncio.sleep(0)  # Start only the controlled producer stand-ins.
                self.assertLess(events.index("TERM"), events.index("runner-drain"), events)
                self.assertLess(events.index("runner-drain"), events.index("final-phase"), events)
                self.assertLess(events.index("final-phase"), events.index("KILL"), events)
                self.assertLess(events.index("KILL"), events.index("finalizers-joined"), events)
                self.assertLess(events.index("finalizers-joined"), events.index("close"), events)

    async def test_terminal_during_shutdown_keeps_waiter_parked(self) -> None:
        # The open queue makes resume suppression observable independently of
        # the second, existing TaskQueue admission firewall. The normal case
        # proves the same real terminal writer and hook can enqueue this waiter.
        for shutting_down in (False, True):
            with self.subTest(shutting_down=shutting_down), tempfile.TemporaryDirectory() as temp:
                release, finished = asyncio.Event(), asyncio.Event()
                original_spawn = jobs._spawn_job

                async def hold_terminal(**kwargs: object) -> jobs_runner.JobRunResult:
                    result = await original_spawn(**kwargs)
                    finished.set()
                    await release.wait()
                    return result

                async with fixture(Path(temp) / "org") as (org, client):
                    jobs_runner.attach_jobs_resume_main_loop(asyncio.get_running_loop(), lambda slug: org.orchestrator)
                    state = client._transport.app.state.daemon
                    org.orchestrator._queue = state.queue
                    org.orchestrator._main_loop = asyncio.get_running_loop()
                    with patch.object(jobs, "_spawn_job", side_effect=hold_terminal):
                        registration = await self.launch(org, "print('natural result')\n")
                        try:
                            await asyncio.wait_for(finished.wait(), 10)
                            if shutting_down:
                                jobs_runner.begin_job_shutdown()
                            release.set()
                            await asyncio.wait_for(asyncio.shield(registration.task), 10)
                            self.assertEqual(org.db.get_job("JOB-901").status.value, "completed")
                            task = org.db.get_task("TASK-901")
                            self.assertEqual((task.status, task.block_kind, task.current_session_id),
                                (TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB, "sess-synthetic-shutdown"))
                            self.assertEqual(state.queue._queue.qsize(), 0 if shutting_down else 1)
                        finally:
                            release.set()
                            await self.reap(registration)

    async def test_spawn_crossing_shutdown_fence_receives_term_on_binding(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        original_spawn = asyncio.create_subprocess_exec

        async def hold_process(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            proc = await original_spawn(*args, **kwargs)
            entered.set()
            await release.wait()
            return proc

        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, _client):
                with patch.object(asyncio, "create_subprocess_exec", side_effect=hold_process):
                    registration = await self.launch(org, "print('binding result')\n")
                    try:
                        await asyncio.wait_for(entered.wait(), 10)
                        self.assertIsNone(registration.process)
                        jobs_runner.begin_job_shutdown()
                        release.set()
                        await asyncio.wait_for(asyncio.shield(registration.task), 10)
                        result = org.db.get_job("JOB-901")
                        self.assertEqual((result.status.value, result.reason), ("failed", "daemon_shutdown"))
                    finally:
                        release.set()
                        await self.reap(registration)

    async def test_shutdown_grace_is_thirty_seconds_from_early_term(self) -> None:
        for elapsed in (0, 17):
            with self.subTest(drain_seconds=elapsed), tempfile.TemporaryDirectory() as temp:
                jobs_runner.attach_jobs_resume_main_loop(asyncio.get_running_loop(), lambda slug: None)
                # Standalone fixture has no daemon resolver until lifespan wiring.
                jobs_runner._ORCH_RESOLVER = None
                async with fixture(Path(temp) / "org") as (org, _client):
                    registration = await self.resistant_job(org)
                    sleeps = []
                    real_sleep = asyncio.sleep

                    async def elapse(seconds: float) -> None:
                        if seconds > 0:
                            sleeps.append(seconds)
                        await real_sleep(0)

                    try:
                        jobs_runner.begin_job_shutdown()
                        registration.shutdown_term_at -= elapsed  # A drain has already spent this grace.
                        with patch.object(asyncio, "sleep", side_effect=elapse):
                            await jobs_runner.terminate_all_inflight()
                        self.assertEqual(len(sleeps), 1)
                        self.assertGreater(sleeps[0], 29 - elapsed)
                        self.assertLessEqual(sleeps[0], 30 - elapsed)
                        self.assertTrue(registration.task.done())
                        self.assertEqual(org.db.get_job("JOB-901").reason, "daemon_shutdown")
                        self.assertEqual(registration.process.returncode, -signal.SIGKILL)
                    finally:
                        await self.reap(registration)


class StopPolicyTests(unittest.TestCase):
    def test_shell_stop_allows_sixty_seconds_before_kill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "daemon.pid").write_text("424242")
            log = home / "signals"
            result = subprocess.run(["bash", "-c", """
kill() { printf '%s\\n' "kill $*" >> "$SHUTDOWN_TEST_LOG"; }
sleep() { printf '%s\\n' "sleep $*" >> "$SHUTDOWN_TEST_LOG"; }
export -f kill sleep
bash "$1" stop
""", "shutdown-test", str(ROOT / "scripts/daemon.sh")],
                env={**os.environ, "HAPPYRANCH_DAEMON_HOME": str(home), "SHUTDOWN_TEST_LOG": str(log)},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = log.read_text().splitlines()
            self.assertEqual(lines.count("sleep 1"), 60)
            self.assertLess(lines.index("kill -TERM 424242"), lines.index("sleep 1"))
            self.assertEqual(lines[-1], "kill -KILL 424242")

    def test_desktop_daemon_stop_and_service_allow_sixty_seconds(self) -> None:
        window = load_script("shutdown_window", "packaging/linux/desktop/happyranch_window.py")
        with tempfile.TemporaryDirectory() as temp:
            child = Mock()
            child.poll.return_value = None
            child.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 60), 0]
            window.stop_child(child, Path(temp))
            with self.subTest(path="child"):
                self.assertEqual(child.wait.call_args_list[0].kwargs, {"timeout": 60})
                child.kill.assert_called_once()
            run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
            controller = window.ServiceMode(Path(temp), runner=run)
            for verb, timeout in (("stop", 60), ("restart", 60), ("show", 10)):
                controller.command("systemctl", "--user", verb, controller.unit)
                with self.subTest(path="control", verb=verb):
                    self.assertEqual(run.call_args.kwargs["timeout"], timeout)
        unit = (ROOT / "packaging/linux/systemd/happyranch-daemon.service").read_text()
        with self.subTest(path="daemon-unit"):
            self.assertIn("TimeoutStopSec=60", unit.splitlines())

    def test_legacy_migration_stop_allows_sixty_seconds(self) -> None:
        migration = load_script("shutdown_migration", "scripts/migrate_opc_to_grassland.py")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "daemon.pid").write_text("424242")
            with patch.object(migration.os, "kill") as kill, patch.object(migration.time, "sleep") as sleep:
                migration.stop_daemon_if_running(home, dry=False)
            self.assertEqual(sleep.call_count, 60)
            self.assertEqual(kill.call_args.args, (424242, signal.SIGKILL))


if __name__ == "__main__":
    unittest.main()
