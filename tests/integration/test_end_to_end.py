from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

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


def _submit_task(base: str, brief: str = "smoke") -> str:
    r = httpx.post(
        f"{base}/tasks",
        json={"type": "general", "brief": brief},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _await_terminal(base: str, task_id: str, timeout: float = 20.0) -> str:
    """Poll /tasks/{id} for a terminal status. Polling beats SSE here because
    sse_starlette emits keepalive pings every 15s, which keeps the socket
    read timeout from ever firing — meaning a hung task would deadlock the
    test for the entire pytest budget."""
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=2.0,
        )
        body = r.json()
        status_value = body["task"]["status"]
        if status_value == "completed":
            return "task_complete"
        if status_value == "failed":
            return "task_failed"
        if status_value == "blocked":
            return "task_blocked"
        _time.sleep(0.2)
    raise AssertionError(
        f"task {task_id} did not reach terminal within {timeout}s "
        f"(last status={status_value!r}, note={body['task'].get('note')!r})"
    )


def test_register_and_run_completes_via_callback(
    live_daemon, runtime, fake_plan_env
):
    """End-to-end happy path: submit a task, fake Claude calls
    `opc report-completion`, daemon records the row, SSE stream reports
    `task_complete`.

    This is the regression guard for commit 8581f26 — which removed the
    SessionTracker wiring from `_run_agent` and made every agent callback
    fail with 409 unknown_session. Without the fix in 32e77d6, this test
    fails because the callback is rejected and `run_step` marks the task
    failed with note='agent session failed'."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    # fake_plan_env preallocated this path via env var; the daemon inherits
    # FAKE_CLAUDE_PLAN and re-reads the file each time fake_claude runs.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    _register_runtime(base, runtime)
    # The EH workspace must have the start-task skill marker or run_step
    # raises WorkspaceNotInitialized and fails the task without invoking
    # Claude at all.
    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base)
    assert _await_terminal(base, task_id) == "task_complete"

    # Confirm DB has a task_result tagged with a session_id
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "completed"
    assert any(res["session_id"] for res in body["results"])
    # Pin the regression: if the SessionTracker wiring is broken again, the
    # callback is rejected with 409 unknown_session and run_step writes this
    # exact note. Failing on this string is what would have flagged
    # commit 8581f26 immediately.
    assert body["task"].get("note") != "agent session failed"


def test_completion_callback_rejected_when_session_unknown(
    live_daemon, runtime, fake_plan_env
):
    """Negative path: a callback whose session_id was never registered must
    be rejected with HTTP 409 / detail.code='unknown_session'.

    This is the daemon-side invariant the SessionTracker is supposed to
    enforce. Without it, fabricated callbacks would silently insert
    task_results rows under any session_id.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    # No-op plan — the test uses a hand-crafted task to exercise the daemon
    # endpoint directly, not the orchestrator.
    fake_plan_env.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_plan_env.chmod(0o755)

    _register_runtime(base, runtime)
    # Use a fabricated task_id that the daemon never spawned. The completion
    # endpoint short-circuits on the SessionTracker lookup before looking
    # up the task, so this exercises the unknown_session branch in
    # isolation and avoids racing the orchestrator's real EH session
    # (which would otherwise register a real session_id and flip the
    # rejection into session_mismatch instead).
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


def test_delegate_and_resume_roundtrip(live_daemon, runtime, fake_plan_env):
    """End-to-end delegate path: EH returns a `delegate` decision spawning a
    child for `dev_agent`; child completes; parent auto-resumes; EH then
    returns `done`; parent reaches `completed`.

    This exercises the full queue-driven re-enqueue path that `run_step`
    relies on for delegated work. It also covers the SessionTracker
    invariant for two distinct (task_id, agent) pairs being live in
    parallel-ish sequence.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    # Plan branches on the agent name. The EH first delegates to dev_agent,
    # then on the resume call returns done. We track resume state via a
    # marker file so the EH plan emits different decisions across the two
    # invocations on the same parent task.
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

    _register_runtime(base, runtime)
    seed_workspace(runtime, "engineering_head")
    seed_workspace(runtime, "dev_agent")

    task_id = _submit_task(base, brief="delegate me")
    # Generous deadline: this round-trip is two EH steps + one dev_agent
    # step + queue handoffs; serial subprocess.run calls add up.
    assert _await_terminal(base, task_id, timeout=30.0) == "task_complete"

    r = httpx.get(f"{base}/tasks", headers=_auth_headers(), timeout=5.0)
    tasks_list = r.json()["tasks"]
    children = [t for t in tasks_list if t.get("parent_task_id") == task_id]
    assert len(children) == 1, tasks_list
    child = children[0]
    assert child["assigned_agent"] == "dev_agent"
    assert child["status"] == "completed"

    # Parent's audit log must show one orchestration_step recording the
    # delegate decision. Without this, future regressions could complete
    # the task by accident and the test would still pass.
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    audit = r.json()["audit_log"]
    delegate_steps = [
        a for a in audit
        if a.get("action") == "orchestration_step"
        and ((a.get("payload") or {}).get("decision") or {}).get("action") == "delegate"
    ]
    assert delegate_steps, audit


def test_idle_daemon_starts_workers_after_register(
    live_daemon, runtime, fake_plan_env
):
    """Lifespan-bootstrap regression guard.

    The daemon boots idle and only learns about a runtime via
    POST /runtimes/register. Before the fix in this change, the lifespan
    one-shot Orchestrator+worker bootstrap was gated on `not state.is_idle`
    at boot, so a runtime swapped in later would never get workers — every
    enqueued task would sit in the queue forever and SSE streams would
    stall on heartbeats. We assert the inverse: a task submitted after a
    runtime is registered actually progresses.
    """
    port = live_daemon
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
    assert _await_terminal(base, task_id, timeout=10.0) == "task_complete"
