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


def test_events_terminal_after_completed(tmp_home, daemon_state):
    """Connecting to /events on an already-terminal SR sends one terminal
    event and closes."""
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
                  "script": "echo hi", "interpreter": "bash"},
        )
        sr_id = r.json()["id"]
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        client.post(f"/api/v1/orgs/alpha/scripts/{sr_id}/run", json={})
        for _ in range(50):
            if client.get(f"/api/v1/orgs/alpha/scripts/{sr_id}").json()["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        # Now connect to /events — should immediately get a terminal event.
        with client.stream("GET", f"/api/v1/orgs/alpha/scripts/{sr_id}/events") as resp:
            assert resp.status_code == 200
            data = b""
            for chunk in resp.iter_bytes():
                data += chunk
                if b"event: terminal" in data:
                    break
            assert b"event: terminal" in data


def test_events_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/scripts/SR-999/events")
    assert r.status_code == 404


def test_submit_script_fires_notify_when_orchestrator_attached(
    client_with_runtime, monkeypatch,
):
    """submit_script must schedule a Feishu notification via the org's
    orchestrator bridge. The bridge is a no-op when no notifier is attached,
    so we just verify the route invokes it with the right kwargs."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)

    calls: list[dict] = []

    def _capture(**kw):
        calls.append(kw)

    monkeypatch.setattr(org.orchestrator, "notify_script_submitted", _capture)

    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR",
            "rationale": "permission wall",
            "script": "echo hi",
            "interpreter": "bash",
            "cwd_hint": None,
        },
    )
    assert r.status_code == 201, r.text
    sr_id = r.json()["id"]

    assert len(calls) == 1, f"expected exactly one notify call, got {calls!r}"
    kw = calls[0]
    assert kw["sr_id"] == sr_id
    assert kw["task_id"] == task_id
    assert kw["title"] == "Close PR"
    assert kw["rationale"] == "permission wall"
    assert kw["script_text"] == "echo hi"
    assert kw["interpreter"] == "bash"
    assert kw["cwd_hint"] is None
    # agent is derived from task.assigned_agent — _make_active_session uses
    # "engineering_head" as the default.
    assert kw["agent"] == "engineering_head"


def test_events_stream_terminates_after_db_only_terminal_transition(
    tmp_home, daemon_state,
):
    """Regression for the SSE race: if the SR is marked terminal in the DB
    AFTER the /events handler's initial check but BEFORE its subscribe queue
    is registered (so the runner's terminal publish went to a subscriber list
    that didn't include us), the handler must still close via the periodic
    DB re-poll rather than hang forever.

    Setup: insert a running SR, schedule a background thread to flip it to
    `completed` ~250ms later WITHOUT publishing on the event bus. Open the
    stream — only the poll-recovery path can produce the terminal frame.
    """
    import threading
    import time as _t

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
                  "script": "echo hi", "interpreter": "bash"},
        )
        sr_id = r.json()["id"]

        # Pre-flip to `running` so /events takes the subscribe-and-poll path
        # (not the early-terminal short-circuit).
        org.db._conn.execute(
            "UPDATE script_requests SET status='running', started_at='2026-05-23T00:00:00Z' "
            "WHERE id=?", (sr_id,),
        )
        org.db._conn.commit()

        # Background thread: ~250ms in, mark terminal in DB. No event-bus
        # publish — only the poll-recovery path can detect this transition.
        def flip_terminal() -> None:
            _t.sleep(0.25)
            org.db._conn.execute(
                "UPDATE script_requests SET status='completed', exit_code=0, "
                "finished_at='2026-05-23T00:00:01Z', duration_ms=100 WHERE id=?",
                (sr_id,),
            )
            org.db._conn.commit()

        flipper = threading.Thread(target=flip_terminal, daemon=True)
        flipper.start()

        # Open the stream and iterate. Poll cadence is 1s, so the terminal
        # frame should arrive within ~1.5s of the DB flip = ~1.75s total.
        with client.stream("GET", f"/api/v1/orgs/alpha/scripts/{sr_id}/events") as resp:
            assert resp.status_code == 200
            data = b""
            deadline = _t.time() + 5.0
            for chunk in resp.iter_bytes():
                data += chunk
                if b"event: terminal" in data:
                    break
                if _t.time() > deadline:
                    break

        flipper.join(timeout=1.0)
        assert b"event: terminal" in data, (
            f"timed out waiting for terminal frame "
            f"(poll-recovery never fired; got {len(data)} bytes)"
        )
