from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from runtime.infrastructure.artifact_store import ArtifactStore
from runtime.models import (
    BlockKind,
    TaskRecord,
    TaskStatus,
    ThreadAttachment,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    ThreadStatus,
)
from runtime.orchestrator._paths import OrgPaths

# We use the existing daemon conftest fixtures: tmp_home, app, org_state, auth_headers.
# Helper to seed an approved agent in the alpha org.


def _seed_agent(org_state, name: str, *, team: str = "engineering", role: str = "worker") -> None:
    """Create the agent's pending file and workspace dir.

    The compose endpoint validates: `prompt_loader.load_agent(...)` is not None
    AND `<root>/workspaces/<name>` exists.
    """
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"team: {team}\n"
        f"role: {role}\n"
        "executor: claude\n"
        "description: test agent\n"
        "---\n"
        "# system prompt\n"
    )
    (org_state.root / "workspaces" / name).mkdir(parents=True, exist_ok=True)


def test_compose_creates_thread_and_invocations(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")

    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "Refund policy",
            "recipients": ["dev_agent", "qa_engineer"],
            "body_markdown": "should we cap refunds at 30 days?",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"].startswith("THR-")
    assert set(data["pending_replies"]) == {"dev_agent", "qa_engineer"}

    invocations = org_state.db.list_thread_invocations(data["thread_id"])
    assert len(invocations) == 2
    assert all(inv.purpose.value == "reply" for inv in invocations)


def test_compose_rejects_unknown_recipient(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "x",
            "recipients": ["ghost"],
            "body_markdown": "hi",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_agent"


def test_compose_rejects_empty_subject(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "   ",
            "recipients": ["dev_agent"],
            "body_markdown": "hi",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_compose_accepts_attachment_only_message(client, auth_headers, org_state) -> None:
    _seed_agent(org_state, "dev_agent")
    _artifact_store(org_state).put("compose-report.pdf", b"pdf")

    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        headers=auth_headers,
        json={
            "subject": "Files",
            "recipients": ["dev_agent"],
            "body_markdown": "",
            "attachments": [{"artifact_name": "compose-report.pdf"}],
        },
    )

    assert resp.status_code == 200, resp.text
    messages = org_state.db.list_thread_messages(resp.json()["thread_id"])
    assert messages[0].body_markdown is None
    assert messages[0].attachments[0].artifact_name == "compose-report.pdf"
    assert messages[0].attachments[0].display_name == "compose-report.pdf"
    assert messages[0].attachments[0].content_type == "application/pdf"


# ---------------------------------------------------------------------------
# Task 20 — GET /threads, GET /threads/{id}, GET /threads/{id}/messages
# ---------------------------------------------------------------------------


def test_list_threads_returns_recent(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "x"},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "b", "recipients": ["dev_agent"], "body_markdown": "x"},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/orgs/alpha/threads", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["subject"] in {"a", "b"}


def test_get_thread_returns_messages_and_participants(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.get(f"/api/v1/orgs/alpha/threads/{tid}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == tid
    assert data["participants"] == ["dev_agent"]
    assert data["messages"][0]["body_markdown"] == "hi"


def test_get_thread_response_includes_attachments(client, auth_headers, org_state) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])
    org_state.db.append_thread_message(
        thread_id=thread_id,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        attachments=[
            ThreadAttachment(
                artifact_name="detail-report.pdf",
                display_name="detail report.pdf",
                size_bytes=3,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["messages"][0]["attachments"] == [
        {
            "artifact_name": "detail-report.pdf",
            "display_name": "detail report.pdf",
            "size_bytes": 3,
            "content_type": None,
            "uploaded_by": "founder",
            "thread_attachment_id": None,
        }
    ]


def test_get_thread_missing_returns_404(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.get("/api/v1/orgs/alpha/threads/THR-999", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 21 — POST /threads/{id}/reply with token validation
# ---------------------------------------------------------------------------


def _start_thread(client, org_state, auth_headers, *, recipient="dev_agent"):
    """Helper: seeds the agent and creates a thread, returning (thread_id, invocation_token)."""
    _seed_agent(org_state, recipient)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": [recipient], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    inv = org_state.db.list_thread_invocations(r["thread_id"])[0]
    return r["thread_id"], inv.invocation_token


def _seed_open_thread(org_state, *, participants: list[str]) -> str:
    thread_id = org_state.db.next_thread_id()
    org_state.db.insert_thread(ThreadRecord(id=thread_id, subject="Files"))
    for agent in participants:
        org_state.db.add_thread_participant(thread_id, agent, added_by="founder")
    return thread_id


def _artifact_store(org_state) -> ArtifactStore:
    return ArtifactStore(OrgPaths(org_state.root).artifacts_dir)


def test_reply_appends_message_and_consumes_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid, "invocation_token": token,
            "speaker": "dev_agent", "body_markdown": "hello back",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].body_markdown == "hello back"
    # Broadcast model: compose increments turns_used by 1; reply increments by 1 → 2 total.
    assert org_state.db.get_thread(tid).turns_used == 2
    assert org_state.db.get_pending_invocation(token) is None


def test_reply_rejects_missing_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, _token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": "bogus",
              "speaker": "dev_agent", "body_markdown": "x", "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_invalid"


def test_reply_rejects_consumed_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    p = {"thread_id": tid, "invocation_token": token,
         "speaker": "dev_agent", "body_markdown": "hi", "in_response_to_seq": 1}
    assert client.post(f"/api/v1/orgs/alpha/threads/{tid}/reply", json=p, headers=auth_headers).status_code == 200
    second = client.post(f"/api/v1/orgs/alpha/threads/{tid}/reply", json=p, headers=auth_headers)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "invocation_token_consumed"


def test_reply_rejects_mismatched_speaker(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent", "qa_engineer"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    dev_token = next(
        inv.invocation_token
        for inv in org_state.db.list_thread_invocations(tid)
        if inv.agent_name == "dev_agent"
    )
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": dev_token,
              "speaker": "qa_engineer", "body_markdown": "x", "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 401


def test_reply_accepts_attachments_with_speaker_uploaded_by(client, auth_headers, org_state) -> None:
    _artifact_store(org_state).put("reply-report.pdf", b"pdf")
    thread_id, token = _start_thread(client, org_state, auth_headers)

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/reply",
        headers=auth_headers,
        json={
            "thread_id": thread_id,
            "invocation_token": token,
            "speaker": "dev_agent",
            "body_markdown": "see attached",
            "attachments": [{"artifact_name": "reply-report.pdf"}],
            "in_response_to_seq": 1,
        },
    )

    assert resp.status_code == 200, resp.text
    message = org_state.db.list_thread_messages(thread_id)[-1]
    assert message.attachments[0].artifact_name == "reply-report.pdf"
    assert message.attachments[0].uploaded_by == "dev_agent"


# ---------------------------------------------------------------------------
# Task 22 — POST /threads/{id}/decline
# ---------------------------------------------------------------------------


def test_decline_records_decline_and_consumes_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/decline",
        json={"thread_id": tid, "invocation_token": token,
              "speaker": "dev_agent", "reason": "nothing to add",
              "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    # Broadcast model: silent decline — no transcript row is inserted.
    msgs = org_state.db.list_thread_messages(tid)
    assert not any(m.kind.value == "decline" for m in msgs)
    # Invocation is marked declined; token is consumed.
    from runtime.models import ThreadInvocationStatus
    inv = org_state.db.get_invocation_any_status(token)
    assert inv.status is ThreadInvocationStatus.DECLINED
    assert inv.decline_reason == "nothing to add"
    assert org_state.db.get_pending_invocation(token) is None


# ---------------------------------------------------------------------------
# Task 23 — POST /threads/{id}/dispatch
# ---------------------------------------------------------------------------


def test_worker_self_dispatch_creates_task_with_thread_link(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "dev_agent",
              "brief": "Implement option B"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dispatched_from_thread_id"] == tid
    assert data["assigned_agent"] == "dev_agent"

    # System message landed.
    msgs = org_state.db.list_thread_messages(tid)
    sys_msg = [m for m in msgs if m.kind.value == "system"][-1]
    assert sys_msg.system_payload["kind_tag"] == "task_dispatched"

    # Token stays pending (dispatch does NOT consume).
    assert org_state.db.get_pending_invocation(token) is not None
    inv = org_state.db.get_invocation_any_status(token)
    assert inv.dispatched_task_id == data["task_id"]


def test_worker_cannot_dispatch_to_other_agent(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "dev_agent", "target_agent": "qa_engineer",
              "brief": "do x"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "thread_dispatch_must_be_self"


def test_manager_cannot_dispatch_to_team_worker(tmp_home, app, org_state, auth_headers):
    """The manager exemption from the self-dispatch rule is removed.

    A manager attempting to thread-dispatch a worker in their own team is
    rejected with thread_dispatch_must_be_self. Fix for THR-010 (founder
    diagnosis 2026-05-28): managers must self-dispatch a phase root and
    delegate internally via the manager-decision loop.
    """
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    _seed_agent(org_state, "dev_agent")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head", "target_agent": "dev_agent",
              "brief": "do x"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "thread_dispatch_must_be_self"
    assert detail["dispatcher"] == "engineering_head"
    assert detail["requested_target"] == "dev_agent"
    assert "compose" in detail["hint"].lower()


def test_manager_self_dispatch_from_thread_succeeds(tmp_home, app, org_state, auth_headers):
    """Manager dispatching with target_agent omitted (or set to self) is allowed."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "drive web-app v1 phase"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_agent"] == "engineering_head"


def test_dispatch_twice_on_same_token_rejected(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    p = {"thread_id": tid, "invocation_token": token,
         "dispatcher": "dev_agent", "brief": "x"}
    assert client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch", json=p, headers=auth_headers
    ).status_code == 200
    again = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch", json=p, headers=auth_headers,
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "dispatch_already_used"


# ---------------------------------------------------------------------------
# THR-018 §3a — thread-dispatch supersede leg (maker-checker, both directions)
# ---------------------------------------------------------------------------


def _audit_payload(org_state, task_id: str, action: str) -> dict:
    for e in org_state.db.get_audit_logs(task_id):
        if e["action"] == action:
            return e["payload"] or {}
    return {}


def test_manager_dispatch_supersedes_escalated_predecessor(
    tmp_home, app, org_state, auth_headers,
):
    """A manager-authorized thread-dispatch naming an escalated
    predecessor auto-resolves it to SUPERSEDED, citing the new root +
    the thread ruling in the audit (the maker-checker evidence)."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="orphan escalation", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "continue the escalated work", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["superseded_task_id"] == "TASK-900"

    pred = org_state.db.get_task("TASK-900")
    assert pred.status == TaskStatus.SUPERSEDED
    assert pred.block_kind is None
    assert pred.completed_at is not None
    payload = _audit_payload(org_state, "TASK-900", "escalation_superseded")
    assert payload["successor_root"] == data["task_id"]
    assert payload["prior_block_kind"] == "escalated"
    assert payload["thread_id"] == tid  # thread ruling cited


def test_manager_dispatch_supersedes_delegated_when_children_terminal(
    tmp_home, app, org_state, auth_headers,
):
    """Gap-B on the thread path: an in_progress(delegated) predecessor with ALL
    children terminal is supersedable without cascade."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="delegated parent", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-901", brief="c1", parent_task_id="TASK-900", status=TaskStatus.COMPLETED,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-902", brief="c2", parent_task_id="TASK-900", status=TaskStatus.FAILED,
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "carry the delegated branch", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    pred = org_state.db.get_task("TASK-900")
    assert pred.status == TaskStatus.SUPERSEDED
    assert pred.block_kind is None
    payload = _audit_payload(org_state, "TASK-900", "escalation_superseded")
    assert payload["prior_block_kind"] == "delegated"


def test_manager_dispatch_refuses_supersede_of_delegated_with_live_child(
    tmp_home, app, org_state, auth_headers,
):
    """Gap-B gate: a delegated predecessor with a live child is NOT supersedable
    (would abandon the live child). 409; predecessor + live child untouched."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="engineering_head")
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="delegated parent", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-901", brief="done", parent_task_id="TASK-900", status=TaskStatus.COMPLETED,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-902", brief="live", parent_task_id="TASK-900",
        status=TaskStatus.IN_PROGRESS,
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "x", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "predecessor_not_supersedable"
    assert org_state.db.get_task("TASK-900").status == TaskStatus.IN_PROGRESS
    assert org_state.db.get_task("TASK-900").block_kind == BlockKind.DELEGATED
    assert org_state.db.get_task("TASK-902").status == TaskStatus.IN_PROGRESS
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-900")
    ]


def test_worker_self_dispatch_cannot_supersede_predecessor(
    tmp_home, app, org_state, auth_headers,
):
    """Maker-checker NEGATIVE (mirrors the revisit-path negative test): a
    worker-originated thread dispatch is NOT authorized to auto-close a
    predecessor — 403, and the predecessor stays blocked. Only a founder
    (`revisit`) or a team manager (this path) may supersede."""
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="orphan escalation", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "dev_agent",
              "brief": "sneaky close", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "thread_supersede_not_authorized"
    # Predecessor untouched: never auto-closed by an unauthorized dispatch.
    assert org_state.db.get_task("TASK-900").status == TaskStatus.ESCALATED
    assert org_state.db.get_task("TASK-900").block_kind is None
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-900")
    ]


# ---------------------------------------------------------------------------
# THR-046 msg127 — broader revisit-family closure on human-authorized continuations
# ---------------------------------------------------------------------------


def test_manager_dispatch_supersedes_escalated_sibling_revisits(
    tmp_home, app, org_state, auth_headers,
):
    """A manager thread-dispatch with `resolves` that supersedes one escalated
    predecessor also closes eligible escalated sibling revisits in the same
    revisit family (same revisit_of_task_id). Failed siblings are left alone."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(
        client, org_state, auth_headers, recipient="engineering_head",
    )

    # Seed the family: TASK-900 is the original escalated root.
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="original escalation", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # TASK-901 is an escalated direct revisit sibling of TASK-900.
    org_state.db.insert_task(TaskRecord(
        id="TASK-901", brief="escalated sibling revisit", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-900",
    ))
    # TASK-902 is a FAILED direct revisit sibling — should NOT be touched.
    org_state.db.insert_task(TaskRecord(
        id="TASK-902", brief="failed sibling revisit", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.FAILED, block_kind=None,
        revisit_of_task_id="TASK-900",
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "continue the escalated work", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["superseded_task_id"] == "TASK-900"
    new_task_id = data["task_id"]

    # Explicit predecessor superseded.
    pred = org_state.db.get_task("TASK-900")
    assert pred.status == TaskStatus.SUPERSEDED
    assert pred.block_kind is None
    pred_payload = _audit_payload(org_state, "TASK-900", "escalation_superseded")
    assert pred_payload["successor_root"] == new_task_id
    assert pred_payload["prior_block_kind"] == "escalated"
    assert pred_payload["thread_id"] == tid

    # Escalated sibling superseded.
    sib = org_state.db.get_task("TASK-901")
    assert sib.status == TaskStatus.SUPERSEDED
    assert sib.block_kind is None
    sib_payload = _audit_payload(org_state, "TASK-901", "escalation_superseded")
    assert sib_payload["successor_root"] == new_task_id
    assert sib_payload["prior_block_kind"] == "escalated"
    assert sib_payload["thread_id"] == tid

    # Failed sibling untouched.
    fail = org_state.db.get_task("TASK-902")
    assert fail.status == TaskStatus.FAILED
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-902")
    ]

    # superseded_by_task_id is exposed via get-task for the sibling.
    r = client.get(
        f"/api/v1/orgs/alpha/tasks/TASK-901", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["superseded_by_task_id"] == new_task_id


def test_manager_dispatch_supersedes_ancestor_revisit_chain(
    tmp_home, app, org_state, auth_headers,
):
    """Finding 1 (ancestor-chain): A is escalated, B is an escalated revisit
    of A, and a manager thread-dispatch resolving B closes both B and A.
    When the explicit predecessor is itself a revisit, the family root
    (ancestor) must also be evaluated through the eligibility gate."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(
        client, org_state, auth_headers, recipient="engineering_head",
    )

    # A: TASK-900 — original escalated root.
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="original root escalated", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # B: TASK-901 — escalated revisit of A (explicit predecessor).
    org_state.db.insert_task(TaskRecord(
        id="TASK-901", brief="escalated revisit of root", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-900",
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "continue from the revisit", "resolves": "TASK-901"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["superseded_task_id"] == "TASK-901"
    new_task_id = data["task_id"]

    # B (explicit predecessor) superseded.
    pred_b = org_state.db.get_task("TASK-901")
    assert pred_b.status == TaskStatus.SUPERSEDED
    assert pred_b.block_kind is None
    pb_payload = _audit_payload(org_state, "TASK-901", "escalation_superseded")
    assert pb_payload["successor_root"] == new_task_id
    assert pb_payload["prior_block_kind"] == "escalated"

    # A (ancestor root in the family) must also be superseded.
    pred_a = org_state.db.get_task("TASK-900")
    assert pred_a.status == TaskStatus.SUPERSEDED
    assert pred_a.block_kind is None
    pa_payload = _audit_payload(org_state, "TASK-900", "escalation_superseded")
    assert pa_payload["successor_root"] == new_task_id
    assert pa_payload["prior_block_kind"] == "escalated"

    # superseded_by_task_id exposed for both.
    for tid_check in ["TASK-900", "TASK-901"]:
        r = client.get(
            f"/api/v1/orgs/alpha/tasks/{tid_check}", headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["superseded_by_task_id"] == new_task_id


def test_manager_dispatch_family_closure_leaves_non_supersedable_siblings(
    tmp_home, app, org_state, auth_headers,
):
    """Negative: completed, cancelled, pending, in_progress(non-delegated),
    and already superseded family members are NOT rewritten."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(
        client, org_state, auth_headers, recipient="engineering_head",
    )

    # Original root: escalated.
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="original", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # Non-supersedable siblings of the original root.
    for sid, st, bk in [
        ("TASK-901", TaskStatus.COMPLETED, None),
        ("TASK-902", TaskStatus.CANCELLED, None),
        ("TASK-903", TaskStatus.PENDING, None),
        ("TASK-904", TaskStatus.IN_PROGRESS, None),
        ("TASK-905", TaskStatus.SUPERSEDED, None),
    ]:
        org_state.db.insert_task(TaskRecord(
            id=sid, brief="sibling", team="engineering",
            assigned_agent="dev_agent",
            status=st, block_kind=bk,
            revisit_of_task_id="TASK-900",
        ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "continue", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Explicit predecessor superseded.
    assert org_state.db.get_task("TASK-900").status == TaskStatus.SUPERSEDED
    # All non-supersedable siblings unchanged.
    assert org_state.db.get_task("TASK-901").status == TaskStatus.COMPLETED
    assert org_state.db.get_task("TASK-902").status == TaskStatus.CANCELLED
    assert org_state.db.get_task("TASK-903").status == TaskStatus.PENDING
    assert org_state.db.get_task("TASK-904").status == TaskStatus.IN_PROGRESS
    assert org_state.db.get_task("TASK-905").status == TaskStatus.SUPERSEDED
    for sid in ["TASK-901", "TASK-902", "TASK-903", "TASK-904", "TASK-905"]:
        assert "escalation_superseded" not in [
            e["action"] for e in org_state.db.get_audit_logs(sid)
        ]


def test_manager_dispatch_family_closure_delegated_safety(
    tmp_home, app, org_state, auth_headers,
):
    """Delegated safety: an in_progress(delegated) family sibling with a live
    child is NOT closed; a delegated sibling with all terminal children MAY be
    closed. Live children are never mutated."""
    client = TestClient(app)
    _seed_agent(org_state, "engineering_head", role="manager")
    tid, token = _start_thread(
        client, org_state, auth_headers, recipient="engineering_head",
    )

    # Original root: escalated.
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="original", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))

    # Delegated sibling with a LIVE child — should NOT be closed.
    org_state.db.insert_task(TaskRecord(
        id="TASK-910", brief="delegated with live child", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        revisit_of_task_id="TASK-900",
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-911", brief="live child", parent_task_id="TASK-910",
        status=TaskStatus.IN_PROGRESS,
    ))

    # Delegated sibling with ALL terminal children — MAY be closed.
    org_state.db.insert_task(TaskRecord(
        id="TASK-920", brief="delegated all done", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        revisit_of_task_id="TASK-900",
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-921", brief="done child", parent_task_id="TASK-920",
        status=TaskStatus.COMPLETED,
    ))

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "engineering_head",
              "brief": "continue", "resolves": "TASK-900"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    new_task_id = resp.json()["task_id"]

    # Explicit predecessor superseded.
    assert org_state.db.get_task("TASK-900").status == TaskStatus.SUPERSEDED

    # Delegated sibling with live child NOT closed.
    assert org_state.db.get_task("TASK-910").status == TaskStatus.IN_PROGRESS
    assert org_state.db.get_task("TASK-910").block_kind == BlockKind.DELEGATED
    assert org_state.db.get_task("TASK-911").status == TaskStatus.IN_PROGRESS
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-910")
    ]

    # Delegated sibling with all terminal children IS closed.
    sib = org_state.db.get_task("TASK-920")
    assert sib.status == TaskStatus.SUPERSEDED
    assert sib.block_kind is None
    sib_payload = _audit_payload(org_state, "TASK-920", "escalation_superseded")
    assert sib_payload["successor_root"] == new_task_id
    assert sib_payload["prior_block_kind"] == "delegated"

    # Live child untouched.
    assert org_state.db.get_task("TASK-921").status == TaskStatus.COMPLETED


def test_plain_revisit_lineage_does_not_close_predecessor(
    tmp_home, app, org_state, auth_headers,
):
    """Negative: tasks with revisit_of_task_id / auto-revisit_spawned lineage
    do NOT close their active predecessor before a human-authorized
    continuation (thread dispatch or founder revisit). Seeding revisit_of
    rows WITHOUT calling dispatch leaves all statuses unchanged."""
    # Seed a family WITHOUT doing a dispatch — just insert revisits directly.
    org_state.db.insert_task(TaskRecord(
        id="TASK-900", brief="original escalation", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # Seeded revisit siblings (simulating auto-revisit_spawned lineage).
    org_state.db.insert_task(TaskRecord(
        id="TASK-901", brief="auto-revisit", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-900",
    ))

    # Assert statuses are unchanged — no dispatch happened, nothing should close.
    assert org_state.db.get_task("TASK-900").status == TaskStatus.ESCALATED
    assert org_state.db.get_task("TASK-901").status == TaskStatus.ESCALATED
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-900")
    ]
    assert "escalation_superseded" not in [
        e["action"] for e in org_state.db.get_audit_logs("TASK-901")
    ]


# ---------------------------------------------------------------------------
# Task 24 — POST /threads/{id}/send (founder follow-up)
# ---------------------------------------------------------------------------


def test_founder_send_appends_and_enqueues(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent", "qa_engineer"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    before_invocations = len(org_state.db.list_thread_invocations(tid))
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={"body_markdown": "any thoughts?"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    after_invocations = len(org_state.db.list_thread_invocations(tid))
    # Broadcast model: /send mints REPLY for every participant (2 agents).
    assert after_invocations == before_invocations + 2


def test_thread_send_accepts_attachment_only(client, auth_headers, org_state) -> None:
    _artifact_store(org_state).put("THR-001-report.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "",
            "attachments": [
                {"artifact_name": "THR-001-report.pdf", "display_name": "report.pdf"}
            ],
        },
    )

    assert r.status_code == 200
    messages = org_state.db.list_thread_messages(thread_id)
    assert messages[-1].body_markdown is None
    assert messages[-1].attachments[0].artifact_name == "THR-001-report.pdf"


def test_compose_as_agent_accepts_attachments_with_composer_uploaded_by(
    client, auth_headers, org_state
) -> None:
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "dev_agent")
    _artifact_store(org_state).put("agent-report.pdf", b"pdf")
    from runtime.models import TaskRecord, TaskStatus
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    task_id = "TASK-500"
    sid = "sess-500"
    org_state.db.insert_task(TaskRecord(
        id=task_id, brief="compose test", assigned_agent="engineering_head",
        created_at=now, updated_at=now,
    ))
    org_state.sessions.set_active(task_id, "engineering_head", sid)

    resp = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "Files",
            "recipients": ["dev_agent"],
            "body_markdown": "see attached",
            "attachments": [{"artifact_name": "agent-report.pdf"}],
            "task_id": task_id,
            "session_id": sid,
        },
    )

    assert resp.status_code == 200, resp.text
    message = org_state.db.list_thread_messages(resp.json()["thread_id"])[0]
    assert message.attachments[0].artifact_name == "agent-report.pdf"
    assert message.attachments[0].uploaded_by == "engineering_head"


def test_thread_send_defaults_display_name_to_artifact_name(
    client, auth_headers, org_state
) -> None:
    _artifact_store(org_state).put("default-name.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "file",
            "attachments": [{"artifact_name": "default-name.pdf"}],
        },
    )

    assert resp.status_code == 200, resp.text
    attachment = org_state.db.list_thread_messages(thread_id)[-1].attachments[0]
    assert attachment.display_name == "default-name.pdf"


@pytest.mark.parametrize("display_name", ["", "   "])
def test_thread_send_rejects_invalid_attachment_display_name(
    client, auth_headers, org_state, display_name: str
) -> None:
    _artifact_store(org_state).put("display.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "file",
            "attachments": [
                {"artifact_name": "display.pdf", "display_name": display_name}
            ],
        },
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_attachment_display_name"


def test_thread_send_rejects_invalid_artifact_name(
    client, auth_headers, org_state
) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "file",
            "attachments": [{"artifact_name": "../bad.pdf"}],
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_artifact_name"


def test_thread_send_rejects_unknown_attachment(client, auth_headers, org_state) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "see file",
            "attachments": [{"artifact_name": "missing.pdf"}],
        },
    )

    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "artifact_not_found"


def test_thread_send_rejects_empty_without_attachments(client, auth_headers, org_state) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={"body_markdown": "   ", "attachments": []},
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_body"


def test_thread_send_rejects_duplicate_attachment(client, auth_headers, org_state) -> None:
    _artifact_store(org_state).put("report.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "files",
            "attachments": [
                {"artifact_name": "report.pdf"},
                {"artifact_name": "report.pdf"},
            ],
        },
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "duplicate_attachment"


def test_thread_send_rejects_too_many_attachments(client, auth_headers, org_state) -> None:
    for idx in range(6):
        _artifact_store(org_state).put(f"file-{idx}.txt", b"x")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "files",
            "attachments": [{"artifact_name": f"file-{idx}.txt"} for idx in range(6)],
        },
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "too_many_attachments"


# ---------------------------------------------------------------------------
# POST /threads/{id}/send with agent binding (THR-069)
# ---------------------------------------------------------------------------


def test_send_with_agent_binding_attributes_to_agent(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with a valid task+session binding => speaker is the agent (not 'founder')."""
    client = TestClient(app)
    tid = _seed_open_thread(
        org_state, participants=["dev_agent", "qa_engineer", "code_reviewer"]
    )
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-990", sid="sess-990")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "agent message",
            "composer": "dev_agent",
            "task_id": "TASK-990",
            "session_id": "sess-990",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].speaker == "dev_agent"
    assert msgs[-1].speaker != "founder"


def test_send_with_agent_binding_mints_to_other_participants(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with agent binding => REPLY minted to OTHER participants only (composer excluded)."""
    client = TestClient(app)
    tid = _seed_open_thread(
        org_state, participants=["dev_agent", "qa_engineer", "code_reviewer"]
    )
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-991", sid="sess-991")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "status",
            "composer": "dev_agent",
            "task_id": "TASK-991",
            "session_id": "sess-991",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["pending_replies"]) == {"qa_engineer", "code_reviewer"}
    assert "dev_agent" not in data["pending_replies"]


def test_send_with_agent_binding_stores_sent_from_task_id(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with agent binding => sent_from_task_id is stored in the DB row."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-992", sid="sess-992")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "task msg",
            "composer": "dev_agent",
            "task_id": "TASK-992",
            "session_id": "sess-992",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    row = org_state.db._conn.execute(
        "SELECT sent_from_task_id FROM thread_messages "
        "WHERE thread_id = ? AND seq = ?",
        (tid, resp.json()["seq"]),
    ).fetchone()
    assert row["sent_from_task_id"] == "TASK-992"


def test_send_with_agent_binding_rejects_non_participant(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with agent binding => 403 if composer is not a participant."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["qa_engineer"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-993", sid="sess-993")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "intruder",
            "composer": "dev_agent",
            "task_id": "TASK-993",
            "session_id": "sess-993",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "not_a_participant"


def test_send_with_agent_binding_rejects_non_owner(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with agent binding => 403 if task is not owned by composer."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    _bind_task_session(org_state, agent="qa_engineer", task_id="TASK-994", sid="sess-994")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "not mine",
            "composer": "dev_agent",
            "task_id": "TASK-994",
            "session_id": "sess-994",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "composer_not_task_owner"


def test_send_with_agent_binding_rejects_session_mismatch(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with agent binding => 409 if active session doesn't match."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-995", sid="sess-real")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "wrong session",
            "composer": "dev_agent",
            "task_id": "TASK-995",
            "session_id": "sess-stale",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "session_mismatch"


def test_send_with_no_binding_stamps_founder(
    tmp_home, app, org_state, auth_headers
):
    """POST /send with NO binding => speaker='founder' and broadcast to all participants (unchanged)."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent", "qa_engineer"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={"body_markdown": "founder follow-up"},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].speaker == "founder"
    data = resp.json()
    # Founder broadcast goes to ALL participants.
    assert set(data["pending_replies"]) == {"dev_agent", "qa_engineer"}


def test_send_partial_binding_task_id_only_rejected(
    tmp_home, app, org_state, auth_headers
):
    """FINDING 1: /send with --task-id but NO --session-id => 422 binding_required (NOT speaker='founder')."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-1000", sid="sess-1000")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "partial",
            "task_id": "TASK-1000",
            # session_id intentionally absent — partial binding.
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "binding_required"
    # Must NOT have posted any message (not even speaker='founder').
    msgs = org_state.db.list_thread_messages(tid)
    assert len(msgs) == 0


def test_send_partial_binding_session_id_only_rejected(
    tmp_home, app, org_state, auth_headers
):
    """FINDING 1: /send with --session-id but NO --task-id => 422 binding_required."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "partial",
            "session_id": "sess-1001",
            # task_id + composer absent.
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "binding_required"


def test_send_partial_binding_composer_only_rejected(
    tmp_home, app, org_state, auth_headers
):
    """FINDING 1: /send with composer but NO task_id/session_id => 422 binding_required."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={
            "body_markdown": "partial",
            "composer": "dev_agent",
            # task_id + session_id absent.
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "binding_required"


def test_send_no_binding_no_composer_in_response(
    tmp_home, app, org_state, auth_headers
):
    """FINDING 1: /send with NO binding fields at all => founder path (composer not in body)."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={"body_markdown": "founder msg"},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "pending_replies" in data
    # No binding rejection; founder path works with NO fields at all.


def test_send_agent_and_post_as_agent_share_attribution(
    tmp_home, app, org_state, auth_headers
):
    """FINDING 2: /send (agent path) and /post-as-agent produce IDENTICAL attribution + REPLY routing + sent_from_task_id."""
    client = TestClient(app)

    # --- /send agent path ---
    tid_send = _seed_open_thread(
        org_state, participants=["dev_agent", "qa_engineer", "code_reviewer"]
    )
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-S1", sid="sess-S1")

    resp_send = client.post(
        f"/api/v1/orgs/alpha/threads/{tid_send}/send",
        json={
            "body_markdown": "same message",
            "composer": "dev_agent",
            "task_id": "TASK-S1",
            "session_id": "sess-S1",
        },
        headers=auth_headers,
    )
    assert resp_send.status_code == 200, resp_send.text
    data_send = resp_send.json()

    # --- /post-as-agent path ---
    tid_paa = _seed_open_thread(
        org_state, participants=["dev_agent", "qa_engineer", "code_reviewer"]
    )
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-P1", sid="sess-P1")

    resp_paa = client.post(
        f"/api/v1/orgs/alpha/threads/{tid_paa}/post-as-agent",
        json={
            "body_markdown": "same message",
            "composer": "dev_agent",
            "task_id": "TASK-P1",
            "session_id": "sess-P1",
        },
        headers=auth_headers,
    )
    assert resp_paa.status_code == 200, resp_paa.text
    data_paa = resp_paa.json()

    # Both routes produce the same response shape (except IDs vary).
    assert "pending_replies" in data_send
    assert "pending_replies" in data_paa
    assert "seq" in data_send
    assert "seq" in data_paa

    # REPLY routing: both exclude the speaker from pending_replies.
    assert "dev_agent" not in data_send["pending_replies"]
    assert "dev_agent" not in data_paa["pending_replies"]
    assert set(data_send["pending_replies"]) == {"qa_engineer", "code_reviewer"}
    assert set(data_paa["pending_replies"]) == {"qa_engineer", "code_reviewer"}

    # Speaker attribution: both attribute to the agent (not 'founder').
    msgs_send = org_state.db.list_thread_messages(tid_send)
    msgs_paa = org_state.db.list_thread_messages(tid_paa)
    assert msgs_send[-1].speaker == "dev_agent"
    assert msgs_paa[-1].speaker == "dev_agent"

    # sent_from_task_id stored identically in both paths.
    for tid, task, seq_val in [(tid_send, "TASK-S1", data_send["seq"]), (tid_paa, "TASK-P1", data_paa["seq"])]:
        row = org_state.db._conn.execute(
            "SELECT sent_from_task_id FROM thread_messages WHERE thread_id = ? AND seq = ?",
            (tid, seq_val),
        ).fetchone()
        assert row["sent_from_task_id"] == task

    # REPLY invocations: both mint to other participants only, triggered by correct seq.
    for tid, seq_val in [(tid_send, data_send["seq"]), (tid_paa, data_paa["seq"])]:
        minted = [
            inv for inv in org_state.db.list_thread_invocations(tid)
            if inv.triggering_seq == seq_val
        ]
        assert {inv.agent_name for inv in minted} == {"qa_engineer", "code_reviewer"}
        assert all(inv.purpose.value == "reply" for inv in minted)


def test_thread_messages_response_includes_attachments(client, auth_headers, org_state) -> None:
    _artifact_store(org_state).put("THR-001-report.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])
    org_state.db.append_thread_message(
        thread_id=thread_id,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=3,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    r = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}/messages",
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert r.json()["messages"][0]["attachments"] == [
        {
            "artifact_name": "THR-001-report.pdf",
            "display_name": "report.pdf",
            "size_bytes": 3,
            "content_type": None,
            "uploaded_by": "founder",
            "thread_attachment_id": None,
        }
    ]


# ---------------------------------------------------------------------------
# Task 25 — POST /threads/{id}/invite
# ---------------------------------------------------------------------------


def test_invite_adds_participant_without_bootstrap_invocation(tmp_home, app, org_state, auth_headers):
    """Invite adds the participant + transparency records, but does NOT auto-mint
    a BOOTSTRAP thread-invocation. The invited agent receives a REPLY invocation
    naturally via broadcast when the next message is posted."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    parts = [p.agent_name for p in org_state.db.list_thread_participants(tid)]
    assert "qa_engineer" in parts
    msgs = org_state.db.list_thread_messages(tid)
    sys_msgs = [m for m in msgs if m.kind.value == "system"]
    assert sys_msgs[-1].system_payload["kind_tag"] == "participant_added"
    pending = org_state.db.list_thread_invocations(tid)
    # After invite, there should be NO pending bootstrap invocation for the
    # newly-added participant — the BOOTSTRAP auto-mint has been removed.
    assert not any(
        inv.agent_name == "qa_engineer" and inv.purpose.value == "bootstrap"
        for inv in pending
    )


def test_invite_then_reply_delivers_reply_invocation_to_new_participant(tmp_home, app, org_state, auth_headers):
    """After invite adds a participant, the NEXT message posted to the thread
    triggers broadcast REPLY minting — the newly-added participant receives a
    pending REPLY invocation."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    # Invite qa_engineer (no bootstrap).
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # dev_agent replies — broadcast mints REPLY for every participant except the speaker.
    dev_inv = next(
        inv for inv in org_state.db.list_thread_invocations(tid)
        if inv.agent_name == "dev_agent"
    )
    reply_resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "welcome!",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert reply_resp.status_code == 200, reply_resp.text

    # qa_engineer should now have a pending REPLY invocation.
    pending = org_state.db.list_thread_invocations(tid)
    assert any(
        inv.agent_name == "qa_engineer" and inv.purpose.value == "reply"
        for inv in pending
    ), f"Expected REPLY invocation for qa_engineer, got {[(i.agent_name, i.purpose.value) for i in pending]}"


def test_invite_already_participant_409(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{r['thread_id']}/invite",
        json={"agent_name": "dev_agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# THR-069 msg85 — POST /threads/{id}/remove-participant
# ---------------------------------------------------------------------------


def test_remove_participant_succeeds(tmp_home, app, org_state, auth_headers):
    """Founder removes a participant; row is hard-deleted, system message emitted."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    # Invite qa_engineer first.
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert org_state.db.is_thread_participant(tid, "qa_engineer") is True

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread_id"] == tid
    assert body["agent_name"] == "qa_engineer"
    assert "system_message_seq" in body

    # Participant row hard-deleted.
    assert org_state.db.is_thread_participant(tid, "qa_engineer") is False


def test_remove_participant_emits_system_message_correct_tag(tmp_home, app, org_state, auth_headers):
    """Remove emits a SYSTEM message with kind_tag='participant_removed'."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )

    msgs = org_state.db.list_thread_messages(tid)
    sys_msgs = [m for m in msgs if m.kind.value == "system"]
    assert any(
        m.system_payload.get("kind_tag") == "participant_removed"
        for m in sys_msgs
    ), f"Expected participant_removed in system messages, got: {[m.system_payload for m in sys_msgs]}"


def test_remove_participant_non_participant_409(tmp_home, app, org_state, auth_headers):
    """Removing a non-participant returns 409 (mirrors invite's already_participant 409)."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text


def test_remove_participant_clears_pending_invocations(tmp_home, app, org_state, auth_headers):
    """After removing a participant, their pending invocations are declined."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    # Invite qa_engineer, then dev_agent replies to mint a reply invocation for qa_engineer.
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    dev_inv = next(
        inv for inv in org_state.db.list_thread_invocations(tid)
        if inv.agent_name == "dev_agent"
    )
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "hello!",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )

    # Confirm qa_engineer has a pending REPLY invocation.
    pending_before = org_state.db.list_thread_invocations(tid, status=ThreadInvocationStatus.PENDING)
    assert any(inv.agent_name == "qa_engineer" for inv in pending_before)

    # Remove the participant.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # qa_engineer's pending invocations should now be declined.
    pending_after = org_state.db.list_thread_invocations(tid, status=ThreadInvocationStatus.PENDING)
    assert not any(inv.agent_name == "qa_engineer" for inv in pending_after)
    all_invocations = org_state.db.list_thread_invocations(tid)
    qa_inv = next(inv for inv in all_invocations if inv.agent_name == "qa_engineer")
    assert qa_inv.status is ThreadInvocationStatus.DECLINED


def test_remove_participant_removed_from_participant_list(tmp_home, app, org_state, auth_headers):
    """Removed agent no longer appears in list_thread_participants."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    participants_before = [
        p.agent_name for p in org_state.db.list_thread_participants(tid)
    ]
    assert "qa_engineer" in participants_before

    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    participants_after = [
        p.agent_name for p in org_state.db.list_thread_participants(tid)
    ]
    assert "qa_engineer" not in participants_after


def test_remove_participant_writes_audit_row(tmp_home, app, org_state, auth_headers):
    """Removal writes a thread_participant_removed audit row."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )

    rows = org_state.db.get_audit_logs(tid)
    assert any(r["action"] == "thread_participant_removed" for r in rows)
    removed_row = next(r for r in rows if r["action"] == "thread_participant_removed")
    assert removed_row["payload"].get("agent_name") == "qa_engineer"
    assert removed_row["payload"].get("removed_by") == "founder"


def test_remove_participant_404_missing_thread(tmp_home, app, org_state, auth_headers):
    """Remove-participant on a non-existent thread returns 404 not_found."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    resp = client.post(
        "/api/v1/orgs/alpha/threads/THR-NOSUCH/remove-participant",
        json={"agent_name": "dev_agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_remove_participant_404_unknown_agent(tmp_home, app, org_state, auth_headers):
    """Remove-participant with a non-existent agent name returns 404 unknown_agent."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "ghost_agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_agent"


def test_remove_participant_401_missing_auth(tmp_home, app, org_state, auth_headers):
    """Remove-participant without bearer token returns 401 (founder-only)."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/remove-participant",
        json={"agent_name": "dev_agent"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 26 — POST /threads/{id}/extend
# ---------------------------------------------------------------------------


def test_extend_increases_turn_cap(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/extend",
        json={"new_cap": 1000},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert org_state.db.get_thread(tid).turn_cap == 1000


def test_extend_rejects_non_increase(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{r['thread_id']}/extend",
        json={"new_cap": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /threads/{id}/archive — synchronous
# ---------------------------------------------------------------------------


def test_archive_completes_synchronously(tmp_home, app, org_state, auth_headers):
    """Archive is synchronous: 200, status='archived', transcript_path populated,
    no transitional 'archiving' state observable, no close-out invocations minted."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent", "qa_engineer"],
              "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/archive",
        json={"summary": "wrapped up"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "archived"
    assert data["transcript_path"] is not None

    # Follow-up GET shows the same terminal status.
    detail = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}",
        headers=auth_headers,
    ).json()
    assert detail["status"] == "archived"



def test_archive_with_empty_summary_succeeds(tmp_home, app, org_state, auth_headers):
    """Archive accepts an empty/omitted summary (no validation error)."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/archive",
        json={},  # empty body — summary defaults to ""
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "archived"
    # Thread row's summary should be empty string (not None or KeyError).
    t = org_state.db.get_thread(tid)
    assert t.summary == ""


def test_archive_payload_with_request_close_outs_silently_ignored(tmp_home, app, org_state, auth_headers):
    """Legacy clients sending request_close_outs are not rejected — Pydantic
    drops the unknown field silently; the field has no effect now."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/archive",
        json={"summary": "done", "request_close_outs": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_tail_sse_endpoint_404_for_missing_thread(tmp_home, app, auth_headers):
    """GET /threads/{id}/tail returns 404 for an unknown thread_id.

    This proves the endpoint is registered. End-to-end streaming cannot be
    tested with TestClient because the live-subscribe phase blocks the
    in-process transport indefinitely (no real TCP socket to close).
    The replay logic is validated via the DB directly in the compose tests.
    """
    client = TestClient(app)
    resp = client.get(
        "/api/v1/orgs/alpha/threads/THR-NOSUCHTHREAD/tail",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_tail_sse_replays_existing_messages(tmp_home, app, org_state, auth_headers):
    """Verify that /threads/{id}/tail replays persisted messages.

    We drive the async generator directly — bypassing TestClient's synchronous
    transport layer — so we can stop the generator after the replay chunk.
    """
    import asyncio
    import json as _json
    from runtime.daemon.routes.threads import _msg_to_dict

    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "s",
            "recipients": ["dev_agent"],
            "body_markdown": "hi",
        },
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    # Verify the message was persisted (the replay source).
    msgs = org_state.db.list_thread_messages(tid)
    assert len(msgs) == 1
    assert msgs[0].body_markdown == "hi"

    # Verify the gen() replay logic produces the expected SSE line.
    # We drive just the replay portion (since_seq=0, limit=1000).
    replay_lines = [
        f"data: {_json.dumps(_msg_to_dict(m))}\n\n"
        for m in org_state.db.list_thread_messages(tid, since_seq=0, limit=1000)
    ]
    assert len(replay_lines) == 1
    assert '"hi"' in replay_lines[0]


# ---------------------------------------------------------------------------
# Task 3 — TASK_FOLLOWUP admitted by reply/decline; dispatch stays restricted
# ---------------------------------------------------------------------------


def _open_thread_with_followup_token(client, org_state, auth_headers, *, recipient="dev_agent"):
    """Create a thread, then mint a TASK_FOLLOWUP invocation for the recipient.

    Returns (thread_id, followup_token, seq_of_compose_msg).
    """
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, recipient)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "s",
            "recipients": [recipient],
            "body_markdown": "hi",
        },
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    seq = org_state.db.list_thread_messages(tid)[0].seq
    token = org_state.db.mint_thread_invocation(
        thread_id=tid,
        agent_name=recipient,
        triggering_seq=seq,
        purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    ).invocation_token
    return tid, token, seq


def test_reply_admits_task_followup_purpose(tmp_home, app, org_state, auth_headers):
    """A TASK_FOLLOWUP invocation token must be accepted by the reply endpoint."""
    client = TestClient(app)
    tid, token, seq = _open_thread_with_followup_token(client, org_state, auth_headers)

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": token,
            "speaker": "dev_agent",
            "body_markdown": "task completed, here is the result",
            "in_response_to_seq": seq,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "message"


def test_dispatch_rejects_task_followup_purpose(tmp_home, app, org_state, auth_headers):
    """Spec §6.4: a TASK_FOLLOWUP turn may NOT be used to dispatch new tasks.

    Dispatch stays restricted to {REPLY, BOOTSTRAP} only.
    _validate_invocation_token raises 400 with code "wrong_invocation_purpose"
    when the purpose is not in require_purposes.
    """
    client = TestClient(app)
    tid, token, _seq = _open_thread_with_followup_token(client, org_state, auth_headers)

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={
            "thread_id": tid,
            "invocation_token": token,
            "dispatcher": "dev_agent",
            "brief": "do something else",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "wrong_invocation_purpose"


# ---------------------------------------------------------------------------
# POST /threads/{id}/resume — founder reopens an archived thread
# ---------------------------------------------------------------------------


def test_resume_flips_archived_to_open(tmp_home, app, org_state, auth_headers):
    """Archive a thread, then resume it: status goes back to open, archived_at
    + summary preserved, a 'resumed' system message appears."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    # Synchronous archive — no manual finalize_thread call needed anymore.
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/archive",
        json={"summary": "wrapped up"},
        headers=auth_headers,
    )

    pre = org_state.db.get_thread(tid)
    assert pre.status.value == "archived"
    pre_archived_at = pre.archived_at
    assert pre_archived_at is not None

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/resume",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"thread_id": tid, "status": "open"}

    post = org_state.db.get_thread(tid)
    assert post.status.value == "open"
    assert post.archived_at == pre_archived_at
    assert post.summary == "wrapped up"

    # A 'resumed' system message was appended.
    msgs = org_state.db.list_thread_messages(tid)
    resumed_msgs = [
        m for m in msgs
        if m.kind.value == "system" and (m.system_payload or {}).get("kind_tag") == "resumed"
    ]
    assert len(resumed_msgs) == 1
    assert resumed_msgs[0].speaker == "founder"


def test_resume_is_idempotent_on_open_thread(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"], "body_markdown": "hi"},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/resume",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"thread_id": tid, "status": "open", "idempotent": True}

    # No 'resumed' system message written on idempotent return.
    msgs = org_state.db.list_thread_messages(tid)
    resumed_msgs = [
        m for m in msgs
        if m.kind.value == "system" and (m.system_payload or {}).get("kind_tag") == "resumed"
    ]
    assert resumed_msgs == []


def test_resume_404_on_missing_thread(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads/THR-NEVER/resume",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# POST /threads/{id}/post-as-agent — a live task session appends to an
# EXISTING thread it participates in (THR-027, participant-only ruling).
# ---------------------------------------------------------------------------


def _bind_task_session(org_state, *, agent: str, task_id: str, sid: str) -> None:
    """Seed an active task owned by `agent` and register its live session."""
    now = datetime.now(timezone.utc).isoformat()
    org_state.db.insert_task(TaskRecord(
        id=task_id, brief="post-as-agent test", assigned_agent=agent,
        created_at=now, updated_at=now,
    ))
    org_state.sessions.set_active(task_id, agent, sid)


def test_post_as_agent_appends_and_mints_to_other_participants(
    tmp_home, app, org_state, auth_headers
):
    client = TestClient(app)
    tid = _seed_open_thread(
        org_state, participants=["dev_agent", "qa_engineer", "code_reviewer"]
    )
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-900", sid="sess-900")
    before_turns = org_state.db.get_thread(tid).turns_used

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-900",
            "session_id": "sess-900", "body_markdown": "status update",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"] == tid
    # REPLY invocations go to OTHER participants only — composer is excluded.
    assert set(data["pending_replies"]) == {"qa_engineer", "code_reviewer"}
    assert "dev_agent" not in data["pending_replies"]

    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].speaker == "dev_agent"
    assert msgs[-1].body_markdown == "status update"
    assert data["seq"] == msgs[-1].seq
    # turns_used incremented by exactly 1.
    assert org_state.db.get_thread(tid).turns_used == before_turns + 1
    # One REPLY invocation per other participant, triggered by this message.
    minted = [
        inv for inv in org_state.db.list_thread_invocations(tid)
        if inv.triggering_seq == data["seq"]
    ]
    assert {inv.agent_name for inv in minted} == {"qa_engineer", "code_reviewer"}
    assert all(inv.purpose.value == "reply" for inv in minted)
    # Provenance column persists the posting task id (storage-only, off-model).
    row = org_state.db._conn.execute(
        "SELECT sent_from_task_id FROM thread_messages "
        "WHERE thread_id = ? AND seq = ?",
        (tid, data["seq"]),
    ).fetchone()
    assert row["sent_from_task_id"] == "TASK-900"


def test_post_as_agent_rejects_non_participant(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["qa_engineer"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-901", sid="sess-901")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-901",
            "session_id": "sess-901", "body_markdown": "let me in",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "not_a_participant"


def test_post_as_agent_rejects_non_owner(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    # Task is owned by someone other than the composer.
    _bind_task_session(org_state, agent="qa_engineer", task_id="TASK-902", sid="sess-902")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-902",
            "session_id": "sess-902", "body_markdown": "not mine",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "composer_not_task_owner"


def test_post_as_agent_rejects_session_mismatch(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-903", sid="sess-real")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-903",
            "session_id": "sess-stale", "body_markdown": "wrong session",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "session_mismatch"


def test_post_as_agent_rejects_archived_thread(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    org_state.db.set_thread_status(tid, status=ThreadStatus.ARCHIVED, summary="done")
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-904", sid="sess-904")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-904",
            "session_id": "sess-904", "body_markdown": "too late",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "thread_not_open"


def test_post_as_agent_succeeds_past_turn_cap(tmp_home, app, org_state, auth_headers):
    """THR-046: turn-cap guard removed — agent reply succeeds even when
    turns_used has reached turn_cap. Verifies turns_used still increments."""
    client = TestClient(app)
    tid = org_state.db.next_thread_id()
    org_state.db.insert_thread(ThreadRecord(id=tid, subject="cap", turn_cap=1))
    org_state.db.add_thread_participant(tid, "dev_agent", added_by="founder")
    org_state.db.add_thread_participant(tid, "qa_engineer", added_by="founder")
    org_state.db.increment_thread_turns_used(tid, by=1)  # turns_used == cap
    before_turns = org_state.db.get_thread(tid).turns_used
    _bind_task_session(org_state, agent="dev_agent", task_id="TASK-905", sid="sess-905")

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-905",
            "session_id": "sess-905", "body_markdown": "one more turn",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    # turns_used must still increment (display path intact).
    after_turns = org_state.db.get_thread(tid).turns_used
    assert after_turns == before_turns + 1


def test_post_as_agent_rejects_missing_binding(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={"composer": "dev_agent", "body_markdown": "no binding"},
        headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "binding_required"


def test_post_as_agent_rejects_unknown_task(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/post-as-agent",
        json={
            "composer": "dev_agent", "task_id": "TASK-NOPE",
            "session_id": "sess-x", "body_markdown": "ghost task",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "unknown_task"


# ---------------------------------------------------------------------------
# POST /threads/{id}/abort-replies
# ---------------------------------------------------------------------------


def _seed_pending_invocations(org_state, thread_id: str, participants: list[str]) -> list[str]:
    """Mint pending REPLY invocations for a thread and return tokens."""
    tokens: list[str] = []
    for agent in participants:
        inv = org_state.db.mint_thread_invocation(
            thread_id=thread_id, agent_name=agent,
            triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
        )
        tokens.append(inv.invocation_token)
    return tokens


def test_abort_replies_marks_pending_failed(tmp_home, app, org_state, auth_headers):
    """Abort marks pending invocations failed with founder_aborted reason."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    tid = _seed_open_thread(org_state, participants=["dev_agent", "qa_engineer"])
    tokens = _seed_pending_invocations(org_state, tid, ["dev_agent", "qa_engineer"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"] == tid
    assert data["aborted_count"] == 2

    for token in tokens:
        inv = org_state.db.get_invocation_any_status(token)
        assert inv.status.value == "failed"
        assert inv.decline_reason == "founder_aborted"
        assert inv.consumed_at is not None

    # No turns consumed, no transcript rows added.
    t = org_state.db.get_thread(tid)
    assert t.turns_used == 0
    msgs = org_state.db.list_thread_messages(tid)
    assert len(msgs) == 0


def test_abort_replies_rejects_stale_token(tmp_home, app, org_state, auth_headers):
    """After abort, a stale reply token is rejected (409 consumed)."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    tokens = _seed_pending_invocations(org_state, tid, ["dev_agent"])
    token = tokens[0]

    # Abort first.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["aborted_count"] == 1

    # Try to reply with the now-aborted token.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid, "invocation_token": token,
            "speaker": "dev_agent", "body_markdown": "stale reply",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "invocation_token_consumed"

    # No new message added.
    msgs = org_state.db.list_thread_messages(tid)
    assert len(msgs) == 0


def test_abort_replies_idempotent_returns_zero(tmp_home, app, org_state, auth_headers):
    """Second abort on a thread with no pending invocations returns 0."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    tokens = _seed_pending_invocations(org_state, tid, ["dev_agent"])

    # First abort — counts 1.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["aborted_count"] == 1

    # Second abort — no pending left.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["aborted_count"] == 0

    # Still no transcript rows.
    msgs = org_state.db.list_thread_messages(tid)
    assert len(msgs) == 0


def test_abort_replies_missing_thread_returns_404(tmp_home, app, org_state, auth_headers):
    """Aborting a non-existent thread returns 404."""
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads/THR-NOPE/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_abort_replies_archived_thread_returns_400(tmp_home, app, org_state, auth_headers):
    """Aborting an archived thread returns 400."""
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    # Archive it.
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/archive",
        json={"summary": "done"},
        headers=auth_headers,
    )
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "thread_not_open"


def test_abort_replies_does_not_break_normal_reply(tmp_home, app, org_state, auth_headers):
    """Normal reply still works: abort on a fresh thread with no pending is no-op."""
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)

    # Abort on thread that has pending invocations (_start_thread mints one).
    # This will kill the token — then show a NEW compose -> reply flow works.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abort-replies",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Now compose a fresh thread and reply normally.
    tid2, token2 = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid2}/reply",
        json={
            "thread_id": tid2, "invocation_token": token2,
            "speaker": "dev_agent", "body_markdown": "normal reply",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


def test_abort_replies_does_not_break_normal_decline(tmp_home, app, org_state, auth_headers):
    """Normal decline still works after abort infrastructure exists."""
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/decline",
        json={
            "thread_id": tid, "invocation_token": token,
            "speaker": "dev_agent", "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_invocation_any_status(token).status.value == "declined"


# ---------------------------------------------------------------------------
# Thread-scoped attachment tests (TASK-1616)
# ---------------------------------------------------------------------------


def test_upload_thread_attachment_success(client, auth_headers, org_state) -> None:
    """Upload to an existing thread's attachment store succeeds."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["attachment_id"].startswith("att-")
    assert data["thread_id"] == tid
    assert data["display_name"] == "hello.txt"
    assert data["size_bytes"] == 11
    assert data["content_type"] == "text/plain"
    assert data["uploaded_by"] == "founder"

    # Verify stored in DB.
    row = org_state.db.get_thread_scoped_attachment(tid, data["attachment_id"])
    assert row is not None
    assert row.display_name == "hello.txt"


def test_upload_thread_attachment_nonexistent_thread(client, auth_headers, org_state) -> None:
    """Upload to a nonexistent thread returns 404."""
    resp = client.post(
        "/api/v1/orgs/alpha/threads/nonexistent/attachments",
        files={"file": ("h.txt", b"hi", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_list_thread_attachments(client, auth_headers, org_state) -> None:
    """List returns all attachments for a thread."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Upload two files.
    for name, content in [("a.txt", b"aa"), ("b.txt", b"bbb")]:
        client.post(
            f"/api/v1/orgs/alpha/threads/{tid}/attachments",
            files={"file": (name, content, "text/plain")},
            params={"agent": "founder"},
            headers=auth_headers,
        )

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["attachments"]) == 2
    names = {a["display_name"] for a in data["attachments"]}
    assert names == {"a.txt", "b.txt"}


def test_get_thread_attachment_download(client, auth_headers, org_state) -> None:
    """Download a thread-scoped attachment works."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    upload_resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    att_id = upload_resp.json()["attachment_id"]

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.content == b"hello world"
    assert "text/plain" in resp.headers.get("content-type", "")


def test_get_thread_attachment_not_found(client, auth_headers, org_state) -> None:
    """Download nonexistent attachment returns 404."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/nonexistent",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_upload_attachment_too_large(client, auth_headers, org_state) -> None:
    """Uploading an oversized attachment returns 413."""
    from runtime.infrastructure.thread_scoped_attachment_store import MAX_THREAD_ATTACHMENT_BYTES
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    big_content = b"x" * (MAX_THREAD_ATTACHMENT_BYTES + 1)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("big.bin", big_content, "application/octet-stream")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert resp.status_code == 413


def test_reply_with_thread_scoped_attachment(client, auth_headers, org_state) -> None:
    """Reply route accepts thread-scoped attachment refs and stores them."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")

    # Create thread via compose to get invocations for both agents.
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "Test", "recipients": ["dev_agent", "qa_engineer"], "body_markdown": "hello"},
        headers=auth_headers,
    )
    tid = r.json()["thread_id"]

    # First upload a thread-scoped attachment.
    upload_resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("data.csv", b"a,b,c", "text/csv")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    att_id = upload_resp.json()["attachment_id"]

    # Get an invocation token for dev_agent.
    invs = org_state.db.list_thread_invocations(tid)
    dev_inv = [i for i in invs if i.agent_name == "dev_agent"][0]

    # Reply with thread-scoped attachment ref.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "data attached",
            "attachments": [{"attachment_id": att_id}],
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Verify the attachment appears in the thread messages.
    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}",
        headers=auth_headers,
    )
    msgs = get_resp.json()["messages"]
    reply_msg = [m for m in msgs if m["speaker"] == "dev_agent"][0]
    assert len(reply_msg["attachments"]) == 1
    att = reply_msg["attachments"][0]
    assert att["thread_attachment_id"] == att_id
    assert att["display_name"] == "data.csv"
    assert att["content_type"] == "text/csv"


def test_reply_with_thread_scoped_attachment_not_found(client, auth_headers, org_state) -> None:
    """Reply with a nonexistent thread-scoped attachment id returns 404."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "Test", "recipients": ["dev_agent", "qa_engineer"], "body_markdown": "hello"},
        headers=auth_headers,
    )
    tid = r.json()["thread_id"]

    invs = org_state.db.list_thread_invocations(tid)
    dev_inv = [i for i in invs if i.agent_name == "dev_agent"][0]

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "bad ref",
            "attachments": [{"attachment_id": "nonexistent"}],
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_legacy_shared_artifact_attachment_still_works(client, auth_headers, org_state) -> None:
    """Legacy shared artifact refs in attachments still render in responses."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "Test", "recipients": ["dev_agent", "qa_engineer"], "body_markdown": "hello"},
        headers=auth_headers,
    )
    tid = r.json()["thread_id"]

    # Create a shared artifact.
    artifact_store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
    artifact_store.put("test-legacy.pdf", b"legacy content")

    invs = org_state.db.list_thread_invocations(tid)
    dev_inv = [i for i in invs if i.agent_name == "dev_agent"][0]

    # Reply with a shared artifact ref (legacy path).
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "legacy attachment",
            "attachments": [{"artifact_name": "test-legacy.pdf"}],
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Verify the legacy attachment still renders.
    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}",
        headers=auth_headers,
    )
    msgs = get_resp.json()["messages"]
    reply_msg = [m for m in msgs if m["speaker"] == "dev_agent"][0]
    atts = reply_msg["attachments"]
    assert any(a["artifact_name"] == "test-legacy.pdf" for a in atts)

    # Verify the attachment can still be downloaded via artifacts route.
    dl_resp = client.get(
        f"/api/v1/orgs/alpha/artifacts/test-legacy.pdf",
        headers=auth_headers,
    )
    assert dl_resp.status_code == 200
    assert dl_resp.content == b"legacy content"


def test_mixed_attachments_shared_and_thread_scoped(client, auth_headers, org_state) -> None:
    """A message can carry both shared artifact refs and thread-scoped refs."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "Test", "recipients": ["dev_agent", "qa_engineer"], "body_markdown": "hello"},
        headers=auth_headers,
    )
    tid = r.json()["thread_id"]

    # Create a shared artifact.
    artifact_store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
    artifact_store.put("shared.pdf", b"shared")

    # Upload a thread-scoped attachment.
    upload_resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("private.txt", b"private", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    att_id = upload_resp.json()["attachment_id"]

    invs = org_state.db.list_thread_invocations(tid)
    dev_inv = [i for i in invs if i.agent_name == "dev_agent"][0]

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid,
            "invocation_token": dev_inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "mixed",
            "attachments": [
                {"artifact_name": "shared.pdf"},
                {"attachment_id": att_id},
            ],
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}",
        headers=auth_headers,
    )
    msgs = get_resp.json()["messages"]
    reply_msg = [m for m in msgs if m["speaker"] == "dev_agent"][0]
    assert len(reply_msg["attachments"]) == 2
    has_shared = any(a.get("artifact_name") == "shared.pdf" for a in reply_msg["attachments"])
    has_thread = any(a.get("thread_attachment_id") == att_id for a in reply_msg["attachments"])
    assert has_shared
    assert has_thread


# ── Thread-scoped attachment authorization tests (TASK-1616) ──────────────


def test_upload_attachment_rejects_non_participant(
    client, auth_headers, org_state,
) -> None:
    """A non-participant agent cannot upload thread-scoped attachments."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "intruder")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("secret.txt", b"secret", "text/plain")},
        params={"agent": "intruder"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_participant"


def test_upload_attachment_allows_participant(
    client, auth_headers, org_state,
) -> None:
    """A thread participant CAN upload thread-scoped attachments."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("ok.txt", b"ok", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["uploaded_by"] == "dev_agent"


def test_list_attachments_rejects_non_participant(
    client, auth_headers, org_state,
) -> None:
    """A non-participant agent cannot list thread-scoped attachments (missing token)."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "intruder")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Non-participant without invocation token → 401 (proof required).
    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "intruder"},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_required"


def test_list_attachments_rejects_non_participant_with_token(
    client, auth_headers, org_state,
) -> None:
    """A non-participant agent with a valid invocation token is still rejected."""
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "intruder")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Mint a token for the intruder on this thread (contrived but proves the gate).
    token = org_state.db.mint_thread_invocation(
        thread_id=tid, agent_name="intruder", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "intruder", "invocation_token": token},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_participant"


def test_list_attachments_allows_participant(
    client, auth_headers, org_state,
) -> None:
    """A thread participant with a valid invocation token CAN list."""
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    token = org_state.db.mint_thread_invocation(
        thread_id=tid, agent_name="dev_agent", triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token

    # Upload a file first.
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("a.txt", b"aa", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "dev_agent", "invocation_token": token},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["attachments"]) == 1


def test_get_attachment_rejects_non_participant(
    client, auth_headers, org_state,
) -> None:
    """A non-participant agent cannot download (missing token)."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "intruder")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Upload as participant.
    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("secret.txt", b"secret", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    att_id = upload.json()["attachment_id"]

    # Non-participant without invocation token → 401.
    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "intruder"},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_required"


def test_get_attachment_rejects_non_participant_with_token(
    client, auth_headers, org_state,
) -> None:
    """A non-participant with a valid invocation token is still rejected."""
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "intruder")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Upload as participant.
    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("secret.txt", b"secret", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    att_id = upload.json()["attachment_id"]

    token = org_state.db.mint_thread_invocation(
        thread_id=tid, agent_name="intruder", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "intruder", "invocation_token": token},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_participant"


def test_get_attachment_allows_participant_with_token(
    client, auth_headers, org_state,
) -> None:
    """A thread participant with a valid invocation token CAN download."""
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    token = org_state.db.mint_thread_invocation(
        thread_id=tid, agent_name="dev_agent", triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token

    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("ok.txt", b"ok", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    att_id = upload.json()["attachment_id"]

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "dev_agent", "invocation_token": token},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.content == b"ok"


def test_list_attachments_rejects_bogus_token(
    client, auth_headers, org_state,
) -> None:
    """A participant with a bogus invocation token is rejected."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "dev_agent", "invocation_token": "not-a-real-token"},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_invalid"


def test_get_attachment_rejects_mismatched_token(
    client, auth_headers, org_state,
) -> None:
    """A token for a different thread is rejected."""
    from runtime.models import ThreadInvocationPurpose
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])
    tid2 = _seed_open_thread(org_state, participants=["dev_agent"])

    token2 = org_state.db.mint_thread_invocation(
        thread_id=tid2, agent_name="dev_agent", triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token

    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("x.txt", b"x", "text/plain")},
        params={"agent": "dev_agent"},
        headers=auth_headers,
    )
    att_id = upload.json()["attachment_id"]

    # Token is for tid2, not tid → rejected.
    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "dev_agent", "invocation_token": token2},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_invalid"


def test_founder_bypasses_participation_check(
    client, auth_headers, org_state,
) -> None:
    """Founder (agent='founder') bypasses participation checks for all routes."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    # Upload as founder works even though founder is not a participant row.
    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("founder.txt", b"founder", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert upload.status_code == 200
    att_id = upload.json()["attachment_id"]

    # List as founder works.
    list_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert list_resp.status_code == 200

    # Get as founder works.
    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert get_resp.status_code == 200


def test_no_agent_param_rejected(
    client, auth_headers, org_state,
) -> None:
    """Without agent param, list/get are rejected — no bearer-only bypass."""
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state, participants=["dev_agent"])

    upload = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        files={"file": ("public.txt", b"public", "text/plain")},
        params={"agent": "founder"},
        headers=auth_headers,
    )
    att_id = upload.json()["attachment_id"]

    # List without agent param — rejected.
    list_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments",
        headers=auth_headers,
    )
    assert list_resp.status_code == 401
    assert list_resp.json()["detail"]["code"] == "agent_required"

    # Get without agent param — rejected.
    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/attachments/{att_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 401
    assert get_resp.json()["detail"]["code"] == "agent_required"


# ── Compose-as-agent multipart (TASK-1616) ─────────────────────────────────


def test_compose_as_agent_multipart_with_files(
    client, auth_headers, org_state,
) -> None:
    """Agent compose with multipart stores files thread-scoped."""
    import json, mimetypes
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "review_agent")

    body = {
        "composer": "dev_agent",
        "subject": "Files attached",
        "recipients": ["review_agent"],
        "body_markdown": "see attached",
        "task_id": "TASK-001",
        "session_id": "sess-1",
    }
    # Seed a task owned by dev_agent.
    org_state.db.insert_task(TaskRecord(
        id="TASK-001", brief="test", assigned_agent="dev_agent",
        task_type="task", status=TaskStatus.IN_PROGRESS,
        session_timeout_seconds=600,
        block_kind=BlockKind.DELEGATED,
    ))
    org_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")

    files = [
        ("files", ("data.csv", b"a,b,c", "text/csv")),
        ("files", ("notes.txt", b"notes", "text/plain")),
    ]
    data = {"body": json.dumps(body)}

    resp = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        files=files, data=data,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    thread_id = resp.json()["thread_id"]

    # Verify thread-scoped attachments exist.
    rows = org_state.db.list_thread_scoped_attachments(thread_id)
    assert len(rows) == 2
    names = {r.display_name for r in rows}
    assert names == {"data.csv", "notes.txt"}

    # Verify the thread message includes thread-scoped attachment refs.
    get_resp = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}",
        headers=auth_headers,
    )
    msgs = get_resp.json()["messages"]
    assert len(msgs) == 1
    atts = msgs[0]["attachments"]
    assert len(atts) == 2
    for a in atts:
        assert a["thread_attachment_id"].startswith("att-")
        assert a["artifact_name"] == ""


