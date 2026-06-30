"""Unit tests for the PR CI waiter engine (runtime/daemon/pr_ci_waiter.py).

Covers every terminal verdict path (spec §4.1 and §4.3) with injected fakes —
no live network calls, no real sleeps. Verdict set:
  ci_pass, ci_failed, stale_head, checks_missing, timeout,
  pr_closed, pr_draft, github_error

Exit-code mapping (stable for scripting):
  ci_pass=0, ci_failed=1, stale_head=2, checks_missing=3,
  timeout=4, pr_closed=5, pr_draft=6, github_error=7
"""
from __future__ import annotations

from typing import Callable

import pytest

from runtime.daemon.pr_ci_waiter import (
    CheckState,
    PRState,
    PRCIWaiterVerdict,
    VERDICT_EXIT_CODES,
    wait_for_ci,
)


# ── fakes ────────────────────────────────────────────────────────────────────


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


# ── helpers ──────────────────────────────────────────────────────────────────


def _waiter(
    *,
    pinned_head_sha: str = "a" * 40,
    expected_checks: list[str] | None = None,
    settle_seconds: float = 10.0,
    poll_interval_seconds: float = 1.0,
    timeout_seconds: float = 120.0,
    fetcher: Callable[[], tuple[PRState, list[CheckState]]] | None = None,
    clock: FakeClock | None = None,
) -> tuple[PRCIWaiterVerdict, FakeClock]:
    """Convenience wrapper: run wait_for_ci with sensible defaults and
    return the verdict + clock for assertions."""
    c = clock or FakeClock()

    if fetcher is None:
        fetcher = lambda: (
            _pr(pinned_head_sha),
            [
                _check(name, "completed", "success")
                for name in (expected_checks or ["ci"])
            ],
        )

    def fetch_pr() -> PRState:
        pr, _checks = fetcher()
        return pr

    def fetch_checks(sha: str) -> list[CheckState]:
        _pr_state, checks = fetcher()
        return checks

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha=pinned_head_sha,
        expected_checks=expected_checks or ["ci"],
        settle_seconds=settle_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=c,
    )
    return verdict, c


# ── ci_pass ──────────────────────────────────────────────────────────────────


def test_ci_pass_all_success() -> None:
    """All expected checks return success → ci_pass."""
    verdict, _ = _waiter()
    assert verdict.verdict == "ci_pass"
    assert VERDICT_EXIT_CODES["ci_pass"] == 0


def test_ci_pass_skipped_and_neutral_are_pass() -> None:
    """skipped and neutral conclusions count as pass."""
    fetcher = lambda: (
        _pr(),
        [_check("lint", "completed", "skipped"), _check("test", "completed", "neutral")],
    )
    verdict, _ = _waiter(expected_checks=["lint", "test"], fetcher=fetcher)
    assert verdict.verdict == "ci_pass"


def test_ci_pass_mixed_pass_statuses() -> None:
    """Combination of success, skipped, neutral all pass."""
    fetcher = lambda: (
        _pr(),
        [
            _check("lint", "completed", "success"),
            _check("test", "completed", "skipped"),
            _check("build", "completed", "neutral"),
        ],
    )
    verdict, _ = _waiter(expected_checks=["lint", "test", "build"], fetcher=fetcher)
    assert verdict.verdict == "ci_pass"


# ── ci_failed ────────────────────────────────────────────────────────────────


def test_ci_failed_failure_check() -> None:
    """A check with conclusion=failure → ci_failed."""
    fetcher = lambda: (
        _pr(),
        [_check("ci", "completed", "failure")],
    )
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "ci_failed"
    assert VERDICT_EXIT_CODES["ci_failed"] == 1


def test_ci_failed_cancelled() -> None:
    """cancelled counts as failed."""
    fetcher = lambda: (_pr(), [_check("ci", "completed", "cancelled")])
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "ci_failed"


def test_ci_failed_timed_out() -> None:
    """timed_out counts as failed."""
    fetcher = lambda: (_pr(), [_check("ci", "completed", "timed_out")])
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "ci_failed"


def test_ci_failed_action_required() -> None:
    """action_required counts as failed."""
    fetcher = lambda: (_pr(), [_check("ci", "completed", "action_required")])
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "ci_failed"


# ── pending → pass ───────────────────────────────────────────────────────────


