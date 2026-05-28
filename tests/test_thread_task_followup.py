import pytest
from pathlib import Path
from src.infrastructure.database import Database
from src.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
)


def _fresh_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def test_task_followup_purpose_value():
    assert ThreadInvocationPurpose.TASK_FOLLOWUP.value == "task_followup"
    assert "task_followup" in {p.value for p in ThreadInvocationPurpose}


def test_count_pending_turn_obligations_counts_reply_bootstrap_followup(tmp_path):
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="t"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    seq = db.append_thread_message(
        thread_id="THR-001", speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="hi", addressed_to=["@all"],
    )
    for purpose in (
        ThreadInvocationPurpose.REPLY,
        ThreadInvocationPurpose.BOOTSTRAP,
        ThreadInvocationPurpose.TASK_FOLLOWUP,
        ThreadInvocationPurpose.CLOSE_OUT,  # must NOT be counted
    ):
        db.mint_thread_invocation(
            thread_id="THR-001", agent_name="alice",
            triggering_seq=seq, purpose=purpose,
        )

    assert db.count_pending_turn_obligations("THR-001") == 3
