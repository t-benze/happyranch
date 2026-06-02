"""End-to-end Phase 2 (dispatch branch): inbound top-level DISPATCH message
creates a task and sends a confirmation card. Mirrors the failure-revisit
e2e but for the dispatch surface."""
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


def _make_dispatch_event(*, text: str, event_id: str = "evt_dispatch_1"):
    """Top-level (no root_id) inbound DISPATCH event matching lark-oapi shape."""
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        chat_id="oc_test",
        message_id=f"om_inbound_{event_id}",
        root_id=None,  # ← top-level message
        message_type="text",
        content=content,
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_founder"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def _build_org(tmp_path, test_settings, *, allow_dispatch: bool, monkeypatch, base_url):
    """Build an OrgState with the given allow_dispatch flag, with the lark
    SDK pointed at the fake Feishu server."""
    import runtime.daemon.org_state as org_state_mod
    monkeypatch.setitem(org_state_mod._REGION_TO_DOMAIN, "feishu", base_url)

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
        f"  allow_dispatch: {str(allow_dispatch).lower()}\n"
    )
    # Need a teams.yaml so the dispatch route can resolve "engineering"
    (root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
    )
    # And an engineering_head agent definition so TeamsRegistry.manager_for_team works
    (root / "org" / "agents").mkdir()
    (root / "org" / "agents" / "engineering_head.md").write_text(
        "---\n"
        "name: engineering_head\n"
        "team: engineering\n"
        "role: manager\n"
        "executor: claude\n"
        "description: engineering team manager\n"
        "---\n"
        "you are the engineering head.\n"
    )

    from runtime.daemon.org_state import OrgState
    return OrgState.load(slug="test", root=root, settings=test_settings)


def _start_listener_without_websocket(org, state, loop, monkeypatch):
    """Construct + register the listener but monkeypatch start() so no real WS opens."""
    from runtime.daemon.feishu_listener import (
        FeishuEventListener, maybe_start_feishu_listener_for_org,
    )
    monkeypatch.setattr(FeishuEventListener, "start", lambda self: None)
    maybe_start_feishu_listener_for_org(org, state, loop)
    return org.feishu_listener


class _State:
    """Minimal DaemonState stand-in for the dispatch in-process helper."""
    def __init__(self):
        self.is_idle = False
        self._enqueued = []
        self.queue = SimpleNamespace(
            enqueue=lambda slug, tid: self._enqueued.append((slug, tid)),
        )


def test_dispatch_success_creates_task_and_sends_confirmation(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, fake_state = fake_feishu
    org = _build_org(
        tmp_path, test_settings,
        allow_dispatch=True, monkeypatch=monkeypatch, base_url=base_url,
    )
    state = _State()
    loop = asyncio.new_event_loop()
    listener = _start_listener_without_websocket(org, state, loop, monkeypatch)

    event = _make_dispatch_event(
        text="DISPATCH engineering\nfix the 503 issue on weekday mornings",
    )
    loop.run_until_complete(listener._handle_event_async(event))

    # A task was inserted in the DB
    new_task_ids = org.db.get_nonterminal_task_ids()
    assert len(new_task_ids) == 1
    task = org.db.get_task(new_task_ids[0])
    assert task.brief == "fix the 503 issue on weekday mornings"
    assert task.team == "engineering"

    # The task was enqueued
    assert state._enqueued == [("test", task.id)]

    # The confirmation card was sent to fake Feishu
    # (give the async send a moment, though it's local)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_state["messages"]:
            break
        time.sleep(0.05)

    assert len(fake_state["messages"]) == 1
    confirm = fake_state["messages"][0]
    confirm_payload = json.loads(confirm["body"]["content"])
    confirm_title = confirm_payload["zh_cn"]["title"]
    confirm_body = " ".join(
        seg["text"] for line in confirm_payload["zh_cn"]["content"] for seg in line
    )
    assert "dispatched" in confirm_title
    assert task.id in confirm_title
    assert "engineering" in confirm_body
    assert "fix the 503" in confirm_body

    # Audit row records the accepted dispatch
    audit = org.db.get_audit_logs(task.id)
    actions = [r["action"] for r in audit]
    assert "dispatch_via_feishu_accepted" in actions

    org.close()


def test_dispatch_unknown_team_sends_error_card(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, fake_state = fake_feishu
    org = _build_org(
        tmp_path, test_settings,
        allow_dispatch=True, monkeypatch=monkeypatch, base_url=base_url,
    )
    state = _State()
    loop = asyncio.new_event_loop()
    listener = _start_listener_without_websocket(org, state, loop, monkeypatch)

    event = _make_dispatch_event(
        text="DISPATCH nonexistent\nbrief", event_id="evt_unknown",
    )
    loop.run_until_complete(listener._handle_event_async(event))

    # No task created
    assert len(org.db.get_nonterminal_task_ids()) == 0

    # Error card sent
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if fake_state["messages"]:
            break
        time.sleep(0.05)

    assert len(fake_state["messages"]) == 1
    err = fake_state["messages"][0]
    err_payload = json.loads(err["body"]["content"])
    err_title = err_payload["zh_cn"]["title"]
    err_body = " ".join(
        seg["text"] for line in err_payload["zh_cn"]["content"] for seg in line
    )
    assert "rejected" in err_title.lower()
    assert "unknown team" in err_body
    assert "nonexistent" in err_body
    assert "engineering" in err_body  # listed as valid team

    org.close()


def test_dispatch_disabled_silently_drops(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, fake_state = fake_feishu
    org = _build_org(
        tmp_path, test_settings,
        allow_dispatch=False,  # ← OFF
        monkeypatch=monkeypatch, base_url=base_url,
    )
    state = _State()
    loop = asyncio.new_event_loop()
    listener = _start_listener_without_websocket(org, state, loop, monkeypatch)

    event = _make_dispatch_event(
        text="DISPATCH engineering\nbrief", event_id="evt_disabled",
    )
    loop.run_until_complete(listener._handle_event_async(event))

    # No task created
    assert len(org.db.get_nonterminal_task_ids()) == 0

    # No outbound card
    time.sleep(0.2)
    assert fake_state["messages"] == []

    org.close()
