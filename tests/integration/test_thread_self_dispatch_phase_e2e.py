"""End-to-end test for manager self-dispatch from a thread.

Verifies the self-only dispatch doctrine introduced in the
2026-05-28-thread-talk-self-dispatch-only design:

  - A manager (engineering_head) receives a thread invocation.
  - On the first reply turn it self-dispatches (no target_agent → defaults to
    dispatcher), confirming the route allows manager→self.
  - The dispatched task completes.
  - The runtime fires a TASK_FOLLOWUP invocation at the manager.
  - The manager posts a followup reply.

Spec: docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md §13
Plan: docs/superpowers/plans/2026-05-28-thread-talk-self-dispatch-only.md Task 6
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


# ---------------------------------------------------------------------------
# Agent seeding
# ---------------------------------------------------------------------------


def _seed_manager_agent(runtime: Path, agent: str) -> None:
    """Create the agent workspace + frontmatter file for a MANAGER agent.

    Writes role: manager (not worker) so the teams registry recognises this
    agent as the team manager, allowing it to self-dispatch.
    """
    seed_workspace(runtime, agent)
    agents_dir = runtime / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.md").write_text(
        "---\n"
        f"name: {agent}\n"
        "team: engineering\n"
        "role: manager\n"
        "executor: claude\n"
        "description: integration test manager agent\n"
        "---\n"
        "# system prompt\n"
    )


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------


def _wait_for_condition(
    base: str,
    thread_id: str,
    *,
    predicate,
    timeout: float = 90.0,
) -> list[dict]:
    """Poll thread messages until `predicate(msgs)` is truthy, then return msgs."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/threads/{thread_id}/messages",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            msgs = r.json().get("messages", [])
            if predicate(msgs):
                return msgs
        time.sleep(0.5)
    # Return whatever we have on timeout (may be empty / incomplete).
    r = httpx.get(
        f"{base}/threads/{thread_id}/messages",
        headers=_auth_headers(),
        timeout=5.0,
    )
    if r.status_code == 200:
        return r.json().get("messages", [])
    return []


def _sys_tags(msgs: list[dict]) -> list[str]:
    return [
        m.get("system_payload", {}).get("kind_tag", "")
        for m in msgs
        if m.get("kind") == "system"
    ]


def _agent_replies(msgs: list[dict], agent: str) -> list[dict]:
    return [m for m in msgs if m.get("kind") == "message" and m.get("speaker") == agent]


# ---------------------------------------------------------------------------
# Thread plan script
#
# Args: $1=thread_id, $2=token, $3=agent, $4=org_slug, $5=purpose
#
# - purpose == "reply" (first invocation from founder send):
#     Self-dispatch (no target_agent → defaults to dispatcher == engineering_head),
#     then reply to release the invocation token.
#     An idempotency guard prevents duplicate dispatches on any subsequent
#     reply invocations.
# - purpose == "task_followup":
#     Post a followup reply acknowledging the task result.
# ---------------------------------------------------------------------------

_THREAD_PLAN = """\
#!/usr/bin/env bash
thread_id=$1; token=$2; agent=$3; org=$4; purpose=$5

if [ "$purpose" = "task_followup" ]; then
    # Followup turn: reply acknowledging the completed task.
    payload=$(mktemp)
    printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"followup: task done","in_response_to_seq":1}' \\
        "$thread_id" "$token" "$agent" > "$payload"
    grassland threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"
    exit 0
fi

# purpose == "reply" (or bootstrap): self-dispatch only on the FIRST invocation
# for this thread+agent pair to avoid duplicate dispatches.
# Use the plan's own directory (per-test tmp) so concurrent tests don't share
# state files even though thread IDs always start at THR-001.
plan_dir=$(dirname "$FAKE_CLAUDE_THREAD_PLAN")
state_file="$plan_dir/self_dispatch_test_seen.${thread_id}.${agent}"
if [ -f "$state_file" ]; then
    # Already dispatched; just reply to release the token.
    payload=$(mktemp)
    printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"already dispatched","in_response_to_seq":1}' \\
        "$thread_id" "$token" "$agent" > "$payload"
    grassland threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"
    exit 0
fi
touch "$state_file"

# Self-dispatch: omit target_agent so the route defaults to dispatcher.
dispatch=$(mktemp)
printf '{"thread_id":"%s","invocation_token":"%s","dispatcher":"%s","brief":"manager self-dispatched task"}' \\
    "$thread_id" "$token" "$agent" > "$dispatch"
grassland threads dispatch --org "$org" --thread-id "$thread_id" --from-file "$dispatch"

# Reply to release the invocation token (dispatch does NOT consume it).
reply=$(mktemp)
printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"dispatched to myself, will report back","in_response_to_seq":1}' \\
    "$thread_id" "$token" "$agent" > "$reply"
grassland threads reply --org "$org" --thread-id "$thread_id" --from-file "$reply"
"""


