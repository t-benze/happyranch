"""Independent read-only durable expectations. Never import product writers."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def require(condition: bool, message: str, observed: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{message}; observed={observed!r}")


class Store:
    def __init__(self, root: Path, org: str) -> None:
        self.path = root / "runtime" / "orgs" / org / "happyranch.db"

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        # Reopen on EVERY observation. immutable=1 would hide a live WAL.
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            return [dict(row) for row in db.execute(sql, args)]

    def snapshot(self, task: str | None = None) -> dict:
        where = "" if task is None else " WHERE id=? OR parent_task_id=?"
        tasks = self.query("SELECT * FROM tasks" + where + " ORDER BY id",
                           () if task is None else (task, task))
        ids = [row["id"] for row in tasks]
        marks = ",".join("?" for _ in ids) or "NULL"
        return {
            "tasks": tasks,
            "results": self.query(f"SELECT * FROM task_results WHERE task_id IN ({marks}) ORDER BY id", tuple(ids)),
            "audit": self.query(f"SELECT * FROM audit_log WHERE task_id IN ({marks}) ORDER BY id", tuple(ids)),
        }

    def ordinary(self) -> None:
        selectors = self.query("SELECT team,family FROM authority_policy_active_selector")
        require(selectors == [{"team": "engineering", "family": "empty"}],
                "fresh public org must use empty ordinary policy", selectors)
        require(not self.query("SELECT * FROM authority_policy_v2_session_bindings"),
                "unexpected v2 session binding")

    def result(self, payload: dict) -> dict:
        rows = self.query("SELECT * FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                          (payload["task_id"], payload["agent"], payload["session_id"]))
        require(len(rows) == 1, "exactly one committed result per invocation", rows)
        row = rows[0]
        require(row["status"] == "completed" and row["output_summary"] == payload["summary"],
                "committed original result retained", row)
        decision = json.loads(row["decision_json"]) if row["decision_json"] else None
        require(decision == payload.get("decision"), "exact accepted decision", decision)
        require(row["confidence_score"] == 90, "confidence persisted", row)
        return row

    def finished(self, root: str, payloads: list[dict], starts: list[dict], *, child: str | None = None) -> dict:
        snapshot = self.snapshot(root)
        tasks = {row["id"]: row for row in snapshot["tasks"]}
        expected_ids = {root} if child is None else {root, child}
        require(set(tasks) == expected_ids, "exact task closure", tasks)
        require(len(snapshot["results"]) == len(payloads), "exact result count", snapshot["results"])
        for payload in payloads:
            self.result(payload)
        for task_id, row in tasks.items():
            require(row["status"] == "completed" and row["completed_at"] is not None,
                    "task completed durably", row)
            for key in ("block_kind", "active_chain", "active_fanout"):
                require(row[key] is None, f"terminal {key} cleared", row[key])
            require(row["assigned_agent"] == ("case_manager" if task_id == root else "case_worker"),
                    "task owner retained", row)
            latest = next(p for p in reversed(payloads) if p["task_id"] == task_id)
            require(row["current_session_id"] == latest["session_id"], "durable current generation", row)
        require(tasks[root]["parent_task_id"] is None, "root linkage", tasks[root])
        require(tasks[root]["orchestration_step_count"] == (2 if child else 1), "decision step count", tasks[root])
        if child:
            require(tasks[child]["parent_task_id"] == root, "child linkage", tasks[child])
        expected_sessions = [p["session_id"] for p in payloads]
        require([s["session"] for s in starts] == expected_sessions, "actual launch order", starts)
        audit = snapshot["audit"]
        by_action = {key: [r for r in audit if r["action"] == key]
                     for key in ("session_start", "session_end", "completion_report", "orchestration_step", "chain_auto_advance")}
        for key in ("session_start", "session_end", "completion_report"):
            require(len(by_action[key]) == len(payloads), f"exact {key} count", by_action[key])
        require(not by_action["chain_auto_advance"], "no ordinary chain advance", by_action)
        require([json.loads(r["payload"])["decision"]["action"] for r in by_action["orchestration_step"]]
                == (["delegate", "done"] if child else ["done"]), "consumed decision order", by_action)
        for index, payload in enumerate(payloads):
            start, end, completion = (by_action[key][index] for key in ("session_start", "session_end", "completion_report"))
            require(json.loads(start["payload"])["session_id"] == payload["session_id"], "intended/actual session join", start)
            require(start["task_id"] == end["task_id"] == completion["task_id"] == payload["task_id"], "audit task join")
            require(start["id"] < end["id"] < completion["id"], "start/end/consumption order", [start, end, completion])
        if child:
            require(by_action["orchestration_step"][0]["id"] < by_action["session_start"][1]["id"], "delegate before worker")
            require(by_action["completion_report"][1]["id"] < by_action["session_start"][2]["id"], "worker completion before natural M2")
        return snapshot
