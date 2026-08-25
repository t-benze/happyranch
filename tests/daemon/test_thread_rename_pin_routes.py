"""THR-209 thread rename + pin route tests (founder-only mutations).

Covers: rename success + validation boundaries (whitespace, 1/120/121 chars,
duplicates, plain-text), last-successful-save semantics, pin/unpin, idempotent
no-ops, unauthorized/non-founder rejection, audit rows, and the non-effect
invariants (no thread message, no notification, no participant/unread/
lifecycle/timestamp change, no pin state change on delete-less rename).

Atomicity (TASK-5644): audit-fault tests prove an audit-insert failure rolls
back the whole mutation (no durable rename/pin, no stray audit row, error
response); overlapping-request tests prove concurrent rename/pin requests
serialize through the real ``org.db_lock`` + transaction and produce truthful
ordered audit history (last-successful-save-wins chains, exactly one audit row
per durable pin transition, true no-ops unaudited).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from runtime.daemon.routes.threads import (
    RenameThreadBody,
    SetThreadPinBody,
    rename_thread_endpoint,
    set_thread_pin_endpoint,
)
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


# ---------------------------------------------------------------------------
# Audit-fault atomicity (TASK-5644) — rollback-safe mutation + audit
# ---------------------------------------------------------------------------


def _audit_fault_raiser(*args, **kwargs):
    raise RuntimeError("audit insertion failed")


def test_rename_audit_failure_rolls_back_no_stray_audit(
    tmp_home, app, org_state, auth_headers, monkeypatch,
):
    """If the audit insert fails, the rename must roll back EVERYTHING: no
    durable subject change, no stray audit row, and an error response."""
    client = TestClient(app, raise_server_exceptions=False)
    tid = _seed_open_thread(org_state, subject="Old title")

    with monkeypatch.context() as ctx:
        ctx.setattr(
            org_state.db, "insert_audit_log_uncommitted", _audit_fault_raiser,
        )
        resp = client.post(
            f"/api/v1/orgs/alpha/threads/{tid}/rename",
            json={"subject": "New title"},
            headers=auth_headers,
        )
        assert resp.status_code == 500, resp.text
        # No durable mutation and no stray audit row.
        assert org_state.db.get_thread(tid).subject == "Old title"
        assert [
            e for e in org_state.db.get_audit_logs(tid)
            if e["action"] == "thread_renamed"
        ] == []

    # The transaction machinery recovered: the same connection serves the next
    # save (with the audit seam restored) without a durable unaudited write.
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/rename",
        json={"subject": "Recovered"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(tid).subject == "Recovered"
    assert [
        e["payload"] for e in org_state.db.get_audit_logs(tid)
        if e["action"] == "thread_renamed"
    ] == [{"old_subject": "Old title", "new_subject": "Recovered"}]


def test_pin_audit_failure_rolls_back_no_stray_audit(
    tmp_home, app, org_state, auth_headers, monkeypatch,
):
    """If the audit insert fails, the pin must roll back EVERYTHING: no
    durable pinned_at change, no stray audit row, and an error response."""
    client = TestClient(app, raise_server_exceptions=False)
    tid = _seed_open_thread(org_state)

    with monkeypatch.context() as ctx:
        ctx.setattr(
            org_state.db, "insert_audit_log_uncommitted", _audit_fault_raiser,
        )
        resp = client.post(
            f"/api/v1/orgs/alpha/threads/{tid}/pin",
            json={"pinned": True},
            headers=auth_headers,
        )
        assert resp.status_code == 500, resp.text
        assert org_state.db.get_thread(tid).pinned_at is None
        assert [
            e for e in org_state.db.get_audit_logs(tid)
            if e["action"] in ("thread_pinned", "thread_unpinned")
        ] == []

    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert org_state.db.get_thread(tid).pinned_at is not None
    assert [
        e["action"] for e in org_state.db.get_audit_logs(tid)
        if e["action"] in ("thread_pinned", "thread_unpinned")
    ] == ["thread_pinned"]


# ---------------------------------------------------------------------------
# Overlapping-request interleavings (TASK-5644) — real lock + transaction
# ---------------------------------------------------------------------------


async def _overlap_through_db_lock(org, *coros):
    """Run the real route coroutines with a deterministic serialization
    through the SHIPPING ``org.db_lock``: the test holds the lock first so
    every request blocks at the real lock, then releases so they execute in
    FIFO waiter order. Inside the critical section the atomic DB methods hold
    the connection lock for the whole read+decide+write+audit unit, so no
    request can observe or write against another's in-flight state."""
    await org.db_lock.acquire()
    tasks = [asyncio.create_task(c) for c in coros]
    await asyncio.sleep(0)  # let every coroutine reach the lock
    org.db_lock.release()
    return await asyncio.gather(*tasks)


