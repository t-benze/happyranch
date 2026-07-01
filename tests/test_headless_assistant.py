"""Tests for PR-1: TurnFrame vocabulary, HeadlessAdapter interface, registry,
AssistantConversation persistence, and HeadlessAssistantManager.

Tests the headless A-mode framework (PR-1 of THR-056 approach-A dock rebuild).
The PTY path is frozen — these tests are additive, covering only the new
headless symbols.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from runtime.daemon.headless_assistant import (
    TurnFrame,
    PermissionPosture,
    HeadlessAdapter,
    NullAdapter,
    register_adapter,
    get_adapter,
    AssistantConversation,
    HeadlessAssistantManager,
    run_headless_turn,
    _TurnRecord,
)


# ---------------------------------------------------------------------------
# TurnFrame vocabulary
# ---------------------------------------------------------------------------

class TestTurnFrameVocabulary:
    def test_turn_start(self) -> None:
        f = TurnFrame.turn_start(role="assistant")
        assert f.type == "turn_start"
        assert f.role == "assistant"
        assert f.text is None

    def test_text_delta(self) -> None:
        f = TurnFrame.text_delta(text="hello world")
        assert f.type == "text_delta"
        assert f.text == "hello world"

    def test_tool_call(self) -> None:
        f = TurnFrame.tool_call(name="bash", input={"command": "ls"})
        assert f.type == "tool_call"
        assert f.name == "bash"
        assert f.input == {"command": "ls"}

    def test_tool_result(self) -> None:
        f = TurnFrame.tool_result(name="bash", ok=True)
        assert f.type == "tool_result"
        assert f.name == "bash"
        assert f.ok is True

    def test_turn_end(self) -> None:
        f = TurnFrame.turn_end(usage={"input_tokens": 10, "output_tokens": 5})
        assert f.type == "turn_end"
        assert f.role == "assistant"
        assert f.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_status(self) -> None:
        f = TurnFrame.status(code="ready")
        assert f.type == "status"
        assert f.code == "ready"

        f2 = TurnFrame.status(code="session_closed", detail="dock disconnected")
        assert f2.detail == "dock disconnected"

    def test_error(self) -> None:
        f = TurnFrame.error(message="something went wrong")
        assert f.type == "error"
        assert f.message == "something went wrong"

    def test_json_round_trip(self) -> None:
        """TurnFrame serializes to/from JSON losslessly."""
        f = TurnFrame.tool_call(name="read", input={"path": "/tmp/foo"})
        raw = f.model_dump_json()
        reloaded = TurnFrame.model_validate_json(raw)
        assert reloaded.type == "tool_call"
        assert reloaded.name == "read"
        assert reloaded.input == {"path": "/tmp/foo"}

    def test_exclude_none(self) -> None:
        """model_dump(exclude_none=True) strips nulls. The save path uses this."""
        f = TurnFrame.text_delta(text="hi")
        d = f.model_dump(exclude_none=True)
        assert "role" not in d
        assert "name" not in d
        assert d == {"type": "text_delta", "text": "hi"}


# ---------------------------------------------------------------------------
# NullAdapter parsing
# ---------------------------------------------------------------------------

class TestNullAdapterParsing:
    def test_text_delta_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("TEXT_DELTA: hello")
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "hello"

    def test_tool_call_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("TOOL_CALL: bash")
        assert f is not None
        assert f.type == "tool_call"
        assert f.name == "bash"

    def test_tool_result_ok(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("TOOL_RESULT: bash:ok")
        assert f is not None
        assert f.type == "tool_result"
        assert f.name == "bash"
        assert f.ok is True

    def test_tool_result_not_ok(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("TOOL_RESULT: bash:error")
        assert f is not None
        assert f.type == "tool_result"
        assert f.name == "bash"
        assert f.ok is False

    def test_usage_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event('USAGE: {"input_tokens": 10}')
        assert f is not None
        assert f.type == "turn_end"
        assert f.usage == {"input_tokens": 10}

    def test_session_id_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("SESSION_ID: abc123")
        assert f is not None
        assert f.usage == {"session_id": "abc123"}

    def test_status_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("STATUS: working")
        assert f is not None
        assert f.type == "status"
        assert f.code == "working"

    def test_error_line(self) -> None:
        adapter = NullAdapter()
        f = adapter.parse_event("ERROR: something failed")
        assert f is not None
        assert f.type == "error"
        assert f.message == "something failed"

    def test_fallback_text_delta(self) -> None:
        """Unknown line format → text_delta."""
        adapter = NullAdapter()
        f = adapter.parse_event("just some text")
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "just some text"

    def test_empty_line_returns_none(self) -> None:
        adapter = NullAdapter()
        assert adapter.parse_event("") is None
        assert adapter.parse_event("   ") is None

    def test_extract_session_id(self) -> None:
        adapter = NullAdapter()
        f = TurnFrame.turn_end(usage={"session_id": "sess-abc"})
        assert adapter.extract_session_id(f) == "sess-abc"

    def test_extract_session_id_none(self) -> None:
        adapter = NullAdapter()
        f = TurnFrame.turn_end()
        assert adapter.extract_session_id(f) is None

        f2 = TurnFrame.turn_end(usage={"other": "data"})
        assert adapter.extract_session_id(f2) is None


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

class TestAdapterRegistry:
    def setup_method(self) -> None:
        # Use a unique key so tests don't interfere with the global registry.
        self._test_key = "test_adapter_reg"

    def test_register_and_lookup(self) -> None:
        adapter = NullAdapter()
        register_adapter(self._test_key, adapter)
        found = get_adapter(self._test_key)
        assert found is adapter

    def test_unknown_executor_returns_none(self) -> None:
        assert get_adapter("no_such_executor_xyz") is None

    def test_lookup_is_case_insensitive(self) -> None:
        adapter = NullAdapter()
        register_adapter("MyExecutor", adapter)
        assert get_adapter("myexecutor") is adapter
        assert get_adapter("MYEXECUTOR") is adapter

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            register_adapter("", NullAdapter())

    def test_null_adapter_is_registered_by_default(self) -> None:
        """The _null adapter is registered at import time."""
        found = get_adapter("_null")
        assert found is not None
        assert isinstance(found, NullAdapter)


# ---------------------------------------------------------------------------
# AssistantConversation persistence
# ---------------------------------------------------------------------------

class TestAssistantConversation:
    def test_fresh_workspace_load_returns_false(self, tmp_path: Path) -> None:
        conv = AssistantConversation(tmp_path)
        assert conv.load() is False
        assert conv.executor is None
        assert conv.turns == []

    def test_save_and_reload(self, tmp_path: Path) -> None:
        conv = AssistantConversation(tmp_path)
        conv.executor = "claude"
        conv.resume_session_id = "sess-001"

        turn = conv.begin_turn(turn_id="turn-1", prompt="hello")
        conv.append_frame(turn, TurnFrame.turn_start())
        conv.append_frame(turn, TurnFrame.text_delta(text="hi there"))
        conv.append_frame(turn, TurnFrame.turn_end(usage={"tokens": 42}))
        conv.finish_turn(turn, session_id="sess-002")
        conv.save()

        # Reload — survives "daemon restart".
        conv2 = AssistantConversation(tmp_path)
        assert conv2.load() is True
        assert conv2.executor == "claude"
        assert conv2.resume_session_id == "sess-002"
        assert len(conv2.turns) == 1

        loaded_turn = conv2.turns[0]
        assert loaded_turn.id == "turn-1"
        assert loaded_turn.prompt == "hello"
        assert loaded_turn.started_at is not None
        assert loaded_turn.finished_at is not None
        assert loaded_turn.session_id == "sess-002"
        assert len(loaded_turn.frames) == 3
        assert loaded_turn.frames[0].type == "turn_start"
        assert loaded_turn.frames[1].type == "text_delta"
        assert loaded_turn.frames[2].type == "turn_end"

    def test_multiple_turns(self, tmp_path: Path) -> None:
        conv = AssistantConversation(tmp_path)
        conv.executor = "pi"

        for i in range(3):
            turn = conv.begin_turn(turn_id=f"turn-{i}", prompt=f"msg {i}")
            conv.append_frame(turn, TurnFrame.text_delta(text=f"reply {i}"))
            conv.finish_turn(turn)
        conv.save()

        conv2 = AssistantConversation(tmp_path)
        conv2.load()
        assert len(conv2.turns) == 3
        for i, t in enumerate(conv2.turns):
            assert t.id == f"turn-{i}"
            assert t.prompt == f"msg {i}"
            # Each turn: text_delta (start + end frames added by run_headless_turn,
            # but here we only added text_delta).
            assert len(t.frames) == 1
            assert t.frames[0].text == f"reply {i}"

    def test_tool_call_in_conversation(self, tmp_path: Path) -> None:
        conv = AssistantConversation(tmp_path)
        turn = conv.begin_turn(turn_id="t1", prompt="read /tmp/foo")
        conv.append_frame(turn, TurnFrame.turn_start())
        conv.append_frame(turn, TurnFrame.tool_call(name="read", input={"path": "/tmp/foo"}))
        conv.append_frame(turn, TurnFrame.tool_result(name="read", ok=True))
        conv.append_frame(turn, TurnFrame.turn_end())
        conv.finish_turn(turn)
        conv.save()

        conv2 = AssistantConversation(tmp_path)
        conv2.load()
        frames = conv2.turns[0].frames
        assert frames[1].type == "tool_call"
        assert frames[1].name == "read"
        assert frames[1].input == {"path": "/tmp/foo"}
        assert frames[2].type == "tool_result"
        assert frames[2].ok is True

    def test_empty_conversation_saves_and_loads(self, tmp_path: Path) -> None:
        conv = AssistantConversation(tmp_path)
        conv.executor = "opencode"
        conv.save()

        conv2 = AssistantConversation(tmp_path)
        conv2.load()
        assert conv2.executor == "opencode"
        assert conv2.turns == []

    def test_atomic_save_does_not_corrupt(self, tmp_path: Path) -> None:
        """write-then-rename should prevent partial writes from corrupting reads."""
        conv = AssistantConversation(tmp_path)
        turn = conv.begin_turn(turn_id="t1", prompt="hello")
        conv.append_frame(turn, TurnFrame.text_delta(text="world"))
        conv.finish_turn(turn)
        conv.save()

        # Verify the file is valid JSON and reads correctly.
        raw = (tmp_path / "conversation.json").read_text()
        data = json.loads(raw)
        assert data["executor"] is None
        assert len(data["turns"]) == 1


# ---------------------------------------------------------------------------
# HeadlessAssistantManager
# ---------------------------------------------------------------------------

class TestHeadlessAssistantManager:
    @pytest.mark.asyncio
    async def test_get_conversation_returns_same_instance(self, tmp_path: Path) -> None:
        manager = HeadlessAssistantManager()
        conv1 = await manager.get_conversation(workspace=tmp_path)
        conv2 = await manager.get_conversation(workspace=tmp_path)
        assert conv1 is conv2

    @pytest.mark.asyncio
    async def test_close_workspace_persists(self, tmp_path: Path) -> None:
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)
        conv.executor = "claude"
        turn = conv.begin_turn(turn_id="t1", prompt="test")
        conv.append_frame(turn, TurnFrame.text_delta(text="response"))
        conv.finish_turn(turn)
        await manager.close_workspace(tmp_path)

        # After close, reloading from a fresh manager should recover the data.
        manager2 = HeadlessAssistantManager()
        conv2 = await manager2.get_conversation(workspace=tmp_path)
        assert conv2.executor == "claude"
        assert len(conv2.turns) == 1

    @pytest.mark.asyncio
    async def test_close_all_persists_all(self, tmp_path: Path) -> None:
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()

        manager = HeadlessAssistantManager()
        conv1 = await manager.get_conversation(workspace=ws1)
        conv1.executor = "claude"
        conv2 = await manager.get_conversation(workspace=ws2)
        conv2.executor = "pi"

        await manager.close_all()

        manager2 = HeadlessAssistantManager()
        conv1b = await manager2.get_conversation(workspace=ws1)
        conv2b = await manager2.get_conversation(workspace=ws2)
        assert conv1b.executor == "claude"
        assert conv2b.executor == "pi"

    @pytest.mark.asyncio
    async def test_finish_inflight_persists_turn(self, tmp_path: Path) -> None:
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        turn_record = conv.begin_turn(turn_id="turn-1", prompt="hi")
        await manager.start_inflight(
            workspace=tmp_path, turn_id="turn-1", prompt="hi",
            turn_record=turn_record,
        )
        f = TurnFrame.text_delta(text="hello")
        conv.append_frame(turn_record, f)
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f)
        await manager.finish_inflight(workspace=tmp_path, session_id="sess-42")

        conv2 = await manager.get_conversation(workspace=tmp_path)
        assert len(conv2.turns) == 1
        assert conv2.turns[0].session_id == "sess-42"
        assert conv2.resume_session_id == "sess-42"

    @pytest.mark.asyncio
    async def test_close_workspace_buffers_unfinished_inflight(self, tmp_path: Path) -> None:
        """close_workspace on an in-flight turn buffers frames to the log."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        turn_record = conv.begin_turn(turn_id="t1", prompt="hi")
        await manager.start_inflight(
            workspace=tmp_path, turn_id="t1", prompt="hi",
            turn_record=turn_record,
        )

        # Buffer two frames but don't call finish_inflight.
        f1 = TurnFrame.text_delta(text="part 1")
        f2 = TurnFrame.tool_call(name="bash", input={"cmd": "ls"})
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f1)
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f2)

        # Close workspace — should buffer frames.
        await manager.close_workspace(tmp_path)

        # Reload from a fresh manager.
        manager2 = HeadlessAssistantManager()
        conv2 = await manager2.get_conversation(workspace=tmp_path)
        assert len(conv2.turns) == 1
        assert len(conv2.turns[0].frames) == 2
        assert conv2.turns[0].frames[0].text == "part 1"
        assert conv2.turns[0].frames[1].name == "bash"

    @pytest.mark.asyncio
    async def test_finish_inflight_flushes_buffered_frames_to_turn_record(
        self, tmp_path: Path
    ) -> None:
        """finish_inflight MUST flush buffered_frames into the turn_record before
        save — otherwise a finished-in-background turn persists with EMPTY frames
        (reviewer finding §1).  This test only buffers frames via
        buffer_inflight_frame (does NOT also call conv.append_frame), so it
        fails on the unfixed code."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        turn_record = conv.begin_turn(turn_id="turn-flush", prompt="test")
        await manager.start_inflight(
            workspace=tmp_path,
            turn_id="turn-flush",
            prompt="test",
            turn_record=turn_record,
        )

        # Buffer frames ONLY via inflight — the "real" path taken by
        # run_headless_turn.  No conv.append_frame() call.
        f1 = TurnFrame.text_delta(text="streamed chunk 1")
        f2 = TurnFrame.tool_call(name="bash", input={"cmd": "ls"})
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f1)
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f2)

        await manager.finish_inflight(workspace=tmp_path, session_id="sess-post")

        # Reload from disk — finishing should have flushed all frames.
        conv2 = AssistantConversation(tmp_path)
        assert conv2.load()
        assert len(conv2.turns) == 1
        assert conv2.turns[0].id == "turn-flush"
        assert len(conv2.turns[0].frames) == 2, (
            "finish_inflight must flush buffered_frames into turn_record.frames"
        )
        assert conv2.turns[0].frames[0].text == "streamed chunk 1"
        assert conv2.turns[0].frames[1].name == "bash"
        assert conv2.turns[0].finished_at is not None, (
            "finish_inflight must stamp finished_at"
        )

    @pytest.mark.asyncio
    async def test_close_all_buffers_all_inflight(self, tmp_path: Path) -> None:
        manager = HeadlessAssistantManager()
        ws = tmp_path / "sub"
        ws.mkdir()
        conv = await manager.get_conversation(workspace=ws)

        turn_record = conv.begin_turn(turn_id="t1", prompt="q")
        await manager.start_inflight(
            workspace=ws, turn_id="t1", prompt="q",
            turn_record=turn_record,
        )
        await manager.buffer_inflight_frame(
            workspace=ws, frame=TurnFrame.text_delta(text="a"),
        )

        await manager.close_all()

        manager2 = HeadlessAssistantManager()
        conv2 = await manager2.get_conversation(workspace=ws)
        assert len(conv2.turns) == 1
        assert len(conv2.turns[0].frames) == 1
        assert conv2.turns[0].frames[0].text == "a"


# ---------------------------------------------------------------------------
# run_headless_turn
# ---------------------------------------------------------------------------

class TestRunHeadlessTurn:
    @pytest.mark.asyncio
    async def test_run_headless_turn_with_null_adapter(self, tmp_path: Path) -> None:
        """Integration: run_headless_turn with a stubbed adapter that echoes
        a few text_delta lines."""
        from unittest.mock import MagicMock

        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        # Stub adapter: build a dummy argv that runs echo.
        adapter = MagicMock(spec=HeadlessAdapter)
        adapter.build_turn_argv.return_value = [
            "echo", "TEXT_DELTA: hello", "\n", "TEXT_DELTA: world",
        ]
        # parse_event will be the real NullAdapter parser.
        adapter.parse_event = NullAdapter().parse_event
        adapter.extract_session_id = NullAdapter().extract_session_id

        frames: list[TurnFrame] = []
        async def collector(frame: TurnFrame) -> None:
            frames.append(frame)

        result = await run_headless_turn(
            manager=manager,
            adapter=adapter,
            workspace=tmp_path,
            prompt="test prompt",
            conversation=conv,
            permission_posture=PermissionPosture(),
            frame_sender=collector,
        )

        # We expect: turn_start, status:working, text_delta:hello, text_delta:world,
        # turn_end, status:ready
        types = [f.type for f in frames]
        assert types[0] == "turn_start"
        assert "status" in types  # working
        assert types.count("text_delta") >= 1
        assert "turn_end" in types

        # Conversation should be persisted with the turn.
        assert len(conv.turns) == 1
        assert conv.turns[0].prompt == "test prompt"

    @pytest.mark.asyncio
    async def test_turn_added_to_conversation(self, tmp_path: Path) -> None:
        """After a successful turn, the conversation should have the turn persisted."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        adapter = NullAdapter()

        frames: list[TurnFrame] = []
        async def collector(frame: TurnFrame) -> None:
            frames.append(frame)

        # We can't actually run the null adapter (it raises NotImplementedError
        # for build_turn_argv), so we test the manager flows directly.
        turn_record = conv.begin_turn(turn_id="test-turn", prompt="hello")
        await manager.start_inflight(
            workspace=tmp_path, turn_id="test-turn", prompt="hello",
            turn_record=turn_record,
        )

        f1 = TurnFrame.text_delta(text="response")
        conv.append_frame(turn_record, f1)
        await manager.buffer_inflight_frame(workspace=tmp_path, frame=f1)
        await manager.finish_inflight(workspace=tmp_path, session_id="sess-99")

        assert conv.resume_session_id == "sess-99"
        assert len(conv.turns) == 1
        assert conv.turns[0].id == "test-turn"
        assert conv.turns[0].prompt == "hello"

        # Reload survives.
        conv2 = AssistantConversation(tmp_path)
        assert conv2.load()
        assert conv2.resume_session_id == "sess-99"
        assert len(conv2.turns) == 1


# ---------------------------------------------------------------------------
# PermissionPosture placeholder
# ---------------------------------------------------------------------------

class TestPermissionPosture:
    def test_can_instantiate(self) -> None:
        posture = PermissionPosture()
        assert posture is not None

    def test_can_be_passed_to_build_turn_argv(self) -> None:
        adapter = NullAdapter()
        posture = PermissionPosture()
        # build_turn_argv raises NotImplementedError on null adapter —
        # this test just confirms the type is compatible.
        with pytest.raises(NotImplementedError):
            adapter.build_turn_argv(
                prompt="test",
                resume_id=None,
                permission_posture=posture,
            )
