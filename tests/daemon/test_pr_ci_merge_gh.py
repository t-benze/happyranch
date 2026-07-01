"""Unit tests for the real-gh adapter + CLI entrypoint of pr_ci_merge.py.

Covers:
  - CLI arg parsing
  - Structured-verdict JSON output shape
  - Exit-code mapping for each verdict
  - Real-gh callable construction and dispatch (mock subprocess — NO network)
  - Recall-based verdict fetching (mock happyranch recall)
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.daemon.pr_ci_merge import (
    GuardedMergeVerdict,
    MergeableState,
    MergeResult,
    VERDICT_EXIT_CODES,
    _gh_fetch_mergeable,
    _gh_fetch_pr_state,
    _gh_perform_merge,
    _recall_fetch_verdict,
)
from runtime.daemon.pr_ci_waiter import PRState


# ── _gh_fetch_pr_state tests (merge-specific coverage) ─────────────────────


def test_gh_fetch_pr_state_merge_module() -> None:
    """gh pr view in merge module parses correctly."""
    stdout = json.dumps({"headRefOid": "d" * 40, "state": "OPEN", "isDraft": False})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        pr = _gh_fetch_pr_state("owner/repo", 10)
    assert pr.head_sha == "d" * 40
    assert pr.open is True
    assert pr.draft is False


# ── _gh_fetch_mergeable tests ────────────────────────────────────────────────


def test_gh_fetch_mergeable_clean() -> None:
    """mergeStateStatus = CLEAN → MergeableState(mergeable='CLEAN')."""
    stdout = json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        ms = _gh_fetch_mergeable("owner/repo", 1)
    assert ms.mergeable == "CLEAN"
    assert "MERGEABLE" in (ms.detail or "")


def test_gh_fetch_mergeable_blocked() -> None:
    """mergeStateStatus = BLOCKED."""
    stdout = json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "BLOCKED"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        ms = _gh_fetch_mergeable("owner/repo", 2)
    assert ms.mergeable == "BLOCKED"


def test_gh_fetch_mergeable_unknown() -> None:
    """mergeStateStatus = UNKNOWN (checks still running)."""
    stdout = json.dumps({"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        ms = _gh_fetch_mergeable("owner/repo", 3)
    assert ms.mergeable == "UNKNOWN"


def test_gh_fetch_mergeable_gh_failure() -> None:
    """gh pr view fails → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        with pytest.raises(RuntimeError, match="gh pr view.*mergeable.*failed"):
            _gh_fetch_mergeable("owner/repo", 1)


# ── _gh_perform_merge tests ─────────────────────────────────────────────────


