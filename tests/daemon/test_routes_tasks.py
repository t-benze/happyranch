from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_submit_task_returns_id(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "test"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["task_id"].startswith("TASK-")


def test_submit_task_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_list_tasks_returns_list(tmp_home, app, auth_headers) -> None:
    TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["tasks"]
    assert len(items) >= 1


def test_list_tasks_filter_by_assigned_agent(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """?assigned_agent= filters the inbox so the agent detail drawer can
    render an agent-scoped recent-task list."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord

    org_state.db.insert_task(TaskRecord(
        id="TASK-A", brief="alpha", team="engineering",
        assigned_agent="engineering_head",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-B", brief="bravo", team="content",
        assigned_agent="content_manager",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?assigned_agent=engineering_head",
        headers=auth_headers,
    )
    assert r.status_code == 200
    ids = [t["task_id"] for t in r.json()["tasks"]]
    assert ids == ["TASK-A"]


def test_list_tasks_filter_by_status_and_block_kind(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Path B: escalated and in_progress(delegated) are separate stored statuses.
    Query each with its respective filter."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus, BlockKind

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="TASK-ESC", brief="esc", team="engineering", assigned_agent="dev_agent",
        status=TaskStatus.ESCALATED, block_kind=None,
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-DEL", brief="del", team="engineering", assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="TASK-OK", brief="ok", team="engineering", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, created_at=now, updated_at=now,
    ))

    # Path B: escalated is a top-level status. Query by status=escalated.
    escalated = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?status=escalated", headers=auth_headers,
    ).json()
    assert {t["task_id"] for t in escalated["tasks"]} == {"TASK-ESC"}

    # Path B: delegated tasks are in_progress + block_kind=delegated.
    delegated = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?status=in_progress&block_kind=delegated", headers=auth_headers,
    ).json()
    assert {t["task_id"] for t in delegated["tasks"]} == {"TASK-DEL"}


def test_list_tasks_cursor_pagination(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """`?before=<task_id>` returns the page strictly older than that task,
    and `next_cursor` is null when the page is not full."""
    from datetime import datetime, timezone, timedelta
    from runtime.models import TaskRecord

    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, name in enumerate(["TASK-P1", "TASK-P2", "TASK-P3"]):
        org_state.db.insert_task(TaskRecord(
            id=name, brief=name, team="engineering",
            assigned_agent="engineering_head",
            created_at=base + timedelta(seconds=i),
            updated_at=base + timedelta(seconds=i),
        ))

    page1 = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?limit=2", headers=auth_headers,
    ).json()
    assert [t["task_id"] for t in page1["tasks"]] == ["TASK-P3", "TASK-P2"]
    assert page1["next_cursor"] == "TASK-P2"

    page2 = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?limit=2&before=TASK-P2", headers=auth_headers,
    ).json()
    assert [t["task_id"] for t in page2["tasks"]] == ["TASK-P1"]
    assert page2["next_cursor"] is None


def test_list_tasks_cursor_missing_id_returns_empty(
    tmp_home, app, auth_headers,
) -> None:
    """An unknown `before` is a terminating signal, not an error — the
    cursor likely points at a task that was deleted between pages."""
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks?before=TASK-NEVER", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"tasks": [], "next_cursor": None}


def test_get_task_detail_404_when_missing(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-999", headers=auth_headers)
    assert r.status_code == 404


def test_submit_task_unknown_team_returns_400(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"team": "garbage", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_team"


def test_completion_requires_session_id(tmp_home, app, auth_headers) -> None:
    # Create a task first
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"agent": "dev_agent", "status": "completed", "confidence": 90,
              "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # missing session_id


def test_completion_session_mismatch_409(tmp_home, app, daemon_state, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Mark a different session_id as active.
    org_state.sessions.set_active(task_id, "dev_agent", "sess-real")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"session_id": "sess-stale", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_completion_unknown_session_409(tmp_home, app, daemon_state, org_state, auth_headers) -> None:
    """If the daemon never registered a session for (task, agent), reject —
    do not silently persist a fabricated completion."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    # Note: no set_active() call — tracker is empty for (task_id, dev_agent).

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"session_id": "fabricated", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    # And nothing was persisted.
    assert org_state.db.get_task_results(task_id) == []


def test_completion_persists_when_session_matches(tmp_home, app, daemon_state, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = org_state.db.get_task_results(task_id)
    assert any(r["session_id"] == "sess-1" for r in rows)


def test_completion_callback_plus_audit_logger_does_not_duplicate_row(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Regression for TASK-137: every task ended up with two task_results rows
    per agent step. The agent callback wrote the row (via insert_task_result),
    and then the orchestrator's post-processing called AuditLogger.log_completion_report
    which silently re-wrote the same row. Both writes are real and converge at
    `task_results`. The fix: log_completion_report only audits — the callback
    is the single source of truth."""
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import CompletionReport

    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    callback = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-1", "agent": "dev_agent",
            "status": "completed", "confidence": 90, "output_summary": "ok",
        },
        headers=auth_headers,
    )
    assert callback.status_code == 200
    assert len(org_state.db.get_task_results(task_id)) == 1

    # Replay what the orchestrator does after the subprocess returns.
    AuditLogger(org_state.db).log_completion_report(
        CompletionReport(
            task_id=task_id, agent="dev_agent", status="completed",
            confidence=90, output_summary="ok",
        )
    )
    assert len(org_state.db.get_task_results(task_id)) == 1


def test_completion_clears_session_so_duplicate_rejected(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """After a successful completion POST, the tracker must be cleared so that a
    second POST with the same session id is rejected as unknown_session rather
    than silently persisting a duplicate row."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    payload = {
        "session_id": "sess-1", "agent": "dev_agent",
        "status": "completed", "confidence": 90, "output_summary": "ok",
    }
    first = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert first.status_code == 200

    second = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "unknown_session"
    # And the second POST did not persist a duplicate row.
    rows = org_state.db.get_task_results(task_id)
    assert len([r for r in rows if r["session_id"] == "sess-1"]) == 1


def test_completion_preserves_empty_risks_flagged(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """An empty risks_flagged list submitted by the agent must round-trip as an
    empty list, not be coerced to NULL/None by the DB layer."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok",
              "risks_flagged": []},
        headers=auth_headers,
    )
    assert r.status_code == 200
    latest = org_state.db.get_latest_task_result(task_id, "dev_agent", "sess-1")
    assert latest is not None
    assert latest["risks_flagged"] == []


def test_completion_persists_output_dir(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-a")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-a", "agent": "dev_agent",
            "status": "completed", "confidence": 80,
            "output_summary": "Wrote Q1 report",
            "output_dir": f"output/{task_id}",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = org_state.db.get_task_results(task_id)
    assert rows[-1]["output_dir"] == f"output/{task_id}"


def test_completion_persists_decision_json_for_engineering_head(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """EH's structured decision must land on task_results.decision_json as a
    serialized JSON string so the orchestrator can rehydrate it into
    report.decision when run_step reads it back."""
    import json as _json

    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "engineering_head", "sess-eh")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-eh", "agent": "engineering_head",
            "status": "completed", "confidence": 95,
            "output_summary": "Triaged and delegated.",
            "decision": {
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Implement feature X",
            },
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = org_state.db.get_task_results(task_id)
    stored = rows[-1]["decision_json"]
    assert stored is not None
    assert _json.loads(stored) == {
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "Implement feature X",
    }


def test_completion_leaves_decision_json_null_when_omitted(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Workers don't set `decision`. The daemon must store NULL, not an
    empty-object string — the parser distinguishes 'no decision field' from
    'malformed decision', and a persisted empty object would be the latter."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-a")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-a", "agent": "dev_agent",
            "status": "completed", "confidence": 80,
            "output_summary": "Done",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = org_state.db.get_task_results(task_id)
    assert rows[-1]["decision_json"] is None


def test_completion_rejects_cancelled_task(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Guard A: a completion arriving after /cancel stamped cancelled_at must
    be rejected with `task_not_active` BEFORE the session-tracker check, so the
    `delegate` decision it might carry never reaches insert_task_result.

    Mirrors the validation order in src/daemon/routes/scripts.py:64-90.
    """
    from datetime import datetime, timezone
    from runtime.models import TaskStatus

    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # The session was active (agent registered) before cancel landed.
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    # Simulate /cancel's Phase 1: status=FAILED + cancelled_at stamped + founder note.
    now = datetime.now(timezone.utc).isoformat()
    org_state.db.update_task(
        task_id,
        status=TaskStatus.FAILED,
        block_kind=None,
        note="cancelled by founder: stop",
        cancelled_at=now,
        completed_at=now,
    )
    # Phase 2 has NOT yet cleared the tracker — this models the exact race
    # window where a late HTTP POST can still find the session active.

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-1", "agent": "dev_agent",
            "status": "completed", "confidence": 90, "output_summary": "ok",
            "decision": {"action": "delegate", "agent": "worker", "prompt": "do it"},
        },
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_active"
    assert r.json()["detail"]["cancelled"] is True
    # Critical: the delegate decision must not have been persisted.
    assert org_state.db.get_task_results(task_id) == []


def test_completion_rejects_already_completed_task(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Guard A: terminal-status check fires even without cancelled_at (e.g., a
    completed task receiving a duplicate callback). 409 task_not_active."""
    from runtime.models import TaskStatus

    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")
    org_state.db.update_task(task_id, status=TaskStatus.COMPLETED)

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": "sess-1", "agent": "dev_agent",
            "status": "completed", "confidence": 90, "output_summary": "late",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_active"
    assert r.json()["detail"]["cancelled"] is False


def test_completion_rejects_unknown_task_404(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Guard A: existence check fires before session check. Matches the
    `unknown_task` shape in src/daemon/routes/scripts.py:69-74."""
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-999/completion",
        json={
            "session_id": "sess-x", "agent": "dev_agent",
            "status": "completed", "confidence": 90, "output_summary": "ok",
        },
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_progress_rejects_cancelled_task(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Guard A applied to /progress for symmetry. A late progress beat from a
    cancelled session is noise + symptomatic of the same race."""
    from datetime import datetime, timezone
    from runtime.models import TaskStatus

    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    now = datetime.now(timezone.utc).isoformat()
    org_state.db.update_task(
        task_id, status=TaskStatus.FAILED, cancelled_at=now, completed_at=now,
    )

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-1", "agent": "dev_agent", "message": "still working"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_active"


def test_recall_returns_task_payload(tmp_home, app, daemon_state, org_state, auth_headers) -> None:
    from runtime.models import TaskRecord, TaskStatus
    org_state.db.insert_task(
        TaskRecord(id="TASK-001", brief="Review Q1")
    )
    org_state.db.update_task(
        "TASK-001",
        status=TaskStatus.COMPLETED,
        note="Report delivered",
        final_output_dir="output/TASK-001",
    )
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-001/recall", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "TASK-001"
    assert body["output_summary"] == "Report delivered"
    assert body["output_dir"] == "output/TASK-001"
    assert body["children"] == []


def test_recall_missing_task_returns_404(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-404/recall", headers=auth_headers)
    assert r.status_code == 404


def test_recall_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/orgs/alpha/tasks/TASK-001/recall", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_recall_payload_includes_revisit_of_task_id(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import TaskRecord
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-002/recall", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["revisit_of_task_id"] == "TASK-001"

    # Non-revisit: NULL round-trips as null, not missing key.
    r2 = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-001/recall", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["revisit_of_task_id"] is None


def test_recall_tree_includes_descendants(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import TaskRecord
    org_state.db.insert_task(
        TaskRecord(id="TASK-001", brief="root")
    )
    org_state.db.insert_task(TaskRecord(
        id="TASK-002", brief="child",
        parent_task_id="TASK-001",
    ))
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall",
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


def test_recall_include_output_reads_files(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import TaskRecord
    ws = org_state.root / "workspaces" / "dev_agent"
    output = ws / "output" / "TASK-001"
    output.mkdir(parents=True)
    (output / "report.md").write_text("# Q1 report\n\nAll good.")
    org_state.db.insert_task(TaskRecord(
        id="TASK-001", brief="b",
        assigned_agent="dev_agent",
    ))
    org_state.db.update_task(
        "TASK-001", final_output_dir="output/TASK-001",
    )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall",
        params={"include_output": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["output"] == {
        "files": [{"path": "report.md", "content": "# Q1 report\n\nAll good."}],
        "truncated": False,
    }


def test_recall_rejects_absolute_output_path(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """output_dir comes from an agent-supplied completion payload. A buggy or
    malicious agent that stores an absolute path must not be able to read
    arbitrary files on the host via /recall?include_output=true."""
    from runtime.models import TaskRecord
    secret = tmp_home / "secret.txt"
    secret.write_text("DO NOT LEAK")
    org_state.db.insert_task(TaskRecord(
        id="TASK-001", brief="b",
        assigned_agent="dev_agent",
    ))
    org_state.db.update_task(
        "TASK-001", final_output_dir=str(tmp_home),
    )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall",
        params={"include_output": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    # Must not expose files from outside the assigned agent's workspace.
    # Either the endpoint returns no output payload at all, or an empty one —
    # but it must never contain secret.txt.
    output = body.get("output")
    contents = "" if not output else "".join(
        f.get("content", "") for f in output.get("files", [])
    )
    assert "DO NOT LEAK" not in contents


def test_recall_rejects_parent_traversal_output_path(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """A `..` in output_dir must not let an agent read another agent's
    workspace."""
    from runtime.models import TaskRecord
    workspaces = org_state.root / "workspaces"
    # dev_agent workspace must exist so `dev_agent/..` can resolve through it.
    (workspaces / "dev_agent").mkdir(parents=True)
    other = workspaces / "other_agent" / "secrets"
    other.mkdir(parents=True)
    (other / "token.txt").write_text("SUPERSECRET")
    org_state.db.insert_task(TaskRecord(
        id="TASK-001", brief="b",
        assigned_agent="dev_agent",
    ))
    org_state.db.update_task(
        "TASK-001", final_output_dir="../other_agent/secrets",
    )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall",
        params={"include_output": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    output = body.get("output")
    contents = "" if not output else "".join(
        f.get("content", "") for f in output.get("files", [])
    )
    assert "SUPERSECRET" not in contents


def test_events_unknown_task_returns_404(tmp_home, app, auth_headers) -> None:
    """Opening /events for a task the daemon never saw must 404, not hang."""
    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-999/events", headers=auth_headers)
    assert r.status_code == 404


def test_resolve_escalation_requires_rationale(tmp_home, app, org_state, auth_headers):
    from runtime.models import BlockKind, TaskRecord, TaskStatus
    org_state.db.insert_task(TaskRecord(
        id="TASK-045", brief="x",
    ))
    org_state.db.update_task(
        "TASK-045", status=TaskStatus.ESCALATED, block_kind=None,
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/tasks/TASK-045/resolve-escalation",
        json={"decision": "approve", "rationale": ""},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "rationale_required"


def test_events_stream_yields_completion(tmp_home, app, daemon_state, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Set the task to a terminal status so history_loader synthesizes a
    # task_complete event on subscribe — the stream closes immediately without
    # needing to publish into an empty bus.
    from runtime.models import TaskStatus
    org_state.db.update_task(task_id, status=TaskStatus.COMPLETED)

    with TestClient(app).stream(
        "GET", f"/api/v1/orgs/alpha/tasks/{task_id}/events", headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert b"task_complete" in body


def test_resolve_escalation_rejects_non_escalated_task(client_with_runtime):
    """Under the Phase 3 model, the precondition is status=ESCALATED.
    A task that is merely in_progress(delegated) must 409."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))
    state.db.update_task("T-1", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_escalated"


def test_resolve_escalation_approve_resumes_task(client_with_runtime):
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))
    state.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None, note="halted")
    daemon = client.app.state.daemon
    while not daemon.queue._queue.empty():
        daemon.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "pending"
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.PENDING
    assert t.block_kind is None
    # Self re-enqueued so the manager picks it up next; queue carries
    # (slug, task_id) tuples in the multi-org layout.
    assert daemon.queue._queue.get_nowait() == ("alpha", "T-1", None)


def test_resolve_escalation_approve_target_is_root(client_with_runtime):
    """THR-033 Change A: escalation is root-only (run_step's three escalation
    sites all gate on is_root; non-roots fail and route to their parent). So a
    resolve-escalation approve ALWAYS lands on a root, where re-enqueue →
    fresh decision step (re-author) is the correct disposition. Locks the
    invariant that the approve target is structurally a root."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-ROOT", brief="x"))
    state.db.update_task("T-ROOT", status=TaskStatus.ESCALATED, block_kind=None, note="halted")
    # Precondition: an escalated task is structurally a root.
    assert state.db.get_task("T-ROOT").parent_task_id is None
    daemon = client.app.state.daemon
    while not daemon.queue._queue.empty():
        daemon.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-ROOT/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-ROOT")
    assert t.status == TaskStatus.PENDING
    assert t.parent_task_id is None  # approve target stays a root
    assert daemon.queue._queue.get_nowait() == ("alpha", "T-ROOT", None)


def test_resolve_escalation_reject_transitions_to_failed(client_with_runtime):
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))
    state.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None, note="halted")

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/resolve-escalation",
        json={"decision": "reject", "rationale": "nope"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.block_kind is None


def test_resolve_escalation_overwrites_note_with_rationale(client_with_runtime):
    """P2 regression: _build_prior_steps_from_db surfaces child.note as the
    result summary shown to a resumed parent EH. After founder resolution the
    note must reflect the disposition/rationale, not the stale escalation
    reason the task parked with."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))
    state.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None,
                         note="Original escalation reason")

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "proceed with caveats"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.PENDING
    assert t.note and "proceed with caveats" in t.note
    assert "Original escalation reason" not in (t.note or "")


def test_resolve_escalation_approve_reenqueues_child_not_parent(client_with_runtime):
    """Approve resumes the child itself; parent stays in_progress(delegated) and
    will be woken later when the child reaches a true terminal."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-PAR", brief="p"))
    state.db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    state.db.insert_task(TaskRecord(
        id="T-CHD", brief="c", parent_task_id="T-PAR"))
    state.db.update_task("T-CHD", status=TaskStatus.ESCALATED, block_kind=None, note="halt")

    # The global queue lives on the DaemonState, not the OrgState; items are
    # (slug, task_id) tuples in the multi-org layout.
    daemon = client.app.state.daemon
    # Drain queue before the request so we only see post-resolve puts.
    while not daemon.queue._queue.empty():
        daemon.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CHD/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    # Approve re-enqueues the child itself (resumes the work). Parent stays
    # in_progress(delegated) and will be woken when the child next reaches a
    # true terminal — no immediate parent wake here.
    assert daemon.queue._queue.get_nowait() == ("alpha", "T-CHD", None)
    assert daemon.queue._queue.empty()
    par = state.db.get_task("T-PAR")
    assert par.status == TaskStatus.IN_PROGRESS
    assert par.block_kind == BlockKind.DELEGATED


def test_resolve_escalation_reject_cascades_to_parent(client_with_runtime):
    """Reject on a child fails it and wakes the parent via bounded
    failure-recovery (TASK-573) — parent stays in_progress(delegated) for
    a bounded-wake decision step, NOT cascade-failed."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-PAR", brief="p", task_type="task"))
    state.db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    state.db.insert_task(TaskRecord(
        id="T-CHD", brief="c", parent_task_id="T-PAR", task_type="subtask"))
    state.db.update_task("T-CHD", status=TaskStatus.ESCALATED, block_kind=None, note="halt")

    daemon = client.app.state.daemon
    while not daemon.queue._queue.empty():
        daemon.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CHD/resolve-escalation",
        json={"decision": "reject", "rationale": "no"},
    )
    assert r.status_code == 200
    chd = state.db.get_task("T-CHD")
    assert chd.status == TaskStatus.FAILED
    par = state.db.get_task("T-PAR")
    # TASK-573: bounded-wake, not cascade-fail.
    assert par.status == TaskStatus.IN_PROGRESS
    assert par.block_kind == BlockKind.DELEGATED


