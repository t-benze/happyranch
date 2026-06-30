"""Unit tests for the guarded PR-CI merge engine (runtime/daemon/pr_ci_merge.py).

Covers every conjunctive guard branch (spec §4.2 and §4.3) with injected fakes —
no live network calls, no `gh` CLI invocation. Verdict set:
  merged, merge_guard_review, merge_guard_qa, merge_guard_mergeable,
  merge_failed
plus pass-through of waiter verdicts:
  ci_failed, stale_head, checks_missing, timeout, pr_closed, pr_draft,
  github_error

Exit-code mapping (stable for scripting):
  merged=0; every other verdict distinct non-zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from runtime.daemon.pr_ci_merge import (
    GuardedMergeVerdict,
    MergeableState,
    MergeResult,
    VERDICT_EXIT_CODES,
    guarded_merge,
)
from runtime.daemon.pr_ci_waiter import PRState
from runtime.daemon.pr_ci_waiter import VERDICT_EXIT_CODES as WAITER_EXIT_CODES


# ── fakes ────────────────────────────────────────────────────────────────────


def _pr(sha: str = "a" * 40, open: bool = True, draft: bool = False) -> PRState:
    return PRState(head_sha=sha, open=open, draft=draft)


def _mergeable(clean: bool = True) -> MergeableState:
    return MergeableState(mergeable="CLEAN" if clean else "BLOCKED", detail=None)


def _result(sha: str = "m" * 40, merged_at: str | None = None) -> MergeResult:
    if merged_at is None:
        merged_at = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    return MergeResult(merged_sha=sha, merged_at=merged_at)


# ── helpers ──────────────────────────────────────────────────────────────────


def _merge(
    *,
    pinned_head_sha: str = "a" * 40,
    merge_method: str = "squash",
    ci_verdict: str = "ci_pass",
    fetch_pr_state: Callable[[], PRState] | None = None,
    fetch_mergeable: Callable[[], MergeableState] | None = None,
    fetch_review_verdict: Callable[[], str] | None = None,
    fetch_qa_verdict: Callable[[], str] | None = None,
    perform_merge: Callable[[str], MergeResult] | None = None,
    clock_now: Callable[[], str] | None = None,
) -> GuardedMergeVerdict:
    """Convenience wrapper: run guarded_merge with sensible defaults."""
    return guarded_merge(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha=pinned_head_sha,
        merge_method=merge_method,
        ci_verdict=ci_verdict,
        fetch_pr_state=fetch_pr_state or (lambda: _pr(pinned_head_sha)),
        fetch_mergeable=fetch_mergeable or (lambda: _mergeable(clean=True)),
        fetch_review_verdict=fetch_review_verdict or (lambda: "APPROVE"),
        fetch_qa_verdict=fetch_qa_verdict or (lambda: "PASS"),
        perform_merge=perform_merge or (lambda m: _result()),
        clock_now=clock_now,
    )


# ── happy path: merged ───────────────────────────────────────────────────────


def test_merged_all_guards_pass() -> None:
    """All guards pass → merged verdict with merge result details."""
    verdict = _merge()
    assert verdict.verdict == "merged"
    assert verdict.merged_sha == "m" * 40
    assert verdict.merged_at is not None
    assert verdict.pr_number == 1
    assert verdict.pinned_head_sha == "a" * 40
    assert VERDICT_EXIT_CODES["merged"] == 0


def test_merged_carries_all_output_fields() -> None:
    """Merged verdict carries pr_number, pinned_head_sha, merged_sha, merged_at."""
    fixed_now = "2026-06-30T12:00:00+00:00"
    verdict = _merge(
        perform_merge=lambda m: _result(sha="b" * 40, merged_at=fixed_now),
    )
    assert verdict.verdict == "merged"
    assert verdict.merged_sha == "b" * 40
    assert verdict.merged_at == fixed_now
    assert verdict.pr_number == 1
    assert verdict.pinned_head_sha == "a" * 40


# ── guard: review ────────────────────────────────────────────────────────────


def test_merge_guard_review_not_approve() -> None:
    """Review verdict != APPROVE → merge_guard_review."""
    verdict = _merge(fetch_review_verdict=lambda: "REQUEST_CHANGES")
    assert verdict.verdict == "merge_guard_review"
    assert verdict.merged_sha is None
    assert verdict.merged_at is None
    assert VERDICT_EXIT_CODES["merge_guard_review"] != 0


def test_merge_guard_review_explicit_reject() -> None:
    """Review verdict is REJECT → merge_guard_review."""
    verdict = _merge(fetch_review_verdict=lambda: "REJECT")
    assert verdict.verdict == "merge_guard_review"


def test_merge_guard_review_pending() -> None:
    """Review verdict is PENDING (not yet complete) → merge_guard_review."""
    verdict = _merge(fetch_review_verdict=lambda: "PENDING")
    assert verdict.verdict == "merge_guard_review"


# ── guard: qa ────────────────────────────────────────────────────────────────


def test_merge_guard_qa_not_pass() -> None:
    """QA verdict != PASS → merge_guard_qa."""
    verdict = _merge(fetch_qa_verdict=lambda: "FAIL")
    assert verdict.verdict == "merge_guard_qa"
    assert verdict.merged_sha is None
    assert VERDICT_EXIT_CODES["merge_guard_qa"] != 0


def test_merge_guard_qa_pending() -> None:
    """QA verdict is PENDING → merge_guard_qa."""
    verdict = _merge(fetch_qa_verdict=lambda: "PENDING")
    assert verdict.verdict == "merge_guard_qa"


# ── guard: ci_verdict pass-through ─────────────────────────────────────────


def test_ci_verdict_pass_through_ci_failed() -> None:
    """ci_verdict='ci_failed' → pass-through ci_failed (not a merge guard)."""
    verdict = _merge(ci_verdict="ci_failed")
    assert verdict.verdict == "ci_failed"
    # Waiter exit code is stable
    assert VERDICT_EXIT_CODES["ci_failed"] == WAITER_EXIT_CODES["ci_failed"]


def test_ci_verdict_pass_through_stale_head() -> None:
    """ci_verdict='stale_head' → pass-through stale_head."""
    verdict = _merge(ci_verdict="stale_head")
    assert verdict.verdict == "stale_head"


def test_ci_verdict_pass_through_checks_missing() -> None:
    """ci_verdict='checks_missing' → pass-through checks_missing."""
    verdict = _merge(ci_verdict="checks_missing")
    assert verdict.verdict == "checks_missing"


def test_ci_verdict_pass_through_timeout() -> None:
    """ci_verdict='timeout' → pass-through timeout."""
    verdict = _merge(ci_verdict="timeout")
    assert verdict.verdict == "timeout"


def test_ci_verdict_pass_through_pr_closed() -> None:
    """ci_verdict='pr_closed' → pass-through pr_closed."""
    verdict = _merge(ci_verdict="pr_closed")
    assert verdict.verdict == "pr_closed"


def test_ci_verdict_pass_through_pr_draft() -> None:
    """ci_verdict='pr_draft' → pass-through pr_draft."""
    verdict = _merge(ci_verdict="pr_draft")
    assert verdict.verdict == "pr_draft"


def test_ci_verdict_pass_through_github_error() -> None:
    """ci_verdict='github_error' → pass-through github_error."""
    verdict = _merge(ci_verdict="github_error")
    assert verdict.verdict == "github_error"


# ── guard: stale head at merge time (spec §4.2 pt 3 / §7 trap 1) ───────────


def test_stale_head_between_ci_pass_and_merge() -> None:
    """PR head SHA changed between waiter's ci_pass and merge time → stale_head."""
    verdict = _merge(
        pinned_head_sha="a" * 40,
        fetch_pr_state=lambda: _pr(sha="b" * 40),
    )
    assert verdict.verdict == "stale_head"
    assert verdict.observed_head_sha == "b" * 40


