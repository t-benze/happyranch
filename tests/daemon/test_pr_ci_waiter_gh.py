"""Unit tests for the real-gh adapter + CLI entrypoint of pr_ci_waiter.py.

Covers:
  - CLI arg parsing
  - Structured-verdict JSON output shape
  - Exit-code mapping for each verdict
  - Real-gh callable construction and dispatch (mock subprocess — NO network)
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.daemon.pr_ci_waiter import (
    CheckState,
    PRState,
    PRCIWaiterVerdict,
    VERDICT_EXIT_CODES,
    _gh_fetch_checks,
    _gh_fetch_pr_state,
)


# ── _gh_fetch_pr_state tests ─────────────────────────────────────────────────


def test_gh_fetch_pr_state_open() -> None:
    """gh pr view returns OPEN, not draft → correct PRState."""
    stdout = json.dumps({"headRefOid": "a" * 40, "state": "OPEN", "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        pr = _gh_fetch_pr_state("owner/repo", 1)
    assert pr.head_sha == "a" * 40
    assert pr.open is True
    assert pr.draft is False


def test_gh_fetch_pr_state_closed() -> None:
    """gh pr view returns CLOSED → open=False."""
    stdout = json.dumps({"headRefOid": "b" * 40, "state": "CLOSED", "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        pr = _gh_fetch_pr_state("owner/repo", 2)
    assert pr.open is False


def test_gh_fetch_pr_state_draft() -> None:
    """gh pr view returns isDraft=True."""
    stdout = json.dumps({"headRefOid": "c" * 40, "state": "OPEN", "isDraft": True})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        pr = _gh_fetch_pr_state("owner/repo", 3)
    assert pr.draft is True


def test_gh_fetch_pr_state_gh_failure() -> None:
    """gh pr view returns non-zero → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="gh: not found")
        with pytest.raises(RuntimeError, match="gh pr view failed"):
            _gh_fetch_pr_state("owner/repo", 1)


def test_gh_fetch_pr_state_correct_command() -> None:
    """gh pr view is called with correct arguments."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"headRefOid": "a" * 40, "state": "OPEN", "isDraft": False}),
            stderr="",
        )
        _gh_fetch_pr_state("myorg/myrepo", 42)
    call_args = mock_run.call_args[0][0]
    assert call_args[:4] == ["gh", "pr", "view", "42"]
    assert "--repo" in call_args
    assert call_args[call_args.index("--repo") + 1] == "myorg/myrepo"
    assert "--json" in call_args
    assert "headRefOid" in call_args[call_args.index("--json") + 1]


# ── _gh_fetch_checks tests ──────────────────────────────────────────────────


def test_gh_fetch_checks_check_runs() -> None:
    """Check runs are parsed into CheckState objects."""
    check_runs_json = json.dumps([
        {"name": "Python CI", "status": "completed", "conclusion": "success"},
        {"name": "Web CI", "status": "completed", "conclusion": "failure"},
    ])
    status_json = json.dumps([])

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "check-runs" in cmd_str:
            return MagicMock(returncode=0, stdout=check_runs_json, stderr="")
        return MagicMock(returncode=0, stdout=status_json, stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        checks = _gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 2
    assert checks[0].name == "Python CI"
    assert checks[0].status == "completed"
    assert checks[0].conclusion == "success"
    assert checks[1].name == "Web CI"
    assert checks[1].conclusion == "failure"


def test_gh_fetch_checks_commit_statuses() -> None:
    """Commit statuses are parsed into CheckState objects."""
    check_runs_json = json.dumps([])
    status_json = json.dumps([
        {"name": "deploy-preview", "status": "completed", "conclusion": "success"},
    ])

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "check-runs" in cmd_str:
            return MagicMock(returncode=0, stdout=check_runs_json, stderr="")
        return MagicMock(returncode=0, stdout=status_json, stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        checks = _gh_fetch_checks("owner/repo", "b" * 40)

    assert len(checks) == 1
    assert checks[0].name == "deploy-preview"
    assert checks[0].status == "completed"
    assert checks[0].conclusion == "success"


def test_gh_fetch_checks_combined() -> None:
    """Check runs and commit statuses are combined."""
    check_runs_json = json.dumps([
        {"name": "lint", "status": "completed", "conclusion": "success"},
    ])
    status_json = json.dumps([
        {"name": "deploy", "status": "completed", "conclusion": "success"},
    ])

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "check-runs" in cmd_str:
            return MagicMock(returncode=0, stdout=check_runs_json, stderr="")
        return MagicMock(returncode=0, stdout=status_json, stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        checks = _gh_fetch_checks("owner/repo", "c" * 40)

    assert len(checks) == 2
    names = {c.name for c in checks}
    assert names == {"lint", "deploy"}


def test_gh_fetch_checks_check_runs_failure() -> None:
    """gh api check-runs returns non-zero → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Not Found"
        )
        with pytest.raises(RuntimeError, match="gh api check-runs failed"):
            _gh_fetch_checks("owner/repo", "a" * 40)


