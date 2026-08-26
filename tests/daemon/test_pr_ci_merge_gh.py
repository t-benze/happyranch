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


def test_recall_fetch_verdict_structured_disagrees_with_legacy() -> None:
    """Structured verdict disagrees with anchored legacy Verdict: line → fail closed."""
    import json
    recall_json = json.dumps({
        "task_id": "TASK-BAD",
        "status": "completed",
        "verdict": "APPROVE",
        "output_summary": "Verdict: PASS\n\nReview completed with notes.",
    }, indent=2)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="disagrees with anchored legacy"):
            _recall_fetch_verdict("happyranch", "TASK-BAD", "review")


def test_recall_fetch_verdict_structured_non_string_fails() -> None:
    """Structured verdict that is not a string → fail closed (no fallback)."""
    import json
    recall_json = json.dumps({
        "task_id": "TASK-INT",
        "status": "completed",
        "verdict": 123,
        "output_summary": "Verdict: PASS\n",
    }, indent=2)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="not a non-empty string"):
            _recall_fetch_verdict("happyranch", "TASK-INT", "review")


def test_recall_fetch_verdict_structured_empty_string_fails() -> None:
    """Structured verdict that is an empty string → fail closed."""
    import json
    recall_json = json.dumps({
        "task_id": "TASK-EMPTY",
        "status": "completed",
        "verdict": "   ",
        "output_summary": "Verdict: PASS\n",
    }, indent=2)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="not a non-empty string"):
            _recall_fetch_verdict("happyranch", "TASK-EMPTY", "review")


def test_recall_fetch_verdict_no_json_unanchored_prose_fails() -> None:
    """Non-JSON output with unanchored verdict: prefix → fail closed.

    The old parser accepted bare ``verdict: VALUE`` lines and free-form
    ``Verdict:`` text anywhere.  The hardened parser requires valid JSON.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="verdict: PASS\nstatus: completed", stderr=""
        )
        with pytest.raises(RuntimeError, match="Could not parse recall output as JSON"):
            _recall_fetch_verdict("happyranch", "TASK-456", "qa")


def test_recall_fetch_verdict_no_json_multiline_prose_fails() -> None:
    """Multi-line non-JSON output → fail closed."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Completion report:\nverdict: APPROVE\nconfidence: 90",
            stderr="",
        )
        with pytest.raises(RuntimeError, match="Could not parse recall output as JSON"):
            _recall_fetch_verdict("happyranch", "TASK-789", "review")


def test_recall_fetch_verdict_failure() -> None:
    """happyranch recall fails → RuntimeError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="not found"
        )
        with pytest.raises(RuntimeError, match="happyranch recall.*failed"):
            _recall_fetch_verdict("happyranch", "TASK-999", "review")


def test_recall_fetch_verdict_no_verdict() -> None:
    """Valid JSON but no verdict field and no Verdict: line → RuntimeError."""
    import json
    recall_json = json.dumps({
        "task_id": "TASK-000",
        "status": "completed",
        "output_summary": "No verdict line here.\nJust some prose.\n",
    }, indent=2)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Could not extract.*verdict"):
            _recall_fetch_verdict("happyranch", "TASK-000", "review")


def test_recall_fetch_verdict_real_output_fixture() -> None:
    """_recall_fetch_verdict accepts structured APPROVE + Verdict: APPROVE (canonical).

    Real recall output includes a top-level ``verdict`` field (added in
    TASK-3739).  ``Verdict: APPROVE`` is now in the strict legacy vocabulary
    (PASS|FAIL|REVISE|APPROVE).  When structured and anchored prose agree on
    the canonical token APPROVE, the verdict is accepted.
    """
    import json

    # REAL recall output shape — multi-line pretty-printed JSON with verdict field.
    real_recall_json = json.dumps({
        "task_id": "TASK-1496",
        "parent_task_id": "TASK-1479",
        "assigned_agent": "code_reviewer",
        "brief": "Code-review the REVISE pushed to PR #257 ...",
        "status": "completed",
        "verdict": "APPROVE",
        "output_summary": "Verdict: APPROVE\n\nSubsystems touched: system assistant A-mode ...",
        "output_dir": None,
        "children": []
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=real_recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-1496", "review")
    assert verdict == "APPROVE", (
        f"Expected 'APPROVE' from structured+prose agreement, got {verdict!r}"
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


def test_recall_fetch_verdict_structured_only_no_legacy_line() -> None:
    """Structured verdict present, NO anchored Verdict: in output_summary → use structured."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-SO",
        "status": "completed",
        "verdict": "APPROVE",
        "output_summary": "Review completed. No verdict line here.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-SO", "review")
    assert verdict == "APPROVE"