def test_stale_head_with_observed_sha_in_verdict() -> None:
    """stale_head verdict carries observed_head_sha for diagnostics."""
    verdict = _merge(
        pinned_head_sha="a" * 40,
        fetch_pr_state=lambda: _pr(sha="c" * 40),
    )
    assert verdict.verdict == "stale_head"
    assert verdict.observed_head_sha == "c" * 40
    assert verdict.pinned_head_sha == "a" * 40


# ── guard: pr_closed at merge time ──────────────────────────────────────────


def test_pr_closed_at_merge_time() -> None:
    """PR is closed when re-fetched at merge time → pr_closed."""
    verdict = _merge(fetch_pr_state=lambda: _pr(open=False))
    assert verdict.verdict == "pr_closed"


# ── guard: pr_draft at merge time ───────────────────────────────────────────


def test_pr_draft_at_merge_time() -> None:
    """PR is draft when re-fetched at merge time → pr_draft."""
    verdict = _merge(fetch_pr_state=lambda: _pr(draft=True))
    assert verdict.verdict == "pr_draft"


# ── guard: mergeability (spec §4.2 pt 6 / §7 trap 3) ───────────────────────


def test_merge_guard_mergeable_blocked() -> None:
    """GitHub mergeability is not CLEAN → merge_guard_mergeable."""
    verdict = _merge(fetch_mergeable=lambda: _mergeable(clean=False))
    assert verdict.verdict == "merge_guard_mergeable"
    assert verdict.merged_sha is None
    assert VERDICT_EXIT_CODES["merge_guard_mergeable"] != 0