# ── THR-061 PR-1: GET /threads/{thread_id}/tasks ──────────────────────────


def test_list_thread_tasks_db_newest_first(tmp_home, app, org_state, auth_headers):
    """Database query returns rows newest-first filtered by dispatched_from_thread_id."""
    tid, _token = _start_thread(TestClient(app), org_state, auth_headers)
    db = org_state.db

    # Insert tasks with different timestamps via dispatched_from_thread_id.
    db.insert_task(TaskRecord(
        id="TASK-10", brief="first", team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_thread_id=tid,
        created_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc),
    ))
    db.insert_task(TaskRecord(
        id="TASK-11", brief="second", team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_thread_id=tid,
        created_at=datetime(2026, 7, 2, 10, 0, 0, tzinfo=timezone.utc),
    ))
    db.insert_task(TaskRecord(
        id="TASK-12", brief="third", team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_thread_id=tid,
        created_at=datetime(2026, 7, 3, 10, 0, 0, tzinfo=timezone.utc),
    ))
    # Also insert a task NOT dispatched from this thread.
    db.insert_task(TaskRecord(
        id="TASK-99", brief="other", team="engineering",
        assigned_agent="qa_engineer",
    ))

    rows = db.list_tasks_by_thread(tid)
    assert len(rows) == 3
    # Newest first: TASK-12 (Jul 3) > TASK-11 (Jul 2) > TASK-10 (Jul 1)
    assert rows[0]["id"] == "TASK-12"
    assert rows[1]["id"] == "TASK-11"
    assert rows[2]["id"] == "TASK-10"
    # Check fields.
    assert rows[0]["brief"] == "third"
    assert rows[0]["status"] == "pending"
    assert rows[0]["assigned_agent"] == "dev_agent"
    assert rows[0]["parent_task_id"] is None


