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
from typing import Callable

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


def _wait_for_task_parked_on_job(
    base: str,
    task_id: str,
    expected_job_id: str,
    *,
    timeout: float = 20.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Wait until the exact submitted job durably parks the task."""
    deadline = monotonic() + timeout
    body: dict = {}
    while monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
        body = r.json()
        task = body.get("task", {})
        raw_job_ids = task.get("blocked_on_job_ids") or "[]"
        try:
            job_ids = json.loads(raw_job_ids)
        except json.JSONDecodeError:
            job_ids = []
        if (
            task.get("status") == "in_progress"
            and task.get("block_kind") == "blocked_on_job"
            and job_ids == [expected_job_id]
        ):
            return body
        sleep(0.2)
    raise AssertionError(
        "task did not durably park on the expected job before founder action: "
        f"task_id={task_id!r} expected_job_id={expected_job_id!r} last_body={body}"
    )


def _single_audit_entry(entries: list[dict], action: str) -> dict:
    """Return the one audit row for a lifecycle action, never a lookalike."""
    matches = [entry for entry in entries if entry["action"] == action]
    assert len(matches) == 1, f"expected one {action}; got {matches!r}"
    return matches[0]


def _assert_job_pending(base: str, job_id: str, headers: dict) -> None:
    """Founder action is permitted only after the submitted job is still pending."""
    response = httpx.get(f"{base}/jobs/{job_id}", headers=headers, timeout=5.0)
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "pending", response.json()


def _audit_payload(entry: dict) -> dict:
    payload = entry.get("payload") or {}
    return json.loads(payload) if isinstance(payload, str) else payload


@pytest.mark.parametrize(
    ("task", "expected_job_id"),
    [
        ({"status": "in_progress"}, "JOB-1"),
        (
            {
                "status": "in_progress",
                "block_kind": "blocked_on_job",
                "blocked_on_job_ids": json.dumps(["JOB-wrong"]),
            },
            "JOB-expected",
        ),
    ],
)
def test_wait_for_task_parked_on_job_rejects_incomplete_or_wrong_readiness(
    monkeypatch, task: dict, expected_job_id: str
) -> None:
    """Status-only and wrong-job observations never satisfy founder readiness."""
    reads = 0

    class _Response:
        def json(self) -> dict:
            return {"task": task}

    def get(*args, **kwargs) -> _Response:
        nonlocal reads
        reads += 1
        return _Response()

    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(AssertionError, match="did not durably park") as rejected:
        _wait_for_task_parked_on_job(
            "http://test", "TASK-1", expected_job_id, timeout=1.0,
            monotonic=lambda: next(ticks), sleep=lambda _: None,
        )
    assert reads == 1
    assert f"last_body={{'task': {task!r}}}" in str(rejected.value)


def test_wait_for_task_parked_on_job_accepts_exact_durable_readiness(monkeypatch) -> None:
    """The helper accepts only an observed exact durable parked state."""
    reads = 0

    class _Response:
        def json(self) -> dict:
            return {
                "task": {
                    "status": "in_progress",
                    "block_kind": "blocked_on_job",
                    "blocked_on_job_ids": json.dumps(["JOB-expected"]),
                }
            }

    def get(*args, **kwargs) -> _Response:
        nonlocal reads
        reads += 1
        return _Response()

    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(httpx, "get", get)
    parked = _wait_for_task_parked_on_job(
        "http://test", "TASK-1", "JOB-expected", timeout=1.0,
        monotonic=lambda: next(ticks), sleep=lambda _: None,
    )
    assert reads == 1
    assert parked["task"]["blocked_on_job_ids"] == json.dumps(["JOB-expected"])


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
            payload="{tmp_path}/blocked-by-job-rr-approve-submit-$$.json"
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

            submit_log="{tmp_path}/blocked-by-job-rr-approve-submit-log-$$.txt"
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

            # The shipping file callback forwards the exact blocking job IDs.
            completion_payload="{tmp_path}/blocked-by-job-rr-approve-completion-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "summary": "Waiting for %s to be reviewed and approved.",
              "risks": [],
              "dependencies": [],
              "reviewer_focus": [],
              "waiting_on_job_ids": ["%s"]
            }}' "$task_id" "$session_id" "$agent" "$job_id" "$job_id" > "$completion_payload"

            happyranch report-completion --org "$org_slug" --from-file "$completion_payload" >&2
            echo "" >&2
            echo "Stage 1: blocked with waiting_on_job_ids=[$job_id]" >&2

        else
            # ── Stage 2: founder-approved job ran; complete the task ──
            echo "Stage 2: task resumed after founder-approved job, reporting completion" >&2

            done_payload="{tmp_path}/rr-approve-done-$$.json"
            printf '{{"task_id":"%s","session_id":"%s","agent":"%s","status":"completed","confidence":90,"summary":"completed after founder-approved job unblock","decision":{{"action":"done","summary":"completed after founder-approved job unblock"}}}}' "$task_id" "$session_id" "$agent" > "$done_payload"
            happyranch report-completion --org "$org_slug" --from-file "$done_payload"
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
    parked = _wait_for_task_parked_on_job(base, task_id, job_id)
    assert parked["task"]["blocked_on_job_ids"] == json.dumps([job_id])
    _assert_job_pending(base, job_id, headers)

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
    # 8a. Agent submitted a job.
    submitted_entry = _single_audit_entry(entries, "job_submitted")

    # 8b. Task was blocked on the job.
    blocked_entry = _single_audit_entry(entries, "task_blocked_on_jobs")
    assert _audit_payload(blocked_entry)["blocking_job_ids"] == [job_id]

    # 8c. Job ran and completed (after founder approval via /run).
    started_entry = _single_audit_entry(entries, "job_run_started")
    terminal_entry = _single_audit_entry(entries, "job_run_completed")
    assert started_entry["agent"] == "founder"
    assert terminal_entry["agent"] == "founder"

    # 8d. Task was auto-resumed (proves CAS flip fired and BLOCKED-JOBS-RESULTS
    #     header was injected before stage 2).
    _single_audit_entry(entries, "task_resumed_from_jobs")

    # 8e. The resume audit row should reference the same job.
    payload_raw = _audit_payload(submitted_entry)
    submitted_job_id = payload_raw.get("script_request_id")
    assert submitted_job_id == job_id, (
        f"job_submitted.script_request_id={submitted_job_id!r} != job_id={job_id!r}"
    )

    resumed_entry = _single_audit_entry(entries, "task_resumed_from_jobs")
    resumed_payload = _audit_payload(resumed_entry)
    blocking_ids = resumed_payload.get("blocking_job_ids", [])
    assert blocking_ids == [job_id]
    assert resumed_payload.get("job_outcomes") == {job_id: "completed"}

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
    assert resumed_payload.get("triggering_job_id") == job_id
    print("E2 effects", json.dumps({"task_id": task_id, "job_id": job_id, "parked": parked["task"]["blocked_on_job_ids"], "resume": resumed_payload, "audit_actions": [entry["action"] for entry in entries], "invocations": int(counter_file.read_text())}))


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
            payload="{tmp_path}/blocked-by-job-rr-reject-submit-$$.json"
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

            submit_log="{tmp_path}/blocked-by-job-rr-reject-submit-log-$$.txt"
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

            # The shipping file callback forwards the exact blocking job IDs.
            completion_payload="{tmp_path}/blocked-by-job-rr-reject-completion-$$.json"
            printf '{{
              "task_id": "%s",
              "session_id": "%s",
              "agent": "%s",
              "status": "blocked",
              "confidence": 0,
              "summary": "Waiting for %s to be reviewed — may be rejected.",
              "risks": [],
              "dependencies": [],
              "reviewer_focus": [],
              "waiting_on_job_ids": ["%s"]
            }}' "$task_id" "$session_id" "$agent" "$job_id" "$job_id" > "$completion_payload"

            happyranch report-completion --org "$org_slug" --from-file "$completion_payload" >&2
            echo "" >&2
            echo "Stage 1: blocked with waiting_on_job_ids=[$job_id]" >&2

        else
            # ── Stage 2: job was rejected by founder; adapt and complete ──
            echo "Stage 2: task resumed after founder-rejected job, reporting completion" >&2

            done_payload="{tmp_path}/rr-reject-done-$$.json"
            printf '{{"task_id":"%s","session_id":"%s","agent":"%s","status":"completed","confidence":90,"summary":"completed despite job rejection — adapted plan","decision":{{"action":"done","summary":"completed despite job rejection — adapted plan"}}}}' "$task_id" "$session_id" "$agent" > "$done_payload"
            happyranch report-completion --org "$org_slug" --from-file "$done_payload"
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
    parked = _wait_for_task_parked_on_job(base, task_id, job_id)
    assert parked["task"]["blocked_on_job_ids"] == json.dumps([job_id])
    _assert_job_pending(base, job_id, headers)

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
    # 8a. Agent submitted a job.
    submitted_entry = _single_audit_entry(entries, "job_submitted")

    # 8b. Task was blocked on the job.
    blocked_entry = _single_audit_entry(entries, "task_blocked_on_jobs")
    assert _audit_payload(blocked_entry)["blocking_job_ids"] == [job_id]

    # 8c. Job was rejected by founder.
    rejected_entry = _single_audit_entry(entries, "job_rejected")
    assert rejected_entry["agent"] == "founder"

    # 8d. Task was auto-resumed.
    _single_audit_entry(entries, "task_resumed_from_jobs")

    # 8e. The resume payload references the correct job.
    payload_raw = _audit_payload(submitted_entry)
    submitted_job_id = payload_raw.get("script_request_id")
    assert submitted_job_id == job_id, (
        f"job_submitted.script_request_id={submitted_job_id!r} != job_id={job_id!r}"
    )

    resumed_entry = _single_audit_entry(entries, "task_resumed_from_jobs")
    resumed_payload = _audit_payload(resumed_entry)
    blocking_ids = resumed_payload.get("blocking_job_ids", [])
    assert blocking_ids == [job_id]

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
    assert resumed_payload.get("triggering_job_id") == job_id
    print("E2 effects", json.dumps({"task_id": task_id, "job_id": job_id, "parked": parked["task"]["blocked_on_job_ids"], "resume": resumed_payload, "audit_actions": [entry["action"] for entry in entries], "invocations": int(counter_file.read_text())}))
