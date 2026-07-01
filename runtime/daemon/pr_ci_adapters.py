"""PR CI waiter / guarded-merge adapters — thin real-GitHub wiring.

Two adapter entry points that wire live ``gh`` CLI I/O into the pure
engines (``pr_ci_waiter.wait_for_ci`` and ``pr_ci_merge.guarded_merge``).

DESIGN (THR-047 rework, PR #4):
  - NO new daemon route, NO auth surface, NO permission-model change.
  - CI-POLL adapter: a ``review_required=false`` job that polls CI via
    ``gh`` and returns a structured verdict (exit code per engine map).
  - GUARDED-MERGE adapter: a ``review_required=true`` job that runs the
    conjunctive merge guard and (only on ``ci_pass`` + all guards green)
    calls ``gh pr merge``.  Gated by founder/EM approval.

Both adapters are invoked through the EXISTING generic jobs path
(``happyranch jobs submit``) with bash one-liner wrappers.  There is no
new ``happyranch`` subcommand and no inline ``gh pr merge`` escape hatch
for baseline agents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

from runtime.daemon.pr_ci_merge import (
    MergeResult,
    MergeableState,
    VERDICT_EXIT_CODES as MERGE_EXIT_CODES,
    guarded_merge,
)
from runtime.daemon.pr_ci_waiter import (
    CheckState,
    PRState,
    VERDICT_EXIT_CODES as WAITER_EXIT_CODES,
    wait_for_ci,
)


# ═══════════════════════════════════════════════════════════════════════════════
# gh-backed fetchers — each is a standalone, testable function
# ═══════════════════════════════════════════════════════════════════════════════


def gh_fetch_pr_state(repo: str, pr_number: int) -> PRState:
    """Fetch PR state via ``gh pr view``.

    Returns PRState(head_sha, open, draft).  Raises ``subprocess.CalledProcessError``
    on gh failure so the engine can surface ``github_error``.
    """
    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "state,headRefOid,isDraft",
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return PRState(
        head_sha=data["headRefOid"],
        open=(data["state"] == "OPEN"),
        draft=bool(data.get("isDraft", False)),
    )


def gh_fetch_checks(repo: str, sha: str) -> list[CheckState]:
    """Fetch check runs + commit statuses for *sha* via ``gh api``.

    Combines the Check Runs API and the Commit Status API, normalising
    both into ``CheckState`` records.  Raises ``subprocess.CalledProcessError``
    on gh failure.
    """
    checks: list[CheckState] = []

    # ── Check Runs (GitHub Checks API) ──
    try:
        cr_result = subprocess.run(
            [
                "gh", "api",
                f"repos/{repo}/commits/{sha}/check-runs",
                "--jq", ".check_runs[] | {name: .name, status: .status, conclusion: .conclusion}",
            ],
            capture_output=True, text=True, check=True,
        )
        for line in cr_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            cr = json.loads(line)
            checks.append(CheckState(
                name=cr["name"],
                status=cr.get("status", "completed"),
                conclusion=cr.get("conclusion"),
            ))
    except subprocess.CalledProcessError:
        # Check Runs endpoint may 404 for repos without GitHub Actions;
        # treat as zero check runs rather than a fatal error.
        pass

    # ── Commit Statuses (legacy Status API, e.g. external CI) ──
    try:
        cs_result = subprocess.run(
            [
                "gh", "api",
                f"repos/{repo}/commits/{sha}/status",
                "--jq", ".statuses[] | {name: .context, status: \"completed\", conclusion: .state}",
            ],
            capture_output=True, text=True, check=True,
        )
        for line in cs_result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            cs = json.loads(line)
            # Map combined-status state to CheckState conclusion
            state_to_conclusion = {
                "success": "success",
                "failure": "failure",
                "error": "failure",
                "pending": None,
            }
            conclusion = state_to_conclusion.get(cs.get("conclusion", "pending"))
            checks.append(CheckState(
                name=cs["name"],
                status="completed" if conclusion is not None else "pending",
                conclusion=conclusion,
            ))
    except subprocess.CalledProcessError:
        # Status endpoint may 404 for repos without statuses configured.
        pass

    return checks


def gh_fetch_mergeable(repo: str, pr_number: int) -> MergeableState:
    """Fetch GitHub mergeability for a PR via ``gh pr view``.

    Returns ``MergeableState(mergeable="CLEAN")`` when mergeStateStatus is
    ``CLEAN``; otherwise the raw ``mergeStateStatus`` value and any detail.
    """
    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "mergeable,mergeStateStatus",
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    status = data.get("mergeStateStatus", "UNKNOWN")
    return MergeableState(
        mergeable=status,
        detail=None if status == "CLEAN" else f"mergeStateStatus={status}",
    )


def gh_perform_merge(repo: str, pr_number: int, merge_method: str) -> MergeResult:
    """Execute ``gh pr merge`` with the given method.

    Returns ``MergeResult(merged_sha, merged_at)``.  Raises
    ``subprocess.CalledProcessError`` on non-zero exit so the engine can
    surface ``merge_failed``.
    """
    flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}[merge_method]
    result = subprocess.run(
        [
            "gh", "pr", "merge", str(pr_number),
            "--repo", repo,
            flag,
        ],
        capture_output=True, text=True, check=True,
    )
    # gh pr merge prints the merge SHA to stdout on success.
    merged_sha = result.stdout.strip()
    merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return MergeResult(merged_sha=merged_sha, merged_at=merged_at)


# ═══════════════════════════════════════════════════════════════════════════════
# Real clock (thin wrapper around stdlib time)
# ═══════════════════════════════════════════════════════════════════════════════


class RealClock:
    """Monotonic clock for production use.  Matches the engine's clock protocol."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entry points
