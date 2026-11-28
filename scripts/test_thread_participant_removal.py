"""Focused removal-route regressions in the existing synthetic ASGI/SQLite venue."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.daemon.event_bus import thread_inbox_topic, thread_topic
from runtime.models import ThreadInvocationPurpose, ThreadMessageKind, ThreadRecord
from scripts.test_agent_retirement import PREFIX, fixture


THREAD = "THR-901"


@pytest.mark.parametrize("lifecycle", ["active", "terminated", "missing_definition", "missing_workspace"])
async def test_current_participant_removal_preserves_cleanup(tmp_path: Path, lifecycle: str) -> None:
    async with fixture(tmp_path / "org") as (org, client):
        db = org.db
        db.insert_thread(ThreadRecord(id=THREAD, subject="Participant removal"))
        for name in ("worker", "keeper"):
            db.add_thread_participant(THREAD, name, added_by="founder")
        if lifecycle == "terminated":
            response = await client.post(f"{PREFIX}/agents/worker/retire")
            assert response.status_code == 200, response.text
            assert db.is_thread_participant(THREAD, "worker")
            assert not (org.root / "org/agents/worker.md").exists()
            assert not (org.root / "workspaces/worker").exists()

        # Real arrivals own the REPLY slots; the special-purpose tokens exercise
        # the route's additional bulk decline. No executors or queue are started.
        seq, _ = db.record_conversational_arrival(
            thread_id=THREAD, speaker="founder", kind=ThreadMessageKind.MESSAGE,
            body_markdown="Synthetic question", recipients=["worker", "keeper"],
        )
        for purpose in (ThreadInvocationPurpose.BOOTSTRAP, ThreadInvocationPurpose.TASK_FOLLOWUP):
            db.mint_thread_invocation(thread_id=THREAD, agent_name="worker",
                                      triggering_seq=seq, purpose=purpose)
        assert db.get_reply_delivery_state(THREAD, "worker").queued_invocation_token is not None
        db.insert_thread(ThreadRecord(id="THR-902", subject="Other thread"))
        db.add_thread_participant("THR-902", "worker", added_by="founder")
        other = db.mint_thread_invocation(thread_id="THR-902", agent_name="worker",
                                         triggering_seq=0, purpose=ThreadInvocationPurpose.BOOTSTRAP)
        if lifecycle == "missing_definition":
            (org.root / "org/agents/worker.md").unlink()
        elif lifecycle == "missing_workspace":
            (org.root / "workspaces/worker").rename(org.root / "workspaces/archived-worker")

        with patch.object(org.event_bus, "publish", wraps=org.event_bus.publish) as publish:
            response = await client.post(f"{PREFIX}/threads/{THREAD}/remove-participant",
                                         json={"agent_name": "worker"})
        assert response.status_code == 200, response.text
        assert response.json() == {"thread_id": THREAD, "agent_name": "worker", "system_message_seq": seq + 1}
        assert [p.agent_name for p in db.list_thread_participants(THREAD)] == ["keeper"]
        delivery = db.get_reply_delivery_state(THREAD, "worker")
        assert delivery is None or (delivery.queued_invocation_token is None and delivery.running_invocation_token is None)
        invocations = db.execute(
            "SELECT purpose,status,decline_reason FROM thread_invocations WHERE thread_id=? AND agent_name=?",
            (THREAD, "worker"),
        ).fetchall()
        assert {row["purpose"] for row in invocations} == {"reply", "bootstrap", "task_followup"}
        assert {(row["status"], row["decline_reason"]) for row in invocations} == {("declined", "participant_removed")}
        assert db.get_pending_invocation(other.invocation_token) is not None
        assert db.get_reply_delivery_state(THREAD, "keeper").queued_invocation_token is not None
        message = db.get_thread_message_by_seq(THREAD, seq + 1)
        assert message.kind is ThreadMessageKind.SYSTEM and message.speaker == "founder"
        assert message.system_payload == {"kind_tag": "participant_removed", "agent_name": "worker", "removed_by": "founder"}
        audit = db.execute(
            "SELECT task_id,agent,payload FROM audit_log WHERE action='thread_participant_removed'",
        ).fetchall()
        assert [(row["task_id"], row["agent"], json.loads(row["payload"])) for row in audit] == [
            (THREAD, "founder", {"agent_name": "worker", "removed_by": "founder"})]
        assert [call.args for call in publish.call_args_list] == [
            (thread_topic(THREAD), {"thread_id": THREAD, "seq": seq + 1, "speaker": "founder",
                                    "kind": "system", "preview": "removed worker"}),
            (thread_inbox_topic("alpha"), {"thread_id": THREAD, "event_kind": "system", "status": "open"}),
        ]


@pytest.mark.parametrize(("agent", "status", "code"), [
    ("unknown", 404, "unknown_agent"),
    ("worker", 409, "not_participant"),
])
async def test_nonparticipant_refusal_has_no_effects(tmp_path: Path, agent: str, status: int, code: str) -> None:
    async with fixture(tmp_path / "org") as (org, client):
        db = org.db
        db.insert_thread(ThreadRecord(id=THREAD, subject="Nonparticipant refusal"))
        db.add_thread_participant(THREAD, "keeper", added_by="founder")
        tables = ("thread_participants", "thread_messages", "thread_invocations", "audit_log")
        before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        with patch.object(org.event_bus, "publish", wraps=org.event_bus.publish) as publish:
            response = await client.post(f"{PREFIX}/threads/{THREAD}/remove-participant", json={"agent_name": agent})
        assert response.status_code == status, response.text
        assert response.json() == {"detail": {"code": code}}
        assert {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables} == before
        publish.assert_not_called()
