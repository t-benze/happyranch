"""Guarded PR-CI merge engine — a pure, unit-testable conjunctive guard.

Given a CI verdict from the waiter engine (runtime/daemon/pr_ci_waiter.py),
this engine re-verifies every precondition at merge time and performs the
merge ONLY when ALL guards pass.

DESIGN: pure engine — no network calls, no `gh` CLI invocation, no real
sleeps inside the implementation.  Every GitHub interaction (fetch PR state,
fetch mergeability, fetch review/QA evidence, perform the merge) is an
INJECTED callable so all guard paths are unit-testable with fakes.

VERDICT SET (spec §4.3):
  merged, merge_guard_review, merge_guard_qa, merge_guard_mergeable,
  merge_failed
plus pass-through of waiter verdicts:
  ci_failed, stale_head, checks_missing, timeout, pr_closed, pr_draft,
  github_error

This module is PR #3 of the TASK-1279 breakdown.  Spec: §4.2 (guarded merge
engine), §4.3 (verdict vocabulary table), §7 (traps).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from runtime.daemon.pr_ci_waiter import PRState

# ── data types ───────────────────────────────────────────────────────────────


@dataclass
class MergeableState:
    """GitHub mergeability status for the PR."""

    mergeable: str  # CLEAN | BLOCKED | UNKNOWN | ...
    detail: str | None = None


@dataclass
class MergeResult:
    """Output of a successful merge operation."""

    merged_sha: str
    merged_at: str


@dataclass
class GuardedMergeVerdict:
    """Structured terminal verdict from the guarded-merge engine.

    Mirrors the spec §4.2 output JSON shape:
      verdict, merged_sha, merged_at, pr_number, pinned_head_sha,
      observed_head_sha, error_detail
    """

    verdict: str
    pr_number: int
    pinned_head_sha: str
    merged_sha: str | None = None
    merged_at: str | None = None
    observed_head_sha: str | None = None
    error_detail: str | None = None


# ── verdict → exit-code map ──────────────────────────────────────────────────

# Stable, distinct non-zero codes for scripting.  merged = 0.  Waiter
# pass-through verdicts keep their original codes.  New merge-guard codes
# start at 11 to avoid collision with the waiter's 1-7 range.
from runtime.daemon.pr_ci_waiter import VERDICT_EXIT_CODES as _W

VERDICT_EXIT_CODES: dict[str, int] = {
    # Inherit waiter codes for pass-through verdicts.
    # ci_pass is deliberately excluded — the merge engine never returns it;
    # merged = 0 is the sole success code.
    "ci_failed": _W["ci_failed"],
    "stale_head": _W["stale_head"],
    "checks_missing": _W["checks_missing"],
    "timeout": _W["timeout"],
    "pr_closed": _W["pr_closed"],
    "pr_draft": _W["pr_draft"],
    "github_error": _W["github_error"],
    # Merge-engine verdicts
    "merged": 0,
    "merge_guard_review": 11,
    "merge_guard_qa": 12,
    "merge_guard_mergeable": 13,
    "merge_failed": 14,
}

# ── valid merge methods ─────────────────────────────────────────────────────

_VALID_MERGE_METHODS: set[str] = {"merge", "squash", "rebase"}

# ── known waiter failure verdicts (spec §4.3) ──────────────────────────────

_KNOWN_WAITER_FAILURE_VERDICTS: set[str] = {
    "ci_failed",
    "stale_head",
    "checks_missing",
    "timeout",
    "pr_closed",
    "pr_draft",
    "github_error",
}


# ── engine ───────────────────────────────────────────────────────────────────


def guarded_merge(
    *,
    repo: str,
    pr_number: int,
    pinned_head_sha: str,
    merge_method: str,
    ci_verdict: str,
    fetch_pr_state: Callable[[], PRState],
    fetch_mergeable: Callable[[], MergeableState],
    fetch_review_verdict: Callable[[], str],
    fetch_qa_verdict: Callable[[], str],
    perform_merge: Callable[[str], MergeResult],
    clock_now: Callable[[], str] | None = None,
) -> GuardedMergeVerdict:
    """Run the conjunctive merge guard and (if all pass) perform the merge.

    Guard evaluation order (spec §4.2), short-circuiting on first failure:

        1. Review evidence verdict == APPROVE  → else ``merge_guard_review``
        2. QA evidence verdict == PASS         → else ``merge_guard_qa``
        3. Re-fetch PR state at merge time:
           head SHA unchanged == pinned_head_sha → else ``stale_head``
        4. ci_verdict == 'ci_pass'             → else pass through the
           waiter's failure verdict
        5. PR open and not draft                → else ``pr_closed`` /
           ``pr_draft``
        6. GitHub mergeability == CLEAN          → else ``merge_guard_mergeable``
        7. All pass → call perform_merge(merge_method);
           on error → ``merge_failed``;
           on success → ``merged`` with merge details.

    Parameters
    ----------
    repo: ``owner/repo`` — informational.
    pr_number: GitHub PR number.
    pinned_head_sha: full 40-char SHA the PR pointed to when the job was
        launched.  Re-verified at merge time — this is THE key stale-head guard
        (spec §7 trap 1).
    merge_method: ``merge`` | ``squash`` | ``rebase``.  Invalid value
        produces an error verdict.
    ci_verdict: verdict string from the PR CI waiter engine.
    fetch_pr_state: callable → PRState.  Used for the merge-time SHA / open /
        draft check.
    fetch_mergeable: callable → MergeableState.
    fetch_review_verdict: callable → str (e.g. ``"APPROVE"``).
    fetch_qa_verdict: callable → str (e.g. ``"PASS"``).
    perform_merge: callable taking merge_method and returning MergeResult.
        Raise an exception on failure.
    clock_now: optional callable → ISO-8601 timestamp string.  When absent,
        ``merged_at`` is taken from the MergeResult.
    """
    # ── 0. Validate merge method ──
    if merge_method not in _VALID_MERGE_METHODS:
        return GuardedMergeVerdict(
            verdict="github_error",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=(
                f"invalid merge_method {merge_method!r}; "
                f"must be one of {sorted(_VALID_MERGE_METHODS)!r}"
            ),
        )

    # ── 1. Review evidence ──
    try:
        review_v = fetch_review_verdict()
    except Exception as exc:
        return GuardedMergeVerdict(
            verdict="github_error",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=str(exc),
        )
    if review_v != "APPROVE":
        return GuardedMergeVerdict(
            verdict="merge_guard_review",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
        )

    # ── 2. QA evidence ──
    try:
        qa_v = fetch_qa_verdict()
    except Exception as exc:
        return GuardedMergeVerdict(
            verdict="github_error",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=str(exc),
        )
    if qa_v != "PASS":
        return GuardedMergeVerdict(
            verdict="merge_guard_qa",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
        )

    # ── 3. Re-fetch PR state — SHA guard (spec §4.2 pt 3 / §7 trap 1) ──
    # Per spec §4.2 lines 85-90: stale_head (guard 3) MUST be checked BEFORE
    # the CI verdict pass-through (guard 4).  open/draft (guard 5) MUST be
    # checked AFTER the CI verdict (guard 4).  fetch_pr_state is called once
    # here; its result feeds both guard 3 (stale_head) and guard 5 (open/draft).
    try:
        pr = fetch_pr_state()
    except Exception as exc:
        return GuardedMergeVerdict(
            verdict="github_error",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=str(exc),
        )
    if pr.head_sha != pinned_head_sha:
        return GuardedMergeVerdict(
            verdict="stale_head",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            observed_head_sha=pr.head_sha,
        )

    # ── 4. CI verdict pass-through (spec §4.2 pt 4) ──
    if ci_verdict != "ci_pass":
        if ci_verdict not in _KNOWN_WAITER_FAILURE_VERDICTS:
            return GuardedMergeVerdict(
                verdict="github_error",
                pr_number=pr_number,
                pinned_head_sha=pinned_head_sha,
                error_detail=f"unknown ci_verdict {ci_verdict!r}",
            )
        return GuardedMergeVerdict(
            verdict=ci_verdict,
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
        )

    # ── 5. PR open and not draft (spec §4.2 pt 5) — reuses `pr` from step 3 ──
    if not pr.open:
        return GuardedMergeVerdict(
            verdict="pr_closed",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
        )

    if pr.draft:
        return GuardedMergeVerdict(
            verdict="pr_draft",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
        )

    # ── 6. GitHub mergeability (spec §4.2 pt 6 / §7 trap 3) ──
    try:
        mergeability = fetch_mergeable()
    except Exception as exc:
        return GuardedMergeVerdict(
            verdict="github_error",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=str(exc),
        )
    if mergeability.mergeable != "CLEAN":
        return GuardedMergeVerdict(
            verdict="merge_guard_mergeable",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=(
                f"mergeability is {mergeability.mergeable!r} "
                + (f"({mergeability.detail})" if mergeability.detail else "")
            ),
        )

    # ── 7. All guards pass — perform the merge ──
    try:
        result = perform_merge(merge_method)
    except Exception as exc:
        return GuardedMergeVerdict(
            verdict="merge_failed",
            pr_number=pr_number,
            pinned_head_sha=pinned_head_sha,
            error_detail=str(exc),
        )

    return GuardedMergeVerdict(
        verdict="merged",
        pr_number=pr_number,
        pinned_head_sha=pinned_head_sha,
        merged_sha=result.merged_sha,
        merged_at=result.merged_at,
    )


# ── real-gh adapter + CLI entrypoint ───────────────────────────────────────


def _gh_fetch_pr_state(repo: str, pr_number: int) -> PRState:
    """Fetch PR state via `gh pr view`."""
    import json
    import subprocess

    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "headRefOid,state,isDraft",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr view failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    data = json.loads(result.stdout)
    return PRState(
        head_sha=data["headRefOid"],
        open=(data["state"] == "OPEN"),
        draft=data.get("isDraft", False),
    )


def _gh_fetch_mergeable(repo: str, pr_number: int) -> MergeableState:
    """Fetch mergeability status via `gh pr view`."""
    import json
    import subprocess

    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "mergeable,mergeStateStatus",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr view (mergeable) failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    data = json.loads(result.stdout)
    raw = data.get("mergeStateStatus", data.get("mergeable", "UNKNOWN"))
    detail = f"mergeable={data.get('mergeable')}, mergeStateStatus={data.get('mergeStateStatus')}"
    return MergeableState(mergeable=raw, detail=detail)


def _gh_perform_merge(repo: str, pr_number: int, merge_method: str) -> MergeResult:
    """Perform the merge via `gh pr merge`.

    Returns MergeResult with merged_sha and merged_at.
    Raises RuntimeError on failure.
    """
    import datetime
    import json
    import subprocess

    result = subprocess.run(
        [
            "gh", "pr", "merge", str(pr_number),
            "--repo", repo,
            f"--{merge_method}",
            "--admin",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr merge failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    # After merge, fetch the merge commit SHA
    sha_result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "mergeCommit",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    merged_sha = ""
    if sha_result.returncode == 0:
        try:
            data = json.loads(sha_result.stdout)
            merged_sha = data.get("mergeCommit", {}).get("oid", "")
        except (json.JSONDecodeError, KeyError):
            pass

    return MergeResult(
        merged_sha=merged_sha,
        merged_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def _recall_fetch_verdict(org: str, task_id: str, verdict_key: str) -> str:
    """Fetch a task's verdict via `happyranch recall`.

    Calls happyranch recall --org <org> <task_id> and parses the verdict
    from the completion report output.  Returns the verdict string, or
    raises RuntimeError on failure.
    """
    import json
    import subprocess

    result = subprocess.run(
        ["happyranch", "recall", "--org", org, task_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"happyranch recall {task_id} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    # The recall output contains the completion report as structured text.
    # Look for a verdict line in the output.
    for line in result.stdout.splitlines():
        stripped = line.strip()
        # Try to parse as JSON first (for structured completion output)
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and "verdict" in data:
                return data["verdict"]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: look for "verdict: <value>" pattern
        if stripped.lower().startswith("verdict:"):
            return stripped.split(":", 1)[1].strip()
    raise RuntimeError(
        f"Could not extract {verdict_key} verdict from recall output for {task_id}"
    )


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Guarded PR-CI merge — re-verify all guards and merge.",
    )
    parser.add_argument("--org", required=True, help="HappyRanch org slug")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--head-sha", required=True, help="Pinned 40-char head SHA")
    parser.add_argument(
        "--merge-method", required=True,
        choices=["merge", "squash", "rebase"],
        help="Merge method",
    )
    parser.add_argument(
        "--ci-verdict", required=True,
        help="CI verdict from the waiter engine",
    )
    parser.add_argument(
        "--review-task-id", required=True,
        help="Task ID whose completion report carries the review verdict",
    )
    parser.add_argument(
        "--qa-task-id", required=True,
        help="Task ID whose completion report carries the QA verdict",
    )
    args = parser.parse_args()

    verdict = guarded_merge(
        repo=args.repo,
        pr_number=args.pr,
        pinned_head_sha=args.head_sha,
        merge_method=args.merge_method,
        ci_verdict=args.ci_verdict,
        fetch_pr_state=lambda: _gh_fetch_pr_state(args.repo, args.pr),
        fetch_mergeable=lambda: _gh_fetch_mergeable(args.repo, args.pr),
        fetch_review_verdict=lambda: _recall_fetch_verdict(
            args.org, args.review_task_id, "review"
        ),
        fetch_qa_verdict=lambda: _recall_fetch_verdict(
            args.org, args.qa_task_id, "qa"
        ),
        perform_merge=lambda m: _gh_perform_merge(args.repo, args.pr, m),
    )

    # Print structured JSON verdict to stdout
    output = {
        "verdict": verdict.verdict,
        "pr_number": verdict.pr_number,
        "pinned_head_sha": verdict.pinned_head_sha,
        "merged_sha": verdict.merged_sha,
        "merged_at": verdict.merged_at,
        "observed_head_sha": verdict.observed_head_sha,
        "error_detail": verdict.error_detail,
    }
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")

    exit_code = VERDICT_EXIT_CODES.get(verdict.verdict, 7)
    sys.exit(exit_code)
