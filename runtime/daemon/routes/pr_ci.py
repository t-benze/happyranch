"""PR CI / guarded merge job-submission route (spec §4.4).

POST /api/v1/orgs/{slug}/pr-ci/complete

Validates agent task/session auth (mirrors jobs/submit), validates the
review and QA evidence tasks carry the expected verdicts and are in the
current task's lineage, constructs a daemon-generated script, and creates
a bounded review_required=false jobs row.

HARD GUARDRAILS:
- Does NOT touch the agent permission model (Claude --allowedTools, Codex
  sandbox flags, opencode permission.bash, baseline happyranch allow-rule).
- Does NOT touch authentication, the daemon bearer-token flow, Feishu
  credentials, or notification routing.
- Reuses the EXISTING job-creation path — no new table or column.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.jobs import _now_iso, _run_job_core
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import LineageTooDeep
from runtime.models import JobInterpreter, JobRecord, JobStatus

router = APIRouter()

# ── valid merge methods (matched with pr_ci_merge.VALID_MERGE_METHODS) ────
_VALID_MERGE_METHODS: set[str] = {"merge", "squash", "rebase"}

# ── margin added to timeout_seconds for the job's max_runtime_seconds ──────
_JOB_MARGIN_SECONDS = 120  # extra time beyond the CI wait for merge + overhead


class PrCiCompleteBody(BaseModel):
    """Request body for POST /api/v1/orgs/{slug}/pr-ci/complete."""

    task_id: str
    session_id: str
    repo: str          # owner/repo
    pr: int            # PR number
    head_sha: str      # full 40-char SHA pinned at submission
    expected_checks: list[str]
    review_task_id: str
    qa_task_id: str
    timeout_seconds: int
    settle_seconds: int
    merge_method: str

    @model_validator(mode="after")
    def _validate(self) -> "PrCiCompleteBody":
        if not self.task_id or not self.session_id:
            raise ValueError("task_id and session_id are required")
        if not self.repo or "/" not in self.repo:
            raise ValueError("repo must be owner/repo")
        if self.pr < 1:
            raise ValueError("pr must be >= 1")
        if not self.head_sha or len(self.head_sha) != 40:
            raise ValueError("head_sha must be a 40-char SHA")
        if not self.expected_checks:
            raise ValueError("expected_checks must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.settle_seconds < 0:
            raise ValueError("settle_seconds must be >= 0")
        if self.merge_method not in _VALID_MERGE_METHODS:
            raise ValueError(
                f"merge_method must be one of {sorted(_VALID_MERGE_METHODS)}"
            )
        return self


# ── evidence verdicts ──────────────────────────────────────────────────────

_REQUIRED_REVIEW_VERDICT = "APPROVE"
_REQUIRED_QA_VERDICT = "PASS"


def _check_evidence_verdict(
    org, evidence_task_id: str, expected_verdict: str
) -> None:
    """Verify *evidence_task_id* exists, has a completion report, and its
    verdict matches *expected_verdict*.  Raises HTTPException on mismatch.
    """
    task = org.db.get_task(evidence_task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "unknown_evidence_task",
                "task_id": evidence_task_id,
            },
        )
    report = org.db.get_latest_completion_report(evidence_task_id)
    if report is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "evidence_task_not_completed",
                "task_id": evidence_task_id,
            },
        )
    if report.verdict != expected_verdict:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "evidence_verdict_mismatch",
                "task_id": evidence_task_id,
                "expected": expected_verdict,
                "got": report.verdict,
            },
        )


def _check_in_lineage(org, submitting_task_id: str, evidence_task_id: str) -> None:
    """Verify *evidence_task_id* is in the same task lineage as
    *submitting_task_id*.

    Walks ancestors of *submitting_task_id*, building a set of ancestor ids.
    Then checks that *evidence_task_id* is either in the ancestor chain OR is
    a child of one of the ancestors (i.e., has parent_task_id in the ancestor
    set).

    Raises HTTPException on failure.
    """
    try:
        ancestors = org.db.walk_ancestors(submitting_task_id)
    except LineageTooDeep:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "lineage_too_deep",
                "task_id": submitting_task_id,
            },
        )

    ancestor_ids: set[str] = {t.id for t in ancestors}

    if evidence_task_id in ancestor_ids:
        return

    # Check if evidence_task is a child of any ancestor (sibling of
    # another ancestor, or child of the submitting task, etc.)
    evidence_task = org.db.get_task(evidence_task_id)
    if evidence_task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "unknown_evidence_task",
                "task_id": evidence_task_id,
            },
        )
    if evidence_task.parent_task_id is not None and evidence_task.parent_task_id in ancestor_ids:
        return

    raise HTTPException(
        status_code=422,
        detail={
            "code": "evidence_not_in_lineage",
            "task_id": evidence_task_id,
            "submitting_task_id": submitting_task_id,
        },
    )


# ── script construction ────────────────────────────────────────────────────

def _build_pr_ci_script(
    *,
    repo: str,
    pr: int,
    head_sha: str,
    expected_checks: list[str],
    timeout_seconds: int,
    settle_seconds: int,
    merge_method: str,
) -> str:
    """Build a self-contained Python script that invokes the waiter →
    guarded-merge pipeline with real GitHub `gh` CLI queries.

    The script is constructed server-side from validated parameters ONLY.
    There is NO path for agent-supplied arbitrary text to enter the script
    body — every variable is a validated typed parameter.
    """
    # Serialize expected_checks as a Python list literal safe for embedding.
    checks_literal = _json.dumps(expected_checks)

    script = f'''"""PR CI wait + guarded merge — daemon-generated script.