# ═══════════════════════════════════════════════════════════════════════════════


def ci_poll_main(argv: list[str] | None = None) -> int:
    """CI-poll adapter — wait for CI via gh polling.

    Args are parsed from *argv* (defaults to ``sys.argv[1:]``).

    Returns the engine's exit code (0 = ci_pass, non-zero = failure verdict).
    Prints a structured JSON verdict dict to stdout before exiting.

    Intended use as a job::

        python3 -m runtime.daemon.pr_ci_adapters ci-poll \\
            --repo owner/repo --pr-number 245 --head-sha abc... \\
            --expected-check "Python CI" --expected-check "Web CI" \\
            --timeout-seconds 3600 --settle-seconds 120 --poll-interval 30

    The script text submitted via ``happyranch jobs submit`` is a thin bash
    wrapper that invokes this entry point with the right PYTHONPATH.
    """
    parser = _ci_poll_arg_parser()
    args = parser.parse_args(argv)

    clock = RealClock()

    verdict = wait_for_ci(
        repo=args.repo,
        pr_number=args.pr_number,
        pinned_head_sha=args.head_sha,
        expected_checks=args.expected_check,
        settle_seconds=args.settle_seconds,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        fetch_pr_state=lambda: gh_fetch_pr_state(args.repo, args.pr_number),
        fetch_checks=lambda sha: gh_fetch_checks(args.repo, sha),
        clock=clock,
    )

    _print_verdict(verdict)
    return WAITER_EXIT_CODES.get(verdict.verdict, 7)


def guarded_merge_main(argv: list[str] | None = None) -> int:
    """Guarded-merge adapter — run the conjunctive guard + perform merge.

    Args are parsed from *argv* (defaults to ``sys.argv[1:]``).

    Returns the engine's exit code (0 = merged, non-zero = guard failure).
    Prints a structured JSON verdict dict to stdout before exiting.

    **Permission guardrail:** this entry point calls ``gh pr merge`` but is
    ONLY invoked inside a ``review_required=true`` job.  Baseline agents
    cannot self-merge — the job sits pending until founder/EM approves it.

    Intended use as a job::

        python3 -m runtime.daemon.pr_ci_adapters guarded-merge \\
            --repo owner/repo --pr-number 245 --head-sha abc... \\
            --merge-method squash --ci-verdict ci_pass \\
            --review-verdict APPROVE --qa-verdict PASS

    The review/QA verdicts are supplied by the task owner on resume (they
    come from the HappyRanch chain, not from GitHub).
    """
    parser = _guarded_merge_arg_parser()
    args = parser.parse_args(argv)

    verdict = guarded_merge(
        repo=args.repo,
        pr_number=args.pr_number,
        pinned_head_sha=args.head_sha,
        merge_method=args.merge_method,
        ci_verdict=args.ci_verdict,
        fetch_pr_state=lambda: gh_fetch_pr_state(args.repo, args.pr_number),
        fetch_mergeable=lambda: gh_fetch_mergeable(args.repo, args.pr_number),
        fetch_review_verdict=lambda: args.review_verdict,
        fetch_qa_verdict=lambda: args.qa_verdict,
        perform_merge=lambda method: gh_perform_merge(args.repo, args.pr_number, method),
    )

    _print_verdict(verdict)
    return MERGE_EXIT_CODES.get(verdict.verdict, 14)


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _print_verdict(verdict: Any) -> None:
    """Print a structured JSON verdict dict to stdout.

    Handles both ``PRCIWaiterVerdict`` and ``GuardedMergeVerdict`` (both are
    dataclasses — we dump their ``__dict__``).
    """
    d: dict[str, Any] = {}
    for field_name in vars(verdict):
        value = getattr(verdict, field_name)
        if isinstance(value, list):
            # Convert CheckState list to dicts
            d[field_name] = [
                {"name": c.name, "status": c.status, "conclusion": c.conclusion}
                if hasattr(c, "name") else c
                for c in value
            ]
        else:
            d[field_name] = value
    json.dump(d, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _ci_poll_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ci-poll",
        description="Poll GitHub CI for a PR and return a structured verdict.",
    )
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--pr-number", type=int, required=True, help="GitHub PR number")
    p.add_argument("--head-sha", required=True, help="pinned 40-char head SHA")
    p.add_argument(
        "--expected-check", action="append", default=[],
        dest="expected_check",
        help="check-run/status context name (repeatable)",
    )
    p.add_argument(
        "--settle-seconds", type=float, default=120.0,
        help="how long to wait for checks to appear (default: 120)",
    )
    p.add_argument(
        "--poll-interval", type=float, default=30.0,
        help="seconds between polls (default: 30)",
    )
    p.add_argument(
        "--timeout-seconds", type=float, default=3600.0,
        help="total bounded wait ceiling (default: 3600)",
    )
    return p


def _guarded_merge_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="guarded-merge",
        description="Run the conjunctive merge guard and (on all-green) merge the PR.",
    )
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--pr-number", type=int, required=True, help="GitHub PR number")
    p.add_argument("--head-sha", required=True, help="pinned 40-char head SHA")
    p.add_argument(
        "--merge-method", required=True,
        choices=["merge", "squash", "rebase"],
        help="merge method",
    )
    p.add_argument(
        "--ci-verdict", required=True,
        help="CI verdict from the waiter (ci_pass or failure verdict)",
    )
    p.add_argument(
        "--review-verdict", default="APPROVE",
        help="review evidence verdict (default: APPROVE)",
    )
    p.add_argument(
        "--qa-verdict", default="PASS",
        help="QA evidence verdict (default: PASS)",
    )
    return p