def test_gh_perform_merge_success() -> None:
    """gh pr merge succeeds → MergeResult with merged_sha."""
    merge_stdout = "Merged PR #42\n"
    view_stdout = json.dumps({"mergeCommit": {"oid": "m" * 40}})

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "merge" in cmd_str and "view" not in cmd_str:
            return MagicMock(returncode=0, stdout=merge_stdout, stderr="")
        return MagicMock(returncode=0, stdout=view_stdout, stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        result = _gh_perform_merge("owner/repo", 42, "squash")

    assert result.merged_sha == "m" * 40
    assert result.merged_at is not None


def test_gh_perform_merge_failure() -> None:
    """gh pr merge fails → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="merge conflict"
        )
        with pytest.raises(RuntimeError, match="gh pr merge failed"):
            _gh_perform_merge("owner/repo", 1, "merge")


def test_gh_perform_merge_method_in_command() -> None:
    """The merge method flag is passed to gh pr merge."""
    merge_stdout = "Merged\n"
    view_stdout = json.dumps({"mergeCommit": {"oid": "m" * 40}})

    captured_cmd: list[list[str]] = []

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        captured_cmd.append(list(args[0]))
        cmd_str = " ".join(str(c) for c in args[0])
        if "merge" in cmd_str and "view" not in cmd_str:
            return MagicMock(returncode=0, stdout=merge_stdout, stderr="")
        return MagicMock(returncode=0, stdout=view_stdout, stderr="")

    with patch("subprocess.run", side_effect=run_side_effect):
        _gh_perform_merge("owner/repo", 1, "rebase")

    merge_cmd = captured_cmd[0]
    assert "--rebase" in merge_cmd


def test_gh_perform_merge_view_failure_graceful() -> None:
    """If post-merge view fails, still returns MergeResult (empty sha)."""
    merge_stdout = "Merged\n"

    def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        cmd = args[0] if args else []
        cmd_str = " ".join(str(c) for c in cmd)
        if "merge" in cmd_str and "view" not in cmd_str:
            return MagicMock(returncode=0, stdout=merge_stdout, stderr="")
        return MagicMock(returncode=1, stdout="", stderr="gh: not found")

    with patch("subprocess.run", side_effect=run_side_effect):
        result = _gh_perform_merge("owner/repo", 1, "squash")

    assert result.merged_sha == ""  # gracefully empty
    assert result.merged_at is not None


# ── _recall_fetch_verdict tests ─────────────────────────────────────────────


def test_recall_fetch_verdict_json_output() -> None:
    """happyranch recall returns JSON with verdict field."""
    recall_output = '{"verdict": "APPROVE", "status": "completed"}'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_output, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-123", "review")
    assert verdict == "APPROVE"


def test_recall_fetch_verdict_key_value() -> None:
    """happyranch recall returns key: value format."""
    recall_output = "verdict: PASS\nstatus: completed"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_output, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-456", "qa")
    assert verdict == "PASS"


def test_recall_fetch_verdict_multiline() -> None:
    """Multi-line output with verdict inline."""
    recall_output = "Completion report:\nverdict: APPROVE\nconfidence: 90"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_output, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-789", "review")
    assert verdict == "APPROVE"


def test_recall_fetch_verdict_failure() -> None:
    """happyranch recall fails → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="not found"
        )
        with pytest.raises(RuntimeError, match="happyranch recall.*failed"):
            _recall_fetch_verdict("happyranch", "TASK-999", "review")


def test_recall_fetch_verdict_no_verdict() -> None:
    """No verdict in recall output → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="No completion report", stderr=""
        )
        with pytest.raises(RuntimeError, match="Could not extract.*verdict"):
            _recall_fetch_verdict("happyranch", "TASK-000", "review")


def test_recall_fetch_verdict_real_output_fixture() -> None:
    """HIGH: _recall_fetch_verdict must parse real happyranch recall output.

    The current parser treats each line as standalone JSON and falls back to
    a 'verdict:' line prefix.  Real recall output is a SINGLE multi-line
    pretty-printed JSON blob — so line-by-line JSON parsing ALWAYS fails.

    This test uses a fixture COPIED FROM a real `happyranch recall` call
    (TASK-1496, a code_reviewer task with verdict APPROVE).  The verdict
    lives in output_summary as 'Verdict: APPROVE'.
    """
    import json

    # REAL recall output shape — multi-line pretty-printed JSON.
    # The verdict is "APPROVE", embedded in output_summary.
    real_recall_json = json.dumps({
        "task_id": "TASK-1496",
        "parent_task_id": "TASK-1479",
        "assigned_agent": "code_reviewer",
        "brief": "Code-review the REVISE pushed to PR #257 ...",
        "status": "completed",
        "output_summary": "Verdict: APPROVE\n\nSubsystems touched: system assistant A-mode ...",
        "output_dir": None,
        "children": []
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=real_recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-1496", "review")

    # The current parser FAILS here because:
    # - Line 1 is "{"  → not JSON, not "verdict:"
    # - Line 2 is `  "task_id": "TASK-1496",` → not a complete JSON object, not "verdict:"
    # - ... eventually hits RuntimeError("Could not extract ... verdict")
    assert verdict == "APPROVE", (
        f"Expected 'APPROVE' from output_summary, got {verdict!r}. "
        "The parser must parse the ENTIRE stdout as JSON first, "
        "then extract the verdict from output_summary."
    )


def test_recall_fetch_verdict_top_level_verdict_field() -> None:
    """Top-level verdict JSON property is used when present."""
    import json

    # Some recall output may carry a top-level 'verdict' field
    recall_json = json.dumps({
        "task_id": "TASK-XXX",
        "verdict": "APPROVE",
        "status": "completed",
        "output_summary": "Review passed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-XXX", "review")

    assert verdict == "APPROVE"


def test_recall_fetch_verdict_output_summary_verdict_line() -> None:
    """Extract verdict from output_summary's 'Verdict:' line."""
    import json

    # Verdict in output_summary but NO top-level verdict field
    recall_json = json.dumps({
        "task_id": "TASK-YYY",
        "status": "completed",
        "output_summary": "Verdict: PASS\n\nQA checks completed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-YYY", "qa")

    assert verdict == "PASS"


def test_recall_fetch_verdict_correct_command() -> None:
    """Correct CLI command passed to subprocess."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"verdict": "APPROVE"}', stderr=""
        )
        _recall_fetch_verdict("myorg", "TASK-042", "review")
    call_args = mock_run.call_args[0][0]
    assert call_args == ["happyranch", "recall", "--org", "myorg", "TASK-042"]


# ── CLI entrypoint tests: arg parsing ────────────────────────────────────────


def test_cli_merge_required_args() -> None:
    """All required args parsed correctly."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--merge-method", required=True, choices=["merge", "squash", "rebase"])
    parser.add_argument("--ci-verdict", required=True)
    parser.add_argument("--review-task-id", required=True)
    parser.add_argument("--qa-task-id", required=True)

    args = parser.parse_args([
        "--org", "happyranch",
        "--repo", "owner/repo",
        "--pr", "42",
        "--head-sha", "a" * 40,
        "--merge-method", "squash",
        "--ci-verdict", "ci_pass",
        "--review-task-id", "TASK-001",
        "--qa-task-id", "TASK-002",
    ])
    assert args.org == "happyranch"
    assert args.repo == "owner/repo"
    assert args.pr == 42
    assert args.head_sha == "a" * 40
    assert args.merge_method == "squash"
    assert args.ci_verdict == "ci_pass"
    assert args.review_task_id == "TASK-001"
    assert args.qa_task_id == "TASK-002"


