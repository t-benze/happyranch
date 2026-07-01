"""End-to-end integration tests for the PR CI waiter → guarded merge workflow.

Ties the two pure engines (pr_ci_waiter + pr_ci_merge) together in the
canonical poll → resume → guard → merge-or-fail flow with FAKE injected
callables.  Covers the three workflow scenarios from the spec §6 PR-5
plan and the protocol/skills/jobs/SKILL.md end-to-end example:

  (a) Happy path: waiter → ci_pass → guarded_merge → merged
  (b) CI failed:  waiter → ci_failed → guarded_merge does NOT merge
  (c) Stale head: waiter → stale_head → guarded_merge does NOT merge

No network, no real `gh` CLI, no daemon routes — the engines are injectable
pure functions and every verdict path is unit-testable with fakes.

Mirrors the mocking style of test_pr_ci_waiter.py / test_pr_ci_merge.py:
FakeClock for deterministic time, lambda-based PR/check/mergeability/verdict
fakes, and explicit assertion on the chained output.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from runtime.daemon.pr_ci_waiter import (
    CheckState,
    PRCIWaiterVerdict,
    PRState,
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


# ═══════════════════════════════════════════════════════════════════════════════
# Fakes — deterministic, no I/O
# ═══════════════════════════════════════════════════════════════════════════════


class FakeClock:
    """Deterministic monotonic clock. Only advances via sleep()."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _pr(sha: str = "a" * 40, open: bool = True, draft: bool = False) -> PRState:
    return PRState(head_sha=sha, open=open, draft=draft)


def _check(name: str, status: str, conclusion: str | None = None) -> CheckState:
    return CheckState(name=name, status=status, conclusion=conclusion)


def _mergeable(clean: bool = True) -> MergeableState:
    return MergeableState(mergeable="CLEAN" if clean else "BLOCKED", detail=None)


def _result(sha: str = "m" * 40, merged_at: str | None = None) -> MergeResult:
    if merged_at is None:
        merged_at = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    return MergeResult(merged_sha=sha, merged_at=merged_at)


# ═══════════════════════════════════════════════════════════════════════════════
# E2E helper — chains waiter verdict into guarded_merge with fakes
# ═══════════════════════════════════════════════════════════════════════════════


def _e2e_flow(
    *,
    pinned_head_sha: str = "a" * 40,
    expected_checks: list[str] | None = None,
    pr_fetches: list[PRState] | None = None,
    check_fetches: list[list[CheckState]] | None = None,
    settle_seconds: float = 0.0,
    merge_head_sha: str | None = None,
    mergeable_clean: bool = True,
    review_verdict: str = "APPROVE",
    qa_verdict: str = "PASS",
    merge_method: str = "squash",
    merged_sha: str = "m" * 40,
) -> tuple[PRCIWaiterVerdict, GuardedMergeVerdict, FakeClock]:
    """Run the full poll → merge e2e flow with fakes.

    Returns (waiter_verdict, merge_verdict, clock) for assertion.
    """
    clock = FakeClock()

    # ── Waiter phase ──────────────────────────────────────────────────────────
    checks_expected = expected_checks or ["ci"]

    # Index-tracking fakes that consume from the supplied sequences
    pr_idx: int = -1
    check_idx: int = -1

    def fetch_pr() -> PRState:
        nonlocal pr_idx
        pr_idx += 1
        if pr_fetches and pr_idx < len(pr_fetches):
            return pr_fetches[pr_idx]
        return _pr(pinned_head_sha)

    def fetch_checks(sha: str) -> list[CheckState]:
        nonlocal check_idx
        check_idx += 1
        if check_fetches and check_idx < len(check_fetches):
            return check_fetches[check_idx]
        # Default: all checks pass immediately
        return [_check(name, "completed", "success") for name in checks_expected]

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha=pinned_head_sha,
        expected_checks=checks_expected,
        settle_seconds=settle_seconds,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )

    # ── Merge phase ───────────────────────────────────────────────────────────
    head_at_merge = merge_head_sha if merge_head_sha is not None else pinned_head_sha

    merge_call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        merge_call_log.append(method)
        return _result(sha=merged_sha)

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha=pinned_head_sha,
        merge_method=merge_method,
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(head_at_merge),
        fetch_mergeable=lambda: _mergeable(clean=mergeable_clean),
        fetch_review_verdict=lambda: review_verdict,
        fetch_qa_verdict=lambda: qa_verdict,
        perform_merge=track_merge,
    )

    return waiter_verdict, merge_verdict, clock