def test_merge_guard_mergeable_unknown() -> None:
    """Mergeability UNKNOWN → merge_guard_mergeable."""
    verdict = _merge(
        fetch_mergeable=lambda: MergeableState(mergeable="UNKNOWN", detail="check runs pending")
    )
    assert verdict.verdict == "merge_guard_mergeable"


# ── guard: merge_failed (perform_merge returns error) ──────────────────────


def test_merge_failed_perform_merge_raises() -> None:
    """perform_merge raises an exception → merge_failed with error_detail."""

    def bad_merge(method: str) -> MergeResult:
        raise RuntimeError("gh pr merge exit 1: branch protection")

    verdict = _merge(perform_merge=bad_merge)
    assert verdict.verdict == "merge_failed"
    assert "gh pr merge" in (verdict.error_detail or "")
    assert verdict.merged_sha is None
    assert VERDICT_EXIT_CODES["merge_failed"] != 0


def test_merge_failed_non_zero_result() -> None:
    """perform_merge returns a result with error (simulated non-zero exit)."""

    def failing_merge(method: str) -> MergeResult:
        raise RuntimeError("merge conflict")

    verdict = _merge(perform_merge=failing_merge)
    assert verdict.verdict == "merge_failed"
    assert "merge conflict" in (verdict.error_detail or "")


# ── CRITICAL: perform_merge is NEVER called when any guard fails ───────────


def test_perform_merge_not_called_on_review_fail() -> None:
    """When review guard fails, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        fetch_review_verdict=lambda: "REQUEST_CHANGES",
        perform_merge=track_merge,
    )
    assert verdict.verdict == "merge_guard_review"
    assert "perform_merge_called" not in call_log


def test_perform_merge_not_called_on_qa_fail() -> None:
    """When QA guard fails, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(fetch_qa_verdict=lambda: "FAIL", perform_merge=track_merge)
    assert verdict.verdict == "merge_guard_qa"
    assert not call_log


def test_perform_merge_not_called_on_ci_fail() -> None:
    """When ci_verdict is not ci_pass, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(ci_verdict="ci_failed", perform_merge=track_merge)
    assert verdict.verdict == "ci_failed"
    assert not call_log


def test_perform_merge_not_called_on_stale_head() -> None:
    """When head SHA changed at merge time, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(
        pinned_head_sha="a" * 40,
        fetch_pr_state=lambda: _pr(sha="b" * 40),
        perform_merge=track_merge,
    )
    assert verdict.verdict == "stale_head"
    assert not call_log


def test_perform_merge_not_called_on_pr_closed() -> None:
    """When PR is closed at merge time, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(
        fetch_pr_state=lambda: _pr(open=False),
        perform_merge=track_merge,
    )
    assert verdict.verdict == "pr_closed"
    assert not call_log


def test_perform_merge_not_called_on_pr_draft() -> None:
    """When PR is draft at merge time, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(
        fetch_pr_state=lambda: _pr(draft=True),
        perform_merge=track_merge,
    )
    assert verdict.verdict == "pr_draft"
    assert not call_log


