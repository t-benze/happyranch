"""Unit tests for PR CI adapters (runtime/daemon/pr_ci_adapters.py).

Tests the thin gh-backed fetchers and the adapter CLI entry points with
mocked subprocess calls — no live ``gh`` invocations.
"""
from __future__ import annotations

import io
import json
import runpy
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from runtime.daemon.pr_ci_adapters import (
    RealClock,
    ci_poll_main,
    gh_fetch_checks,
    gh_fetch_mergeable,
    gh_fetch_pr_state,
    gh_perform_merge,
    guarded_merge_main,
    main,
)
from runtime.daemon.pr_ci_merge import MergeableState, MergeResult, VERDICT_EXIT_CODES as MERGE_EXIT_CODES
from runtime.daemon.pr_ci_waiter import CheckState, PRState


# ═══════════════════════════════════════════════════════════════════════════════
# gh_fetch_pr_state
# ═══════════════════════════════════════════════════════════════════════════════


def test_gh_fetch_pr_state_open() -> None:
    """Open, non-draft PR → PRState(open=True, draft=False)."""
    gh_output = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        pr = gh_fetch_pr_state("owner/repo", 1)

    assert pr.head_sha == "a" * 40
    assert pr.open is True
    assert pr.draft is False


def test_gh_fetch_pr_state_closed() -> None:
    """Closed PR → PRState(open=False)."""
    gh_output = json.dumps({"state": "CLOSED", "headRefOid": "b" * 40, "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        pr = gh_fetch_pr_state("owner/repo", 2)

    assert pr.open is False


def test_gh_fetch_pr_state_draft() -> None:
    """Draft PR → PRState(draft=True)."""
    gh_output = json.dumps({"state": "OPEN", "headRefOid": "c" * 40, "isDraft": True})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        pr = gh_fetch_pr_state("owner/repo", 3)

    assert pr.draft is True
    assert pr.open is True


def test_gh_fetch_pr_state_raises_on_gh_failure() -> None:
    """gh pr view non-zero exit → CalledProcessError propagates."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
        with pytest.raises(subprocess.CalledProcessError):
            gh_fetch_pr_state("owner/repo", 1)


def test_gh_fetch_pr_state_merges_state() -> None:
    """gh pr view output → MERGED is not open."""
    gh_output = json.dumps({"state": "MERGED", "headRefOid": "d" * 40, "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        pr = gh_fetch_pr_state("owner/repo", 1)

    assert pr.open is False


# ═══════════════════════════════════════════════════════════════════════════════
# gh_fetch_checks
# ═══════════════════════════════════════════════════════════════════════════════


def test_gh_fetch_checks_check_runs() -> None:
    """Parse check-runs JSON into CheckState list."""
    cr_json = json.dumps([
        {"name": "Python CI", "status": "completed", "conclusion": "success"},
        {"name": "Web CI", "status": "completed", "conclusion": "failure"},
    ]).replace("[", "").replace("]", "")

    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            # Return one JSON line per check run (matches --jq output)
            lines = "\n".join([
                json.dumps({"name": "Python CI", "status": "completed", "conclusion": "success"}),
                json.dumps({"name": "Web CI", "status": "completed", "conclusion": "failure"}),
            ])
            return MagicMock(stdout=lines, stderr="")
        if "status" in str(cmd):
            return MagicMock(stdout="", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 2
    assert checks[0].name == "Python CI"
    assert checks[0].conclusion == "success"
    assert checks[1].name == "Web CI"
    assert checks[1].conclusion == "failure"


def test_gh_fetch_checks_commit_statuses() -> None:
    """Parse commit-statuses JSON into CheckState list."""
    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            return MagicMock(stdout="", stderr="")
        if "status" in str(cmd):
            lines = "\n".join([
                json.dumps({"name": "ci/circleci", "conclusion": "success"}),
                json.dumps({"name": "deploy/preview", "conclusion": "failure"}),
            ])
            return MagicMock(stdout=lines, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 2
    assert checks[0].name == "ci/circleci"
    assert checks[0].conclusion == "success"
    assert checks[1].name == "deploy/preview"
    assert checks[1].conclusion == "failure"


def test_gh_fetch_checks_combined() -> None:
    """Both check-runs and commit-statuses → combined list."""
    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            lines = "\n".join([
                json.dumps({"name": "gha-check", "status": "completed", "conclusion": "success"}),
            ])
            return MagicMock(stdout=lines, stderr="")
        if "status" in str(cmd):
            lines = "\n".join([
                json.dumps({"name": "ci/jenkins", "conclusion": "success"}),
            ])
            return MagicMock(stdout=lines, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 2
    names = {c.name for c in checks}
    assert names == {"gha-check", "ci/jenkins"}


def test_gh_fetch_checks_pending_status() -> None:
    """Pending commit status → CheckState with status='pending', conclusion=None."""
    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            return MagicMock(stdout="", stderr="")
        if "status" in str(cmd):
            lines = json.dumps({"name": "ci/waiting", "conclusion": "pending"})
            return MagicMock(stdout=lines + "\n", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 1
    assert checks[0].name == "ci/waiting"
    assert checks[0].status == "pending"
    assert checks[0].conclusion is None


def test_gh_fetch_checks_handles_check_runs_404() -> None:
    """Check-runs endpoint 404 → zero check runs, not an error."""
    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            raise subprocess.CalledProcessError(1, "gh", stderr="not found")
        if "status" in str(cmd):
            return MagicMock(stdout="", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert checks == []


def test_gh_fetch_checks_handles_status_404() -> None:
    """Status endpoint 404 → zero status checks, not an error."""
    def fake_run(cmd, **kwargs):
        if "check-runs" in str(cmd):
            return MagicMock(stdout="", stderr="")
        if "status" in str(cmd):
            raise subprocess.CalledProcessError(1, "gh", stderr="not found")
        raise RuntimeError(f"unexpected cmd: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        checks = gh_fetch_checks("owner/repo", "a" * 40)

    assert checks == []


# ═══════════════════════════════════════════════════════════════════════════════
# gh_fetch_mergeable
# ═══════════════════════════════════════════════════════════════════════════════


def test_gh_fetch_mergeable_clean() -> None:
    """mergeStateStatus=CLEAN → MergeableState(mergeable='CLEAN')."""
    gh_output = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        ms = gh_fetch_mergeable("owner/repo", 1)

    assert ms.mergeable == "CLEAN"
    assert ms.detail is None


def test_gh_fetch_mergeable_blocked() -> None:
    """mergeStateStatus=BLOCKED → MergeableState(mergeable='BLOCKED') with detail."""
    gh_output = json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "BLOCKED"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        ms = gh_fetch_mergeable("owner/repo", 1)

    assert ms.mergeable == "BLOCKED"
    assert ms.detail is not None
    assert "BLOCKED" in ms.detail


def test_gh_fetch_mergeable_unknown() -> None:
    """mergeStateStatus=UNKNOWN (no key) → MergeableState(mergeable='UNKNOWN')."""
    gh_output = json.dumps({"mergeable": "UNKNOWN"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=gh_output, stderr="")
        ms = gh_fetch_mergeable("owner/repo", 1)

    assert ms.mergeable == "UNKNOWN"


def test_gh_fetch_mergeable_raises_on_failure() -> None:
    """gh pr view failure → CalledProcessError propagates."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
        with pytest.raises(subprocess.CalledProcessError):
            gh_fetch_mergeable("owner/repo", 1)


# ═══════════════════════════════════════════════════════════════════════════════
# gh_perform_merge
# ═══════════════════════════════════════════════════════════════════════════════


def test_gh_perform_merge_squash() -> None:
    """gh pr merge --squash → MergeResult with merged_sha."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="abc123\n", stderr="")
        result = gh_perform_merge("owner/repo", 1, "squash")

    assert result.merged_sha == "abc123"
    assert result.merged_at is not None
    # Verify correct flag was passed
    call_args = mock_run.call_args[0][0]
    assert "--squash" in call_args


def test_gh_perform_merge_merge() -> None:
    """gh pr merge --merge → MergeResult."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="def456\n", stderr="")
        result = gh_perform_merge("owner/repo", 1, "merge")

    assert result.merged_sha == "def456"
    call_args = mock_run.call_args[0][0]
    assert "--merge" in call_args


def test_gh_perform_merge_rebase() -> None:
    """gh pr merge --rebase → MergeResult."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ghi789\n", stderr="")
        result = gh_perform_merge("owner/repo", 1, "rebase")

    assert result.merged_sha == "ghi789"
    call_args = mock_run.call_args[0][0]
    assert "--rebase" in call_args


def test_gh_perform_merge_raises_on_failure() -> None:
    """gh pr merge non-zero → CalledProcessError propagates."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="merge conflict")
        with pytest.raises(subprocess.CalledProcessError):
            gh_perform_merge("owner/repo", 1, "merge")


# ═══════════════════════════════════════════════════════════════════════════════
# CI-POLL adapter (ci_poll_main) — mock subprocess, test verdict mapping
# ═══════════════════════════════════════════════════════════════════════════════


def _ci_poll_gh_output(pr_state_output: str, checks_output: str) -> MagicMock:
    """Build a subprocess.run mock that returns *pr_state_output* for
    ``gh pr view`` and *checks_output* for ``gh api``."""

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr view" in cmd_str:
            return MagicMock(stdout=pr_state_output, stderr="")
        if "check-runs" in cmd_str or "/status" in cmd_str:
            return MagicMock(stdout=checks_output, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    return fake_run


def test_ci_poll_adapter_ci_pass() -> None:
    """All checks pass → exit code 0."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    checks_out = json.dumps({"name": "ci", "status": "completed", "conclusion": "success"})
    fake = _ci_poll_gh_output(pr_json, checks_out + "\n")

    with patch("subprocess.run", side_effect=fake):
        with patch("time.sleep"):  # suppress real sleeps
            exit_code = ci_poll_main([
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "ci",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    assert exit_code == 0


def test_ci_poll_adapter_ci_failed() -> None:
    """A check fails → exit code 1 (ci_failed)."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    checks_out = json.dumps({"name": "ci", "status": "completed", "conclusion": "failure"})
    fake = _ci_poll_gh_output(pr_json, checks_out + "\n")

    with patch("subprocess.run", side_effect=fake):
        with patch("time.sleep"):
            exit_code = ci_poll_main([
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "ci",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    assert exit_code == 1


def test_ci_poll_adapter_stale_head() -> None:
    """Head SHA changed → exit code 2 (stale_head)."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "b" * 40, "isDraft": False})
    fake = _ci_poll_gh_output(pr_json, "")

    with patch("subprocess.run", side_effect=fake):
        exit_code = ci_poll_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--expected-check", "ci",
            "--settle-seconds", "0", "--poll-interval", "1",
            "--timeout-seconds", "10",
        ])

    assert exit_code == 2


def test_ci_poll_adapter_pr_closed() -> None:
    """PR is closed → exit code 5 (pr_closed)."""
    pr_json = json.dumps({"state": "CLOSED", "headRefOid": "a" * 40, "isDraft": False})
    fake = _ci_poll_gh_output(pr_json, "")

    with patch("subprocess.run", side_effect=fake):
        exit_code = ci_poll_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--expected-check", "ci",
            "--settle-seconds", "0", "--poll-interval", "1",
            "--timeout-seconds", "10",
        ])

    assert exit_code == 5


def test_ci_poll_adapter_pr_draft() -> None:
    """PR is draft → exit code 6 (pr_draft)."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": True})
    fake = _ci_poll_gh_output(pr_json, "")

    with patch("subprocess.run", side_effect=fake):
        exit_code = ci_poll_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--expected-check", "ci",
            "--settle-seconds", "0", "--poll-interval", "1",
            "--timeout-seconds", "10",
        ])

    assert exit_code == 6


def test_ci_poll_adapter_github_error() -> None:
    """gh pr view failure → exit code 7 (github_error)."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="API error")
        exit_code = ci_poll_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--expected-check", "ci",
            "--settle-seconds", "0", "--poll-interval", "1",
            "--timeout-seconds", "10",
        ])

    assert exit_code == 7


def test_ci_poll_adapter_multiple_expected_checks() -> None:
    """Multiple expected checks, all pass → ci_pass."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr view" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "check-runs" in cmd_str:
            lines = "\n".join([
                json.dumps({"name": "lint", "status": "completed", "conclusion": "success"}),
                json.dumps({"name": "test", "status": "completed", "conclusion": "success"}),
            ])
            return MagicMock(stdout=lines, stderr="")
        if "/status" in cmd_str:
            return MagicMock(stdout="", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        with patch("time.sleep"):
            exit_code = ci_poll_main([
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "lint",
                "--expected-check", "test",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    assert exit_code == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Guarded-merge adapter (guarded_merge_main) — mock subprocess, test guard logic
# ═══════════════════════════════════════════════════════════════════════════════


def _gm_subprocess_mock(
    pr_state_output: str | None = None,
    mergeable_output: str | None = None,
    merge_sha: str = "m" * 40,
) -> MagicMock:
    """Build a subprocess.run mock for guarded-merge."""

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            return MagicMock(stdout=merge_sha + "\n", stderr="")
        if "pr view" in cmd_str and pr_state_output is not None:
            return MagicMock(stdout=pr_state_output, stderr="")
        if "pr view" in cmd_str and mergeable_output is not None:
            return MagicMock(stdout=mergeable_output, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    return fake_run


def test_guarded_merge_adapter_merged() -> None:
    """All guards pass → exit code 0 (merged), subprocess calls made."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    call_log = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        call_log.append(cmd_str)
        if "pr merge" in cmd_str:
            return MagicMock(stdout="m" * 40 + "\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 0
    # Verify gh pr merge was called
    assert any("pr merge" in c for c in call_log), "gh pr merge should have been called"


def test_guarded_merge_adapter_ci_failed_no_merge() -> None:
    """CI verdict is not ci_pass → exit code 1 (ci_failed), merge NOT called."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_failed",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 1  # ci_failed
    assert not merge_called, "gh pr merge must NOT be called when ci_verdict != ci_pass"


def test_guarded_merge_adapter_stale_head_no_merge() -> None:
    """Head SHA changed at merge time → exit code 2 (stale_head), merge NOT called."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "b" * 40, "isDraft": False})  # different SHA
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 2  # stale_head
    assert not merge_called, "gh pr merge must NOT be called on stale head"


def test_guarded_merge_adapter_review_not_approve_no_merge() -> None:
    """Review verdict != APPROVE → merge_guard_review, merge NOT called."""
    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "REQUEST_CHANGES",
            "--qa-verdict", "PASS",
        ])

    assert exit_code != 0
    assert exit_code == MERGE_EXIT_CODES["merge_guard_review"]
    assert not merge_called, "gh pr merge must NOT be called when review fails"


def test_guarded_merge_adapter_qa_not_pass_no_merge() -> None:
    """QA verdict != PASS → merge_guard_qa, merge NOT called."""
    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "FAIL",
        ])

    assert exit_code != 0
    assert exit_code == MERGE_EXIT_CODES["merge_guard_qa"]
    assert not merge_called


def test_guarded_merge_adapter_mergeability_blocked_no_merge() -> None:
    """Mergeability BLOCKED → merge_guard_mergeable, merge NOT called."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "BLOCKED"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == MERGE_EXIT_CODES["merge_guard_mergeable"]
    assert not merge_called


def test_guarded_merge_adapter_merge_failed() -> None:
    """gh pr merge raises → exit code 14 (merge_failed)."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            raise subprocess.CalledProcessError(1, "gh", stderr="branch protection rule")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == MERGE_EXIT_CODES["merge_failed"]


def test_guarded_merge_adapter_github_error_on_fetch_failure() -> None:
    """gh pr view raises → exit code 7 (github_error)."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="API error")
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    # github_error = 7
    assert exit_code == 7


def test_guarded_merge_adapter_pr_closed_no_merge() -> None:
    """PR closed at merge time → exit code 5 (pr_closed), merge NOT called."""
    pr_json = json.dumps({"state": "CLOSED", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 5  # pr_closed
    assert not merge_called


def test_guarded_merge_adapter_pr_draft_no_merge() -> None:
    """PR draft at merge time → exit code 6 (pr_draft), merge NOT called."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": True})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 6  # pr_draft
    assert not merge_called


def test_guarded_merge_adapter_verify_merge_method_passed() -> None:
    """Verify that --merge-method is passed to gh pr merge."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_calls = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_calls.append(cmd)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "rebase",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 0
    assert len(merge_calls) == 1
    assert "--rebase" in str(merge_calls[0])


# ═══════════════════════════════════════════════════════════════════════════════
# RealClock
# ═══════════════════════════════════════════════════════════════════════════════


def test_real_clock_monotonic() -> None:
    """RealClock.monotonic() returns a float that increases."""
    clock = RealClock()
    t1 = clock.monotonic()
    t2 = clock.monotonic()
    assert isinstance(t1, float)
    assert t2 >= t1


def test_real_clock_sleep() -> None:
    """RealClock.sleep() calls time.sleep."""
    with patch("time.sleep") as mock_sleep:
        clock = RealClock()
        clock.sleep(0.5)
        mock_sleep.assert_called_once_with(0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING 1 — main() dispatcher (dead entry point)
# ═══════════════════════════════════════════════════════════════════════════════


def test_main_dispatches_ci_poll() -> None:
    """main(['ci-poll', ...]) routes to ci_poll_main and returns its exit code."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    checks_out = json.dumps({"name": "ci", "status": "completed", "conclusion": "success"})
    fake = _ci_poll_gh_output(pr_json, checks_out + "\n")

    with patch("subprocess.run", side_effect=fake):
        with patch("time.sleep"):
            exit_code = main([
                "ci-poll",
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "ci",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    assert exit_code == 0


def test_main_dispatches_guarded_merge() -> None:
    """main(['guarded-merge', ...]) routes to guarded_merge_main and returns its exit code."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            return MagicMock(stdout="m" * 40 + "\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        exit_code = main([
            "guarded-merge",
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
            "--qa-verdict", "PASS",
        ])

    assert exit_code == 0


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING 2 — fail-closed merge guard (missing verdicts → refuse merge)
# ═══════════════════════════════════════════════════════════════════════════════


def test_guarded_merge_missing_review_verdict_is_guard_blocked() -> None:
    """Omitting --review-verdict must refuse the merge (guard-blocked), never proceed."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        # --review-verdict omitted; --qa-verdict PASS
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--qa-verdict", "PASS",
        ])

    # Must NOT merge; must return merge_guard_review (exit code 11)
    assert exit_code != 0, f"missing --review-verdict must not proceed, got exit={exit_code}"
    assert exit_code == MERGE_EXIT_CODES["merge_guard_review"], f"expected merge_guard_review (11), got {exit_code}"
    assert not merge_called, "gh pr merge must NOT be called when --review-verdict is missing"


def test_guarded_merge_missing_qa_verdict_is_guard_blocked() -> None:
    """Omitting --qa-verdict must refuse the merge (guard-blocked), never proceed."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    merge_called = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            merge_called.append(True)
            return MagicMock(stdout="x\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        # --qa-verdict omitted; --review-verdict APPROVE
        exit_code = guarded_merge_main([
            "--repo", "owner/repo", "--pr-number", "1",
            "--head-sha", "a" * 40,
            "--merge-method", "squash",
            "--ci-verdict", "ci_pass",
            "--review-verdict", "APPROVE",
        ])

    # Must NOT merge; must return merge_guard_qa (exit code 12)
    assert exit_code != 0, f"missing --qa-verdict must not proceed, got exit={exit_code}"
    assert exit_code == MERGE_EXIT_CODES["merge_guard_qa"], f"expected merge_guard_qa (12), got {exit_code}"
    assert not merge_called, "gh pr merge must NOT be called when --qa-verdict is missing"


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING 3 — error masking in gh_fetch_checks (non-404 errors → github_error)
# ═══════════════════════════════════════════════════════════════════════════════


def test_ci_poll_adapter_check_runs_auth_error_is_github_error() -> None:
    """Non-404 check-runs API error (e.g. auth) → github_error, NOT checks_missing."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr view" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "check-runs" in cmd_str:
            raise subprocess.CalledProcessError(22, "gh", stderr="Bad credentials")
        if "/status" in cmd_str:
            return MagicMock(stdout="", stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        with patch("time.sleep"):
            exit_code = ci_poll_main([
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "ci",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    # Must be github_error (7), NOT checks_missing (3) or timeout (4)
    assert exit_code == 7, f"auth error must yield github_error (7), got {exit_code}"


def test_ci_poll_adapter_status_api_auth_error_is_github_error() -> None:
    """Non-404 status API error (e.g. rate limit) → github_error, NOT checks_missing."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr view" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "check-runs" in cmd_str:
            # Check-runs succeeds but returns nothing
            return MagicMock(stdout="", stderr="")
        if "/status" in cmd_str:
            raise subprocess.CalledProcessError(1, "gh", stderr="API rate limit exceeded")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    with patch("subprocess.run", side_effect=fake_run):
        with patch("time.sleep"):
            exit_code = ci_poll_main([
                "--repo", "owner/repo", "--pr-number", "1",
                "--head-sha", "a" * 40,
                "--expected-check", "ci",
                "--settle-seconds", "0", "--poll-interval", "1",
                "--timeout-seconds", "10",
            ])

    # Must be github_error (7), NOT checks_missing (3) or timeout (4)
    assert exit_code == 7, f"rate-limit error must yield github_error (7), got {exit_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# Module entry-point tests — prove ``python -m`` __main__ guard works
# ═══════════════════════════════════════════════════════════════════════════════


def test_module_entrypoint_ci_poll_ci_pass() -> None:
    """``python -m runtime.daemon.pr_ci_adapters ci-poll ...`` executes
    the ``if __name__ == '__main__': raise SystemExit(main())`` guard
    and exits with the engine exit code."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    checks_out = json.dumps({"name": "ci", "status": "completed", "conclusion": "success"})
    fake = _ci_poll_gh_output(pr_json, checks_out + "\n")

    test_argv = [
        "pr_ci_adapters",
        "ci-poll",
        "--repo", "owner/repo", "--pr-number", "1",
        "--head-sha", "a" * 40,
        "--expected-check", "ci",
        "--settle-seconds", "0", "--poll-interval", "1",
        "--timeout-seconds", "10",
    ]

    stdout = io.StringIO()
    with patch("subprocess.run", side_effect=fake):
        with patch("time.sleep"):
            with patch.object(sys, "argv", test_argv):
                with patch("sys.stdout", stdout):
                    with pytest.raises(SystemExit) as exc_info:
                        runpy.run_module("runtime.daemon.pr_ci_adapters", run_name="__main__")

    assert exc_info.value.code == 0, f"expected exit 0, got {exc_info.value.code}"
    output = stdout.getvalue()
    verdict = json.loads(output)
    assert verdict.get("verdict") == "ci_pass", f"verdict JOON must include verdict=ci_pass, got {verdict}"


def test_module_entrypoint_ci_poll_github_error() -> None:
    """``python -m ... ci-poll ...`` with an invalid repo → exit 7 (github_error),
    and the structured verdict text is printed to stdout."""
    test_argv = [
        "pr_ci_adapters",
        "ci-poll",
        "--repo", "owner/nonexistent", "--pr-number", "1",
        "--head-sha", "a" * 40,
        "--expected-check", "ci",
        "--settle-seconds", "0", "--poll-interval", "1",
        "--timeout-seconds", "10",
    ]

    stdout = io.StringIO()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="Could not resolve to a Repository")
        with patch("time.sleep"):
            with patch.object(sys, "argv", test_argv):
                with patch("sys.stdout", stdout):
                    with pytest.raises(SystemExit) as exc_info:
                        runpy.run_module("runtime.daemon.pr_ci_adapters", run_name="__main__")

    assert exc_info.value.code == 7, f"expected exit 7 (github_error), got {exc_info.value.code}"
    output = stdout.getvalue()
    verdict = json.loads(output)
    assert verdict.get("verdict") == "github_error", f"verdict JOON must include verdict=github_error, got {verdict}"


def test_module_entrypoint_guarded_merge_merged() -> None:
    """``python -m ... guarded-merge ...`` with all guards green → exit 0 (merged),
    and the structured verdict text is printed to stdout."""
    pr_json = json.dumps({"state": "OPEN", "headRefOid": "a" * 40, "isDraft": False})
    mergeable_json = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "pr merge" in cmd_str:
            return MagicMock(stdout="m" * 40 + "\n", stderr="")
        if "pr view" in cmd_str and "isDraft" in cmd_str:
            return MagicMock(stdout=pr_json, stderr="")
        if "pr view" in cmd_str:
            return MagicMock(stdout=mergeable_json, stderr="")
        raise RuntimeError(f"unexpected cmd: {cmd_str}")

    test_argv = [
        "pr_ci_adapters",
        "guarded-merge",
        "--repo", "owner/repo", "--pr-number", "1",
        "--head-sha", "a" * 40,
        "--merge-method", "squash",
        "--ci-verdict", "ci_pass",
        "--review-verdict", "APPROVE",
        "--qa-verdict", "PASS",
    ]

    stdout = io.StringIO()
    with patch("subprocess.run", side_effect=fake_run):
        with patch.object(sys, "argv", test_argv):
            with patch("sys.stdout", stdout):
                with pytest.raises(SystemExit) as exc_info:
                    runpy.run_module("runtime.daemon.pr_ci_adapters", run_name="__main__")

    assert exc_info.value.code == 0, f"expected exit 0, got {exc_info.value.code}"
    output = stdout.getvalue()
    verdict = json.loads(output)
    assert verdict.get("verdict") == "merged", f"verdict JOON must include verdict=merged, got {verdict}"


def test_module_entrypoint_guarded_merge_missing_review_verdict() -> None:
    """``python -m ... guarded-merge ...`` without --review-verdict → exit 11
    (merge_guard_review), and the structured verdict text is printed."""
    test_argv = [
        "pr_ci_adapters",
        "guarded-merge",
        "--repo", "owner/repo", "--pr-number", "1",
        "--head-sha", "a" * 40,
        "--merge-method", "squash",
        "--ci-verdict", "ci_pass",
        "--qa-verdict", "PASS",
    ]

    stdout = io.StringIO()
    with patch.object(sys, "argv", test_argv):
        with patch("sys.stdout", stdout):
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_module("runtime.daemon.pr_ci_adapters", run_name="__main__")

    assert exc_info.value.code == 11, f"expected exit 11 (merge_guard_review), got {exc_info.value.code}"
    output = stdout.getvalue()
    verdict = json.loads(output)
    assert verdict.get("verdict") == "merge_guard_review", (
        f"verdict JOON must include verdict=merge_guard_review, got {verdict}"
    )


def test_module_entrypoint_guarded_merge_missing_qa_verdict() -> None:
    """``python -m ... guarded-merge ...`` without --qa-verdict → exit 12
    (merge_guard_qa), and the structured verdict text is printed."""
    test_argv = [
        "pr_ci_adapters",
        "guarded-merge",
        "--repo", "owner/repo", "--pr-number", "1",
        "--head-sha", "a" * 40,
        "--merge-method", "squash",
        "--ci-verdict", "ci_pass",
        "--review-verdict", "APPROVE",
    ]

    stdout = io.StringIO()
    with patch.object(sys, "argv", test_argv):
        with patch("sys.stdout", stdout):
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_module("runtime.daemon.pr_ci_adapters", run_name="__main__")

    assert exc_info.value.code == 12, f"expected exit 12 (merge_guard_qa), got {exc_info.value.code}"
    output = stdout.getvalue()
    verdict = json.loads(output)
    assert verdict.get("verdict") == "merge_guard_qa", (
        f"verdict JOON must include verdict=merge_guard_qa, got {verdict}"
    )
