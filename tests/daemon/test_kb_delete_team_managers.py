"""KB delete: team managers can delete; workers are rejected; founder override always works."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.infrastructure.kb_store import KBEntry, KBStore


_MINIMAL_MD = "# Test entry\n\nBody.\n"


def _seed_entry(runtime_root, slug: str = "test-entry") -> None:
    store = KBStore(runtime_root / "kb")
    store.write_entry(
        KBEntry(
            slug=slug,
            title="Test entry",
            type="reference",
            topic="test",
            tags=[],
            body=_MINIMAL_MD,
        ),
        agent="dev_agent",
    )


def _delete(client, slug: str, agent: str, as_founder: bool = False) -> object:
    params: dict = {"agent": agent, "confirm": True}
    if as_founder:
        params["as_founder"] = True
    return client.request(
        "DELETE",
        f"/api/v1/kb/{slug}",
        params=params,
    )


def test_engineering_head_can_delete(tmp_home, app, runtime, auth_headers):
    _seed_entry(runtime.root, slug="entry-eh")
    client = TestClient(app)
    client.headers.update(auth_headers)
    r = _delete(client, "entry-eh", agent="engineering_head")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "slug": "entry-eh"}
    assert not (runtime.root / "kb" / "entry-eh.md").exists()


def test_content_manager_can_delete(tmp_home, app, runtime, auth_headers):
    _seed_entry(runtime.root, slug="entry-cm")
    client = TestClient(app)
    client.headers.update(auth_headers)
    r = _delete(client, "entry-cm", agent="content_manager")
    assert r.status_code == 200, r.text
    assert not (runtime.root / "kb" / "entry-cm.md").exists()


def test_worker_is_rejected(tmp_home, app, runtime, auth_headers):
    _seed_entry(runtime.root, slug="entry-worker")
    client = TestClient(app)
    client.headers.update(auth_headers)
    r = _delete(client, "entry-worker", agent="dev_agent")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "delete_forbidden"


def test_founder_override_always_allowed(tmp_home, app, runtime, auth_headers):
    _seed_entry(runtime.root, slug="entry-founder")
    client = TestClient(app)
    client.headers.update(auth_headers)
    r = _delete(client, "entry-founder", agent="dev_agent", as_founder=True)
    assert r.status_code == 200, r.text
    assert not (runtime.root / "kb" / "entry-founder.md").exists()
