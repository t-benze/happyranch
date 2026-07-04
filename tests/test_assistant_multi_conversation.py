"""TDD tests for THR-056 STEP-A: multi-conversation model + routes.

Tests the multi-conversation assistant store (conversations/<id>.json +
conversations/index.json), legacy migration, re-keyed HeadlessAssistantManager,
and the 5 new HTTP routes on the A-mode surface.

RED-FIRST: run these BEFORE implementing the model/routes.  They should
FAIL because the new symbols/paths don't exist yet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from runtime.daemon.headless_assistant import (
    TurnFrame,
    PermissionPosture,
    HeadlessAssistantManager,
    AssistantConversation,
)

# ---------------------------------------------------------------------------
# AssistantConversationStore — file-based multi-conversation persistence
# ---------------------------------------------------------------------------

class TestAssistantConversationStore:
    """Tests for the new conversations/ store under the assistant workspace."""

    def test_store_directory_structure(self, tmp_path: Path) -> None:
        """The store creates conversations/ directory with index + per-conversation files."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        conversations_dir = tmp_path / "conversations"
        assert not conversations_dir.exists(), "store must not create dirs on init"

        # Load triggers directory creation if not exists.
        store.load()
        assert conversations_dir.is_dir()
        assert (conversations_dir / "index.json").exists()

    def test_create_first_conversation(self, tmp_path: Path) -> None:
        """Creating a conversation in a fresh store returns it with correct fields."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        # load() auto-creates one conversation in a fresh store.
        store.load()
        convs = store.list_conversations()
        assert len(convs) == 1
        conv = convs[0]
        assert conv.id is not None
        assert conv.title == "New conversation"
        assert conv.created_at is not None
        assert conv.executor is None
        assert conv.resume_session_id is None
        assert conv.turns == []
        assert store.active_id == conv.id
        assert conv.active is True

        # Verify on disk.
        conv_path = tmp_path / "conversations" / f"{conv.id}.json"
        assert conv_path.exists()
        data = json.loads(conv_path.read_text())
        assert data["id"] == conv.id
        assert data["title"] == "New conversation"

    def test_load_persisted_conversations(self, tmp_path: Path) -> None:
        """Conversations survive daemon restart (fresh store load)."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()  # creates 1 auto conversation
        initial = store.list_conversations()
        assert len(initial) == 1
        # Rename the auto-created one, then add more.
        store.rename_conversation(initial[0].id, "Chat 0")
        c1 = store.create_conversation()
        c1.title = "Chat 1"
        store.save_conversation(c1)
        c2 = store.create_conversation()
        c2.title = "Chat 2"
        store.save_conversation(c2)
        assert len(store.list_conversations()) == 3

        # Fresh store load.
        store2 = AssistantConversationStore(tmp_path)
        store2.load()
        assert store2.active_id == c2.id  # newest-first
        convs = store2.list_conversations()
        assert len(convs) == 3
        # Newest first.
        assert convs[0].id == c2.id
        assert convs[0].title == "Chat 2"
        assert convs[1].id == c1.id
        assert convs[1].title == "Chat 1"

    def test_switch_active(self, tmp_path: Path) -> None:
        """Switching active conversation persists to index."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)
        c2 = store.create_conversation()
        store.save_conversation(c2)
        assert store.active_id == c2.id

        store.set_active(c1.id)
        assert store.active_id == c1.id

        # Persisted in index.
        store2 = AssistantConversationStore(tmp_path)
        store2.load()
        assert store2.active_id == c1.id

    def test_rename_conversation(self, tmp_path: Path) -> None:
        """Renaming a conversation persists to its file."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)

        store.rename_conversation(c1.id, "Renamed Chat")
        assert c1.title == "Renamed Chat"

        # Persisted.
        store2 = AssistantConversationStore(tmp_path)
        store2.load()
        conv = store2.get_conversation(c1.id)
        assert conv is not None
        assert conv.title == "Renamed Chat"

    def test_delete_conversation(self, tmp_path: Path) -> None:
        """Deleting a non-active conversation removes it and its file."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)
        c2 = store.create_conversation()
        store.save_conversation(c2)
        assert len(store.list_conversations()) == 3

        # Delete c1 (not active — c2 is active as newest).
        store.delete_conversation(c1.id)
        assert len(store.list_conversations()) == 2
        remaining_ids = [c.id for c in store.list_conversations()]
        assert c2.id in remaining_ids
        assert c1.id not in remaining_ids

        # File removed.
        conv_path = tmp_path / "conversations" / f"{c1.id}.json"
        assert not conv_path.exists()

    def test_delete_active_activates_most_recent(self, tmp_path: Path) -> None:
        """Deleting the active conversation activates the most-recent remaining."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)
        c2 = store.create_conversation()
        store.save_conversation(c2)
        store.set_active(c1.id)

        store.delete_conversation(c1.id)
        assert store.active_id == c2.id

    def test_delete_last_auto_creates_empty(self, tmp_path: Path) -> None:
        """Deleting the LAST conversation auto-creates an empty one and makes it active."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)

        store.delete_conversation(c1.id)
        convs = store.list_conversations()
        assert len(convs) == 1, "must auto-create one empty conversation"
        assert convs[0].title == "New conversation"
        assert convs[0].turns == []
        assert store.active_id == convs[0].id

    def test_get_conversation_by_id(self, tmp_path: Path) -> None:
        """get_conversation returns the correct conversation or None."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        c1 = store.create_conversation()
        store.save_conversation(c1)

        found = store.get_conversation(c1.id)
        assert found is not None
        assert found.id == c1.id

        assert store.get_conversation("nonexistent") is None

    def test_conversation_with_turns_survives_restart(self, tmp_path: Path) -> None:
        """A conversation with turns persists and reloads correctly."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        conv = store.create_conversation()
        conv.executor = "claude"
        conv.resume_session_id = "sess-123"

        turn = conv.begin_turn(turn_id="turn-1", prompt="hello")
        conv.append_frame(turn, TurnFrame.turn_start())
        conv.append_frame(turn, TurnFrame.text_delta(text="hi"))
        conv.append_frame(turn, TurnFrame.turn_end())
        conv.finish_turn(turn, session_id="sess-456")
        store.save_conversation(conv)

        # Fresh load.
        store2 = AssistantConversationStore(tmp_path)
        store2.load()
        conv2 = store2.get_conversation(conv.id)
        assert conv2 is not None
        assert conv2.executor == "claude"
        assert conv2.resume_session_id == "sess-456"
        assert len(conv2.turns) == 1
        assert conv2.turns[0].prompt == "hello"
        assert len(conv2.turns[0].frames) == 3

    def test_list_conversations_includes_active_flag(self, tmp_path: Path) -> None:
        """list_conversations returns id, title, created_at, active flag."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        # load() creates one auto-conversation (active).
        c1 = store.create_conversation()
        c1.title = "First"
        store.save_conversation(c1)

        convs = store.list_conversations()
        assert len(convs) == 2
        # c1 (newest) should be active.
        newest = convs[0]
        assert newest.id == c1.id
        assert newest.title == "First"
        assert newest.created_at is not None
        assert newest.active is True
        # The older auto-created one is not active.
        assert convs[1].active is False


