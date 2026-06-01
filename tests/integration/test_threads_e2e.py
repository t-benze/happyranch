"""End-to-end thread flows against a live daemon + fake claude.

Unit tests cover validation, token math, and the state machine. These tests
prove the wiring works: real daemon → executor subprocess → callback → DB.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


def _seed_thread_agent(runtime: Path, agent: str) -> None:
    """Create the agent's workspace + frontmatter file so compose accepts it."""
    seed_workspace(runtime, agent)
    agents_dir = runtime / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.md").write_text(
        "---\n"
        f"name: {agent}\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "description: integration test agent\n"
        "---\n"
        "# system prompt\n"
    )


def _wait_for_message(
    base: str,
    thread_id: str,
    *,
    predicate,
    timeout: float = 30.0,
) -> dict:
    """Poll the thread until a message matching `predicate(msg)` exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/threads/{thread_id}", headers=_auth_headers(), timeout=5.0)
        if r.status_code == 200:
            for m in r.json().get("messages", []):
                if predicate(m):
                    return m
        time.sleep(0.25)
    raise AssertionError(f"thread {thread_id}: predicate not satisfied within {timeout}s")


def _wait_for_status(base: str, thread_id: str, *, status: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/threads/{thread_id}", headers=_auth_headers(), timeout=5.0)
        if r.status_code == 200 and r.json().get("status") == status:
            return r.json()
        time.sleep(0.25)
    raise AssertionError(f"thread {thread_id} did not reach status={status}")


def test_compose_reply_archive_writes_transcript(
    live_daemon,
    runtime,
    fake_claude_thread_plan_env,
):
    """Compose → fake agent replies → archive (no close-outs) → transcript."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _seed_thread_agent(runtime, "dev_agent")

    # Plan: when invoked on a thread, post a reply.
    # Use printf to avoid heredoc variable-expansion pitfalls.
    fake_claude_thread_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'thread_id=$1; token=$2; agent=$3; org=$4; purpose=$5\n'
        'payload=$(mktemp)\n'
        'printf \'{"thread_id":"%s","invocation_token":"%s","speaker":"%s",'
        '"body_markdown":"hello from %s","in_response_to_seq":1}\' '
        '"$thread_id" "$token" "$agent" "$agent" > "$payload"\n'
        'happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"\n'
    )
    fake_claude_thread_plan_env.chmod(0o755)

    # Compose.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "smoke",
            "recipients": ["dev_agent"],
            "body_markdown": "hi dev_agent",
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # Wait for the agent reply to land.
    reply = _wait_for_message(
        base,
        thread_id,
        predicate=lambda m: m["speaker"] == "dev_agent" and m["kind"] == "message",
    )
    assert "hello from dev_agent" in reply["body_markdown"]

    # Archive — now synchronous.
    r = httpx.post(
        f"{base}/threads/{thread_id}/archive",
        json={"summary": "smoke wrap-up"},
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "archived"
    assert body["transcript_path"]
    final = body
    transcript = Path(final["transcript_path"]).read_text(encoding="utf-8")
    assert "smoke wrap-up" in transcript
    assert "hello from dev_agent" in transcript


def test_agent_dispatch_from_thread_creates_task(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """Fake agent issues `happyranch threads dispatch` from inside a thread invocation;
    the system message lands AND the new task is enqueued + run by the daemon."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _seed_thread_agent(runtime, "dev_agent")

    # Thread plan: dispatch a task, then reply (to release the token cleanly).
    # Uses a state file to avoid re-dispatching on any follow-up invocation.
    fake_claude_thread_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'thread_id=$1; token=$2; agent=$3; org=$4\n'
        '# Idempotency guard — don\'t re-dispatch on a second invocation.\n'
        'state_file="${FAKE_CLAUDE_THREAD_PLAN}.seen.${thread_id}.${agent}"\n'
        'if [[ -f "$state_file" ]]; then\n'
        '  # Second time around: just reply.\n'
        '  payload=$(mktemp)\n'
        '  printf \'{"thread_id":"%s","invocation_token":"%s","speaker":"%s",'
        '"body_markdown":"already dispatched","in_response_to_seq":1}\' '
        '"$thread_id" "$token" "$agent" > "$payload"\n'
        '  happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"\n'
        '  exit 0\n'
        'fi\n'
        'touch "$state_file"\n'
        '\n'
        'dispatch=$(mktemp)\n'
        'printf \'{"thread_id":"%s","invocation_token":"%s","dispatcher":"%s",'
        '"brief":"do the thing"}\' "$thread_id" "$token" "$agent" > "$dispatch"\n'
        'happyranch threads dispatch --org "$org" --thread-id "$thread_id" --from-file "$dispatch"\n'
        '\n'
        'reply=$(mktemp)\n'
        'printf \'{"thread_id":"%s","invocation_token":"%s","speaker":"%s",'
        '"body_markdown":"dispatched","in_response_to_seq":1}\' '
        '"$thread_id" "$token" "$agent" > "$reply"\n'
        'happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$reply"\n'
    )
    fake_claude_thread_plan_env.chmod(0o755)

    # Task plan: when the dispatched task runs dev_agent, complete cleanly.
    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_claude_plan_env.chmod(0o755)

    # Compose.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "ship it",
            "recipients": ["dev_agent"],
            "body_markdown": "please dispatch the thing",
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # Wait for the system message announcing the dispatch.
    sys_msg = _wait_for_message(
        base,
        thread_id,
        predicate=lambda m: (
            m["kind"] == "system"
            and (m.get("system_payload") or {}).get("kind_tag") == "task_dispatched"
        ),
        timeout=30.0,
    )
    task_id = sys_msg["system_payload"]["task_id"]

    # Wait for the dispatched task to terminate.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        tr = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
        if tr.status_code == 200 and tr.json()["task"]["status"] == "completed":
            break
        time.sleep(0.25)
    else:
        pytest.fail(f"dispatched task {task_id} did not complete")
