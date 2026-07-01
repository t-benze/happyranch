"""End-to-end tests for task-followup re-invocation.

When a task dispatched from a thread reaches its terminal state the runtime
injects a system message (task_completed / task_failed) and re-invokes the
dispatching agent with purpose=task_followup so it can post the reply it
promised. These tests verify the full wiring: dispatch → task run → followup
system message → followup agent invocation → followup reply.

Spec: docs/superpowers/specs/2026-05-28-thread-task-followup-design.md §11
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from runtime.daemon import paths as paths_mod
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


def _seed_thread_agent(runtime: Path, agent: str) -> None:
    """Create the agent workspace + frontmatter file so compose/dispatch accepts it."""
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


def _poll_messages(base: str, thread_id: str, timeout: float = 60.0) -> list[dict]:
    """Return the thread message list, polling until stable or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/threads/{thread_id}/messages",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            return r.json().get("messages", [])
        time.sleep(0.25)
    return []


def _wait_for_condition(
    base: str,
    thread_id: str,
    *,
    predicate,
    timeout: float = 60.0,
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
    return _poll_messages(base, thread_id, timeout=5.0)


def _sys_tags(msgs: list[dict]) -> list[str]:
    return [
        m.get("system_payload", {}).get("kind_tag", "")
        for m in msgs
        if m.get("kind") == "system"
    ]


def _agent_replies(msgs: list[dict], agent: str) -> list[dict]:
    return [m for m in msgs if m.get("kind") == "message" and m.get("speaker") == agent]


# ---------------------------------------------------------------------------
# Thread plan script shared across all three tests.
# Args: $1=thread_id, $2=token, $3=agent, $4=org_slug, $5=purpose
#
# - purpose == "reply" (first invocation): dispatch a task + post a reply.
#   Uses an idempotency guard so only the very first reply-purpose invocation
#   dispatches; subsequent reply invocations (if any) just reply.
# - purpose == "task_followup": post a followup reply acknowledging the task.
# ---------------------------------------------------------------------------

_THREAD_PLAN = """\
#!/usr/bin/env bash
thread_id=$1; token=$2; agent=$3; org=$4; purpose=$5

if [ "$purpose" = "task_followup" ]; then
    # Followup turn: reply acknowledging the task result.
    payload=$(mktemp)
    printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"followup: task done","in_response_to_seq":1}' \\
        "$thread_id" "$token" "$agent" > "$payload"
    happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"
    exit 0
fi

# purpose == "reply" (or bootstrap): dispatch only on the FIRST invocation
# for this thread+agent pair to avoid duplicate dispatches.
# Use the plan's own directory (per-test tmp) so concurrent tests don't share
# state files even though thread IDs always start at THR-001.
plan_dir=$(dirname "$FAKE_CLAUDE_THREAD_PLAN")
state_file="$plan_dir/followup_test_seen.${thread_id}.${agent}"
if [ -f "$state_file" ]; then
    # Already dispatched; just reply to release the token.
    payload=$(mktemp)
    printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"already dispatched","in_response_to_seq":1}' \\
        "$thread_id" "$token" "$agent" > "$payload"
    happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"
    exit 0
fi
touch "$state_file"

# Dispatch the task.
dispatch=$(mktemp)
printf '{"thread_id":"%s","invocation_token":"%s","dispatcher":"%s","brief":"do the thing"}' \\
    "$thread_id" "$token" "$agent" > "$dispatch"
happyranch threads dispatch --org "$org" --thread-id "$thread_id" --from-file "$dispatch"

# Reply to release the token cleanly (dispatch does NOT consume it).
reply=$(mktemp)
printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"dispatched, will report back","in_response_to_seq":1}' \\
    "$thread_id" "$token" "$agent" > "$reply"
happyranch threads reply --org "$org" --thread-id "$thread_id" --from-file "$reply"
"""


def _write_thread_plan(path: Path) -> None:
    path.write_text(_THREAD_PLAN)
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# Test 1 — happy path
# ---------------------------------------------------------------------------


def test_followup_fires_on_completed_thread_dispatched_task(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """Founder send → manager reply+dispatch → task completes → followup runs.

    Expected message timeline:
      seq 1: founder message ('please do X')
      (system task_dispatched — from the dispatch call)
      manager reply ('dispatched, will report back')
      (system task_completed — injected by _maybe_post_thread_followup)
      manager followup reply ('followup: task done')
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed_thread_agent(runtime, "dev_agent")

    # Thread plan: dispatch + reply on first turn; followup reply on task_followup.
    _write_thread_plan(fake_claude_thread_plan_env)

    # Task plan: complete cleanly.
    fake_claude_plan_env.write_text(
        "#!/usr/bin/env bash\n"
        "task_id=$1; session_id=$2; agent=$3; org_slug=$4\n"
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        "  --summary '{\"action\":\"done\",\"summary\":\"task finished ok\"}'\n"
    )
    fake_claude_plan_env.chmod(0o755)

    # 1. Compose thread with founder message.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "followup test",
            "recipients": ["dev_agent"],
            "body_markdown": "please do X",
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # 2. Wait for the full timeline: task_dispatched, task_completed, 2+ agent replies.
    def _timeline_complete(msgs: list[dict]) -> bool:
        tags = _sys_tags(msgs)
        replies = _agent_replies(msgs, "dev_agent")
        return "task_dispatched" in tags and "task_completed" in tags and len(replies) >= 2

    msgs = _wait_for_condition(base, thread_id, predicate=_timeline_complete, timeout=90.0)

    # 3. Assertions.
    tags = _sys_tags(msgs)
    assert "task_dispatched" in tags, f"missing task_dispatched; got tags: {tags}"
    assert "task_completed" in tags, f"missing task_completed; got tags: {tags}"

    replies = _agent_replies(msgs, "dev_agent")
    assert len(replies) >= 2, f"expected 2+ agent replies, got {len(replies)}: {replies}"

    # The second reply must be the followup (contains 'followup' or 'task done').
    followup_reply = replies[-1]
    assert "followup" in (followup_reply.get("body_markdown") or "").lower(), (
        f"last reply doesn't look like a followup: {followup_reply}"
    )

    # The task_completed system message must reference a valid task_id.
    tc_msg = next(
        m for m in msgs
        if m.get("kind") == "system"
        and (m.get("system_payload") or {}).get("kind_tag") == "task_completed"
    )
    assert tc_msg["system_payload"]["task_id"], "task_completed system message missing task_id"


# ---------------------------------------------------------------------------
# Test 2 — revisit case
# ---------------------------------------------------------------------------


def test_followup_fires_once_after_revisit(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """First task attempt fails (executor_error) → auto-revisit → revisit
    completes → exactly ONE followup fires at the revisit's terminal, not
    at the original failure.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed_thread_agent(runtime, "dev_agent")

    _write_thread_plan(fake_claude_thread_plan_env)

    # Task plan: first invocation exits 1 (no callback → executor_error),
    # triggering an auto-revisit. Subsequent invocations complete cleanly.
    # A counter file in the runtime dir tracks which attempt this is.
    counter = runtime / "task_attempt_counter"
    fake_claude_plan_env.write_text(
        "#!/usr/bin/env bash\n"
        "task_id=$1; session_id=$2; agent=$3; org_slug=$4\n"
        f'counter="{counter}"\n'
        "n=$(cat \"$counter\" 2>/dev/null || echo 0)\n"
        "n=$((n + 1))\n"
        'echo "$n" > "$counter"\n'
        'if [ "$n" = "1" ]; then\n'
        "  # First attempt: fail with a non-zero exit so auto-revisit fires.\n"
        "  exit 1\n"
        "fi\n"
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        "  --summary '{\"action\":\"done\",\"summary\":\"after revisit\"}'\n"
    )
    fake_claude_plan_env.chmod(0o755)

    # 1. Compose + founder send.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "revisit followup test",
            "recipients": ["dev_agent"],
            "body_markdown": "please do Y",
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # 2. Wait for timeline: task_dispatched + task_completed + 2+ agent replies.
    # Give extra time because two task invocations are needed (fail + revisit).
    def _timeline_complete(msgs: list[dict]) -> bool:
        tags = _sys_tags(msgs)
        replies = _agent_replies(msgs, "dev_agent")
        return "task_dispatched" in tags and "task_completed" in tags and len(replies) >= 2

    msgs = _wait_for_condition(base, thread_id, predicate=_timeline_complete, timeout=120.0)

    tags = _sys_tags(msgs)
    assert "task_dispatched" in tags, f"missing task_dispatched; tags: {tags}"
    assert "task_completed" in tags, f"missing task_completed; tags: {tags}"

    # Exactly ONE task_completed system message (the revisit's terminal, not the
    # original failure — which never fires a followup because auto_revisit_spawned=True).
    completed_msgs = [
        m for m in msgs
        if m.get("kind") == "system"
        and (m.get("system_payload") or {}).get("kind_tag") == "task_completed"
    ]
    assert len(completed_msgs) == 1, (
        f"expected exactly 1 task_completed, got {len(completed_msgs)}: {completed_msgs}"
    )

    # The original failure now fires a task_failed system message (THR-046 msg99)
    # carrying revisit_task_id so the thread surface can render 'revisiting as
    # <SUCCESSOR>'. The revisit successor terminal also fires its own followup
    # (task_completed), so we should see BOTH system messages.
    assert "task_failed" in tags, (
        f"missing task_failed system message; tags: {tags}"
    )
    # Verify the task_failed payload carries the successor task id.
    failed_msgs = [
        m for m in msgs
        if m.get("kind") == "system"
        and (m.get("system_payload") or {}).get("kind_tag") == "task_failed"
    ]
    assert len(failed_msgs) == 1, (
        f"expected exactly 1 task_failed, got {len(failed_msgs)}: {failed_msgs}"
    )
    assert failed_msgs[0]["system_payload"].get("revisit_task_id"), (
        "task_failed payload must carry revisit_task_id"
    )

    replies = _agent_replies(msgs, "dev_agent")
    assert len(replies) >= 2, f"expected 2+ agent replies, got {len(replies)}"

    # The followup reply must still appear after the task_completed system
    # message (the revisit successor's terminal, which carries the re-invocation).
    tc_seq = completed_msgs[0]["seq"]
    followup_reply = replies[-1]
    assert followup_reply["seq"] > tc_seq, (
        f"followup reply (seq={followup_reply['seq']}) must come after "
        f"task_completed (seq={tc_seq})"
    )


# ---------------------------------------------------------------------------
# Test 3 — archived-thread skip
# ---------------------------------------------------------------------------


def test_followup_skipped_when_thread_archived_before_task_terminal(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """Thread archived between dispatch and task terminal → audit-only, no
    thread mutation.

    Strategy: configure the task plan to sleep long enough for us to archive
    the thread before it reports completion, then verify:
      - No task_completed or task_failed system message appears.
      - An audit entry with action='thread_followup_skipped',
        reason='thread_not_open' exists.

    Because exact timing is flaky in CI, we use a synchronisation file:
    the task plan blocks until a "go" file appears (written by the test
    after archive completes), then reports completion. This guarantees the
    archive happens before the terminal transition regardless of machine speed.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed_thread_agent(runtime, "dev_agent")

    _write_thread_plan(fake_claude_thread_plan_env)

    # Synchronisation file: task plan waits for this before completing.
    go_file = runtime / "task_go_signal"

    fake_claude_plan_env.write_text(
        "#!/usr/bin/env bash\n"
        "task_id=$1; session_id=$2; agent=$3; org_slug=$4\n"
        f'go_file="{go_file}"\n'
        "# Wait up to 30s for the test to archive the thread, then report.\n"
        'deadline=$(($(date +%s) + 30))\n'
        'while [ ! -f "$go_file" ] && [ "$(date +%s)" -lt "$deadline" ]; do\n'
        "  sleep 0.2\n"
        "done\n"
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent "$agent" --status completed --confidence 90 \\\n'
        "  --summary '{\"action\":\"done\",\"summary\":\"late completion\"}'\n"
    )
    fake_claude_plan_env.chmod(0o755)

    # 1. Compose + founder send.
    r = httpx.post(
        f"{base}/threads",
        json={
            "subject": "archived before terminal",
            "recipients": ["dev_agent"],
            "body_markdown": "please do Z",
        },
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # 2. Wait for the task_dispatched system message (means dispatch ran and task is enqueued).
    deadline = time.monotonic() + 60.0
    task_id = None
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/threads/{thread_id}/messages",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            for m in r.json().get("messages", []):
                if (
                    m.get("kind") == "system"
                    and (m.get("system_payload") or {}).get("kind_tag") == "task_dispatched"
                ):
                    task_id = m["system_payload"]["task_id"]
                    break
        if task_id:
            break
        time.sleep(0.5)

    assert task_id is not None, "task_dispatched system message never appeared"

    # 3. Archive the thread BEFORE signalling the task to complete.
    r = httpx.post(
        f"{base}/threads/{thread_id}/archive",
        json={"summary": "archiving before task done"},
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text

    # 4. Signal the task to complete now.
    go_file.write_text("go\n")

    # 5. Wait for the task to reach a terminal state.
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        tr = httpx.get(
            f"{base}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if tr.status_code == 200:
            status = tr.json().get("task", {}).get("status")
            if status in ("completed", "failed"):
                break
        time.sleep(0.5)
    else:
        pytest.fail(f"task {task_id} did not reach a terminal state within 60s")

    # 6. Give the daemon a moment to process any (incorrectly fired) followup.
    time.sleep(3.0)

    # 7. Assertions: no task_completed or task_failed system message in the thread.
    r = httpx.get(
        f"{base}/threads/{thread_id}/messages",
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    msgs = r.json().get("messages", [])
    tags = _sys_tags(msgs)
    assert "task_completed" not in tags, (
        f"task_completed should NOT appear when thread was archived; tags: {tags}"
    )
    assert "task_failed" not in tags, (
        f"task_failed should NOT appear when thread was archived; tags: {tags}"
    )

    # 8. Verify the audit log records the skip.
    # The audit row's task_id column is set to terminal_task_id (= the dispatched
    # task), so we query by that task_id.
    r = httpx.get(
        f"{base}/audit",
        headers=_auth_headers(),
        params={"task_id": task_id, "limit": 200},
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    audit_rows = r.json().get("entries", [])
    skipped_rows = [
        row for row in audit_rows
        if row.get("action") == "thread_followup_skipped"
        and (row.get("payload") or {}).get("reason") == "thread_not_open"
    ]
    assert len(skipped_rows) >= 1, (
        f"expected an audit row with action=thread_followup_skipped, "
        f"reason=thread_not_open; audit rows (actions): "
        f"{[row.get('action') for row in audit_rows]}"
    )