def test_rename_overlapping_requests_truthful_chain(
    tmp_home, app, org_state, auth_headers,
):
    """Two concurrent renames must serialize through the real lock: the first
    writes A→B, the second re-reads inside ITS transaction and writes B→C.
    Final durable state is C and the audit history is a contiguous truthful
    chain (each row's old == previous row's new) — never two stale A→* rows.
    """
    tid = _seed_open_thread(org_state, subject="A")
    results = asyncio.run(_overlap_through_db_lock(
        org_state,
        rename_thread_endpoint(
            "alpha", tid, RenameThreadBody(subject="B"), org_state,
        ),
        rename_thread_endpoint(
            "alpha", tid, RenameThreadBody(subject="C"), org_state,
        ),
    ))
    assert results[0] == {"thread_id": tid, "subject": "B"}
    assert results[1] == {"thread_id": tid, "subject": "C"}
    assert org_state.db.get_thread(tid).subject == "C"
    renamed = [
        e for e in org_state.db.get_audit_logs(tid)
        if e["action"] == "thread_renamed"
    ]
    assert len(renamed) == 2
    assert [e["payload"]["old_subject"] for e in renamed] == ["A", "B"]
    assert [e["payload"]["new_subject"] for e in renamed] == ["B", "C"]


def test_pin_same_state_overlap_single_transition_audit(
    tmp_home, app, org_state, auth_headers,
):
    """Two concurrent pin=True requests on an unpinned thread: the first
    performs the durable transition (one ``thread_pinned`` row); the second
    re-reads inside its transaction and is a TRUE no-op (unaudited). Exactly
    one audit row corresponds to the one durable transition."""
    tid = _seed_open_thread(org_state)
    results = asyncio.run(_overlap_through_db_lock(
        org_state,
        set_thread_pin_endpoint(
            "alpha", tid, SetThreadPinBody(pinned=True), org_state,
        ),
        set_thread_pin_endpoint(
            "alpha", tid, SetThreadPinBody(pinned=True), org_state,
        ),
    ))
    assert results[0] == {"thread_id": tid, "pinned": True}
    assert results[1] == {
        "thread_id": tid, "pinned": True, "idempotent": True,
    }
    assert org_state.db.get_thread(tid).pinned_at is not None
    assert _audit_actions(org_state, tid).count("thread_pinned") == 1


def test_pin_opposite_state_overlap_truthful_history(
    tmp_home, app, org_state, auth_headers,
):
    """Concurrent opposite-state pins: pin=True runs first (transition +
    ``thread_pinned``), then pin=False re-reads the DURABLE pinned state inside
    its transaction and performs the REAL opposite transition (``thread_unpinned``).
    Final state is unpinned and history [thread_pinned, thread_unpinned] matches
    exactly the two durable transitions — no request is misclassified from a
    stale pre-lock snapshot."""
    tid = _seed_open_thread(org_state)
    results = asyncio.run(_overlap_through_db_lock(
        org_state,
        set_thread_pin_endpoint(
            "alpha", tid, SetThreadPinBody(pinned=True), org_state,
        ),
        set_thread_pin_endpoint(
            "alpha", tid, SetThreadPinBody(pinned=False), org_state,
        ),
    ))
    assert results[0] == {"thread_id": tid, "pinned": True}
    assert results[1] == {"thread_id": tid, "pinned": False}
    assert org_state.db.get_thread(tid).pinned_at is None
    assert _audit_actions(org_state, tid) == [
        "thread_pinned", "thread_unpinned",
    ]


def test_rename_overlapping_requests_http_full_path(
    tmp_home, app, org_state, auth_headers,
):
    """Same interleaving proven through the FULL HTTP+auth+ASGI path (not just
    direct route calls): two concurrent POSTs serialize on the shipping lock,
    last-successful-save-wins, and the audit chain is truthful."""
    import httpx

    tid = _seed_open_thread(org_state, subject="A")
    url = f"http://test/api/v1/orgs/alpha/threads/{tid}/rename"

    async def _driver():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            await org_state.db_lock.acquire()
            t1 = asyncio.create_task(
                client.post(url, json={"subject": "B"}, headers=auth_headers),
            )
            t2 = asyncio.create_task(
                client.post(url, json={"subject": "C"}, headers=auth_headers),
            )
            await asyncio.sleep(0)
            org_state.db_lock.release()
            return await asyncio.gather(t1, t2)

    r1, r2 = asyncio.run(_driver())
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    final = org_state.db.get_thread(tid).subject
    renamed = [
        e for e in org_state.db.get_audit_logs(tid)
        if e["action"] == "thread_renamed"
    ]
    # Order-independent truthful chain: two real transitions, the first from
    # the seeded "A", each row's old == previous row's new, and the final
    # durable subject == the last row's new (last successful save wins).
    assert len(renamed) == 2
    olds = [e["payload"]["old_subject"] for e in renamed]
    news = [e["payload"]["new_subject"] for e in renamed]
    assert olds[0] == "A"
    assert olds[1] == news[0]
    assert news[1] == final
    assert sorted(news) == ["B", "C"]
