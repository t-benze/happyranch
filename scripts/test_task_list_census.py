"""Task-list budget and response checks in the existing no-launch ASGI venue."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from scripts.test_agent_retirement import PREFIX, fixture
from runtime.daemon.org_state import OrgState
from runtime.infrastructure.memory_collection import _census_budget
from runtime.models import BlockKind, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


STAMP = "2026-01-02T03:04:05+00:00"
EXPECTED = Path(__file__).with_name("fixtures") / "task_list_census.json"


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        return datetime.fromisoformat(STAMP)


def seed(org: OrgState, task_id: str, *, parent: str | None = None,
         status: TaskStatus = TaskStatus.COMPLETED, block: BlockKind | None = None,
         payload: str = "Synthetic brief — 保留") -> None:
    org.db.insert_task(TaskRecord(
        id=task_id, parent_task_id=parent, assigned_agent="worker", team="retiring",
        brief=payload, note="Synthetic note — 保留" if len(payload) < 100 else payload,
        status=status, block_kind=block, created_at=STAMP, updated_at=STAMP,
    ))


def representative_tree(org: OrgState) -> None:
    seed(org, "TASK-001", status=TaskStatus.IN_PROGRESS, block=BlockKind.DELEGATED)
    seed(org, "TASK-002", parent="TASK-001", status=TaskStatus.PENDING)
    seed(org, "TASK-003", parent="TASK-001")
    seed(org, "TASK-004", parent="TASK-003", status=TaskStatus.IN_PROGRESS)
    seed(org, "TASK-005", status=TaskStatus.CANCELLED)
    org.db.insert_job(JobRecord(
        id="JOB-001", task_id="TASK-003", agent_name="worker", title="Synthetic job",
        rationale="Read-only observation", script_text="pass", interpreter=JobInterpreter.PYTHON3,
        status=JobStatus.RUNNING, created_at=STAMP,
    ))
    org.db._task_pause_store.control("TASK-001", held=True, expected_generation=0,
                                     actor="founder", sessions=org.sessions)


class TaskListCensusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.org, self.client = await self.enterAsyncContext(fixture(Path(temp.name) / "org"))
        self.enterContext(patch("runtime.infrastructure.task_pause_controls.now", return_value=STAMP))
        self.enterContext(patch("runtime.orchestrator.task_wait_readers.datetime", FrozenDatetime))

    async def response(self, **params: str | int) -> httpx.Response:
        return await self.client.get(f"{PREFIX}/tasks",
                                     params={"assigned_agent": "worker", "limit": 1000, **params})

    async def test_large_sibling_page_keeps_payloads_and_fits_budget(self) -> None:
        payload = "synthetic-" * 6000
        seed(self.org, "TASK-000", payload=payload)
        for index in range(1, 41):
            seed(self.org, f"TASK-{index:03}", parent="TASK-000", payload=payload)
        with _census_budget() as budget:
            response = await self.response()
        self.assertEqual(response.status_code, 200, response.text[:200])
        tasks = response.json()["tasks"]
        self.assertEqual({row["task_id"] for row in tasks}, {f"TASK-{i:03}" for i in range(41)})
        self.assertTrue(all(row["brief"] == payload and row["note"] == payload for row in tasks))
        self.assertLess(budget.sql_bytes, 64 * 1024 * 1024)
        self.assertLess(budget.sql_rows, 100000)
        self.assertIsNone(response.json()["next_cursor"])

    async def test_public_tree_response_matches_pre_fix_bytes(self) -> None:
        representative_tree(self.org)
        response = await self.response()
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.content, EXPECTED.read_bytes())

    async def test_unknown_facts_refresh_between_requests(self) -> None:
        representative_tree(self.org)
        first = await self.response()
        self.assertEqual(first.status_code, 200)
        original = first.json()["tasks"]
        tree = [row["pause"] for row in original if row["task_id"] != "TASK-005"]
        self.assertTrue(all(value == tree[0] for value in tree))
        self.assertFalse(tree[0]["evidence_complete"])
        self.assertTrue(any(b["reason"] == "execution_evidence_unavailable" for b in tree[0]["blockers"]))
        self.org.db.update_task("TASK-004", status=TaskStatus.COMPLETED)
        second = await self.response()
        self.assertEqual(second.status_code, 200)
        changed = next(row["pause"] for row in second.json()["tasks"] if row["task_id"] == "TASK-001")
        self.assertTrue(changed["evidence_complete"])
        self.assertFalse(any(b["reason"] == "execution_evidence_unavailable" for b in changed["blockers"]))
        self.assertTrue(any(b["job_id"] == "JOB-001" for b in changed["blockers"]))

    async def test_cyclic_ancestry_retains_error(self) -> None:
        seed(self.org, "TASK-001")
        seed(self.org, "TASK-002", parent="TASK-001")
        self.org.db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-002", "TASK-001"))
        self.org.db._conn.commit()
        response = await self.response()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "pause_control_unavailable")


if __name__ == "__main__":
    unittest.main()
