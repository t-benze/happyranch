from fastapi.testclient import TestClient


def _receipt() -> dict:
    unavailable = {"available": False, "bytes": None, "inodes": None, "reason": "not_measured"}
    return {
        "version": 1, "mode": "report_only", "outcome": "completed",
        "measured_before": unavailable, "measured_after": unavailable,
        "reclaimed_bytes": 0, "reclaimed_inodes": 0, "removal_count": 0,
        "skip_count": None, "error_summary": None, "ambiguity_summary": "ledger_unavailable",
    }


def _new_cleanup_task(client: TestClient, org_state, auth_headers) -> tuple[str, dict]:
    task_id = client.post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"},
        headers=auth_headers,
    ).json()["task_id"]
    # The scheduler owns this association in production.  This HTTP-fixture
    # seam supplies the same authoritative task/trigger state without running
    # the scheduler.
    org_state.db._conn.execute(
        "UPDATE tasks SET assigned_agent = ? WHERE id = ?", ("dev_agent", task_id),
    )
    org_state.db._conn.commit()
    org_state.sessions.set_active(task_id, "dev_agent", "sess-cleanup")
    org_state.db.insert_audit_log(
        task_id, "dev_agent", "workspace_cleanup_triggered",
        {"brief_kind": "report_only", "run_number": 1},
    )
    return task_id, {
        "session_id": "sess-cleanup", "agent": "dev_agent", "status": "completed",
        "confidence": 80, "output_summary": "ok", "cleanup_activity": _receipt(),
    }


def test_cleanup_completion_absent_field_legacy_compatible(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id = client.post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "ordinary"}, headers=auth_headers,
    ).json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-ordinary")
    response = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"session_id": "sess-ordinary", "agent": "dev_agent", "status": "completed", "confidence": 80, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert response.status_code == 200


def test_cleanup_completion_explicit_invalid_receipt_no_result_no_audit(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id = client.post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"}, headers=auth_headers,
    ).json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-cleanup")
    org_state.db.insert_audit_log(task_id, "dev_agent", "workspace_cleanup_triggered", {"brief_kind": "report_only", "run_number": 1})
    payload = {"session_id": "sess-cleanup", "agent": "dev_agent", "status": "completed", "confidence": 80, "output_summary": "ok", "cleanup_activity": _receipt()}
    payload["cleanup_activity"]["reclaimed_bytes"] = None
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []


def test_cleanup_retry_after_post_commit_interruption_clears_and_publishes(app, org_state, auth_headers, monkeypatch) -> None:
    """H7: an interruption after the real commit leaves a durable pair, then retry finishes volatile effects."""
    client = TestClient(app, raise_server_exceptions=False)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    original = org_state.db.insert_cleanup_completion

    def commit_then_interrupt(**kwargs):
        assert original(**kwargs) is True
        raise RuntimeError("test interruption after commit")

    monkeypatch.setattr(org_state.db, "insert_cleanup_completion", commit_then_interrupt)
    first = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert first.status_code == 500
    assert len(org_state.db.get_task_results(task_id)) == 1
    assert len([r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "workspace_cleanup_completed"]) == 1
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"

    monkeypatch.setattr(org_state.db, "insert_cleanup_completion", original)
    published: list[dict] = []

    async def observe_publish(received_task_id: str, event: dict) -> None:
        published.append({"task_id": received_task_id, **event})

    monkeypatch.setattr(org_state.event_bus, "publish", observe_publish)
    retry = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert retry.status_code == 200
    assert org_state.sessions.get_active(task_id, "dev_agent") is None
    assert published == [{"task_id": task_id, "type": "completion_reported", "agent": "dev_agent", "session_id": "sess-cleanup", "status": "completed"}]
    assert len(org_state.db.get_task_results(task_id)) == 1
    assert len([r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "workspace_cleanup_completed"]) == 1
