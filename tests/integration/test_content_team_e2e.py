"""Integration test for Content Team MVP PASS flow.

Spawns a real daemon with fake Claude binaries scripted to simulate the
PASS path: CM → writer → QA(PASS) → CM done → task COMPLETED.

Uses the same harness as ``test_end_to_end.py`` (live_daemon + fake_claude).
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.integration.conftest import seed_workspace

pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from runtime.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def _submit_task(base: str, brief: str, team: str) -> str:
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
) -> str:
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
            return status
        if status == "escalated":
            return "blocked"
        time.sleep(0.3)
    raise AssertionError(
        f"task {task_id} did not reach terminal state within {timeout}s "
        f"(last={body})"
    )


def test_content_team_pass_path_completes(
    live_daemon,
    runtime,
    fake_claude_plan_env,
) -> None:
    """Content Team PASS path: CM → writer → QA(PASS) → CM done → COMPLETED."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    # Seed workspaces for all three content agents.
    seed_workspace(runtime, "content_manager")
    seed_workspace(runtime, "content_writer")
    seed_workspace(runtime, "content_qa")

    # State file: track how many times CM has been called so it can step through
    # its 3 decision phases: delegate-to-writer → delegate-to-qa → done.
    cm_state_file = fake_claude_plan_env.parent / "cm_step.txt"

    plan = fake_claude_plan_env
    # Write a plan script that:
    # - For content_manager: checks a counter file to decide which step to emit.
    # - For content_writer: completes with a simple summary.
    # - For content_qa: completes with a PASS verdict.
    # All agents use happyranch report-completion --from-file <json>.
    plan.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id="$1"; session_id="$2"; agent="$3"; org_slug="$4"\n'
        f'cm_state="{cm_state_file}"\n'
        '\n'
        'if [[ "$agent" == "content_manager" ]]; then\n'
        '    # Read current CM step (0=delegate-writer, 1=delegate-qa, 2=done)\n'
        '    if [[ -f "$cm_state" ]]; then\n'
        '        step=$(cat "$cm_state")\n'
        '    else\n'
        '        step=0\n'
        '    fi\n'
        '    next=$((step+1))\n'
        '    echo "$next" > "$cm_state"\n'
        '\n'
        '    tmpfile=$(mktemp /tmp/completion-XXXXXX.json)\n'
        '    if [[ "$step" -eq 0 ]]; then\n'
        '        # Step 1: delegate to content_writer\n'
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"content_manager","status":"completed","summary":"delegating to writer","confidence":90,"decision":{"action":"delegate","agent":"content_writer","prompt":"Write a comprehensive Macau visa guide for UK tourists"}}\n'
        '__JSON__\n'
        '    elif [[ "$step" -eq 1 ]]; then\n'
        '        # Step 2: delegate to content_qa\n'
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"content_manager","status":"completed","summary":"delegating to QA","confidence":90,"decision":{"action":"delegate","agent":"content_qa","prompt":"Review the draft"}}\n'
        '__JSON__\n'
        '    else\n'
        '        # Step 3: done\n'
        '        cat > "$tmpfile" << \'__JSON__\'\n'
        '{"task_id":"__TID__","session_id":"__SID__","agent":"content_manager","status":"completed","summary":"content approved","confidence":90,"decision":{"action":"done","summary":"content approved"}}\n'
        '__JSON__\n'
        '    fi\n'
        '    # Substitute real task_id and session_id into the JSON\n'
        '    sed -i "" "s/__TID__/$task_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__TID__/$task_id/g" "$tmpfile"\n'
        '    sed -i "" "s/__SID__/$session_id/g" "$tmpfile" 2>/dev/null || sed -i "s/__SID__/$session_id/g" "$tmpfile"\n'
        '    happyranch report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "content_writer" ]]; then\n'
        '    tmpfile=$(mktemp /tmp/completion-XXXXXX.json)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"content_writer","status":"completed","summary":"Draft completed","confidence":85}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    happyranch report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'elif [[ "$agent" == "content_qa" ]]; then\n'
        '    tmpfile=$(mktemp /tmp/completion-XXXXXX.json)\n'
        '    printf \'{"task_id":"%s","session_id":"%s","agent":"content_qa","status":"completed","summary":"VERDICT: PASS - content is accurate","confidence":90}\' "$task_id" "$session_id" > "$tmpfile"\n'
        '    happyranch report-completion --org "$org_slug" --from-file "$tmpfile"\n'
        '    rm -f "$tmpfile"\n'
        '\n'
        'else\n'
        '    echo "Unknown agent: $agent" >&2\n'
        '    exit 1\n'
        'fi\n'
    )
    plan.chmod(0o755)

    task_id = _submit_task(base, brief="Write Macau visa guide for UK tourists", team="content")
    final_status = _wait_for_terminal(base, task_id, timeout=60.0)

    assert final_status == "completed", (
        f"expected COMPLETED, got {final_status!r}. "
        f"Check daemon logs or GET /tasks/{task_id} for details."
    )

    # Verify final task state via the tasks endpoint.
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "completed"
    # Note: output_dir assertion is skipped — fake_claude.sh does not create output
    # files on disk, so happyranch recall --fetch-output would 404. The output_dir
    # creation contract is verified by the unit tests (which control the full
    # CompletionReport). Tracking concern: if a later task adds output-writing
    # to fake_claude.sh, add an happyranch recall assertion here.
