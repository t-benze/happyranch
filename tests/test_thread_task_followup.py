import pytest
from pathlib import Path
from runtime.infrastructure.database import Database
from runtime.models import (
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
        body_markdown="hi",
    )
    for purpose in (
        ThreadInvocationPurpose.REPLY,
        ThreadInvocationPurpose.BOOTSTRAP,
        ThreadInvocationPurpose.TASK_FOLLOWUP,
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
        body_markdown="hi",
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
    from runtime.daemon.thread_runner import _purpose_note
    from runtime.models import ThreadMessage, ThreadMessageKind
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
        invoked_agent="family_manager",
        triggering_message=triggering,
    )
    assert "TASK-007" in note
    assert "completed" in note
    assert "happyranch details" in note


# ---------------------------------------------------------------------------
# Task 5 — render task_completed and task_failed system messages
# ---------------------------------------------------------------------------


def _make_system_msg(seq: int, payload: dict) -> "ThreadMessage":
    from runtime.models import ThreadMessage, ThreadMessageKind
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
    from runtime.infrastructure.thread_store import render_transcript_body

    msg = _make_system_msg(
        7,
        {
            "kind_tag": "task_completed",
            "task_id": "TASK-007",
            "original_task_id": "TASK-007",
            "status": "completed",
            "final_output_summary": "PDF uploaded to Drive",
            "final_output_dir": None,
            "cancelled": False,
            "revisit_chain_length": 1,
        },
    )
    out = render_transcript_body([msg])
    assert "Task TASK-007" in out
    assert "completed" in out
    assert "PDF uploaded to Drive" in out


