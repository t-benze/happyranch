from __future__ import annotations

import json
import time
from pathlib import Path
from textwrap import dedent

import httpx
import pytest

from src.infrastructure.database import Database
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _register_runtime(base: str, runtime: Path) -> None:
    r = httpx.post(
        f"{base}/runtimes/register",
        json={"path": str(runtime)},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text


def _write_agent_config(runtime: Path, agent: str, executor: str) -> None:
    workspace = runtime / "workspaces" / agent
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "agent.yaml").write_text(f"repos: {{}}\nexecutor: {executor}\n")


def _write_plan(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -e\n" + dedent(body).lstrip())
    path.chmod(0o755)


def _init_agent(base: str, agent: str, headers: dict) -> None:
    with httpx.stream(
        "POST",
        f"{base}/agents/init",
        json={"agent": agent},
        headers=headers,
        timeout=30.0,
    ) as stream:
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            if event.get("phase") == "error":
                raise AssertionError(event.get("detail") or f"agent init failed: {event}")
            if event.get("phase") == "all_done":
                return
    raise AssertionError(f"agent init did not complete for {agent}")


def _wait_for_terminal_status(
    base: str,
    task_id: str,
    headers: dict | None = None,
    timeout: float = 30.0,
    *,
    allow_blocked: bool = False,
) -> str:
    """Poll /tasks/{id} until a terminal state is reached.

    `blocked(DELEGATED)` is not terminal for normal end-to-end flows because the
    parent should resume after the child finishes. Tests that explicitly assert
    a delegated block, such as mixed-fleet handoff coverage, can opt into
    `allow_blocked=True`.
    """
    headers = headers or _auth_headers()
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
        body = r.json()
        task = body["task"]
        status = task["status"]
        block_kind = task.get("block_kind")
        if status in ("completed", "failed"):
            return status
        if status == "blocked" and (allow_blocked or block_kind == "escalated"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} did not reach a terminal state (last body={body})")


def _submit_task(base: str, brief: str = "smoke", headers: dict | None = None) -> str:
    headers = headers or _auth_headers()
    r = httpx.post(
        f"{base}/tasks",
        json={"type": "general", "brief": brief},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def test_register_and_run_completes_via_callback(
    live_daemon,
    runtime,
    fake_plan_env,
):
    """Regression guard for session-tracker wiring on the Claude path."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base)
    assert _wait_for_terminal_status(base, task_id, timeout=20.0) == "completed"

    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "completed"
    assert any(res["session_id"] for res in body["results"])
    assert body["task"].get("note") != "agent session failed"


def test_completion_callback_rejected_when_session_unknown(
    live_daemon,
    runtime,
    fake_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    fake_plan_env.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_plan_env.chmod(0o755)

    task_id = "TASK-fake"
    r = httpx.post(
        f"{base}/tasks/{task_id}/completion",
        json={
            "session_id": "sess-fabricated",
            "agent": "engineering_head",
            "status": "completed",
            "confidence": 99,
            "output_summary": "{}",
        },
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_session"
    assert detail["task_id"] == task_id
    assert detail["agent"] == "engineering_head"


def test_delegate_and_resume_roundtrip(
    live_daemon,
    runtime,
    fake_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    marker = fake_plan_env.parent / "eh_called_once"
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3\n'
        f'marker="{marker}"\n'
        'if [[ "$agent" == "engineering_head" ]]; then\n'
        '  if [[ -f "$marker" ]]; then\n'
        '    summary=\'{"action":"done","summary":"all done"}\'\n'
        '  else\n'
        '    touch "$marker"\n'
        '    summary=\'{"action":"delegate","agent":"dev_agent","prompt":"do the thing"}\'\n'
        '  fi\n'
        'else\n'
        '  summary="dev_agent finished"\n'
        'fi\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        '  --summary "$summary"\n'
    )
    fake_plan_env.chmod(0o755)

    seed_workspace(runtime, "engineering_head")
    seed_workspace(runtime, "dev_agent")

    task_id = _submit_task(base, brief="delegate me")
    assert _wait_for_terminal_status(base, task_id, timeout=30.0) == "completed"

    r = httpx.get(f"{base}/tasks", headers=_auth_headers(), timeout=5.0)
    tasks_list = r.json()["tasks"]
    children = [t for t in tasks_list if t.get("parent_task_id") == task_id]
    assert len(children) == 1, tasks_list
    child = children[0]
    assert child["assigned_agent"] == "dev_agent"
    assert child["status"] == "completed"

    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    audit = r.json()["audit_log"]
    delegate_steps = [
        a
        for a in audit
        if a.get("action") == "orchestration_step"
        and ((a.get("payload") or {}).get("decision") or {}).get("action") == "delegate"
    ]
    assert delegate_steps, audit


def test_idle_daemon_starts_workers_after_register(
    live_daemon_idle,
    runtime,
    fake_plan_env,
):
    port = live_daemon_idle
    base = f"http://127.0.0.1:{port}/api/v1"

    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 80 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    _register_runtime(base, runtime)
    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base, brief="post-register smoke")
    assert _wait_for_terminal_status(base, task_id, timeout=10.0) == "completed"


def test_register_and_run_completes_via_codex_callback(
    live_daemon,
    runtime,
    fake_codex_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"
    headers = _auth_headers()

    _write_agent_config(runtime, "engineering_head", "codex")
    _init_agent(base, "engineering_head", headers)

    _write_plan(
        fake_codex_plan_env,
        """
        task_id=$1
        session_id=$2
        opc report-completion \
          --task-id "$task_id" --session-id "$session_id" \
          --agent engineering_head --status completed --confidence 90 \
          --summary '{"action":"done","summary":"codex ok"}'
        """,
    )

    task_id = _submit_task(base, brief="codex smoke", headers=headers)
    outcome = _wait_for_terminal_status(base, task_id, headers=headers)
    assert outcome == "completed"

    r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "completed"
    assert body["task"]["assigned_agent"] == "engineering_head"
    assert any(res["session_id"] for res in body["results"])


def test_mixed_fleet_roundtrip_uses_claude_and_codex(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_codex_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"
    headers = _auth_headers()

    _write_agent_config(runtime, "engineering_head", "claude")
    _write_agent_config(runtime, "dev_agent", "codex")
    _init_agent(base, "engineering_head", headers)
    _init_agent(base, "dev_agent", headers)

    _write_plan(
        fake_claude_plan_env,
        """
        task_id=$1
        session_id=$2
        state_file="${FAKE_CLAUDE_PLAN}.seen.${task_id}"
        if [[ ! -f "$state_file" ]]; then
            touch "$state_file"
            opc report-completion \
              --task-id "$task_id" --session-id "$session_id" \
              --agent engineering_head --status completed --confidence 90 \
              --summary '{"action":"delegate","agent":"dev_agent","prompt":"build the follow-up"}'
        else
            opc report-completion \
              --task-id "$task_id" --session-id "$session_id" \
              --agent engineering_head --status completed --confidence 90 \
              --summary '{"action":"done","summary":"parent done"}'
        fi
        """,
    )
    _write_plan(
        fake_codex_plan_env,
        """
        task_id=$1
        session_id=$2
        opc report-completion \
          --task-id "$task_id" --session-id "$session_id" \
          --agent dev_agent --status completed --confidence 90 \
          --summary '{"action":"done","summary":"child done"}'
        """,
    )

    task_id = _submit_task(base, brief="mixed fleet smoke", headers=headers)
    outcome = _wait_for_terminal_status(
        base, task_id, headers=headers, allow_blocked=True,
    )
    assert outcome == "blocked"

    db = Database(runtime / "opc.db")
    root = db.get_task(task_id)
    children = db.get_children(task_id)
    assert root is not None
    assert root.status.value == "blocked"
    assert root.block_kind.value == "delegated"
    assert len(children) == 1

    child = db.get_task(children[0])
    assert child is not None
    assert child.assigned_agent == "dev_agent"
    assert child.status.value == "in_progress"

    child_audit = db.get_audit_logs(child.id)
    assert any(
        entry["action"] == "session_start" and entry["agent"] == "dev_agent"
        for entry in child_audit
    )
