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
# OpenCodeAdapter (PR-2)
# ---------------------------------------------------------------------------

# JSONL fixtures captured from opencode 1.14.31 `run --format json` output.
OPC_STEP_START = (
    '{"type":"step_start","sessionID":"ses_abc123",'
    '"part":{"id":"prt_1","messageID":"msg_1",'
    '"sessionID":"ses_abc123","type":"step_start"}}'
)
OPC_TEXT_EVENT = (
    '{"type":"text","sessionID":"ses_abc123",'
    '"part":{"id":"prt_2","messageID":"msg_1",'
    '"sessionID":"ses_abc123","type":"text",'
    '"text":"Hello from opencode"}}'
)
OPC_TOOL_USE_EVENT = (
    '{"type":"tool_use","sessionID":"ses_abc123",'
    '"part":{"type":"tool_use","tool":"bash",'
    '"callID":"call_1","state":{"command":"ls"},'
    '"id":"prt_3","sessionID":"ses_abc123","messageID":"msg_1"}}'
)
OPC_STEP_FINISH = (
    '{"type":"step_finish","sessionID":"ses_abc123",'
    '"part":{"id":"prt_4","reason":"stop",'
    '"messageID":"msg_1","sessionID":"ses_abc123",'
    '"type":"step_finish","tokens":{"input":100,"output":50,'
    '"total":150,"reasoning":0,"cache":{"write":0,"read":0}}}}'
)


class TestOpenCodeAdapter:
    """Tests for OpenCodeAdapter: event parsing, session-id extraction, argv building."""

    @pytest.fixture
    def adapter(self):
        from runtime.daemon.headless_assistant import OpenCodeAdapter
        return OpenCodeAdapter()

    def test_parse_text_event(self, adapter) -> None:
        f = adapter.parse_event(OPC_TEXT_EVENT)
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "Hello from opencode"

    def test_parse_tool_use_event(self, adapter) -> None:
        f = adapter.parse_event(OPC_TOOL_USE_EVENT)
        assert f is not None
        assert f.type == "tool_call"
        assert f.name == "bash"
        assert f.input == {"command": "ls"}

    def test_step_start_returns_none(self, adapter) -> None:
        """step_start events are internal; no dock frame."""
        assert adapter.parse_event(OPC_STEP_START) is None

    def test_step_finish_returns_none(self, adapter) -> None:
        """step_finish is informative but not dock-facing."""
        assert adapter.parse_event(OPC_STEP_FINISH) is None

    def test_empty_line_returns_none(self, adapter) -> None:
        assert adapter.parse_event("") is None
        assert adapter.parse_event("   ") is None

    def test_non_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event("not json at all") is None

    def test_invalid_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event('{"type":"text", broken') is None

    def test_session_id_extracted_from_event(self, adapter) -> None:
        """Session id is tracked internally from events during parse_event."""
        assert adapter.extract_session_id(None) is None
        adapter.parse_event(OPC_STEP_START)  # carries sessionID=ses_abc123
        sid = adapter.extract_session_id(None)
        assert sid == "ses_abc123"

    def test_build_turn_argv_basic(self, adapter) -> None:
        argv = adapter.build_turn_argv(
            prompt="hello",
            resume_id=None,
            permission_posture=PermissionPosture(),
        )
        assert argv[0] == "opencode"
        assert "run" in argv
        assert "--format" in argv
        assert "json" in argv
        assert "--dangerously-skip-permissions" in argv
        assert argv[-1] == "hello"

    def test_build_turn_argv_with_resume(self, adapter) -> None:
        argv = adapter.build_turn_argv(
            prompt="continue please",
            resume_id="ses_xyz",
            permission_posture=PermissionPosture(),
        )
        assert "-s" in argv
        idx = argv.index("-s")
        assert argv[idx + 1] == "ses_xyz"

    def test_extract_session_id_returns_none_before_events(self, adapter) -> None:
        """Before any events are parsed, extract_session_id returns None."""
        assert adapter.extract_session_id(TurnFrame.text_delta(text="x")) is None


