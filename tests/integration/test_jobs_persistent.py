"""End-to-end persistent job: agent auto-runs a persistent=true job,
founder stops it, agent then reports completion.

The plan runs as a single fake_claude session:
  1. submit a persistent + review_required=false job whose script sleeps 60s
  2. wait on a test-driven sentinel file
  3. report completion (status="completed")

Meanwhile the test:
  1. dispatches the task
  2. polls until the job row appears + is running
  3. asserts persistent=True, review_required=False
  4. POST /jobs/{id}/stop
  5. waits for the job to be failed + reason=founder_stop
  6. touches the sentinel so the plan can finish + the task can terminate
"""
from __future__ import annotations

import time
from textwrap import dedent

import httpx
import pytest

from tests.integration.conftest import seed_workspace, DEFAULT_TEST_SLUG


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def _wait_for_job(base: str, task_id: str, *, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/jobs/",
            params={"status": "all", "task_id": task_id},
            headers=_auth_headers(),
            timeout=5.0,
        )
        last = r
        rows = r.json()["jobs"]
        if rows:
            return rows[0]
        time.sleep(0.2)
    raise AssertionError(
        f"no job appeared for task {task_id} within {timeout}s; last={last.text if last else None}"
    )


def _wait_for_job_terminal(
    base: str, job_id: str, *, timeout: float = 10.0
) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/jobs/{job_id}", headers=_auth_headers(), timeout=5.0
        )
        last = r.json()
        if last.get("status") in ("completed", "failed"):
            return last
        time.sleep(0.1)
    raise AssertionError(
        f"job {job_id} did not reach terminal within {timeout}s; last={last}"
    )


def test_persistent_job_full_lifecycle(
    live_daemon, runtime, fake_claude_plan_env, tmp_path,
):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{DEFAULT_TEST_SLUG}"
    headers = _auth_headers()

    seed_workspace(runtime, "engineering_head")

    # Sentinel files the plan polls so the test can interleave its assertions.
    job_sentinel = tmp_path / "job-submitted.txt"
    stop_sentinel = tmp_path / "founder-acted.txt"

    fake_claude_plan_env.write_text(dedent(f"""\
        #!/usr/bin/env bash
        task_id="$1"
        session_id="$2"
        agent="$3"
        org_slug="$4"

        # 1. Submit a persistent + auto-run job whose script sleeps 60s.
        payload="/tmp/job-persistent-payload-$$.json"
        printf '{{
          "task_id": "%s",
          "session_id": "%s",
          "title": "persistent dev loop",
          "rationale": "long-running background task",
          "script": "echo starting; sleep 60",
          "interpreter": "bash",
          "review_required": false,
          "persistent": true
        }}' "$task_id" "$session_id" > "$payload"

        grassland jobs submit --from-file "$payload" --org "$org_slug" \\
            > /tmp/job-persistent-submit-$$.log 2>&1
        cat /tmp/job-persistent-submit-$$.log >&2
        touch "{job_sentinel}"

        # 2. Wait until the test side has finished its assertions + stop.
        for _ in $(seq 1 600); do
            if [[ -f "{stop_sentinel}" ]]; then
                break
            fi
            sleep 0.1
        done

        # 3. Report completion so the task transitions normally.
        report="/tmp/job-persistent-completion-$$.json"
        printf '{{
          "task_id": "%s",
          "session_id": "%s",
          "agent": "%s",
          "status": "completed",
          "summary": "{{\\"action\\":\\"done\\",\\"summary\\":\\"loop launched, founder stopped\\"}}",
          "confidence": 90,
          "risks_flagged": [],
          "dependencies": [],
          "suggested_reviewer_focus": []
        }}' "$task_id" "$session_id" "$agent" > "$report"

        grassland report-completion --from-file "$report" --org "$org_slug"
    """))
    fake_claude_plan_env.chmod(0o755)

    # ── Dispatch the task.
    r = httpx.post(
        f"{base}/tasks",
        json={"brief": "start a persistent loop", "team": "engineering"},
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # ── Wait for the plan to have submitted the job.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if job_sentinel.exists():
            break
        time.sleep(0.1)
    assert job_sentinel.exists(), "plan never submitted the job"

    # ── Find the running job.
    job = _wait_for_job(base, task_id, timeout=10.0)
    assert job["persistent"] is True, job
    assert job["review_required"] is False, job
    assert job["status"] == "running", job
    job_id = job["id"]
    assert job_id.startswith("JOB-"), f"expected JOB- prefix, got {job_id!r}"

    # ── Tail the running job (founder bearer-auth path).
    r = httpx.get(
        f"{base}/jobs/{job_id}/tail",
        params={"stream": "stdout", "lines": 10},
        headers=headers, timeout=5.0,
    )
    assert r.status_code == 200, r.text
    tail_payload = r.json()
    assert tail_payload["stream"] == "stdout"
    # tail may be empty (timing) or include "starting" — either is acceptable.

    # ── Founder stops the job.
    r = httpx.post(
        f"{base}/jobs/{job_id}/stop", headers=headers, timeout=5.0,
    )
    assert r.status_code == 200, r.text

    # ── Wait for terminal status; assert founder_stop reason.
    final = _wait_for_job_terminal(base, job_id, timeout=10.0)
    assert final["status"] == "failed", final
    assert final["reason"] == "founder_stop", final

    # ── Let the plan finish so the task transitions cleanly.
    stop_sentinel.touch()
