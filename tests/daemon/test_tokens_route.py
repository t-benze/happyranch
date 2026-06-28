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


def test_tokens_filters_by_thread_scope_and_purpose(
    tmp_home, app, org_state, auth_headers,
) -> None:
    org_state.db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="TOK-1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=30, output_tokens=5),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
    )
    org_state.db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="TOK-2",
        executor="claude",
        token_usage=TokenUsage(input_tokens=99, output_tokens=1),
        scope_type="thread",
        scope_id="THR-002",
        thread_id="THR-002",
        invocation_purpose="bootstrap",
    )

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={
            "scope_type": "thread",
            "thread_id": "THR-001",
            "purpose": "reply",
        },
        headers=auth_headers,
    )

    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["scope_type"] == "thread"
    assert rows[0]["scope_id"] == "THR-001"
    assert rows[0]["thread_id"] == "THR-001"
    assert rows[0]["invocation_purpose"] == "reply"
    assert rows[0]["total_tokens"] == 35


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


def test_tokens_group_by_thread_returns_rollups(
    tmp_home, app, org_state, auth_headers,
) -> None:
    org_state.db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="thread-a",
        executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
    )
    org_state.db.insert_session_token_usage(
        task_id=None,
        agent="bob",
        session_id="thread-b",
        executor="codex",
        token_usage=TokenUsage(input_tokens=20, output_tokens=7),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="task_followup",
    )

    thread_r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "thread"},
        headers=auth_headers,
    )

    assert thread_r.status_code == 200
    # thread row: alice/claude (NULL model) + bob/codex (NULL model). The single
    # null-claude session makes null_claude_*_created_at a real timestamp — pop.
    [trow] = thread_r.json()["rollup"]
    assert trow.pop("null_claude_min_created_at") is not None
    assert trow.pop("null_claude_max_created_at") is not None
    assert trow == {
        "thread_id": "THR-001",
        "sessions": 2,
        "input_tokens": 30,
        "output_tokens": 12,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 42,
        "churn_tokens": 42,
        "context_tokens": 42,
        "model_distinct": 0,
        "model_any": None,
        "non_null_sessions": 0,
        "null_codex_sessions": 1,
        "null_claude_sessions": 1,
    }


def test_tokens_group_by_scope_returns_scope_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed(org_state)
    org_state.db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="TOK-1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=30, output_tokens=5),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
    )

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "scope", "scope_type": "thread"},
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert r.json()["rollup"] == [{
        "scope_type": "thread",
        "scope_id": "THR-001",
        "sessions": 1,
        "input_tokens": 30,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 35,
    }]


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


def _seed_failed(state) -> None:
    """Two failed tasks + one completed task, each with token usage."""
    from runtime.models import TaskRecord, TaskStatus

    state.db.insert_task(TaskRecord(id="TASK-F1", brief="x", status=TaskStatus.FAILED))
    state.db.insert_task(TaskRecord(id="TASK-F2", brief="x", status=TaskStatus.FAILED))
    state.db.insert_task(TaskRecord(id="TASK-OK", brief="x", status=TaskStatus.COMPLETED))
    state.db.insert_session_token_usage(
        task_id="TASK-F1", agent="dev_agent", session_id="s1", executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    state.db.insert_session_token_usage(
        task_id="TASK-F1", agent="qa_engineer", session_id="s2", executor="codex",
        token_usage=TokenUsage(input_tokens=20, output_tokens=8),
    )
    state.db.insert_session_token_usage(
        task_id="TASK-F2", agent="dev_agent", session_id="s3", executor="claude",
        token_usage=TokenUsage(input_tokens=7, output_tokens=3),
    )
    # Completed task usage must be excluded from the failed-task rollup.
    state.db.insert_session_token_usage(
        task_id="TASK-OK", agent="dev_agent", session_id="s4", executor="claude",
        token_usage=TokenUsage(input_tokens=999, output_tokens=999),
    )


def test_tokens_group_by_failed_task_returns_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_failed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "failed_task"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rollup" in body and "rows" not in body
    keyed = {(row["task_id"], row["agent"]): row for row in body["rollup"]}
    # Only failed tasks appear; the completed task is excluded.
    assert {row["task_id"] for row in body["rollup"]} == {"TASK-F1", "TASK-F2"}
    assert keyed[("TASK-F1", "dev_agent")]["sessions"] == 1
    assert keyed[("TASK-F1", "dev_agent")]["input_tokens"] == 10
    assert keyed[("TASK-F1", "qa_engineer")]["input_tokens"] == 20
    assert keyed[("TASK-F2", "dev_agent")]["input_tokens"] == 7


def test_tokens_group_by_failed_task_composes_with_agent_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_failed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "failed_task", "agent": "qa_engineer"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rollup = r.json()["rollup"]
    assert len(rollup) == 1
    assert rollup[0]["task_id"] == "TASK-F1"
    assert rollup[0]["agent"] == "qa_engineer"
    assert rollup[0]["input_tokens"] == 20


def _seed_purpose(state) -> None:
    """Two 'reply' thread sessions + one 'bootstrap' + a NULL-purpose task row."""
    state.db.insert_session_token_usage(
        task_id=None, agent="alice", session_id="p1", executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=2),
        scope_type="thread", scope_id="THR-1", thread_id="THR-1",
        invocation_purpose="reply",
    )
    state.db.insert_session_token_usage(
        task_id=None, agent="bob", session_id="p2", executor="claude",
        token_usage=TokenUsage(input_tokens=20, output_tokens=3),
        scope_type="thread", scope_id="THR-2", thread_id="THR-2",
        invocation_purpose="reply",
    )
    state.db.insert_session_token_usage(
        task_id=None, agent="alice", session_id="p3", executor="claude",
        token_usage=TokenUsage(input_tokens=5, output_tokens=1),
        scope_type="thread", scope_id="THR-1", thread_id="THR-1",
        invocation_purpose="bootstrap",
    )
    # NULL invocation_purpose (a normal task row) must be excluded.
    state.db.insert_session_token_usage(
        task_id="TASK-900", agent="alice", session_id="p4", executor="claude",
        token_usage=TokenUsage(input_tokens=999, output_tokens=999),
    )


