from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.config import Settings
from src.daemon.thread_runner import build_thread_prompt, run_invocation
from src.infrastructure.database import Database
from src.models import (
    ThreadInvocationPurpose,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)


def test_build_prompt_includes_token_and_history():
    thread = ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    participants = [
        ThreadParticipant(thread_id="THR-001", agent_name="alice"),
        ThreadParticipant(thread_id="THR-001", agent_name="bob"),
    ]
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=1, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="should we cap?",
        ),
    ]
    prompt = build_thread_prompt(
        thread=thread, participants=participants, messages=msgs,
        invocation_token="TOK-ABC",
        invoked_agent="alice", purpose="reply", triggering_seq=1,
    )
    assert "THR-001" in prompt
    assert "Refund policy" in prompt
    assert "TOK-ABC" in prompt
    assert "Message 1" in prompt
    assert "should we cap?" in prompt
    assert "posted to this thread" in prompt.lower()


class FakeExecutorResult:
    def __init__(self, success: bool, error: str = ""):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"
        self.duration_seconds = 1


class FakeOrgState:
    def __init__(self, db: Database, root):
        self.db = db
        self.root = root


@pytest.mark.asyncio
async def test_run_invocation_no_callback_silent_decline(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    # Workspace stub so the runner can find agent.yaml.
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    # Replace the executor builder so .run() returns immediately without callback.
    import src.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            return FakeExecutorResult(success=True)

    original_build = runner_mod._build_executor_for_provider
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FakeExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    # Broadcast model: silent decline — no transcript row is inserted.
    msgs = db.list_thread_messages("THR-001")
    assert not any(m.kind.value == "decline" for m in msgs), "silent decline: no transcript row"
    # The invocation row itself transitions to a terminal failed/timeout status.
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status.value in {"failed", "timeout"}
