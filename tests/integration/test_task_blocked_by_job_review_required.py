"""End-to-end tests for the review_required=true blocked-by-job flow.

Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §9.2

Covers two paths that require explicit founder action:

Scenario A — Founder approves:
  1. Agent submits a job with review_required=true.
  2. Agent self-blocks via report-completion with waiting_on_job_ids=[JOB-NNN].
  3. Test driver (acting as founder) calls POST /jobs/{id}/run to approve.
  4. Job runs and completes.
  5. Task auto-resumes (in_progress(blocked_on_job) → in_progress(NULL)).
  6. Next agent session sees BLOCKED-JOBS-RESULTS header (verified via audit).
  7. Task completes.

Scenario B — Founder rejects:
  1. Agent submits a job with review_required=true.
  2. Agent self-blocks via report-completion with waiting_on_job_ids=[JOB-NNN].
  3. Test driver (acting as founder) calls POST /jobs/{id}/reject.
  4. Task auto-resumes (rejected is terminal — predicate fires on job_terminal).
  5. Next agent session sees BLOCKED-JOBS-RESULTS header with "rejected" outcome.
  6. Task completes (agent adapts to the rejection).
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
    from runtime.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def _wait_for_task_status(
    base: str,
    task_id: str,
    *,
    terminal: tuple[str, ...] = ("completed", "failed"),
    timeout: float = 60.0,
) -> dict:
    """Poll GET /tasks/{task_id} until the task reaches one of the target statuses."""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
        body = r.json()
        task = body.get("task", {})
        if task.get("status") in terminal:
            return body
        time.sleep(0.2)
    raise AssertionError(
        f"task {task_id} did not reach {terminal} within {timeout}s; last body={body}"
    )


def test_review_required_founder_approves_then_resumes(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    tmp_path,
):
    """Founder approves the pending review_required job → it runs → task resumes.

    Audit log must contain:
      job_submitted, task_blocked_on_jobs, job_run_started,
      job_run_completed, task_resumed_from_jobs.
    Task must finish with status=completed.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    # ── 1. Seed the agent workspace.
    seed_workspace(runtime, "engineering_head")

    # ── 2. Counter file: distinguishes stage 1 from stage 2.
    counter_file = tmp_path / "invocation_counter"
    jobid_file = tmp_path / "invocation_counter.jobid"

    # ── 3. Write the two-stage fake_claude plan.
    #
    # Stage 1: agent submits review_required=true job and blocks.
    #   - The review_required job stays PENDING after submit (no auto-run).
    #   - The agent then self-blocks with waiting_on_job_ids.
    #   - It writes the job_id to a file so the test driver can act.
    # Between stage 1 and stage 2: the TEST DRIVER (this process) calls
    #   POST /jobs/{id}/run (founder approval) — that starts the job.
    # Stage 2: agent sees BLOCKED-JOBS-RESULTS header → completes.
    fake_claude_plan_env.write_text(dedent(f"""\
        #!/usr/bin/env bash
        set -e
        task_id="$1"
        session_id="$2"
        agent="$3"
        org_slug="$4"

        counter="{counter_file}"
        n=$(cat "$counter" 2>/dev/null || echo 0)
        n=$((n + 1))
        echo "$n" > "$counter"

        if [ "$n" = "1" ]; then
            # ── Stage 1: submit review_required=true job + self-block ──

            # Submit a job that needs founder review (review_required=true).
            payload="/tmp/blocked-by-job-rr-approve-submit-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "title": "review-required approve e2e job",
              "rationale": "needs founder approval before running — integration test",
              "script": "echo review-required-job-ran",
              "interpreter": "bash",
              "review_required": true,
              "persistent": false
            }}' "$task_id" "$session_id" > "$payload"

            submit_log="/tmp/blocked-by-job-rr-approve-submit-log-$$.txt"
            happyranch jobs submit --from-file "$payload" --org "$org_slug" > "$submit_log" 2>&1
            cat "$submit_log" >&2

            job_id=$(grep -oE 'JOB-[0-9]+' "$submit_log" | head -1)
            if [ -z "$job_id" ]; then
                echo "ERROR: could not parse JOB id from submit output" >&2
                cat "$submit_log" >&2
                exit 1
            fi
            echo "Stage 1: submitted $job_id (review_required=true)" >&2

            # Record the job_id for the test driver to pick up and act on.
            echo "$job_id" > "{jobid_file}"

            # Self-block with waiting_on_job_ids via direct HTTP call.
            # The happyranch CLI report-completion --from-file path does not yet
            # expose waiting_on_job_ids; we POST to the daemon directly.
            port=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.port")
            token=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.token")

            completion_payload="/tmp/blocked-by-job-rr-approve-completion-$$.json"
            printf '{{
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "output_summary": "Waiting for %s to be reviewed and approved.",
              "risks_flagged": [],
              "dependencies": [],
              "suggested_reviewer_focus": [],
              "waiting_on_job_ids": ["%s"]
            }}' "$session_id" "$agent" "$job_id" "$job_id" > "$completion_payload"

            curl -s -X POST \\
                "http://127.0.0.1:$port/api/v1/orgs/$org_slug/tasks/$task_id/completion" \\
                -H "Authorization: Bearer $token" \\
                -H "Content-Type: application/json" \\
                -d @"$completion_payload" >&2
            echo "" >&2
            echo "Stage 1: blocked with waiting_on_job_ids=[$job_id]" >&2

        else
            # ── Stage 2: founder-approved job ran; complete the task ──
            echo "Stage 2: task resumed after founder-approved job, reporting completion" >&2

            happyranch report-completion --org "$org_slug" \\
                --task-id "$task_id" --session-id "$session_id" \\
                --agent "$agent" --status completed --confidence 90 \\
                --summary '{{"action":"done","summary":"completed after founder-approved job unblock"}}'
            echo "Stage 2: reported completed" >&2
        fi
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── 4. Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "review-required approve e2e test", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── 5. Wait for the job_id file to appear (stage 1 wrote it).
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if jobid_file.exists():
            break
        time.sleep(0.2)
    assert jobid_file.exists(), (
        "stage 1 plan never wrote the job_id file — fake_claude may have failed"
    )
    job_id = jobid_file.read_text().strip()
    assert job_id.startswith("JOB-"), f"unexpected job_id: {job_id!r}"

    # Wait for the task to reach blocked state (self-block must complete before
    # we act as founder, to avoid the task resuming before it's fully blocked).
    _wait_for_task_status(
        base, task_id,
        terminal=("in_progress",),  # Path B: parked on jobs = in_progress(blocked_on_job)
        timeout=20.0,
    )

    # ── 6. Founder action: approve (run) the pending review_required job.
    r = httpx.post(
        f"{base}/jobs/{job_id}/run",
        json={},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 202, (
        f"POST /jobs/{job_id}/run failed: {r.status_code} {r.text}"
    )

    # ── 7. Wait for the task to complete (job run + task resume + stage 2).
    body = _wait_for_task_status(base, task_id, terminal=("completed",), timeout=90.0)
    assert body["task"]["status"] == "completed", (
        f"expected completed, got: {body['task']}"
    )

    # ── 8. Verify audit log.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]

    # 8a. Agent submitted a job.
    assert "job_submitted" in actions, (
        f"missing job_submitted in audit; actions={actions}"
    )

    # 8b. Task was blocked on the job.
    assert "task_blocked_on_jobs" in actions, (
        f"missing task_blocked_on_jobs in audit; actions={actions}"
    )

    # 8c. Job ran and completed (after founder approval via /run).
    assert "job_run_started" in actions, (
        f"missing job_run_started (founder-triggered) in audit; actions={actions}"
    )
    assert "job_run_completed" in actions, (
        f"missing job_run_completed in audit; actions={actions}"
    )

    # 8d. Task was auto-resumed (proves CAS flip fired and BLOCKED-JOBS-RESULTS
    #     header was injected before stage 2).
    assert "task_resumed_from_jobs" in actions, (
        f"missing task_resumed_from_jobs in audit; actions={actions}"
    )

    # 8e. The resume audit row should reference the same job.
    submitted_entry = next(e for e in entries if e["action"] == "job_submitted")
    payload_raw = submitted_entry.get("payload") or {}
    if isinstance(payload_raw, str):
        payload_raw = json.loads(payload_raw)
    submitted_job_id = payload_raw.get("script_request_id")
    assert submitted_job_id == job_id, (
        f"job_submitted.script_request_id={submitted_job_id!r} != job_id={job_id!r}"
    )

    resumed_entry = next(e for e in entries if e["action"] == "task_resumed_from_jobs")
    resumed_payload = resumed_entry.get("payload") or {}
    if isinstance(resumed_payload, str):
        resumed_payload = json.loads(resumed_payload)
    blocking_ids = resumed_payload.get("blocking_job_ids", [])
    assert job_id in blocking_ids, (
        f"task_resumed_from_jobs.blocking_job_ids={blocking_ids!r} "
        f"does not include job_id={job_id!r}"
    )

    # 8f. Both stages ran.
    assert counter_file.exists(), "counter file was never created by fake_claude"
    assert counter_file.read_text().strip() == "2", (
        f"expected 2 invocations (stage1 + stage2), "
        f"counter={counter_file.read_text().strip()!r}"
    )

    # 8g. Trigger was "job_terminal" (Caller A fires after the job's terminal
    #     commit — the job was PENDING during blocking, so there's no instant
    #     block_submit race that could fire first).
    assert resumed_payload.get("trigger") == "job_terminal", (
        f"expected trigger=job_terminal (founder-approved flow), got: {resumed_payload}"
    )


def test_review_required_founder_rejects_then_resumes(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    tmp_path,
):
    """Founder rejects the pending job → task resumes with rejected outcome.

    Audit log must contain:
      job_submitted, task_blocked_on_jobs, job_rejected, task_resumed_from_jobs.
    task_resumed_from_jobs.job_outcomes must contain an entry with
    status="rejected". Task must finish with status=completed.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    # ── 1. Seed the agent workspace.
    seed_workspace(runtime, "engineering_head")

    # ── 2. Counter file.
    counter_file = tmp_path / "invocation_counter"
    jobid_file = tmp_path / "invocation_counter.jobid"

    # ── 3. Write the two-stage fake_claude plan.
    fake_claude_plan_env.write_text(dedent(f"""\
        #!/usr/bin/env bash
        set -e
        task_id="$1"
        session_id="$2"
        agent="$3"
        org_slug="$4"

        counter="{counter_file}"
        n=$(cat "$counter" 2>/dev/null || echo 0)
        n=$((n + 1))
        echo "$n" > "$counter"

        if [ "$n" = "1" ]; then
            # ── Stage 1: submit review_required=true job + self-block ──

            # Submit a job that needs founder review (review_required=true).
            payload="/tmp/blocked-by-job-rr-reject-submit-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "title": "review-required reject e2e job",
              "rationale": "needs founder review — will be rejected in integration test",
              "script": "echo this-job-will-be-rejected",
              "interpreter": "bash",
              "review_required": true,
              "persistent": false
            }}' "$task_id" "$session_id" > "$payload"

            submit_log="/tmp/blocked-by-job-rr-reject-submit-log-$$.txt"
            happyranch jobs submit --from-file "$payload" --org "$org_slug" > "$submit_log" 2>&1
            cat "$submit_log" >&2

            job_id=$(grep -oE 'JOB-[0-9]+' "$submit_log" | head -1)
            if [ -z "$job_id" ]; then
                echo "ERROR: could not parse JOB id from submit output" >&2
                cat "$submit_log" >&2
                exit 1
            fi
            echo "Stage 1: submitted $job_id (review_required=true)" >&2

            # Record the job_id for the test driver to pick up and reject.
            echo "$job_id" > "{jobid_file}"

            # Self-block with waiting_on_job_ids via direct HTTP call.
            port=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.port")
            token=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.token")

            completion_payload="/tmp/blocked-by-job-rr-reject-completion-$$.json"
            printf '{{
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "output_summary": "Waiting for %s to be reviewed — may be rejected.",
              "risks_flagged": [],
              "dependencies": [],
              "suggested_reviewer_focus": [],
              "waiting_on_job_ids": ["%s"]
            }}' "$session_id" "$agent" "$job_id" "$job_id" > "$completion_payload"

            curl -s -X POST \\
                "http://127.0.0.1:$port/api/v1/orgs/$org_slug/tasks/$task_id/completion" \\
                -H "Authorization: Bearer $token" \\
                -H "Content-Type: application/json" \\
                -d @"$completion_payload" >&2
            echo "" >&2
            echo "Stage 1: blocked with waiting_on_job_ids=[$job_id]" >&2

        else
            # ── Stage 2: job was rejected by founder; adapt and complete ──
            echo "Stage 2: task resumed after founder-rejected job, reporting completion" >&2

            happyranch report-completion --org "$org_slug" \\
                --task-id "$task_id" --session-id "$session_id" \\
                --agent "$agent" --status completed --confidence 90 \\
                --summary '{{"action":"done","summary":"completed despite job rejection — adapted plan"}}'
            echo "Stage 2: reported completed" >&2
        fi
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── 4. Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "review-required reject e2e test", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── 5. Wait for the job_id file to appear.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if jobid_file.exists():
            break
        time.sleep(0.2)
    assert jobid_file.exists(), (
        "stage 1 plan never wrote the job_id file — fake_claude may have failed"
    )
    job_id = jobid_file.read_text().strip()
    assert job_id.startswith("JOB-"), f"unexpected job_id: {job_id!r}"

    # Wait for the task to reach blocked state.
    _wait_for_task_status(
        base, task_id,
        terminal=("in_progress",),  # Path B: parked on jobs = in_progress(blocked_on_job)
        timeout=20.0,
    )

    # ── 6. Founder action: REJECT the pending job.
    r = httpx.post(
        f"{base}/jobs/{job_id}/reject",
        json={"reason": "not needed for e2e test scenario B"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, (
        f"POST /jobs/{job_id}/reject failed: {r.status_code} {r.text}"
    )
    assert r.json().get("status") == "rejected", (
        f"expected rejected status in reject response: {r.json()}"
    )

    # ── 7. Wait for the task to complete (resume from rejection + stage 2).
    body = _wait_for_task_status(base, task_id, terminal=("completed",), timeout=90.0)
    assert body["task"]["status"] == "completed", (
        f"expected completed, got: {body['task']}"
    )

    # ── 8. Verify audit log.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]

    # 8a. Agent submitted a job.
    assert "job_submitted" in actions, (
        f"missing job_submitted in audit; actions={actions}"
    )

    # 8b. Task was blocked on the job.
    assert "task_blocked_on_jobs" in actions, (
        f"missing task_blocked_on_jobs in audit; actions={actions}"
    )

    # 8c. Job was rejected by founder.
    assert "job_rejected" in actions, (
        f"missing job_rejected in audit; actions={actions}"
    )

    # 8d. Task was auto-resumed.
    assert "task_resumed_from_jobs" in actions, (
        f"missing task_resumed_from_jobs in audit; actions={actions}"
    )

    # 8e. The resume payload references the correct job.
    submitted_entry = next(e for e in entries if e["action"] == "job_submitted")
    payload_raw = submitted_entry.get("payload") or {}
    if isinstance(payload_raw, str):
        payload_raw = json.loads(payload_raw)
    submitted_job_id = payload_raw.get("script_request_id")
    assert submitted_job_id == job_id, (
        f"job_submitted.script_request_id={submitted_job_id!r} != job_id={job_id!r}"
    )

    resumed_entry = next(e for e in entries if e["action"] == "task_resumed_from_jobs")
    resumed_payload = resumed_entry.get("payload") or {}
    if isinstance(resumed_payload, str):
        resumed_payload = json.loads(resumed_payload)
    blocking_ids = resumed_payload.get("blocking_job_ids", [])
    assert job_id in blocking_ids, (
        f"task_resumed_from_jobs.blocking_job_ids={blocking_ids!r} "
        f"does not include job_id={job_id!r}"
    )

    # 8f. The outcomes in the resume payload show "rejected" for this job.
    job_outcomes = resumed_payload.get("job_outcomes", {})
    outcome_for_job = job_outcomes.get(job_id)
    assert outcome_for_job == "rejected", (
        f"expected job_outcomes[{job_id!r}]='rejected', got: {outcome_for_job!r}; "
        f"full job_outcomes={job_outcomes!r}"
    )

    # 8g. Both stages ran.
    assert counter_file.exists(), "counter file was never created by fake_claude"
    assert counter_file.read_text().strip() == "2", (
        f"expected 2 invocations (stage1 + stage2), "
        f"counter={counter_file.read_text().strip()!r}"
    )

    # 8h. Trigger was "job_terminal" (rejection fires the terminal check just
    #     like a job completion does).
    assert resumed_payload.get("trigger") == "job_terminal", (
        f"expected trigger=job_terminal (founder-rejected flow), got: {resumed_payload}"
    )