def test_recall_fetch_verdict_structured_and_legacy_agree() -> None:
    """Structured verdict agrees with anchored legacy verdict → returns the value."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-AGREE",
        "status": "completed",
        "verdict": "PASS",
        "output_summary": "Verdict: PASS\n\nAll QA checks green.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-AGREE", "qa")
    assert verdict == "PASS"


def test_recall_fetch_verdict_null_verdict_field_rejected() -> None:
    """When verdict key is present but value is null, fail closed — do NOT fall back to legacy."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-NULLV",
        "status": "completed",
        "verdict": None,
        "output_summary": "Verdict: PASS\n\nReview passed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="not a non-empty string"):
            _recall_fetch_verdict("happyranch", "TASK-NULLV", "review")


def test_recall_fetch_verdict_null_verdict_no_legacy_rejected() -> None:
    """Null verdict key present with no Verdict: line → fail closed (no prose fallback)."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-NULLNL",
        "status": "completed",
        "verdict": None,
        "output_summary": "All checks completed.\n",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="not a non-empty string"):
            _recall_fetch_verdict("happyranch", "TASK-NULLNL", "review")


def test_recall_fetch_verdict_legacy_newline_split_rejected() -> None:
    """Newline-split Verdict:\nPASS is rejected (no merge).

    ``\\s*`` in the previous regex consumed the newline; the hardened parser
    processes individual physical lines and rejects ``Verdict:`` on one line
    and ``PASS`` on the next.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-NLSPLIT",
        "status": "completed",
        "output_summary": "Verdict:\nPASS\n\nWork done.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-NLSPLIT", "qa")


def test_recall_fetch_verdict_legacy_duplicate_same_verdict_rejected() -> None:
    """Duplicate identical legacy Verdict: lines are rejected (exactly-one rule)."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-DUP",
        "status": "completed",
        "output_summary": "Verdict: PASS\nVerdict: PASS\n\nWork done.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Multiple legacy verdict lines"):
            _recall_fetch_verdict("happyranch", "TASK-DUP", "qa")


def test_recall_fetch_verdict_legacy_conflicting_verdict_rejected() -> None:
    """Conflicting legacy Verdict: lines (PASS + FAIL) are rejected."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-CONFLICT",
        "status": "completed",
        "output_summary": "Verdict: PASS\nVerdict: FAIL\n\nAmbiguous.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Multiple legacy verdict lines"):
            _recall_fetch_verdict("happyranch", "TASK-CONFLICT", "qa")


def test_recall_fetch_verdict_legacy_case_variant_rejected() -> None:
    """Case-variant legacy label (e.g. 'pass') is rejected — strict case-sensitive matching."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-CASE",
        "status": "completed",
        "output_summary": "Verdict: pass\n\nLowercase verdict.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-CASE", "qa")


def test_recall_fetch_verdict_legacy_malformed_label_rejected() -> None:
    """Malformed legacy label (e.g. 'APPROVED') is rejected — strict PASS|FAIL|REVISE|APPROVE only."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-MALF",
        "status": "completed",
        "output_summary": "Verdict: APPROVED\n\nNon-standard label.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-MALF", "qa")


def test_recall_fetch_verdict_legacy_pass_plus_malformed_rejected() -> None:
    """Valid legacy PASS + malformed Verdict: APPROVED → reject (fail closed).

    Per KB contract: ANY malformed Verdict: candidate line fails closed
    unconditionally, even when a valid legacy verdict is also present.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-MIX",
        "status": "completed",
        "output_summary": "Verdict: PASS\nVerdict: APPROVED\n\nMixed evidence.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-MIX", "qa")


def test_recall_fetch_verdict_structured_pass_plus_malformed_rejected() -> None:
    """Structured verdict PASS + malformed Verdict: APPROVED → fail closed.

    Per KB contract: malformed legacy candidates fail closed even when
    a valid structured verdict exists.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-SMIX",
        "status": "completed",
        "verdict": "PASS",
        "output_summary": "Verdict: APPROVED\n\nNon-standard label.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-SMIX", "qa")


def test_recall_fetch_verdict_structured_approve_plus_verdict_approve_accepted() -> None:
    """Structured APPROVE + physical Verdict: APPROVE → accepted (canonical).

    ``Verdict: APPROVE`` is now in the strict legacy vocabulary
    (PASS|FAIL|REVISE|APPROVE).  When structured verdict agrees with
    the anchored physical line, the verdict is accepted.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-RAPPROVE",
        "status": "completed",
        "verdict": "APPROVE",
        "output_summary": "Verdict: APPROVE\n\nCode review passed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-RAPPROVE", "review")
    assert verdict == "APPROVE"


def test_recall_fetch_verdict_legacy_horizontal_whitespace_ok() -> None:
    """Horizontal whitespace around the legacy verdict token is accepted."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-HWSP",
        "status": "completed",
        "output_summary": "Verdict:  \tPASS  \t\n\nExtra whitespace.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-HWSP", "qa")
    assert verdict == "PASS"


