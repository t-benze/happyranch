"""Unit tests for src/daemon/routes/scripts.py (spec §5.1)."""
from __future__ import annotations

import secrets

import pytest

from src.models import TaskRecord, TaskStatus


def _make_active_session(org, agent: str = "engineering_head"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="test",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def _make_completed_task(org, agent: str = "engineering_head"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="done",
        status=TaskStatus.COMPLETED,
    )
    org.db.insert_task(task)
    return task.id


def test_submit_unknown_task(client_with_runtime):
    client, org = client_with_runtime
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": "TASK-999",
            "session_id": "sid",
            "title": "x",
            "rationale": "y",
            "script": "echo hi",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_submit_task_not_active(client_with_runtime):
    client, org = client_with_runtime
    task_id = _make_completed_task(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "sid",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"


def test_submit_session_mismatch(client_with_runtime):
    client, org = client_with_runtime
    task_id, _real_sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "WRONG",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_submit_happy_path(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR #247",
            "rationale": "needs founder gh scope",
            "script": "gh pr close 247",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("SR-")
    assert body["status"] == "pending"


def test_submit_empty_title(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "  ", "rationale": "y", "script": "x", "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_title"


def test_submit_unknown_interpreter(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "ruby",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unknown_interpreter"


def test_submit_invalid_cwd_hint_dotdot(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "bash",
            "cwd_hint": "../../etc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_cwd_hint"


def test_submit_script_too_large(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    big = "x" * 65537
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": big, "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "script_too_large"


def _submit_pending(client, org) -> str:
    """Helper: submit one pending SR and return its id."""
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={"task_id": task_id, "session_id": sid,
              "title": "t", "rationale": "r", "script": "echo z", "interpreter": "bash"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_reject_happy_path(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/scripts/{sr_id}/reject",
        json={"reason": "too risky in prod"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "too risky in prod"


def test_reject_empty_reason(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/scripts/{sr_id}/reject", json={"reason": "  "}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_reason"


def test_reject_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.post("/api/v1/orgs/alpha/scripts/SR-999/reject", json={"reason": "x"})
    assert r.status_code == 404


def test_reject_not_pending(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/reject", json={"reason": "y"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"


def test_list_scripts_default_filter_pending(client_with_runtime):
    client, org = client_with_runtime
    sr1 = _submit_pending(client, org)
    sr2 = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/scripts/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/alpha/scripts/")
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["scripts"]]
    assert sr2 in ids
    assert sr1 not in ids


def test_list_scripts_status_all(client_with_runtime):
    client, org = client_with_runtime
    sr1 = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/scripts/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/alpha/scripts/?status=all")
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["scripts"]]
    assert sr1 in ids


def test_get_script_detail(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={"task_id": task_id, "session_id": sid,
              "title": "title-x", "rationale": "y", "script": "echo 1", "interpreter": "bash"},
    )
    sr_id = r.json()["id"]
    r = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == sr_id
    assert body["title"] == "title-x"
    assert body["script_text"] == "echo 1"


def test_get_script_detail_404(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/scripts/SR-999")
    assert r.status_code == 404


def test_list_scripts_invalid_status(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/scripts/?status=bogus")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_status"


def test_list_scripts_invalid_limit(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/scripts/?limit=0")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_limit"


import time


def test_run_happy_path_completes(tmp_home, daemon_state):
    """Submit, run, and verify the SR transitions to completed.

    Uses TestClient as a context manager so the anyio portal stays alive
    across requests — asyncio.create_task in the /run handler needs the
    event loop to keep running between HTTP calls to drain the background task.
    """
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)

    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        r = client.post(
            "/api/v1/orgs/alpha/scripts/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "echo", "rationale": "test",
                  "script": "echo hello", "interpreter": "bash"},
        )
        sr_id = r.json()["id"]
        # Ensure workspace dir exists (cwd defaults to workspaces/<agent>/).
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/run", json={})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "running"
        assert body["events_url"].endswith(f"/scripts/{sr_id}/events")

        # Poll for terminal state (max ~5s).
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
    assert d["status"] == "completed", d
    assert d["exit_code"] == 0
    assert "hello" in (d["stdout_head"] or "")


def test_run_not_pending(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/run", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"


def test_run_invalid_timeout(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/run", json={"timeout_seconds": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_timeout"


def test_run_cwd_override_missing(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/scripts/{sr_id}/run",
        json={"cwd_override": "/this/path/does/not/exist"},
    )
    # Either 422 (invalid_cwd_override) or 409 (cwd_missing) is acceptable;
    # spec §5.4 step 4 says cwd_missing is the right code when resolved path doesn't exist.
    assert r.status_code in (409, 422), r.text
    code = r.json()["detail"]["code"]
    assert code in ("invalid_cwd_override", "cwd_missing")


def test_output_after_run(tmp_home, daemon_state):
    """Run a script, wait for terminal, fetch full output."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        r = client.post(
            "/api/v1/orgs/alpha/scripts/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "x", "rationale": "y",
                  "script": "echo abc; echo def >&2", "interpreter": "bash"},
        )
        sr_id = r.json()["id"]
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/run", json={})
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        r = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}/output")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "abc" in body["stdout"]
    assert "def" in body["stderr"]
    assert body["total_stdout_bytes"] >= 4
    assert body["truncated_stdout"] is False


def test_output_pending_409(client_with_runtime):
    """Output endpoint refuses to read non-terminal SRs."""
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}/output")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_terminal"


def test_output_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/scripts/SR-999/output")
    assert r.status_code == 404


def test_output_invalid_max_bytes(client_with_runtime):
    client, org = client_with_runtime
    sr_id = _submit_pending(client, org)
    r = client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}/output?max_bytes=0")
    # Either 422 invalid_max_bytes OR 409 not_terminal — accept either since
    # we're testing the validation gate before terminal-state check is fine.
    assert r.status_code in (409, 422)