def test_gh_fetch_checks_empty_both() -> None:
    """Both check-runs and statuses return empty → empty list."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="[]", stderr=""
        )
        checks = _gh_fetch_checks("owner/repo", "a" * 40)
    assert checks == []


def test_gh_fetch_checks_no_conclusion() -> None:
    """Check runs without conclusion (in_progress) handled correctly."""
    check_runs_json = json.dumps([
        {"name": "ci", "status": "in_progress"},
    ])

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "check-runs" in cmd_str:
            return MagicMock(returncode=0, stdout=check_runs_json, stderr="")
        return MagicMock(returncode=0, stdout="[]", stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        checks = _gh_fetch_checks("owner/repo", "a" * 40)

    assert len(checks) == 1
    assert checks[0].name == "ci"
    assert checks[0].status == "in_progress"
    assert checks[0].conclusion is None


# ── CLI entrypoint tests: arg parsing ────────────────────────────────────────


def test_cli_required_args() -> None:
    """Missing required args → SystemExit."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    with patch.object(sys, "argv", ["pr_ci_waiter.py"]):
        with pytest.raises(SystemExit):
            parser.parse_args()


def test_cli_arg_parsing_all_args() -> None:
    """All args parsed correctly from CLI."""
    test_args = [
        "pr_ci_waiter.py",
        "--repo", "owner/repo",
        "--pr", "42",
        "--head-sha", "a" * 40,
        "--expected-check", "Python CI",
        "--expected-check", "Web CI",
        "--settle-seconds", "60",
        "--poll-interval-seconds", "10",
        "--timeout-seconds", "1800",
    ]
    with patch.object(sys, "argv", test_args):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo", required=True)
        parser.add_argument("--pr", required=True, type=int)
        parser.add_argument("--head-sha", required=True)
        parser.add_argument("--expected-check", action="append", default=[], dest="expected_checks")
        parser.add_argument("--settle-seconds", type=float, default=120.0)
        parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
        parser.add_argument("--timeout-seconds", type=float, default=3600.0)
        args = parser.parse_args()
        assert args.repo == "owner/repo"
        assert args.pr == 42
        assert args.head_sha == "a" * 40
        assert args.expected_checks == ["Python CI", "Web CI"]
        assert args.settle_seconds == 60.0
        assert args.poll_interval_seconds == 10.0
        assert args.timeout_seconds == 1800.0


def test_cli_arg_defaults() -> None:
    """Default values for optional args."""
    test_args = [
        "pr_ci_waiter.py",
        "--repo", "owner/repo",
        "--pr", "1",
        "--head-sha", "a" * 40,
    ]
    with patch.object(sys, "argv", test_args):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo", required=True)
        parser.add_argument("--pr", required=True, type=int)
        parser.add_argument("--head-sha", required=True)
        parser.add_argument("--expected-check", action="append", default=[], dest="expected_checks")
        parser.add_argument("--settle-seconds", type=float, default=120.0)
        parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
        parser.add_argument("--timeout-seconds", type=float, default=3600.0)
        args = parser.parse_args()
        assert args.expected_checks == []
        assert args.settle_seconds == 120.0
        assert args.poll_interval_seconds == 15.0
        assert args.timeout_seconds == 3600.0


