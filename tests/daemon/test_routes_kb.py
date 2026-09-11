from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from runtime.infrastructure.kb_store import KBEntry, KBStore


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


def test_kb_routes_real_tag_matrix_and_dto_contracts(tmp_home, app, org_state, auth_headers):
    """Exercise parser projections through the shipping list/detail/reindex routes."""
    store = KBStore(org_state.root / "kb")
    cases = [
        ("array", "tags: [policy, finance]", ["policy", "finance"]),
        ("scalar", "tags: whole scalar", ["whole scalar"]),
        ("delimiter", "tags: policy, finance", ["policy, finance"]),
        ("empty-array", "tags: []", []),
        ("absent", None, []),
        ("null-tags", "tags: null", []),
        ("empty", "tags: ''", []),
        ("numeric", "tags: 42", []),
        ("boolean", "tags: true", []),
        ("mapping", "tags: {unexpected: mapping}", []),
        ("mixed", "tags: [string, 7]", []),
    ]
    sources: dict[str, bytes] = {}
    for slug, tags_line, _expected_tags in cases:
        tags = f"{tags_line}\n" if tags_line else ""
        source = (
            f"---\nslug: {slug}\ntitle: {slug} title\ntype: reference\ntopic: matrix\n"
            f"{tags}---\n\n# {slug}\n"
        )
        store.path_for(slug).write_text(source)
        sources[slug] = source.encode()
    scalar_source = (
        "---\nslug: scalar-search\ntitle: Unrelated title\ntype: reference\n"
        "topic: unrelated\ntags: policy, finance\n---\n\nNo query in this body.\n"
    )
    store.path_for("scalar-search").write_text(scalar_source)
    sources["scalar-search"] = scalar_source.encode()

    client = TestClient(app)
    expected = {slug: tags for slug, _raw, tags in cases} | {"scalar-search": ["policy, finance"]}
    for phase in ("before", "after"):
        listed = client.get("/api/v1/orgs/alpha/kb", headers=auth_headers)
        assert listed.status_code == 200
        entries = {entry["slug"]: entry for entry in listed.json()["entries"]}
        for slug, expected_tags in expected.items():
            assert set(entries[slug]) == {"slug", "title", "type", "topic", "tags", "updated_at"}
            assert entries[slug]["tags"] == expected_tags
            detail = client.get(f"/api/v1/orgs/alpha/kb/{slug}", headers=auth_headers)
            assert detail.status_code == 200
            assert detail.json()["tags"] == expected_tags
        if phase == "before":
            reindex = client.post("/api/v1/orgs/alpha/kb/reindex", headers=auth_headers)
            assert reindex.status_code == 200
            assert reindex.json() == {"ok": True}

    search = client.get(
        "/api/v1/orgs/alpha/kb/search?q=policy%2C%20finance", headers=auth_headers,
    )
    assert search.status_code == 200
    assert search.json() == {"hits": [
        {
            "slug": "delimiter", "title": "delimiter title",
            "snippet": "topic=matrix tags=['policy, finance']", "score": 2,
        },
        {
            "slug": "scalar-search", "title": "Unrelated title",
            "snippet": "topic=unrelated tags=['policy, finance']", "score": 2,
        },
    ]}
    assert all(path.read_bytes() == sources[slug] for slug, path in (
        (slug, store.path_for(slug)) for slug in sources
    ))


def test_kb_duplicate_route_is_tag_sensitive_without_emitting_tags(
    tmp_home, app, org_state, auth_headers,
):
    store = KBStore(org_state.root / "kb")
    sources = {
        "array-duplicate": (
        "---\nslug: array-duplicate\ntitle: Entirely unrelated heading\ntype: reference\n"
        "topic: unrelated\ntags: [policy, finance]\n---\n\nNo query in this body.\n"
        ).encode(),
        "scalar-duplicate": (
        "---\nslug: scalar-duplicate\ntitle: Another unrelated heading\ntype: reference\n"
        "topic: unrelated\ntags: policy, finance\n---\n\nNo query in this body.\n"
        ).encode(),
    }
    for slug, source in sources.items():
        store.path_for(slug).write_bytes(source)
    client = TestClient(app)
    expected_tags = {"array-duplicate": ["policy", "finance"], "scalar-duplicate": ["policy, finance"]}
    for phase in ("before", "after"):
        listed = client.get("/api/v1/orgs/alpha/kb", headers=auth_headers)
        assert listed.status_code == 200
        entries = {entry["slug"]: entry for entry in listed.json()["entries"]}
        for slug, tags in expected_tags.items():
            assert entries[slug]["tags"] == tags
            detail = client.get(f"/api/v1/orgs/alpha/kb/{slug}", headers=auth_headers)
            assert detail.status_code == 200
            assert detail.json()["tags"] == tags
        if phase == "before":
            assert client.post("/api/v1/orgs/alpha/kb/reindex", headers=auth_headers).json() == {"ok": True}
    included = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(
            slug="included", title="Completely different title", topic="other",
            tags=["policy", "finance"], body="No overlap in title or body.\n",
        ), headers=auth_headers,
    )
    assert included.status_code == 409
    candidate = included.json()["detail"]["candidates"]
    assert candidate == [{"slug": "array-duplicate", "title": "Entirely unrelated heading", "similarity": 0.346}]
    excluded = client.post(
        "/api/v1/orgs/alpha/kb",
        json=_add_body(
            slug="excluded", title="Still completely distinct", topic="other",
            tags=["policy, finance"], body="No overlap in title or body.\n",
        ), headers=auth_headers,
    )
    assert excluded.status_code == 200, excluded.text
    assert all(store.path_for(slug).read_bytes() == source for slug, source in sources.items())


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


