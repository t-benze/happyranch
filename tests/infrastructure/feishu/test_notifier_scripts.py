"""Body-builder tests for script-request Feishu push + result follow-up."""
from __future__ import annotations

from src.infrastructure.feishu.notifier import (
    _build_script_request_body,
    _build_script_result_body,
    _SCRIPT_PREVIEW_CAP,
    _RESULT_OUTPUT_PREVIEW_CAP,
)


def test_request_body_renders_all_fields():
    title, lines = _build_script_request_body(
        slug="acme",
        sr_id="SR-019",
        agent="engineering_head",
        task_id="TASK-91",
        title="Close PR #247",
        rationale="Need founder to close because allow_rules block gh pr close",
        script_text="set -euo pipefail\ngh pr close 247",
        interpreter="bash",
        cwd_hint="repos/web-app",
    )
    body = "\n".join(lines)
    assert "SR-019" in title
    assert "acme" in title
    assert "submitted" in title
    assert "Agent:" in body and "engineering_head" in body
    assert "Task:" in body and "TASK-91" in body
    assert "Interpreter:" in body and "bash" in body
    assert "Cwd hint:" in body and "repos/web-app" in body
    assert "Close PR #247" in body
    assert "Need founder to close" in body
    assert "gh pr close 247" in body
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "grassland scripts show SR-019" in body
    assert "grassland scripts run SR-019" in body
    assert "grassland scripts reject SR-019" in body


def test_request_body_missing_cwd_hint_renders_workspace_root():
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "(workspace root)" in body


def test_request_body_truncates_long_script():
    long_script = "x" * (_SCRIPT_PREVIEW_CAP + 500)
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=long_script,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "[truncated — see grassland scripts show SR-019 for full script]" in body
    assert body.count("[truncated") == 1


def test_request_body_keeps_short_script_intact():
    short = "echo hi"
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=short,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "echo hi" in body
    assert "[truncated" not in body


def test_result_body_completed_branch():
    title, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=0, duration_ms=1400,
        stdout_head="✓ Closed pull request #247\n",
        stderr_head=None, reason=None,
    )
    body = "\n".join(lines)
    assert "SR-019" in title
    assert "completed" in title
    assert "exit 0" in title
    assert "Duration: 1.4s" in body
    assert "✓ Closed pull request #247" in body
    assert "(empty)" in body


def test_result_body_failed_branch_with_reason():
    title, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="failed",
        exit_code=None, duration_ms=300_000,
        stdout_head=None,
        stderr_head="Error: connection timed out",
        reason="timeout",
    )
    body = "\n".join(lines)
    assert "failed" in title
    assert "timeout" in title
    assert "Duration: 300.0s" in body
    assert "Error: connection timed out" in body


def test_result_body_truncates_long_output():
    long_out = "line\n" * 200
    _, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=0, duration_ms=100,
        stdout_head=long_out, stderr_head=None, reason=None,
    )
    body = "\n".join(lines)
    assert f"[truncated — full output in grassland scripts output SR-019]" in body


def test_result_body_completed_unknown_exit_code():
    title, _ = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=None, duration_ms=100,
        stdout_head=None, stderr_head=None, reason=None,
    )
    assert "exit ?" in title