# -------- /tasks/{id}/cancel --------


def test_cancel_404_when_task_missing(client_with_runtime):
    client, _state = client_with_runtime
    r = client.post("/api/v1/orgs/alpha/tasks/TASK-404/cancel", json={"rationale": ""})
    assert r.status_code == 404


def test_cancel_409_when_already_terminal(client_with_runtime):
    from runtime.models import TaskRecord, TaskStatus
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-DONE", brief="x"))
    state.db.update_task("T-DONE", status=TaskStatus.COMPLETED, note="ok")

    r = client.post("/api/v1/orgs/alpha/tasks/T-DONE/cancel", json={"rationale": "too late"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_already_terminal"


def test_cancel_marks_task_cancelled_with_cancelled_at_and_note(client_with_runtime):
    from runtime.models import TaskRecord, TaskStatus
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/cancel", json={"rationale": "rerouting"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] == ["T-1"]
    # No subprocess was attached so nothing to SIGTERM.
    assert body["killed"] == []

    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.CANCELLED  # Path B: dedicated terminal status
    assert t.cancelled_at is not None
    assert t.completed_at is not None
    assert t.note == "cancelled by founder: rerouting"


def test_cancel_cascades_down_subtree(client_with_runtime):
    """Default cascade=True must cancel every non-terminal descendant and
    leave already-terminal siblings untouched."""
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-P", brief="parent"))
    state.db.update_task(
        "T-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    )
    state.db.insert_task(TaskRecord(
        id="T-C1", brief="running",
        parent_task_id="T-P",
    ))
    # Sibling finished long ago — must not be touched.
    state.db.insert_task(TaskRecord(
        id="T-C2", brief="done",
        parent_task_id="T-P",
    ))
    state.db.update_task("T-C2", status=TaskStatus.COMPLETED, note="already done")
    # Grandchild under the running branch — should also be cancelled.
    state.db.insert_task(TaskRecord(
        id="T-G", brief="grand",
        parent_task_id="T-C1",
    ))

    r = client.post("/api/v1/orgs/alpha/tasks/T-P/cancel", json={"rationale": "abort"})
    assert r.status_code == 200
    cancelled = set(r.json()["cancelled"])
    assert cancelled == {"T-P", "T-C1", "T-G"}

    assert state.db.get_task("T-P").status == TaskStatus.CANCELLED
    assert state.db.get_task("T-C1").status == TaskStatus.CANCELLED
    assert state.db.get_task("T-G").status == TaskStatus.CANCELLED
    # Sibling that was already terminal is untouched.
    t_c2 = state.db.get_task("T-C2")
    assert t_c2.status == TaskStatus.COMPLETED
    assert t_c2.cancelled_at is None
    assert t_c2.note == "already done"


def test_cancel_no_cascade_cancels_only_target(client_with_runtime):
    from runtime.models import TaskRecord, TaskStatus
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-P", brief="parent"))
    state.db.insert_task(TaskRecord(
        id="T-C", brief="child",
        parent_task_id="T-P",
    ))

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-P/cancel",
        json={"rationale": "", "cascade": False},
    )
    assert r.status_code == 200
    assert r.json()["cancelled"] == ["T-P"]
    assert state.db.get_task("T-P").status == TaskStatus.CANCELLED
    # Child must NOT be cancelled.
    assert state.db.get_task("T-C").status == TaskStatus.PENDING


def test_cancel_sigterms_live_pids_and_returns_them(client_with_runtime, monkeypatch):
    """The SessionTracker's pid half is the /cancel → SIGTERM bridge. Pin
    that the route reads from iter_task_pids, calls os.kill(pid, SIGTERM),
    and clears the tracker entry on the way out."""
    import signal as _signal
    from runtime.daemon.routes import tasks as tasks_route
    from runtime.models import TaskRecord

    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))
    state.sessions.set_active("T-1", "dev_agent", "sess-1")
    state.sessions.set_pid("T-1", "dev_agent", 99999)

    kills: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        kills.append((pid, sig))

    monkeypatch.setattr(tasks_route.os, "kill", fake_kill)

    r = client.post("/api/v1/orgs/alpha/tasks/T-1/cancel", json={"rationale": ""})
    assert r.status_code == 200
    body = r.json()
    assert kills == [(99999, _signal.SIGTERM)]
    assert body["killed"] == [
        {"task_id": "T-1", "agent": "dev_agent", "pid": 99999}
    ]
    # Tracker cleared so a late completion callback is rejected as unknown_session.
    assert state.sessions.get_active("T-1", "dev_agent") is None
    assert state.sessions.get_pid("T-1", "dev_agent") is None