# ---------------------------------------------------------------------------
# PiAdapter (PR-2)
# ---------------------------------------------------------------------------

# JSONL fixtures captured from pi 0.80.2 `-p --mode json` output.
PI_SESSION_EVENT = (
    '{"type":"session","version":3,'
    '"id":"019f2072-55ca-78cb-a6dd-00ed63a1650a",'
    '"timestamp":"2026-07-02T01:29:51.818Z","cwd":"/tmp"}'
)
PI_TEXT_DELTA_EVENT = (
    '{"type":"message_update",'
    '"assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":1,"delta":"hello",'
    '"partial":{"role":"assistant","content":[{"type":"text","text":"hello"}]},'
    '"message":{"role":"assistant","content":[{"type":"text","text":"hello"}]}}}'
)
PI_THINKING_DELTA_EVENT = (
    '{"type":"message_update",'
    '"assistantMessageEvent":{"type":"thinking_delta",'
    '"contentIndex":0,"delta":"Let me think",'
    '"partial":{"role":"assistant","content":[{"type":"thinking",'
    '"thinking":"Let me think"}]},"message":{}}}'
)
PI_TEXT_START_EVENT = (
    '{"type":"message_update",'
    '"assistantMessageEvent":{"type":"text_start",'
    '"contentIndex":1,"partial":{"role":"assistant",'
    '"content":[{"type":"text","text":""}]},"message":{}}}'
)
PI_MESSAGE_END_EVENT = (
    '{"type":"message_end",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"hello"}],'
    '"usage":{"input":100,"output":5,"cacheRead":0,"cacheWrite":0,'
    '"totalTokens":105}}}'
)
PI_TURN_END_EVENT = (
    '{"type":"turn_end",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"hello"}],'
    '"usage":{"input":100,"output":5,"cacheRead":0,"cacheWrite":0,'
    '"totalTokens":105}}}'
)


class TestPiAdapter:
    """Tests for PiAdapter: event parsing, session-id extraction, argv building."""

    @pytest.fixture
    def adapter(self):
        from runtime.daemon.headless_assistant import PiAdapter
        return PiAdapter()

    def test_parse_text_delta_event(self, adapter) -> None:
        f = adapter.parse_event(PI_TEXT_DELTA_EVENT)
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "hello"

    def test_parse_thinking_delta_returns_none(self, adapter) -> None:
        """thinking_delta events are internal reasoning; no dock frame."""
        assert adapter.parse_event(PI_THINKING_DELTA_EVENT) is None

    def test_parse_text_start_returns_none(self, adapter) -> None:
        """text_start is just a structural event."""
        assert adapter.parse_event(PI_TEXT_START_EVENT) is None

    def test_session_event_yields_session_id(self, adapter) -> None:
        """The initial 'session' event carries the pi session id."""
        assert adapter.extract_session_id(None) is None
        adapter.parse_event(PI_SESSION_EVENT)
        sid = adapter.extract_session_id(None)
        assert sid == "019f2072-55ca-78cb-a6dd-00ed63a1650a"

    def test_empty_line_returns_none(self, adapter) -> None:
        assert adapter.parse_event("") is None
        assert adapter.parse_event("   ") is None

    def test_non_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event("just some text") is None

    def test_invalid_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event('{"type":"text_delta", broken') is None

    def test_unknown_event_type_returns_none(self, adapter) -> None:
        assert adapter.parse_event('{"type":"unknown","x":1}') is None

    def test_message_end_returns_none(self, adapter) -> None:
        """message_end carries usage but the dock doesn't need a frame from it."""
        assert adapter.parse_event(PI_MESSAGE_END_EVENT) is None

    def test_turn_end_returns_none(self, adapter) -> None:
        """turn_end is handled by run_headless_turn infrastructure."""
        assert adapter.parse_event(PI_TURN_END_EVENT) is None

    def test_build_turn_argv_basic(self, adapter) -> None:
        argv = adapter.build_turn_argv(
            prompt="hello",
            resume_id=None,
            permission_posture=PermissionPosture(),
        )
        assert argv[0] == "pi"
        assert "-p" in argv
        assert "--mode" in argv
        assert "json" in argv
        assert "-c" in argv  # default: continue last session
        assert argv[-1] == "hello"

    def test_build_turn_argv_with_resume(self, adapter) -> None:
        argv = adapter.build_turn_argv(
            prompt="continue please",
            resume_id="019f2072-sess-uuid",
            permission_posture=PermissionPosture(),
        )
        assert "--session-id" in argv
        idx = argv.index("--session-id")
        assert argv[idx + 1] == "019f2072-sess-uuid"
        assert "-c" not in argv  # session-id takes precedence over -c

    def test_build_turn_argv_pi_accepted_uncontained(self, adapter) -> None:
        """Pi is accepted uncontained per THR-056 design §3.

        No sandbox, no permission flags, no approval — pi -p runs as the
        invoking user with full access.  Documented in PR notes."""
        argv = adapter.build_turn_argv(
            prompt="test",
            resume_id=None,
            permission_posture=PermissionPosture(),
        )
        containment_flags = [
            "--sandbox", "--allowedTools", "--permission-mode",
            "--dangerously-skip-permissions", "--approval",
        ]
        for flag in containment_flags:
            assert flag not in argv, f"pi adapter must not add containment flag {flag}"


