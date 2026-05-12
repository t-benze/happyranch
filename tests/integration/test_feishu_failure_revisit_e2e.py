"""End-to-end Phase 2: a task FAILED with notify_on_failure=true triggers a
Feishu send, then an inbound REVISIT reply spawns a new root task linked
via revisit_of_task_id."""
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


def _make_revisit_event(*, root_id: str, text: str = "REVISIT\nadd Service Class field"):
    """Construct a lark-oapi-shaped inbound event for the REVISIT reply.

    Matches the shape that test_feishu_listener_kind_routing.py uses —
    data.header.event_id + data.event.message + data.event.sender.
    """
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        chat_id="oc_test",
        message_id=f"om_reply_{int(time.time() * 1000)}",
        root_id=root_id,
        message_type="text",
        content=content,
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_founder"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=f"evt_{root_id}"),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def test_failure_notify_then_revisit_via_reply(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    """Full failure → REVISIT round-trip via fake Feishu."""
    base_url, fake_state = fake_feishu

    # Point the lark SDK at our fake Feishu server
    import src.daemon.org_state as org_state_mod
    monkeypatch.setitem(org_state_mod._REGION_TO_DOMAIN, "feishu", base_url)

    # Build the org with notify_on_failure=true and allow_dispatch=false
    root = tmp_path / "orgs" / "test"
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
        "  notify_on_failure: true\n"
    )

    from src.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is not None

    # Insert a FAILED task — bypassing run_step for this wiring test
    from src.models import TaskRecord, TaskStatus
    from datetime import datetime, timezone
    org.db.insert_task(TaskRecord(
        id="TASK-1", team="engineering",
        brief="self-block to test failure notify",
        assigned_agent="dev_agent",
        status=TaskStatus.FAILED,
    ))
    org.db.update_task(
        "TASK-1",
        note="self-blocked: cannot determine fare-tier mapping",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )

    # Trigger the failure notify (exercises orchestrator → notifier wiring)
    org.orchestrator.notify_failed(
        task_id="TASK-1", agent="dev_agent",
        failure_kind="self_blocked",
        failure_note="self-blocked: cannot determine fare-tier mapping",
        last_summary="delegated; agent returned blocked status",
    )

    # Drain the fire-and-forget daemon thread
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if fake_state["messages"]:
            break
        time.sleep(0.05)

    assert len(fake_state["messages"]) == 1
    msg = fake_state["messages"][0]
    feishu_message_id = msg["message_id"]
    body = msg["body"]
    payload = json.loads(body["content"])

    # Verify the outbound card content
    assert "FAILED" in payload["zh_cn"]["title"]
    body_text = " ".join(
        seg["text"] for line in payload["zh_cn"]["content"] for seg in line
    )
    assert "self_blocked" in body_text
    assert "REVISIT" in body_text

    # Verify the notification row was minted with kind='failure'
    nrow = org.db.get_escalation_notification(feishu_message_id)
    assert nrow is not None
    assert nrow["kind"] == "failure"
    assert nrow["task_id"] == "TASK-1"
    assert nrow["consumed_at"] is None

    # Verify audit row
    actions = [r["action"] for r in org.db.get_audit_logs("TASK-1")]
    assert "failure_notify_sent" in actions

    # === Now simulate the inbound REVISIT reply ===
    # Monkeypatch FeishuEventListener.start to a no-op so the factory doesn't
    # open a real WebSocket connection.
    from src.daemon.feishu_listener import FeishuEventListener
    monkeypatch.setattr(FeishuEventListener, "start", lambda self: None)

    # Build a minimal DaemonState stand-in with a no-op enqueue.
    enqueued: list[tuple[str, str]] = []

    class _State:
        is_idle = False

        class queue:
            @staticmethod
            def enqueue(slug: str, task_id: str) -> None:
                enqueued.append((slug, task_id))

    state = _State()
    loop = asyncio.new_event_loop()

    from src.daemon.feishu_listener import maybe_start_feishu_listener_for_org
    maybe_start_feishu_listener_for_org(org, state, loop)
    assert org.feishu_listener is not None

    # Construct a REVISIT reply event whose root_id points to the outbound
    # message we captured above.
    event = _make_revisit_event(root_id=feishu_message_id)

    # Drive the listener directly (skip the real WS plumbing).
    loop.run_until_complete(org.feishu_listener._handle_event_async(event))

    # Verify a new root task was spawned with revisit_of_task_id back to TASK-1.
    nonterminal_ids = org.db.get_nonterminal_task_ids()
    all_tasks = [org.db.get_task(tid) for tid in nonterminal_ids]
    revisit_task = next(
        (t for t in all_tasks if t is not None and t.revisit_of_task_id == "TASK-1"),
        None,
    )
    assert revisit_task is not None, "new revisit task should exist"
    assert revisit_task.brief == "self-block to test failure notify"
    assert revisit_task.team == "engineering"

    # Verify the task was enqueued.
    assert any(tid == revisit_task.id for _, tid in enqueued), (
        f"expected {revisit_task.id} to be enqueued; got {enqueued}"
    )

    # Verify the audit row carries the founder_note (logged on the new task's id).
    audit = org.db.get_audit_logs(revisit_task.id)
    revisit_via_reply = [r for r in audit if r["action"] == "failure_revisit_via_reply"]
    assert len(revisit_via_reply) == 1
    payload_audit = revisit_via_reply[0]["payload"]
    assert payload_audit["founder_note"] == "add Service Class field"
    assert payload_audit["predecessor_task_id"] == "TASK-1"

    # Verify the notification row is now consumed by feishu-reply.
    nrow_after = org.db.get_escalation_notification(feishu_message_id)
    assert nrow_after["consumed_at"] is not None
    assert nrow_after["consumed_by"] == "feishu-reply"

    org.close()
    loop.close()
