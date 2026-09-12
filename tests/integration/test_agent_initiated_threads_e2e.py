"""End-to-end coverage for agent-initiated thread composition.

Drives a real daemon with `fake_claude.sh`. The composer's task plan runs
`happyranch threads compose --task-id ... --session-id ...`, spawning a
thread that invokes payment_agent via the thread queue. The thread-plan
path then accepts payment_agent's reply.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from tests.thr211_containment import build_threads_plan, build_thread_reply_plan

from runtime.daemon import paths as paths_mod
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
    _seed_thread_agent(runtime, "payment_agent")

    # Task plan: composer writes compose payload, calls happyranch threads compose
    # (with the binding flags wired from fake_claude.sh's positional args), then
    # reports completion.
    #
    # Shared builders serialize runtime values and own transient callback files.
    fake_claude_plan_env.write_text(build_threads_plan(fake_claude_plan_env.parent))
    fake_claude_plan_env.chmod(0o755)

    # Thread plan: payment_agent replies "got it" when invoked.
    # It uses the independently provisioned thread-plan root.
    fake_claude_thread_plan_env.write_text(build_thread_reply_plan(fake_claude_thread_plan_env.parent))
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

    # Wait for payment_agent's reply.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/threads/{thread_id}", headers=_auth_headers(), timeout=5.0,
        )
        if r.status_code == 200:
            msgs = r.json().get("messages", [])
            if any(
                m["speaker"] == "payment_agent" and "got it" in (m["body_markdown"] or "")
                for m in msgs
            ):
                return  # success
        time.sleep(0.5)
    pytest.fail(f"payment_agent never replied on thread {thread_id}")