def test_cancel_records_audit_entry(client_with_runtime):
    from runtime.models import TaskRecord
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", brief="x"))

    r = client.post("/api/v1/orgs/alpha/tasks/T-1/cancel", json={"rationale": "wrong path"})
    assert r.status_code == 200
    entries = state.db.get_audit_logs("T-1")
    cancelled = [e for e in entries if e["action"] == "task_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["agent"] == "founder"
    assert cancelled[0]["payload"]["rationale"] == "wrong path"
    assert cancelled[0]["payload"]["cascade"] is True


def test_revisit_creates_new_root_from_failed_predecessor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Revisit a failed root: new root inherits brief/task_type, both audit
    entries are written, predecessor row stays exactly as it was."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", brief="Add Alipay support",
    ))
    db.update_task(
        "TASK-052",
        status=TaskStatus.FAILED,
        note="delegated child TASK-058 failed: rc=1",
        completed_at="2026-04-21T00:00:00+00:00",
    )
    pre_snapshot = db.get_task("TASK-052")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"founder_note": "PR #103 already merged"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    new_id = body["new_root_task_id"]
    assert new_id.startswith("TASK-")
    assert body["predecessor_root_task_id"] == "TASK-052"
    assert body["flagged_task_id"] == "TASK-052"
    assert body["cascade"] == ["TASK-052"]
    assert body["predecessor_status"] == "failed"

    # New root row
    new_root = db.get_task(new_id)
    assert new_root is not None
    assert new_root.parent_task_id is None
    assert new_root.status == TaskStatus.PENDING
    assert new_root.brief == "Add Alipay support"
    assert new_root.orchestration_step_count == 0
    assert new_root.cancelled_at is None

    # revisit_of on new root
    new_logs = db.get_audit_logs(new_id)
    revisit_of = next(e for e in new_logs if e["action"] == "revisit_of")
    assert revisit_of["payload"]["predecessor_root"] == "TASK-052"
    assert revisit_of["payload"]["prior_status"] == "failed"
    assert revisit_of["payload"]["founder_note"] == "PR #103 already merged"

    # revisit_spawned on predecessor
    pre_logs = db.get_audit_logs("TASK-052")
    spawned = next(e for e in pre_logs if e["action"] == "revisit_spawned")
    assert spawned["payload"]["new_root"] == new_id

    # Predecessor otherwise untouched
    post_snapshot = db.get_task("TASK-052")
    assert post_snapshot.status == pre_snapshot.status
    assert post_snapshot.note == pre_snapshot.note
    assert post_snapshot.completed_at == pre_snapshot.completed_at
    assert post_snapshot.cancelled_at == pre_snapshot.cancelled_at
    assert post_snapshot.orchestration_step_count == pre_snapshot.orchestration_step_count