def test_thread_store_renders_task_failed_with_cancelled_and_revisits():
    from runtime.infrastructure.thread_store import render_transcript_body

    msg = _make_system_msg(
        31,
        {
            "kind_tag": "task_failed",
            "task_id": "TASK-031",
            "original_task_id": "TASK-031",
            "status": "failed",
            "final_output_summary": "",
            "final_output_dir": None,
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
    from cli.thread_forward import build_forward_body_from_thread
    from runtime.models import ThreadMessage, ThreadMessageKind
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
    from runtime.infrastructure.thread_store import render_transcript_body
    from runtime.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    msg = ThreadMessage(
        thread_id="THR-1", seq=1, speaker="alice",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "task_completed",
            "task_id": "TASK-7", "original_task_id": "TASK-7",
            "status": "completed",
            "final_output_summary": "PDF uploaded",
            "final_output_dir": "/reports/TASK-7/",
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
    from runtime.infrastructure.audit_logger import AuditLogger
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
    from runtime.infrastructure.audit_logger import AuditLogger
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
    from runtime.infrastructure.audit_logger import AuditLogger
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


# ---------------------------------------------------------------------------
# Task 7 — _maybe_post_thread_followup core helper
# ---------------------------------------------------------------------------


import pytest as _pytest

from runtime.config import Settings
from runtime.models import BlockKind, TaskRecord, TaskStatus, ThreadRecord, ThreadStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


@_pytest.fixture
def orch_with_db(tmp_path: Path) -> Orchestrator:
    """Fresh Orchestrator backed by an in-memory-equivalent temp DB.

    Mirrors the pattern used throughout tests/test_run_step.py:
    OrgPaths → TeamsRegistry.load → Orchestrator(db, settings, paths, slug, teams).
    """
    rt = RuntimeDir.init(tmp_path / "runtime")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    db = Database(tmp_path / "test.db")
    teams = TeamsRegistry.load(paths.root)
    return Orchestrator(db=db, settings=Settings(), paths=paths, slug="test", teams=teams)


def _seed_dispatched_root(
    orch: Orchestrator,
    *,
    thread_id: str = "THR-1",
    task_id: str = "TASK-1",
    dispatcher: str = "alice",
    target: str = "alice",
) -> None:
    """Insert an open thread + participant + dispatched root task + thread_dispatch audit row."""
    orch._db.insert_thread(ThreadRecord(id=thread_id, subject="t"))
    orch._db.add_thread_participant(thread_id, dispatcher, added_by="founder")
    orch._db.insert_task(TaskRecord(
        id=task_id, brief="b", team="ops", assigned_agent=target,
        dispatched_from_thread_id=thread_id,
    ))
    orch._audit.log_thread_dispatch(
        thread_id, task_id=task_id, dispatcher=dispatcher,
        target_agent=target, team="ops",
    )


def _payload(row: dict) -> dict:
    import json as _json
    p = row["payload"]
    return p if isinstance(p, dict) else _json.loads(p)


# --- Truth table (spec §4) ---
@_pytest.mark.parametrize("status,spawned,cancelled,should_fire", [
    (TaskStatus.COMPLETED, False, False, True),   # row 1: normal completion
    (TaskStatus.FAILED,    True,  False, False),  # row 2: revisit will run
    (TaskStatus.FAILED,    False, False, True),   # row 3: chain dead
    (TaskStatus.FAILED,    False, True,  True),   # row 4: founder-cancelled
])
def test_fire_predicate_truth_table(orch_with_db, status, spawned, cancelled, should_fire):
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    _seed_dispatched_root(orch)
    if cancelled:
        orch._db.update_task("TASK-1", cancelled_at="2026-05-28T00:00:00+00:00")
    orch._db.update_task("TASK-1", status=status)
    _maybe_post_thread_followup(orch, "TASK-1", status=status, auto_revisit_spawned=spawned)
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert (len(followups) == 1) == should_fire


def test_non_root_task_does_not_fire(orch_with_db):
    """Only root tasks fire. Child terminals must NOT spawn followups."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        parent_task_id="TASK-1",
    ))
    _maybe_post_thread_followup(orch, "TASK-2",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)


def test_walks_revisit_chain_to_find_thread(orch_with_db):
    """Revisit root doesn't carry dispatched_from_thread_id; walk backward to find it."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    _seed_dispatched_root(orch, task_id="TASK-1")
    orch._db.update_task("TASK-1", status=TaskStatus.FAILED)
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        revisit_of_task_id="TASK-1",  # no dispatched_from_thread_id
    ))
    orch._db.update_task("TASK-2", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-2",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1


def test_thread_not_open_skips_with_audit(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.set_thread_status("THR-1", status=ThreadStatus.ARCHIVED)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)
    audit_rows = orch._db.get_audit_logs("TASK-1")
    assert any(r["action"] == "thread_followup_skipped" for r in audit_rows)


def test_dispatcher_unresolved_skips_with_audit(orch_with_db):
    """If task_dispatched audit row is missing, audit + skip."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    # Insert thread + dispatched task but NO audit row.
    orch._db.insert_thread(ThreadRecord(id="THR-X", subject="t"))
    orch._db.add_thread_participant("THR-X", "alice", added_by="founder")
    orch._db.insert_task(TaskRecord(
        id="TASK-X", brief="b", team="ops", assigned_agent="alice",
        dispatched_from_thread_id="THR-X", status=TaskStatus.COMPLETED,
    ))
    _maybe_post_thread_followup(orch, "TASK-X",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    audit_rows = orch._db.get_audit_logs("TASK-X")
    skipped = [r for r in audit_rows if r["action"] == "thread_followup_skipped"]
    assert skipped, "expected thread_followup_skipped audit row"
    assert _payload(skipped[0])["reason"] == "dispatcher_unresolved"


def test_turn_cap_auto_extends_when_projected_over(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    _seed_dispatched_root(orch)
    # Set the cap tight: turns_used=0, pending=0, so projected = 0 + 0 + 1 = 1 > 0 → bump.
    orch._db.set_thread_turn_cap("THR-1", new_cap=0)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    refetched = orch._db.get_thread("THR-1")
    assert refetched.turn_cap == 1


def test_no_dispatched_from_thread_no_op(orch_with_db):
    """Tasks that didn't come from a thread must produce no audit / no invocation."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db
    orch._db.insert_task(TaskRecord(
        id="TASK-N", brief="b", team="ops", assigned_agent="alice",
        status=TaskStatus.COMPLETED,
    ))
    _maybe_post_thread_followup(orch, "TASK-N",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    # No exception, no thread audit row written.
    audit_rows = orch._db.get_audit_logs("TASK-N")
    assert not any(r["action"].startswith("thread_") for r in audit_rows)


# ---------------------------------------------------------------------------
# Task 8 (enqueue fix) — verify cross-thread enqueue via run_coroutine_threadsafe
# ---------------------------------------------------------------------------


@_pytest.fixture
def orch_with_thread_queue(tmp_path: Path):
    """Orchestrator with a real ThreadQueue + a dedicated event loop running in a
    background thread, so run_coroutine_threadsafe can bridge run_step's worker
    thread into the queue's async world.

    Yields (orch, thread_queue, main_loop).  The loop thread is cleaned up
    automatically when the fixture tears down.
    """
    import asyncio
    import threading
    from runtime.daemon.thread_queue import ThreadQueue

    rt = RuntimeDir.init(tmp_path / "runtime")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    db = Database(tmp_path / "test.db")
    teams = TeamsRegistry.load(paths.root)
    orch = Orchestrator(db=db, settings=Settings(), paths=paths, slug="test", teams=teams)

    # Start a real event loop in a background daemon thread so we don't block
    # the test thread.  This mirrors the daemon lifespan where the loop lives in
    # the FastAPI/uvicorn thread and run_step uses run_coroutine_threadsafe to
    # enqueue from the thread-pool worker.
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    queue = ThreadQueue()
    orch.attach_thread_queue(queue, loop)

    yield orch, queue, loop

    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)


def test_cancel_in_progress_thread_dispatched_task_fires_followup(orch_with_thread_queue):
    """IN_PROGRESS task cancelled mid-execution: run_step's cancel-race guard fires the helper."""
    orch, thread_queue, main_loop = orch_with_thread_queue
    _seed_dispatched_root(orch)
    # Simulate the IN_PROGRESS + cancelled state at run_step's cancel-race guard entry.
    from datetime import datetime, timezone
    orch._db.update_task("TASK-1", status=TaskStatus.IN_PROGRESS)
    orch._db.update_task("TASK-1",
                         status=TaskStatus.FAILED,
                         cancelled_at=datetime.now(timezone.utc).isoformat())
    # The cancel-race guard branch in run_step would call:
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.FAILED, auto_revisit_spawned=False)
    # Followup minted.
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1


def test_cancel_blocked_thread_dispatched_task_fires_followup(orch_with_thread_queue):
    """BLOCKED(DELEGATED) root cancellation: cancel route's Phase 1b fires the helper."""
    orch, thread_queue, main_loop = orch_with_thread_queue
    _seed_dispatched_root(orch)
    # Walk through: prior_status=BLOCKED → cancel sets FAILED + cancelled_at → fire.
    from datetime import datetime, timezone
    orch._db.update_task("TASK-1", status=TaskStatus.BLOCKED, block_kind="delegated")
    # Mirror cancel route's Phase 1b shape:
    orch._db.update_task("TASK-1",
                         status=TaskStatus.FAILED,
                         cancelled_at=datetime.now(timezone.utc).isoformat())
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.FAILED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1


def test_lineage_too_deep_skips_with_audit(orch_with_db):
    """Pathologically deep revisit chain raises LineageTooDeep → audit-skip, no crash."""
    from runtime.infrastructure.database import LineageTooDeep
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    # Monkeypatch walk_revisit_chain to simulate a chain that exceeds max_hops.
    def _raise(*args, **kwargs):
        raise LineageTooDeep("too deep")
    orch._db.walk_revisit_chain = _raise
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    # Must not raise.
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    audit_rows = orch._db.get_audit_logs("TASK-1")
    assert any(
        r["action"] == "thread_followup_skipped"
        and "chain_too_deep" in str(r.get("payload", {}))
        for r in audit_rows
    )


def test_helper_enqueues_invocation_to_thread_queue(orch_with_thread_queue):
    """Successful fire must put a ThreadJob on the org's thread queue."""
    import asyncio
    from runtime.orchestrator.run_step import _maybe_post_thread_followup

    orch, thread_queue, main_loop = orch_with_thread_queue
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)

    # Drive the background loop briefly so the scheduled coroutine lands on the queue.
    fut = asyncio.run_coroutine_threadsafe(thread_queue.get(), main_loop)
    job = fut.result(timeout=2.0)
    assert job.org_slug == "test"
    assert job.invocation_token  # any non-empty token

    # Also assert the invocation token on the job matches what was minted in the DB.
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1
    assert job.invocation_token == followups[0].invocation_token


