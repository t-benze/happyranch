"""Focused recall projection checks, outside the retired Python suite."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus


class RecallTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "org.db")
        self.addCleanup(self.db.close)
        self.order = 0
        self.task("parent", agent="manager", parent=None)

    def task(self, ident: str, *, agent: str = "builder", parent: str | None = "parent",
             brief: str = "Build the slice", **fields: object) -> None:
        self.order += 1
        self.db.insert_task(TaskRecord(
            id=ident, assigned_agent=agent, parent_task_id=parent, brief=brief,
            task_type="subtask" if parent else "task", status=TaskStatus.COMPLETED,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.order),
            **fields,
        ))
        if "active_chain" in fields:
            self.db.update_task_active_chain(ident, fields["active_chain"])

    def audit(self, task: str, action: str, payload: dict) -> int:
        cursor = self.db._conn.execute(
            "INSERT INTO audit_log(task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
            (task, "manager", action, json.dumps(payload), "2026-01-01T00:00:00+00:00"),
        )
        self.db._conn.commit()
        return cursor.lastrowid

    def result(self, task: str) -> None:
        self.db.update_task(task, current_session_id="sess-own")
        self.db.insert_task_result(task, self.db.get_task(task).assigned_agent,
                                   "sess-own", "Own result", 90)

    def carrier(self, *, own_result: bool = True, launch: bool = True,
                mismatch: bool = False) -> None:
        self.task("carrier", active_chain=json.dumps({
            "step_index": 1, "step_audit_id": 0,
            "legs": [{"agent": "reviewer", "prompt": "Review the slice"}],
        }))
        self.task("first", parent="carrier")
        self.task("second", parent="carrier", agent="other" if mismatch else "reviewer",
                  brief="Review the slice")
        if launch:
            self.audit("parent", "fanout_spawned", {"children_ids": ["carrier"]})
        if own_result:
            self.result("carrier")

    def track(self) -> dict | None:
        return self.db.get_recall_payload("carrier")["track"]

    def test_fanout_with_own_session_and_result(self) -> None:
        self.carrier()
        self.assertEqual(self.track(), {"planned_agents": ["builder", "reviewer"],
                                       "step_index": 1, "plan_source": "active_chain"})

    def test_fanout_without_own_result_unchanged(self) -> None:
        self.carrier(own_result=False)
        self.assertEqual(self.track(), {"planned_agents": ["builder", "reviewer"],
                                       "step_index": 1, "plan_source": "active_chain"})

    def test_delegate_then_with_own_result(self) -> None:
        decision = {"action": "delegate", "agent": "builder", "prompt": "Implement",
                    "then": [{"agent": "reviewer", "prompt": "Review"}]}
        self.task("carrier", agent="manager", brief="Coordinate")
        aid = self.audit("carrier", "orchestration_step", {"decision": decision})
        self.db.update_task_active_chain("carrier", json.dumps({
            "step_index": 0, "step_audit_id": aid, "legs": decision["then"],
        }))
        self.task("first", parent="carrier", brief="Implement")
        self.result("carrier")
        self.assertEqual(self.track(), {"planned_agents": ["builder", "reviewer"],
                                       "step_index": 0, "plan_source": "active_chain"})

    def test_ordinary_session_and_children_without_chain(self) -> None:
        self.task("carrier")
        self.task("first", parent="carrier")
        self.result("carrier")
        self.assertIsNone(self.track())

    def test_chain_mismatch(self) -> None:
        self.carrier(mismatch=True)
        self.assertIsNone(self.track())
        self.db._conn.execute("DELETE FROM task_results WHERE task_id = 'carrier'")
        self.db._conn.commit()
        self.assertEqual(self.track(), {"planned_agents": None, "step_index": None,
                                       "plan_source": None})

    def test_chain_without_launch_provenance(self) -> None:
        self.carrier(launch=False)
        self.assertIsNone(self.track())

    def test_fanout_launch_bound(self) -> None:
        self.carrier()
        for i in range(63):
            self.audit("parent", "fanout_spawned", {"children_ids": [f"other-{i}"]})
        self.assertEqual(self.track(), {"planned_agents": ["builder", "reviewer"],
                                       "step_index": 1, "plan_source": "active_chain"})
        self.audit("parent", "fanout_spawned", {"children_ids": ["overflow"]})
        self.assertIsNone(self.track())

    def test_ended_fanout_launch_history_unchanged(self) -> None:
        self.carrier(own_result=False)
        self.db.update_task_active_chain("carrier", None)
        self.audit("parent", "orchestration_step", {"decision": {
            "action": "fanout", "children": [{"agent": "builder", "prompt": "Build the slice",
                "then": [{"agent": "reviewer", "prompt": "Review the slice"}]}],
        }})
        self.assertEqual(self.track(), {"planned_agents": ["builder", "reviewer"],
                                       "step_index": None, "plan_source": "launch_history"})


if __name__ == "__main__":
    unittest.main()