def test_revisit_walks_cascade_to_root(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Flag a leaf; endpoint walks parent_task_id to the predecessor root."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-053", brief="mid", parent_task_id="TASK-052",
    ))
    db.insert_task(TaskRecord(
        id="TASK-058", brief="leaf", parent_task_id="TASK-053",
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="cascade")
    db.update_task("TASK-053", status=TaskStatus.FAILED, note="child failed")
    db.update_task("TASK-058", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-058/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predecessor_root_task_id"] == "TASK-052"
    assert body["flagged_task_id"] == "TASK-058"
    assert body["cascade"] == ["TASK-052", "TASK-053", "TASK-058"]


def test_revisit_handles_cancelled_predecessor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    db.update_task(
        "TASK-052",
        status=TaskStatus.FAILED,
        note="cancelled by founder: stuck",
        cancelled_at="2026-04-21T00:00:00+00:00",
    )
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["predecessor_status"] == "failed-cancelled"


def test_revisit_handles_escalated_predecessor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import BlockKind, TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    db.update_task(
        "TASK-052",
        status=TaskStatus.ESCALATED, block_kind=None,
        note="halted",
    )
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={"founder_note": "ruled"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predecessor_status"] == "blocked-escalated"
    # THR-018 tier #3 §3a: revisit now auto-resolves a blocked(escalated)
    # predecessor to the terminal RESOLVED_SUPERSEDED, citing the continuation.
    pre = db.get_task("TASK-052")
    assert pre.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pre.block_kind is None
    payload = next(
        e["payload"] for e in db.get_audit_logs("TASK-052")
        if e["action"] == "escalation_superseded"
    )
    assert payload["successor_root"] == body["new_root_task_id"]
    assert payload["prior_block_kind"] == "escalated"


def test_revisit_handles_completed_predecessor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    db.update_task("TASK-052", status=TaskStatus.COMPLETED, note="ok")
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["predecessor_status"] == "completed"


def test_revisit_missing_task_returns_404(
    tmp_home, app, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-NOPE/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "status,block_kind,note",
    [
        ("in_progress", None, "working"),
        ("pending", None, None),
        ("in_progress", "delegated", "Delegated to dev_agent (child=TASK-053)"),
    ],
)
def test_revisit_rejects_ineligible_predecessor(
    tmp_home, app, daemon_state, org_state, auth_headers, status, block_kind, note,
) -> None:
    """Revisit must reject predecessors whose history isn't final yet."""
    from runtime.models import BlockKind, TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    bk = BlockKind(block_kind) if block_kind else None
    db.update_task(
        "TASK-052",
        status=TaskStatus(status),
        block_kind=bk,
        note=note,
    )
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "cannot_revisit"
    assert detail["predecessor_root_task_id"] == "TASK-052"
    assert detail["predecessor_status"] == status
    # No new task row was created.
    assert len(db.list_tasks()) == 1


def test_revisit_lineage_too_deep_returns_500(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """A 21-hop ancestor chain is pathological; the endpoint guards with 500."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-000", brief="root"))
    db.update_task("TASK-000", status=TaskStatus.FAILED)
    prev = "TASK-000"
    for i in range(1, 25):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, brief=f"t{i}", parent_task_id=prev,
        ))
        db.update_task(tid, status=TaskStatus.FAILED)
        prev = tid
    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{prev}/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "lineage_too_deep"


def test_revisit_concurrent_on_same_predecessor_both_succeed(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Two sequential POSTs against the same failed predecessor both succeed;
    predecessor ends with two revisit_spawned audit entries."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    db.update_task("TASK-052", status=TaskStatus.FAILED)

    client = TestClient(app)
    r1 = client.post("/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    r2 = client.post("/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = r1.json()["new_root_task_id"]
    id2 = r2.json()["new_root_task_id"]
    assert id1 != id2

    spawned = [
        e for e in db.get_audit_logs("TASK-052") if e["action"] == "revisit_spawned"
    ]
    assert sorted(e["payload"]["new_root"] for e in spawned) == sorted([id1, id2])


def test_revisit_a_revisit_chain_of_chains(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """TASK-P → TASK-N (via revisit) → TASK-N' (revisit of TASK-N)."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="x"))
    db.update_task("TASK-052", status=TaskStatus.FAILED)
    client = TestClient(app)
    r1 = client.post("/api/v1/orgs/alpha/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    id_n = r1.json()["new_root_task_id"]
    # Mark the new root as failed so it's revisit-eligible.
    db.update_task(id_n, status=TaskStatus.FAILED, note="also failed")
    r2 = client.post(f"/api/v1/orgs/alpha/tasks/{id_n}/revisit", json={}, headers=auth_headers)
    id_n2 = r2.json()["new_root_task_id"]

    assert id_n != id_n2
    # Second revisit's revisit_of points at id_n, not the original TASK-052.
    logs_n2 = db.get_audit_logs(id_n2)
    ro = next(e for e in logs_n2 if e["action"] == "revisit_of")
    assert ro["payload"]["predecessor_root"] == id_n


def test_revisit_writes_revisit_of_task_id_on_new_root(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """The new root's revisit_of_task_id column must equal the predecessor
    root's id. This is what makes the link queryable without audit-log scans."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", brief="Add Alipay support",
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"founder_note": None},
        headers=auth_headers,
    )
    assert r.status_code == 200
    new_id = r.json()["new_root_task_id"]
    new_root = db.get_task(new_id)
    assert new_root.revisit_of_task_id == "TASK-052"


def test_revisit_persists_session_timeout_seconds_from_payload(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Founder passes --session-timeout-seconds; the value is persisted on the
    new root verbatim and shadows the predecessor's value."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", brief="b", session_timeout_seconds=600,
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"session_timeout_seconds": 7200},
        headers=auth_headers,
    )
    assert r.status_code == 200
    new_id = r.json()["new_root_task_id"]
    new_root = db.get_task(new_id)
    assert new_root.session_timeout_seconds == 7200


def test_revisit_inherits_session_timeout_from_predecessor_when_omitted(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Omitted payload → new root inherits the predecessor's value, so a
    second revisit of an already-bumped task keeps the bump."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", brief="b", session_timeout_seconds=5400,
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 200
    new_id = r.json()["new_root_task_id"]
    new_root = db.get_task(new_id)
    assert new_root.session_timeout_seconds == 5400


def test_revisit_rejects_non_positive_session_timeout(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """0 / negative / non-int values are 422 from the pydantic validator."""
    from runtime.models import TaskRecord, TaskStatus
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-052", brief="b"))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"session_timeout_seconds": 0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_plain_run_leaves_revisit_of_task_id_null(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Plain /tasks POST (no revisit) must not set the column."""
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        json={"brief": "plain task"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    tid = r.json()["task_id"]
    row = org_state.db.get_task(tid)
    assert row.revisit_of_task_id is None


def test_get_task_includes_revisit_chain_and_direct_revisits(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """GET /tasks/{id} must surface the full revisit context for the CLI."""
    from runtime.models import TaskRecord
    db = org_state.db
    db.insert_task(TaskRecord(id="TASK-001", brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", brief="another revisit of P",
        revisit_of_task_id="TASK-001",
    ))
    # prior_status comes from the revisit_of audit entry on TASK-002.
    db.insert_audit_log(
        "TASK-002", "founder", "revisit_of",
        {"predecessor_root": "TASK-001", "flagged": "TASK-001",
         "cascade": ["TASK-001"], "prior_status": "failed-cancelled",
         "founder_note": None},
    )

    r = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-002", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # Chain: [task, predecessor, ...]
    assert body["revisit_chain"] == ["TASK-002", "TASK-001"]
    # prior_status pulled from audit entry
    assert body["predecessor_prior_status"] == "failed-cancelled"
    # Direct revisits of THIS task (not its predecessor) — should be empty.
    assert body["direct_revisits"] == []

    r2 = TestClient(app).get("/api/v1/orgs/alpha/tasks/TASK-001", headers=auth_headers)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["revisit_chain"] == ["TASK-001"]
    assert body2["predecessor_prior_status"] is None
    assert set(body2["direct_revisits"]) == {"TASK-002", "TASK-003"}


def test_get_task_does_not_crash_on_long_revisit_chain(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Regression: revisit history grows naturally; GET /tasks/{id} must not
    500 once the chain exceeds walk_revisit_chain's defensive max_hops. The
    route opts into truncation so the response stays usable even at depth.
    """
    from runtime.models import TaskRecord
    db = org_state.db
    # Build a chain 25 deep — well past the default max_hops=20.
    db.insert_task(TaskRecord(id="TASK-000", brief="orig"))
    prev = "TASK-000"
    for i in range(1, 26):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, brief=f"t{i}",
            revisit_of_task_id=prev,
        ))
        prev = tid

    r = TestClient(app).get(f"/api/v1/orgs/alpha/tasks/{prev}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # Chain is truncated to max_hops entries rather than raising.
    assert len(body["revisit_chain"]) == 20
    # Truncation preserves the most-recent end (head of the walk).
    assert body["revisit_chain"][0] == prev


# --- Progress endpoint ---


def test_progress_unknown_session_409(tmp_home, app, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "fabricated", "agent": "dev_agent",
              "message": "phase 1"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"


def test_progress_session_mismatch_409(tmp_home, app, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-real")
    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-stale", "agent": "dev_agent",
              "message": "phase 1"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_progress_persists_audit_entry(tmp_home, app, org_state, auth_headers) -> None:
    """A successful progress POST writes an audit entry with action=progress
    and the supplied message — visible to `happyranch details` / `happyranch audit`."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "message": "Phase 3 of 6: tests passing"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    logs = org_state.db.get_audit_logs(task_id)
    progress_logs = [log for log in logs if log["action"] == "progress"]
    assert len(progress_logs) == 1
    assert progress_logs[0]["agent"] == "dev_agent"
    assert progress_logs[0]["payload"]["message"] == "Phase 3 of 6: tests passing"


def test_progress_does_not_clear_session(tmp_home, app, org_state, auth_headers) -> None:
    """Unlike completion, progress is mid-task — the session must stay live so
    the agent can keep emitting beats and eventually report completion."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r1 = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-1", "agent": "dev_agent", "message": "step 1"},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    # Second beat with the same session must still succeed.
    r2 = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-1", "agent": "dev_agent", "message": "step 2"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-1"


def test_progress_empty_message_rejected(tmp_home, app, org_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    org_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/progress",
        json={"session_id": "sess-1", "agent": "dev_agent", "message": "   "},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "message_required"


# --- GET /tasks/{id} blocked_on_jobs ---


def test_get_task_includes_blocked_on_jobs_when_blocked(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """GET /tasks/{id} includes blocked_on_jobs list with id+status for each
    blocking job when the task is in in_progress(blocked_on_job) state."""
    import json as _json
    from datetime import datetime, timezone
    from runtime.models import BlockKind, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus

    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-BOJ-1",
        brief="waiting on two jobs",
        team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
    ))

    job1_id = db.next_job_id()
    db.insert_job(JobRecord(
        id=job1_id,
        task_id="TASK-BOJ-1",
        agent_name="dev_agent",
        title="job one",
        rationale="need it",
        script_text="echo 1",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.RUNNING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))

    job2_id = db.next_job_id()
    db.insert_job(JobRecord(
        id=job2_id,
        task_id="TASK-BOJ-1",
        agent_name="dev_agent",
        title="job two",
        rationale="need it too",
        script_text="echo 2",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))

    db.update_task(
        "TASK-BOJ-1",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids=_json.dumps([job1_id, job2_id]),
    )

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-BOJ-1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    boj = body.get("blocked_on_jobs")
    assert boj is not None
    assert len(boj) == 2
    by_id = {entry["job_id"]: entry["status"] for entry in boj}
    assert by_id[job1_id] == "running"
    assert by_id[job2_id] == "pending"


def test_get_task_blocked_on_jobs_is_none_for_non_blocked_task(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """blocked_on_jobs is None (not an empty list) for tasks not in
    in_progress(blocked_on_job) state, so callers can distinguish the two cases."""
    from runtime.models import TaskRecord

    org_state.db.insert_task(TaskRecord(
        id="TASK-PLAIN", brief="normal task", team="engineering",
        assigned_agent="dev_agent",
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-PLAIN",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("blocked_on_jobs") is None


# --- GET /tasks/{id} active_chain ---


def test_task_detail_includes_parsed_active_chain(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """When a task has active_chain set, the detail response surfaces it as a
    parsed dict (not a raw JSON string) so the web UI and CLI can render it."""
    import json as _json
    from runtime.models import TaskRecord

    db = org_state.db
    db.insert_task(TaskRecord(
        id="TASK-CHAIN-1", brief="chain task", team="engineering",
        assigned_agent="engineering_head",
    ))
    chain_json = _json.dumps({
        "step_index": 0,
        "first_leg_expect_verdict": "APPROVE",
        "legs": [{"agent": "sr", "prompt": "r", "expect_verdict": "APPROVE"}],
        "step_audit_id": 1,
    })
    db.update_task_active_chain("TASK-CHAIN-1", chain_json)

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-CHAIN-1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "active_chain" in body
    chain = body["active_chain"]
    assert isinstance(chain, dict)
    assert chain["step_index"] == 0
    assert chain["first_leg_expect_verdict"] == "APPROVE"
    assert chain["legs"][0]["agent"] == "sr"
    assert chain["step_audit_id"] == 1


def test_task_detail_active_chain_is_null_when_no_chain(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """Tasks without an active chain return active_chain: null (not missing key)."""
    from runtime.models import TaskRecord

    org_state.db.insert_task(TaskRecord(
        id="TASK-NO-CHAIN", brief="plain task", team="engineering",
        assigned_agent="dev_agent",
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-NO-CHAIN",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "active_chain" in body
    assert body["active_chain"] is None


def test_cancel_records_declared_actor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """When the caller declares an actor, the note and audit log record it
    instead of the hardcoded 'founder'."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "", "cascade": True, "actor": "family_manager"},
        headers=auth_headers,
    )
    assert r.status_code == 200

    assert org_state.db.get_task(task_id).note == "cancelled by family_manager"
    cancel_logs = [
        e for e in org_state.db.get_audit_logs(task_id)
        if e["action"] == "task_cancelled"
    ]
    assert len(cancel_logs) == 1
    assert cancel_logs[0]["agent"] == "family_manager"


def test_cancel_actor_with_rationale(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "superseded", "actor": "family_manager"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert org_state.db.get_task(task_id).note == "cancelled by family_manager: superseded"


def test_cancel_defaults_to_founder(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """No actor supplied → unchanged 'founder' strings (backward compat)."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "", "cascade": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert org_state.db.get_task(task_id).note == "cancelled by founder"
    cancel_logs = [
        e for e in org_state.db.get_audit_logs(task_id)
        if e["action"] == "task_cancelled"
    ]
    assert cancel_logs[0]["agent"] == "founder"


# ---------------------------------------------------------------------------
# GET /tasks/roots — roots-only list with severity rollup
# ---------------------------------------------------------------------------

def test_list_roots_returns_only_root_tasks(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Children (parent_task_id != NULL) are excluded from the list."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-A", brief="root", team="engineering",
        assigned_agent="dev_agent",
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="CHILD-A", brief="child", team="engineering",
        assigned_agent="dev_agent", parent_task_id="ROOT-A",
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots", headers=auth_headers,
    )
    assert r.status_code == 200
    ids = [t["task_id"] for t in r.json()["tasks"]]
    assert "ROOT-A" in ids
    assert "CHILD-A" not in ids


def test_list_roots_includes_severity_rollup_field(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Each root task dict carries a severity_rollup field."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-A", brief="root", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots", headers=auth_headers,
    )
    assert r.status_code == 200
    task = r.json()["tasks"][0]
    assert "severity_rollup" in task
    assert task["severity_rollup"] == "completed"


def test_list_roots_severity_rollup_reflects_escalated_child(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A root with an escalated child shows 'escalated' in severity_rollup
    (Path B: escalated is the worst rollup severity; blocked is retired)."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-A", brief="root", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="CHILD-B", brief="child", team="engineering",
        assigned_agent="dev_agent", parent_task_id="ROOT-A",
        status=TaskStatus.ESCALATED,
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots", headers=auth_headers,
    )
    assert r.status_code == 200
    task = r.json()["tasks"][0]
    assert task["severity_rollup"] == "escalated"


def test_list_roots_supports_status_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Status filter applies to the root itself, rollup still computed."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-A", brief="root", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="ROOT-B", brief="root2", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots?status=in_progress", headers=auth_headers,
    )
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "ROOT-B"


def test_list_roots_supports_agent_filter(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Assigned agent filter on roots."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-A", brief="root", team="engineering",
        assigned_agent="dev_agent",
        created_at=now, updated_at=now,
    ))
    org_state.db.insert_task(TaskRecord(
        id="ROOT-B", brief="root2", team="engineering",
        assigned_agent="qa_engineer",
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots?assigned_agent=dev_agent",
        headers=auth_headers,
    )
    assert r.status_code == 200
    ids = [t["task_id"] for t in r.json()["tasks"]]
    assert ids == ["ROOT-A"]


def test_list_roots_handles_cursor_pagination(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Cursor pagination works the same as list_tasks."""
    from datetime import datetime, timezone, timedelta
    from runtime.models import TaskRecord

    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, name in enumerate(["ROOT-P1", "ROOT-P2", "ROOT-P3"]):
        org_state.db.insert_task(TaskRecord(
            id=name, brief=name, team="engineering",
            assigned_agent="engineering_head",
            created_at=base + timedelta(seconds=i),
            updated_at=base + timedelta(seconds=i),
        ))

    page1 = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots?limit=2", headers=auth_headers,
    ).json()
    assert [t["task_id"] for t in page1["tasks"]] == ["ROOT-P3", "ROOT-P2"]
    assert page1["next_cursor"] == "ROOT-P2"

    page2 = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots?limit=2&before=ROOT-P2",
        headers=auth_headers,
    ).json()
    assert [t["task_id"] for t in page2["tasks"]] == ["ROOT-P1"]
    assert page2["next_cursor"] is None


def test_list_roots_includes_direct_revisits(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Each root task dict carries a direct_revisits field listing tasks
    that revisit this root (backed by revisit_of_task_id)."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-Z", brief="root", team="engineering",
        assigned_agent="dev_agent",
        created_at=now, updated_at=now,
    ))
    # Insert a revisit that points back at ROOT-Z
    org_state.db.insert_task(TaskRecord(
        id="REVISIT-Z", brief="revisit", team="engineering",
        assigned_agent="dev_agent", revisit_of_task_id="ROOT-Z",
        created_at=now, updated_at=now,
    ))
    # Insert a SECOND revisit — both should appear
    org_state.db.insert_task(TaskRecord(
        id="REVISIT-Z2", brief="revisit2", team="engineering",
        assigned_agent="dev_agent", revisit_of_task_id="ROOT-Z",
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots", headers=auth_headers,
    )
    assert r.status_code == 200
    task = r.json()["tasks"][0]
    assert task["task_id"] == "ROOT-Z"
    assert "direct_revisits" in task
    assert isinstance(task["direct_revisits"], list)
    assert set(task["direct_revisits"]) == {"REVISIT-Z", "REVISIT-Z2"}


def test_list_roots_direct_revisits_empty_when_no_revisits(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A root with no revisits gets an empty direct_revisits list."""
    from datetime import datetime, timezone
    from runtime.models import TaskRecord

    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="ROOT-NO-REV", brief="root without revisits", team="engineering",
        assigned_agent="dev_agent",
        created_at=now, updated_at=now,
    ))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/roots", headers=auth_headers,
    )
    assert r.status_code == 200
    task = r.json()["tasks"][0]
    assert "direct_revisits" in task
    assert task["direct_revisits"] == []
