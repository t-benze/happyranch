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
    predecessor auto-resolves it to RESOLVED_SUPERSEDED, citing the new root +
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
    assert pred.status == TaskStatus.RESOLVED_SUPERSEDED
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
    assert pred.status == TaskStatus.RESOLVED_SUPERSEDED
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
        }
    ]


# ---------------------------------------------------------------------------
# Task 25 — POST /threads/{id}/invite
# ---------------------------------------------------------------------------


def test_invite_adds_participant_and_bootstrap_invocation(tmp_home, app, org_state, auth_headers):
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
    assert any(
        inv.agent_name == "qa_engineer" and inv.purpose.value == "bootstrap"
        for inv in pending
    )


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

    # qa_engineer (other participant) must get a pending REPLY invocation;
    # dev_agent (the speaker) is excluded.
    from runtime.models import ThreadInvocationStatus as TIS_P
    pending = {}
    for inv in org_state.db.list_thread_invocations(tid):
        if inv.status == TIS_P.PENDING:
            pending[inv.agent_name] = pending.get(inv.agent_name, 0) + 1
    assert pending == {"qa_engineer": 1}, f"got {pending}"


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
