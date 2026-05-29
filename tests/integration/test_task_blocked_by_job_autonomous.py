"""End-to-end test for the autonomous blocked-by-job flow.

Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §9.2

Covers the full autonomous happy-path:
  1. Agent submits a review_required=false job (auto-runs immediately).
  2. Agent self-blocks via report-completion with waiting_on_job_ids=[JOB-NNN].
  3. The job runs and completes (review_required=false → auto-runs, no founder action).
  4. The task auto-resumes (blocked_on_job → in_progress CAS flip by run_step).
  5. The next agent session is invoked (stage 2 plan runs).
  6. Task completes.

Audit log assertions cover all 6 key events:
  job_submitted, task_blocked_on_jobs, job_run_completed,
  task_resumed_from_jobs (proves BLOCKED-JOBS-RESULTS header was injected),
  and the final task status=completed.
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


def test_blocks_on_job_then_auto_resumes(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    tmp_path,
):
    """Autonomous flow: agent submits review_required=false job, blocks,
    job completes, task auto-resumes, agent sees BLOCKED-JOBS-RESULTS header
    (verified via audit), task completes.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    # ── 1. Seed the agent workspace.
    seed_workspace(runtime, "engineering_head")

    # ── 2. Counter file: distinguishes stage 1 (first agent invocation) from
    #        stage 2 (resumed invocation after the job completes).
    counter_file = tmp_path / "invocation_counter"

    # ── 3. Write the two-stage fake_claude plan.
    #
    # The plan receives $1=task_id  $2=session_id  $3=agent  $4=org_slug.
    # $GRASSLAND_DAEMON_HOME is inherited from the daemon process and points
    # at the test-isolated home directory set by the `tmp_home` fixture.
    # We use it to read daemon.port and daemon.token for the direct HTTP call
    # that passes waiting_on_job_ids (the grassland CLI --from-file path does
    # not yet support this field).
    #
    # Stage 2 uses grassland report-completion inline args (not --from-file)
    # to avoid the nested-JSON-in-single-quoted-printf escaping issue.
    # The --summary value is a JSON object that _parse_next_step parses via
    # the legacy prose path: json.loads('{"action":"done",...}') -> NextStep.
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
            # ── Stage 1: submit job + self-block with waiting_on_job_ids ──

            # Submit a quick auto-run job.
            payload="/tmp/blocked-by-job-submit-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "title": "autonomous e2e job",
              "rationale": "auto-run integration test",
              "script": "echo autonomous-job-ran",
              "interpreter": "bash",
              "review_required": false,
              "persistent": false
            }}' "$task_id" "$session_id" > "$payload"

            submit_log="/tmp/blocked-by-job-submit-log-$$.txt"
            grassland jobs submit --from-file "$payload" --org "$org_slug" > "$submit_log" 2>&1
            cat "$submit_log" >&2

            job_id=$(grep -oE 'JOB-[0-9]+' "$submit_log" | head -1)
            if [ -z "$job_id" ]; then
                echo "ERROR: could not parse JOB id from submit output" >&2
                cat "$submit_log" >&2
                exit 1
            fi
            echo "Stage 1: submitted $job_id" >&2

            # Self-block with waiting_on_job_ids via direct HTTP call.
            # The grassland CLI report-completion --from-file path does not yet
            # expose waiting_on_job_ids; we POST to the daemon directly.
            port=$(cat "$GRASSLAND_DAEMON_HOME/daemon.port")
            token=$(cat "$GRASSLAND_DAEMON_HOME/daemon.token")

            completion_payload="/tmp/blocked-by-job-completion-$$.json"
            printf '{{
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "output_summary": "Waiting for %s to finish before proceeding.",
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
            # ── Stage 2: job results are available; complete the task ──
            echo "Stage 2: task resumed, reporting completion" >&2

            # Use inline args to avoid nested-JSON escaping issues with --from-file.
            # The --summary value is a JSON decision object; _parse_next_step parses
            # it via the legacy prose path when no decision field is provided.
            grassland report-completion --org "$org_slug" \\
                --task-id "$task_id" --session-id "$session_id" \\
                --agent "$agent" --status completed --confidence 90 \\
                --summary '{{"action":"done","summary":"completed after job unblock"}}'
            echo "Stage 2: reported completed" >&2
        fi
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── 4. Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "autonomous blocked-by-job e2e test", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── 5. Wait for the task to reach completed.
    #        Give 90s: stage 1 + job run + resume + stage 2 all need to run.
    body = _wait_for_task_status(base, task_id, terminal=("completed",), timeout=90.0)
    assert body["task"]["status"] == "completed", (
        f"expected completed, got: {body['task']}"
    )

    # ── 6. Verify the audit log covers all key events.
    r = httpx.get(
        f"{base}/audit",
        params={"task_id": task_id},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]

    # 6a. Agent submitted a job.
    assert "job_submitted" in actions, (
        f"missing job_submitted in audit; actions={actions}"
    )

    # 6b. Task was blocked on the job.
    assert "task_blocked_on_jobs" in actions, (
        f"missing task_blocked_on_jobs in audit; actions={actions}"
    )

    # 6c. Job ran and completed (auto-run, no founder action).
    assert "job_run_completed" in actions, (
        f"missing job_run_completed in audit; actions={actions}"
    )

    # 6d. Task was auto-resumed by the system (proves the CAS flip fired and
    #     BLOCKED-JOBS-RESULTS header was injected before stage 2).
    assert "task_resumed_from_jobs" in actions, (
        f"missing task_resumed_from_jobs in audit; actions={actions}"
    )

    # 6e. The resume audit row should reference the same job that was submitted.
    submitted_entry = next(e for e in entries if e["action"] == "job_submitted")
    payload_raw = submitted_entry.get("payload") or {}
    if isinstance(payload_raw, str):
        payload_raw = json.loads(payload_raw)
    job_id = payload_raw.get("script_request_id")
    assert job_id and job_id.startswith("JOB-"), (
        f"job_submitted entry missing or bad script_request_id: {payload_raw}"
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

    # 6f. Both stages ran (counter file must contain "2").
    assert counter_file.exists(), "counter file was never created by fake_claude"
    assert counter_file.read_text().strip() == "2", (
        f"expected 2 invocations (stage1 + stage2), "
        f"counter={counter_file.read_text().strip()!r}"
    )

    # 6g. Trigger was "block_submit" (Caller B: immediate check after blocking,
    #     since the job completed almost instantly for a trivial echo script).
    #     Accept "job_terminal" as well (Caller A: jobs_runner hook); which fires
    #     depends on whether the job finishes before or after the block is recorded.
    assert resumed_payload.get("trigger") in ("block_submit", "job_terminal"), (
        f"unexpected trigger: {resumed_payload}"
    )