Parameters pinned at job creation — agent cannot inject arbitrary text.
"""
from __future__ import annotations

import json as _json
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

# ── pinned parameters ──
_REPO = {repo!r}
_PR_NUMBER = {pr}
_PINNED_HEAD_SHA = {head_sha!r}
_EXPECTED_CHECKS: list[str] = {checks_literal}
_SETTLE_SECONDS: float = {float(settle_seconds)}
_POLL_INTERVAL: float = 30.0
_TIMEOUT_SECONDS: float = {float(timeout_seconds)}
_MERGE_METHOD = {merge_method!r}

# ── import engines ──
from runtime.daemon.pr_ci_waiter import (
    PRCIWaiterVerdict,
    PRState,
    CheckState,
    VERDICT_EXIT_CODES as WAITER_EXIT_CODES,
    wait_for_ci,
)
from runtime.daemon.pr_ci_merge import (
    GuardedMergeVerdict,
    MergeableState,
    MergeResult,
    VERDICT_EXIT_CODES as MERGE_EXIT_CODES,
    guarded_merge,
)


# ── GitHub CLI helpers ──

def _gh(*_args: str) -> Any:
    """Run ``gh`` with the given arguments, return parsed JSON on success."""
    result = subprocess.run(
        ["gh"] + list(_args),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gh " + " ".join(_args) + " exited " + str(result.returncode) + ": "
            + result.stderr.strip()
        )
    return _json.loads(result.stdout)


# ── clock ──

class _WallClock:
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


_clock = _WallClock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── real GitHub fetchers ──

def _fetch_pr_state() -> PRState:
    data = _gh("pr", "view", str(_PR_NUMBER), "--repo", _REPO,
                "--json", "headRefOid,state,isDraft")
    return PRState(
        head_sha=data.get("headRefOid", ""),
        open=data.get("state") == "OPEN",
        draft=bool(data.get("isDraft")),
    )


def _fetch_checks(sha: str) -> list[CheckState]:
    """Collect check-runs and commit-statuses for *sha*."""
    checks: list[CheckState] = []

    # Check runs via the API
    try:
        data = _gh("api",
            "/repos/" + _REPO + "/commits/" + sha + "/check-runs",
            "-H", "Accept: application/vnd.github+json")
        for c in data.get("check_runs", []):
            checks.append(CheckState(
                name=c["name"],
                status=c.get("status", ""),
                conclusion=c.get("conclusion"),
            ))
    except Exception:
        pass  # check-runs endpoint can 422 on older repos

    # Commit statuses
    try:
        data = _gh("api",
            "/repos/" + _REPO + "/commits/" + sha + "/statuses")
        for s in data:
            checks.append(CheckState(
                name=s.get("context", ""),
                status="completed",
                conclusion=s.get("state"),
            ))
    except Exception:
        pass

    return checks


def _fetch_mergeable() -> MergeableState:
    data = _gh("pr", "view", str(_PR_NUMBER), "--repo", _REPO,
                "--json", "mergeable,mergeStateStatus")
    return MergeableState(
        mergeable=data.get("mergeStateStatus", "UNKNOWN"),
        detail=None,
    )


# ── verdict fetchers (stubs — real daemon reads from DB) ──
# These are supplied to the guarded_merge engine. When run as a job the
# engines don't have database access; the route pre-validates evidence
# verdicts.  The merge engine's fetch_review_verdict / fetch_qa_verdict
# are therefore hardcoded to the REQUIRED values (the guard was already
# satisfied pre-job-creation).  The SHA re-fetch at merge time is the
# real freshness check.

def _fetch_review_verdict() -> str:
    return "APPROVE"


def _fetch_qa_verdict() -> str:
    return "PASS"


# ── merge performer ──

def _perform_merge(method: str) -> MergeResult:
    _gh("pr", "merge", str(_PR_NUMBER),
        "--repo", _REPO,
        "--" + method,
        "--subject", "Auto-merge PR #" + str(_PR_NUMBER) + " (PR CI cleared)")
    merged_sha = ""
    try:
        merged_sha = _gh("api",
            "/repos/" + _REPO + "/pulls/" + str(_PR_NUMBER) + "/merge",
            "--jq", ".sha")
    except Exception:
        pass
    return MergeResult(merged_sha=merged_sha, merged_at=_now_iso())


# ── main ──

def main() -> int:
    print("PR CI waiter: repo=" + _REPO + " pr=#" + str(_PR_NUMBER)
          + " sha=" + _PINNED_HEAD_SHA[:8], file=sys.stderr)
    print("  expected_checks=" + str(_EXPECTED_CHECKS), file=sys.stderr)
    print("  timeout=" + str(_TIMEOUT_SECONDS) + "s settle=" + str(_SETTLE_SECONDS) + "s", file=sys.stderr)

    # Phase 1: wait for CI
    waiter_verdict = wait_for_ci(
        repo=_REPO,
        pr_number=_PR_NUMBER,
        pinned_head_sha=_PINNED_HEAD_SHA,
        expected_checks=_EXPECTED_CHECKS,
        settle_seconds=_SETTLE_SECONDS,
        poll_interval_seconds=_POLL_INTERVAL,
        timeout_seconds=_TIMEOUT_SECONDS,
        fetch_pr_state=_fetch_pr_state,
        fetch_checks=_fetch_checks,
        clock=_clock,
    )

    print("Waiter verdict: " + waiter_verdict.verdict
          + " (elapsed " + str(round(waiter_verdict.elapsed_seconds or 0, 1)) + "s)", file=sys.stderr)

    # Phase 2: guarded merge
    merge_verdict = guarded_merge(
        repo=_REPO,
        pr_number=_PR_NUMBER,
        pinned_head_sha=_PINNED_HEAD_SHA,
        merge_method=_MERGE_METHOD,
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=_fetch_pr_state,
        fetch_mergeable=_fetch_mergeable,
        fetch_review_verdict=_fetch_review_verdict,
        fetch_qa_verdict=_fetch_qa_verdict,
        perform_merge=_perform_merge,
        clock_now=_now_iso,
    )

    # Output structured final JSON
    final = {{
        "verdict": merge_verdict.verdict,
        "pr_number": merge_verdict.pr_number,
        "pinned_head_sha": merge_verdict.pinned_head_sha,
        "merged_sha": merge_verdict.merged_sha,
        "merged_at": merge_verdict.merged_at,
        "observed_head_sha": merge_verdict.observed_head_sha,
        "error_detail": merge_verdict.error_detail,
    }}
    print(_json.dumps(final), file=sys.stdout)

    exit_code = MERGE_EXIT_CODES.get(merge_verdict.verdict, 99)
    print("Exit: " + merge_verdict.verdict + " (code " + str(exit_code) + ")", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
'''
    return script


# ── route ──────────────────────────────────────────────────────────────────


@router.post("/pr-ci/complete", status_code=201)
async def pr_ci_complete(slug: str, body: PrCiCompleteBody, org: OrgDep) -> dict:
    """Submit a bounded PR CI / guarded merge job.

    Validates agent task/session auth, evidence verdicts, and lineage,
    then constructs a daemon-generated script and creates a jobs row
    with review_required=false, persistent=false.
    """
    # ── 1. Validate task/session auth (mirrors jobs submit) ──
    task = org.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_task", "task_id": body.task_id},
        )

    if task.status.value not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail={"code": "task_not_active", "status": task.status.value},
        )

    agent = task.assigned_agent
    active_sid = org.sessions.get_active(body.task_id, agent)
    if active_sid is None or active_sid != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_mismatch",
                "active": active_sid,
                "got": body.session_id,
            },
        )

    # ── 2a. Validate evidence tasks exist and carry correct verdicts ──
    _check_evidence_verdict(org, body.review_task_id, _REQUIRED_REVIEW_VERDICT)
    _check_evidence_verdict(org, body.qa_task_id, _REQUIRED_QA_VERDICT)

    # ── 2b. Validate evidence tasks are in the submitting task's lineage ──
    _check_in_lineage(org, body.task_id, body.review_task_id)
    _check_in_lineage(org, body.task_id, body.qa_task_id)

    # ── 3. Construct the daemon-generated script ──
    script_text = _build_pr_ci_script(
        repo=body.repo,
        pr=body.pr,
        head_sha=body.head_sha,
        expected_checks=body.expected_checks,
        timeout_seconds=body.timeout_seconds,
        settle_seconds=body.settle_seconds,
        merge_method=body.merge_method,
    )

    # ── 4. Allocate job id + row ──
    effective_max_runtime = body.timeout_seconds + _JOB_MARGIN_SECONDS
    title = f"PR CI + merge: {body.repo}#{body.pr} ({body.head_sha[:8]})"

    async with org.db_lock:
        job_id = org.db.next_job_id()
        record = JobRecord(
            id=job_id,
            task_id=body.task_id,
            agent_name=agent,
            title=title,
            rationale="",
            script_text=script_text,
            interpreter=JobInterpreter("python3"),
            cwd_hint=None,
            status=JobStatus.PENDING,
            review_required=False,
            persistent=False,
            max_runtime_seconds=effective_max_runtime,
            created_at=_now_iso(),
        )
        org.db.insert_job(record)

    audit = AuditLogger(org.db)
    audit.log_job_submitted(
        task_id=body.task_id,
        job_id=job_id,
        agent=agent,
        title=title,
        interpreter="python3",
        cwd_hint=None,
        byte_size=len(script_text.encode("utf-8")),
        line_count=script_text.count("\n") + 1,
    )

    # ── 5. Auto-run the job immediately ──
    try:
        run_result = await _run_job_core(
            org, job_id=job_id,
            cwd_override=None, timeout_override=None,
            trigger="agent", trigger_actor=agent,
        )
    except HTTPException:
        raise

    return {
        "job_id": job_id,
        "status": run_result["status"],
        "created_at": record.created_at,
    }
