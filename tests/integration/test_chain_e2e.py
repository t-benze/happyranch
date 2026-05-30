"""End-to-end chain tests using the fake-claude harness.

These tests spawn a real daemon + fake CLI workers and exercise the inline
delegation chain feature end-to-end. They verify:
- Auto-advance fires len(legs)-1 times when all verdicts match
- Parent orchestration_step_count grows by exactly 2 per clean chain (declare + final wake)
- Verdict mismatch aborts chain immediately
- Founder cancel mid-chain clears active_chain and ends parent in failed-cancelled

Agent roster used: engineering team workers already in the conftest's teams.yaml
  engineering_head (manager), dev_agent, payment_agent, qa_engineer.
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


def _submit_task(base: str, brief: str, team: str = "engineering") -> str:
    headers = _auth_headers()
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": brief, "team": team},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _wait_for_terminal(
    base: str,
    task_id: str,
    timeout: float = 60.0,
) -> dict:
    """Poll until task reaches completed, failed, or blocked(escalated). Returns full body dict."""
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


def _wait_for_child_agent(
    base: str,
    parent_task_id: str,
    agent: str,
    timeout: float = 30.0,
) -> str:
    """Poll until a child task assigned to `agent` appears under `parent_task_id`.
    Returns the child task_id."""
    headers = _auth_headers()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks", headers=headers, timeout=5.0)
        for t in r.json().get("tasks", []):
            if t.get("parent_task_id") == parent_task_id and t.get("assigned_agent") == agent:
                return t["task_id"]
        time.sleep(0.25)
    raise AssertionError(
        f"no child task for agent={agent!r} under parent={parent_task_id!r} within {timeout}s"
    )


def _wait_for_task_status(
    base: str,
    task_id: str,
    target_status: str,
    timeout: float = 30.0,
) -> None:
    headers = _auth_headers()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
        if r.json()["task"]["status"] == target_status:
            return
        time.sleep(0.25)
    raise AssertionError(
        f"task {task_id} did not reach status={target_status!r} within {timeout}s"
    )


def _count_audit_rows(body: dict, action: str) -> int:
    return sum(1 for row in body.get("audit_log", []) if row.get("action") == action)


def test_chain_happy_path_e2e(
    live_daemon,
    runtime,
    fake_claude_plan_env,
) -> None:
    """Manager declares a 3-leg chain; all workers pass verdict checks;
    chain auto-advances twice; manager wakes once at the end.

    Chain layout:
      Leg 1 (implicit first): dev_agent      (no expect_verdict)
      Leg 2: payment_agent   (expect_verdict="APPROVE")
      Leg 3: qa_engineer     (expect_verdict="PASS")

    Expected orchestration_step_count on the parent task:
      Step 1: manager declares chain (delegate)
      Step 2: manager wakes after chain complete (done)
      Total: 2
    Expected chain_auto_advance audit rows: 2
      (dev_agent→payment_agent and payment_agent→qa_engineer)

    Workers used are from the engineering team as defined in conftest's teams.yaml:
      engineering_head (manager), dev_agent, payment_agent, qa_engineer.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    seed_workspace(runtime, "engineering_head")
    seed_workspace(runtime, "dev_agent")
    seed_workspace(runtime, "payment_agent")
    seed_workspace(runtime, "qa_engineer")

    # State file: how many times has engineering_head been called?
    eh_state = fake_claude_plan_env.parent / "eh_chain_happy_step.txt"

    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"\n'
        f'eh_state="{eh_state}"\n'
        '\n'
        'if [[ "$agent" == "engineering_head" ]]; then\n'
        '    if [[ -f "$eh_state" ]]; then\n'
        '        step=$(cat "$eh_state")\n'
        '    else\n'
        '        step=0\n'
        '    fi\n'
        '    next=$((step+1))\n'
        '    echo "$next" > "$eh_state"\n'
        '\n'
        '    tmpfile=$(mktemp)\n'
        '    if [[ "$step" -eq 0 ]]; then\n'
        # Step 1: declare 3-leg chain.
        # Leg 1 (implicit first): dev_agent, no expect_verdict.
        # Leg 2: payment_agent, expect_verdict="APPROVE".
        # Leg 3: qa_engineer, expect_verdict="PASS".
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"engineering_head","status":"completed","summary":"chain declared","confidence":90,"decision":{"action":"delegate","agent":"dev_agent","prompt":"implement the feature","then":[{"agent":"payment_agent","prompt":"review the implementation","expect_verdict":"APPROVE"},{"agent":"qa_engineer","prompt":"qa the implementation","expect_verdict":"PASS"}]}}\n'
        '__JSON__\n'
        '    else\n'
        # Step 2: chain complete, just finish.
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"engineering_head","status":"completed","summary":"chain ran to completion","confidence":90,"decision":{"action":"done","summary":"chain ran to completion"}}\n'
        '__JSON__\n'
        '    fi\n'
        '    sed -i "" "s/__TID__/$task_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__TID__/$task_id/g" "$tmpfile"\n'
        '    sed -i "" "s/__SID__/$session_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__SID__/$session_id/g" "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "dev_agent" ]]; then\n'
        # dev_agent: complete with no verdict (first leg, expect_verdict=None).
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"dev_agent","status":"completed","summary":"feature implemented","confidence":85}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "payment_agent" ]]; then\n'
        # payment_agent: complete with verdict="APPROVE" (matches expect_verdict).
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"payment_agent","status":"completed","summary":"code looks good","confidence":90,"verdict":"APPROVE"}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "qa_engineer" ]]; then\n'
        # qa_engineer: complete with verdict="PASS" (matches expect_verdict).
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"qa_engineer","status":"completed","summary":"qa passed","confidence":95,"verdict":"PASS"}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'else\n'
        '    echo "Unknown agent: $agent" >&2\n'
        '    exit 1\n'
        'fi\n'
    )
    fake_claude_plan_env.chmod(0o755)

    task_id = _submit_task(base, brief="implement and review a feature")
    body = _wait_for_terminal(base, task_id, timeout=60.0)
    task = body["task"]

    assert task["status"] == "completed", (
        f"expected completed, got {task['status']!r} (note={task.get('note')!r})"
    )

    # orchestration_step_count must be exactly 2: declare + final wake.
    assert task["orchestration_step_count"] == 2, (
        f"expected step_count=2, got {task['orchestration_step_count']}"
    )

    # Exactly 2 chain_auto_advance audit rows.
    advance_count = _count_audit_rows(body, "chain_auto_advance")
    assert advance_count == 2, (
        f"expected 2 chain_auto_advance rows, got {advance_count}. "
        f"audit={body.get('audit_log')}"
    )

    # active_chain cleared after completion (the raw field on the task row).
    assert task.get("active_chain") is None, (
        f"active_chain should be None after completion, got {task.get('active_chain')!r}"
    )

    # Verify all 3 worker child tasks were spawned and completed.
    all_tasks = httpx.get(f"{base}/tasks", headers=_auth_headers(), timeout=5.0).json()["tasks"]
    children = [t for t in all_tasks if t.get("parent_task_id") == task_id]
    child_agents = {c["assigned_agent"] for c in children}
    assert child_agents == {"dev_agent", "payment_agent", "qa_engineer"}, (
        f"unexpected child agents: {child_agents}"
    )
    for c in children:
        assert c["status"] == "completed", (
            f"child {c['task_id']} ({c['assigned_agent']}) expected completed, "
            f"got {c['status']!r}"
        )


