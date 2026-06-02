from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from runtime.models import TokenUsage


def _seed(state) -> None:
    state.db.insert_session_token_usage(
        task_id="TASK-001",
        agent="dev_agent",
        session_id="s1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=100, output_tokens=50, model="sonnet"),
    )
    state.db.insert_session_token_usage(
        task_id="TASK-002",
        agent="dev_agent",
        session_id="s2",
        executor="claude",
        token_usage=TokenUsage(input_tokens=20, output_tokens=10),
    )
    state.db.insert_session_token_usage(
        task_id="TASK-003",
        agent="qa_engineer",
        session_id="s3",
        executor="codex",
        token_usage=TokenUsage(input_tokens=200, output_tokens=80),
    )


def test_tokens_requires_token(tmp_home, app) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/tokens")
    assert r.status_code == 401


def test_tokens_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/orgs/alpha/tokens", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_tokens_unknown_org_returns_404(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/missing/tokens", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_org"


def test_tokens_empty_returns_rows_list(tmp_home, app, org_state, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/tokens", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_tokens_returns_inserted_rows(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get("/api/v1/orgs/alpha/tokens", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 3
    assert {row["task_id"] for row in rows} == {"TASK-001", "TASK-002", "TASK-003"}
    by_task = {row["task_id"]: row for row in rows}
    assert by_task["TASK-001"]["input_tokens"] == 100
    assert by_task["TASK-001"]["output_tokens"] == 50
    assert by_task["TASK-001"]["executor"] == "claude"
    assert by_task["TASK-001"]["model"] == "sonnet"


def test_tokens_filters_by_task_id(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"task_id": "TASK-002"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "TASK-002"


def test_tokens_filters_by_agent(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"agent": "qa_engineer"},
        headers=auth_headers,
    )
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["agent"] == "qa_engineer"


def test_tokens_filters_by_since(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    # All seeded rows have created_at == now; a `since` set in the future
    # should drop everything.
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"since": future},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_tokens_limit_caps_rows(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"limit": 2},
        headers=auth_headers,
    )
    rows = r.json()["rows"]
    assert len(rows) == 2


def test_tokens_group_by_agent_returns_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "agent"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rollup" in body and "rows" not in body
    rollup = {row["agent"]: row for row in body["rollup"]}
    assert rollup["dev_agent"]["sessions"] == 2
    assert rollup["dev_agent"]["input_tokens"] == 120
    assert rollup["dev_agent"]["output_tokens"] == 60
    assert rollup["qa_engineer"]["sessions"] == 1
    assert rollup["qa_engineer"]["input_tokens"] == 200


def test_tokens_group_by_task_returns_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Two sessions on the same task to verify task-level aggregation.
    org_state.db.insert_session_token_usage(
        task_id="TASK-100",
        agent="dev_agent",
        session_id="s1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    org_state.db.insert_session_token_usage(
        task_id="TASK-100",
        agent="qa_engineer",
        session_id="s2",
        executor="claude",
        token_usage=TokenUsage(input_tokens=20, output_tokens=8),
    )
    org_state.db.insert_session_token_usage(
        task_id="TASK-200",
        agent="dev_agent",
        session_id="s3",
        executor="codex",
        token_usage=TokenUsage(input_tokens=7, output_tokens=3),
    )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "task"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    rollup = {row["task_id"]: row for row in body["rollup"]}
    assert rollup["TASK-100"]["sessions"] == 2
    assert rollup["TASK-100"]["input_tokens"] == 30
    assert rollup["TASK-100"]["output_tokens"] == 13
    assert rollup["TASK-200"]["sessions"] == 1


def test_tokens_invalid_group_by_returns_400(
    tmp_home, app, org_state, auth_headers,
) -> None:
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "invalid"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_tokens_group_by_agent_with_agent_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """`group_by=agent&agent=X` returns a one-row rollup scoped to X."""
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "agent", "agent": "dev_agent"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rollup = r.json()["rollup"]
    assert len(rollup) == 1
    assert rollup[0]["agent"] == "dev_agent"
    assert rollup[0]["sessions"] == 2
    assert rollup[0]["input_tokens"] == 120
    assert rollup[0]["output_tokens"] == 60


def test_tokens_group_by_task_with_task_id_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """`group_by=task&task_id=X` returns a one-row rollup scoped to X."""
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "task", "task_id": "TASK-001"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rollup = r.json()["rollup"]
    assert len(rollup) == 1
    assert rollup[0]["task_id"] == "TASK-001"
    assert rollup[0]["sessions"] == 1
    assert rollup[0]["input_tokens"] == 100
    assert rollup[0]["output_tokens"] == 50
