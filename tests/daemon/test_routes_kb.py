from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.infrastructure.kb_store import KBEntry, KBStore


def _seed_kb(runtime_root: Path, agent: str = "dev_agent") -> KBStore:
    store = KBStore(runtime_root / "kb")
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
            slug="mainland-visa-90day",
            title="Mainland China tourist L-visa",
            type="reference",
            topic="visa",
            tags=["mainland"],
            body="# Mainland China tourist L-visa\n\nRules.\n",
        ),
        agent=agent,
    )
    return store


def test_kb_list_returns_all_entries(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb", headers=auth_headers)
    assert r.status_code == 200
    slugs = [e["slug"] for e in r.json()["entries"]]
    assert set(slugs) == {"alipay-refund-endpoint", "mainland-visa-90day"}


def test_kb_list_filter_by_topic(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb?topic=visa", headers=auth_headers)
    assert r.status_code == 200
    assert [e["slug"] for e in r.json()["entries"]] == ["mainland-visa-90day"]


def test_kb_list_filter_by_type(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb?type=reference", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 2


def test_kb_get_returns_entry_body(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb/alipay-refund-endpoint", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "alipay-refund-endpoint"
    assert body["title"].startswith("Alipay")
    assert "Details." in body["body"]
    assert body["authored_by"] == "dev_agent"


def test_kb_get_returns_404_on_missing(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb/ghost", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_kb_search_ranks_title_hits(tmp_home, app, runtime, auth_headers):
    _seed_kb(runtime.root)
    client = TestClient(app)
    r = client.get("/api/v1/kb/search?q=Alipay", headers=auth_headers)
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits[0]["slug"] == "alipay-refund-endpoint"


def test_kb_routes_reject_when_idle(tmp_home, app_idle, auth_headers):
    client = TestClient(app_idle)
    r = client.get("/api/v1/kb", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"