def test_chain_aborts_on_verdict_mismatch_e2e(
    live_daemon,
    runtime,
    fake_claude_plan_env,
) -> None:
    """Manager declares 3-leg chain; payment_agent reports REQUEST_CHANGES instead of
    APPROVE; chain aborts at leg 2, parent wakes immediately with mismatch context.

    Chain layout:
      Leg 1: dev_agent       (no expect_verdict) — completes fine
      Leg 2: payment_agent   (expect_verdict="APPROVE") — reports "REQUEST_CHANGES" → MISMATCH
      Leg 3: qa_engineer     — should NOT be spawned

    Expected:
      - 1 chain_auto_advance row (dev_agent→payment_agent only)
      - active_chain cleared after mismatch
      - qa_engineer child task does NOT exist
      - parent ends at blocked(escalated) — manager chose to escalate after wake
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    seed_workspace(runtime, "engineering_head")
    seed_workspace(runtime, "dev_agent")
    seed_workspace(runtime, "payment_agent")
    seed_workspace(runtime, "qa_engineer")

    eh_state = fake_claude_plan_env.parent / "eh_chain_mismatch_step.txt"

    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"\n'
        f'eh_state="{eh_state}"\n'
        '\n'
        'if [[ "$agent" == "engineering_head" ]]; then\n'
        '    if [[ -f "$eh_state" ]]; then\n'
        '        step=$(cat "$eh_state")\n'
        '    else\n'
        '        step=0\n'
        '    fi\n'
        '    next=$((step+1))\n'
        '    echo "$next" > "$eh_state"\n'
        '\n'
        '    tmpfile=$(mktemp)\n'
        '    if [[ "$step" -eq 0 ]]; then\n'
        # Same 3-leg chain declaration.
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"engineering_head","status":"completed","summary":"chain declared","confidence":90,"decision":{"action":"delegate","agent":"dev_agent","prompt":"implement the feature","then":[{"agent":"payment_agent","prompt":"review the implementation","expect_verdict":"APPROVE"},{"agent":"qa_engineer","prompt":"qa the implementation","expect_verdict":"PASS"}]}}\n'
        '__JSON__\n'
        '    else\n'
        # Manager wakes after mismatch — escalate (simple terminal for the test).
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"engineering_head","status":"completed","summary":"verdict mismatch noted, escalating","confidence":50,"decision":{"action":"escalate","reason":"payment_agent requested changes — needs founder review"}}\n'
        '__JSON__\n'
        '    fi\n'
        '    sed -i "" "s/__TID__/$task_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__TID__/$task_id/g" "$tmpfile"\n'
        '    sed -i "" "s/__SID__/$session_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__SID__/$session_id/g" "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "dev_agent" ]]; then\n'
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"dev_agent","status":"completed","summary":"feature implemented","confidence":85}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "payment_agent" ]]; then\n'
        # payment_agent: reports REQUEST_CHANGES instead of APPROVE → verdict mismatch.
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"payment_agent","status":"completed","summary":"needs refactoring","confidence":70,"verdict":"REQUEST_CHANGES"}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "qa_engineer" ]]; then\n'
        # Should NOT be reached if chain aborts correctly.
        '    echo "ERROR: qa_engineer should not have been called (chain should have aborted)" >&2\n'
        '    exit 1\n'
        '\n'
        'else\n'
        '    echo "Unknown agent: $agent" >&2\n'
        '    exit 1\n'
        'fi\n'
    )
    fake_claude_plan_env.chmod(0o755)

    task_id = _submit_task(base, brief="implement and review a feature (mismatch test)")
    body = _wait_for_terminal(base, task_id, timeout=60.0)
    task = body["task"]

    # Parent ends blocked(escalated) because manager chose escalate after mismatch.
    assert task["status"] == "blocked" and task.get("block_kind") == "escalated", (
        f"expected blocked(escalated), got status={task['status']!r} "
        f"block_kind={task.get('block_kind')!r} (note={task.get('note')!r})"
    )

    # Exactly 1 chain_auto_advance row: dev_agent→payment_agent only.
    advance_count = _count_audit_rows(body, "chain_auto_advance")
    assert advance_count == 1, (
        f"expected 1 chain_auto_advance row, got {advance_count}. "
        f"audit={body.get('audit_log')}"
    )

    # active_chain cleared after mismatch.
    assert task.get("active_chain") is None, (
        f"active_chain should be None after mismatch, got {task.get('active_chain')!r}"
    )

    # qa_engineer should NOT have been spawned.
    all_tasks = httpx.get(f"{base}/tasks", headers=_auth_headers(), timeout=5.0).json()["tasks"]
    children = [t for t in all_tasks if t.get("parent_task_id") == task_id]
    child_agents = {c["assigned_agent"] for c in children}
    assert "qa_engineer" not in child_agents, (
        f"qa_engineer should not have been spawned, but found: {child_agents}"
    )
    # dev_agent and payment_agent should exist.
    assert "dev_agent" in child_agents, f"dev_agent child missing: {child_agents}"
    assert "payment_agent" in child_agents, f"payment_agent child missing: {child_agents}"


def test_chain_aborts_on_founder_cancel_e2e(
    live_daemon,
    runtime,
    fake_claude_plan_env,
) -> None:
    """Founder cancels parent during leg 2 (payment_agent) in-flight.
    active_chain is cleared, no leg 3 (qa_engineer) spawn, parent ends failed-cancelled.

    Chain layout:
      Leg 1: dev_agent      (runs fast, completes)
      Leg 2: payment_agent  (sleeps; cancel arrives during sleep)
      Leg 3: qa_engineer    — should NOT be spawned

    The test waits until the payment_agent child task exists (auto-advanced from
    dev_agent completing), then cancels the parent with cascade=True while
    payment_agent's fake subprocess is sleeping.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    seed_workspace(runtime, "engineering_head")
    seed_workspace(runtime, "dev_agent")
    seed_workspace(runtime, "payment_agent")
    seed_workspace(runtime, "qa_engineer")

    fake_claude_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"\n'
        '\n'
        'if [[ "$agent" == "engineering_head" ]]; then\n'
        '    tmpfile=$(mktemp)\n'
        # Manager always declares the 3-leg chain on its first invocation.
        '    cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"engineering_head","status":"completed","summary":"chain declared","confidence":90,"decision":{"action":"delegate","agent":"dev_agent","prompt":"implement the feature","then":[{"agent":"payment_agent","prompt":"review the implementation"},{"agent":"qa_engineer","prompt":"qa the implementation"}]}}\n'
        '__JSON__\n'
        '    sed -i "" "s/__TID__/$task_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__TID__/$task_id/g" "$tmpfile"\n'
        '    sed -i "" "s/__SID__/$session_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__SID__/$session_id/g" "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "dev_agent" ]]; then\n'
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"dev_agent","status":"completed","summary":"feature implemented","confidence":85}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "payment_agent" ]]; then\n'
        # payment_agent sleeps long enough for the cancel to arrive.
        # The cancel route sends SIGTERM to kill this subprocess.
        '    sleep 30\n'
        # If SIGTERM kills the sleep, set -e causes bash to exit without
        # calling report-completion, leaving the parent correctly cancelled.
        '    tmpfile=$(mktemp)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"payment_agent","status":"completed","summary":"review done","confidence":90,"verdict":"APPROVE"}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    grassland report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "qa_engineer" ]]; then\n'
        # Should NOT be reached — if cancel works correctly.
        '    echo "ERROR: qa_engineer should not have been called (chain should have been cancelled)" >&2\n'
        '    exit 1\n'
        '\n'
        'else\n'
        '    echo "Unknown agent: $agent" >&2\n'
        '    exit 1\n'
        'fi\n'
    )
    fake_claude_plan_env.chmod(0o755)

    task_id = _submit_task(base, brief="implement and review a feature (cancel test)")

    # Wait until dev_agent completes and chain auto-advances to payment_agent.
    # payment_agent child task is spawned by _advance_chain_for_completed_child.
    payment_agent_id = _wait_for_child_agent(
        base, task_id, agent="payment_agent", timeout=30.0,
    )

    # Give payment_agent a moment to be picked up by the orchestrator queue
    # and reach IN_PROGRESS (so a live PID exists for the cancel route to SIGTERM).
    _wait_for_task_status(base, payment_agent_id, target_status="in_progress", timeout=15.0)

    # Now cancel the parent (cascade=True terminates payment_agent too).
    r = httpx.post(
        f"{base}/tasks/{task_id}/cancel",
        json={"cascade": True, "rationale": "test cancel during chain leg 2"},
        headers=_auth_headers(),
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    cancel_body = r.json()
    assert cancel_body["ok"] is True

    # Parent should end up failed (with cancelled_at set = "failed-cancelled").
    body = _wait_for_terminal(base, task_id, timeout=30.0)
    task = body["task"]
    assert task["status"] == "failed", (
        f"expected failed (failed-cancelled), got {task['status']!r}"
    )
    assert task.get("cancelled_at") is not None, (
        "parent task should have cancelled_at set (failed-cancelled)"
    )

    # qa_engineer should NOT have been spawned.
    all_tasks = httpx.get(f"{base}/tasks", headers=_auth_headers(), timeout=5.0).json()["tasks"]
    children = [t for t in all_tasks if t.get("parent_task_id") == task_id]
    child_agents = {c["assigned_agent"] for c in children}
    assert "qa_engineer" not in child_agents, (
        f"qa_engineer should not have been spawned, but found: {child_agents}"
    )