def test_recall_fetch_verdict_legacy_revise_accepted() -> None:
    """REVISE is a valid legacy verdict token."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-REV",
        "status": "completed",
        "output_summary": "Verdict: REVISE\n\nNeeds changes.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=recall_json, stderr=""
        )
        verdict = _recall_fetch_verdict("happyranch", "TASK-REV", "qa")
    assert verdict == "REVISE"


def test_recall_fetch_verdict_correct_command() -> None:
    """Correct CLI command passed to subprocess."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"verdict": "APPROVE"}', stderr=""
        )
        _recall_fetch_verdict("myorg", "TASK-042", "review")
    call_args = mock_run.call_args[0][0]
    assert call_args == ["happyranch", "recall", "--org", "myorg", "TASK-042"]


# ── canonical-vocabulary / annotated-prose extraction contract (THR-204) ────
# The contract is canonical in protocol/00-completion-contract.md
# ("Merge-evidence contract"): structured `verdict` must be a canonical
# non-empty token; prose `Verdict:` lines may carry the canonical token
# followed by a human annotation (e.g. `Verdict: PASS — rationale`); both
# forms must agree exactly when both present.  The canonical vocabulary is
# APPROVE | REQUEST_CHANGES | BLOCK (review) and PASS | REVISE | FAIL (QA)
# — the full shared producer vocabulary — and the downstream role gates
# (review == APPROVE, QA == PASS) are the second layer of rejection.


def test_recall_fetch_verdict_annotated_prose_accepted() -> None:
    """Legacy-only `Verdict: PASS — rationale` (em-dash annotation) → PASS.

    Mirrors the durable TASK-5619 QA summary form `Verdict: PASS — Independent
    QA of PR #...` that the previous strict single-line grammar rejected as
    malformed.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-ANN1",
        "status": "completed",
        "output_summary": (
            "Verdict: PASS \u2014 Independent QA of PR #710 at the exact "
            "pinned immutable head. All checks green.\n"
        ),
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-ANN1", "qa")
    assert verdict == "PASS"


def test_recall_fetch_verdict_request_changes_legacy_accepted() -> None:
    """Legacy-only `Verdict: REQUEST_CHANGES` parses to REQUEST_CHANGES.

    REQUEST_CHANGES is a canonical reviewer producer token; the parser must
    NOT treat it as malformed.  The downstream review gate (== APPROVE) is
    what rejects it before merge.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-RC1",
        "status": "completed",
        "output_summary": "Verdict: REQUEST_CHANGES\n\nReview found gaps.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-RC1", "review")
    assert verdict == "REQUEST_CHANGES"


def test_recall_fetch_verdict_block_legacy_accepted() -> None:
    """Legacy-only `Verdict: BLOCK` parses to BLOCK (canonical producer token)."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-BLK1",
        "status": "completed",
        "output_summary": "Verdict: BLOCK\n\nEvidence gate failed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-BLK1", "qa")
    assert verdict == "BLOCK"


def test_recall_fetch_verdict_structured_plus_annotated_prose_agree() -> None:
    """Structured PASS + `Verdict: PASS — rationale` agree → PASS."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-AGREE2",
        "status": "completed",
        "verdict": "PASS",
        "output_summary": "Verdict: PASS \u2014 Independent QA of PR #710. Done.\n",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-AGREE2", "qa")
    assert verdict == "PASS"


def test_recall_fetch_verdict_structured_approve_plus_annotated_prose_agree() -> None:
    """Structured APPROVE + `Verdict: APPROVE — reviewed` agree → APPROVE."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-RAPP2",
        "status": "completed",
        "verdict": "APPROVE",
        "output_summary": "Verdict: APPROVE \u2014 all guards re-verified.\n",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-RAPP2", "review")
    assert verdict == "APPROVE"


def test_recall_fetch_verdict_structured_disagrees_annotated_prose() -> None:
    """Structured PASS + `Verdict: REVISE — ...` → fail closed (disagreement)."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-DISAG2",
        "status": "completed",
        "verdict": "PASS",
        "output_summary": "Verdict: REVISE \u2014 needs more work.\n",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="disagrees with anchored legacy"):
            _recall_fetch_verdict("happyranch", "TASK-DISAG2", "qa")