# ═══════════════════════════════════════════════════════════════════════════════
# (a) Happy path: ci_pass → merged
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_happy_path_all_checks_pass_then_merge() -> None:
    """Waiter returns ci_pass → guarded_merge returns merged with merge details."""
    w_verdict, m_verdict, _ = _e2e_flow(
        pinned_head_sha="a" * 40,
        expected_checks=["Python CI", "Web CI"],
        check_fetches=[
            [
                _check("Python CI", "completed", "success"),
                _check("Web CI", "completed", "success"),
            ]
        ],
    )

    # Waiter phase
    assert w_verdict.verdict == "ci_pass"
    assert WAITER_EXIT_CODES["ci_pass"] == 0

    # Merge phase — happy path
    assert m_verdict.verdict == "merged"
    assert m_verdict.merged_sha == "m" * 40
    assert m_verdict.pr_number == 245
    assert m_verdict.pinned_head_sha == "a" * 40
    assert m_verdict.merged_at is not None
    assert MERGE_EXIT_CODES["merged"] == 0


def test_e2e_happy_path_checks_pending_then_pass() -> None:
    """Waiter sees in_progress → later success → ci_pass → merge.

    Simulates the realistic pattern: checks start non-terminal, then complete.
    """
    w_verdict, m_verdict, clock = _e2e_flow(
        expected_checks=["Python CI"],
        check_fetches=[
            [_check("Python CI", "queued")],
            [_check("Python CI", "in_progress")],
            [_check("Python CI", "completed", "success")],
        ],
    )

    assert w_verdict.verdict == "ci_pass"
    # Clock should have slept between non-terminal polls
    assert len(clock.sleeps) >= 2

    assert m_verdict.verdict == "merged"
    assert m_verdict.merged_sha == "m" * 40


def test_e2e_happy_path_skipped_and_neutral_are_pass() -> None:
    """skipped and neutral conclusions count as pass → ci_pass → merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["lint", "test", "build"],
        check_fetches=[
            [
                _check("lint", "completed", "skipped"),
                _check("test", "completed", "neutral"),
                _check("build", "completed", "success"),
            ]
        ],
    )

    assert w_verdict.verdict == "ci_pass"
    assert m_verdict.verdict == "merged"


# ═══════════════════════════════════════════════════════════════════════════════
# (b) CI failed: ci_failed → merge NOT reached
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_ci_failed_single_failing_check() -> None:
    """Waiter returns ci_failed → guarded_merge passes through ci_failed;
    merge is NOT attempted.
    """
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["Python CI"],
        check_fetches=[
            [_check("Python CI", "completed", "failure")],
        ],
    )

    # Waiter: ci_failed
    assert w_verdict.verdict == "ci_failed"
    assert WAITER_EXIT_CODES["ci_failed"] != 0

    # Merge: pass-through ci_failed — NO merge
    assert m_verdict.verdict == "ci_failed"
    assert m_verdict.merged_sha is None
    assert MERGE_EXIT_CODES["ci_failed"] != 0


def test_e2e_ci_failed_cancelled_check() -> None:
    """cancelled check counts as failed → ci_failed → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[
            [_check("ci", "completed", "cancelled")],
        ],
    )

    assert w_verdict.verdict == "ci_failed"
    assert m_verdict.verdict == "ci_failed"
    assert m_verdict.merged_sha is None


def test_e2e_ci_failed_timed_out_check() -> None:
    """timed_out check counts as failed → ci_failed → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[
            [_check("ci", "completed", "timed_out")],
        ],
    )

    assert w_verdict.verdict == "ci_failed"
    assert m_verdict.verdict == "ci_failed"


def test_e2e_ci_failed_action_required_check() -> None:
    """action_required conclusion → ci_failed → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[
            [_check("ci", "completed", "action_required")],
        ],
    )

    assert w_verdict.verdict == "ci_failed"
    assert m_verdict.verdict == "ci_failed"


