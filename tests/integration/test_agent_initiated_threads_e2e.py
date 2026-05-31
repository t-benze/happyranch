"""End-to-end coverage for agent-initiated thread composition.

Drives a real daemon with `fake_claude.sh`. The composer's task plan runs
`grassland threads compose --task-id ... --session-id ...`, spawning a
thread that invokes payment_agt via the thread queue. The thread-plan
path then accepts payment_agt's reply.
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


def test_agent_compose_from_task_spawns_thread_and_recipient_replies(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """Composer runs a task, calls compose-as-agent, recipient replies via thread queue."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _seed_thread_agent(runtime, "engineering_head")
    _seed_thread_agent(runtime, "payment_agt")

    # Task plan: composer writes compose payload, calls grassland threads compose
    # (with the binding flags wired from fake_claude.sh's positional args), then
    # reports completion.
    #
    # The heredoc (<<EOF, unquoted) expands ${TASK_ID} and ${SESSION_ID} at
    # bash runtime — that's intentional. Python's single-quoted strings don't
    # expand ${...} so we get the literal text in the file, which bash then runs.
    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'TASK_ID="$1"; SESSION_ID="$2"; AGENT="$3"; ORG_SLUG="$4"\n'
        '\n'
        '# Write the compose payload (no shell vars needed — static content).\n'
        'cat > /tmp/thread-compose-int.json << \'ENDJSON\'\n'
        '{"composer": "engineering_head",\n'
        ' "subject": "int test loop in",\n'
        ' "recipients": ["payment_agt"],\n'
        ' "body_markdown": "looping payment_agt in"}\n'
        'ENDJSON\n'
        '\n'
        'grassland threads compose --org "$ORG_SLUG" --task-id "$TASK_ID" '
        '--session-id "$SESSION_ID" --from-file /tmp/thread-compose-int.json >&2\n'
        '\n'
        '# Write the completion payload using printf to avoid heredoc expansion issues.\n'
        'printf \'{"task_id": "%s", "session_id": "%s", "agent": "engineering_head", '
        '"status": "completed", "confidence": 90, "summary": "composed thread"}\' '
        '"$TASK_ID" "$SESSION_ID" > "/tmp/completion-${TASK_ID}.json"\n'
        '\n'
        'grassland report-completion --org "$ORG_SLUG" '
        '--from-file "/tmp/completion-${TASK_ID}.json" >&2\n'
    )
    fake_claude_plan_env.chmod(0o755)

    # Thread plan: payment_agt replies "got it" when invoked.
    # Use printf to avoid heredoc variable expansion pitfalls (same pattern as
    # test_threads_e2e.py's existing tests).
    fake_claude_thread_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'THREAD_ID="$1"; TOKEN="$2"; AGENT="$3"; ORG_SLUG="$4"; PURPOSE="$5"\n'
        'payload=$(mktemp)\n'
        'printf \'{"thread_id": "%s", "invocation_token": "%s", "speaker": "%s", '
        '"body_markdown": "got it", "in_response_to_seq": 1}\' '
        '"$THREAD_ID" "$TOKEN" "$AGENT" > "$payload"\n'
        'grassland threads reply --org "$ORG_SLUG" --thread-id "$THREAD_ID" '
        '--from-file "$payload" >&2\n'
    )
    fake_claude_thread_plan_env.chmod(0o755)

    # Kick off the composer's task.
    # The conftest teams.yaml registers engineering_head as the engineering manager.
    # POST /tasks auto-assigns to the team manager — no assigned_agent field needed.
    r = httpx.post(
        f"{base}/tasks", headers=_auth_headers(),
        json={
            "brief": "compose a thread",
            "team": "engineering",
        },
        timeout=5.0,
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["task_id"]

    # Wait for the agent-composed thread to appear.
    deadline = time.monotonic() + 30
    thread_id = None
    while time.monotonic() < deadline:
        threads = httpx.get(
            f"{base}/threads", headers=_auth_headers(), timeout=5.0,
        ).json()
        agent_threads = [
            t for t in threads["threads"]
            if t.get("composed_by") == "engineering_head"
        ]
        if agent_threads:
            thread_id = agent_threads[0]["thread_id"]
            break
        time.sleep(0.5)
    assert thread_id is not None, "agent-composed thread never appeared"

    # Wait for payment_agt's reply.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/threads/{thread_id}", headers=_auth_headers(), timeout=5.0,
        )
        if r.status_code == 200:
            msgs = r.json().get("messages", [])
            if any(
                m["speaker"] == "payment_agt" and "got it" in (m["body_markdown"] or "")
                for m in msgs
            ):
                return  # success
        time.sleep(0.5)
    pytest.fail(f"payment_agt never replied on thread {thread_id}")
