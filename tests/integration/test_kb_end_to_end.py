"""End-to-end: daemon + real HTTP + two KB write/read cycles.

No fake-claude binary needed — this test exercises the KB surface only,
which is fully synchronous and does not spawn agent subprocesses.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def test_agent_writes_other_agent_reads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    from src.daemon import paths as paths_mod
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    headers = {"Authorization": f"Bearer {paths_mod.read_token()}"}

    runtime = RuntimeDir.init(tmp_path / "runtime")
    state = DaemonState.from_runtime(runtime, Settings())
    app = create_app(state)
    client = TestClient(app)

    # Agent A writes
    r_add = client.post(
        "/api/v1/kb",
        json={
            "agent": "compliance_agent",
            "slug": "mainland-visa-90day",
            "title": "Mainland China tourist L-visa (90-day)",
            "type": "reference",
            "topic": "visa",
            "tags": ["mainland", "tourist"],
            "body": "# Mainland China tourist L-visa (90-day)\n\nEligibility, fees, processing time.\n",
            "force_new_sibling": False,
        },
        headers=headers,
    )
    assert r_add.status_code == 200

    # Agent B searches and reads
    r_search = client.get(
        "/api/v1/kb/search", params={"q": "tourist L-visa"}, headers=headers
    )
    assert r_search.status_code == 200
    hits = r_search.json()["hits"]
    assert hits and hits[0]["slug"] == "mainland-visa-90day"

    r_get = client.get(f"/api/v1/kb/{hits[0]['slug']}", headers=headers)
    assert r_get.status_code == 200
    entry = r_get.json()
    assert entry["authored_by"] == "compliance_agent"
    assert "Eligibility" in entry["body"]

    # Index exists on disk
    assert (runtime.root / "kb" / "_index.md").exists()
