"""End-to-end review-required job lifecycle: agent submits, founder runs, founder revisits.

Covers the full happy path:
  1. Dispatch a task to engineering_head.
  2. Fake agent submits a review_required=true job (via happyranch jobs submit)
     and self-blocks (report-completion status=blocked → task becomes FAILED).
  3. Founder lists pending jobs, runs the job via POST /jobs/{id}/run.
  4. Job reaches completed + exit_code==0.
  5. Audit log for the task contains job_submitted + job_run_completed.
  6. Founder revisits the failed task; the new root's audit entry links back
     to the predecessor, which had the job — confirming the revisit chain.
"""
from __future__ import annotations

import json
import time
from textwrap import dedent

import httpx
import pytest

from tests.integration.conftest import seed_workspace, DEFAULT_TEST_SLUG


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def _wait_for_task_status(
    base: str,
    task_id: str,
    *,
    terminal: tuple[str, ...] = ("completed", "failed"),
    timeout: float = 30.0,
) -> dict:
    """Poll GET /tasks/{task_id} until the task reaches one of the terminal statuses."""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
        body = r.json()
        task = body["task"]
        if task["status"] in terminal:
            return body
        time.sleep(0.2)
    raise AssertionError(
        f"task {task_id} did not reach {terminal} within {timeout}s; last body={body}"
    )


def _wait_for_job_terminal(
    base: str,
    job_id: str,
    *,
    timeout: float = 20.0,
) -> dict:
    """Poll GET /jobs/{job_id} until the job is completed or failed."""
    deadline = time.monotonic() + timeout
    d: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/jobs/{job_id}", headers=_auth_headers(), timeout=5.0)
        d = r.json()
        if d.get("status") in ("completed", "failed"):
            return d
        time.sleep(0.1)
    raise AssertionError(
        f"job {job_id} did not reach terminal within {timeout}s; last={d}"
    )


