"""End-to-end self-decomposition test.

Proves that a NON-manager worker dispatched as owner of a root task can
self-decompose: spawn a sub-task assigned to itself, be woken when that
sub-task completes, and then mark the whole task done.

Flow
-----
1. POST /tasks with owner="dev_agent"  →  root task (task_type=task)
2. dev_agent (step 0): emits delegate-to-self →  child (task_type=subtask)
3. dev_agent as subtask (leaf): emits plain completion, no decision
4. dev_agent (step 1, root woken): emits done → root completes

Assertions
-----------
- Child has assigned_agent == "dev_agent" and task_type == "subtask"
- Root reaches status == "completed" with note "all phases complete"
- Root orchestration_step_count == 2 (declare + final wake)
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from tests.integration.conftest import seed_workspace

pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def _submit_task_with_owner(base: str, brief: str, team: str, owner: str) -> str:
    headers = _auth_headers()
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": brief, "team": team, "owner": owner},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _wait_for_terminal(
    base: str,
    task_id: str,
    timeout: float = 90.0,
) -> dict:
    """Poll until the task reaches completed, failed, or blocked(escalated)."""
    headers = _auth_headers()
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
        body = r.json()
        task = body["task"]
        status = task["status"]
        block_kind = task.get("block_kind")
        if status in ("completed", "failed"):
            return body
        if status == "blocked" and block_kind == "escalated":
            return body
        time.sleep(0.3)
    raise AssertionError(
        f"task {task_id} did not reach terminal state within {timeout}s "
        f"(last={body})"
    )


def _count_audit_rows(body: dict, action: str) -> int:
    return sum(1 for row in body.get("audit_log", []) if row.get("action") == action)


def test_worker_self_decompose_via_subtask_e2e(
    live_daemon,
    runtime,
    fake_claude_plan_env,
) -> None:
    """dev_agent (non-manager, dispatched as owner) self-delegates a sub-task,
    the sub-task completes as a leaf, and the root is woken to declare done.

    Expected orchestration_step_count on root: 2
      Step 1: dev_agent declares self-delegation (delegate)
      Step 2: dev_agent woken after subtask completes (done)
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    seed_workspace(runtime, "dev_agent")

    # State file: tracks how many times dev_agent has been called as the ROOT task owner.
    # dev_agent will also be called as the SUB-TASK agent. We distinguish them by
    # checking whether the task has a parent (non-zero parentage counter increments
    # only for the root invocations). Since we can't easily call the HTTP API from
    # inside the bash plan without auth, we instead rely on the invocation ORDER:
    #
    #   Call 1: root, step 0 — dev_agent's first invocation overall
    #   Call 2: subtask (child) — dev_agent's second overall invocation
    #   Call 3: root, step 1 (woken) — dev_agent's third overall invocation
    #
    # We track total dev_agent invocation count with a single state file, then
    # branch:  count=1 → root step 0, count=2 → subtask leaf, count=3 → root step 1.
    dev_state = fake_claude_plan_env.parent / "dev_agent_call_count.txt"

    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"\n'
        f'dev_state="{dev_state}"\n'
        '\n'
        'if [[ "$agent" != "dev_agent" ]]; then\n'
        '    echo "ERROR: unexpected agent $agent" >&2\n'
        '    exit 1\n'
        'fi\n'
        '\n'
        '# Increment persistent call counter.\n'
        'if [[ -f "$dev_state" ]]; then\n'
        '    count=$(cat "$dev_state")\n'
        'else\n'
        '    count=0\n'
        'fi\n'
        'count=$((count + 1))\n'
        'echo "$count" > "$dev_state"\n'
        '\n'
        'tmpfile=$(mktemp)\n'
        '\n'
        'if [[ "$count" -eq 1 ]]; then\n'
        # Root, step 0: self-delegate to dev_agent (spawn a sub-task).
        '    cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"dev_agent","status":"completed","summary":"starting phase 1","confidence":80,"decision":{"action":"delegate","agent":"dev_agent","prompt":"phase 2 of the work"}}\n'
        '__JSON__\n'
        '\n'
        'elif [[ "$count" -eq 2 ]]; then\n'
        # Subtask (child), leaf: plain completion, no decision.
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"dev_agent","status":"completed","summary":"phase 2 done","confidence":90}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '\n'
        'elif [[ "$count" -eq 3 ]]; then\n'
        # Root, step 1: woken after subtask completes. Declare done.
        '    cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"dev_agent","status":"completed","summary":"all phases complete","confidence":95,"decision":{"action":"done","summary":"all phases complete"}}\n'
        '__JSON__\n'
        '\n'
        'else\n'
        '    echo "ERROR: dev_agent called unexpected number of times: $count" >&2\n'
        '    exit 1\n'
        'fi\n'
        '\n'
        '# Replace __TID__ / __SID__ placeholders (only present in heredoc blocks).\n'
        'sed -i "" "s/__TID__/$task_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__TID__/$task_id/g" "$tmpfile"\n'
        'sed -i "" "s/__SID__/$session_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__SID__/$session_id/g" "$tmpfile"\n'
        'happyranch report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        'rm -f "$tmpfile"\n'
    )
    fake_claude_plan_env.chmod(0o755)

    # Dispatch root task directly to dev_agent (non-manager worker as owner).
    root_id = _submit_task_with_owner(
        base,
        brief="implement the feature in two phases",
        team="engineering",
        owner="dev_agent",
    )

    body = _wait_for_terminal(base, root_id, timeout=90.0)
    task = body["task"]

    # --- Assertion 1: root completes ---
    assert task["status"] == "completed", (
        f"expected completed, got {task['status']!r} (note={task.get('note')!r})"
    )
    assert task.get("note") == "all phases complete", (
        f"expected note='all phases complete', got {task.get('note')!r}"
    )

    # --- Assertion 2: orchestration_step_count == 2 ---
    assert task["orchestration_step_count"] == 2, (
        f"expected step_count=2, got {task['orchestration_step_count']}"
    )

    # --- Assertion 3: child sub-task exists with correct agent and task_type ---
    headers = _auth_headers()
    all_tasks = httpx.get(f"{base}/tasks", headers=headers, timeout=5.0).json()["tasks"]
    children = [t for t in all_tasks if t.get("parent_task_id") == root_id]

    assert len(children) == 1, (
        f"expected exactly 1 child task, got {len(children)}: {children}"
    )
    child_summary = children[0]

    assert child_summary["assigned_agent"] == "dev_agent", (
        f"expected child assigned_agent='dev_agent', got {child_summary['assigned_agent']!r}"
    )
    assert child_summary["status"] == "completed", (
        f"expected child status='completed', got {child_summary['status']!r}"
    )

    # Fetch the child detail to verify task_type (list endpoint omits task_type;
    # the detail endpoint's get_task includes it).
    child_id = child_summary["task_id"]
    child_detail = httpx.get(
        f"{base}/tasks/{child_id}", headers=headers, timeout=5.0,
    ).json()["task"]
    assert child_detail["task_type"] == "subtask", (
        f"expected child task_type='subtask', got {child_detail['task_type']!r}"
    )