def test_pending_to_pass_across_polls() -> None:
    """Non-terminal (queued/in_progress/pending) → later success across polls."""
    # Use an explicit iteration counter shared between fetch_pr and fetch_checks.
    iteration = 0

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        nonlocal iteration
        iteration += 1
        if iteration == 1:
            return [_check("ci", "queued")]
        elif iteration == 2:
            return [_check("ci", "in_progress")]
        else:
            return [_check("ci", "completed", "success")]

    clock = FakeClock()
    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "ci_pass"
    # Should have slept between non-terminal polls
    assert len(clock.sleeps) >= 2


def test_pending_status_variants() -> None:
    """queued, in_progress, and pending are all non-terminal — engine keeps
    polling until they complete."""
    for status in ("queued", "in_progress", "pending"):
        iteration = 0

        def fetch_pr() -> PRState:
            return _pr()

        def fetch_checks(sha: str) -> list[CheckState]:
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                return [_check("ci", status)]
            return [_check("ci", "completed", "success")]

        clock = FakeClock()
        verdict = wait_for_ci(
            repo="test-owner/test-repo",
            pr_number=1,
            pinned_head_sha="a" * 40,
            expected_checks=["ci"],
            settle_seconds=0.0,
            poll_interval_seconds=1.0,
            timeout_seconds=120.0,
            fetch_pr_state=fetch_pr,
            fetch_checks=fetch_checks,
            clock=clock,
        )
        assert verdict.verdict == "ci_pass", f"status={status} should not be terminal"
        # Must have slept at least once (non-terminal first poll)
        assert len(clock.sleeps) >= 1, f"status={status} should require at least one re-poll"


# ── settle window ────────────────────────────────────────────────────────────


def test_settle_window_absent_checks_not_pass() -> None:
    """During settle window, absent expected checks do NOT trigger ci_pass —
    the engine keeps polling."""
    iteration = 0

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        nonlocal iteration
        iteration += 1
        if iteration <= 2:
            return []
        return [_check("ci", "completed", "success")]

    clock = FakeClock()
    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=10.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "ci_pass"
    assert iteration == 3


def test_settle_window_checks_absent_pass_after_appear() -> None:
    """Checks appear during settle window → keep polling until pass."""
    iteration = 0

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        nonlocal iteration
        iteration += 1
        if iteration <= 3:
            return []
        return [_check("ci", "completed", "success")]

    clock = FakeClock()
    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=60.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "ci_pass"


# ── checks_missing ───────────────────────────────────────────────────────────


def test_checks_missing_after_settle() -> None:
    """Expected check never appears after settle window → checks_missing."""
    clock = FakeClock()

    def fetcher():
        # Advance clock past settle window
        clock.sleep(20.0)
        return _pr(), []  # no checks

    verdict, c = _waiter(
        settle_seconds=10.0,
        poll_interval_seconds=1.0,
        fetcher=fetcher,
        clock=clock,
    )
    assert verdict.verdict == "checks_missing"
    assert VERDICT_EXIT_CODES["checks_missing"] == 3


def test_checks_missing_one_of_many() -> None:
    """One expected check never appears while others pass → checks_missing."""
    clock = FakeClock()

    def fetcher():
        clock.sleep(20.0)
        return _pr(), [_check("lint", "completed", "success")]  # missing "test"

    verdict, _ = _waiter(
        expected_checks=["lint", "test"],
        settle_seconds=10.0,
        poll_interval_seconds=1.0,
        fetcher=fetcher,
        clock=clock,
    )
    assert verdict.verdict == "checks_missing"


# ── stale_head ───────────────────────────────────────────────────────────────


def test_stale_head_sha_changed() -> None:
    """PR head SHA differs from pinned → stale_head (terminal, no merge)."""
    fetcher = lambda: (_pr(sha="b" * 40), [])
    verdict, _ = _waiter(pinned_head_sha="a" * 40, fetcher=fetcher)
    assert verdict.verdict == "stale_head"
    assert verdict.observed_head_sha == "b" * 40
    assert VERDICT_EXIT_CODES["stale_head"] == 2


