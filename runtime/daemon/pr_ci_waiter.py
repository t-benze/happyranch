"""PR CI waiter engine — a pure, unit-testable verdict state machine.

Given a GitHub PR (repo, number, pinned head SHA, expected checks) and
configurable time bounds (settle window, poll interval, total timeout), the
engine polls PR state + check runs / commit statuses via injected callables
and returns a STRUCTURED terminal verdict.

DESIGN: pure engine — no network calls, no `gh` CLI invocation, no real
sleeps inside the implementation.  The GitHub-query layer AND the clock/sleep
are injected so every verdict path is unit-testable with fakes (spec §7
"Traps").

VERDICT SET (spec §4.3):
  ci_pass, ci_failed, stale_head, checks_missing, timeout,
  pr_closed, pr_draft, github_error

The guarded-merge verdicts (merged, merge_guard_review, merge_guard_qa,
merge_guard_mergeable, merge_failed) are OUT OF SCOPE for this module
(PR #3 in the breakdown).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# ── data types ───────────────────────────────────────────────────────────────


@dataclass
class CheckState:
    """A single check-run or commit-status for the pinned SHA."""

    name: str
    status: str  # queued | in_progress | pending | completed
    conclusion: str | None = None
    # ^ success | skipped | neutral | failure | cancelled | timed_out |
    #   action_required | None (when status != completed)


@dataclass
class PRState:
    """PR metadata fetched each poll iteration."""

    head_sha: str
    open: bool
    draft: bool


@dataclass
class PRCIWaiterVerdict:
    """Structured terminal verdict from the waiter engine.

    Carries diagnostic detail so the task owner can decide revise / fail /
    escalate on resume.
    """

    verdict: str
    observed_head_sha: str | None = None
    checks: list[CheckState] | None = None
    elapsed_seconds: float | None = None
    error_detail: str | None = None


# ── verdict → exit-code map ──────────────────────────────────────────────────

# Stable, distinct non-zero codes for scripting.  ci_pass is the only
# zero-code verdict.
VERDICT_EXIT_CODES: dict[str, int] = {
    "ci_pass": 0,
    "ci_failed": 1,
    "stale_head": 2,
    "checks_missing": 3,
    "timeout": 4,
    "pr_closed": 5,
    "pr_draft": 6,
    "github_error": 7,
}

# ── internal helpers ─────────────────────────────────────────────────────────

_NON_TERMINAL_STATUSES: set[str] = {"queued", "in_progress", "pending"}
_PASSED_CONCLUSIONS: set[str] = {"success", "skipped", "neutral"}
_FAILED_CONCLUSIONS: set[str] = {"failure", "cancelled", "timed_out", "action_required"}


def _classify_check(check: CheckState) -> str | None:
    """Classify a check as 'passed', 'failed', or None (non-terminal)."""
    if check.status in _NON_TERMINAL_STATUSES:
        return None
    if check.conclusion in _PASSED_CONCLUSIONS:
        return "passed"
    if check.conclusion in _FAILED_CONCLUSIONS:
        return "failed"
    # Unknown conclusion — treat as non-terminal (keep polling)
    return None


# ── clock protocol ───────────────────────────────────────────────────────────


class _Clock:
    """Minimal clock interface for injection.

    Any object with monotonic() and sleep(seconds) satisfies this protocol.
    """

    ...


# ── engine ───────────────────────────────────────────────────────────────────


def wait_for_ci(
    *,
    repo: str,
    pr_number: int,
    pinned_head_sha: str,
    expected_checks: list[str],
    settle_seconds: float,
    poll_interval_seconds: float,
    timeout_seconds: float,
    fetch_pr_state: Callable[[], PRState],
    fetch_checks: Callable[[str], list[CheckState]],
    clock: object,
) -> PRCIWaiterVerdict:
    """Poll GitHub until CI reaches a terminal verdict for *pinned_head_sha*.

    Poll-loop order per spec §4.1:
      1. Fetch PR state.
      2. Re-verify head SHA FIRST — if != pinned_head_sha → stale_head (terminal).
      3. If PR closed → pr_closed (terminal).
      4. If PR draft → pr_draft (terminal).
      5. Collect check runs + commit statuses for pinned SHA.
      6. Settle window: if expected checks absent and elapsed < settle_seconds,
         keep polling — do NOT treat absence as pass.
      7. After settle: if any expected check never appeared → checks_missing.
      8. Classify each check.
      9. All expected checks passed → ci_pass (terminal, merge-eligible).
      10. Any expected check failed → ci_failed (terminal).
      11. Elapsed >= timeout_seconds while still pending → timeout (terminal).
      12. GitHub-query layer raises an unrecoverable error → github_error
          (terminal, carries error detail).

    Parameters
    ----------
    repo: ``owner/repo`` — informational, for error messages.
    pr_number: GitHub PR number.
    pinned_head_sha: full 40-char SHA the PR pointed to when the waiter was
        launched.  The SHA is re-verified on EVERY iteration; if the PR head
        advances the verdict is ``stale_head``.
    expected_checks: list of check-run / commit-status context names that
        must ALL pass for CI to be considered green.
    settle_seconds: how long to wait for expected checks to *appear* before
        treating absence as ``checks_missing``.
    poll_interval_seconds: seconds between polls (injected via clock.sleep).
    timeout_seconds: total bounded wait ceiling.
    fetch_pr_state: callable returning the current PR state (head SHA,
        open/closed, draft).  Raise an exception on unrecoverable GitHub
        errors.
    fetch_checks: callable taking a SHA and returning check-runs / commit
        statuses for that SHA.  Raise an exception on unrecoverable GitHub
        errors.
    clock: object with ``monotonic() -> float`` and ``sleep(seconds)``.
        The implementation, not the test, does the sleeping.
    """
    start = clock.monotonic()  # type: ignore[union-attr]

    def _now() -> float:
        return clock.monotonic()  # type: ignore[union-attr]

    def _elapsed() -> float:
        return _now() - start

    def _verdict(
        name: str,
        observed_head_sha: str | None = None,
        checks: list[CheckState] | None = None,
        error_detail: str | None = None,
    ) -> PRCIWaiterVerdict:
        return PRCIWaiterVerdict(
            verdict=name,
            observed_head_sha=observed_head_sha,
            checks=checks,
            elapsed_seconds=_elapsed(),
            error_detail=error_detail,
        )

    # Expected checks as a set for fast lookup
    expected: set[str] = set(expected_checks)

    while True:
        # ── fetch PR state (with error guard) ──
        try:
            pr = fetch_pr_state()
        except Exception as exc:
            return _verdict("github_error", error_detail=str(exc))

        # ── 2. SHA re-verify FIRST (before reading checks) ──
        if pr.head_sha != pinned_head_sha:
            return _verdict("stale_head", observed_head_sha=pr.head_sha)

        # ── 3. PR closed ──
        if not pr.open:
            return _verdict("pr_closed")

        # ── 4. PR draft ──
        if pr.draft:
            return _verdict("pr_draft")

        # ── 5. Collect checks (with error guard) ──
        try:
            all_checks = fetch_checks(pinned_head_sha)
        except Exception as exc:
            return _verdict("github_error", error_detail=str(exc))

        # Map checks by name for classification
        checks_by_name: dict[str, CheckState] = {
            c.name: c for c in all_checks
        }

        # ── 6. Settle window — absent expected checks → keep polling ──
        missing_expected = expected - set(checks_by_name.keys())
        if missing_expected and _elapsed() < settle_seconds:
            # Keep polling — checks may not have materialized yet
            _sleep(clock, poll_interval_seconds)
            continue

        # ── 7. After settle: expected checks never appeared ──
        if missing_expected:
            return _verdict("checks_missing", checks=all_checks)

        # ── 8. Classify expected checks ──
        passed: set[str] = set()
        failed: set[str] = set()
        pending: set[str] = set()

        for name in expected:
            check = checks_by_name[name]
            classification = _classify_check(check)
            if classification == "passed":
                passed.add(name)
            elif classification == "failed":
                failed.add(name)
            else:
                pending.add(name)

        # ── 10. Any expected check failed → ci_failed ──
        if failed:
            return _verdict("ci_failed", checks=all_checks)

        # ── 9. All expected checks passed → ci_pass ──
        if not pending:
            return _verdict("ci_pass", checks=all_checks)

        # ── 11. Timeout check BEFORE sleeping ──
        if _elapsed() >= timeout_seconds:
            return _verdict("timeout", checks=all_checks)

        # ── non-terminal — sleep, then poll again ──
        _sleep(clock, poll_interval_seconds)


def _sleep(clock: object, seconds: float) -> None:
    """Sleep using the injected clock."""
    clock.sleep(seconds)  # type: ignore[union-attr]