# ---------------------------------------------------------------------------
# Legacy migration — adopt conversation.json as first conversation
# ---------------------------------------------------------------------------

class TestLegacyConversationMigration:
    """Tests for adopting the legacy single conversation.json as the first
    conversation in the new conversations/ store."""

    def test_migration_adopts_legacy_file(self, tmp_path: Path) -> None:
        """When legacy conversation.json exists and no conversations/ store
        exists yet, load() adopts it as the first conversation."""
        from runtime.daemon.headless_assistant import AssistantConversationStore

        # Write a legacy conversation.json.
        legacy_data = {
            "executor": "claude",
            "resume_session_id": "sess-legacy",
            "workspace": str(tmp_path),
            "turns": [
                {
                    "id": "turn-legacy",
                    "prompt": "what is 2+2?",
                    "frames": [
                        {"type": "turn_start", "role": "assistant"},
                        {"type": "text_delta", "text": "4"},
                        {"type": "turn_end", "role": "assistant"},
                    ],
                    "started_at": "2026-06-01T00:00:00Z",
                    "finished_at": "2026-06-01T00:00:01Z",
                    "session_id": "sess-legacy",
                }
            ],
        }
        legacy_path = tmp_path / "conversation.json"
        legacy_path.write_text(json.dumps(legacy_data, indent=2) + "\n")

        # Now load the store — should auto-migrate.
        store = AssistantConversationStore(tmp_path)
        store.load()

        convs = store.list_conversations()
        assert len(convs) == 1, "migration should create exactly one conversation"
        conv = convs[0]
        assert conv.title == "Conversation 1"
        assert conv.executor == "claude"
        assert conv.resume_session_id == "sess-legacy"
        assert len(conv.turns) == 1
        assert conv.turns[0].id == "turn-legacy"
        assert conv.turns[0].prompt == "what is 2+2?"
        assert conv.active is True
        assert store.active_id == conv.id

        # Legacy file should still exist (don't delete — keep for safety).
        assert legacy_path.exists()

    def test_migration_only_runs_once(self, tmp_path: Path) -> None:
        """If conversations/ already exists, the legacy file is NOT re-migrated."""
        from runtime.daemon.headless_assistant import AssistantConversationStore

        # First load: creates conversations/ store.
        legacy_data = {
            "executor": "opencode",
            "resume_session_id": "sess-leg",
            "workspace": str(tmp_path),
            "turns": [],
        }
        (tmp_path / "conversation.json").write_text(
            json.dumps(legacy_data, indent=2) + "\n"
        )
        store = AssistantConversationStore(tmp_path)
        store.load()
        assert len(store.list_conversations()) == 1

        # Now modify the legacy file (simulating a new turn added to old format).
        legacy_data["turns"].append({"id": "turn-2", "prompt": "new turn", "frames": []})
        (tmp_path / "conversation.json").write_text(
            json.dumps(legacy_data, indent=2) + "\n"
        )

        # Re-load — should NOT create a second conversation.
        store2 = AssistantConversationStore(tmp_path)
        store2.load()
        convs = store2.list_conversations()
        assert len(convs) == 1, "migration must not re-run when store already exists"
        assert len(convs[0].turns) == 0  # original empty turns from first migration

    def test_migration_no_legacy_file_is_noop(self, tmp_path: Path) -> None:
        """If no conversation.json exists, load() creates a fresh store with
        one empty conversation."""
        from runtime.daemon.headless_assistant import AssistantConversationStore
        store = AssistantConversationStore(tmp_path)
        store.load()
        convs = store.list_conversations()
        assert len(convs) == 1
        assert convs[0].title == "New conversation"
        assert convs[0].turns == []
        assert store.active_id == convs[0].id


