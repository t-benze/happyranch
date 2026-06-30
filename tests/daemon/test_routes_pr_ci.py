"""Unit tests for POST /api/v1/orgs/{slug}/pr-ci/complete (spec §4.4)."""
from __future__ import annotations

import secrets

import pytest

from runtime.models import TaskRecord, TaskStatus


def _make_active_session(org, agent: str = "dev_agent"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="test",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def _make_root_task(org, agent: str = "dev_agent") -> tuple[str, str]:
    """Create a root task (parent_task_id=None) with an active session."""
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="root task",
        status=TaskStatus.IN_PROGRESS,
        parent_task_id=None,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def _make_child_task(
    org,
    parent_id: str,
    agent: str,
    status: TaskStatus = TaskStatus.COMPLETED,
    verdict: str | None = None,
) -> str:
    """Create a child task and insert a completion report with the given verdict."""
    child_id = org.db.next_task_id()
    child = TaskRecord(
        id=child_id,
        assigned_agent=agent,
        team="engineering",
        brief="child task",
        status=status,
        parent_task_id=parent_id,
    )
    org.db.insert_task(child)

    if verdict is not None:
        org.db.insert_task_result(
            task_id=child_id,
            agent=agent,
            session_id="sid-old",
            status="completed",
            confidence_score=90,
            output_summary="test",
            verdict=verdict,
            risks_flagged=[],
            waiting_on_job_ids=[],
            output_dir=None,
        )
    return child_id


def _valid_body(
    task_id: str,
    session_id: str,
    review_task_id: str,
    qa_task_id: str,
    **overrides,
) -> dict:
    body = {
        "task_id": task_id,
        "session_id": session_id,
        "repo": "owner/repo",
        "pr": 1,
        "head_sha": "a" * 40,
        "expected_checks": ["Python CI", "Web CI"],
        "review_task_id": review_task_id,
        "qa_task_id": qa_task_id,
        "timeout_seconds": 600,
        "settle_seconds": 60,
        "merge_method": "squash",
    }
    body.update(overrides)
    return body


# ── happy path ──────────────────────────────────────────────────────────────


def test_pr_ci_complete_happy_path(client_with_runtime):
    """Happy path: creates a bounded review_required=false job, returns job_id."""
    client, org = client_with_runtime
    # Ensure workspace dir exists (required for auto-run path).
    (org.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    root_id, root_sid = _make_root_task(org, agent="dev_agent")
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "job_id" in body
    assert body["job_id"].startswith("JOB-")
    assert body["status"] == "running"  # auto-run fires immediately

    # Verify the job row
    job = org.db.get_job(body["job_id"])
    assert job is not None
    assert job.review_required is False
    assert job.persistent is False
    assert job.max_runtime_seconds == 720  # 600 + 120 margin
    assert job.agent_name == "dev_agent"
    assert job.task_id == root_id
    assert job.interpreter.value == "python3"
    # Verify the script contains pinned params (not empty, not user-injected)
    assert "owner/repo" in job.script_text
    assert "a" * 40 in job.script_text
    assert "Python CI" in job.script_text
    assert "guarded_merge" in job.script_text


def test_pr_ci_complete_with_submitting_task_as_own_lineage(client_with_runtime):
    """The submitting task can also be the root with direct child evidence tasks."""
    client, org = client_with_runtime
    (org.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    dev_id, dev_sid = _make_active_session(org, agent="dev_agent")

    # Make dev task a root (no parent)
    dev = org.db.get_task(dev_id)
    # We need to set parent_task_id=None. Actually, _make_active_session
    # already creates a root. But it may not have parent_task_id set.
    # Let's create explicit children
    review_id = _make_child_task(org, dev_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, dev_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(dev_id, dev_sid, review_id, qa_id),
    )
    assert r.status_code == 201, r.text
    assert r.json()["job_id"].startswith("JOB-")


# ── auth gating ─────────────────────────────────────────────────────────────


def test_pr_ci_complete_unknown_task(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body("TASK-999", root_sid, review_id, qa_id),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_pr_ci_complete_task_not_active(client_with_runtime):
    client, org = client_with_runtime
    completed_id = org.db.next_task_id()
    completed = TaskRecord(
        id=completed_id,
        assigned_agent="dev_agent",
        team="engineering",
        brief="done",
        status=TaskStatus.COMPLETED,
    )
    org.db.insert_task(completed)

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(completed_id, "sid", "TASK-001", "TASK-002"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"


def test_pr_ci_complete_session_mismatch(client_with_runtime):
    client, org = client_with_runtime
    task_id, _real_sid = _make_active_session(org)

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(task_id, "WRONG-SID", "TASK-001", "TASK-002"),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


# ── evidence verdict gating ─────────────────────────────────────────────────


def test_pr_ci_complete_review_task_not_found(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, "TASK-999", qa_id),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_evidence_task"


def test_pr_ci_complete_qa_task_not_found(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, "TASK-999"),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_evidence_task"


def test_pr_ci_complete_review_not_completed(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    # Create review task WITHOUT a completion report
    review_id = _make_child_task(
        org, root_id, "code_reviewer", status=TaskStatus.IN_PROGRESS, verdict=None
    )
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "evidence_task_not_completed"


def test_pr_ci_complete_qa_not_completed(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(
        org, root_id, "qa_engineer", status=TaskStatus.IN_PROGRESS, verdict=None
    )

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "evidence_task_not_completed"


def test_pr_ci_complete_review_wrong_verdict(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="REQUEST_CHANGES")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "evidence_verdict_mismatch"
    assert detail["expected"] == "APPROVE"
    assert detail["got"] == "REQUEST_CHANGES"


def test_pr_ci_complete_qa_wrong_verdict(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="FAIL")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "evidence_verdict_mismatch"
    assert detail["expected"] == "PASS"
    assert detail["got"] == "FAIL"


# ── lineage gating ──────────────────────────────────────────────────────────


def test_pr_ci_complete_evidence_not_in_lineage(client_with_runtime):
    """Evidence task from a different root tree is rejected."""
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org, agent="dev_agent")

    # Create a SEPARATE task tree
    other_root_id = org.db.next_task_id()
    other_root = TaskRecord(
        id=other_root_id,
        assigned_agent="product_manager",
        team="engineering",
        brief="other tree",
        status=TaskStatus.IN_PROGRESS,
        parent_task_id=None,
    )
    org.db.insert_task(other_root)

    # Review task from the other tree
    review_id = _make_child_task(org, other_root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "evidence_not_in_lineage"


# ── input validation ────────────────────────────────────────────────────────


def test_pr_ci_complete_empty_expected_checks(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, expected_checks=[]),
    )
    assert r.status_code == 422


def test_pr_ci_complete_invalid_merge_method(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, merge_method="fast-forward"),
    )
    assert r.status_code == 422


def test_pr_ci_complete_invalid_repo(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, repo="norepo"),
    )
    assert r.status_code == 422


def test_pr_ci_complete_invalid_pr_number(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, pr=0),
    )
    assert r.status_code == 422


def test_pr_ci_complete_invalid_head_sha(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, head_sha="short"),
    )
    assert r.status_code == 422


def test_pr_ci_complete_invalid_timeout(client_with_runtime):
    client, org = client_with_runtime
    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    r = client.post(
        "/api/v1/orgs/alpha/pr-ci/complete",
        json=_valid_body(root_id, root_sid, review_id, qa_id, timeout_seconds=0),
    )
    assert r.status_code == 422


# ── script is daemon-generated, not user-injected ───────────────────────────


def test_pr_ci_complete_script_cannot_be_injected(client_with_runtime):
    """The request body has no 'script' field — the script is always
    daemon-generated from validated params only."""
    client, org = client_with_runtime
    (org.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    root_id, root_sid = _make_root_task(org)
    review_id = _make_child_task(org, root_id, "code_reviewer", verdict="APPROVE")
    qa_id = _make_child_task(org, root_id, "qa_engineer", verdict="PASS")

    body = _valid_body(root_id, root_sid, review_id, qa_id)
    body["script"] = "echo injected"  # Should be ignored / not cause an error
    r = client.post("/api/v1/orgs/alpha/pr-ci/complete", json=body)
    assert r.status_code == 201

    # The stored job script should NOT contain "echo injected"
    job = org.db.get_job(r.json()["job_id"])
    assert "echo injected" not in job.script_text
    assert "guarded_merge" in job.script_text