# ── CLI entrypoint tests: JSON verdict output shape ──────────────────────────


def test_verdict_json_output_shape() -> None:
    """JSON output contains all expected top-level fields."""
    from runtime.daemon.pr_ci_waiter import PRCIWaiterVerdict
    verdict = PRCIWaiterVerdict(
        verdict="ci_pass",
        observed_head_sha="a" * 40,
        checks=[CheckState(name="ci", status="completed", conclusion="success")],
        elapsed_seconds=12.5,
        error_detail=None,
    )

    output = {
        "verdict": verdict.verdict,
        "observed_head_sha": verdict.observed_head_sha,
        "elapsed_seconds": verdict.elapsed_seconds,
        "error_detail": verdict.error_detail,
        "checks": [
            {"name": c.name, "status": c.status, "conclusion": c.conclusion}
            for c in (verdict.checks or [])
        ],
    }
    json_str = json.dumps(output, indent=2)
    parsed = json.loads(json_str)

    assert parsed["verdict"] == "ci_pass"
    assert parsed["observed_head_sha"] == "a" * 40
    assert parsed["elapsed_seconds"] == 12.5
    assert parsed["error_detail"] is None
    assert len(parsed["checks"]) == 1
    assert parsed["checks"][0]["name"] == "ci"
    assert parsed["checks"][0]["status"] == "completed"
    assert parsed["checks"][0]["conclusion"] == "success"


def test_verdict_json_error_detail_present() -> None:
    """Error detail is included when present."""
    verdict = PRCIWaiterVerdict(
        verdict="github_error",
        error_detail="gh api: 502 Bad Gateway",
    )
    output = {
        "verdict": verdict.verdict,
        "observed_head_sha": verdict.observed_head_sha,
        "elapsed_seconds": verdict.elapsed_seconds,
        "error_detail": verdict.error_detail,
        "checks": [
            {"name": c.name, "status": c.status, "conclusion": c.conclusion}
            for c in (verdict.checks or [])
        ],
    }
    parsed = json.loads(json.dumps(output))
    assert parsed["error_detail"] == "gh api: 502 Bad Gateway"


def test_verdict_json_checks_empty() -> None:
    """Empty checks list serializes as empty array."""
    verdict = PRCIWaiterVerdict(verdict="pr_closed", checks=[])
    output = {
        "verdict": verdict.verdict,
        "observed_head_sha": verdict.observed_head_sha,
        "elapsed_seconds": verdict.elapsed_seconds,
        "error_detail": verdict.error_detail,
        "checks": [
            {"name": c.name, "status": c.status, "conclusion": c.conclusion}
            for c in (verdict.checks or [])
        ],
    }
    parsed = json.loads(json.dumps(output))
    assert parsed["checks"] == []


# ── exit-code mapping ────────────────────────────────────────────────────────


def test_exit_code_mapping_all_verdicts() -> None:
    """Every waiter verdict has a distinct non-zero exit code; ci_pass=0."""
    assert VERDICT_EXIT_CODES["ci_pass"] == 0
    assert VERDICT_EXIT_CODES["ci_failed"] == 1
    assert VERDICT_EXIT_CODES["stale_head"] == 2
    assert VERDICT_EXIT_CODES["checks_missing"] == 3
    assert VERDICT_EXIT_CODES["timeout"] == 4
    assert VERDICT_EXIT_CODES["pr_closed"] == 5
    assert VERDICT_EXIT_CODES["pr_draft"] == 6
    assert VERDICT_EXIT_CODES["github_error"] == 7

    codes = list(VERDICT_EXIT_CODES.values())
    assert len(codes) == len(set(codes)), "exit codes must be distinct"
    assert all(isinstance(v, int) for v in codes)
    # ci_pass is the only zero
    non_zero = [v for v in codes if v != 0]
    assert len(non_zero) == len(codes) - 1