def test_stale_head_mid_wait() -> None:
    """Head SHA flips between a would-be pass and the next poll → stale_head.

    Poll 1: SHA matches, checks in_progress → continue polling.
    Poll 2: SHA changed → stale_head (terminal, SHA re-verify happens first).
    """
    iteration = 0

    def fetch_pr() -> PRState:
        nonlocal iteration
        iteration += 1
        if iteration == 1:
            return _pr("a" * 40)
        else:
            return _pr("b" * 40)

    def fetch_checks(sha: str) -> list[CheckState]:
        return [_check("ci", "in_progress")]

    clock = FakeClock()
    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    # SHA re-verify happens BEFORE reading check state on every iteration.
    # On poll 2 the SHA flipped → stale_head returned, checks not read.
    assert verdict.verdict == "stale_head"
    assert verdict.observed_head_sha == "b" * 40


def test_stale_head_before_pass_read() -> None:
    """Head SHA flips before checks could pass → stale_head."""
    iteration = 0

    def fetch_pr() -> PRState:
        nonlocal iteration
        # fetch_pr is called first each iteration; increment here
        iteration += 1
        if iteration == 1:
            return _pr("a" * 40)
        else:
            return _pr("b" * 40)

    def fetch_checks(sha: str) -> list[CheckState]:
        if sha == "a" * 40:
            return [_check("ci", "in_progress")]
        return []

    clock = FakeClock()
    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "stale_head"
    assert verdict.observed_head_sha == "b" * 40


# ── timeout ──────────────────────────────────────────────────────────────────


def test_timeout_deadline_exceeded_while_pending() -> None:
    """Total bounded wait exceeded while checks still pending → timeout."""
    clock = FakeClock()

    def fetcher():
        # Jump past timeout
        clock.sleep(130.0)
        return _pr(), [_check("ci", "in_progress")]

    verdict, c = _waiter(
        timeout_seconds=120.0,
        poll_interval_seconds=1.0,
        settle_seconds=0.0,
        fetcher=fetcher,
        clock=clock,
    )
    assert verdict.verdict == "timeout"
    assert verdict.elapsed_seconds is not None
    assert verdict.elapsed_seconds >= 120.0
    assert VERDICT_EXIT_CODES["timeout"] == 4


# ── pr_closed ──────────────────────────────────────────────────────────────


def test_pr_closed() -> None:
    """PR is closed → pr_closed (terminal)."""
    fetcher = lambda: (_pr(open=False), [])
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "pr_closed"
    assert VERDICT_EXIT_CODES["pr_closed"] == 5


# ── pr_draft ─────────────────────────────────────────────────────────────────


def test_pr_draft() -> None:
    """PR is in draft state → pr_draft (terminal)."""
    fetcher = lambda: (_pr(draft=True), [])
    verdict, _ = _waiter(fetcher=fetcher)
    assert verdict.verdict == "pr_draft"
    assert VERDICT_EXIT_CODES["pr_draft"] == 6


# ── github_error ─────────────────────────────────────────────────────────────


def test_github_error_fetch_pr_raises() -> None:
    """GitHub API error during fetch_pr_state → github_error."""

    def fetch_pr() -> PRState:
        raise RuntimeError("GitHub API 500")

    def fetch_checks(sha: str) -> list[CheckState]:
        return []

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=10.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=FakeClock(),
    )
    assert verdict.verdict == "github_error"
    assert "GitHub API 500" in (verdict.error_detail or "")
    assert VERDICT_EXIT_CODES["github_error"] == 7


def test_github_error_fetch_checks_raises() -> None:
    """GitHub API error during fetch_checks → github_error."""

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> RuntimeError:
        raise RuntimeError("rate limit")

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=10.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=FakeClock(),
    )
    assert verdict.verdict == "github_error"
    assert "rate limit" in (verdict.error_detail or "")


# ── exit code mapping ────────────────────────────────────────────────────────


def test_exit_code_map_covers_all_verdicts() -> None:
    """Every terminal verdict has a distinct non-zero code; ci_pass=0."""
    assert VERDICT_EXIT_CODES["ci_pass"] == 0
    assert VERDICT_EXIT_CODES["ci_failed"] == 1
    assert VERDICT_EXIT_CODES["stale_head"] == 2
    assert VERDICT_EXIT_CODES["checks_missing"] == 3
    assert VERDICT_EXIT_CODES["timeout"] == 4
    assert VERDICT_EXIT_CODES["pr_closed"] == 5
    assert VERDICT_EXIT_CODES["pr_draft"] == 6
    assert VERDICT_EXIT_CODES["github_error"] == 7

    # All codes are integers
    assert all(isinstance(v, int) for v in VERDICT_EXIT_CODES.values())
    # ci_pass is the only zero code
    non_zero = {k: v for k, v in VERDICT_EXIT_CODES.items() if v != 0}
    assert len(non_zero) == len(VERDICT_EXIT_CODES) - 1
    # All non-zero codes are distinct
    codes = list(VERDICT_EXIT_CODES.values())
    assert len(codes) == len(set(codes))


