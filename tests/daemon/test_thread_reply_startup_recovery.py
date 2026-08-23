"""GitHub #688 Phase 1 Slice B — startup reply-delivery recovery.

The generic startup reaper's conversational REPLY portion is replaced by the
store-owned recovery path: valid queued wakes survive and re-enqueue; an
interrupted running REPLY becomes exactly one daemon_restart replacement;
legacy orphan REPLY receipts (no delivery-state ownership) still reap as
daemon_restart; BOOTSTRAP and TASK_FOLLOWUP keep the generic reaper exactly.
"""
from __future__ import annotations

from pathlib import Path

from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    ThreadStatus,
)


def _seed_org(tmp_path: Path, slug: str = "test") -> Database:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="s"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    return db


def _pending_reply_tokens(db, thread_id="THR-001", agent="alice"):
    return [
        i.invocation_token for i in db.list_thread_invocations(thread_id)
        if i.agent_name == agent
        and i.purpose is ThreadInvocationPurpose.REPLY
        and i.status is ThreadInvocationStatus.PENDING
    ]


def test_startup_sweep_retains_valid_queued_and_returns_token(tmp_path):
    """A valid queued wake survives the restart: the sweep retains its pending
    row and returns the token for post-commit re-enqueue (no daemon_restart)."""
    db = _seed_org(tmp_path)
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, inv.invocation_token,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    tokens = _sweep_on_startup(db, TaskQueue(), "test")
    assert tokens == [inv.invocation_token]  # retained, re-enqueue after commit
    assert (
        db.get_invocation_any_status(inv.invocation_token).status
        is ThreadInvocationStatus.PENDING  # never reaped as daemon_restart
    )
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.queued_invocation_token == inv.invocation_token
    assert len(_pending_reply_tokens(db)) == 1


def test_startup_sweep_replaces_running_with_exactly_one_daemon_restart(tmp_path):
    """An interrupted running REPLY is terminalized exactly once as
    daemon_restart and replaced by exactly one queued wake whose token is
    returned for re-enqueue (repeated recovery is idempotent)."""
    db = _seed_org(tmp_path)
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.stamp_invocation_started(inv.invocation_token, session_id=None)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "running_invocation_token, running_from_seq, running_through_seq, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("THR-001", "alice", 0, 1, inv.invocation_token, 1, 1,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    tokens = _sweep_on_startup(db, TaskQueue(), "test")
    assert len(tokens) == 1  # exactly one replacement
    replacement = tokens[0]
    assert replacement != inv.invocation_token

    original = db.get_invocation_any_status(inv.invocation_token)
    assert original.status is ThreadInvocationStatus.FAILED
    assert original.decline_reason == "daemon_restart"
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.queued_invocation_token == replacement
    assert st.running_invocation_token is None
    assert st.last_terminal_reason == "daemon_restart"
    assert _pending_reply_tokens(db) == [replacement]

    # Repeated recovery is idempotent: the replacement is now retained, and a
    # second sweep returns the same single token with no second mint.
    tokens2 = _sweep_on_startup(db, TaskQueue(), "test")
    assert tokens2 == [replacement]
    assert _pending_reply_tokens(db) == [replacement]


def test_startup_sweep_cutover_legacy_pending_reply_mints_one_queued(tmp_path):
    """An open thread holding a legacy pending REPLY is cut over at startup:
    legacy rows terminalize with a coalesced_cutover receipt and exactly one
    queued replacement is returned for re-enqueue."""
    db = _seed_org(tmp_path)
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="m1",
    )
    legacy = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    tokens = _sweep_on_startup(db, TaskQueue(), "test")
    assert len(tokens) == 1
    replacement = tokens[0]
    assert replacement != legacy.invocation_token
    legacy_after = db.get_invocation_any_status(legacy.invocation_token)
    assert legacy_after.status is ThreadInvocationStatus.FAILED
    assert legacy_after.decline_reason == "coalesced_cutover"
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.queued_invocation_token == replacement
    assert len(_pending_reply_tokens(db)) == 1


def test_startup_sweep_reaps_orphan_reply_without_state(tmp_path):
    """A legacy REPLY receipt with no delivery-state ownership (e.g. an
    archived thread cutover never reaches) is still reaped daemon_restart."""
    db = _seed_org(tmp_path)
    # Archived thread: cutover (open-thread-only) never seeds it.
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVED)
    orphan = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    tokens = _sweep_on_startup(db, TaskQueue(), "test")
    assert tokens == []
    orphan_after = db.get_invocation_any_status(orphan.invocation_token)
    assert orphan_after.status is ThreadInvocationStatus.FAILED
    assert orphan_after.decline_reason == "daemon_restart"
    assert db.list_reply_delivery_states() == []


def test_startup_sweep_preserves_bootstrap_and_task_followup_reaping(tmp_path):
    """BOOTSTRAP and TASK_FOLLOWUP keep the generic daemon_restart reaper
    exactly — never routed through delivery-state recovery, never returned."""
    db = _seed_org(tmp_path)
    boot = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    followup, _ = db.mint_followup_invocation_with_cap_extend(
        "THR-001", agent_name="alice", triggering_seq=1,
    )

    tokens = _sweep_on_startup(db, TaskQueue(), "test")
    assert tokens == []  # reply-delivery recovery returns nothing
    for inv_token in (boot.invocation_token, followup.invocation_token):
        after = db.get_invocation_any_status(inv_token)
        assert after.status is ThreadInvocationStatus.FAILED
        assert after.decline_reason == "daemon_restart"
    # The open thread's pair was activated (seeded) at startup, but the seed
    # is obligation-free: no queued/running token, nothing to re-enqueue.
    states = db.list_reply_delivery_states()
    assert len(states) == 1
    assert states[0].queued_invocation_token is None
    assert states[0].running_invocation_token is None
