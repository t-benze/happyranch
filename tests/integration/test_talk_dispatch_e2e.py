"""End-to-end: agent in a talk dispatches a task; orchestrator runs it.

Mirrors the bootstrap + lifecycle pattern of ``test_talk_flow_e2e.py``
and ``test_end_to_end.py``. The dispatch HTTP call simulates the agent
calling ``grassland dispatch`` from inside an open talk; the daemon enqueues
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
- The dispatch endpoint requires the target agent to be active under
  ``<runtime>/org/agents/<name>.md`` (via ``prompt_loader.load_agent``)
  AND to have a workspace dir. We write the agent file directly via
  ``render_agent_text`` — same pattern as ``tests/daemon/test_talks_dispatch.py``.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.orchestrator.agent_def import AgentDef, render_agent_text
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1/orgs/test"


def _seed_active_agent(runtime: Path, name: str) -> None:
    """Write an active agent file so the dispatch endpoint accepts the target.

    ``seed_workspace`` covers the workspace dir; this covers the agent
    file the dispatch route consults via ``prompt_loader.load_agent``.
    The file's ``team`` field is a stub — dispatch reads team membership
    from TeamsRegistry (seeded by conftest's teams.yaml).
    """
    agents_dir = runtime / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(
        name=name,
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt="x\n",
        description=name,
    )
    (agents_dir / f"{name}.md").write_text(render_agent_text(agent))


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
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'grassland report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        '  --summary "dispatched task done"\n'
    )
    fake_plan_env.chmod(0o755)

    # dev_agent needs both a workspace (with SKILL marker) and an
    # active agent file for the dispatch endpoint to accept it.
    seed_workspace(runtime, "dev_agent")
    _seed_active_agent(runtime, "dev_agent")

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