def test_perform_merge_not_called_on_mergeable_fail() -> None:
    """When mergeability is not CLEAN, perform_merge is NOT called."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("called")
        return _result()

    verdict = _merge(
        fetch_mergeable=lambda: _mergeable(clean=False),
        perform_merge=track_merge,
    )
    assert verdict.verdict == "merge_guard_mergeable"
    assert not call_log


# ── guard ordering: short-circuits on first failure ────────────────────────


def test_short_circuit_order_review_before_qa() -> None:
    """Review guard checked before QA guard; review fail → merge_guard_review."""
    qa_called: list[bool] = [False]

    def qa() -> str:
        qa_called[0] = True
        return "PASS"

    verdict = _merge(
        fetch_review_verdict=lambda: "REQUEST_CHANGES",
        fetch_qa_verdict=qa,
    )
    assert verdict.verdict == "merge_guard_review"
    # QA should not be called if review already failed
    assert not qa_called[0]


def test_short_circuit_order_review_before_pr_state() -> None:
    """Review guard checked before re-fetching PR state."""
    pr_fetched: list[bool] = [False]

    def pr_state() -> PRState:
        pr_fetched[0] = True
        return _pr()

    verdict = _merge(
        fetch_review_verdict=lambda: "REQUEST_CHANGES",
        fetch_pr_state=pr_state,
    )
    assert verdict.verdict == "merge_guard_review"
    assert not pr_fetched[0]


# ── merge_method validation ────────────────────────────────────────────────


def test_invalid_merge_method_rejected() -> None:
    """Invalid merge_method raises an error verdict."""
    verdict = _merge(merge_method="fast-forward")
    # Should get an error verdict — the engine must validate merge_method
    assert verdict.verdict != "merged"
    assert "fast-forward" in (verdict.error_detail or "")


def test_valid_merge_methods_accepted() -> None:
    """merge, squash, rebase are all valid methods."""
    for method in ("merge", "squash", "rebase"):
        call_log: list[str] = []

        def track_merge(m: str) -> MergeResult:
            call_log.append(m)
            return _result()

        verdict = _merge(merge_method=method, perform_merge=track_merge)
        assert verdict.verdict == "merged", f"method={method} should be valid"
        assert call_log == [method], f"perform_merge should receive {method!r}"


# ── github_error on fetcher exception (spec §4.3 terminal-verdict contract) ──


def test_fetch_pr_state_raises_returns_github_error() -> None:
    """When fetch_pr_state raises, return github_error verdict (match waiter contract)."""
    call_log: list[str] = []

    def failing_fetch() -> PRState:
        raise RuntimeError("gh api /repos/.../pulls/N: 502 Bad Gateway")

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        fetch_pr_state=failing_fetch,
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "502 Bad Gateway" in (verdict.error_detail or "")
    assert verdict.merged_sha is None
    assert verdict.merged_at is None
    assert "perform_merge_called" not in call_log
    assert VERDICT_EXIT_CODES["github_error"] != 0


def test_fetch_mergeable_raises_returns_github_error() -> None:
    """When fetch_mergeable raises, return github_error verdict; perform_merge NOT called."""
    call_log: list[str] = []

    def failing_fetch() -> MergeableState:
        raise ConnectionError("failed to connect to GitHub API")

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        fetch_mergeable=failing_fetch,
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "failed to connect" in (verdict.error_detail or "")
    assert verdict.merged_sha is None
    assert "perform_merge_called" not in call_log


def test_fetch_review_verdict_raises_returns_github_error() -> None:
    """When fetch_review_verdict raises, return github_error verdict; perform_merge NOT called."""
    call_log: list[str] = []

    def failing_fetch() -> str:
        raise OSError("API error fetching review evidence")

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        fetch_review_verdict=failing_fetch,
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "API error" in (verdict.error_detail or "")
    assert "perform_merge_called" not in call_log


def test_fetch_qa_verdict_raises_returns_github_error() -> None:
    """When fetch_qa_verdict raises, return github_error verdict; perform_merge NOT called."""
    call_log: list[str] = []

    def failing_fetch() -> str:
        raise TimeoutError("QA evidence fetch timed out")

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        fetch_qa_verdict=failing_fetch,
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "timed out" in (verdict.error_detail or "")
    assert "perform_merge_called" not in call_log


# ── unknown/malformed ci_verdict → github_error (spec §4.3) ──────────────────


def test_unknown_ci_verdict_maps_to_github_error() -> None:
    """A malformed ci_verdict not in the known waiter vocabulary → github_error."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        ci_verdict="unexpected_value_xyz",
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "unknown ci_verdict" in (verdict.error_detail or "")
    assert "unexpected_value_xyz" in (verdict.error_detail or "")
    assert verdict.merged_sha is None
    assert "perform_merge_called" not in call_log
    # github_error has a valid exit code
    assert verdict.verdict in VERDICT_EXIT_CODES
    assert VERDICT_EXIT_CODES[verdict.verdict] != 0