def test_kb_add_accepts_arbitrary_type(tmp_home, app, auth_headers):
    """`type` is freeform — any non-empty string round-trips."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb", json=_add_body(type="guide", slug="guide-entry"),
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    got = client.get("/api/v1/orgs/alpha/kb/guide-entry", headers=auth_headers).json()
    assert got["type"] == "guide"


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


def test_kb_precedent_route_removed(tmp_home, app, auth_headers):
    """The dedicated `/kb/precedent` route is gone — that path is now just
    the slug 'precedent' under the standard GET/POST endpoints."""
    client = TestClient(app)
    # GET behaves as a normal entry lookup (no such slug → 404).
    r = client.get("/api/v1/orgs/alpha/kb/precedent", headers=auth_headers)
    assert r.status_code == 404
    # POSTing the legacy precedent body shape no longer validates.
    r = client.post(
        "/api/v1/orgs/alpha/kb/precedent",
        json={"task_id": "TASK-001", "decision": "approve", "rationale": "r"},
        headers=auth_headers,
    )
    assert r.status_code == 422


_CLI_HEADER = {"X-HappyRanch-Surface": "cli"}


def test_kb_get_with_cli_surface_header_increments_view(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    headers = {**auth_headers, **_CLI_HEADER}
    r = client.get("/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=headers)
    assert r.status_code == 200
    stats = org_state.db.kb_view_stats()
    assert stats == [
        {
            "slug": "alipay-refund-endpoint",
            "view_count": 1,
            "last_viewed_at": stats[0]["last_viewed_at"],
        }
    ]
    assert stats[0]["last_viewed_at"] is not None


def test_kb_get_without_surface_header_does_not_increment(tmp_home, app, org_state, auth_headers):
    """A web read (no X-HappyRanch-Surface header) must not be counted."""
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=auth_headers)
    assert r.status_code == 200
    assert org_state.db.kb_view_stats() == []


def test_kb_get_view_tracking_failure_does_not_500(tmp_home, app, org_state, auth_headers, monkeypatch):
    """A tracking-write failure must never 500 the read; the 200 body still returns."""
    _seed_kb(org_state.root)

    def _boom(_slug: str) -> None:
        raise RuntimeError("kb_views write exploded")

    monkeypatch.setattr(org_state.db, "record_kb_view", _boom)
    client = TestClient(app)
    headers = {**auth_headers, **_CLI_HEADER}
    r = client.get("/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "alipay-refund-endpoint"
    assert "Details." in body["body"]


def test_kb_get_404_does_not_increment(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    headers = {**auth_headers, **_CLI_HEADER}
    r = client.get("/api/v1/orgs/alpha/kb/ghost", headers=headers)
    assert r.status_code == 404
    assert org_state.db.kb_view_stats() == []


def test_kb_stats_endpoint_reflects_tally(tmp_home, app, org_state, auth_headers):
    _seed_kb(org_state.root)
    client = TestClient(app)
    headers = {**auth_headers, **_CLI_HEADER}
    client.get("/api/v1/orgs/alpha/kb/hk-visa-90day", headers=headers)
    client.get("/api/v1/orgs/alpha/kb/hk-visa-90day", headers=headers)
    client.get("/api/v1/orgs/alpha/kb/alipay-refund-endpoint", headers=headers)

    r = client.get("/api/v1/orgs/alpha/kb/stats", headers=auth_headers)
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["slug"] for e in entries] == ["hk-visa-90day", "alipay-refund-endpoint"]
    assert entries[0]["view_count"] == 2
    assert entries[1]["view_count"] == 1
    assert all(e["last_viewed_at"] is not None for e in entries)


def test_kb_stats_path_not_captured_by_slug_param(tmp_home, app, org_state, auth_headers):
    """/kb/stats must route to the stats handler, not be read as slug='stats'."""
    _seed_kb(org_state.root)
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/kb/stats", headers=auth_headers)
    assert r.status_code == 200
    assert "entries" in r.json()


def test_kb_accepts_arbitrary_type(tmp_home, app, org_state, auth_headers):
    """Founders or agents can write a 'precedent'-typed entry through the
    plain add route — no special route, no --as-founder gate."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/kb",
        json={
            "agent": "founder",
            "slug": "ruling-task-099",
            "title": "Refund cap raised to $500",
            "type": "precedent",
            "topic": "payment",
            "tags": ["refund", "policy"],
            "body": "# Ruling\n\nFounder decision after TASK-099.\n",
            "source_task": "TASK-099",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    got = client.get("/api/v1/orgs/alpha/kb/ruling-task-099", headers=auth_headers).json()
    assert got["type"] == "precedent"
    assert got["source_task"] == "TASK-099"
