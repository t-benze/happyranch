"""THR-209 thread rename + pin route tests (founder-only mutations).

Covers: rename success + validation boundaries (whitespace, 1/120/121 chars,
duplicates, plain-text), last-successful-save semantics, pin/unpin, idempotent
no-ops, unauthorized/non-founder rejection, audit rows, and the non-effect
invariants (no thread message, no notification, no participant/unread/
lifecycle/timestamp change, no pin state change on delete-less rename).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from runtime.models import ThreadMessageKind, ThreadRecord, ThreadStatus

from tests.daemon.test_threads_routes import _seed_agent


def _seed_open_thread(org_state, *, subject: str = "Files") -> str:
    thread_id = org_state.db.next_thread_id()
    org_state.db.insert_thread(ThreadRecord(id=thread_id, subject=subject))
    return thread_id


def _audit_actions(org_state, thread_id: str) -> list[str]:
    return [e["action"] for e in org_state.db.get_audit_logs(thread_id)]


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


def test_rename_updates_subject_and_audits(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, subject="Old title")
    before = org_state.db.get_thread(tid).started_at

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "  New title  "},  # trims surrounding whitespace
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"thread_id": tid, "subject": "New title"}
    assert org_state.db.get_thread(tid).subject == "New title"
    assert "thread_renamed" in _audit_actions(org_state, tid)
    entry = [e for e in org_state.db.get_audit_logs(tid) if e["action"] == "thread_renamed"][-1]
    assert entry["payload"]["old_subject"] == "Old title"
    assert entry["payload"]["new_subject"] == "New title"
    # Non-effect: started_at (activity timestamp) untouched.
    assert org_state.db.get_thread(tid).started_at == before


def test_rename_rejects_empty_and_whitespace_only(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    for value in ["", "   ", "\t\n "]:
        resp = client.post(
            f"/api/v1/orgs/alpha/threads/{tid}/rename",
            json={"subject": value},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (value, resp.text)
        assert resp.json()["detail"]["code"] == "empty_subject"


def test_rename_length_boundary_120_ok_121_rejected(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    ok = "x" * 120
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": ok},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["subject"] == ok

    too_long = "x" * 121
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": too_long},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "subject_too_long"
    # Failed save must not clobber the stored title.
    assert org_state.db.get_thread(tid).subject == ok


def test_rename_duplicates_allowed(tmp_home, app, org_state, auth_headers):
    """Duplicate titles are legal — the immutable thread ID stays canonical."""
    client = TestClient(app)
    a = _seed_open_thread(org_state, subject="Shared")
    b = _seed_open_thread(org_state, subject="Other")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{a}/rename",
        json={"subject": "Other"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(a).subject == "Other"
    assert org_state.db.get_thread(b).subject == "Other"


def test_rename_identical_value_is_idempotent_success(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, subject="Same")
    before = len(org_state.db.get_audit_logs(tid))
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "Same"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["idempotent"] is True
    # No duplicate audit row for an identical save.
    assert len(org_state.db.get_audit_logs(tid)) == before


def test_rename_last_successful_save_wins(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state, subject="A")
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "B"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "C"},
        headers=auth_headers,
    )
    assert org_state.db.get_thread(tid).subject == "C"


def test_rename_404_on_missing_thread(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads/THR-NOPE/rename",
        json={"subject": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_rename_works_on_archived_thread(tmp_home, app, org_state, auth_headers):
    """The founder can rename any thread, including archived/closed ones."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    org_state.db.set_thread_status(tid, status=ThreadStatus.ARCHIVED, summary="done")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "Renamed after archive"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(tid).subject == "Renamed after archive"


def test_rename_creates_no_message_and_keeps_participants(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    tid = _seed_open_thread(org_state)
    org_state.db.add_thread_participant(tid, "dev_agent", added_by="founder")
    msgs_before = org_state.db.list_thread_messages(tid)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    msgs_after = org_state.db.list_thread_messages(tid)
    assert len(msgs_after) == len(msgs_before)
    assert all(m.kind is ThreadMessageKind.MESSAGE for m in msgs_after)
    participants = [p.agent_name for p in org_state.db.list_thread_participants(tid)]
    assert participants == ["dev_agent"]


# ---------------------------------------------------------------------------
# Pin / unpin
# ---------------------------------------------------------------------------


def test_pin_sets_state_and_audits(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"thread_id": tid, "pinned": True}
    assert org_state.db.get_thread(tid).pinned_at is not None
    assert "thread_pinned" in _audit_actions(org_state, tid)

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(tid).pinned_at is None
    assert "thread_unpinned" in _audit_actions(org_state, tid)


def test_pin_idempotent_noop(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    before = len(org_state.db.get_audit_logs(tid))
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["idempotent"] is True
    assert len(org_state.db.get_audit_logs(tid)) == before


def test_pin_rejects_non_bool(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": "yes"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_pin_404_on_missing_thread(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads/THR-NOPE/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_pin_does_not_create_message_or_change_timestamps(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    started = org_state.db.get_thread(tid).started_at
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert org_state.db.list_thread_messages(tid) == []
    got = org_state.db.get_thread(tid)
    assert got.started_at == started
    assert got.subject == "Files"
    # Pin state survives a reload (durable persistence).
    reloaded = org_state.db.get_thread(tid)
    assert reloaded.pinned_at is not None


def test_pin_on_archived_thread_allowed(tmp_home, app, org_state, auth_headers):
    """Archived/closed pins persist and remain visible only where eligible."""
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    org_state.db.set_thread_status(tid, status=ThreadStatus.ARCHIVED, summary="s")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(tid).pinned_at is not None


def test_list_wire_exposes_pinned_fields(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/orgs/alpha/threads", headers=auth_headers)
    assert resp.status_code == 200
    row = [t for t in resp.json()["threads"] if t["thread_id"] == tid][0]
    assert row["pinned"] is True
    assert row["pinned_at"] is not None
    # Pinned thread ranks first in the list payload.
    assert resp.json()["threads"][0]["thread_id"] == tid


def test_detail_wire_exposes_pinned_fields(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    resp = client.get(f"/api/v1/orgs/alpha/threads/{tid}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pinned"] is False
    assert resp.json()["pinned_at"] is None


# ---------------------------------------------------------------------------
# Auth / permission
# ---------------------------------------------------------------------------


def test_rename_requires_master_bearer(tmp_home, app, org_state):
    """Routes sit behind require_token(); an agent invocation token (or no
    token) is not the founder's master bearer and must be rejected."""
    from runtime.daemon import paths as paths_mod

    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "x"},
    )
    assert resp.status_code in (401, 403), resp.status_code
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "x"},
        headers={"Authorization": "Bearer invocation-token-not-master"},
    )
    assert resp.status_code in (401, 403), resp.status_code
    assert org_state.db.get_thread(tid).subject == "Files"


def test_pin_requires_master_bearer(tmp_home, app, org_state):
    client = TestClient(app)
    tid = _seed_open_thread(org_state)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers={"Authorization": "Bearer invocation-token-not-master"},
    )
    assert resp.status_code in (401, 403), resp.status_code
    assert org_state.db.get_thread(tid).pinned_at is None