# ---------------------------------------------------------------------------
# HeadlessAssistantManager — re-keyed per (workspace, conversation-id)
# ---------------------------------------------------------------------------

class TestHeadlessAssistantManagerMultiConversation:
    """Tests for the re-keyed HeadlessAssistantManager that keys conversations
    per (workspace, conversation-id) instead of workspace alone."""

    @pytest.mark.asyncio
    async def test_get_conversation_returns_active(self, tmp_path: Path) -> None:
        """get_conversation returns the ACTIVE conversation (not keyed by workspace alone)."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)
        assert conv is not None
        assert conv.active is True

    @pytest.mark.asyncio
    async def test_get_conversation_by_id(self, tmp_path: Path) -> None:
        """get_conversation with explicit conversation_id returns a specific conversation."""
        manager = HeadlessAssistantManager()
        # First, get active (creates the store + first conversation).
        active = await manager.get_conversation(workspace=tmp_path)
        active_id = active.id

        # Create another conversation.
        conv2 = await manager.create_conversation(workspace=tmp_path)
        assert conv2.id != active_id

        # Get by id.
        found = await manager.get_conversation(workspace=tmp_path, conversation_id=active_id)
        assert found is not None
        assert found.id == active_id

    @pytest.mark.asyncio
    async def test_in_flight_keyed_by_conversation_id(self, tmp_path: Path) -> None:
        """In-flight turns are keyed by (workspace, conversation_id), so two
        conversations can have simultaneous in-flight turns without collision."""
        manager = HeadlessAssistantManager()
        conv1 = await manager.get_conversation(workspace=tmp_path)
        conv2 = await manager.create_conversation(workspace=tmp_path)
        await manager.switch_conversation(workspace=tmp_path, conversation_id=conv2.id)

        # Start in-flight on conv1 (which is now NOT the active one).
        turn1 = conv1.begin_turn(turn_id="turn-conv1", prompt="hello from 1")
        await manager.start_inflight(
            workspace=tmp_path, conversation_id=conv1.id,
            turn_id="turn-conv1", prompt="hello from 1",
            turn_record=turn1,
        )

        # Start in-flight on conv2 (the active one).
        turn2 = conv2.begin_turn(turn_id="turn-conv2", prompt="hello from 2")
        await manager.start_inflight(
            workspace=tmp_path, conversation_id=conv2.id,
            turn_id="turn-conv2", prompt="hello from 2",
            turn_record=turn2,
        )

        # Both in-flight.
        inflight1 = await manager._get_inflight(workspace=tmp_path, conversation_id=conv1.id)
        inflight2 = await manager._get_inflight(workspace=tmp_path, conversation_id=conv2.id)
        assert inflight1 is not None
        assert inflight2 is not None
        assert inflight1.turn_id == "turn-conv1"
        assert inflight2.turn_id == "turn-conv2"

    @pytest.mark.asyncio
    async def test_close_workspace_buffers_all_inflight(self, tmp_path: Path) -> None:
        """close_workspace buffers in-flight turns for ALL conversations in the workspace."""
        manager = HeadlessAssistantManager()
        conv1 = await manager.get_conversation(workspace=tmp_path)
        conv2 = await manager.create_conversation(workspace=tmp_path)

        # Inflight on conv1.
        turn1 = conv1.begin_turn(turn_id="t1", prompt="q1")
        await manager.start_inflight(
            workspace=tmp_path, conversation_id=conv1.id,
            turn_id="t1", prompt="q1", turn_record=turn1,
        )
        await manager.buffer_inflight_frame(
            workspace=tmp_path, conversation_id=conv1.id,
            frame=TurnFrame.text_delta(text="a1"),
        )

        # Inflight on conv2.
        turn2 = conv2.begin_turn(turn_id="t2", prompt="q2")
        await manager.start_inflight(
            workspace=tmp_path, conversation_id=conv2.id,
            turn_id="t2", prompt="q2", turn_record=turn2,
        )
        await manager.buffer_inflight_frame(
            workspace=tmp_path, conversation_id=conv2.id,
            frame=TurnFrame.text_delta(text="a2"),
        )

        await manager.close_workspace(tmp_path)

        # Reload — both conversations should have their buffered frames.
        manager2 = HeadlessAssistantManager()
        conv1b = await manager2.get_conversation(workspace=tmp_path, conversation_id=conv1.id)
        conv2b = await manager2.get_conversation(workspace=tmp_path, conversation_id=conv2.id)
        assert len(conv1b.turns) == 1
        assert len(conv1b.turns[0].frames) == 1
        assert conv1b.turns[0].frames[0].text == "a1"
        assert len(conv2b.turns) == 1
        assert len(conv2b.turns[0].frames) == 1
        assert conv2b.turns[0].frames[0].text == "a2"

    @pytest.mark.asyncio
    async def test_finish_inflight_keyed_by_conversation(self, tmp_path: Path) -> None:
        """finish_inflight persists to the correct conversation."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        turn_record = conv.begin_turn(turn_id="turn-finish", prompt="test")
        await manager.start_inflight(
            workspace=tmp_path, conversation_id=conv.id,
            turn_id="turn-finish", prompt="test", turn_record=turn_record,
        )
        frame = TurnFrame.text_delta(text="response")
        conv.append_frame(turn_record, frame)
        await manager.buffer_inflight_frame(
            workspace=tmp_path, conversation_id=conv.id, frame=frame,
        )
        await manager.finish_inflight(
            workspace=tmp_path, conversation_id=conv.id,
            session_id="sess-finish",
        )

        # Reload — turn is persisted with session_id.
        manager2 = HeadlessAssistantManager()
        conv2 = await manager2.get_conversation(workspace=tmp_path, conversation_id=conv.id)
        assert len(conv2.turns) == 1
        assert conv2.turns[0].session_id == "sess-finish"

    @pytest.mark.asyncio
    async def test_switch_conversation_changes_active(self, tmp_path: Path) -> None:
        """Switching conversation changes the active one returned by get_conversation."""
        manager = HeadlessAssistantManager()
        conv1 = await manager.get_conversation(workspace=tmp_path)
        conv2 = await manager.create_conversation(workspace=tmp_path)

        # Default active is conv2 (newest).
        active = await manager.get_conversation(workspace=tmp_path)
        assert active.id == conv2.id

        # Switch to conv1.
        await manager.switch_conversation(workspace=tmp_path, conversation_id=conv1.id)
        active2 = await manager.get_conversation(workspace=tmp_path)
        assert active2.id == conv1.id

    @pytest.mark.asyncio
    async def test_close_all_persists_all_conversations(self, tmp_path: Path) -> None:
        """close_all persists all conversations across all workspaces."""
        ws = tmp_path
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=ws)
        conv.executor = "claude"
        turn = conv.begin_turn(turn_id="t1", prompt="test")
        conv.append_frame(turn, TurnFrame.text_delta(text="response"))
        conv.finish_turn(turn)

        await manager.close_all()

        manager2 = HeadlessAssistantManager()
        conv2 = await manager2.get_conversation(workspace=ws)
        assert conv2.executor == "claude"
        assert len(conv2.turns) == 1

    @pytest.mark.asyncio
    async def test_auto_title_from_first_user_message(self, tmp_path: Path) -> None:
        """A new conversation's title is set from its first user message."""
        manager = HeadlessAssistantManager()
        conv = await manager.create_conversation(workspace=tmp_path)
        # Initially "New conversation".
        assert conv.title == "New conversation"

        # After first user message, auto-title.
        await manager.set_conversation_title_from_first_message(
            workspace=tmp_path, conversation_id=conv.id,
            message="Help me fix the token counter bug",
        )
        conv2 = await manager.get_conversation(workspace=tmp_path, conversation_id=conv.id)
        # Title should be the first message, truncated if too long.
        assert "Help me fix" in conv2.title or "token counter" in conv2.title

    @pytest.mark.asyncio
    async def test_auto_title_fallback_to_new_conversation(self, tmp_path: Path) -> None:
        """If the first user message is empty/whitespace, fallback to 'New conversation'."""
        manager = HeadlessAssistantManager()
        conv = await manager.create_conversation(workspace=tmp_path)
        await manager.set_conversation_title_from_first_message(
            workspace=tmp_path, conversation_id=conv.id, message="   ",
        )
        conv2 = await manager.get_conversation(workspace=tmp_path, conversation_id=conv.id)
        assert conv2.title == "New conversation"


