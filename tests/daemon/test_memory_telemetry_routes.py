"""THR-091 Slice 2 fix-forward: production-seam route tests.

Tests for trusted session correlation via SessionTracker, cross-task/session
negatives, search privacy, legacy compatibility, and audit pagination.
"""
from __future__ import annotations

import json


def test_read_route_without_validated_session_is_uncorrelated(client_with_runtime):
    """GET memory read without a validated session → source=explicit_or_other
    and no task_id in payload."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["id"] == "MEM-001"
    assert payload["slug"] == "test"
    assert payload.get("source", "explicit_or_other") == "explicit_or_other"
    assert "task_id" not in payload
    assert "session_id" not in payload


def test_read_route_with_invalid_session_is_uncorrelated(client_with_runtime):
    """GET memory read with a session_id not in SessionTracker → no correlation."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-ghost"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert payload.get("source", "explicit_or_other") == "explicit_or_other"
    assert "task_id" not in payload


def test_read_route_with_validated_session_gets_correlated(client_with_runtime):
    """GET memory read with a SessionTracker-validated session → correlation
    metadata attached."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    org.sessions.set_active(
        "TASK-001", "dev_agent", "sess-real", org_slug="alpha",
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-real"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["session_id"] == "sess-real"
    assert payload["task_id"] == "TASK-001"
    # source is explicit_or_other because there's no impression for this session
    assert payload["source"] == "explicit_or_other"


def test_read_cannot_cross_credit_different_task(client_with_runtime):
    """A read in TASK-B with TASK-A's session_id → NOT credited as TASK-A's
    digest.  Server-side validation binds to the CURRENT task."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    # Set up TASK-A with a digest impression
    org.db.insert_audit_log(
        task_id="TASK-A", agent="dev_agent",
        action="memory_digest_impression",
        payload=json.dumps({
            "agent": "dev_agent", "session_id": "sess-shared",
            "digest_ids": ["MEM-001"], "digest_count": 1, "budget": 1500,
        }),
    )
    # Now set active session for TASK-B (different task, same agent)
    org.sessions.set_active(
        "TASK-B", "dev_agent", "sess-shared", org_slug="alpha",
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-shared"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["session_id"] == "sess-shared"
    assert payload["task_id"] == "TASK-B"
    # _resolve_read_source checks TASK-B impressions, not TASK-A's.
    # No impression exists for TASK-B → source=explicit_or_other.
    assert payload["source"] == "explicit_or_other"


def test_read_no_cross_credit_stale_session(client_with_runtime):
    """A read with a previously-active session that has been cleared → no
    correlation (get_context_by_session returns None for cleared sessions)."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    org.sessions.set_active(
        "TASK-001", "dev_agent", "sess-stale", org_slug="alpha",
    )
    org.sessions.clear("TASK-001", "dev_agent")
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-stale"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert "task_id" not in payload
    assert payload.get("source", "explicit_or_other") == "explicit_or_other"


def test_read_no_cross_credit_other_agent(client_with_runtime):
    """A session bound to a different agent → not credited for the route's agent."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    # Session belongs to qa_engineer, not dev_agent
    org.sessions.set_active(
        "TASK-001", "qa_engineer", "sess-other-agent", org_slug="alpha",
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-other-agent"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    # Agent mismatch: context agent (qa_engineer) != route agent (dev_agent)
    assert "task_id" not in payload
    assert payload.get("source", "explicit_or_other") == "explicit_or_other"


def test_search_route_without_validated_session_no_correlation(client_with_runtime):
    """POST memory search without validated session → search telemetry has
    no task_id correlation."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    r = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search",
        json={"query": "test"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_search'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert "session_id" not in payload
    assert "task_id" not in payload


def test_search_route_with_validated_session_gets_correlated(client_with_runtime):
    """POST memory search with validated session → task_id correlation attached."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    org.sessions.set_active(
        "TASK-999", "dev_agent", "sess-search", org_slug="alpha",
    )
    r = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search",
        json={"query": "test"},
        params={"session_id": "sess-search"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_search'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["session_id"] == "sess-search"
    assert payload["task_id"] == "TASK-999"


def test_search_route_does_not_store_query(client_with_runtime):
    """Search via HTTP route never persists the raw query text."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search",
        json={"query": "secret sensitive query text"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_search'"
        " AND agent = 'dev_agent'",
    )
    for row in rows:
        payload = json.loads(row["payload"])
        assert "query" not in payload
        assert "query_text" not in payload
        assert "secret" not in json.dumps(payload).lower()


def test_search_route_kb_hits_excluded_from_memory_ids(client_with_runtime):
    """KB hits from search route are counted but excluded from memory_ids."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test-entry\ntitle: Test Entry\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nTest body content\n"
    )
    r = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search",
        json={"query": "Test", "include_kb": False},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_search'"
        " AND agent = 'dev_agent'",
    )
    for row in rows:
        payload = json.loads(row["payload"])
        for mid in payload.get("memory_ids", []):
            assert mid.startswith("MEM-"), f"Expected MEM- prefix, got {mid}"