def _write_thread_plan(path: Path) -> None:
    path.write_text(_THREAD_PLAN)
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_manager_self_dispatch_from_thread_completes_with_followup(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """Founder send → manager self-dispatch → task completes → followup fires.

    Expected message timeline:
      seq 1: founder message ('please do X')
      (system task_dispatched — manager self-dispatched; target == dispatcher == engineering_head)
      manager reply ('dispatched to myself, will report back')
      (system task_completed — injected by _maybe_post_thread_followup)
      manager followup reply ('followup: task done')
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    # Seed the manager agent (role: manager, team: engineering).
    # engineering_head is already declared as the engineering team's manager
    # in the test runtime's teams.yaml (see conftest.py), so the teams registry
    # will recognise it as a manager and allow self-dispatch.
    _seed_manager_agent(runtime, "engineering_head")

    # Write the thread plan (dispatch + reply on first turn; followup on task_followup).
    _write_thread_plan(fake_claude_thread_plan_env)

    # Task plan: complete cleanly on the first invocation (single-shot).
    fake_claude_plan_env.write_text(
        "#!/usr/bin/env bash\n"
        "task_id=$1; session_id=$2; agent=$3; org_slug=$4\n"
        'grassland report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        "  --summary '{\"action\":\"done\",\"summary\":\"manager task finished ok\"}'\n"
    )
    fake_claude_plan_env.chmod(0o755)

    # 1. Compose the thread: founder → engineering_head.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "manager self-dispatch test",
            "recipients": ["engineering_head"],
            "body_markdown": "please handle this yourself",
            "addressed_to": ["@all"],
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # 2. Wait for the full timeline: task_dispatched, task_completed, 2+ manager replies.
    def _timeline_complete(msgs: list[dict]) -> bool:
        tags = _sys_tags(msgs)
        replies = _agent_replies(msgs, "engineering_head")
        return (
            "task_dispatched" in tags
            and "task_completed" in tags
            and len(replies) >= 2
        )

    msgs = _wait_for_condition(base, thread_id, predicate=_timeline_complete, timeout=90.0)

    # 3. Assertions.
    tags = _sys_tags(msgs)
    assert "task_dispatched" in tags, f"missing task_dispatched; got tags: {tags}"
    assert "task_completed" in tags, f"missing task_completed; got tags: {tags}"

    # Exactly ONE task_dispatched system message.
    dispatched_msgs = [
        m for m in msgs
        if m.get("kind") == "system"
        and (m.get("system_payload") or {}).get("kind_tag") == "task_dispatched"
    ]
    assert len(dispatched_msgs) == 1, (
        f"expected exactly 1 task_dispatched, got {len(dispatched_msgs)}: {dispatched_msgs}"
    )

    # Exactly ONE task_completed system message.
    completed_msgs = [
        m for m in msgs
        if m.get("kind") == "system"
        and (m.get("system_payload") or {}).get("kind_tag") == "task_completed"
    ]
    assert len(completed_msgs) == 1, (
        f"expected exactly 1 task_completed, got {len(completed_msgs)}: {completed_msgs}"
    )

    # The task_dispatched payload must show self-dispatch: dispatcher == target == engineering_head.
    dispatch_payload = dispatched_msgs[0]["system_payload"]
    assert dispatch_payload["dispatcher"] == "engineering_head", (
        f"expected dispatcher=engineering_head, got: {dispatch_payload['dispatcher']}"
    )
    assert dispatch_payload["target_agent"] == "engineering_head", (
        f"expected target_agent=engineering_head, got: {dispatch_payload['target_agent']}"
    )

    # At least 2 manager replies (initial + followup).
    replies = _agent_replies(msgs, "engineering_head")
    assert len(replies) >= 2, (
        f"expected 2+ agent replies, got {len(replies)}: {replies}"
    )

    # The last reply must contain 'followup' (the task_followup invocation's reply).
    followup_reply = replies[-1]
    assert "followup" in (followup_reply.get("body_markdown") or "").lower(), (
        f"last reply doesn't look like a followup: {followup_reply}"
    )

    # The followup reply must appear after the task_completed system message.
    tc_seq = completed_msgs[0]["seq"]
    assert followup_reply["seq"] > tc_seq, (
        f"followup reply (seq={followup_reply['seq']}) must come after "
        f"task_completed (seq={tc_seq})"
    )