# ---------------------------------------------------------------------------
# Adapter registry — PR-2 adapters are registered
# ---------------------------------------------------------------------------

class TestAdapterRegistryPR2:
    def test_opencode_adapter_is_registered(self) -> None:
        from runtime.daemon.headless_assistant import get_adapter, OpenCodeAdapter
        adapter = get_adapter("opencode")
        assert adapter is not None, "opencode adapter must be registered at import time"
        assert isinstance(adapter, OpenCodeAdapter)

    def test_pi_adapter_is_registered(self) -> None:
        from runtime.daemon.headless_assistant import get_adapter, PiAdapter
        adapter = get_adapter("pi")
        assert adapter is not None, "pi adapter must be registered at import time"
        assert isinstance(adapter, PiAdapter)

    def test_lookup_is_case_insensitive(self) -> None:
        from runtime.daemon.headless_assistant import get_adapter, OpenCodeAdapter, PiAdapter
        assert isinstance(get_adapter("OPENCODE"), OpenCodeAdapter)
        assert isinstance(get_adapter("Pi"), PiAdapter)


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

    def test_claude_allowed_tools_defaults_none(self) -> None:
        posture = PermissionPosture()
        assert posture.claude_allowed_tools is None

    def test_claude_permission_mode_defaults_auto(self) -> None:
        posture = PermissionPosture()
        assert posture.claude_permission_mode == "auto"

    def test_claude_fields_can_be_set(self) -> None:
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *) Bash(git *)",
            claude_permission_mode="acceptEdits",
        )
        assert posture.claude_allowed_tools == "Bash(happyranch *) Bash(git *)"
        assert posture.claude_permission_mode == "acceptEdits"


# ---------------------------------------------------------------------------
# ClaudeAdapter — argv builder (PR-3)
# ---------------------------------------------------------------------------