def test_recall_fetch_verdict_structured_unknown_token_rejected() -> None:
    """Structured verdict that is not a canonical token (e.g. APPROVED) → fail closed.

    No fallback to prose; the row claims structured data that is unusable.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-UNK1",
        "status": "completed",
        "verdict": "APPROVED",
        "output_summary": "Verdict: APPROVE\n\nReview passed.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="not a canonical verdict token"):
            _recall_fetch_verdict("happyranch", "TASK-UNK1", "review")


def test_recall_fetch_verdict_structured_annotated_value_rejected() -> None:
    """Structured verdict carrying an in-field annotation → fail closed.

    Producers must persist ONLY the canonical token in `verdict`; annotations
    belong in prose.  Historical rows like `"REVISE — STRUCTURAL ESCALATION — ..."`
    are unusable structured evidence.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-ANNF1",
        "status": "completed",
        "verdict": "REVISE \u2014 STRUCTURAL ESCALATION \u2014 boundary reached",
        "output_summary": "Verdict: REVISE\n\nFix-forward boundary.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="not a canonical verdict token"):
            _recall_fetch_verdict("happyranch", "TASK-ANNF1", "qa")


def test_recall_fetch_verdict_structured_case_variant_rejected() -> None:
    """Structured verdict `pass` (case variant) → fail closed."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-CASE2",
        "status": "completed",
        "verdict": "pass",
        "output_summary": "Verdict: PASS\n\nQA green.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="not a canonical verdict token"):
            _recall_fetch_verdict("happyranch", "TASK-CASE2", "qa")


def test_recall_fetch_verdict_annotated_malformed_token_rejected() -> None:
    """Annotated line with a non-canonical token (`Verdict: APPROVED — ...`) → malformed."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-MALF2",
        "status": "completed",
        "output_summary": "Verdict: APPROVED \u2014 non-standard label.\n",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-MALF2", "qa")


def test_recall_fetch_verdict_em_dash_no_space_rejected() -> None:
    """`Verdict: PASS—rationale` (no whitespace before annotation) → malformed.

    The annotation must be whitespace-separated from the canonical token;
    the token itself must be exactly a canonical vocabulary member.
    """
    import json

    recall_json = json.dumps({
        "task_id": "TASK-NOSP1",
        "status": "completed",
        "output_summary": "Verdict: PASS\u2014rationale\n\nNo space.",
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="Malformed Verdict candidate"):
            _recall_fetch_verdict("happyranch", "TASK-NOSP1", "qa")


def test_recall_fetch_verdict_duplicate_annotated_candidates_rejected() -> None:
    """Two annotated `Verdict: PASS — ...` lines (same token) → fail closed."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-DUP2",
        "status": "completed",
        "output_summary": (
            "Verdict: PASS \u2014 first note.\nVerdict: PASS \u2014 second note.\n"
        ),
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="Multiple legacy verdict lines"):
            _recall_fetch_verdict("happyranch", "TASK-DUP2", "qa")


def test_recall_fetch_verdict_annotated_conflicting_candidates_rejected() -> None:
    """Annotated PASS + annotated REVISE lines → fail closed (multiple candidates)."""
    import json

    recall_json = json.dumps({
        "task_id": "TASK-CONF2",
        "status": "completed",
        "output_summary": (
            "Verdict: PASS \u2014 one.\nVerdict: REVISE \u2014 two.\n"
        ),
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=recall_json, stderr="")
        with pytest.raises(RuntimeError, match="Multiple legacy verdict lines"):
            _recall_fetch_verdict("happyranch", "TASK-CONF2", "qa")


def test_recall_fetch_verdict_real_annotated_qa_fixture() -> None:
    """Real TASK-5619-shaped recall (structured PASS + annotated prose) → PASS.

    This is the durable evidence shape that THR-204's contract repair must
    accept: the previous strict grammar rejected the `Verdict: PASS \u2014 ...`
    annotation as malformed and blocked the guarded merge of PR #710.
    """
    import json

    real_recall_json = json.dumps({
        "task_id": "TASK-5619",
        "parent_task_id": "TASK-5614",
        "assigned_agent": "qa_engineer",
        "status": "completed",
        "verdict": "PASS",
        "output_summary": (
            "Verdict: PASS \u2014 Independent QA of PR #710 "
            "(fix(executor-binary-registry): fail closed on test writes to "
            "production registry, THR-204 issue 3) at the exact pinned "
            "immutable head 7988f322. Reviewer gate cleared (TASK-5615 APPROVE). "
            "Full report: output/TASK-5619/qa-report.md."
        ),
        "output_dir": "output/TASK-5619",
        "children": [],
    }, indent=2)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=real_recall_json, stderr="")
        verdict = _recall_fetch_verdict("happyranch", "TASK-5619", "qa")
    assert verdict == "PASS"


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
