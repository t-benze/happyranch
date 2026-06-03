"""End-to-end multi-job blocked-by-job test.

Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §9.2

Verifies the ALL-terminal predicate: the task stays blocked when only the
FIRST of two review_required=true jobs is approved, and resumes only after
BOTH are terminal.

Scenario:
  1. Agent submits JOB-A and JOB-B (both review_required=true).
  2. Agent self-blocks with waiting_on_job_ids=[JOB-A, JOB-B].
  3. Test driver approves JOB-A → JOB-A completes.
  4. Verify: task STILL blocked (JOB-B is still pending — predicate unsatisfied).
  5. Test driver approves JOB-B → JOB-B completes.
  6. Task auto-resumes (all jobs terminal).
  7. Audit row task_resumed_from_jobs.triggering_job_id == JOB-B.
  8. Stage-2 agent sees BLOCKED-JOBS-RESULTS header listing both jobs.
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


def _get_task_status(base: str, task_id: str) -> str:
    """Return the current task status without waiting."""
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    return r.json().get("task", {}).get("status", "")


def test_multi_job_resume_waits_for_all(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    tmp_path,
):
    """Two review_required=true jobs; verify all-terminal predicate.

    After JOB-A completes, caller A fires but finds JOB-B still pending and
    no-ops. The task stays blocked. Only after JOB-B completes does the
    predicate pass and the task resume.

    Key assertions:
    - task is STILL blocked after JOB-A completes (intermediate state check)
    - task_resumed_from_jobs.triggering_job_id == JOB-B (the predicate-closer)
    - task_resumed_from_jobs.blocking_job_ids contains both JOB-A and JOB-B
    - stage-2 agent ran (counter == 2)
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    # ── 1. Seed the agent workspace.
    seed_workspace(runtime, "engineering_head")

    # ── 2. State files shared between the test driver and the fake_claude plan.
    counter_file = tmp_path / "invocation_counter"
    # Stage 1 writes both job IDs here so the test driver can act on them.
    joba_file = tmp_path / "job_a.id"
    jobb_file = tmp_path / "job_b.id"

    # ── 3. Write the two-stage fake_claude plan.
    #
    # Stage 1: submit JOB-A and JOB-B (both review_required=true) then block
    #   on both.  Writes each job ID to its own file so the test driver can
    #   issue /run calls in a controlled sequence.
    # Stage 2: task resumed after both jobs ran; complete.
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
            # ── Stage 1: submit two review_required=true jobs + self-block ──

            # Submit JOB-A.
            payload_a="/tmp/multi-job-submit-a-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "title": "multi-job e2e job A",
              "rationale": "first of two jobs — multi-job integration test",
              "script": "echo job-a-ran",
              "interpreter": "bash",
              "review_required": true,
              "persistent": false
            }}' "$task_id" "$session_id" > "$payload_a"

            submit_log_a="/tmp/multi-job-submit-log-a-$$.txt"
            happyranch jobs submit --from-file "$payload_a" --org "$org_slug" \
                > "$submit_log_a" 2>&1
            cat "$submit_log_a" >&2

            job_a=$(grep -oE 'JOB-[0-9]+' "$submit_log_a" | head -1)
            if [ -z "$job_a" ]; then
                echo "ERROR: could not parse JOB-A id" >&2
                cat "$submit_log_a" >&2
                exit 1
            fi
            echo "Stage 1: submitted $job_a (JOB-A, review_required=true)" >&2
            echo "$job_a" > "{joba_file}"

            # Submit JOB-B.
            payload_b="/tmp/multi-job-submit-b-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "title": "multi-job e2e job B",
              "rationale": "second of two jobs — multi-job integration test",
              "script": "echo job-b-ran",
              "interpreter": "bash",
              "review_required": true,
              "persistent": false
            }}' "$task_id" "$session_id" > "$payload_b"

            submit_log_b="/tmp/multi-job-submit-log-b-$$.txt"
            happyranch jobs submit --from-file "$payload_b" --org "$org_slug" \
                > "$submit_log_b" 2>&1
            cat "$submit_log_b" >&2

            job_b=$(grep -oE 'JOB-[0-9]+' "$submit_log_b" | head -1)
            if [ -z "$job_b" ]; then
                echo "ERROR: could not parse JOB-B id" >&2
                cat "$submit_log_b" >&2
                exit 1
            fi
            echo "Stage 1: submitted $job_b (JOB-B, review_required=true)" >&2
            echo "$job_b" > "{jobb_file}"

            # Self-block on BOTH jobs via direct HTTP call.
            port=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.port")
            token=$(cat "$HAPPYRANCH_DAEMON_HOME/daemon.token")

            completion_payload="/tmp/multi-job-completion-$$.json"
            printf '{{
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "output_summary": "Waiting for %s and %s before proceeding.",
              "risks_flagged": [],
              "dependencies": [],
              "suggested_reviewer_focus": [],
              "waiting_on_job_ids": ["%s", "%s"]
            }}' "$session_id" "$agent" "$job_a" "$job_b" "$job_a" "$job_b" \
                > "$completion_payload"

            curl -s -X POST \\
                "http://127.0.0.1:$port/api/v1/orgs/$org_slug/tasks/$task_id/completion" \\
                -H "Authorization: Bearer $token" \\
                -H "Content-Type: application/json" \\
                -d @"$completion_payload" >&2
            echo "" >&2
            echo "Stage 1: blocked with waiting_on_job_ids=[$job_a, $job_b]" >&2

        else
            # ── Stage 2: both jobs completed; task resumed ──
            echo "Stage 2: task resumed after both jobs ran, reporting completion" >&2

            happyranch report-completion --org "$org_slug" \\
                --task-id "$task_id" --session-id "$session_id" \\
                --agent "$agent" --status completed --confidence 90 \\
                --summary '{{"action":"done","summary":"completed after both jobs unblocked"}}'
            echo "Stage 2: reported completed" >&2
        fi
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── 4. Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "multi-job blocked-by-job e2e test", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── 5. Wait for stage 1 to write both job ID files.
    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        if joba_file.exists() and jobb_file.exists():
            break
        time.sleep(0.2)
    assert joba_file.exists(), (
        "stage 1 plan never wrote joba_file — fake_claude may have failed"
    )
    assert jobb_file.exists(), (
        "stage 1 plan never wrote jobb_file — fake_claude may have failed"
    )
    job_a = joba_file.read_text().strip()
    job_b = jobb_file.read_text().strip()
    assert job_a.startswith("JOB-"), f"unexpected job_a id: {job_a!r}"
    assert job_b.startswith("JOB-"), f"unexpected job_b id: {job_b!r}"

    # Wait for the task to reach blocked state before acting as founder.
    _wait_for_task_status(
        base, task_id,
        terminal=("blocked",),
        timeout=20.0,
    )

    # ── 6. Founder approves JOB-A only.
    r = httpx.post(
        f"{base}/jobs/{job_a}/run",
        json={},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 202, (
        f"POST /jobs/{job_a}/run failed: {r.status_code} {r.text}"
    )

    # ── 7. Intermediate state check: task must remain blocked.
    #
    # JOB-A will complete quickly (it's just `echo job-a-ran`). After it does,
    # caller A (jobs_runner terminal hook) fires for JOB-A, calls
    # _maybe_resume_blocked_task, finds JOB-B still pending, and no-ops.
    # We give the system up to 10 s for JOB-A to run to completion, then
    # assert the task is still blocked (not in_progress or completed).
    #
    # We wait for JOB-A to actually reach a terminal status before checking
    # the task, so we're not racing against the job run itself.
    deadline = time.monotonic() + 20.0
    job_a_terminal = False
    while time.monotonic() < deadline:
        r_job = httpx.get(
            f"{base}/jobs/{job_a}",
            headers=headers,
            timeout=5.0,
        )
        if r_job.status_code == 200:
            job_status = r_job.json().get("status", "")
            if job_status in ("completed", "failed", "rejected"):
                job_a_terminal = True
                break
        time.sleep(0.2)
    assert job_a_terminal, (
        f"JOB-A ({job_a}) never reached terminal status within 20s"
    )

    # Give the resume helper a moment to evaluate the predicate (it runs
    # fire-and-forget via asyncio.run_coroutine_threadsafe). 1 s is generous;
    # the helper is a fast DB query + conditional enqueue.
    time.sleep(1.0)

    # Now assert the task is STILL blocked (predicate unsatisfied because JOB-B
    # is still pending).
    current_status = _get_task_status(base, task_id)
    assert current_status == "blocked", (
        f"task should still be blocked after only JOB-A completes, "
        f"but status is {current_status!r}. "
        f"The all-terminal predicate must have fired prematurely."
    )

    # ── 8. Founder approves JOB-B.
    r = httpx.post(
        f"{base}/jobs/{job_b}/run",
        json={},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 202, (
        f"POST /jobs/{job_b}/run failed: {r.status_code} {r.text}"
    )

    # ── 9. Wait for the task to complete (JOB-B runs → predicate fires → resume
    #        → stage 2 runs → completed).
    body = _wait_for_task_status(base, task_id, terminal=("completed",), timeout=90.0)
    assert body["task"]["status"] == "completed", (
        f"expected completed, got: {body['task']}"
    )

    # ── 10. Verify audit log.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]

    # 10a. Two job_submitted rows (one per job).
    submitted_actions = [e for e in entries if e["action"] == "job_submitted"]
    assert len(submitted_actions) == 2, (
        f"expected 2 job_submitted entries, got {len(submitted_actions)}; "
        f"actions={actions}"
    )

    # 10b. Task was blocked on both jobs.
    assert "task_blocked_on_jobs" in actions, (
        f"missing task_blocked_on_jobs; actions={actions}"
    )
    blocked_entry = next(e for e in entries if e["action"] == "task_blocked_on_jobs")
    blocked_payload = blocked_entry.get("payload") or {}
    if isinstance(blocked_payload, str):
        blocked_payload = json.loads(blocked_payload)
    blocking_ids_at_block = blocked_payload.get("blocking_job_ids", [])
    assert job_a in blocking_ids_at_block, (
        f"task_blocked_on_jobs.blocking_job_ids={blocking_ids_at_block!r} "
        f"missing job_a={job_a!r}"
    )
    assert job_b in blocking_ids_at_block, (
        f"task_blocked_on_jobs.blocking_job_ids={blocking_ids_at_block!r} "
        f"missing job_b={job_b!r}"
    )

    # 10c. Two job_run_started and job_run_completed rows (one per job).
    assert actions.count("job_run_started") == 2, (
        f"expected 2 job_run_started, got {actions.count('job_run_started')}; "
        f"actions={actions}"
    )
    assert actions.count("job_run_completed") == 2, (
        f"expected 2 job_run_completed, got {actions.count('job_run_completed')}; "
        f"actions={actions}"
    )

    # 10d. Exactly one task_resumed_from_jobs row (the predicate fires once,
    #      when JOB-B completes and closes the all-terminal condition).
    resumed_entries = [e for e in entries if e["action"] == "task_resumed_from_jobs"]
    assert len(resumed_entries) == 1, (
        f"expected exactly 1 task_resumed_from_jobs, "
        f"got {len(resumed_entries)}; actions={actions}"
    )
    resumed_entry = resumed_entries[0]
    resumed_payload = resumed_entry.get("payload") or {}
    if isinstance(resumed_payload, str):
        resumed_payload = json.loads(resumed_payload)

    # 10e. The resume audit row lists both blocking jobs.
    blocking_ids_at_resume = resumed_payload.get("blocking_job_ids", [])
    assert job_a in blocking_ids_at_resume, (
        f"task_resumed_from_jobs.blocking_job_ids={blocking_ids_at_resume!r} "
        f"missing job_a={job_a!r}"
    )
    assert job_b in blocking_ids_at_resume, (
        f"task_resumed_from_jobs.blocking_job_ids={blocking_ids_at_resume!r} "
        f"missing job_b={job_b!r}"
    )

    # 10f. triggering_job_id is JOB-B — the job that closed the predicate.
    #      JOB-A ran first; at that point JOB-B was still pending so the
    #      helper no-oped. Only JOB-B's completion satisfied the predicate.
    triggering_job_id = resumed_payload.get("triggering_job_id")
    assert triggering_job_id == job_b, (
        f"expected triggering_job_id={job_b!r} (JOB-B closed the predicate), "
        f"got {triggering_job_id!r}; full payload={resumed_payload}"
    )

    # 10g. Trigger was "job_terminal" (Caller A: jobs_runner terminal hook).
    assert resumed_payload.get("trigger") == "job_terminal", (
        f"expected trigger=job_terminal, got: {resumed_payload}"
    )

    # 10h. Job outcomes in the resume row show both jobs completed.
    job_outcomes = resumed_payload.get("job_outcomes", {})
    assert job_outcomes.get(job_a) == "completed", (
        f"expected job_outcomes[{job_a!r}]='completed', "
        f"got {job_outcomes.get(job_a)!r}; full outcomes={job_outcomes}"
    )
    assert job_outcomes.get(job_b) == "completed", (
        f"expected job_outcomes[{job_b!r}]='completed', "
        f"got {job_outcomes.get(job_b)!r}; full outcomes={job_outcomes}"
    )

    # 10i. Both invocations ran (stage 1 + stage 2).
    assert counter_file.exists(), "counter file was never created by fake_claude"
    assert counter_file.read_text().strip() == "2", (
        f"expected 2 invocations, got {counter_file.read_text().strip()!r}"
    )
