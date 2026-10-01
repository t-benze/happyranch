from __future__ import annotations

import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
)


def _thread(db: Database, thread_id: str = "THR-001", *agents: str) -> None:
    db.insert_thread(ThreadRecord(id=thread_id, subject="reply link"))
    for agent in agents:
        db.add_thread_participant(thread_id, agent, added_by="founder")


def _invocation_row(db: Database, token: str) -> dict[str, object]:
    row = db.execute(
        "SELECT * FROM thread_invocations WHERE invocation_token = ?", (token,),
    ).fetchone()
    assert row is not None
    return dict(row)


def test_modern_reply_links_the_terminalized_wake_to_its_message(tmp_path) -> None:
    db = Database(tmp_path / "modern.db")
    _thread(db, "THR-001", "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="please reply",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    assert token is not None
    assert db.claim_conversational_reply(token) is not None

    seq, settlement, _ = db.reply_conversational(
        thread_id="THR-001",
        speaker="alice",
        body_markdown="reply",
        attachments=[],
        token=token,
        token_purpose=ThreadInvocationPurpose.REPLY,
    )

    assert settlement is not None
    assert _invocation_row(db, token).get("reply_message_seq") == seq
    invocation = db.get_invocation_any_status(token)
    assert invocation is not None
    assert invocation.model_dump().get("reply_message_seq") == seq
    message = next(
        item for item in db.list_thread_messages("THR-001") if item.seq == seq
    )
    assert message.speaker == "alice"


def test_legacy_reply_fallback_links_the_terminalized_wake(tmp_path) -> None:
    db = Database(tmp_path / "legacy-reply.db")
    _thread(db, "THR-001", "alice")
    invocation = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="alice",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    )

    seq, settlement, _ = db.reply_conversational(
        thread_id="THR-001",
        speaker="alice",
        body_markdown="legacy reply",
        attachments=[],
        token=invocation.invocation_token,
        token_purpose=ThreadInvocationPurpose.REPLY,
    )

    assert settlement is None
    assert _invocation_row(db, invocation.invocation_token).get("reply_message_seq") == seq


@pytest.mark.parametrize(
    "purpose",
    [ThreadInvocationPurpose.BOOTSTRAP, ThreadInvocationPurpose.TASK_FOLLOWUP],
)
def test_special_purpose_reply_links_the_terminalized_wake(
    tmp_path, purpose: ThreadInvocationPurpose,
) -> None:
    db = Database(tmp_path / f"{purpose.value}.db")
    _thread(db, "THR-001", "alice")
    invocation = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="alice",
        triggering_seq=0,
        purpose=purpose,
    )

    seq, settlement, _ = db.reply_conversational(
        thread_id="THR-001",
        speaker="alice",
        body_markdown=f"{purpose.value} reply",
        attachments=[],
        token=invocation.invocation_token,
        token_purpose=purpose,
    )

    assert settlement is None
    assert _invocation_row(db, invocation.invocation_token).get("reply_message_seq") == seq


def test_already_terminal_wake_is_not_relinked(tmp_path) -> None:
    db = Database(tmp_path / "terminal.db")
    _thread(db, "THR-001", "alice")
    invocation = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="alice",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    assert db.consume_invocation(invocation.invocation_token)

    seq, settlement, _ = db.reply_conversational(
        thread_id="THR-001",
        speaker="alice",
        body_markdown="late reply",
        attachments=[],
        token=invocation.invocation_token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    assert settlement is None
    assert seq == 1  # Existing direct-store behavior still appends the message.
    assert _invocation_row(db, invocation.invocation_token).get("reply_message_seq") is None


@pytest.mark.parametrize("mismatch", ["thread", "agent"])
def test_foreign_wake_is_never_linked(tmp_path, mismatch: str) -> None:
    db = Database(tmp_path / f"foreign-{mismatch}.db")
    _thread(db, "THR-001", "alice", "bob")
    if mismatch == "thread":
        _thread(db, "THR-OTHER", "alice")
        token_thread, token_agent = "THR-OTHER", "alice"
    else:
        token_thread, token_agent = "THR-001", "bob"
    invocation = db.mint_thread_invocation(
        thread_id=token_thread,
        agent_name=token_agent,
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    db.reply_conversational(
        thread_id="THR-001",
        speaker="alice",
        body_markdown="not your wake",
        attachments=[],
        token=invocation.invocation_token,
        token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    assert _invocation_row(db, invocation.invocation_token).get("reply_message_seq") is None


def test_link_failure_rolls_back_message_and_terminal_transition(tmp_path) -> None:
    db = Database(tmp_path / "rollback.db")
    _thread(db, "THR-001", "alice", "bob")
    blocker = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="bob",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    db.execute(
        "UPDATE thread_invocations SET status = 'consumed', reply_message_seq = 1 "
        "WHERE invocation_token = ?",
        (blocker.invocation_token,),
    )
    target = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="alice",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.reply_conversational(
            thread_id="THR-001",
            speaker="alice",
            body_markdown="must roll back",
            attachments=[],
            token=target.invocation_token,
            token_purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )

    assert db.list_thread_messages("THR-001") == []
    row = _invocation_row(db, target.invocation_token)
    assert row["status"] == ThreadInvocationStatus.PENDING.value
    assert row["reply_message_seq"] is None


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        ("decline", ThreadInvocationStatus.DECLINED),
        ("failed", ThreadInvocationStatus.FAILED),
        ("timeout", ThreadInvocationStatus.TIMEOUT),
    ],
)
def test_non_reply_settlements_leave_link_null(
    tmp_path, outcome: str, expected_status: ThreadInvocationStatus,
) -> None:
    db = Database(tmp_path / f"{outcome}.db")
    _thread(db, "THR-001", "alice")
    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="wake",
        recipients=["alice"],
    )
    token = arrivals[0].invocation_token
    assert token is not None
    assert db.claim_conversational_reply(token) is not None

    settlement = db.settle_conversational_reply(
        token=token,
        outcome=outcome,
        decline_reason="terminal without reply" if outcome != "decline" else None,
    )

    assert settlement is not None
    row = _invocation_row(db, token)
    assert row["status"] == expected_status.value
    assert row.get("reply_message_seq") is None


def test_partial_unique_index_rejects_duplicate_thread_message_link(tmp_path) -> None:
    db = Database(tmp_path / "unique.db")
    _thread(db, "THR-001", "alice", "bob")
    first = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="alice",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    second = db.mint_thread_invocation(
        thread_id="THR-001",
        agent_name="bob",
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    db.execute(
        "UPDATE thread_invocations SET reply_message_seq = 7 WHERE invocation_token = ?",
        (first.invocation_token,),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE thread_invocations SET reply_message_seq = 7 WHERE invocation_token = ?",
            (second.invocation_token,),
        )