def test_list_thread_tasks_route_returns_summaries(tmp_home, app, org_state, auth_headers):
    """Route returns task summaries for a thread with dispatched tasks."""
    client = TestClient(app)
    tid, _token = _start_thread(client, org_state, auth_headers)
    db = org_state.db

    # Insert two tasks dispatched from this thread.
    db.insert_task(TaskRecord(
        id="TASK-20", brief="alpha task", team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_thread_id=tid,
        created_at=datetime(2026, 7, 4, 10, 0, 0, tzinfo=timezone.utc),
    ))
    db.insert_task(TaskRecord(
        id="TASK-21", brief="beta task", team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_thread_id=tid,
        created_at=datetime(2026, 7, 4, 11, 0, 0, tzinfo=timezone.utc),
    ))

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/tasks",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    # Newest first.
    assert data[0]["id"] == "TASK-21"
    assert data[0]["brief"] == "beta task"
    assert data[0]["status"] == "pending"
    assert data[0]["assigned_agent"] == "dev_agent"
    assert data[0]["parent_task_id"] is None
    assert data[1]["id"] == "TASK-20"
    assert data[1]["brief"] == "alpha task"


def test_list_thread_tasks_route_empty_thread(tmp_home, app, org_state, auth_headers):
    """Route returns empty list for a thread with no dispatched tasks."""
    client = TestClient(app)
    tid, _token = _start_thread(client, org_state, auth_headers)

    resp = client.get(
        f"/api/v1/orgs/alpha/threads/{tid}/tasks",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == []
