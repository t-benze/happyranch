"""Focused no-launch Pause/Resume checks; see docs/local-ci.md."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_agent_retirement import PREFIX, fixture
from runtime.daemon.org_state import OrgState
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.task_pause import discover_pause_work


def seed(org: OrgState, *, ended: int = 40) -> str:
    """Put historical controls before the actual held root in traversal order."""
    statuses = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED)
    for index in range(ended):
        task_id = f"TASK-A{index:03}"
        org.db.insert_task(TaskRecord(id=task_id, brief="Synthetic ended root", status=statuses[index % 4]))
        with org.db._task_pause_store.writer(task_id):
            org.db._task_pause_store.ensure_uncommitted(task_id)
    root_id = "TASK-Zroot"
    org.db.insert_task(TaskRecord(id=root_id, brief="Synthetic root", status=TaskStatus.IN_PROGRESS,
                                 assigned_agent="worker", team="retiring"))
    org.db.insert_task(TaskRecord(id="TASK-Zchild", parent_task_id=root_id, brief="Synthetic held child",
                                 assigned_agent="worker", team="retiring"))
    org.db._task_pause_store.control(root_id, held=True, expected_generation=0,
                                     actor="founder", sessions=org.sessions)
    return root_id


def release_flag(org: OrgState, root_id: str) -> int | None:
    with org.db.coherent_read_view():
        return org.db._task_pause_store.row_uncommitted(root_id)["release_pending_generation"]


def queued(org: OrgState) -> list[tuple]:
    queue = org.orchestrator._queue._queue
    result = []
    while not queue.empty():
        result.append(queue.get_nowait())
        queue.task_done()
    return result


class TaskPauseResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_releases_own_root_without_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, client):
                root_id = seed(org)
                with patch("runtime.orchestrator.task_pause.discover_pause_work", new_callable=AsyncMock) as sweep:
                    response = await client.post(f"{PREFIX}/tasks/{root_id}/resume", json={"expected_generation": 1})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["changed"])
                self.assertEqual(queued(org), [(org.slug, "TASK-Zchild", None)])
                self.assertIsNone(release_flag(org, root_id))
                sweep.assert_not_awaited()
                duplicate = await client.post(f"{PREFIX}/tasks/{root_id}/resume", json={"expected_generation": 1})
                self.assertEqual(duplicate.status_code, 200, duplicate.text)
                self.assertFalse(duplicate.json()["changed"])
                self.assertEqual(queued(org), [])

    async def test_sweep_skips_ended_roots_without_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, _):
                root_id = seed(org)
                org.db._task_pause_store.control(root_id, held=False, expected_generation=1,
                                                 actor="founder", sessions=org.sessions)
                await discover_pause_work(org, org.orchestrator._queue)
                self.assertEqual(queued(org), [(org.slug, "TASK-Zchild", None)])
                self.assertIsNone(release_flag(org, root_id))
                self.assertEqual(org.db._task_pause_store._discovery_cursor, root_id)
                self.assertEqual(org.db.execute("SELECT COUNT(*) FROM task_pause_controls").fetchone()[0], 41)

    async def test_sweep_keeps_ended_root_with_pending_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, _):
                root_id = seed(org, ended=0)
                org.db._task_pause_store.control(root_id, held=False, expected_generation=1,
                                                 actor="founder", sessions=org.sessions)
                org.db.update_task(root_id, status=TaskStatus.COMPLETED)
                await discover_pause_work(org, org.orchestrator._queue)
                self.assertEqual(queued(org), [(org.slug, "TASK-Zchild", None)])
                self.assertIsNone(release_flag(org, root_id))

    async def test_failed_direct_release_retains_sweep_backstop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            async with fixture(Path(temp) / "org") as (org, client):
                root_id = seed(org, ended=0)
                client._transport.raise_app_exceptions = False
                with patch.object(org.orchestrator._queue, "enqueue", side_effect=RuntimeError("synthetic queue failure")):
                    response = await client.post(f"{PREFIX}/tasks/{root_id}/resume", json={"expected_generation": 1})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["changed"])
                self.assertEqual(release_flag(org, root_id), 2)
                await discover_pause_work(org, org.orchestrator._queue)
                self.assertEqual(queued(org), [(org.slug, "TASK-Zchild", None)])
                self.assertIsNone(release_flag(org, root_id))


if __name__ == "__main__":
    unittest.main()