# ── verdict detail fields ────────────────────────────────────────────────────


def test_verdict_carries_observed_sha() -> None:
    """Verdict carries the observed head SHA for diagnostics."""
    fetcher = lambda: (_pr("c" * 40), [_check("ci", "completed", "failure")])
    verdict, _ = _waiter(pinned_head_sha="a" * 40, fetcher=fetcher)
    assert verdict.observed_head_sha == "c" * 40


def test_verdict_carries_checks() -> None:
    """Verdict carries per-check states for diagnostics."""
    checks = [_check("lint", "completed", "success"), _check("test", "completed", "failure")]
    fetcher = lambda: (_pr(), checks)
    verdict, _ = _waiter(expected_checks=["lint", "test"], fetcher=fetcher)
    assert verdict.checks is not None
    assert len(verdict.checks) == 2


def test_verdict_carries_elapsed() -> None:
    """Verdict carries elapsed seconds."""
    clock = FakeClock(start=100.0)

    def fetcher():
        clock.sleep(5.0)
        return _pr(), [_check("ci", "completed", "success")]

    verdict, _ = _waiter(settle_seconds=0.0, fetcher=fetcher, clock=clock)
    assert verdict.elapsed_seconds is not None
    assert verdict.elapsed_seconds >= 5.0


# ── poll-loop order (spec §4.1) ──────────────────────────────────────────────


def test_sha_checked_before_checks() -> None:
    """SHA re-verification happens BEFORE reading check state on every iteration.
    Even if checks would pass, a stale head is terminal."""
    call_log = []

    def fetch_pr() -> PRState:
        call_log.append("pr")
        # Return stale on first call
        if len(call_log) <= 2:
            return _pr("b" * 40)
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        call_log.append("checks")
        return [_check("ci", "completed", "success")]

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=FakeClock(),
    )
    assert verdict.verdict == "stale_head"
    # fetch_checks should NOT have been called — SHA failed first
    assert "checks" not in call_log
    assert call_log == ["pr"]


def test_pr_closed_checked_before_checks() -> None:
    """PR closed is checked before reading check state."""
    call_log = []

    def fetch_pr() -> PRState:
        call_log.append("pr")
        return _pr(open=False)

    def fetch_checks(sha: str) -> list[CheckState]:
        call_log.append("checks")
        return []

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=FakeClock(),
    )
    assert verdict.verdict == "pr_closed"
    assert "checks" not in call_log


def test_pr_draft_checked_before_checks() -> None:
    """PR draft is checked before reading check state."""
    call_log = []

    def fetch_pr() -> PRState:
        call_log.append("pr")
        return _pr(draft=True)

    def fetch_checks(sha: str) -> list[CheckState]:
        call_log.append("checks")
        return []

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=FakeClock(),
    )
    assert verdict.verdict == "pr_draft"
    assert "checks" not in call_log


# ── edge cases ───────────────────────────────────────────────────────────────


def test_empty_expected_checks_immediate_pass() -> None:
    """With no expected checks, ci_pass is immediate (vacuously true)."""
    verdict, _ = _waiter(expected_checks=[], settle_seconds=0.0)
    assert verdict.verdict == "ci_pass"


def test_extra_checks_ignored() -> None:
    """Checks not in expected_checks are ignored (pass/fail only on expected)."""
    fetcher = lambda: (
        _pr(),
        [
            _check("ci", "completed", "success"),
            _check("extra-lint", "completed", "failure"),  # not in expected, ignored
        ],
    )
    verdict, _ = _waiter(expected_checks=["ci"], fetcher=fetcher)
    assert verdict.verdict == "ci_pass"


