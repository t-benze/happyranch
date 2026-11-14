"""Real job terminal Pause checks in a synthetic org; Linux process venue."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import ctypes
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_agent_retirement import PREFIX, fixture
from runtime.daemon import jobs_runner
from runtime.daemon.routes import jobs
from runtime.models import BlockKind, JobInterpreter, JobRecord, TaskRecord, TaskStatus


@unittest.skipUnless(sys.platform == "linux", "Executed Linux /proc and subreaper cleanup venue")
class JobPauseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Test-only adoption allows finally to reap EVERY orphaned descendant.
        self.libc = ctypes.CDLL(None, use_errno=True)
        current = ctypes.c_int()
        self.assertEqual(self.libc.prctl(37, ctypes.byref(current), 0, 0, 0), 0)
        self.old_subreaper = current.value
        self.assertEqual(self.libc.prctl(36, 1, 0, 0, 0), 0)
        self.addCleanup(self.libc.prctl, 36, self.old_subreaper, 0, 0, 0)

    async def check_job(self, mode: str, *, failure: str | None = None, historical: bool = False) -> None:
        child_pid = None
        runner_task = None
        release = asyncio.Event()
        observed = asyncio.Event()
        results = []
        original_spawn = jobs._spawn_job

        async def spawn_and_hold_terminal(**kwargs):
            try:
                result = await original_spawn(**kwargs)  # REAL run_job, wait, streams and registration
            except BaseException:
                observed.set()  # Surface runner failure immediately, without a timeout wait.
                raise
            results.append(result)
            observed.set()
            await release.wait()  # Pause wins before the REAL terminal writer.
            return result

        child_code = (
            "import os,json,time; "
            + ("os.setpgid(0,0); " if mode == "setpgid" else "")
            + "info=json.dumps(dict(pid=os.getpid(),pgid=os.getpgrp(),sid=os.getsid(0))); "
            + "open('pause-job-child.json','w').write(info); print(info,flush=True); time.sleep(60)"
        )
        script = "import subprocess,sys; subprocess.run([sys.executable,'-c','pass'],check=True); print('{}')\n" if mode == "empty" else (
            "import subprocess,sys\n"
            f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}], stdin=subprocess.DEVNULL, "
            f"stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session={mode == 'setsid'!r})\n"
            "print(child.stdout.readline().decode(),end='',flush=True)\n"
            "child.stdout.close()\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, client):
                root_id, job_id = "TASK-901", "JOB-901"
                # Parked ordinary root; no task executor or provider is launched.
                org.db.insert_task(TaskRecord(id=root_id, brief="Synthetic job root",
                    assigned_agent="worker", team="retiring", status=TaskStatus.IN_PROGRESS,
                    block_kind=BlockKind.DELEGATED, current_session_id="sess-synthetic-job"))
                org.db.insert_job(JobRecord(id=job_id, task_id=root_id, agent_name="worker",
                    title="Synthetic process observation", rationale="Focused test", script_text=script,
                    interpreter=JobInterpreter.PYTHON3, created_at="2026-11-14T00:00:00+00:00"))
                registration = None
                try:
                    with patch.object(jobs, "_spawn_job", side_effect=spawn_and_hold_terminal):
                        launched = await jobs._run_job_core(org, job_id=job_id, cwd_override=None, timeout_override=None)
                        self.assertEqual(launched["status"], "running", launched)
                        registration = jobs_runner._RUNNER_TASKS[(org.db, job_id)]
                        runner_task = registration.task
                        await asyncio.wait_for(observed.wait(), timeout=10)
                        self.assertTrue(results, org.db.get_job(job_id))
                        descendant = json.loads(results[0].stdout_head)
                        child_pid = descendant.get("pid")
                        self.assertEqual(results[0].exit_code, 0, results[0].stderr_head)
                        self.assertIsNotNone(registration.process.returncode, "Leader must already be reaped")
                        original_pid = registration.process.pid
                        if child_pid is not None:
                            self.assertEqual(os.getpgid(child_pid), descendant["pgid"])
                            self.assertEqual(os.getsid(child_pid), descendant["sid"])
                            self.assertEqual(descendant["sid"] == original_pid, mode != "setsid")
                            self.assertEqual(descendant["pgid"] == original_pid, mode == "group")
                        paused = await client.post(f"{PREFIX}/tasks/{root_id}/pause", json={"expected_generation": 0})
                        self.assertEqual(paused.status_code, 200, paused.text)
                        self.assertEqual(org.db._task_pause_store.projection(root_id)["control_state"], "pausing")
                        if historical:
                            with org.db._task_pause_store.writer(root_id) as (_, row):
                                row["journal"]["entries"][0]["phase"] = "unknown"
                                org.db._task_pause_store.save_journal_uncommitted(row)
                        if failure == "missing_handle":
                            registration.original_group_session = None
                        fault = nullcontext()
                        if failure == "enumeration":
                            fault = patch.object(Path, "iterdir", side_effect=PermissionError("synthetic /proc refusal"))
                        elif failure == "eperm":
                            fault = patch.object(jobs_runner.os, "killpg", side_effect=PermissionError("synthetic EPERM"))
                        elif failure == "probe_exception":
                            fault = patch.object(jobs, "confirm_job_processes_empty", side_effect=RuntimeError("synthetic probe error"))
                        with fault:
                            release.set()
                            await asyncio.wait_for(asyncio.shield(runner_task), timeout=10)
                        terminal = org.db.get_job(job_id)
                        self.assertEqual(terminal.status.value, "completed")
                        self.assertEqual(terminal.exit_code, 0)
                        # Independent persisted control read drives the REAL pausing -> paused decision.
                        projection = org.db._task_pause_store.projection(root_id)
                        with org.db.coherent_read_view():
                            row = org.db._task_pause_store.row_uncommitted(root_id)
                        entries = [e for e in row["journal"]["entries"] if e["owner_identity"].get("job_id") == job_id]
                        closes = mode in {"empty", "setsid"} and failure is None and not historical
                        self.assertEqual(projection["control_state"], "paused" if closes else "pausing", projection)
                        self.assertEqual(projection["evidence_complete"], closes)
                        self.assertEqual([e["phase"] for e in entries], [] if closes else ["unknown"])
                        audits = org.db.execute("SELECT action,payload FROM audit_log WHERE task_id=? AND action IN "
                            "('job_run_completed','job_run_failed')", (root_id,)).fetchall()
                        self.assertEqual(len(audits), 1)
                        self.assertEqual(audits[0]["action"], "job_run_completed")
                        self.assertEqual(set(json.loads(audits[0]["payload"])), {"script_request_id", "exit_code", "duration_ms",
                            "stdout_bytes", "stderr_bytes", "truncated_stdout", "truncated_stderr"})
                finally:
                    release.set()
                    info_path = org.root / "workspaces" / "worker" / "pause-job-child.json"
                    if child_pid is None and info_path.exists():
                        child_pid = json.loads(info_path.read_text())["pid"]
                    if registration is not None and registration.process is not None:
                        proc = registration.process
                        if proc.returncode is None:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            await proc.wait()
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.waitpid(child_pid, 0)  # Adopted by this test, including setsid escapes.
                    if runner_task is not None:
                        if not runner_task.done():
                            runner_task.cancel()
                        await asyncio.gather(runner_task, return_exceptions=True)
                    await asyncio.sleep(0)  # Join registry done callback before fixture shutdown.

    async def test_all_exited_closes_entry_and_root_is_paused(self) -> None:
        await self.check_job("empty")

    async def test_live_descendant_in_original_group_stays_unknown(self) -> None:
        await self.check_job("group")

    async def test_setpgid_descendant_in_original_session_stays_unknown(self) -> None:
        await self.check_job("setpgid")

    async def test_unconfirmable_probe_stays_unknown(self) -> None:
        for failure in ("enumeration", "eperm", "probe_exception", "missing_handle"):
            with self.subTest(failure=failure):
                await self.check_job("empty", failure=failure)

    async def test_setsid_descendant_DOCUMENTING_THE_ACCEPTED_BLIND_SPOT(self) -> None:
        await self.check_job("setsid")

    async def test_historical_unknown_is_not_retroactively_closed(self) -> None:
        await self.check_job("empty", historical=True)


if __name__ == "__main__":
    unittest.main()