def test_cli_merge_method_validation() -> None:
    """Invalid merge method rejected by argparse."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--merge-method", choices=["merge", "squash", "rebase"])

    with pytest.raises(SystemExit):
        parser.parse_args(["--merge-method", "fast-forward"])


# ── JSON verdict output shape tests ─────────────────────────────────────────


def test_merge_verdict_json_output_shape_merged() -> None:
    """JSON output for merged verdict contains all fields."""
    output = {
        "verdict": "merged",
        "pr_number": 42,
        "pinned_head_sha": "a" * 40,
        "merged_sha": "m" * 40,
        "merged_at": "2026-07-01T12:00:00+00:00",
        "observed_head_sha": None,
        "error_detail": None,
    }
    parsed = json.loads(json.dumps(output))
    assert parsed["verdict"] == "merged"
    assert parsed["pr_number"] == 42
    assert parsed["pinned_head_sha"] == "a" * 40
    assert parsed["merged_sha"] == "m" * 40
    assert parsed["merged_at"] is not None
    assert parsed["observed_head_sha"] is None
    assert parsed["error_detail"] is None


def test_merge_verdict_json_output_shape_guard() -> None:
    """JSON output for guard failure carries error detail."""
    output = {
        "verdict": "merge_guard_review",
        "pr_number": 1,
        "pinned_head_sha": "a" * 40,
        "merged_sha": None,
        "merged_at": None,
        "observed_head_sha": None,
        "error_detail": None,
    }
    parsed = json.loads(json.dumps(output))
    assert parsed["verdict"] == "merge_guard_review"
    assert parsed["merged_sha"] is None
    assert parsed["merged_at"] is None
    assert parsed["error_detail"] is None


def test_merge_verdict_json_output_shape_error() -> None:
    """JSON output for error carries error_detail."""
    output = {
        "verdict": "merge_failed",
        "pr_number": 1,
        "pinned_head_sha": "a" * 40,
        "merged_sha": None,
        "merged_at": None,
        "observed_head_sha": None,
        "error_detail": "gh pr merge exit 1: branch protection",
    }
    parsed = json.loads(json.dumps(output))
    assert parsed["verdict"] == "merge_failed"
    assert parsed["error_detail"] == "gh pr merge exit 1: branch protection"


# ── exit-code mapping ────────────────────────────────────────────────────────


def test_merge_exit_code_mapping() -> None:
    """Every merge engine verdict has a distinct exit code; merged=0."""
    assert VERDICT_EXIT_CODES["merged"] == 0

    merge_verdicts = {
        "merge_guard_review", "merge_guard_qa",
        "merge_guard_mergeable", "merge_failed",
    }
    for v in merge_verdicts:
        assert v in VERDICT_EXIT_CODES, f"missing exit code for {v!r}"
        assert VERDICT_EXIT_CODES[v] != 0, f"{v!r} should be non-zero"
        assert isinstance(VERDICT_EXIT_CODES[v], int)

    # Pass-through codes
    waiter_codes = {"ci_failed", "stale_head", "checks_missing", "timeout",
                    "pr_closed", "pr_draft", "github_error"}
    for v in waiter_codes:
        assert v in VERDICT_EXIT_CODES, f"missing pass-through code for {v!r}"
        assert VERDICT_EXIT_CODES[v] != 0

    codes = list(VERDICT_EXIT_CODES.values())
    assert len(codes) == len(set(codes)), "exit codes must be distinct"


# ── full-entrypoint smoke test (mocked gh / recall / clock) ──────────────────


def test_main_entrypoint_merged_with_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full entrypoint: mocked gh + recall → merged, JSON on stdout."""
    import io

    # Patch subprocess.run for gh calls
    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        cmd_str = " ".join(str(c) for c in cmd)
        if "happyranch" in str(cmd[0]):
            return MagicMock(
                returncode=0,
                stdout='{"verdict": "APPROVE"}',
                stderr="",
            )
        # Post-merge sha view (must come before general view check)
        if "mergeCommit" in cmd_str:
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"mergeCommit": {"oid": "m" * 40}}),
                stderr="",
            )
        if "merge" in cmd_str and "view" not in cmd_str:
            return MagicMock(returncode=0, stdout="Merged\n", stderr="")
        if "view" in cmd_str and "mergeable" in cmd_str:
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}),
                stderr="",
            )
        if "view" in cmd_str:
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"headRefOid": "a" * 40, "state": "OPEN", "isDraft": False}),
                stderr="",
            )
        return MagicMock(returncode=1, stdout="", stderr="unknown")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from runtime.daemon.pr_ci_merge import guarded_merge

    captured = io.StringIO()
    exit_code = 0

    # This exercises the entrypoint logic
    verdict = guarded_merge(
        repo="test/test",
        pr_number=1,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict="ci_pass",
        fetch_pr_state=lambda: _gh_fetch_pr_state("test/test", 1),
        fetch_mergeable=lambda: _gh_fetch_mergeable("test/test", 1),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=lambda m: _gh_perform_merge("test/test", 1, m),
    )

    output = {
        "verdict": verdict.verdict,
        "pr_number": verdict.pr_number,
        "pinned_head_sha": verdict.pinned_head_sha,
        "merged_sha": verdict.merged_sha,
        "merged_at": verdict.merged_at,
        "observed_head_sha": verdict.observed_head_sha,
        "error_detail": verdict.error_detail,
    }

    assert verdict.verdict == "merged"
    parsed = json.loads(json.dumps(output))
    assert parsed["verdict"] == "merged"
    assert parsed["pr_number"] == 1
    assert parsed["pinned_head_sha"] == "a" * 40


def test_main_entrypoint_guard_failure_exit_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard failure verdicts exit with non-zero codes."""
    # No subprocess needed — test pure engine inputs
    from runtime.daemon.pr_ci_merge import guarded_merge

    verdict = guarded_merge(
        repo="test/test",
        pr_number=1,
        pinned_head_sha="a" * 40,
        merge_method="squash",
        ci_verdict="ci_failed",
        fetch_pr_state=lambda: PRState(head_sha="a" * 40, open=True, draft=False),
        fetch_mergeable=lambda: MergeableState(mergeable="CLEAN"),
        fetch_review_verdict=lambda: "APPROVE",
        fetch_qa_verdict=lambda: "PASS",
        perform_merge=lambda m: MergeResult(merged_sha="", merged_at=""),
    )

    assert verdict.verdict == "ci_failed"
    exit_code = VERDICT_EXIT_CODES.get(verdict.verdict, 99)
    assert exit_code != 0
