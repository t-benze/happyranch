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


# ---------------------------------------------------------------------------
# Task 5 — render task_completed and task_failed system messages
# ---------------------------------------------------------------------------


def _make_system_msg(seq: int, payload: dict) -> "ThreadMessage":
    from src.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    return ThreadMessage(
        thread_id="THR-1",
        seq=seq,
        speaker="family_manager",
        kind=ThreadMessageKind.SYSTEM,
        system_payload=payload,
        created_at=datetime(2026, 5, 28, 1, 43, 23, tzinfo=timezone.utc),
    )


def test_thread_store_renders_task_completed_system_message():
    from src.infrastructure.thread_store import render_transcript_body

    msg = _make_system_msg(
        7,
        {
            "kind_tag": "task_completed",
            "task_id": "TASK-007",
            "original_task_id": "TASK-007",
            "status": "completed",
            "final_output_summary": "PDF uploaded to Drive",
            "final_artifact_dir": None,
            "cancelled": False,
            "revisit_chain_length": 1,
        },
    )
    out = render_transcript_body([msg])
    assert "Task TASK-007" in out
    assert "completed" in out
    assert "PDF uploaded to Drive" in out


def test_thread_store_renders_task_failed_with_cancelled_and_revisits():
    from src.infrastructure.thread_store import render_transcript_body

    msg = _make_system_msg(
        31,
        {
            "kind_tag": "task_failed",
            "task_id": "TASK-031",
            "original_task_id": "TASK-031",
            "status": "failed",
            "final_output_summary": "",
            "final_artifact_dir": None,
            "cancelled": True,
            "revisit_chain_length": 3,
        },
    )
    out = render_transcript_body([msg])
    assert "Task TASK-031" in out
    assert "failed" in out
    assert "founder-cancelled" in out
    assert "2 revisits" in out


def test_thread_forward_renders_task_completed_and_failed():
    from src.daemon.thread_forward import build_forward_body_from_thread
    from src.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    def _sys(seq: int, payload: dict) -> ThreadMessage:
        return ThreadMessage(
            thread_id="THR-1",
            seq=seq,
            speaker="family_manager",
            kind=ThreadMessageKind.SYSTEM,
            system_payload=payload,
            created_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        )

    msg_done = _sys(
        1,
        {
            "kind_tag": "task_completed",
            "task_id": "TASK-007",
            "original_task_id": "TASK-007",
            "status": "completed",
            "final_output_summary": "PDF uploaded to Drive",
            "cancelled": False,
            "revisit_chain_length": 1,
        },
    )
    out_done = build_forward_body_from_thread(
        source_id="THR-1", messages=[msg_done], subject="test thread"
    )
    assert "TASK-007" in out_done

    msg_failed = _sys(
        2,
        {
            "kind_tag": "task_failed",
            "task_id": "TASK-031",
            "original_task_id": "TASK-031",
            "status": "failed",
            "final_output_summary": "",
            "cancelled": False,
            "revisit_chain_length": 2,
        },
    )
    out_failed = build_forward_body_from_thread(
        source_id="THR-1", messages=[msg_failed], subject="test thread"
    )
    assert "TASK-031" in out_failed


def test_thread_store_task_completed_blockquote_wraps_all_lines():
    """Every rendered line of the system message must be inside the blockquote (start with '> ')."""
    from src.infrastructure.thread_store import render_transcript_body
    from src.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    msg = ThreadMessage(
        thread_id="THR-1", seq=1, speaker="alice",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "task_completed",
            "task_id": "TASK-7", "original_task_id": "TASK-7",
            "status": "completed",
            "final_output_summary": "PDF uploaded",
            "final_artifact_dir": "/reports/TASK-7/",
            "cancelled": False, "revisit_chain_length": 1,
        },
        created_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    out = render_transcript_body([msg])
    # Extract lines after the message header and before the blank line.
    # The system message content lines should all start with "> ".
    lines = out.splitlines()
    # Find the message header and the blockquote lines that follow
    blockquote_lines = []
    in_system_block = False
    for line in lines:
        if line.startswith("## Message"):
            in_system_block = True
            continue
        if in_system_block:
            if not line.strip():  # blank line marks end of block
                break
            blockquote_lines.append(line)

    # All blockquote lines should start with "> "
    assert blockquote_lines, "Expected to find blockquote lines in output"
    assert all(l.startswith("> ") for l in blockquote_lines), (
        f"Lines escaping blockquote: {[l for l in blockquote_lines if not l.startswith('> ')]!r}"
    )


# ---------------------------------------------------------------------------
# Task 6 — bump_thread_turn_cap + audit helpers
# ---------------------------------------------------------------------------


def test_bump_thread_turn_cap_increments_and_returns_new_cap(tmp_path):
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-1", subject="t", turn_cap=500))
    new_cap = db.bump_thread_turn_cap("THR-1", delta=1)
    assert new_cap == 501
    refetched = db.get_thread("THR-1")
    assert refetched.turn_cap == 501


def test_bump_thread_turn_cap_unknown_thread_raises(tmp_path):
    db = _fresh_db(tmp_path)
    import pytest
    with pytest.raises(Exception):  # KeyError or sqlite error; either is fine
        db.bump_thread_turn_cap("THR-MISSING", delta=1)


def test_log_thread_task_followup_enqueued_writes_audit_row(tmp_path):
    db = _fresh_db(tmp_path)
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_thread_task_followup_enqueued(
        thread_id="THR-1", original_task_id="TASK-1", terminal_task_id="TASK-7",
        dispatcher="alice", invocation_token="abcdefgh12345678",
    )
    rows = db.get_audit_logs("TASK-7")
    assert any(r["action"] == "thread_task_followup_enqueued" for r in rows)
    row = next(r for r in rows if r["action"] == "thread_task_followup_enqueued")
    payload = row["payload"] if isinstance(row["payload"], dict) else __import__("json").loads(row["payload"])
    assert payload["thread_id"] == "THR-1"
    assert payload["original_task_id"] == "TASK-1"
    assert payload["dispatcher"] == "alice"
    assert payload["invocation_token_prefix"] == "abcdefgh"  # truncated to 8


def test_log_thread_followup_skipped_writes_reason_and_extras(tmp_path):
    db = _fresh_db(tmp_path)
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_thread_followup_skipped(
        thread_id="THR-1", original_task_id="TASK-1", terminal_task_id="TASK-1",
        reason="thread_not_open", thread_status="archived", task_status="completed",
    )
    rows = db.get_audit_logs("TASK-1")
    row = next(r for r in rows if r["action"] == "thread_followup_skipped")
    payload = row["payload"] if isinstance(row["payload"], dict) else __import__("json").loads(row["payload"])
    assert payload["reason"] == "thread_not_open"
    assert payload["thread_status"] == "archived"
    assert payload["task_status"] == "completed"


def test_log_thread_turn_cap_auto_extended_writes_new_cap(tmp_path):
    db = _fresh_db(tmp_path)
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_thread_turn_cap_auto_extended(
        thread_id="THR-1", original_task_id="TASK-1",
        reason="task_followup", new_cap=501,
    )
    rows = db.get_audit_logs("TASK-1")
    row = next(r for r in rows if r["action"] == "thread_turn_cap_auto_extended")
    payload = row["payload"] if isinstance(row["payload"], dict) else __import__("json").loads(row["payload"])
    assert payload["thread_id"] == "THR-1"
    assert payload["reason"] == "task_followup"
    assert payload["new_cap"] == 501