def test_e2e_ci_failed_one_of_many() -> None:
    """One failing check among many → ci_failed → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["lint", "test", "build"],
        check_fetches=[
            [
                _check("lint", "completed", "success"),
                _check("test", "completed", "failure"),
                _check("build", "completed", "success"),
            ]
        ],
    )

    assert w_verdict.verdict == "ci_failed"
    assert m_verdict.verdict == "ci_failed"
    assert m_verdict.merged_sha is None


# ═══════════════════════════════════════════════════════════════════════════════
# (c) Stale head: head SHA != pinned → stale_head → no merge
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_stale_head_at_waiter_time() -> None:
    """Waiter detects SHA change → stale_head → guarded_merge passes through
    stale_head; merge NOT attempted.
    """
    # PR head is "b"*40 but pinned is "a"*40 → stale_head on first poll
    w_verdict, m_verdict, _ = _e2e_flow(
        pinned_head_sha="a" * 40,
        pr_fetches=[_pr(sha="b" * 40)],
    )

    assert w_verdict.verdict == "stale_head"
    assert w_verdict.observed_head_sha == "b" * 40
    assert WAITER_EXIT_CODES["stale_head"] != 0

    # Merge: pass-through stale_head — NO merge
    assert m_verdict.verdict == "stale_head"
    assert m_verdict.merged_sha is None
    assert MERGE_EXIT_CODES["stale_head"] != 0


def test_e2e_stale_head_between_ci_pass_and_merge() -> None:
    """Waiter passes ci_pass, but merge-time head SHA check detects stale →
    stale_head verdict from guarded_merge. Merge NOT attempted.

    This is the spec §4.2 guard 3 / §7 trap 1: SHA pinned across the
    waiter → merge boundary.
    """
    # Waiter sees matching SHA, checks pass → ci_pass
    # But at merge time, the head SHA is different
    w_verdict, m_verdict, _ = _e2e_flow(
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        check_fetches=[[_check("ci", "completed", "success")]],
        merge_head_sha="b" * 40,  # SHA changed between waiter and merge
    )

    # Waiter: ci_pass (matching head at poll time)
    assert w_verdict.verdict == "ci_pass"

    # Merge: stale_head (head SHA changed at merge time) — NO merge
    assert m_verdict.verdict == "stale_head"
    assert m_verdict.observed_head_sha == "b" * 40
    assert m_verdict.merged_sha is None
    assert MERGE_EXIT_CODES["stale_head"] != 0


def test_e2e_stale_head_mid_wait() -> None:
    """Head SHA flips between waiter polls → stale_head, merge not reached."""
    w_verdict, m_verdict, _ = _e2e_flow(
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        # Poll 1: matching SHA, check in_progress → continue
        # Poll 2: SHA changed → stale_head
        pr_fetches=[_pr("a" * 40), _pr("b" * 40)],
        check_fetches=[
            [_check("ci", "in_progress")],
            # Second poll: stale_head returned before checks read
            [],
        ],
    )

    assert w_verdict.verdict == "stale_head"
    assert m_verdict.verdict == "stale_head"
    assert m_verdict.merged_sha is None


# ═══════════════════════════════════════════════════════════════════════════════
# Additional integration scenarios from the verdict table (spec §4.3)
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_checks_missing_after_settle_no_merge() -> None:
    """Expected checks never appear → checks_missing → no merge."""
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        return []  # never appear

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=5.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert waiter_verdict.verdict == "checks_missing"

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(),
        fetch_mergeable=lambda: _mergeable(clean=True),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=lambda m: _result(),
    )
    assert merge_verdict.verdict == "checks_missing"
    assert merge_verdict.merged_sha is None


def test_e2e_timeout_no_merge() -> None:
    """Waiter timeout → timeout verdict → no merge."""
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        return [_check("ci", "in_progress")]  # never finishes

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=5.0,
        timeout_seconds=10.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert waiter_verdict.verdict == "timeout"

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(),
        fetch_mergeable=lambda: _mergeable(clean=True),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=lambda m: _result(),
    )
    assert merge_verdict.verdict == "timeout"
    assert merge_verdict.merged_sha is None


def test_e2e_pr_closed_no_merge() -> None:
    """PR closed → pr_closed → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        pr_fetches=[_pr(open=False)],
    )
    assert w_verdict.verdict == "pr_closed"
    assert m_verdict.verdict == "pr_closed"
    assert m_verdict.merged_sha is None


