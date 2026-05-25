"""End-to-end SR Feishu notification round-trip: agent submits a script
request, founder receives the push via Feishu, replies APPROVE/REJECT to
that message, and the listener routes to run/reject — with a threaded
follow-up message posted on terminal completion.

Mirrors the shape of ``test_feishu_failure_revisit_e2e.py`` and exercises
the full submit-push-act-result loop without spawning real subprocesses
or opening a real WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from tests.integration.fake_feishu import make_fake_feishu


pytestmark = pytest.mark.integration


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _run_server(app, host, port, ready):
    @app.on_event("startup")
    async def _on_startup():
        ready.set()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    uvicorn.Server(config).run()


@pytest.fixture
def fake_feishu():
    port = _free_port()
    app, state = make_fake_feishu()
    ready = threading.Event()
    threading.Thread(
        target=_run_server, args=(app, "127.0.0.1", port, ready), daemon=True,
    ).start()
    assert ready.wait(timeout=5.0), "fake feishu didn't start"
    yield f"http://127.0.0.1:{port}", state


def _make_event(*, root_id: str, text: str):
    """Construct a lark-oapi-shaped inbound event for an APPROVE/REJECT reply."""
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        chat_id="oc_test",
        message_id=f"om_in_{int(time.time() * 1000000)}",
        root_id=root_id,
        message_type="text",
        content=content,
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_founder"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=f"evt_{root_id}_{time.time_ns()}"),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def _build_org(root, test_settings):
    """Materialize an org with feishu_notifications enabled + a workspace dir
    for the dev_agent (so cwd-resolution in the run path doesn't 409)."""
    root.mkdir(parents=True)
    (root / "org").mkdir()
    (root / "org" / "config.yaml").write_text(
        "feishu_notifications:\n"
        "  enabled: true\n"
        "  provider: feishu\n"
        "  region: feishu\n"
        "  chat_id: oc_test\n"
        "  app_id: cli_test\n"
        "  app_secret: secret_test\n"
    )
    # Workspace root must exist for _resolve_cwd → not-cwd_missing.
    (root / "workspaces" / "dev_agent").mkdir(parents=True)

    from src.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is not None
    return org


def _seed_task_and_sr(org, *, task_id: str, sr_id: str):
    """Insert an active task assigned to dev_agent + a pending SR row."""
    from datetime import datetime, timezone
    from src.models import (
        ScriptInterpreter,
        ScriptRequestRecord,
        ScriptRequestStatus,
        TaskRecord,
        TaskStatus,
    )

    org.db.insert_task(TaskRecord(
        id=task_id, team="engineering",
        brief="needs founder to run gh pr close",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
    ))
    org.db.insert_script_request(ScriptRequestRecord(
        id=sr_id, task_id=task_id, agent_name="dev_agent",
        title="echo hi", rationale="smoke test",
        script_text="echo hello-from-sr",
        interpreter=ScriptInterpreter.BASH,
        cwd_hint=None,
        status=ScriptRequestStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))


def _start_listener(org, monkeypatch):
    """Mount the listener without spinning up a real WS thread."""
    from src.daemon.feishu_listener import (
        FeishuEventListener,
        maybe_start_feishu_listener_for_org,
    )
    monkeypatch.setattr(FeishuEventListener, "start", lambda self: None)

    class _State:
        is_idle = False

        class queue:
            @staticmethod
            def enqueue(slug: str, task_id: str) -> None:
                pass

    loop = asyncio.new_event_loop()
    maybe_start_feishu_listener_for_org(org, _State(), loop)
    assert org.feishu_listener is not None
    return loop


def _drive_until(loop, condition, *, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Drive the loop in small slices until ``condition()`` is truthy or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        loop.run_until_complete(asyncio.sleep(interval))
    return condition()


def _wait_for_message(fake_state, *, timeout: float = 5.0) -> bool:
    """Poll the fake-feishu state for an outbound push (delivered by daemon thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fake_state["messages"]:
            return True
        time.sleep(0.05)
    return bool(fake_state["messages"])


def _fake_completed_result():
    from src.daemon.scripts_runner import ScriptRunResult
    return ScriptRunResult(
        status="completed", exit_code=0, duration_ms=10,
        stdout_head="hello", stderr_head="",
        stdout_bytes=5, stderr_bytes=0,
        truncated_stdout=False, truncated_stderr=False,
        reason=None,
    )


def test_sr_submit_approve_runs_and_posts_follow_up(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    """APPROVE branch: SR transitions completed and a threaded follow-up arrives."""
    base_url, fake_state = fake_feishu

    import src.daemon.org_state as org_state_mod
    monkeypatch.setitem(org_state_mod._REGION_TO_DOMAIN, "feishu", base_url)

    org = _build_org(tmp_path / "orgs" / "test", test_settings)
    _seed_task_and_sr(org, task_id="TASK-1", sr_id="SR-001")

    # Stub out subprocess spawn — we don't want to execute arbitrary shell here.
    async def _fake_spawn(**_kwargs):
        return _fake_completed_result()
    monkeypatch.setattr(
        "src.daemon.routes.scripts._spawn_script", _fake_spawn,
    )

    # Fire the orchestrator push bridge (fire-and-forget; spawns a daemon thread
    # because we're calling from sync test context with no running loop).
    org.orchestrator.notify_script_submitted(
        sr_id="SR-001", agent="dev_agent", task_id="TASK-1",
        title="echo hi", rationale="smoke test",
        script_text="echo hello-from-sr",
        interpreter="bash", cwd_hint=None,
    )

    assert _wait_for_message(fake_state), "no Feishu push captured"
    push = fake_state["messages"][0]
    feishu_message_id = push["message_id"]
    payload = json.loads(push["body"]["content"])

    # Title / body sanity check.
    assert "SR-001" in payload["zh_cn"]["title"]
    assert "submitted" in payload["zh_cn"]["title"]
    body_text = " ".join(
        seg["text"] for line in payload["zh_cn"]["content"] for seg in line
    )
    assert "dev_agent" in body_text
    assert "echo hi" in body_text  # title appears in body
    assert "echo hello-from-sr" in body_text  # script preview

    # Mounted-row check: kind=script_request keyed by the message_id.
    nrow = org.db.get_escalation_notification(feishu_message_id)
    assert nrow is not None
    assert nrow["kind"] == "script_request"
    assert nrow["task_id"] == "SR-001"
    assert nrow["consumed_at"] is None

    # Now drive the listener with an APPROVE reply.
    loop = _start_listener(org, monkeypatch)
    try:
        event = _make_event(root_id=feishu_message_id, text="APPROVE\nlgtm")
        loop.run_until_complete(org.feishu_listener._handle_event_async(event))

        # The listener has invoked run_script_from_notification, which
        # spawned the _run_and_persist runner task on this loop. Drive the
        # loop until the SR row reaches a terminal status.
        def _terminal():
            row = org.db.get_script_request("SR-001")
            return row is not None and row.status.value in ("completed", "failed", "rejected")

        assert _drive_until(loop, _terminal, timeout=5.0), (
            f"SR did not reach terminal status; "
            f"current={org.db.get_script_request('SR-001').status.value}"
        )

        sr = org.db.get_script_request("SR-001")
        assert sr.status.value == "completed"
        assert sr.exit_code == 0
        assert sr.stdout_head == "hello"

        # Drain the follow-up notify_script_run_result task — created on
        # this loop via loop.create_task(...).
        assert _drive_until(
            loop, lambda: len(fake_state["thread_replies"]) >= 1, timeout=5.0,
        ), "no threaded follow-up captured"

        reply = fake_state["thread_replies"][0]
        assert reply["parent_message_id"] == feishu_message_id

        reply_payload = json.loads(reply["body"]["content"])
        reply_text = " ".join(
            seg["text"] for line in reply_payload["zh_cn"]["content"] for seg in line
        )
        # The reply title says "completed"; body should mention the SR and stdout head.
        assert "completed" in reply_payload["zh_cn"]["title"]
        assert "SR-001" in reply_payload["zh_cn"]["title"]
        assert "hello" in reply_text

        # Notification consumed.
        nrow_after = org.db.get_escalation_notification(feishu_message_id)
        assert nrow_after["consumed_at"] is not None
        assert nrow_after["consumed_by"] == "feishu-reply"

        # Audit row check (terminal result + reply processed).
        sr_audit = org.db.get_audit_logs("SR-001")
        actions = [r["action"] for r in sr_audit]
        assert "script_reply_processed" in actions
    finally:
        org.close()
        loop.close()


def test_sr_submit_reject_transitions_and_posts_no_follow_up(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    """REJECT branch: SR transitions to rejected, no terminal-result follow-up."""
    base_url, fake_state = fake_feishu

    import src.daemon.org_state as org_state_mod
    monkeypatch.setitem(org_state_mod._REGION_TO_DOMAIN, "feishu", base_url)

    org = _build_org(tmp_path / "orgs" / "test", test_settings)
    _seed_task_and_sr(org, task_id="TASK-2", sr_id="SR-001")

    # Belt-and-suspenders: even if reject path accidentally runs the script,
    # don't let it actually fork a subprocess.
    async def _fake_spawn(**_kwargs):
        return _fake_completed_result()
    monkeypatch.setattr(
        "src.daemon.routes.scripts._spawn_script", _fake_spawn,
    )

    org.orchestrator.notify_script_submitted(
        sr_id="SR-001", agent="dev_agent", task_id="TASK-2",
        title="echo hi", rationale="smoke test",
        script_text="echo hello-from-sr",
        interpreter="bash", cwd_hint=None,
    )

    assert _wait_for_message(fake_state), "no Feishu push captured"
    push = fake_state["messages"][0]
    feishu_message_id = push["message_id"]

    loop = _start_listener(org, monkeypatch)
    try:
        event = _make_event(root_id=feishu_message_id, text="REJECT\nnot needed")
        loop.run_until_complete(org.feishu_listener._handle_event_async(event))

        # Reject is synchronous — the row should be rejected immediately.
        sr = org.db.get_script_request("SR-001")
        assert sr.status.value == "rejected"
        assert sr.reject_reason == "not needed"

        nrow_after = org.db.get_escalation_notification(feishu_message_id)
        assert nrow_after["consumed_at"] is not None
        assert nrow_after["consumed_by"] == "feishu-reply"

        # No terminal-result follow-up on reject — give the loop a small
        # window to surface any erroneously-scheduled task.
        loop.run_until_complete(asyncio.sleep(0.2))
        assert fake_state["thread_replies"] == [], (
            f"unexpected follow-up after reject: {fake_state['thread_replies']}"
        )

        # script_rejected is logged against the parent task; script_reply_processed
        # against the SR id (the listener-side audit).
        task_actions = [r["action"] for r in org.db.get_audit_logs("TASK-2")]
        assert "script_rejected" in task_actions
        sr_actions = [r["action"] for r in org.db.get_audit_logs("SR-001")]
        assert "script_reply_processed" in sr_actions
    finally:
        org.close()
        loop.close()
