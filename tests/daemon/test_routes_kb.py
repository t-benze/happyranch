from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.infrastructure.kb_store import KBEntry, KBStore


def _seed_kb(org_root: Path, agent: str = "dev_agent") -> KBStore:
    store = KBStore(org_root / "kb")
    store.write_entry(
        KBEntry(
            slug="alipay-refund-endpoint",
            title="Alipay v3 refund endpoint quirks",
            type="reference",
            topic="payment",
            tags=["alipay", "refund"],
            body="# Alipay v3 refund endpoint quirks\n\nDetails.\n",
        ),
        agent=agent,
    )
    store.write_entry(
        KBEntry(
            slug="hk-visa-90day",
            title="Hong Kong tourist visa",
            type="reference",
            topic="visa",
            tags=["hong-kong"],
            body="# Hong Kong tourist visa\n\nRules.\n",
        ),
        agent=agent,
    )
    return store


def test_kb_list_returns_all_entries(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb", headers=auth_headers)
    assert r.status_code == 200
    slugs = [e["slug"] for e in r.json()["entries"]]
    assert set(slugs) == {"alipay-refund-endpoint", "hk-visa-90day"}


def test_kb_list_filter_by_topic(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb?topic=visa", headers=auth_headers)
    assert r.status_code == 200
    assert [e["slug"] for e in r.json()["entries"]] == ["hk-visa-90day"]


def test_kb_list_filter_by_type(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb?type=reference", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 2


def test_kb_get_returns_entry_body(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "alipay-refund-endpoint"
    assert body["title"].startswith("Alipay")
    assert "Details." in body["body"]
    assert body["authored_by"] == "dev_agent"


def test_kb_get_returns_404_on_missing(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb/ghost", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_kb_search_ranks_title_hits(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb/search?q=Alipay", headers=auth_headers)
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits[0]["slug"] == "alipay-refund-endpoint"


def test_kb_routes_reject_when_idle(tmp_home, app_idle, auth_headers):
    client = TestClient(app_idle)
    r = client.get("/api/v1/orgs/alpha/kb", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def _add_body(slug: str = "alipay-refund-endpoint", **overrides) -> dict:
    body = {
        "agent": "dev_agent",
        "slug": slug,
        "title": "Alipay v3 refund endpoint quirks",
        "type": "reference",
        "topic": "payment",
        "tags": ["alipay", "refund"],
        "body": "# Alipay v3 refund endpoint quirks\n\nDetails.\n",
        "force_new_sibling": False,
    }
    body.update(overrides)
    return body


def test_kb_add_writes_entry(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r = client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "alipay-refund-endpoint"
    assert (org_state.root / "kb" / "alipay-refund-endpoint.md").exists()
    assert (org_state.root / "kb" / "_index.md").exists()


def test_kb_add_rejects_invalid_slug(tmp_home, app, auth_headers):
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb", json=_add_body(slug="Bad Slug"), headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_slug"


def test_kb_add_rejects_invalid_type(tmp_home, app, auth_headers):
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb", json=_add_body(type="guide"), headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_type"


def test_kb_add_rejects_oversized_body(tmp_home, app, auth_headers):
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(body="x" * (32 * 1024 + 1)),
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "entry_too_large"


def test_kb_add_rejects_slug_exists(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r1 = client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "slug_exists"


def test_kb_add_rejects_near_duplicate_without_force(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r1 = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(slug="alipay-v3-refund", title="Alipay v3 refund endpoint quirks"),
        headers=auth_headers,
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(slug="alipay-v3-refunds", title="Alipay v3 refund endpoint gotchas"),
        headers=auth_headers,
    )
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["code"] == "near_duplicate"
    assert any(c["slug"] == "alipay-v3-refund" for c in detail["candidates"])


def test_kb_add_allows_near_duplicate_with_force_flag(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(slug="alipay-v3-refund", title="Alipay v3 refund endpoint quirks"),
        headers=auth_headers,
    )
    r2 = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(
            slug="alipay-v3-refunds",
            title="Alipay v3 refund endpoint gotchas",
            force_new_sibling=True,
        ),
        headers=auth_headers,
    )
    assert r2.status_code == 200


def test_kb_update_preserves_authored_by(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    r = client.post(
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint",
        json={
            "agent": "qa_engineer",
            "slug": "alipay-refund-endpoint",
            "title": "Alipay v3 refund endpoint — updated",
            "type": "reference",
            "topic": "payment",
            "tags": ["alipay", "refund"],
            "body": "# updated\n",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    got = client.get(
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=auth_headers,
    ).json()
    assert got["authored_by"] == "dev_agent"
    assert got["updated_by"] == "qa_engineer"


def test_kb_update_404_on_missing(tmp_home, app, auth_headers):
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/ghost",
        json={
            "agent": "dev_agent",
            "slug": "ghost",
            "title": "t",
            "type": "reference",
            "topic": "x",
            "body": "# x\n",
        },
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_kb_delete_blocks_non_eh(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    r = client.request(
        "DELETE",
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint",
        params={"agent": "dev_agent", "confirm": True},
        headers=auth_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "delete_forbidden"


def test_kb_delete_requires_confirm(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    r = client.request(
        "DELETE",
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint",
        params={"agent": "engineering_head"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirm_required"


def test_kb_delete_by_eh_succeeds(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    r = client.request(
        "DELETE",
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint",
        params={"agent": "engineering_head", "confirm": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not (org_state.root / "kb" / "alipay-refund-endpoint.md").exists()


def test_kb_delete_by_founder_flag_succeeds(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    r = client.request(
        "DELETE",
        "/api/v1/orgs/alpha/kb/alipay-refund-endpoint",
        params={"agent": "dev_agent", "confirm": True, "as_founder": True},
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_kb_reindex_rebuilds_index(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    client.post("/api/v1/orgs/alpha/kb", json=_add_body(), headers=auth_headers)
    index_path = org_state.root / "kb" / "_index.md"
    index_path.unlink()
    r = client.post("/api/v1/orgs/alpha/kb/reindex", headers=auth_headers)
    assert r.status_code == 200
    assert index_path.exists()


def _seed_escalated_task(
    org_state, task_id: str = "TASK-037", brief: str = "Large refund for custom itinerary",
    reason: str = "Amount exceeds CX cap",
) -> None:
    from src.models import BlockKind, TaskRecord, TaskStatus
    org_state.db.insert_task(TaskRecord(
        id=task_id, brief=brief,
    ))
    org_state.db.update_task(
        task_id, status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
    )
    org_state.db.insert_audit_log(
        task_id=task_id, agent="cx_manager", action="escalation",
        payload={"reason": reason},
    )


def test_kb_precedent_writes_entry_from_audit_row(tmp_home, app, org_state, auth_headers):
    _seed_escalated_task(org_state)
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={
            "task_id": "TASK-037",
            "decision": "approve",
            "rationale": "Vendor error per partner-log, <$250 risk",
            "as_founder": True,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    assert slug.startswith("precedent-task-037")
    got = client.get(f"/api/v1/orgs/alpha/kb/{slug}", headers=auth_headers).json()
    assert got["type"] == "precedent"
    assert got["source_task"] == "TASK-037"
    assert "Amount exceeds CX cap" in got["body"]
    assert "Vendor error" in got["body"]


def test_kb_precedent_does_not_transition_task_status(tmp_home, app, org_state, auth_headers):
    from src.models import TaskStatus
    _seed_escalated_task(org_state, task_id="TASK-038")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={"task_id": "TASK-038", "decision": "approve", "rationale": "r", "as_founder": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    got = org_state.db.get_task("TASK-038")
    assert got.status == TaskStatus.BLOCKED
    from src.models import BlockKind
    assert got.block_kind == BlockKind.ESCALATED


def test_kb_precedent_post_hoc_on_resolved_task(tmp_home, app, org_state, auth_headers):
    """Founder can write a precedent for an already-resolved task."""
    from src.models import TaskRecord, TaskStatus
    org_state.db.insert_task(TaskRecord(
        id="TASK-039", brief="Partner change",
        status=TaskStatus.COMPLETED,
    ))
    org_state.db.insert_audit_log(
        task_id="TASK-039", agent="ops_manager", action="escalation",
        payload={"reason": "Partner contract change outside authority"},
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={"task_id": "TASK-039", "decision": "approve", "rationale": "Auth granted.", "as_founder": True},
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_kb_precedent_rejects_task_without_escalation(tmp_home, app, org_state, auth_headers):
    from src.models import TaskRecord, TaskStatus
    org_state.db.insert_task(TaskRecord(
        id="TASK-040", brief="x", status=TaskStatus.COMPLETED,
    ))
    # No escalation audit row
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={"task_id": "TASK-040", "decision": "approve", "rationale": "r", "as_founder": True},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "no_escalation_record"


def test_kb_precedent_honors_slug_override(tmp_home, app, org_state, auth_headers):
    _seed_escalated_task(org_state, task_id="TASK-041")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={
            "task_id": "TASK-041",
            "decision": "approve",
            "rationale": "r",
            "slug": "precedent-large-refund-policy",
            "as_founder": True,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["slug"] == "precedent-large-refund-policy"


def test_kb_precedent_requires_as_founder_flag(tmp_home, app, org_state, auth_headers):
    """Per spec §4.6, `opc kb precedent` requires --as-founder. The flag is
    intent, not identity — real auth awaits the Feishu integration — but the
    gate must exist so founder-only writes can't happen by accident."""
    _seed_escalated_task(org_state, task_id="TASK-042")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={"task_id": "TASK-042", "decision": "approve", "rationale": "r"},
        headers=auth_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "as_founder_required"