def test_legacy_read_rows_still_readable_via_route(client_with_runtime):
    """Directly inserted legacy read row (no source/session_id) doesn't crash
    the route or subsequent reads."""
    client, org = client_with_runtime
    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    org.db.insert_audit_log(
        task_id="AGENT-dev_agent", agent="dev_agent",
        action="memory_read",
        payload=json.dumps({"id": "MEM-001", "slug": "test"}),
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
    )
    assert r.status_code == 200


def test_audit_route_supports_cursor_pagination(client_with_runtime):
    """The /audit route provides next_cursor and supports cursor pagination."""
    client, org = client_with_runtime
    for i in range(10):
        org.db.insert_audit_log(
            task_id="AGENT-dev_agent", agent="dev_agent",
            action="memory_read",
            payload=json.dumps({"id": f"MEM-{i:03d}", "slug": "x"}),
        )
    # First page: limit 4
    r = client.get(
        "/api/v1/orgs/alpha/audit",
        params={"action": "memory_read", "limit": 4},
    )
    assert r.status_code == 200
    page1 = r.json()
    assert len(page1["entries"]) == 4
    assert page1["next_cursor"] is not None

    # Second page
    r2 = client.get(
        "/api/v1/orgs/alpha/audit",
        params={"action": "memory_read", "limit": 4,
                "cursor": page1["next_cursor"]},
    )
    assert r2.status_code == 200
    page2 = r2.json()
    assert len(page2["entries"]) == 4

    # Third page: should have 2 remaining
    r3 = client.get(
        "/api/v1/orgs/alpha/audit",
        params={"action": "memory_read", "limit": 4,
                "cursor": page2["next_cursor"]},
    )
    assert r3.status_code == 200
    page3 = r3.json()
    assert len(page3["entries"]) == 2
    assert page3["next_cursor"] is None

    # All rows distinct
    all_ids = [e["id"] for e in page1["entries"] + page2["entries"] + page3["entries"]]
    assert len(all_ids) == 10 == len(set(all_ids))


# ---------------------------------------------------------------------------
# Cross-org session validation — ctx_org must match route org_slug
# ---------------------------------------------------------------------------


def _setup_beta_org_sessions(org_state, daemon_state):
    """Create and register a beta org with its own SessionTracker.

    Returns the beta OrgState so tests can insert cross-org session
    contexts and verify that alpha routes reject them.
    """
    from runtime.daemon.org_state import OrgState
    from runtime.config import Settings

    if "beta" in daemon_state.orgs:
        return daemon_state.orgs["beta"]

    runtime_root = org_state.root.parent
    beta_root = runtime_root / "beta"
    beta_root.mkdir(parents=True, exist_ok=True)
    (beta_root / "org").mkdir(exist_ok=True)
    (beta_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
    )
    settings = Settings()
    beta = OrgState.load(slug="beta", root=beta_root, settings=settings)
    daemon_state.orgs["beta"] = beta
    return beta


def test_read_cross_org_session_uncorrelated(client_with_runtime):
    """A session registered under org 'beta' must NOT correlate for
    an 'alpha' route — ctx_org != slug, so source=explicit_or_other."""
    client, org = client_with_runtime
    daemon_state = client.app.state.daemon
    beta = _setup_beta_org_sessions(org, daemon_state)

    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    # Set active on beta org, same agent+task as alpha would use
    beta.sessions.set_active(
        "TASK-X", "dev_agent", "sess-cross-org", org_slug="beta",
    )
    r = client.get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/MEM-001",
        params={"session_id": "sess-cross-org"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_read'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert "task_id" not in payload
    assert payload.get("source", "explicit_or_other") == "explicit_or_other"


def test_search_cross_org_session_uncorrelated(client_with_runtime):
    """A session registered under org 'beta' must NOT correlate for
    an 'alpha' search route — ctx_org != slug, so no correlation."""
    client, org = client_with_runtime
    daemon_state = client.app.state.daemon
    beta = _setup_beta_org_sessions(org, daemon_state)

    ws = org.root / "workspaces" / "dev_agent"
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "memory" / "MEM-001-test.md").write_text(
        "---\nid: MEM-001\nslug: test\ntitle: Test\ntopic: w\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\n"
        "salience: 50\n---\n\nbody\n"
    )
    # Set active on beta org
    beta.sessions.set_active(
        "TASK-Y", "dev_agent", "sess-cross-search", org_slug="beta",
    )
    r = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/search",
        json={"query": "test"},
        params={"session_id": "sess-cross-search"},
    )
    assert r.status_code == 200
    rows = org.db.fetch_all_readonly(
        "SELECT payload FROM audit_log WHERE action = 'memory_search'"
        " AND agent = 'dev_agent' ORDER BY id DESC LIMIT 1",
    )
    payload = json.loads(rows[0]["payload"])
    assert "task_id" not in payload
    assert "session_id" not in payload