def test_e2e_pr_draft_no_merge() -> None:
    """PR is draft → pr_draft → no merge."""
    w_verdict, m_verdict, _ = _e2e_flow(
        pr_fetches=[_pr(draft=True)],
    )
    assert w_verdict.verdict == "pr_draft"
    assert m_verdict.verdict == "pr_draft"
    assert m_verdict.merged_sha is None


def test_e2e_github_error_no_merge() -> None:
    """GitHub API error during fetch → github_error → no merge."""

    def error_fetch() -> PRState:
        raise RuntimeError("GitHub API 500")

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=error_fetch,
        fetch_checks=lambda sha: [],
        clock=FakeClock(),
    )
    assert waiter_verdict.verdict == "github_error"

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(),
        fetch_mergeable=lambda: _mergeable(clean=True),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=lambda m: _result(),
    )
    assert merge_verdict.verdict == "github_error"
    assert merge_verdict.merged_sha is None


# ═══════════════════════════════════════════════════════════════════════════════
# Merge-guard failure scenarios within the e2e chain
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_merge_guard_review_not_approve() -> None:
    """Waiter passes ci_pass, but review verdict != APPROVE → merge_guard_review."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[[_check("ci", "completed", "success")]],
        review_verdict="REQUEST_CHANGES",
    )
    assert w_verdict.verdict == "ci_pass"
    assert m_verdict.verdict == "merge_guard_review"
    assert m_verdict.merged_sha is None


def test_e2e_merge_guard_qa_not_pass() -> None:
    """Waiter passes ci_pass, but QA verdict != PASS → merge_guard_qa."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[[_check("ci", "completed", "success")]],
        qa_verdict="FAIL",
    )
    assert w_verdict.verdict == "ci_pass"
    assert m_verdict.verdict == "merge_guard_qa"
    assert m_verdict.merged_sha is None


def test_e2e_merge_guard_mergeable_not_clean() -> None:
    """Waiter passes ci_pass, but mergeability != CLEAN → merge_guard_mergeable."""
    w_verdict, m_verdict, _ = _e2e_flow(
        expected_checks=["ci"],
        check_fetches=[[_check("ci", "completed", "success")]],
        mergeable_clean=False,
    )
    assert w_verdict.verdict == "ci_pass"
    assert m_verdict.verdict == "merge_guard_mergeable"
    assert m_verdict.merged_sha is None


