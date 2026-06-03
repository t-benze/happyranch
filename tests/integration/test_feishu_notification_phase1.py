"""End-to-end Phase 1: a max-steps escalation in run_step triggers a Feishu
send against our fake server."""
from __future__ import annotations

import json
import socket
import threading
import time

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


def test_escalation_via_run_step_sends_feishu_message(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, state = fake_feishu

    # Point the SDK domain to our fake server by patching _REGION_TO_DOMAIN
    # before OrgState.load builds the SDK client. The dict is populated at
    # module-import time from lark.FEISHU_DOMAIN, so patching the lark module
    # global alone wouldn't help — we need to patch the captured dict entry.
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
    )

    from runtime.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is not None

    from runtime.models import TaskRecord
    org.db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="Add Alipay support",
    ))
    org.db._conn.execute(
        "UPDATE tasks SET orchestration_step_count = ? WHERE id = ?",
        (test_settings.max_orchestration_steps, "TASK-1"),
    )
    org.db._conn.commit()

    org.orchestrator.run_step("TASK-1")

    # Drain the fire-and-forget daemon thread
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if state["messages"]:
            break
        time.sleep(0.05)

    assert state["token_calls"] >= 1
    assert len(state["messages"]) == 1
    msg = state["messages"][0]
    assert msg["receive_id_type"] == "chat_id"
    assert msg["body"]["receive_id"] == "oc_test"
    assert msg["body"]["msg_type"] == "post"
    payload = json.loads(msg["body"]["content"])
    assert "TASK-1" in payload["zh_cn"]["title"]
    body_text = " ".join(
        seg["text"] for line in payload["zh_cn"]["content"] for seg in line
    )
    assert "Add Alipay support" in body_text

    actions = [r["action"] for r in org.db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" in actions

    org.close()


def test_escalation_with_feishu_disabled_is_silent(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, state = fake_feishu

    import runtime.daemon.org_state as org_state_mod
    monkeypatch.setitem(org_state_mod._REGION_TO_DOMAIN, "feishu", base_url)

    root = tmp_path / "orgs" / "test"
    root.mkdir(parents=True)
    (root / "org").mkdir()
    (root / "org" / "config.yaml").write_text("session_timeout_seconds: 1800\n")

    from runtime.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is None

    from runtime.models import TaskRecord
    org.db.insert_task(TaskRecord(id="TASK-2", team="engineering", brief="b"))
    org.db._conn.execute(
        "UPDATE tasks SET orchestration_step_count = ? WHERE id = ?",
        (test_settings.max_orchestration_steps, "TASK-2"),
    )
    org.db._conn.commit()

    org.orchestrator.run_step("TASK-2")
    time.sleep(0.3)

    assert state["messages"] == []
    actions = [r["action"] for r in org.db.get_audit_logs("TASK-2")]
    assert "escalation" in actions
    assert "escalation_notify_sent" not in actions

    org.close()