# ---------------------------------------------------------------------------
# Delete-while-in-flight — must reject with 409, not silently drop turn
# ---------------------------------------------------------------------------

class TestDeleteWhileInFlight:
    """FINDING 2 [HIGH]: deleting a conversation that has an in-flight turn
    must NOT silently orphan the turn.  Either reject with 409 or explicitly
    cancel/flush.  This test asserts the 409 path."""

    @pytest.mark.asyncio
    async def test_delete_inflight_rejects_at_manager_level(
        self, tmp_path: Path
    ) -> None:
        """delete_conversation raises an error when an in-flight turn exists
        for the target conversation."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        # Start an in-flight turn.
        turn_record = conv.begin_turn(turn_id="turn-active", prompt="in flight")
        await manager.start_inflight(
            workspace=tmp_path,
            conversation_id=conv.id,
            turn_id="turn-active",
            prompt="in flight",
            turn_record=turn_record,
        )

        # Attempting to delete should raise.
        with pytest.raises(RuntimeError, match="in-flight"):
            await manager.delete_conversation(
                workspace=tmp_path, conversation_id=conv.id
            )

        # In-flight turn should still exist (not silently dropped).
        inflight = await manager._get_inflight(
            workspace=tmp_path, conversation_id=conv.id
        )
        assert inflight is not None, (
            "in-flight turn must still exist after rejected delete"
        )

    @pytest.mark.asyncio
    async def test_delete_after_finish_inflight_succeeds(
        self, tmp_path: Path
    ) -> None:
        """After finish_inflight, delete_conversation succeeds normally."""
        manager = HeadlessAssistantManager()
        conv = await manager.get_conversation(workspace=tmp_path)

        # Start and finish an in-flight turn.
        turn_record = conv.begin_turn(turn_id="turn-done", prompt="done")
        await manager.start_inflight(
            workspace=tmp_path,
            conversation_id=conv.id,
            turn_id="turn-done",
            prompt="done",
            turn_record=turn_record,
        )
        await manager.finish_inflight(
            workspace=tmp_path, conversation_id=conv.id
        )

        # Now delete should succeed without error.
        await manager.delete_conversation(
            workspace=tmp_path, conversation_id=conv.id
        )

        # The conversation file should be removed.
        store = await manager._get_store(tmp_path)
        assert store.get_conversation(conv.id) is None
