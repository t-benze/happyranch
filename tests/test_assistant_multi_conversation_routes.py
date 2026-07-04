"""TDD tests for the 5 new HTTP routes on the A-mode surface.

RED-FIRST: run these BEFORE implementing the routes. They should FAIL because
the new endpoints don't exist yet.

Routes tested:
(a) GET  /assistant/a-mode/conversations      — list conversations
(b) POST /assistant/a-mode/conversations      — create new
(c) POST /assistant/a-mode/conversations/{id}/activate — switch/activate
(d) PATCH /assistant/a-mode/conversations/{id} — rename
(e) DELETE /assistant/a-mode/conversations/{id} — delete
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir
from runtime.system_assistant import (
    AssistantConfig,
    save_assistant_config,
    system_assistant_paths,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return home


def _setup_runtime(tmp_path: Path) -> RuntimeDir:
    rt = RuntimeDir.init(tmp_path / "runtime")
    sa_paths = system_assistant_paths(rt.root)
    sa_paths.root.mkdir(parents=True, exist_ok=True)
    sa_paths.workspace.mkdir(exist_ok=True)
    sa_paths.knowledge_dir.mkdir(exist_ok=True)
    sa_paths.learnings_dir.mkdir(exist_ok=True)
    sa_paths.logs_dir.mkdir(exist_ok=True)
    (sa_paths.workspace / "agent.yaml").write_text(
        "name: system_assistant\nexecutor: _null\nrepos: {}\n"
    )
    (sa_paths.workspace / "AGENTS.md").write_text("# test assistant\n")
    (sa_paths.learnings_dir / "_index.md").write_text("# learnings\n")
    (sa_paths.knowledge_dir / "README.md").write_text("# kb\n")
    config = AssistantConfig(
        selected_executor="_null",
        selected_command="echo",
        selected_argv=["echo", "hi"],
        workspace_path=str(sa_paths.workspace),
    )
    save_assistant_config(rt.root, config)
    return rt


@pytest.fixture
def test_client(tmp_home: Path, tmp_path: Path) -> TestClient:
    rt = _setup_runtime(tmp_path)
    state = DaemonState.from_runtime(rt, Settings())
    app = create_app(state)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return client


# ---------------------------------------------------------------------------
# (a) GET /assistant/a-mode/conversations — list conversations
# ---------------------------------------------------------------------------

class TestListConversations:
    def test_list_requires_token(self, tmp_home: Path) -> None:
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        resp = client.get("/api/v1/assistant/a-mode/conversations")
        assert resp.status_code in (401, 403)

    def test_list_returns_conversations(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        conv = data[0]
        assert "id" in conv
        assert "title" in conv
        assert "created_at" in conv
        assert "active" in conv

    def test_list_has_one_active(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        data = resp.json()
        active_count = sum(1 for c in data if c.get("active"))
        assert active_count == 1, "exactly one conversation must be active"


# ---------------------------------------------------------------------------
# (b) POST /assistant/a-mode/conversations — create new
# ---------------------------------------------------------------------------

class TestCreateConversation:
    def test_create_requires_token(self, tmp_home: Path) -> None:
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        resp = client.post("/api/v1/assistant/a-mode/conversations")
        assert resp.status_code in (401, 403)

    def test_create_new_conversation(self, test_client: TestClient) -> None:
        resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New conversation"
        assert data["active"] is True
        assert "id" in data
        assert "created_at" in data

    def test_create_makes_active(self, test_client: TestClient) -> None:
        """Creating a new conversation makes it the active one."""
        # Create a second conversation.
        resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        conv2 = resp.json()
        assert conv2["active"] is True

        # List should show conv2 as active.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        assert len(convs) >= 2
        active_convs = [c for c in convs if c["active"]]
        assert len(active_convs) == 1
        assert active_convs[0]["id"] == conv2["id"]


# ---------------------------------------------------------------------------
# (c) POST /assistant/a-mode/conversations/{id}/activate — switch/activate
# ---------------------------------------------------------------------------

class TestActivateConversation:
    def test_activate_requires_token(self, tmp_home: Path) -> None:
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        resp = client.post("/api/v1/assistant/a-mode/conversations/fake-id/activate")
        assert resp.status_code in (401, 403)

    def test_activate_switches_active(self, test_client: TestClient) -> None:
        # Get list, pick a non-active one.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        # Create a second conversation so there are at least 2.
        if len(convs) < 2:
            test_client.post("/api/v1/assistant/a-mode/conversations")
            list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
            convs = list_resp.json()

        # Find the non-active one.
        inactive = next((c for c in convs if not c.get("active")), None)
        assert inactive is not None, "need at least one non-active conversation"

        resp = test_client.post(
            f"/api/v1/assistant/a-mode/conversations/{inactive['id']}/activate"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify it's now active.
        list2 = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs2 = list2.json()
        activated = next(
            (c for c in convs2 if c["id"] == inactive["id"]), None
        )
        assert activated is not None
        assert activated["active"] is True

    def test_activate_nonexistent_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/v1/assistant/a-mode/conversations/nonexistent-id/activate"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# (d) PATCH /assistant/a-mode/conversations/{id} — rename
# ---------------------------------------------------------------------------

class TestRenameConversation:
    def test_rename_requires_token(self, tmp_home: Path) -> None:
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/assistant/a-mode/conversations/fake-id",
            json={"title": "New Title"},
        )
        assert resp.status_code in (401, 403)

    def test_rename_conversation(self, test_client: TestClient) -> None:
        # Create a new conversation.
        create_resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        conv = create_resp.json()

        resp = test_client.patch(
            f"/api/v1/assistant/a-mode/conversations/{conv['id']}",
            json={"title": "My Renamed Chat"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify title changed in list.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        renamed = next((c for c in convs if c["id"] == conv["id"]), None)
        assert renamed is not None
        assert renamed["title"] == "My Renamed Chat"

    def test_rename_nonexistent_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.patch(
            "/api/v1/assistant/a-mode/conversations/nonexistent-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_rename_missing_title_returns_422(self, test_client: TestClient) -> None:
        resp = test_client.patch(
            "/api/v1/assistant/a-mode/conversations/some-id",
            json={},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# (e) DELETE /assistant/a-mode/conversations/{id} — delete
# ---------------------------------------------------------------------------

class TestDeleteConversation:
    def test_delete_requires_token(self, tmp_home: Path) -> None:
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        resp = client.delete("/api/v1/assistant/a-mode/conversations/fake-id")
        assert resp.status_code in (401, 403)

    def test_delete_conversation(self, test_client: TestClient) -> None:
        # Create a conversation we can delete.
        create_resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        conv = create_resp.json()
        # Create another that stays active (so the one we delete isn't active).
        test_client.post("/api/v1/assistant/a-mode/conversations")

        resp = test_client.delete(
            f"/api/v1/assistant/a-mode/conversations/{conv['id']}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify it's gone from list.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        assert not any(c["id"] == conv["id"] for c in convs)

    def test_delete_active_activates_most_recent(self, test_client: TestClient) -> None:
        """Deleting the active conversation activates the most-recent remaining."""
        # Get the first conv (active).
        create_resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        active_conv = create_resp.json()
        assert active_conv["active"] is True

        # Delete the active one.
        resp = test_client.delete(
            f"/api/v1/assistant/a-mode/conversations/{active_conv['id']}"
        )
        assert resp.status_code == 200

        # Verify there is still at least one active.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        assert len(convs) >= 1
        active = [c for c in convs if c["active"]]
        assert len(active) == 1

    def test_delete_last_auto_creates_empty(self, test_client: TestClient) -> None:
        """Deleting the last conversation auto-creates an empty one."""
        # Delete all conversations except one.
        list_resp = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs = list_resp.json()
        for c in convs[:-1]:
            test_client.delete(f"/api/v1/assistant/a-mode/conversations/{c['id']}")

        # Delete the last one.
        list_resp2 = test_client.get("/api/v1/assistant/a-mode/conversations")
        last = list_resp2.json()[0]
        resp = test_client.delete(
            f"/api/v1/assistant/a-mode/conversations/{last['id']}"
        )
        assert resp.status_code == 200

        # Verify there is exactly one conversation (auto-created).
        list_resp3 = test_client.get("/api/v1/assistant/a-mode/conversations")
        convs3 = list_resp3.json()
        assert len(convs3) == 1
        assert convs3[0]["title"] == "New conversation"
        assert convs3[0]["active"] is True

    def test_delete_nonexistent_returns_200_idempotent(
        self, test_client: TestClient
    ) -> None:
        resp = test_client.delete(
            "/api/v1/assistant/a-mode/conversations/nonexistent-id"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# Delete-while-in-flight route-level test
# ---------------------------------------------------------------------------

class TestDeleteWhileInFlight:
    """FINDING 2 [HIGH] route-level: DELETE must return 409 when the
    conversation has an in-flight turn."""

    def test_delete_inflight_returns_409(self, test_client: TestClient) -> None:
        """DELETE /conversations/{id} returns 409 when in-flight turn exists."""
        import asyncio

        # Create a conversation and start an in-flight turn on it.
        create_resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        conv = create_resp.json()

        state: DaemonState = test_client.app.state.daemon
        manager = state.headless_assistant
        rt = state.runtime
        assert rt is not None
        from runtime.system_assistant import system_assistant_paths
        workspace = system_assistant_paths(rt.root).workspace

        async def start_inflight():
            conv_obj = await manager.get_conversation(
                workspace=workspace, conversation_id=conv["id"]
            )
            turn = conv_obj.begin_turn(turn_id="turn-inflight", prompt="test")
            await manager.start_inflight(
                workspace=workspace,
                conversation_id=conv["id"],
                turn_id="turn-inflight",
                prompt="test",
                turn_record=turn,
            )

        asyncio.run(start_inflight())

        # Now try to delete — expect 409.
        resp = test_client.delete(
            f"/api/v1/assistant/a-mode/conversations/{conv['id']}"
        )
        assert resp.status_code == 409, (
            f"expected 409 for delete-while-in-flight, got {resp.status_code}"
        )
        detail = resp.json().get("detail", "")
        assert "in-flight" in detail.lower() or "in flight" in detail.lower(), (
            f"409 detail must mention in-flight, got: {detail}"
        )


# ---------------------------------------------------------------------------
# WS: conversation_id in start message
# ---------------------------------------------------------------------------

class TestWSConversationId:
    def test_ws_start_message_with_conversation_id(
        self, test_client: TestClient
    ) -> None:
        """The A-mode WS accepts an optional conversation_id in the start message
        to target a specific conversation."""
        # Create a second conversation.
        create_resp = test_client.post("/api/v1/assistant/a-mode/conversations")
        conv = create_resp.json()

        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            # Drain initial ready/history frames.
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                if frame.get("type") == "status" and frame.get("code") == "ready":
                    break

            # Send a start message targeting the specific conversation.
            ws.send_text(json.dumps({
                "type": "start",
                "text": "test prompt",
                "conversation_id": conv["id"],
            }))
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] in ("turn_start", "error")  # null adapter raises

    def test_ws_targeted_conversation_replays_correct_history(
        self, test_client: TestClient
    ) -> None:
        """FINDING 1 [HIGH]: when a start message targets a non-active
        conversation via conversation_id, the WS handler must replay the
        TARGETED conversation's history BEFORE running the new turn.

        Seeds two conversations with distinct prior turns, keeps one active,
        starts a WS turn targeting the OTHER id, and asserts the history frame
        corresponds to the TARGETED id."""
        import asyncio

        state: DaemonState = test_client.app.state.daemon
        rt = state.runtime
        assert rt is not None
        from runtime.system_assistant import system_assistant_paths
        workspace = system_assistant_paths(rt.root).workspace
        from runtime.daemon.headless_assistant import (
            HeadlessAssistantManager,
            TurnFrame,
        )

        async def seed_conversations():
            manager = HeadlessAssistantManager()
            # Conv A (active) — will have a distinct turn.
            conv_a = await manager.get_conversation(workspace=workspace)
            conv_a_id = conv_a.id
            turn_a = conv_a.begin_turn(turn_id="turn-a", prompt="prompt from A")
            conv_a.append_frame(turn_a, TurnFrame.text_delta(text="response from A"))
            conv_a.finish_turn(turn_a)
            store = await manager._get_store(workspace)
            store.save_conversation(conv_a)

            # Conv B (non-active) — distinct turn.
            conv_b = await manager.create_conversation(workspace=workspace)
            conv_b_id = conv_b.id
            turn_b = conv_b.begin_turn(turn_id="turn-b", prompt="prompt from B")
            conv_b.append_frame(turn_b, TurnFrame.text_delta(text="response from B"))
            conv_b.finish_turn(turn_b)
            store.save_conversation(conv_b)

            # Switch active back to A (so B is NOT active).
            await manager.switch_conversation(
                workspace=workspace, conversation_id=conv_a_id
            )
            return conv_a_id, conv_b_id

        conv_a_id, conv_b_id = asyncio.run(seed_conversations())
        assert conv_b_id != conv_a_id

        # Connect WS — it will replay the ACTIVE conversation (A) history.
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            # Drain initial frames — active conversation (A) has history.
            for _ in range(10):
                msg = ws.receive_text()
                frame = json.loads(msg)
                if frame.get("type") == "status" and frame.get("code") == "ready":
                    break

            # Now send a start message targeting conversation B (non-active).
            ws.send_text(json.dumps({
                "type": "start",
                "text": "new prompt for B",
                "conversation_id": conv_b_id,
            }))

            # Collect frames after the start — a history frame for B
            # should appear BEFORE the turn_start / error / turn_end for
            # the new turn.  The null adapter raises on build_turn_argv
            # so we get an error frame, then the handler waits for the
            # next client message.
            post_start_frames: list[dict] = []
            for _ in range(20):
                try:
                    msg = ws.receive_text()
                except Exception:
                    break
                frame = json.loads(msg)
                post_start_frames.append(frame)
                # Stop when we see an error (null adapter raises) or
                # turn_end.
                if frame.get("type") in ("error", "turn_end"):
                    break

            # Close the session to avoid deadlock (the null adapter
            # errors out and the handler waits for the next message).
            try:
                ws.send_text(json.dumps({"type": "close"}))
                ws.receive_text()  # drain session_closed
            except Exception:
                pass

            # FIND the history frame among post-start frames.
            # It must exist and contain B's turns.
            history_frames = [
                f for f in post_start_frames if f.get("type") == "history"
            ]
            assert len(history_frames) >= 1, (
                f"Expected a 'history' frame for targeted conversation B, "
                f"got post-start frames: {[f['type'] for f in post_start_frames]}"
            )
            history = history_frames[0]
            turns = history.get("turns", [])
            assert len(turns) >= 1, "history frame must contain at least one turn"
            # The turn must be from conversation B (prompt is distinct).
            assert turns[0]["prompt"] == "prompt from B", (
                f"history must replay B's turns, got prompt='{turns[0].get('prompt')}'"
            )