def test_review_required_job_lifecycle_submit_run_revisit(
    live_daemon,
    runtime,
    fake_claude_plan_env,
):
    """Agent submits review-required job → self-blocks → founder runs → revisit surfaces job."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    # ── 1. Seed the agent workspace (required for WorkspaceNotInitialized guard)
    seed_workspace(runtime, "engineering_head")

    # ── 2. Write the FAKE_CLAUDE_PLAN that submits a review-required job and self-blocks.
    #
    # The plan receives: $1=task_id $2=session_id $3=agent $4=org_slug
    #
    # We use printf + a temp file to build the JSON payload so here-docs with
    # variable expansion stay simple, and the payload is a single happyranch
    # invocation (required by the Claude permission model).
    fake_claude_plan_env.write_text(dedent("""\
        #!/usr/bin/env bash
        set -e
        task_id="$1"
        session_id="$2"
        agent="$3"
        org_slug="$4"

        # Write the submit payload to a temp file.
        payload="/tmp/job-e2e-payload-$$.json"
        printf '{
          "task_id": "%s",
          "session_id": "%s",
          "title": "touch e2e sentinel",
          "rationale": "integration test needs founder approval",
          "script": "touch /tmp/happyranch-job-e2e-sentinel",
          "interpreter": "bash",
          "review_required": true
        }' "$task_id" "$session_id" > "$payload"

        # Submit the job (agent callback).
        happyranch jobs submit --from-file "$payload" --org "$org_slug" \\
            > /tmp/job-e2e-submit-$$.log 2>&1

        # Extract job id from submit output, e.g. "ok: submitted JOB-001 (status=pending)."
        job_id=$(grep -oE 'JOB-[0-9]+' /tmp/job-e2e-submit-$$.log | head -1)
        if [[ -z "$job_id" ]]; then
            echo "ERROR: could not parse JOB id from submit output" >&2
            cat /tmp/job-e2e-submit-$$.log >&2
            exit 1
        fi

        # Self-block: tell the orchestrator we are waiting on the founder.
        report="/tmp/job-e2e-completion-$$.json"
        printf '{
          "task_id": "%s",
          "session_id": "%s",
          "agent": "%s",
          "status": "blocked",
          "summary": "Awaiting %s",
          "confidence": 50,
          "risks_flagged": [],
          "dependencies": [],
          "suggested_reviewer_focus": []
        }' "$task_id" "$session_id" "$agent" "$job_id" > "$report"

        happyranch report-completion --from-file "$report" --org "$org_slug"
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── 3. Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "touch the e2e sentinel", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── 4. Wait for the task to reach a terminal state.
    #       When the agent self-blocks, run_step calls _fail() → status=failed.
    body = _wait_for_task_status(base, task_id, terminal=("failed",), timeout=30.0)
    assert body["task"]["status"] == "failed", body["task"]

    # ── 5. Find the pending job submitted by the agent.
    r = httpx.get(
        f"{base}/jobs/",
        params={"status": "pending", "task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) >= 1, f"expected >=1 pending job for task {task_id}, got {jobs}"
    job_id = jobs[0]["id"]
    assert job_id.startswith("JOB-"), f"expected JOB- prefix, got {job_id!r}"

    # ── 6. Founder approves + runs the job.
    r = httpx.post(
        f"{base}/jobs/{job_id}/run",
        json={"timeout_seconds": 10},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 202, r.text

    # ── 7. Wait for the job to reach a terminal state.
    job_detail = _wait_for_job_terminal(base, job_id, timeout=20.0)
    assert job_detail["status"] == "completed", job_detail
    assert job_detail["exit_code"] == 0, job_detail

    # ── 8. Audit log contains job_submitted + job_run_completed.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]
    assert "job_submitted" in actions, f"missing job_submitted in audit; actions={actions}"
    assert "job_run_completed" in actions, (
        f"missing job_run_completed in audit; actions={actions}"
    )

    # ── 9. Confirm the job's task_id link and job_id are correct in the audit.
    submitted_entry = next(e for e in entries if e["action"] == "job_submitted")
    payload_raw = submitted_entry.get("payload") or {}
    if isinstance(payload_raw, str):
        payload_raw = json.loads(payload_raw)
    assert payload_raw.get("script_request_id") == job_id, (
        f"audit job_submitted.script_request_id mismatch: {payload_raw}"
    )

    # ── 10. Founder revisits the failed task.
    r = httpx.post(
        f"{base}/tasks/{task_id}/revisit",
        json={"founder_note": "job ran — rerun with output context"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    revisit_payload = r.json()
    new_task_id = revisit_payload.get("new_root_task_id")
    assert new_task_id, f"expected new_root_task_id in revisit response; got {revisit_payload}"

    # ── 11. Verify the revisit task's audit log has a revisit_of entry
    #        that references the predecessor task.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": new_task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    new_entries = r.json()["entries"]
    revisit_of_entries = [e for e in new_entries if e["action"] == "revisit_of"]
    assert revisit_of_entries, (
        f"new task {new_task_id} has no revisit_of audit entry; actions={[e['action'] for e in new_entries]}"
    )
    revisit_of_payload = revisit_of_entries[0].get("payload") or {}
    if isinstance(revisit_of_payload, str):
        revisit_of_payload = json.loads(revisit_of_payload)
    # The revisit_of entry's predecessor_root should point back to the original task.
    predecessor_root = revisit_of_payload.get("predecessor_root")
    assert predecessor_root == task_id, (
        f"revisit_of.predecessor_root={predecessor_root!r} expected {task_id!r}"
    )

    # ── 12. Confirm the job id appears in the predecessor's audit log
    #        (cross-check that _revisit_header_if_applicable can find it).
    assert any(
        (e.get("payload") or {}).get("script_request_id") == job_id
        for e in entries
        if e["action"] == "job_submitted"
    ), f"job_submitted entry missing script_request_id={job_id!r}"
