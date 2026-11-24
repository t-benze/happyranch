"""Focused job receipt route/CLI checks using the existing no-launch ASGI venue."""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from cli import _shared
from cli.commands import jobs as cli_jobs
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus
from scripts.test_agent_retirement import PREFIX, fixture


class JobReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.org, self.client = await self.enterAsyncContext(fixture(Path(self.scratch.name) / "org"))
        for task_id in ("TASK-901", "TASK-902"):
            self.org.db.insert_task(TaskRecord(id=task_id, assigned_agent="worker", team="retiring",
                brief="Synthetic receipt owner", status=TaskStatus.IN_PROGRESS))
            self.org.sessions.set_active(task_id, "worker", f"sess-{task_id}")
        self.stdout = Path(self.scratch.name) / "job-stdout.txt"
        self.stdout.write_text("receipt-output\n")
        self.org.db.insert_job(JobRecord(id="JOB-901", task_id="TASK-901", agent_name="worker",
            title="Completed synthetic job", rationale="Synthetic regression check", script_text="printf 'receipt-output\\n'",
            interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED, exit_code=0,
            stdout_path=str(self.stdout), created_at="2026-11-24T00:00:00Z", finished_at="2026-11-24T00:00:01Z"))
        self.url = f"{PREFIX}/jobs/JOB-901/receipt"

    async def test_completed_own_job_accepts_current_and_resumed_live_session(self) -> None:
        for sid in ("sess-TASK-901", "sess-resumed"):
            with self.subTest(session=sid):
                self.org.sessions.set_active("TASK-901", "worker", sid)
                response = await self.client.get(self.url, params={"task_id": "TASK-901", "session_id": sid})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["authentication"], {"task_id": "TASK-901", "session_id": sid})
                self.assertEqual(response.json()["job"]["status"], "completed")
                self.assertEqual(response.json()["job"]["exit_code"], 0)
                self.assertEqual(response.json()["output"]["stdout"], "receipt-output\n")
        stale = await self.client.get(self.url, params={"task_id": "TASK-901", "session_id": "sess-TASK-901"})
        self.assertEqual(stale.status_code, 409, stale.text)

    async def test_binding_refusals_explain_expected_and_presented_pair(self) -> None:
        cases = [
            ("other-task", {"task_id": "TASK-902", "session_id": "sess-TASK-902"}, "job_task_mismatch"),
            ("missing-both", {}, "missing_binding"),
            ("missing-task", {"session_id": "sess-TASK-901"}, "missing_binding"),
            ("missing-session", {"task_id": "TASK-901"}, "missing_binding"),
            ("stale-session", {"task_id": "TASK-901", "session_id": "sess-ended"}, "session_mismatch"),
        ]
        for name, params, reason in cases:
            with self.subTest(case=name):
                response = await self.client.get(self.url, params=params)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], {
                    "code": "session_mismatch", "reason": reason,
                    "expected_task_id": "TASK-901",
                    "expected_session_id": "sess-TASK-901" if params.get("session_id") == "sess-TASK-901" else None,
                    "expected_session_state": "active",
                    "presented_task_id": params.get("task_id"), "presented_session_id": params.get("session_id"),
                })
        self.org.sessions.clear("TASK-901", "worker")
        response = await self.client.get(self.url, params={"task_id": "TASK-901", "session_id": "sess-ended"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], {
            "code": "session_mismatch", "reason": "no_active_session",
            "expected_task_id": "TASK-901", "expected_session_id": None,
            "expected_session_state": "inactive",
            "presented_task_id": "TASK-901", "presented_session_id": "sess-ended",
        })

    async def test_json_show_and_output_render_real_route_refusal(self) -> None:
        loop = asyncio.get_running_loop()

        def get(path: str, **kwargs: object) -> httpx.Response:
            return asyncio.run_coroutine_threadsafe(self.client.get(path, **kwargs), loop).result()

        def invoke(command: Callable[[argparse.Namespace], None]) -> tuple[int, str]:
            output = StringIO()
            args = argparse.Namespace(org="alpha", job_id="JOB-901", json=True,
                task_id="TASK-902", session_id="sess-TASK-902", stream="both", max_bytes=1024)
            with patch.object(cli_jobs.OpcClient, "from_env", return_value=SimpleNamespace(get=get)), redirect_stdout(output):
                try:
                    command(args)
                except SystemExit as exc:
                    return exc.code, output.getvalue()
            return 0, output.getvalue()

        for command in (cli_jobs.cmd_jobs_show, cli_jobs.cmd_jobs_output):
            with self.subTest(command=command.__name__):
                code, output = await asyncio.to_thread(invoke, command)
                self.assertEqual(code, 1, output)
                self.assertIn("job belongs to a different task", output)
                self.assertIn("expected task TASK-901, session (active session; identifier withheld)", output)
                self.assertIn("presented task TASK-902, session sess-TASK-902", output)
                self.assertNotIn("sess-TASK-901", output)
                self.assertNotIn("None", output)

    async def test_unbound_human_reads_remain_available(self) -> None:
        for suffix in ("", "/output"):
            response = await self.client.get(f"{PREFIX}/jobs/JOB-901{suffix}")
            self.assertEqual(response.status_code, 200, response.text)

    async def test_reassigned_task_keeps_refusing_receipts(self) -> None:
        params = {"task_id": "TASK-901", "session_id": "sess-TASK-901"}
        self.org.db.execute("UPDATE tasks SET assigned_agent = 'manager' WHERE id = 'TASK-901'")
        self.org.db.commit()
        response = await self.client.get(self.url, params=params)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"].get("reason"), "task_agent_mismatch")


class SessionErrorRenderingTests(unittest.TestCase):
    def test_missing_and_legacy_session_details_are_explicit(self) -> None:
        for detail, expected in (
            ({"code": "session_mismatch"}, "binding details unavailable"),
            ({"code": "session_mismatch", "active": None, "got": "sess-old"}, "no active session"),
            ({"code": "session_mismatch", "active": "sess-current", "got": "sess-old"}, "daemon expected sess-current but got sess-old"),
            ({"code": "session_mismatch", "reason": "no_active_session", "expected_task_id": "TASK-901",
              "expected_session_id": None, "presented_task_id": "TASK-901", "presented_session_id": "sess-old"},
             "expected task TASK-901, session (no active session)"),
        ):
            with self.subTest(detail=detail):
                output = StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                    _shared._ok(httpx.Response(409, json={"detail": detail}))
                self.assertEqual(raised.exception.code, 1)
                self.assertIn(expected, output.getvalue())
                self.assertNotIn("None", output.getvalue())


if __name__ == "__main__":
    unittest.main()