def test_tokens_group_by_purpose_returns_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_purpose(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "purpose"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rollup" in body and "rows" not in body
    keyed = {row["purpose"]: row for row in body["rollup"]}
    # NULL purpose excluded; only the two named purposes appear.
    assert set(keyed) == {"reply", "bootstrap"}
    assert keyed["reply"]["sessions"] == 2
    assert keyed["reply"]["input_tokens"] == 30
    assert keyed["reply"]["total_tokens"] == 35
    assert keyed["bootstrap"]["sessions"] == 1
    assert keyed["bootstrap"]["input_tokens"] == 5


def test_tokens_group_by_purpose_composes_with_agent_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_purpose(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "purpose", "agent": "bob"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rollup = r.json()["rollup"]
    # bob only has the one 'reply' session.
    assert len(rollup) == 1
    assert rollup[0]["purpose"] == "reply"
    assert rollup[0]["input_tokens"] == 20


def test_tokens_group_by_model_returns_rollup(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """`group_by=model` returns a rollup keyed by the stored model column.
    A NULL model row is honest — never blank, never a guessed correction."""
    from runtime.models import TokenUsage
    org_state.db.insert_session_token_usage(
        task_id="TASK-001",
        agent="dev_agent",
        session_id="s1",
        executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, model="sonnet"),
    )
    org_state.db.insert_session_token_usage(
        task_id="TASK-002",
        agent="dev_agent",
        session_id="s2",
        executor="claude",
        token_usage=TokenUsage(input_tokens=20, output_tokens=10, model="sonnet"),
    )
    org_state.db.insert_session_token_usage(
        task_id="TASK-003",
        agent="qa_engineer",
        session_id="s3",
        executor="codex",
        token_usage=TokenUsage(input_tokens=100, output_tokens=50),
    )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "model"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rollup" in body and "rows" not in body
    by_model = {row["model"]: row for row in body["rollup"]}
    assert by_model["sonnet"]["sessions"] == 2
    assert by_model["sonnet"]["input_tokens"] == 30
    assert by_model["sonnet"]["output_tokens"] == 15
    assert by_model["sonnet"]["total_tokens"] == 45
    # NULL model row is honest
    assert by_model[None]["sessions"] == 1
    assert by_model[None]["input_tokens"] == 100
    assert by_model[None]["total_tokens"] == 150


def test_tokens_group_by_model_composes_with_since(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Window filter AND-composes with model grouping."""
    from datetime import datetime, timedelta, timezone
    from runtime.models import TokenUsage
    org_state.db.insert_session_token_usage(
        task_id="TASK-001", agent="dev", session_id="s1", executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, model="sonnet"),
    )
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tokens",
        params={"group_by": "model", "since": future},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["rollup"] == []