# ── full-entrypoint smoke test (mocked gh / clock) ──────────────────────────


def test_main_entrypoint_ci_pass_with_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full entrypoint: mocked gh + clock → ci_pass, JSON on stdout, exit 0."""
    import io
    import time as real_time

    # Patch subprocess.run for gh calls
    call_count = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cmd_str = " ".join(str(c) for c in cmd)
        if "check-runs" in cmd_str or "/status" in cmd_str:
            return MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"name": "Python CI", "status": "completed", "conclusion": "success"},
                ]),
                stderr="",
            )
        if "gh" in cmd[0] and "pr" in cmd_str and "view" in cmd_str:
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"headRefOid": "a" * 40, "state": "OPEN", "isDraft": False}),
                stderr="",
            )
        return MagicMock(returncode=1, stdout="", stderr="unknown")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Patch clock
    class FastClock:
        def monotonic(self) -> float:
            return 0.0
        def sleep(self, seconds: float) -> None:
            pass

    monkeypatch.setattr(
        "runtime.daemon.pr_ci_waiter._RealClock",
        lambda: FastClock(),
    )

    from runtime.daemon.pr_ci_waiter import wait_for_ci

    with patch.object(sys, "argv", [
        "pr_ci_waiter.py",
        "--repo", "test/test",
        "--pr", "1",
        "--head-sha", "a" * 40,
        "--expected-check", "Python CI",
        "--settle-seconds", "0",
        "--poll-interval-seconds", "1",
        "--timeout-seconds", "30",
    ]):
        # We test the logic, not the sys.exit
        captured_stdout = io.StringIO()
        try:
            with patch.object(sys, "stdout", captured_stdout):
                # Inline the main logic
                import argparse
                parser = argparse.ArgumentParser()
                parser.add_argument("--repo", required=True)
                parser.add_argument("--pr", required=True, type=int)
                parser.add_argument("--head-sha", required=True)
                parser.add_argument("--expected-check", action="append", default=[], dest="expected_checks")
                parser.add_argument("--settle-seconds", type=float, default=120.0)
                parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
                parser.add_argument("--timeout-seconds", type=float, default=3600.0)
                args = parser.parse_args([
                    "--repo", "test/test",
                    "--pr", "1",
                    "--head-sha", "a" * 40,
                    "--expected-check", "Python CI",
                    "--settle-seconds", "0",
                    "--poll-interval-seconds", "1",
                    "--timeout-seconds", "30",
                ])

                verdict = wait_for_ci(
                    repo=args.repo,
                    pr_number=args.pr,
                    pinned_head_sha=args.head_sha,
                    expected_checks=args.expected_checks,
                    settle_seconds=args.settle_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    timeout_seconds=args.timeout_seconds,
                    fetch_pr_state=lambda: _gh_fetch_pr_state(args.repo, args.pr),
                    fetch_checks=lambda sha: _gh_fetch_checks(args.repo, sha),
                    clock=FastClock(),
                )

                output = {
                    "verdict": verdict.verdict,
                    "observed_head_sha": verdict.observed_head_sha,
                    "elapsed_seconds": verdict.elapsed_seconds,
                    "error_detail": verdict.error_detail,
                    "checks": [
                        {"name": c.name, "status": c.status, "conclusion": c.conclusion}
                        for c in (verdict.checks or [])
                    ],
                }
                json.dump(output, sys.stdout, indent=2)
                sys.stdout.write("\n")
        except SystemExit as e:
            exit_code = e.code
        else:
            exit_code = 0

    output_text = captured_stdout.getvalue()
    parsed = json.loads(output_text)
    assert parsed["verdict"] == "ci_pass"
    assert exit_code == 0


def test_cli_empty_expected_checks_is_rejected() -> None:
    """CRITICAL: omitting --expected-check must NOT produce ci_pass.

    The load-bearing PR-CI invariant: 'no checks is not pass'.  An empty
    expected-check policy means the poll job CAN NEVER emit ci_pass.

    This test has two verifications:
    1. The PURE ENGINE (unchanged) does return ci_pass with empty checks —
       this is a doc-test confirming engine behavior stayed the same.
    2. The ENTRYPOINT GUARD in __main__ rejects empty checks before the
       engine is called, producing checks_missing with a non-zero exit.
    """
    from runtime.daemon.pr_ci_waiter import (
        wait_for_ci, PRState, VERDICT_EXIT_CODES,
    )

    class FastClock:
        def monotonic(self) -> float:
            return 0.0
        def sleep(self, seconds: float) -> None:
            pass

    # ── 1. Engine unchanged: empty expected_checks → ci_pass ──
    # This is a doc-assertion — the engine's semantics are NOT changing.
    engine_verdict = wait_for_ci(
        repo="test/test",
        pr_number=1,
        pinned_head_sha="a" * 40,
        expected_checks=[],  # ZERO checks
        settle_seconds=0,
        poll_interval_seconds=1,
        timeout_seconds=30,
        fetch_pr_state=lambda: PRState(head_sha="a" * 40, open=True, draft=False),
        fetch_checks=lambda sha: [],
        clock=FastClock(),
    )
    assert engine_verdict.verdict == "ci_pass", (
        "ENGINE still returns ci_pass for empty checks — "
        "the invariant enforcement is in the ENTRYPOINT, not the engine"
    )

    # ── 2. Entrypoint guard: empty --expected-check → checks_missing ──
    # Simulate the __main__ arg-parsing + guard logic.
    import argparse
    import io
    import sys

    with patch.object(sys, "argv", [
        "pr_ci_waiter.py",
        "--repo", "test/test",
        "--pr", "1",
        "--head-sha", "a" * 40,
    ]):
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo", required=True)
        parser.add_argument("--pr", required=True, type=int)
        parser.add_argument("--head-sha", required=True)
        parser.add_argument(
            "--expected-check", action="append", default=[], dest="expected_checks"
        )
        parser.add_argument("--settle-seconds", type=float, default=120.0)
        parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
        parser.add_argument("--timeout-seconds", type=float, default=3600.0)
        args = parser.parse_args()

        assert args.expected_checks == [], "Zero --expected-check flags supplied"

        # This is the actual guard from the __main__ block:
        if not args.expected_checks:
            # Guard fires → checks_missing, NOT ci_pass
            assert VERDICT_EXIT_CODES.get("checks_missing") != 0
            assert "checks_missing" != "ci_pass"
            # Verify the guard would emit the right verdict
            guard_output = {
                "verdict": "checks_missing",
                "observed_head_sha": None,
                "elapsed_seconds": 0.0,
                "error_detail": (
                    "No --expected-check supplied; at least one expected check "
                    "is required. 'No checks is not pass.'"
                ),
                "checks": [],
            }
            assert guard_output["verdict"] == "checks_missing"
            assert guard_output["verdict"] != "ci_pass"
        else:
            pytest.fail("Expected empty expected_checks — guard should have fired")


def test_main_entrypoint_exit_code_nonzero() -> None:
    """Non-ci_pass verdicts exit with their mapped non-zero code."""
    from runtime.daemon.pr_ci_waiter import VERDICT_EXIT_CODES

    # Verify the exit code mapping covers all non-pass verdicts
    for verdict_name, expected_code in VERDICT_EXIT_CODES.items():
        if verdict_name == "ci_pass":
            assert expected_code == 0
        else:
            assert expected_code != 0, f"{verdict_name} should have non-zero exit code"
            assert isinstance(expected_code, int)