def test_e2e_merge_failed_on_perform_merge_error() -> None:
    """Waiter passes ci_pass, all guards pass, but merge command fails →
    merge_failed."""
    clock = FakeClock()

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=lambda: _pr(),
        fetch_checks=lambda sha: [_check("ci", "completed", "success")],
        clock=clock,
    )
    assert waiter_verdict.verdict == "ci_pass"

    def bad_merge(method: str) -> MergeResult:
        raise RuntimeError("gh pr merge exit 1: branch protection")

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(),
        fetch_mergeable=lambda: _mergeable(clean=True),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=bad_merge,
    )
    assert merge_verdict.verdict == "merge_failed"
    assert "branch protection" in (merge_verdict.error_detail or "")
    assert merge_verdict.merged_sha is None


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: merge is NEVER called when the waiter returns non-pass verdict
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "ci_failure_verdict,check_fetch",
    [
        ("ci_failed", [_check("ci", "completed", "failure")]),
        ("stale_head", None),  # pr_fetch returns different SHA
        ("checks_missing", None),  # checks never appear
        ("timeout", None),  # always in_progress
        ("pr_closed", None),  # pr_fetch returns open=False
        ("pr_draft", None),  # pr_fetch returns draft=True
    ],
)
def test_e2e_merge_never_called_on_waiter_failure(
    ci_failure_verdict: str,
    check_fetch: object,
) -> None:
    """For every waiter failure verdict, the guarded_merge engine must NOT
    invoke perform_merge."""
    pinned = "a" * 40
    clock = FakeClock()

    def fetch_pr() -> PRState:
        if ci_failure_verdict == "stale_head":
            return _pr(sha="b" * 40)  # different SHA
        elif ci_failure_verdict == "pr_closed":
            return _pr(open=False)
        elif ci_failure_verdict == "pr_draft":
            return _pr(draft=True)
        return _pr(pinned)

    def fetch_checks(sha: str) -> list[CheckState]:
        if ci_failure_verdict == "ci_failed" and isinstance(check_fetch, list):
            return check_fetch  # type: ignore[return-value]
        elif ci_failure_verdict == "checks_missing":
            # After settle window, checks never appear
            # Advance clock past settle but before timeout
            clock.sleep(10.0)
            return []
        elif ci_failure_verdict == "timeout":
            # Always in_progress, never finishes
            return [_check("ci", "in_progress")]
        return [_check("ci", "completed", "success")]

    waiter_verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha=pinned,
        expected_checks=["ci"],
        settle_seconds=0.0 if ci_failure_verdict != "checks_missing" else 5.0,
        poll_interval_seconds=1.0,
        timeout_seconds=5.0 if ci_failure_verdict == "timeout" else 120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert waiter_verdict.verdict == ci_failure_verdict, (
        f"waiter expected {ci_failure_verdict}, got {waiter_verdict.verdict}"
    )

    merge_call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        merge_call_log.append("PERFORM_MERGE_CALLED")
        return _result()

    merge_verdict = guarded_merge(
        repo="test-owner/test-repo",
        pr_number=245,
        pinned_head_sha=pinned,
        merge_method="squash",
        ci_verdict=waiter_verdict.verdict,
        fetch_pr_state=lambda: _pr(pinned),
        fetch_mergeable=lambda: _mergeable(clean=True),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=track_merge,
    )

    # The pass-through must match
    assert merge_verdict.verdict == ci_failure_verdict, (
        f"merge expected {ci_failure_verdict}, got {merge_verdict.verdict}"
    )
    assert merge_verdict.merged_sha is None
    # The critical invariant: merge was NEVER called
    assert "PERFORM_MERGE_CALLED" not in merge_call_log, (
        f"perform_merge was called despite waiter verdict {ci_failure_verdict}!"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# E2E workflow covers all 6 waiter failure verdicts in the decision table
# ═══════════════════════════════════════════════════════════════════════════════


def test_e2e_decision_table_verdicts_are_defined() -> None:
    """Verify that every verdict in the protocol/skills/jobs/SKILL.md decision
    table maps to a known waiter or merge-engine verdict with an exit code."""
    expected_verdicts = {
        "ci_pass",
        "ci_failed",
        "stale_head",
        "checks_missing",
        "timeout",
        "pr_closed",
        "pr_draft",
        "github_error",
    }
    # All waiter verdicts
    for v in expected_verdicts:
        assert v in WAITER_EXIT_CODES, f"{v!r} missing from waiter exit codes"

    # All merge verdicts (merged + pass-through + guards + error)
    # ci_pass is intentionally absent from merge codes — merge engine never returns it
    merge_expected = {
        "merged",
        "ci_failed",
        "stale_head",
        "checks_missing",
        "timeout",
        "pr_closed",
        "pr_draft",
        "github_error",
        "merge_guard_review",
        "merge_guard_qa",
        "merge_guard_mergeable",
        "merge_failed",
    }
    for v in merge_expected:
        assert v in MERGE_EXIT_CODES, f"{v!r} missing from merge exit codes"

    # ci_pass should NOT be in MERGE_EXIT_CODES as a separate entry
    # (merged=0 is the success code; ci_pass is unused by the merge engine)
    # But it doesn't hurt if present — just verify merged=0 exists
    assert MERGE_EXIT_CODES["merged"] == 0