class TestClaudeAdapterArgv:
    """Tests for ClaudeAdapter.build_turn_argv — permission posture mirroring
    the org-agent allow_rules machinery exactly as ClaudeExecutor does."""

    @pytest.fixture
    def adapter(self):
        from runtime.daemon.headless_assistant import ClaudeAdapter
        return ClaudeAdapter()

    def test_basic_argv_contains_required_flags(self, adapter) -> None:
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *) Bash(git *)",
            claude_permission_mode="auto",
        )
        argv = adapter.build_turn_argv(
            prompt="hello",
            resume_id=None,
            permission_posture=posture,
        )
        assert argv[0] == "claude"
        assert "-p" in argv
        assert argv[argv.index("-p") + 1] == "hello"
        assert "--output-format" in argv
        assert "stream-json" in argv
        assert "--verbose" in argv
        assert "--permission-mode" in argv
        idx = argv.index("--permission-mode")
        assert argv[idx + 1] == "auto"
        assert "--allowedTools" in argv
        idx2 = argv.index("--allowedTools")
        assert argv[idx2 + 1] == "Bash(happyranch *) Bash(git *)"
        # Prompt is right after -p, not at the end.\n        assert argv.index("hello") == argv.index("-p") + 1

    def test_resume_id_adds_resume_flag(self, adapter) -> None:
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *)",
        )
        argv = adapter.build_turn_argv(
            prompt="continue please",
            resume_id="sess-abc-123",
            permission_posture=posture,
        )
        assert "--resume" in argv
        idx = argv.index("--resume")
        assert argv[idx + 1] == "sess-abc-123"
        # Prompt is right after -p, --resume is at the end.
        assert argv[argv.index("-p") + 1] == "continue please"
        assert argv[-1] == "sess-abc-123"

    def test_no_resume_omits_resume_flag(self, adapter) -> None:
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *)",
        )
        argv = adapter.build_turn_argv(
            prompt="hello",
            resume_id=None,
            permission_posture=posture,
        )
        assert "--resume" not in argv

    def test_empty_allowed_tools_defaults_to_happyranch_baseline(self, adapter) -> None:
        """When claude_allowed_tools is empty/None, baseline Bash(happyranch *) is used."""
        posture = PermissionPosture()  # claude_allowed_tools defaults to None
        argv = adapter.build_turn_argv(
            prompt="test",
            resume_id=None,
            permission_posture=posture,
        )
        idx = argv.index("--allowedTools")
        assert argv[idx + 1] == "Bash(happyranch *)"

    def test_empty_permission_mode_defaults_to_auto(self, adapter) -> None:
        posture = PermissionPosture()  # claude_permission_mode defaults to "auto"
        argv = adapter.build_turn_argv(
            prompt="test",
            resume_id=None,
            permission_posture=posture,
        )
        idx = argv.index("--permission-mode")
        assert argv[idx + 1] == "auto"

    def test_never_dangerously_skip_permissions(self, adapter) -> None:
        """Claude adapter must NEVER use --dangerously-skip-permissions.

        KB entry assistant-headless-permission-postures is explicit:
        NOT --dangerously-skip-permissions.  Mirror the allow_rules machinery.
        """
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *)",
        )
        argv = adapter.build_turn_argv(
            prompt="test",
            resume_id=None,
            permission_posture=posture,
        )
        assert "--dangerously-skip-permissions" not in argv
        assert "--allow-dangerously-skip-permissions" not in argv

    def test_allowlist_mirrors_cli_format(self, adapter) -> None:
        """The --allowedTools string uses Bash(<prefix> *) format (cli=True),
        NOT Bash(<prefix>:*) format (settings.json).  This mirrors
        workspace_adapters._format_allow_rule with cli=True."""
        posture = PermissionPosture(
            claude_allowed_tools="Bash(happyranch *) Bash(git *) Bash(gh *)",
        )
        argv = adapter.build_turn_argv(
            prompt="test",
            resume_id=None,
            permission_posture=posture,
        )
        idx = argv.index("--allowedTools")
        tools = argv[idx + 1]
        # Every rule must use the CLI separator " " (space), not ":" (settings.json).
        assert "Bash(happyranch *)" in tools
        assert "Bash(git *)" in tools
        assert "Bash(gh *)" in tools
        assert "Bash(happyranch:*)" not in tools  # wrong separator


