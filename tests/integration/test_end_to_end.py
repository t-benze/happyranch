from __future__ import annotations

import json
import time
from pathlib import Path
from textwrap import dedent

import httpx
import pytest

from runtime.infrastructure.database import Database
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from runtime.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _register_runtime(global_base: str, container: Path) -> None:
    """POST /api/v1/runtime to register a runtime container with the daemon.

    *global_base* must be the unprefixed API root
    (``http://.../api/v1``) — ``/runtime`` is a singleton, not org-scoped.
    """
    r = httpx.post(
        f"{global_base}/runtime",
        json={"path": str(container)},
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
        if status in ("completed", "failed", "cancelled"):
            return status
        # Path B: escalated is the top-level await-founder status (legacy
        # Path B: escalated is a top-level status.
        if status == "escalated":
            return status
        # Parked carriers (delegated / blocked_on_job) are in_progress(kind)
        # under Path B. Opt-in via allow_blocked.
        if (allow_blocked
                and block_kind in ("delegated", "blocked_on_job")
                and status == "in_progress"):
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
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'happyranch report-completion --org "$org_slug" \\\n'
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
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

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
    # Task validation runs before session validation; a fabricated task_id
    # returns 404 unknown_task and never reaches the session check.
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_task"
    assert detail["task_id"] == task_id


def test_delegate_and_resume_roundtrip(
    live_daemon,
    runtime,
    fake_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    marker = fake_plan_env.parent / "eh_called_once"
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
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
        'happyranch report-completion --org "$org_slug" \\\n'
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
    runtime_container,
    runtime,
    fake_plan_env,
):
    port = live_daemon_idle
    global_base = f"http://127.0.0.1:{port}/api/v1"
    base = f"{global_base}/orgs/test"

    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 80 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    _register_runtime(global_base, runtime_container)
    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base, brief="post-register smoke")
    assert _wait_for_terminal_status(base, task_id, timeout=10.0) == "completed"


def test_register_and_run_completes_via_codex_callback(
    live_daemon,
    runtime,
    fake_codex_plan_env,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    headers = _auth_headers()

    _write_agent_config(runtime, "engineering_head", "codex")
    _init_agent(base, "engineering_head", headers)

    _write_plan(
        fake_codex_plan_env,
        """
        task_id=$1
        session_id=$2
        org_slug=$3
        happyranch report-completion --org "$org_slug" \
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
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
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
        agent=$3
        org_slug=$4
        state_file="${FAKE_CLAUDE_PLAN}.seen.${task_id}"
        if [[ ! -f "$state_file" ]]; then
            touch "$state_file"
            happyranch report-completion --org "$org_slug" \
              --task-id "$task_id" --session-id "$session_id" \
              --agent engineering_head --status completed --confidence 90 \
              --summary '{"action":"delegate","agent":"dev_agent","prompt":"build the follow-up"}'
        else
            happyranch report-completion --org "$org_slug" \
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
        org_slug=$3
        happyranch report-completion --org "$org_slug" \
          --task-id "$task_id" --session-id "$session_id" \
          --agent dev_agent --status completed --confidence 90 \
          --summary '{"action":"done","summary":"child done"}'
        """,
    )

    task_id = _submit_task(base, brief="mixed fleet smoke", headers=headers)
    outcome = _wait_for_terminal_status(
        base, task_id, headers=headers, allow_blocked=True,
    )
    assert outcome == "in_progress"  # Path B: delegating parent is in_progress(delegated)

    db = Database(runtime / "happyranch.db")
    root = db.get_task(task_id)
    children = db.get_children(task_id)
    assert root is not None
    assert root.status.value == "in_progress"
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


def test_cancel_sigterms_running_subprocess_and_marks_task_failed(
    live_daemon, runtime, fake_plan_env
):
    """End-to-end cancel: submit a task, let fake_claude hang (sleep), hit
    /tasks/{id}/cancel while the subprocess is alive, and verify:
      1. The route returns ok with a killed pid list.
      2. The task row ends up status=failed, note='cancelled by founder: ...',
         cancelled_at populated.
      3. The post-Popen classifier's stray `session failed rc=-15` note does
         NOT overwrite the founder's note (idempotence guard in _fail).
    """
    import time as _time

    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    # Plan: sleep 10s. We'll cancel within that window. When the subprocess
    # gets SIGTERM'd, `set -e` causes a non-zero exit; run_step's classifier
    # then tries to write "agent session failed rc=-15" — but our idempotence
    # guard must stop it.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'sleep 10\n'
    )
    fake_plan_env.chmod(0o755)

    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base, brief="cancel me")

    # Wait for the subprocess to actually start (status flips to in_progress).
    deadline = _time.monotonic() + 5.0
    while _time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=2.0)
        if r.json()["task"]["status"] == "in_progress":
            break
        _time.sleep(0.1)
    else:
        raise AssertionError("task never reached in_progress before cancel")

    # Cancel it.
    r = httpx.post(
        f"{base}/tasks/{task_id}/cancel",
        json={"rationale": "enough", "cascade": True},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert task_id in body["cancelled"]
    # The sleeping fake_claude subprocess must have been SIGTERM'd — we saw
    # its pid via SessionTracker and delivered a signal to it.
    assert len(body["killed"]) >= 1, body

    # Final task row: founder's note + cancelled_at, status cancelled.
    # Path B: a founder cancel writes the dedicated terminal CANCELLED status.
    assert _wait_for_terminal_status(base, task_id, timeout=15.0) == "cancelled"
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=2.0)
    task_body = r.json()["task"]
    assert task_body["status"] == "cancelled"
    assert task_body.get("cancelled_at") is not None
    assert task_body.get("note", "").startswith("cancelled by founder:"), task_body
    # Pin the race-lock invariant: the run_step classifier's post-Popen
    # "session failed rc=-15" note must NOT have overwritten the founder's.
    assert "rc=" not in (task_body.get("note") or "")

    # Audit trail has a task_cancelled entry recorded.
    audit = httpx.get(
        f"{base}/audit?task_id={task_id}", headers=_auth_headers(), timeout=5.0,
    ).json()["entries"]
    assert any(a.get("action") == "task_cancelled" for a in audit), audit


