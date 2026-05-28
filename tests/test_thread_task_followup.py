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


def test_count_pending_turn_obligations_excludes_non_pending(tmp_path):
    """Prove the status filter is essential: only PENDING invocations count."""
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="t"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    seq = db.append_thread_message(
        thread_id="THR-001", speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="hi", addressed_to=["@all"],
    )

    # Mint two REPLY and two BOOTSTRAP invocations (all PENDING initially).
    reply1 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
    )
    reply2 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
    )
    bootstrap1 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    bootstrap2 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )

    # All four are PENDING; count should be 4.
    assert db.count_pending_turn_obligations("THR-001") == 4

    # Transition one REPLY to FAILED using the canonical API.
    success = db.fail_invocation(
        reply1.invocation_token,
        status=ThreadInvocationStatus.FAILED,
        decline_reason="test_decline",
    )
    assert success is True

    # Count should now be 3 (one REPLY + two BOOTSTRAP).
    assert db.count_pending_turn_obligations("THR-001") == 3


# ---------------------------------------------------------------------------
# Task 3 — TASK_FOLLOWUP admitted by reply/decline; dispatch stays restricted
# (route-level tests live in tests/daemon/test_threads_routes.py where the
#  daemon fixtures tmp_home / app / org_state / auth_headers are declared)
# ---------------------------------------------------------------------------


def test_purpose_note_task_followup_renders_task_id_and_status():
    from src.daemon.thread_runner import _purpose_note
    from src.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    triggering = ThreadMessage(
        thread_id="THR-1", seq=4, speaker="family_manager",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "task_completed",
            "task_id": "TASK-007", "original_task_id": "TASK-007",
            "status": "completed", "final_output_summary": "report uploaded",
        },
        created_at=datetime(2026, 5, 28, 1, 43, 23, tzinfo=timezone.utc),
    )
    note = _purpose_note(
        purpose="task_followup", triggering_seq=4,
        addressed_to=None, invoked_agent="family_manager",
        triggering_message=triggering,
    )
    assert "TASK-007" in note
    assert "completed" in note
    assert "grassland details" in note