# ---------------------------------------------------------------------------
# ClaudeAdapter — stream-json event parsing (PR-3)
# ---------------------------------------------------------------------------

# Real event fixtures captured from claude 2.1.193 -p --output-format stream-json --verbose.
CLAUDE_SYSTEM_INIT = (
    '{"type":"system","subtype":"init",'
    '"session_id":"sess-abc-123","model":"claude-opus-4-8",'
    '"tools":["Task","Bash","Read","Edit","Write"],'
    '"uuid":"u1"}'
)
CLAUDE_SYSTEM_HOOK = (
    '{"type":"system","subtype":"hook_started",'
    '"hook_name":"SessionStart:startup",'
    '"session_id":"sess-abc-123","uuid":"u2"}'
)
CLAUDE_ASSISTANT_TEXT = (
    '{"type":"assistant",'
    '"message":{"model":"claude-opus-4-8","id":"msg_01",'
    '"type":"message","role":"assistant",'
    '"content":[{"type":"text","text":"Hello world"}],'
    '"stop_reason":null,"usage":{"input_tokens":100,"output_tokens":5}},'
    '"session_id":"sess-abc-123","uuid":"u3"}'
)
CLAUDE_ASSISTANT_MULTI_CONTENT = (
    '{"type":"assistant",'
    '"message":{"model":"claude-opus-4-8","id":"msg_02",'
    '"type":"message","role":"assistant",'
    '"content":['
    '{"type":"text","text":"Let me check that."},'
    '{"type":"tool_use","id":"toolu_01","name":"Bash","input":{"command":"ls"}}'
    '],"stop_reason":"tool_use",'
    '"usage":{"input_tokens":200,"output_tokens":10}},'
    '"session_id":"sess-abc-123","uuid":"u4"}'
)
CLAUDE_USER_TOOL_RESULT = (
    '{"type":"user",'
    '"message":{"role":"user","content":['
    '{"type":"tool_result","tool_use_id":"toolu_01",'
    '"content":[{"type":"text","text":"file1.txt\\nfile2.txt"}]}'
    ']},"session_id":"sess-abc-123","uuid":"u5"}'
)
CLAUDE_RESULT_SUCCESS = (
    '{"type":"result","subtype":"success","is_error":false,'
    '"duration_ms":2000,"num_turns":1,"result":"Hello world",'
    '"stop_reason":"end_turn",'
    '"session_id":"sess-abc-123",'
    '"total_cost_usd":0.01,'
    '"usage":{"input_tokens":100,"output_tokens":5,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0,'
    '"service_tier":"standard"},'
    '"modelUsage":{"claude-opus-4-8":{"inputTokens":100,"outputTokens":5}},'
    '"permission_denials":[],"uuid":"u6"}'
)
CLAUDE_RESULT_ERROR = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"duration_ms":500,"num_turns":1,'
    '"session_id":"sess-abc-123",'
    '"errors":["some error"],"uuid":"u7"}'
)
CLAUDE_RATE_LIMIT = (
    '{"type":"rate_limit_event",'
    '"rate_limit_info":{"status":"allowed","resetsAt":1783056000},'
    '"session_id":"sess-abc-123","uuid":"u8"}'
)


