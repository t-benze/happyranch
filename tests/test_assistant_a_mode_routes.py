"""Integration tests for the A-mode assistant route (
GET /assistant/a-mode/status + WebSocket /assistant/a-mode
).

Tests against a real FastAPI test client with a temporary runtime.
Uses HAPPYRANCH_DAEMON_HOME for token isolation (same pattern as
tests/daemon/conftest.py).
"""
from __future__ import annotations

import json
import time
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
    """Isolate daemon state under tmp_path and mint a token."""
    home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return home


def _setup_runtime(tmp_path: Path) -> RuntimeDir:
    """Create a minimal runtime with the assistant configured for _null executor."""
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
# GET /assistant/a-mode/status
# ---------------------------------------------------------------------------

class TestAModeStatus:
    def test_status_rejects_without_token(self, tmp_home: Path) -> None:
        """GET /assistant/a-mode/status MUST be bearer-gated (reviewer finding §2).
        Sibling /assistant/status uses require_token() — this route must
        follow the same pattern."""
        # Create a bare app with no Authorization header.
        rt = _setup_runtime(tmp_home)
        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        # Intentionally omit the Authorization header.
        resp = client.get("/api/v1/assistant/a-mode/status")
        assert resp.status_code in (401, 403), (
            f"expected 401/403 for unauthed request, got {resp.status_code}"
        )

    def test_status_available(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/assistant/a-mode/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["executor"] == "_null"

    def test_status_unavailable_for_unknown_executor(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        """When the assistant config names an executor with no adapter,
        status returns available=false."""
        rt = _setup_runtime(tmp_path)
        # Override config to an executor with no adapter.
        config = AssistantConfig(
            selected_executor="nonexistent_executor",
            selected_command="echo",
            selected_argv=["echo"],
            workspace_path=str(system_assistant_paths(rt.root).workspace),
        )
        save_assistant_config(rt.root, config)

        state = DaemonState.from_runtime(rt, Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        resp = client.get("/api/v1/assistant/a-mode/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "no A-mode adapter" in data.get("reason", "")


# ---------------------------------------------------------------------------
# WebSocket /assistant/a-mode
# ---------------------------------------------------------------------------

class TestAModeWebSocket:
    def test_ws_rejects_without_token(self, test_client: TestClient) -> None:
        # Make a client without auth headers.
        state = test_client.app.state.daemon
        app_noauth = create_app(state)
        client_noauth = TestClient(app_noauth)
        with pytest.raises(Exception):
            with client_noauth.websocket_connect("/api/v1/assistant/a-mode") as ws:
                ws.receive_text()

    def test_ws_accepts_with_token(self, test_client: TestClient) -> None:
        """WebSocket connects with bearer token in Authorization header."""
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] == "status"
            assert frame["code"] == "ready"

    def test_ws_accepts_with_subprotocol(self, test_client: TestClient) -> None:
        token = paths_mod.read_token()
        subprotocol = f"happyranch.bearer.{token}"
        # Build a client without the Authorization header pre-set.
        state = test_client.app.state.daemon
        app = create_app(state)
        client = TestClient(app)
        with client.websocket_connect(
            "/api/v1/assistant/a-mode",
            subprotocols=[subprotocol],
        ) as ws:
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] == "status"
            assert frame["code"] == "ready"

    def test_ws_echo_turn(self, test_client: TestClient) -> None:
        """Send a start message; the _null adapter's build_turn_argv raises
        NotImplementedError, so we should get an error frame (the route handles
        the exception gracefully)."""
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["code"] == "ready"

            ws.send_text(json.dumps({"type": "start", "text": "hello"}))
            msg = ws.receive_text()
            frame = json.loads(msg)
            # The null adapter's build_turn_argv raises NotImplementedError,
            # caught by run_headless_turn → error frame.
            assert frame["type"] in ("turn_start", "error")

    def test_ws_rejects_empty_prompt(self, test_client: TestClient) -> None:
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            ws.receive_text()  # drain ready
            ws.send_text(json.dumps({"type": "start", "text": "  "}))
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] == "error"
            assert "non-empty" in frame.get("message", "")

    def test_ws_rejects_invalid_json(self, test_client: TestClient) -> None:
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            ws.receive_text()  # drain ready
            ws.send_text("not json at all")
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] == "error"
            assert "invalid JSON" in frame.get("message", "")

    def test_ws_reconnect_replays_history(self, test_client: TestClient) -> None:
        """Reconnect MUST replay the persisted structured conversation log
        before status{ready} (reviewer finding §3 / PR-1 item 6 / design §4).

        We pre-populate the assistant-workspace conversation.json with a prior
        turn, then connect — the first frame should be history (containing the
        prior turn), THEN status{ready}."""
        import datetime

        state: DaemonState = test_client.app.state.daemon
        rt = state.runtime
        assert rt is not None
        from runtime.system_assistant import system_assistant_paths
        workspace = system_assistant_paths(rt.root).workspace

        # Pre-populate a conversation.json with one completed turn.
        conv_path = workspace / "conversation.json"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        prior_data = {
            "executor": "_null",
            "resume_session_id": "sess-prior",
            "workspace": str(workspace),
            "turns": [
                {
                    "id": "turn-prior",
                    "prompt": "what is 2+2?",
                    "frames": [
                        {"type": "turn_start", "role": "assistant"},
                        {"type": "text_delta", "text": "4"},
                        {"type": "turn_end", "role": "assistant"},
                    ],
                    "started_at": now,
                    "finished_at": now,
                    "session_id": "sess-prior",
                }
            ],
        }
        conv_path.write_text(json.dumps(prior_data, indent=2) + "\n")

        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            # First frame MUST be history (replay), NOT status.
            msg1 = ws.receive_text()
            frame1 = json.loads(msg1)
            assert frame1["type"] == "history", (
                f"reconnect must replay history first, got type={frame1.get('type')}"
            )
            assert len(frame1.get("turns", [])) == 1
            assert frame1["turns"][0]["id"] == "turn-prior"
            assert frame1["turns"][0]["prompt"] == "what is 2+2?"

            # Second frame MUST be status{ready}.
            msg2 = ws.receive_text()
            frame2 = json.loads(msg2)
            assert frame2["type"] == "status"
            assert frame2["code"] == "ready", (
                f"status{ready} must follow history replay, got code={frame2.get('code')}"
            )

    def test_ws_empty_history_no_replay(self, test_client: TestClient) -> None:
        """Fresh workspace (no turns) should NOT emit a history frame."""
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            msg = ws.receive_text()
            frame = json.loads(msg)
            # First frame should be directly status{ready}, NOT history.
            assert frame["type"] == "status"
            assert frame["code"] == "ready"

    def test_ws_close_message(self, test_client: TestClient) -> None:
        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            ws.receive_text()  # drain ready (or history if present)
            ws.send_text(json.dumps({"type": "close"}))
            msg = ws.receive_text()
            frame = json.loads(msg)
            assert frame["type"] == "status"
            assert frame["code"] == "session_closed"

    def test_ws_turn_receives_full_permission_posture(
        self, test_client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Finding 1 (HIGH): the A-mode WS path MUST pass run_headless_turn
        a FULL computed PermissionPosture (mirror allow_rules_for_agent),
        NOT a blank PermissionPosture() with claude_allowed_tools=None.

        The blank posture under-grants: claude receives only
        Bash(happyranch *) instead of the full worker allowlist.
        """
        from unittest.mock import AsyncMock

        posture_captured: list[object] = []

        async def fake_run_headless_turn(
            *, manager, adapter, workspace, prompt,
            conversation, permission_posture, frame_sender,
        ):
            posture_captured.append(permission_posture)
            return None

        monkeypatch.setattr(
            "runtime.daemon.routes.assistant_a_mode.run_headless_turn",
            fake_run_headless_turn,
        )

        with test_client.websocket_connect("/api/v1/assistant/a-mode") as ws:
            # Drain ready status.
            msg = ws.receive_text()
            frame = json.loads(msg)
            if frame.get("type") == "history":
                ws.receive_text()  # drain another (ready follows history)

            ws.send_text(json.dumps({"type": "start", "text": "test prompt"}))

            # Wait briefly for the async handler to process.
            time.sleep(0.1)

        assert len(posture_captured) == 1, (
            "run_headless_turn must be called exactly once"
        )
        posture = posture_captured[0]
        from runtime.daemon.headless_assistant import PermissionPosture
        assert isinstance(posture, PermissionPosture)
        # The allowlist must NOT be None (blank posture).
        assert posture.claude_allowed_tools is not None, (
            "claude_allowed_tools must NOT be None — blank posture under-grants"
        )
        # Must contain at least the happyranch baseline.
        assert "Bash(happyranch" in posture.claude_allowed_tools, (
            f"allowlist must contain happyranch baseline, got: {posture.claude_allowed_tools}"
        )
        # The mode must be set (defaults to "auto").
        assert posture.claude_permission_mode == "auto", (
            f"expected permission_mode 'auto', got: {posture.claude_permission_mode}"
        )
