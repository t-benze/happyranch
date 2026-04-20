from __future__ import annotations

from fastapi.testclient import TestClient


def test_submit_task_returns_id(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "test"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["task_id"].startswith("TASK-")


def test_submit_task_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_list_tasks_returns_list(tmp_home, app, auth_headers) -> None:
    TestClient(app).post(
        "/api/v1/tasks", json={"type": "general", "brief": "x"}, headers=auth_headers,
    )
    r = TestClient(app).get("/api/v1/tasks", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["tasks"]
    assert len(items) >= 1


def test_get_task_detail_404_when_missing(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/tasks/TASK-999", headers=auth_headers)
    assert r.status_code == 404


def test_submit_task_invalid_type_returns_422(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "garbage", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_completion_requires_session_id(tmp_home, app, auth_headers) -> None:
    # Create a task first
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"agent": "dev_agent", "status": "completed", "confidence": 90,
              "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # missing session_id


def test_completion_session_mismatch_409(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Mark a different session_id as active.
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-real")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-stale", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_completion_unknown_session_409(tmp_home, app, daemon_state, auth_headers) -> None:
    """If the daemon never registered a session for (task, agent), reject —
    do not silently persist a fabricated completion."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    # Note: no set_active() call — tracker is empty for (task_id, dev_agent).

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "fabricated", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    # And nothing was persisted.
    assert daemon_state.db.get_task_results(task_id) == []


def test_completion_persists_when_session_matches(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = daemon_state.db.get_task_results(task_id)
    assert any(r["session_id"] == "sess-1" for r in rows)


def test_completion_clears_session_so_duplicate_rejected(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """After a successful completion POST, the tracker must be cleared so that a
    second POST with the same session id is rejected as unknown_session rather
    than silently persisting a duplicate row."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    payload = {
        "session_id": "sess-1", "agent": "dev_agent",
        "status": "completed", "confidence": 90, "output_summary": "ok",
    }
    first = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert first.status_code == 200

    second = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "unknown_session"
    # And the second POST did not persist a duplicate row.
    rows = daemon_state.db.get_task_results(task_id)
    assert len([r for r in rows if r["session_id"] == "sess-1"]) == 1


def test_completion_preserves_empty_risks_flagged(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """An empty risks_flagged list submitted by the agent must round-trip as an
    empty list, not be coerced to NULL/None by the DB layer."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok",
              "risks_flagged": []},
        headers=auth_headers,
    )
    assert r.status_code == 200
    latest = daemon_state.db.get_latest_task_result(task_id, "dev_agent", "sess-1")
    assert latest is not None
    assert latest["risks_flagged"] == []


def test_completion_persists_artifact_dir(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-a")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={
            "session_id": "sess-a", "agent": "dev_agent",
            "status": "completed", "confidence": 80,
            "output_summary": "Wrote Q1 report",
            "artifact_dir": f"artifacts/{task_id}",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = daemon_state.db.get_task_results(task_id)
    assert rows[-1]["artifact_dir"] == f"artifacts/{task_id}"


def test_recall_returns_task_payload(tmp_home, app, daemon_state, auth_headers) -> None:
    from src.models import TaskRecord, TaskStatus, TaskType
    daemon_state.db.insert_task(
        TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="Review Q1")
    )
    daemon_state.db.update_task(
        "TASK-001",
        status=TaskStatus.COMPLETED,
        note="Report delivered",
        final_artifact_dir="artifacts/TASK-001",
    )
    r = TestClient(app).get("/api/v1/tasks/TASK-001/recall", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "TASK-001"
    assert body["output_summary"] == "Report delivered"
    assert body["artifact_dir"] == "artifacts/TASK-001"
    assert body["children"] == []


def test_recall_missing_task_returns_404(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/tasks/TASK-404/recall", headers=auth_headers)
    assert r.status_code == 404


def test_recall_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/tasks/TASK-001/recall", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_recall_tree_includes_descendants(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TaskRecord, TaskType
    daemon_state.db.insert_task(
        TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root")
    )
    daemon_state.db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child",
        parent_task_id="TASK-001",
    ))
    r = TestClient(app).get(
        "/api/v1/tasks/TASK-001/recall",
        params={"tree": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "TASK-001"
    assert isinstance(body["children"], list)
    assert body["children"][0]["task_id"] == "TASK-002"
    # Grandchildren slot is empty but still a list
    assert body["children"][0]["children"] == []


def test_recall_include_artifact_reads_files(
    tmp_home, app, daemon_state, runtime, auth_headers,
) -> None:
    from src.models import TaskRecord, TaskType
    ws = runtime.workspaces_dir / "dev_agent"
    artifact = ws / "artifacts" / "TASK-001"
    artifact.mkdir(parents=True)
    (artifact / "report.md").write_text("# Q1 report\n\nAll good.")
    daemon_state.db.insert_task(TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="b",
        assigned_agent="dev_agent",
    ))
    daemon_state.db.update_task(
        "TASK-001", final_artifact_dir="artifacts/TASK-001",
    )
    r = TestClient(app).get(
        "/api/v1/tasks/TASK-001/recall",
        params={"include_artifact": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["artifact"] == {
        "files": [{"path": "report.md", "content": "# Q1 report\n\nAll good."}],
        "truncated": False,
    }


def test_recall_rejects_absolute_artifact_path(
    tmp_home, app, daemon_state, runtime, auth_headers,
) -> None:
    """artifact_dir comes from an agent-supplied completion payload. A buggy or
    malicious agent that stores an absolute path must not be able to read
    arbitrary files on the host via /recall?include_artifact=true."""
    from src.models import TaskRecord, TaskType
    secret = tmp_home / "secret.txt"
    secret.write_text("DO NOT LEAK")
    daemon_state.db.insert_task(TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="b",
        assigned_agent="dev_agent",
    ))
    daemon_state.db.update_task(
        "TASK-001", final_artifact_dir=str(tmp_home),
    )
    r = TestClient(app).get(
        "/api/v1/tasks/TASK-001/recall",
        params={"include_artifact": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    # Must not expose files from outside the assigned agent's workspace.
    # Either the endpoint returns no artifact payload at all, or an empty one —
    # but it must never contain secret.txt.
    artifact = body.get("artifact")
    contents = "" if not artifact else "".join(
        f.get("content", "") for f in artifact.get("files", [])
    )
    assert "DO NOT LEAK" not in contents


def test_recall_rejects_parent_traversal_artifact_path(
    tmp_home, app, daemon_state, runtime, auth_headers,
) -> None:
    """A `..` in artifact_dir must not let an agent read another agent's
    workspace."""
    from src.models import TaskRecord, TaskType
    # dev_agent workspace must exist so `dev_agent/..` can resolve through it.
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
    other = runtime.workspaces_dir / "other_agent" / "secrets"
    other.mkdir(parents=True)
    (other / "token.txt").write_text("SUPERSECRET")
    daemon_state.db.insert_task(TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="b",
        assigned_agent="dev_agent",
    ))
    daemon_state.db.update_task(
        "TASK-001", final_artifact_dir="../other_agent/secrets",
    )
    r = TestClient(app).get(
        "/api/v1/tasks/TASK-001/recall",
        params={"include_artifact": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    artifact = body.get("artifact")
    contents = "" if not artifact else "".join(
        f.get("content", "") for f in artifact.get("files", [])
    )
    assert "SUPERSECRET" not in contents


def test_events_unknown_task_returns_404(tmp_home, app, auth_headers) -> None:
    """Opening /events for a task the daemon never saw must 404, not hang."""
    r = TestClient(app).get("/api/v1/tasks/TASK-999/events", headers=auth_headers)
    assert r.status_code == 404


def test_resolve_escalation_requires_rationale(tmp_home, app, auth_headers):
    from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
    state = app.state.daemon
    state.db.insert_task(TaskRecord(
        id="TASK-045", type=TaskType.GENERAL, brief="x",
    ))
    state.db.update_task(
        "TASK-045", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/tasks/TASK-045/resolve-escalation",
        json={"decision": "approve", "rationale": ""},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "rationale_required"


def test_events_stream_yields_completion(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Set the task to a terminal status so history_loader synthesizes a
    # task_complete event on subscribe — the stream closes immediately without
    # needing to publish into an empty bus.
    from src.models import TaskStatus
    daemon_state.db.update_task(task_id, status=TaskStatus.COMPLETED)

    with TestClient(app).stream(
        "GET", f"/api/v1/tasks/{task_id}/events", headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert b"task_complete" in body


def test_resolve_escalation_rejects_non_blocked_task(client_with_runtime):
    """Under the new model, the precondition is (status=BLOCKED AND
    block_kind=ESCALATED). A task that is merely BLOCKED(DELEGATED) must 409."""
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.DELEGATED, note="waiting")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_escalated"


def test_resolve_escalation_approve_transitions_to_completed(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halted")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.block_kind is None


def test_resolve_escalation_reject_transitions_to_failed(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halted")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "reject", "rationale": "nope"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.block_kind is None


def test_resolve_escalation_enqueues_parent_if_waiting(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    state.db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.DELEGATED, note="waiting")
    state.db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c", parent_task_id="T-PAR"))
    state.db.update_task("T-CHD", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halt")

    # Drain queue before the request so we only see post-resolve puts.
    while not state.queue._queue.empty():
        state.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/tasks/T-CHD/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    # Parent now enqueued
    assert state.queue._queue.get_nowait() == "T-PAR"