def test_unknown_ci_verdict_empty_string() -> None:
    """An empty string ci_verdict is unknown → github_error."""
    call_log: list[str] = []

    def track_merge(method: str) -> MergeResult:
        call_log.append("perform_merge_called")
        return _result()

    verdict = _merge(
        ci_verdict="",
        perform_merge=track_merge,
    )
    assert verdict.verdict == "github_error"
    assert "unknown ci_verdict" in (verdict.error_detail or "")
    assert "perform_merge_called" not in call_log
    assert verdict.verdict in VERDICT_EXIT_CODES


def test_known_waiter_verdicts_still_pass_through() -> None:
    """All known waiter failure verdicts still pass through unchanged."""
    known = {"ci_failed", "stale_head", "checks_missing", "timeout",
             "pr_closed", "pr_draft", "github_error"}
    for v in known:
        verdict = _merge(ci_verdict=v)
        assert verdict.verdict == v, f"verdict {v!r} should pass through"
        assert verdict.verdict in VERDICT_EXIT_CODES


# ── exit code mapping ────────────────────────────────────────────────────────


def test_exit_code_map_consistency() -> None:
    """Every verdict has a stable exit code; merged=0; all others non-zero."""
    assert VERDICT_EXIT_CODES["merged"] == 0

    # Waiter pass-through codes must match the waiter's map
    # (ci_pass is never returned by the merge engine — it proceeds to merged or
    # a guard failure — so its exit code is irrelevant here.)
    for key in ("ci_failed", "stale_head", "checks_missing", "timeout",
                "pr_closed", "pr_draft", "github_error"):
        assert VERDICT_EXIT_CODES[key] == WAITER_EXIT_CODES[key], (
            f"exit code mismatch for {key!r}"
        )

    # All merge-guard codes are distinct and non-zero
    merge_verdicts = {"merge_guard_review", "merge_guard_qa",
                      "merge_guard_mergeable", "merge_failed"}
    for v in merge_verdicts:
        assert v in VERDICT_EXIT_CODES, f"missing exit code for {v!r}"
        assert VERDICT_EXIT_CODES[v] != 0, f"{v!r} should be non-zero"
        assert isinstance(VERDICT_EXIT_CODES[v], int)

    # All codes are distinct (ci_pass may share 0 with merged — both
    # are success codes, and ci_pass is never returned by the merge engine)
    codes = list(VERDICT_EXIT_CODES.values())
    assert len(codes) == len(set(codes)), "exit codes must be distinct"


def test_exit_code_map_no_missing_verdicts() -> None:
    """Every engine-returnable verdict has an exit code.

    ci_pass is not returned by the merge engine (it either merges or fails
    a guard) so it does not need a distinct exit code.
    """
    engine_verdicts = {
        "merged", "ci_failed", "stale_head", "checks_missing",
        "timeout", "pr_closed", "pr_draft", "github_error",
        "merge_guard_review", "merge_guard_qa", "merge_guard_mergeable",
        "merge_failed",
    }
    for v in engine_verdicts:
        assert v in VERDICT_EXIT_CODES, (
            f"{v!r} missing from VERDICT_EXIT_CODES"
        )


# ── GuardedMergeVerdict field completeness ──────────────────────────────────


def test_guarded_merge_verdict_default_fields() -> None:
    """Non-merged verdict has merged_sha/merged_at as None."""
    verdict = _merge(fetch_review_verdict=lambda: "REJECT")
    assert verdict.merged_sha is None
    assert verdict.merged_at is None
    assert verdict.pr_number == 1
    assert verdict.pinned_head_sha == "a" * 40
    assert verdict.error_detail is None


def test_guarded_merge_verdict_error_detail() -> None:
    """Error verdicts carry error_detail."""

    def bad_merge(method: str) -> MergeResult:
        raise RuntimeError("branch protection")

    verdict = _merge(perform_merge=bad_merge)
    assert verdict.error_detail is not None
    assert "branch protection" in verdict.error_detail
