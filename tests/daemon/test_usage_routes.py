from __future__ import annotations

from fastapi.testclient import TestClient


def test_usage_routes_require_same_bearer_auth_as_tokens(app) -> None:
    client = TestClient(app)
    for path in ("/api/v1/orgs/alpha/tokens", "/api/v1/orgs/alpha/usage/workload"):
        assert client.get(path).status_code == 401


def test_workload_route_returns_snapshot_metadata(app, auth_headers) -> None:
    response = TestClient(app).get(
        "/api/v1/orgs/alpha/usage/workload",
        params={"compare": "true"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"] == body["data_through"]
    assert body["previous_window"] is not None
    assert body["agents"] == []


def test_efficiency_route_lists_options_without_a_selection(app, auth_headers) -> None:
    response = TestClient(app).get(
        "/api/v1/orgs/alpha/usage/efficiency", headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_efficiency_route_requires_exactly_one_model_selector(app, auth_headers) -> None:
    client = TestClient(app)
    path = "/api/v1/orgs/alpha/usage/efficiency"
    neither = client.get(path, params={"executor": "codex"}, headers=auth_headers)
    both = client.get(
        path,
        params={"executor": "codex", "model": "gpt-5", "model_unpinned": "true"},
        headers=auth_headers,
    )
    no_executor = client.get(path, params={"model": "gpt-5"}, headers=auth_headers)
    selected = client.get(
        path, params={"executor": "codex", "model_unpinned": "true"},
        headers=auth_headers,
    )

    assert neither.status_code == 422
    assert neither.json()["detail"]["code"] == "exactly_one_model_selector_required"
    assert both.status_code == 422
    assert no_executor.status_code == 422
    assert selected.status_code == 200
    assert [row["run_type"] for row in selected.json()["rows"]] == [
        "worker_task", "manager_decision", "thread_reply", "thread_followup", "dream",
    ]
