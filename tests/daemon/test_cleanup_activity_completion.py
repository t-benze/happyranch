from fastapi.testclient import TestClient


def _receipt() -> dict:
    unavailable = {"available": False, "bytes": None, "inodes": None, "reason": "not_measured"}
    return {
        "version": 1, "mode": "report_only", "outcome": "completed",
        "measured_before": unavailable, "measured_after": unavailable,
        "reclaimed_bytes": 0, "reclaimed_inodes": 0, "removal_count": 0,
        "skip_count": None, "error_summary": None, "ambiguity_summary": "ledger_unavailable",
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