def test_poll_interval_sleeps_between_iterations() -> None:
    """Engine sleeps poll_interval_seconds between non-terminal iterations."""
    iteration = 0
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        nonlocal iteration
        iteration += 1
        if iteration <= 2:
            return [_check("ci", "in_progress")]
        return [_check("ci", "completed", "success")]

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=5.0,
        timeout_seconds=120.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "ci_pass"
    # Two waits: after poll 1 (sleep 5s), after poll 2 (sleep 5s)
    assert len(clock.sleeps) >= 2
    assert clock.sleeps[0] == pytest.approx(5.0)
    assert clock.sleeps[1] == pytest.approx(5.0)


def test_timeout_checked_every_iteration() -> None:
    """Timeout is checked every iteration, not just at the start."""
    start = 200.0
    clock = FakeClock(start=start)

    def fetcher():
        # First poll: advance just a bit
        if clock.monotonic() < start + 10.0:
            clock.sleep(5.0)
            return _pr(), [_check("ci", "in_progress")]
        # Second poll: jump past timeout
        clock.sleep(200.0)
        return _pr(), [_check("ci", "in_progress")]

    verdict, c = _waiter(
        timeout_seconds=100.0,
        settle_seconds=0.0,
        poll_interval_seconds=1.0,
        fetcher=fetcher,
        clock=clock,
    )
    assert verdict.verdict == "timeout"


# ── timeout dominates settle window (regression) ────────────────────────────


def test_timeout_dominates_settle_window_absent_checks() -> None:
    """Regression: settle window must NOT bypass the total-timeout ceiling.

    Reviewer repro (d16f95d): settle_seconds=120, timeout_seconds=30,
    absent expected checks → was returning checks_missing at elapsed ~120.
    Fixed: timeout ceiling dominates — must return timeout (exit code 4)
    once timeout_seconds is reached, not checks_missing."""
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        # Checks never appear — absent forever
        return []

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=120.0,
        poll_interval_seconds=5.0,
        timeout_seconds=30.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    # Must return timeout, NOT checks_missing
    assert verdict.verdict == "timeout", (
        f"expected timeout (exit 4) but got {verdict.verdict} "
        f"at elapsed={verdict.elapsed_seconds:.1f}s"
    )
    assert VERDICT_EXIT_CODES["timeout"] == 4
    # Elapsed must not exceed timeout_seconds (precision within one poll interval)
    assert verdict.elapsed_seconds is not None
    assert verdict.elapsed_seconds <= 30.0, (
        f"elapsed {verdict.elapsed_seconds:.1f}s should not exceed timeout 30s"
    )


def test_settle_sleep_capped_to_remaining_deadlines() -> None:
    """Each settle-window sleep is capped to min(remaining_settle,
    remaining_timeout, poll_interval). This prevents overshooting
    the timeout by a full poll interval."""
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        return []  # never appear

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=120.0,
        poll_interval_seconds=8.0,
        timeout_seconds=30.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "timeout"
    # Each sleep must not exceed the remaining timeout or remaining settle
    cumulative = 0.0
    for s in clock.sleeps:
        assert s <= 8.0, f"sleep {s}s should respect poll_interval cap"
        remaining_timeout = 30.0 - cumulative
        assert s <= remaining_timeout, (
            f"sleep {s}s at cumulative={cumulative:.1f}s exceeds "
            f"remaining timeout {remaining_timeout:.1f}s"
        )
        cumulative += s
    # Total elapsed must be ≤ timeout_seconds
    assert verdict.elapsed_seconds is not None
    assert verdict.elapsed_seconds <= 30.0


def test_non_terminal_sleep_capped_to_remaining_timeout() -> None:
    """When checks are pending (not absent), the poll-interval sleep
    is capped to the remaining timeout so the engine does not overshoot."""
    clock = FakeClock()

    def fetch_pr() -> PRState:
        return _pr()

    def fetch_checks(sha: str) -> list[CheckState]:
        return [_check("ci", "in_progress")]

    verdict = wait_for_ci(
        repo="test-owner/test-repo",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=["ci"],
        settle_seconds=0.0,
        poll_interval_seconds=15.0,
        timeout_seconds=10.0,
        fetch_pr_state=fetch_pr,
        fetch_checks=fetch_checks,
        clock=clock,
    )
    assert verdict.verdict == "timeout"
    # The engine must not oversleep: with poll_interval=15, timeout=10,
    # the first (and only) sleep must be capped to 10, not 15.
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] <= 10.0
    # Elapsed must be exactly at the timeout boundary
    assert verdict.elapsed_seconds is not None
    assert verdict.elapsed_seconds == pytest.approx(10.0)