def test_revisit_roundtrip_creates_new_root_and_completes(
    live_daemon,
    runtime,
    fake_plan_env,
):
    """End-to-end: predecessor task escalates -> POST /revisit directly ->
    fake EH on the new root returns done -> new root reaches `completed`,
    predecessor auto-resolves to `resolved_superseded` per THR-018 tier #3.
    CLI is bypassed because the integration harness runs non-TTY (the CLI
    would refuse)."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    # Marker file switches behavior: first EH call escalates (predecessor),
    # every subsequent EH call (new root) returns done.
    marker = fake_plan_env.parent / "eh_revisit_marker"
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        f'marker="{marker}"\n'
        'if [[ -f "$marker" ]]; then\n'
        '  summary=\'{"action":"done","summary":"revisit succeeded"}\'\n'
        'else\n'
        '  touch "$marker"\n'
        '  summary=\'{"action":"escalate","reason":"need founder call"}\'\n'
        'fi\n'
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        '  --summary "$summary"\n'
    )
    fake_plan_env.chmod(0o755)

    seed_workspace(runtime, "engineering_head")

    # Step 1: predecessor escalates -> escalated (Path B: top-level status).
    task_id = _submit_task(base, brief="Revisit me")
    assert _wait_for_terminal_status(base, task_id, timeout=30.0) == "escalated"

    # Step 2: revisit via HTTP (integration harness is non-TTY).
    r = httpx.post(
        f"{base}/tasks/{task_id}/revisit",
        json={"founder_note": "try again"},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    new_id = body["new_root_task_id"]
    assert body["predecessor_status"] == "blocked-escalated"
    assert body["predecessor_root_task_id"] == task_id

    # Step 3: new root reaches `completed` (fake EH returns done on 2nd call).
    assert _wait_for_terminal_status(base, new_id, timeout=30.0) == "completed"

    # Step 4: predecessor auto-resolved to resolved_superseded (THR-018 §3a).
    r_pre = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    pre_task = r_pre.json()["task"]
    assert pre_task["status"] == "resolved_superseded"
    assert pre_task["block_kind"] is None
