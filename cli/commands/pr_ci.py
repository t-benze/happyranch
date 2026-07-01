"""PR CI / guarded merge CLI helpers.

``happyranch pr-ci complete`` — submit a bounded PR CI / guarded merge job
through the daemon route POST /api/v1/orgs/{slug}/pr-ci/complete.
"""
from __future__ import annotations

import argparse
import sys

from cli._shared import _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def register(sub: argparse._SubParsersAction) -> None:
    pr_ci = sub.add_parser("pr-ci", help="PR CI / guarded merge helpers")
    pr_ci_sub = pr_ci.add_subparsers(dest="pr_ci_action")

    complete = pr_ci_sub.add_parser(
        "complete",
        help="Submit a bounded PR CI + guarded merge job",
    )
    complete.add_argument("--org", help="Org slug")
    complete.add_argument("--task-id", required=True, help="Task ID")
    complete.add_argument("--session-id", required=True, help="Session ID")
    complete.add_argument("--repo", required=True, help="owner/repo")
    complete.add_argument("--pr", required=True, type=int, help="PR number")
    complete.add_argument("--head-sha", required=True, help="Pinned PR head SHA")
    complete.add_argument(
        "--expected-check",
        action="append",
        dest="expected_checks",
        required=True,
        help="Check context name (repeatable)",
    )
    complete.add_argument("--review-task-id", required=True, help="Review evidence task ID")
    complete.add_argument("--qa-task-id", required=True, help="QA evidence task ID")
    complete.add_argument(
        "--timeout-seconds", required=True, type=int, help="Total CI wait timeout"
    )
    complete.add_argument(
        "--settle-seconds", required=True, type=int, help="Settle window for checks"
    )
    complete.add_argument(
        "--merge-method",
        required=True,
        choices=["merge", "squash", "rebase"],
        help="Merge method",
    )
    complete.set_defaults(func=cmd_pr_ci_complete)


def cmd_pr_ci_complete(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required", file=sys.stderr)
        sys.exit(1)

    body = {
        "task_id": args.task_id,
        "session_id": args.session_id,
        "repo": args.repo,
        "pr": args.pr,
        "head_sha": args.head_sha,
        "expected_checks": args.expected_checks,
        "review_task_id": args.review_task_id,
        "qa_task_id": args.qa_task_id,
        "timeout_seconds": args.timeout_seconds,
        "settle_seconds": args.settle_seconds,
        "merge_method": args.merge_method,
    }

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    r = client.post(f"/api/v1/orgs/{args.org}/pr-ci/complete", json=body)
    if not _ok(r):
        return
    result = r.json()
    print(f"ok: submitted {result['job_id']} (status={result['status']})")
    print(f"Self-block your task: status=blocked waiting_on_job_ids=['{result['job_id']}']")
