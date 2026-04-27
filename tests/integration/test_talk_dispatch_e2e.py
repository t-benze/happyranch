"""End-to-end: agent in a talk dispatches a task; orchestrator runs it.

Mirrors the bootstrap + lifecycle pattern of ``test_talk_flow_e2e.py``
and ``test_end_to_end.py``. The dispatch HTTP call simulates the agent
calling ``opc dispatch`` from inside an open talk; the daemon enqueues
the new task and the worker pool drives it under fake-claude until it
reaches a terminal status.

Conftest surprise notes:
- The integration conftest exposes ``live_daemon`` (port string) and
  ``runtime`` (Path) — there is no ``integration_daemon`` /
  ``runtime_path`` pair as the plan skeleton suggested.
- Tests talk to the daemon via raw ``httpx`` + a small ``_auth_headers``
  helper that reads the bearer token from ``src.daemon.paths``.
- Workspace bootstrap is done by the file-local ``seed_workspace``
  helper from ``tests/integration/conftest.py``; we reuse it for
  ``dev_agent`` here.
- The dispatch endpoint requires the target agent to have an
  ``approved`` enrollment row, which the conftest does not seed. We
  insert it directly via the SQLite ``Database`` wrapper — same pattern
  ``test_end_to_end.py::test_mixed_fleet_roundtrip_uses_claude_and_codex``
  uses to read state out of band.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.infrastructure.database import Database
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1"


def _seed_approved_enrollment(runtime: Path, name: str) -> None:
    """Seed an approved enrollment row so the dispatch endpoint accepts the target.

    The dispatch endpoint's step 6 requires both an approved enrollment
    and a workspace directory — ``seed_workspace`` covers the latter.
    """
    db = Database(runtime / "opc.db")
    db.insert_enrollment(
        name=name,
        description=name,
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
    )
    db.update_enrollment_status(name, "approved")


def _wait_for_terminal_status(
    base: str,
    task_id: str,
    headers: dict,
    timeout: float = 30.0,
) -> str:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
        if r.status_code == 200:
            last = r.json()
            status = last["task"]["status"]
            if status in ("completed", "failed", "blocked"):
                return status
        time.sleep(0.2)
    raise AssertionError(
        f"task {task_id} did not reach a terminal status within {timeout}s "
        f"(last body={last})"
    )


def test_worker_self_dispatch_runs_to_completion(
    live_daemon,
    runtime: Path,
    fake_plan_env: Path,
) -> None:
    """A worker opens a talk, dispatches a self-task, and the orchestrator
    runs it under fake-claude to a terminal status. The resulting task
    must record the dispatch lineage on the row."""
    port = live_daemon
    base = _base(port)
    headers = _auth_headers()

    # Plan: every fake-claude invocation reports completion. dev_agent's
    # summary doesn't need to be a NextStep JSON object — workers report
    # plain prose. We hard-code the agent name in the callback because
    # this test only ever runs dev_agent.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        '  --summary "dispatched task done"\n'
    )
    fake_plan_env.chmod(0o755)

    # dev_agent needs both a workspace (with SKILL marker) and an
    # approved enrollment row for the dispatch endpoint to accept it.
    seed_workspace(runtime, "dev_agent")
    _seed_approved_enrollment(runtime, "dev_agent")

    # 1. Open a talk for dev_agent.
    r = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    talk_id = r.json()["talk_id"]
    assert talk_id.startswith("TALK-")

    # 2. Dispatch a worker self-task from inside the talk.
    r = httpx.post(
        f"{base}/talks/{talk_id}/dispatch",
        json={"brief": "fake-claude work item"},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    dispatch_resp = r.json()
    task_id = dispatch_resp["task_id"]
    assert task_id.startswith("TASK-")
    assert dispatch_resp["assigned_agent"] == "dev_agent"
    assert dispatch_resp["team"] == "engineering"
    assert dispatch_resp["dispatched_from_talk_id"] == talk_id

    # 3. Worker pool picks it up; fake-claude calls report-completion;
    # the task lands at a terminal status within 30s.
    final_status = _wait_for_terminal_status(base, task_id, headers, timeout=30.0)
    assert final_status == "completed", final_status

    # 4. The task row carries the dispatch lineage exactly.
    r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["dispatched_from_talk_id"] == talk_id
    assert task["assigned_agent"] == "dev_agent"
    assert task["parent_task_id"] is None