class TestClaudeAdapterParsing:
    """Tests for ClaudeAdapter.parse_event — stream-json events → TurnFrame."""

    @pytest.fixture
    def adapter(self):
        from runtime.daemon.headless_assistant import ClaudeAdapter
        return ClaudeAdapter()

    # ---- system events ----

    def test_parse_system_init_extracts_session_id(self, adapter) -> None:
        """The 'system' subtype 'init' event carries the session id.
        parse_event returns None (no dock frame), but extract_session_id
        picks up the tracked id."""
        assert adapter.extract_session_id(TurnFrame.text_delta(text="x")) is None
        adapter.parse_event(CLAUDE_SYSTEM_INIT)
        sid = adapter.extract_session_id(TurnFrame.text_delta(text="x"))
        assert sid == "sess-abc-123"

    def test_parse_system_hook_returns_none(self, adapter) -> None:
        assert adapter.parse_event(CLAUDE_SYSTEM_HOOK) is None

    # ---- assistant events ----

    def test_parse_assistant_text(self, adapter) -> None:
        f = adapter.parse_event(CLAUDE_ASSISTANT_TEXT)
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "Hello world"

    def test_parse_assistant_multi_content(self, adapter) -> None:
        """Assistant message with text + tool_use content blocks
        emits both text_delta and tool_call frames.

        NOTE: parse_event returns one frame per call.  Multi-content
        messages require the caller (run_headless_turn) to call
        parse_event once per JSONL line.  Each line is a single
        event, so we test the single-event case here.*"""
        f = adapter.parse_event(CLAUDE_ASSISTANT_MULTI_CONTENT)
        # The first text block is emitted; tool_use is available in
        # the same message but parse_event returns the first text_delta.
        assert f is not None
        assert f.type == "text_delta"
        assert f.text == "Let me check that."

    def test_assistant_event_tracks_session_id(self, adapter) -> None:
        adapter.parse_event(CLAUDE_ASSISTANT_TEXT)
        sid = adapter.extract_session_id(TurnFrame.text_delta(text="x"))
        assert sid == "sess-abc-123"

    # ---- user events ----

    def test_parse_user_tool_result(self, adapter) -> None:
        f = adapter.parse_event(CLAUDE_USER_TOOL_RESULT)
        assert f is not None
        assert f.type == "tool_result"
        # The tool_result carries the tool_use_id and result content.
        assert f.name is not None
        assert f.ok is True

    # ---- result events ----

    def test_parse_result_success_extracts_session_id(self, adapter) -> None:
        """The 'result' event is parsed for session_id extraction
        but returns None (no dock frame) — the adapter tracks
        session_id internally for extract_session_id."""
        adapter.parse_event(CLAUDE_RESULT_SUCCESS)
        sid = adapter.extract_session_id(TurnFrame.text_delta(text="x"))
        assert sid == "sess-abc-123"

    def test_parse_result_error_tracks_session_id(self, adapter) -> None:
        adapter.parse_event(CLAUDE_RESULT_ERROR)
        sid = adapter.extract_session_id(TurnFrame.text_delta(text="x"))
        assert sid == "sess-abc-123"

    # ---- skippable events ----

    def test_rate_limit_event_returns_none(self, adapter) -> None:
        assert adapter.parse_event(CLAUDE_RATE_LIMIT) is None

    def test_empty_line_returns_none(self, adapter) -> None:
        assert adapter.parse_event("") is None
        assert adapter.parse_event("   ") is None

    def test_non_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event("just some text") is None

    def test_invalid_json_returns_none(self, adapter) -> None:
        assert adapter.parse_event('{"type":"assistant", broken') is None

    def test_unknown_event_type_returns_none(self, adapter) -> None:
        assert adapter.parse_event('{"type":"unknown","x":1}') is None


# ---------------------------------------------------------------------------
# ClaudeAdapter — adapter registry (PR-3)
# ---------------------------------------------------------------------------

class TestClaudeAdapterRegistry:
    def test_claude_adapter_is_registered(self) -> None:
        from runtime.daemon.headless_assistant import get_adapter, ClaudeAdapter
        adapter = get_adapter("claude")
        assert adapter is not None, "claude adapter must be registered at import time"
        assert isinstance(adapter, ClaudeAdapter)

    def test_lookup_is_case_insensitive(self) -> None:
        from runtime.daemon.headless_assistant import get_adapter, ClaudeAdapter
        assert isinstance(get_adapter("CLAUDE"), ClaudeAdapter)
        assert isinstance(get_adapter("Claude"), ClaudeAdapter)