def test_no_thread_queue_wired_writes_enqueue_unavailable_audit(orch_with_db):
    """When no thread_queue is wired (plain test orchestrator), the helper must
    write a thread_followup_skipped row with reason='enqueue_unavailable' and
    still mint the invocation (so the operator can see it)."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    orch = orch_with_db  # no attach_thread_queue call
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)

    # Invocation is still minted (best-effort; operator can reap or retry).
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1

    # Audit row must record the unavailability.
    audit_rows = orch._db.get_audit_logs("TASK-1")
    skipped = [r for r in audit_rows if r["action"] == "thread_followup_skipped"]
    assert skipped, "expected thread_followup_skipped audit row"
    assert _payload(skipped[0])["reason"] == "enqueue_unavailable"


def test_completed_thread_dispatched_task_fires_followup_via_complete(orch_with_db):
    """End-to-end at the _complete + helper interaction: COMPLETED root → followup minted."""
    orch = orch_with_db
    _seed_dispatched_root(orch)
    from runtime.orchestrator.run_step import _complete, _maybe_post_thread_followup
    _complete(orch, "TASK-1", note="done", output_dir=None)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    assert sum(1 for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP) == 1


# ---------------------------------------------------------------------------
# Task 9 — /cancel route fires followup for PENDING tasks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1 — Cancel race at Site D: helper must use DB-actual status, not caller's
# ---------------------------------------------------------------------------


def test_site_d_cancel_race_fires_task_failed_not_completed(orch_with_db):
    """If /cancel lands between Guard B and _complete(), Site D's caller still
    passes status=COMPLETED, but the persisted row is FAILED+cancelled_at.
    The helper must read DB-actual status, not the caller's claim, and emit
    task_failed accordingly."""
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    from datetime import datetime, timezone

    orch = orch_with_db
    _seed_dispatched_root(orch)
    # Simulate the race: /cancel flipped the row to FAILED+cancelled_at,
    # _complete() short-circuited so the COMPLETED write never happened.
    orch._db.update_task(
        "TASK-1",
        status=TaskStatus.FAILED,
        cancelled_at=datetime.now(timezone.utc).isoformat(),
    )
    # Site D's caller still passes COMPLETED (it doesn't know about the race).
    _maybe_post_thread_followup(
        orch, "TASK-1",
        status=TaskStatus.COMPLETED, auto_revisit_spawned=False,
    )
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1, "helper must still fire (FAILED is a terminal requiring followup)"
    # The system message must reflect the ACTUAL DB status (failed), not the
    # caller's claim (completed).
    msgs = orch._db.list_thread_messages("THR-1")
    system_msgs = [
        m for m in msgs
        if m.kind == ThreadMessageKind.SYSTEM
        and (m.system_payload or {}).get("kind_tag") in ("task_completed", "task_failed")
    ]
    assert len(system_msgs) == 1
    payload = system_msgs[0].system_payload or {}
    assert payload["kind_tag"] == "task_failed", (
        f"expected task_failed from DB-actual status, got: {payload['kind_tag']!r}"
    )
    assert payload["status"] == "failed", (
        f"expected status='failed', got: {payload['status']!r}"
    )
    assert payload.get("cancelled") is True


# ---------------------------------------------------------------------------
# P2 — Atomic turn-cap projection + mint
# ---------------------------------------------------------------------------


def test_mint_followup_with_cap_extend_bumps_when_projection_over(tmp_path):
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-1", subject="t", turn_cap=1, turns_used=1))
    db.add_thread_participant("THR-1", "alice", added_by="founder")
    seq = db.append_thread_message(
        thread_id="THR-1", speaker="alice", kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_completed"},
    )
    # turns_used=1, pending=0, projected=1+0+1=2 > turn_cap=1 → must bump.
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        thread_id="THR-1", agent_name="alice", triggering_seq=seq,
    )
    assert new_cap == 2
    refetched = db.get_thread("THR-1")
    assert refetched.turn_cap == 2
    assert inv.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP


def test_mint_followup_with_cap_extend_no_bump_when_within_cap(tmp_path):
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-1", subject="t", turn_cap=500, turns_used=0))
    db.add_thread_participant("THR-1", "alice", added_by="founder")
    seq = db.append_thread_message(
        thread_id="THR-1", speaker="alice", kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_completed"},
    )
    # turns_used=0, pending=0, projected=0+0+1=1 <= turn_cap=500 → no bump.
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        thread_id="THR-1", agent_name="alice", triggering_seq=seq,
    )
    assert new_cap is None
    refetched = db.get_thread("THR-1")
    assert refetched.turn_cap == 500
    assert inv.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP


def test_two_concurrent_mints_dont_exceed_cap(tmp_path):
    """Under @_synchronized the two mints serialize, so each observes the
    other's pending mint when computing the projection, and cap ends at 2
    (one bump per call) rather than the racy outcome of 1 bump total."""
    import threading

    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-1", subject="t", turn_cap=0, turns_used=0))
    db.add_thread_participant("THR-1", "alice", added_by="founder")
    seq1 = db.append_thread_message(
        thread_id="THR-1", speaker="alice", kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_completed"},
    )
    seq2 = db.append_thread_message(
        thread_id="THR-1", speaker="alice", kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_completed"},
    )
    results: list = []
    errors: list = []

    def worker(s: int) -> None:
        try:
            results.append(
                db.mint_followup_invocation_with_cap_extend(
                    thread_id="THR-1", agent_name="alice", triggering_seq=s,
                )
            )
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(seq1,))
    t2 = threading.Thread(target=worker, args=(seq2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 2

    refetched = db.get_thread("THR-1")
    # Both mints must have landed; the cap must reflect both bumps (2).
    assert refetched.turn_cap == 2, (
        f"expected cap=2 (one bump per mint), got {refetched.turn_cap}"
    )
    pending = db.count_pending_turn_obligations("THR-1")
    assert pending == 2


def test_cancel_pending_thread_dispatched_task_fires_followup(orch_with_thread_queue):
    """A PENDING task cancelled via the cancel path must fire a followup.

    The running-task cancellation path is covered transitively via Site B
    in run_step (SIGTERM → rc=-15 → session-failure terminal). This test
    locks the PENDING case: the task was never started, so run_step never
    runs, and the cancel route itself must invoke the helper.
    """
    orch, thread_queue, main_loop = orch_with_thread_queue
    _seed_dispatched_root(orch)
    # Task is PENDING by default after _seed_dispatched_root. Simulate the
    # /cancel route's state mutation + helper invocation directly.
    from datetime import datetime, timezone
    orch._db.update_task(
        "TASK-1",
        status=TaskStatus.FAILED,
        cancelled_at=datetime.now(timezone.utc).isoformat(),
    )
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    _maybe_post_thread_followup(
        orch, "TASK-1",
        status=TaskStatus.FAILED, auto_revisit_spawned=False,
    )
    # Drain to verify enqueue.
    import asyncio
    fut = asyncio.run_coroutine_threadsafe(thread_queue.get(), main_loop)
    job = fut.result(timeout=2.0)
    assert job.invocation_token
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1


# ---------------------------------------------------------------------------
# Thread escalation surfacing — _maybe_post_thread_escalation
# Spec: docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md
# ---------------------------------------------------------------------------


def test_escalation_root_fires_and_carries_reason(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="needs founder auth")

    _maybe_post_thread_escalation(orch, "TASK-1", reason="needs founder auth")

    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1
    msgs = orch._db.list_thread_messages("THR-1")
    sysmsgs = [m for m in msgs if m.system_payload
               and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(sysmsgs) == 1
    assert sysmsgs[0].system_payload["reason"] == "needs founder auth"
    assert sysmsgs[0].system_payload["task_id"] == "TASK-1"
    assert sysmsgs[0].speaker == "alice"


def test_escalation_child_depth_surfaces_via_ancestors(orch_with_db):
    """Escalations do NOT cascade; a child-task escalation must still surface
    in the originating thread by walking ancestors to the dispatched root."""
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch, task_id="TASK-1")
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        parent_task_id="TASK-1",
    ))
    orch._db.update_task("TASK-2", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="deep blocker")

    _maybe_post_thread_escalation(orch, "TASK-2", reason="deep blocker")

    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1
    msgs = orch._db.list_thread_messages("THR-1")
    sysmsgs = [m for m in msgs if m.system_payload
               and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(sysmsgs) == 1
    assert sysmsgs[0].system_payload["task_id"] == "TASK-2"
    assert sysmsgs[0].system_payload["original_task_id"] == "TASK-1"


def test_escalation_resolved_in_race_is_noop(orch_with_db):
    """If the task is no longer blocked/escalated (founder resolved in the
    race window), the helper must not post anything."""
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)

    _maybe_post_thread_escalation(orch, "TASK-1", reason="needs founder auth")

    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)
    assert not orch._db.list_thread_messages("THR-1")


def test_escalation_non_thread_task_noop(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    orch._db.insert_task(TaskRecord(
        id="TASK-N", brief="b", team="ops", assigned_agent="alice",
    ))
    orch._db.update_task("TASK-N", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="x")

    _maybe_post_thread_escalation(orch, "TASK-N", reason="x")

    audit_rows = orch._db.get_audit_logs("TASK-N")
    assert not any(r["action"].startswith("thread_") for r in audit_rows)


def test_escalation_thread_not_open_skips_with_audit(orch_with_db):
    from runtime.orchestrator.run_step import _maybe_post_thread_escalation
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.set_thread_status("THR-1", status=ThreadStatus.ARCHIVED)
    orch._db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="x")

    _maybe_post_thread_escalation(orch, "TASK-1", reason="x")

    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)
    audit_rows = orch._db.get_audit_logs("TASK-1")
    assert any(r["action"] == "thread_followup_skipped" for r in audit_rows)


def test_purpose_note_escalated_uses_escalation_wording():
    from runtime.daemon.thread_runner import _purpose_note
    from runtime.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone

    msg = ThreadMessage(
        thread_id="THR-1", seq=5, speaker="alice",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "task_escalated",
            "task_id": "TASK-893",
            "status": "escalated",
            "reason": "needs founder CDN authorize",
        },
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
    )
    note = _purpose_note("task_followup", 5, "alice", triggering_message=msg)
    assert "ESCALATED" in note
    assert "TASK-893" in note
    assert "needs founder CDN authorize" in note
    assert "resolve the escalation yourself" in note
