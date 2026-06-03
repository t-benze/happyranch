"""Body-builder tests for script-request Feishu push + result follow-up."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.feishu.notifier import (
    EscalationNotifier,
    _build_job_request_body,
    _build_job_result_body,
    _SCRIPT_PREVIEW_CAP,
    _RESULT_OUTPUT_PREVIEW_CAP,
)
from runtime.orchestrator.org_config import FeishuNotificationsConfig


def test_request_body_renders_all_fields():
    title, lines = _build_job_request_body(
        slug="acme",
        job_id="SR-019",
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
    assert "happyranch scripts show SR-019" in body
    assert "happyranch scripts run SR-019" in body
    assert "happyranch scripts reject SR-019" in body


def test_request_body_missing_cwd_hint_renders_workspace_root():
    _, lines = _build_job_request_body(
        slug="acme", job_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "(workspace root)" in body


def test_request_body_truncates_long_script():
    long_script = "x" * (_SCRIPT_PREVIEW_CAP + 500)
    _, lines = _build_job_request_body(
        slug="acme", job_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=long_script,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "[truncated — see happyranch scripts show SR-019 for full script]" in body
    assert body.count("[truncated") == 1


def test_request_body_keeps_short_script_intact():
    short = "echo hi"
    _, lines = _build_job_request_body(
        slug="acme", job_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=short,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "echo hi" in body
    assert "[truncated" not in body


def test_result_body_completed_branch():
    title, lines = _build_job_result_body(
        slug="acme", job_id="SR-019", status="completed",
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
    title, lines = _build_job_result_body(
        slug="acme", job_id="SR-019", status="failed",
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
    _, lines = _build_job_result_body(
        slug="acme", job_id="SR-019", status="completed",
        exit_code=0, duration_ms=100,
        stdout_head=long_out, stderr_head=None, reason=None,
    )
    body = "\n".join(lines)
    assert f"[truncated — full output in happyranch scripts output SR-019]" in body


def test_result_body_completed_unknown_exit_code():
    title, _ = _build_job_result_body(
        slug="acme", job_id="SR-019", status="completed",
        exit_code=None, duration_ms=100,
        stdout_head=None, stderr_head=None, reason=None,
    )
    assert "exit ?" in title


def test_request_body_renders_each_script_line_as_separate_element():
    """Each script line must be its own body_lines element so Feishu renders
    each as a separate paragraph (one paragraph per element is the established
    convention in client.send_post_message)."""
    _, lines = _build_job_request_body(
        slug="acme", job_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r",
        script_text="set -euo pipefail\ngh pr close 247\necho done",
        interpreter="bash", cwd_hint=None,
    )
    # The three script lines must appear as three separate list elements.
    assert "set -euo pipefail" in lines
    assert "gh pr close 247" in lines
    assert "echo done" in lines
    # None of them carry embedded newlines.
    for el in lines:
        assert "\n" not in el, f"element {el!r} contains embedded newline"


def test_request_body_truncation_marker_is_its_own_element():
    """When the script is truncated, the truncation footer must be a separate
    element (not appended to the last script line)."""
    long_script = "x" * (_SCRIPT_PREVIEW_CAP + 500)
    _, lines = _build_job_request_body(
        slug="acme", job_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=long_script,
        interpreter="bash", cwd_hint=None,
    )
    # The truncation marker is a standalone element.
    marker = f"[truncated — see happyranch scripts show SR-019 for full script]"
    assert marker in lines
    # And again, no embedded newlines anywhere.
    for el in lines:
        assert "\n" not in el, f"element {el!r} contains embedded newline"


class _FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.thread_replies: list[dict] = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.posts.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return f"om_post_{len(self.posts)}"

    def send_thread_reply(self, *, parent_message_id, title, body_lines):
        self.thread_replies.append({
            "parent": parent_message_id, "title": title, "body": body_lines,
        })
        return f"om_thread_{len(self.thread_replies)}"


@pytest.fixture()
def notifier_setup(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    notifier = EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    )
    return notifier, db, client


def test_send_script_request_happy_path(notifier_setup):
    notifier, db, client = notifier_setup
    asyncio.run(notifier.send_job_request(
        job_id="SR-019", agent="engineering_head",
        task_id="TASK-91", title="Close PR #247",
        rationale="ok", script_text="echo hi",
        interpreter="bash", cwd_hint="repos/web-app",
    ))
    assert len(client.posts) == 1
    sent = client.posts[0]
    assert "SR-019" in sent["title"]
    assert sent["chat_id"] == "oc_xyz"

    row = db.get_escalation_notification("om_post_1")
    assert row is not None
    assert row["kind"] == "job_request"
    assert row["task_id"] == "SR-019"

    audit_rows = db.get_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "job_notify_sent" for r in audit_rows)


def test_send_script_request_swallows_send_failure(notifier_setup):
    notifier, db, client = notifier_setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_post_message = boom
    asyncio.run(notifier.send_job_request(
        job_id="SR-019", agent="a", task_id="TASK-91",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    ))
    assert db.get_latest_notification_for_sr("SR-019", kind="job_request") is None
    audit_rows = db.get_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "job_notify_failed" for r in audit_rows)


def test_send_script_run_result_happy_path(notifier_setup):
    notifier, db, client = notifier_setup
    asyncio.run(notifier.send_job_run_result(
        job_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root_xyz",
        status="completed", exit_code=0, duration_ms=1400,
        stdout_head="ok", stderr_head=None, reason=None,
    ))
    assert len(client.thread_replies) == 1
    reply = client.thread_replies[0]
    assert reply["parent"] == "om_root_xyz"
    assert "SR-019" in reply["title"]
    assert "completed" in reply["title"]
    audit_rows = db.get_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "job_run_result_notify_sent" for r in audit_rows)


def test_send_script_run_result_swallows_send_failure(notifier_setup):
    notifier, db, client = notifier_setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_thread_reply = boom
    asyncio.run(notifier.send_job_run_result(
        job_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root_xyz",
        status="failed", exit_code=None, duration_ms=0,
        stdout_head=None, stderr_head="x", reason="timeout",
    ))
    audit_rows = db.get_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "job_run_result_notify_failed" for r in audit_rows)
