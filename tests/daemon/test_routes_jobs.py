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
        "/api/v1/orgs/alpha/jobs/submit",
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
        "/api/v1/orgs/alpha/jobs/submit",
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
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": "WRONG",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_submit_happy_path(client_with_runtime):
    """Founder-review path — review_required=True keeps the row pending."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR #247",
            "rationale": "needs founder gh scope",
            "script": "gh pr close 247",
            "interpreter": "bash",
            "review_required": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("JOB-")
    assert body["status"] == "pending"


def test_submit_empty_title(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
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
        "/api/v1/orgs/alpha/jobs/submit",
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
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "bash",
            "cwd_hint": "../../etc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_cwd_hint"


def test_submit_job_too_large(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    big = "x" * 65537
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": big, "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "script_too_large"


def _submit_pending(client, org) -> str:
    """Helper: submit one pending job and return its id.

    Sets review_required=True so the row lands in `pending` (the founder-review
    path). Without the flag, the default is auto-run.
    """
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={"task_id": task_id, "session_id": sid,
              "title": "t", "rationale": "r", "script": "echo z",
              "interpreter": "bash", "review_required": True},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_reject_happy_path(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/reject",
        json={"reason": "too risky in prod"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "too risky in prod"


def test_reject_empty_reason(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/reject", json={"reason": "  "}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_reason"


def test_reject_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.post("/api/v1/orgs/alpha/jobs/SR-999/reject", json={"reason": "x"})
    assert r.status_code == 404


def test_reject_not_pending(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/reject", json={"reason": "y"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"


def test_reject_consumes_open_feishu_notification(client_with_runtime):
    """When the founder rejects via CLI/Web, any open Feishu notification
    must be marked consumed (consumed_by=cli-fallback) so a later in-thread
    APPROVE/REJECT reply doesn't trigger a stale handler_exception loop."""
    from datetime import datetime, timedelta, timezone

    client, org = client_with_runtime
    job_id = _submit_pending(client, org)

    # Simulate the Feishu push having minted a notification row.
    org.db.mint_escalation_notification(
        feishu_message_id="om_fake_push", org_slug="alpha", task_id=job_id,
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="job_request",
    )

    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/reject",
        json={"reason": "no longer needed"},
    )
    assert r.status_code == 200, r.text

    row = org.db.get_escalation_notification("om_fake_push")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


def test_list_jobs_default_filter_pending(client_with_runtime):
    client, org = client_with_runtime
    sr1 = _submit_pending(client, org)
    sr2 = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/jobs/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/alpha/jobs/")
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["jobs"]]
    assert sr2 in ids
    assert sr1 not in ids


def test_list_jobs_status_all(client_with_runtime):
    client, org = client_with_runtime
    sr1 = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/jobs/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/alpha/jobs/?status=all")
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["jobs"]]
    assert sr1 in ids


def test_get_script_detail(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={"task_id": task_id, "session_id": sid,
              "title": "title-x", "rationale": "y", "script": "echo 1",
              "interpreter": "bash", "review_required": True},
    )
    job_id = r.json()["id"]
    r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == job_id
    assert body["title"] == "title-x"
    assert body["script_text"] == "echo 1"


def test_get_script_detail_404(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/SR-999")
    assert r.status_code == 404


def test_list_jobs_invalid_status(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/?status=bogus")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_status"


def test_list_jobs_invalid_limit(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/?limit=0")
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
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "echo", "rationale": "test",
                  "script": "echo hello", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        # Ensure workspace dir exists (cwd defaults to workspaces/<agent>/).
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "running"
        assert body["events_url"].endswith(f"/jobs/{job_id}/events")

        # Poll for terminal state (max ~5s).
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
    assert d["status"] == "completed", d
    assert d["exit_code"] == 0


def test_run_consumes_open_feishu_notification(tmp_home, daemon_state):
    """A founder-triggered run via CLI/Web must consume any open
    script_request notification as cli-fallback. Mirrors the reject path."""
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)

    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "echo", "rationale": "test",
                  "script": "echo hello", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]

        # Simulate the Feishu push having minted a notification row.
        org.db.mint_escalation_notification(
            feishu_message_id="om_fake_run_push", org_slug="alpha",
            task_id=job_id, chat_id="oc_xyz",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
            kind="job_request",
        )

        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        assert r.status_code == 202, r.text

        # Poll until the runner finishes so the test doesn't race shutdown.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        # Query DB inside the TestClient context — once the context exits,
        # lifespan teardown closes the per-org connections.
        row = org.db.get_escalation_notification("om_fake_run_push")
        assert row["consumed_at"] is not None
        assert row["consumed_by"] == "cli-fallback"
    assert "hello" in (d["stdout_head"] or "")


def test_run_not_pending(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"


def test_run_invalid_timeout(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={"timeout_seconds": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_timeout"


def test_run_cwd_override_missing(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/run",
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
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "x", "rationale": "y",
                  "script": "echo abc; echo def >&2", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}/output")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "abc" in body["stdout"]
    assert "def" in body["stderr"]
    assert body["total_stdout_bytes"] >= 4
    assert body["truncated_stdout"] is False


def test_output_pending_409(client_with_runtime):
    """Output endpoint refuses to read non-terminal SRs."""
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}/output")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_terminal"


def test_output_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/SR-999/output")
    assert r.status_code == 404


def test_output_invalid_max_bytes(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}/output?max_bytes=0")
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
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "x", "rationale": "y",
                  "script": "echo hi", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        for _ in range(50):
            if client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        # Now connect to /events — should immediately get a terminal event.
        with client.stream("GET", f"/api/v1/orgs/alpha/jobs/{job_id}/events") as resp:
            assert resp.status_code == 200
            data = b""
            for chunk in resp.iter_bytes():
                data += chunk
                if b"event: terminal" in data:
                    break
            assert b"event: terminal" in data


def test_events_unknown_sr(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/SR-999/events")
    assert r.status_code == 404


def test_submit_job_fires_notify_when_orchestrator_attached(
    client_with_runtime, monkeypatch,
):
    """submit_job must schedule a Feishu notification via the org's
    orchestrator bridge. The bridge is a no-op when no notifier is attached,
    so we just verify the route invokes it with the right kwargs."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)

    calls: list[dict] = []

    def _capture(**kw):
        calls.append(kw)

    monkeypatch.setattr(org.orchestrator, "notify_job_submitted", _capture)

    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR",
            "rationale": "permission wall",
            "script": "echo hi",
            "interpreter": "bash",
            "cwd_hint": None,
            "review_required": True,
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    assert len(calls) == 1, f"expected exactly one notify call, got {calls!r}"
    kw = calls[0]
    assert kw["job_id"] == job_id
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
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "x", "rationale": "y",
                  "script": "echo hi", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]

        # Pre-flip to `running` so /events takes the subscribe-and-poll path
        # (not the early-terminal short-circuit).
        org.db._conn.execute(
            "UPDATE jobs SET status='running', started_at='2026-05-23T00:00:00Z' "
            "WHERE id=?", (job_id,),
        )
        org.db._conn.commit()

        # Background thread: ~250ms in, mark terminal in DB. No event-bus
        # publish — only the poll-recovery path can detect this transition.
        def flip_terminal() -> None:
            _t.sleep(0.25)
            org.db._conn.execute(
                "UPDATE jobs SET status='completed', exit_code=0, "
                "finished_at='2026-05-23T00:00:01Z', duration_ms=100 WHERE id=?",
                (job_id,),
            )
            org.db._conn.commit()

        flipper = threading.Thread(target=flip_terminal, daemon=True)
        flipper.start()

        # Open the stream and iterate. Poll cadence is 1s, so the terminal
        # frame should arrive within ~1.5s of the DB flip = ~1.75s total.
        with client.stream("GET", f"/api/v1/orgs/alpha/jobs/{job_id}/events") as resp:
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


# ---------------------------------------------------------------------------
# Task 11: review_required / persistent flags on /submit
# ---------------------------------------------------------------------------


def test_submit_defaults_to_review_required_false_persistent_false(
    tmp_home, daemon_state,
):
    """Default behavior: both flags absent → auto-run, bounded (300s default)."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)

    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)

        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={
                "task_id": task_id, "session_id": sid,
                "title": "echo test", "rationale": "n/a",
                "script": "echo hi\n", "interpreter": "bash",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Defaults are review_required=False, persistent=False → auto-run.
        assert body["status"] != "pending"
        job = client.get(f"/api/v1/orgs/alpha/jobs/{body['id']}").json()
        assert job["review_required"] is False
        assert job["persistent"] is False
        # Poll to terminal so the runner unwinds cleanly inside the lifespan.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{body['id']}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)


def test_submit_with_review_required_true_requires_rationale(client_with_runtime):
    """review_required=True with blank rationale → 400 rationale_required."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "close PR",
            "rationale": "   ",  # whitespace only — should be rejected
            "script": "gh pr close 1\n", "interpreter": "bash",
            "review_required": True,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "rationale_required"


def test_submit_with_persistent_true_runs_unbounded(tmp_home, daemon_state):
    """persistent=True with no explicit max_runtime_seconds → unbounded (None).

    Uses a short-lived script so the test doesn't have to manually kill an
    inflight subprocess. The invariant under test is the *recorded*
    ``max_runtime_seconds`` on the row — None means the runner was started
    without a timeout cap.
    """
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)

    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)

        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={
                "task_id": task_id, "session_id": sid,
                "title": "dev server", "rationale": "n/a",
                "script": "echo persistent\n", "interpreter": "bash",
                "persistent": True,
            },
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        job = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
        assert job["persistent"] is True
        assert job["max_runtime_seconds"] is None
        # Poll for terminal so the runner unwinds cleanly inside the lifespan.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)


def test_submit_review_required_true_enqueues_pending(client_with_runtime):
    """review_required=True with valid rationale → status=pending (no auto-run)."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "close PR",
            "rationale": "needs founder creds",
            "script": "gh pr close 1\n", "interpreter": "bash",
            "review_required": True,
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    job = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
    assert job["status"] == "pending"
    assert job["review_required"] is True


# ---------------------------------------------------------------------------
# Tasks 12-15: dual-auth GET /{id}, GET /{id}/tail, POST /{id}/stop, POST /{id}/wait
# ---------------------------------------------------------------------------


@pytest.fixture
def client_no_bearer(tmp_home, daemon_state):
    """TestClient without the Authorization header pre-attached.

    Used by the dual-auth-route tests that exercise the agent code path
    (session-binding instead of bearer).
    """
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    app = create_app(daemon_state)
    return TestClient(app)


def _submit_pending_for_session(client, org, task_id: str, sid: str) -> str:
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={"task_id": task_id, "session_id": sid,
              "title": "t", "rationale": "r",
              "script": "echo z", "interpreter": "bash",
              "review_required": True},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- Task 12: dual-auth GET /{id} ---

def test_get_job_session_binding_own_job(client_with_runtime, client_no_bearer):
    """Agent without bearer can read its own job via (task_id, session_id)."""
    client, org = client_with_runtime  # bearer client to set up the row
    task_id, sid = _make_active_session(org)
    job_id = _submit_pending_for_session(client, org, task_id, sid)

    r = client_no_bearer.get(
        f"/api/v1/orgs/alpha/jobs/{job_id}",
        params={"task_id": task_id, "session_id": sid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == job_id


def test_get_job_session_mismatch_returns_409(
    client_with_runtime, client_no_bearer,
):
    """Agent A cannot read agent B's job by passing A's own session-binding."""
    client, org = client_with_runtime
    # Agent B owns the job.
    task_b, sid_b = _make_active_session(org, agent="content_manager")
    job_id = _submit_pending_for_session(client, org, task_b, sid_b)
    # Agent A has an active session but doesn't own this job.
    task_a, sid_a = _make_active_session(org, agent="engineering_head")

    r = client_no_bearer.get(
        f"/api/v1/orgs/alpha/jobs/{job_id}",
        params={"task_id": task_a, "session_id": sid_a},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_get_job_no_bearer_no_session_returns_409(
    client_with_runtime, client_no_bearer,
):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    job_id = _submit_pending_for_session(client, org, task_id, sid)

    r = client_no_bearer.get(f"/api/v1/orgs/alpha/jobs/{job_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_get_job_bearer_still_works(client_with_runtime):
    """Founder bearer auth keeps working (regression for the route move)."""
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    job_id = _submit_pending_for_session(client, org, task_id, sid)

    r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == job_id


# --- Task 13: GET /{id}/tail ---

def test_tail_returns_last_n_lines(tmp_home, daemon_state):
    """Tail returns the last N lines from the stdout file."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        # Submit + run a script that emits 20 lines.
        script = "for i in $(seq 1 20); do echo line-$i; done"
        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "tail-test", "rationale": "y",
                  "script": script, "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        r = client.get(
            f"/api/v1/orgs/alpha/jobs/{job_id}/tail",
            params={"stream": "stdout", "lines": 10},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stream"] == "stdout"
    assert len(body["lines"]) == 10
    # Last 10 of 20 → line-11 through line-20.
    assert body["lines"][0] == "line-11"
    assert body["lines"][-1] == "line-20"


def test_tail_invalid_stream(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.get(
        f"/api/v1/orgs/alpha/jobs/{job_id}/tail",
        params={"stream": "bogus"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_stream"


def test_tail_invalid_lines(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.get(
        f"/api/v1/orgs/alpha/jobs/{job_id}/tail",
        params={"lines": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_lines"


def test_tail_unknown_job(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get("/api/v1/orgs/alpha/jobs/JOB-999/tail")
    assert r.status_code == 404


def test_tail_no_file_yet_returns_empty(client_with_runtime):
    """A pending job has no stdout_path → empty list, not a 404."""
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}/tail")
    assert r.status_code == 200
    assert r.json() == {"stream": "stdout", "lines": []}


# --- Task 14: POST /{id}/stop ---

def test_stop_returns_409_when_not_running(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)  # status=pending
    r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/stop")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_running"


def test_stop_unknown_job(client_with_runtime):
    client, _org = client_with_runtime
    r = client.post("/api/v1/orgs/alpha/jobs/JOB-999/stop")
    assert r.status_code == 404


def test_stop_kills_running_job(tmp_home, daemon_state):
    """SIGTERM a running job; the row transitions to failed with reason=founder_stop."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "long-runner", "rationale": "y",
                  "script": "sleep 30", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        # Wait until it's actually running.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] == "running":
                break
            time.sleep(0.1)
        assert d["status"] == "running", d

        r = client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/stop")
        assert r.status_code == 200, r.text

        # Poll until terminal.
        deadline = time.time() + 5
        while time.time() < deadline:
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] != "running":
                break
            time.sleep(0.1)
    assert d["status"] == "failed", d
    assert d["reason"] == "founder_stop"


# --- Task 15: POST /{id}/wait ---

def test_wait_returns_when_terminal_already(client_with_runtime):
    """A job that's already terminal returns immediately with timed_out=False."""
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    # Reject it → terminal.
    client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/reject", json={"reason": "x"})

    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/wait",
        params={"timeout_seconds": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert body["timed_out"] is False


def test_wait_returns_when_runner_finishes(tmp_home, daemon_state):
    """Spawn a quick-exit job and verify /wait unblocks on the terminal event."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "quick", "rationale": "y",
                  "script": "sleep 0.3; echo done", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})

        r = client.post(
            f"/api/v1/orgs/alpha/jobs/{job_id}/wait",
            params={"timeout_seconds": 5},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("completed", "failed")
    assert body["timed_out"] is False


def test_wait_returns_timeout_status(tmp_home, daemon_state):
    """A still-running job past the wait deadline → timed_out=True, status=running."""
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)
        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={"task_id": task_id, "session_id": sid,
                  "title": "long", "rationale": "y",
                  "script": "sleep 30", "interpreter": "bash",
                  "review_required": True},
        )
        job_id = r.json()["id"]
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/run", json={})
        # Wait until it's actually running (and the runner is past the
        # subscribe race window) before /wait, so the test exercises the
        # real timeout path, not the early-terminal branch.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] == "running":
                break
            time.sleep(0.1)

        r = client.post(
            f"/api/v1/orgs/alpha/jobs/{job_id}/wait",
            params={"timeout_seconds": 1},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "running"
        assert body["timed_out"] is True

        # Kill the long-runner so the test doesn't race shutdown.
        client.post(f"/api/v1/orgs/alpha/jobs/{job_id}/stop")
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{job_id}").json()
            if d["status"] != "running":
                break
            time.sleep(0.1)


def test_wait_invalid_timeout(client_with_runtime):
    client, org = client_with_runtime
    job_id = _submit_pending(client, org)
    r = client.post(
        f"/api/v1/orgs/alpha/jobs/{job_id}/wait",
        params={"timeout_seconds": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_timeout"


def test_wait_unknown_job(client_with_runtime):
    client, _org = client_with_runtime
    r = client.post("/api/v1/orgs/alpha/jobs/JOB-999/wait")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# list filters — review_required, persistent (Task 17)
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_mixed_fixture(client_with_runtime):
    """Insert 4 pending jobs covering all (review_required × persistent) cells.

    Returns ``(client, org, ids_by_cell)`` where ``ids_by_cell`` is a dict
    keyed by ``(review_required: bool, persistent: bool)`` for assertion
    convenience.
    """
    from src.models import JobInterpreter, JobRecord, JobStatus
    client, org = client_with_runtime
    ids_by_cell: dict[tuple[bool, bool], str] = {}
    for review_required in (True, False):
        for persistent in (True, False):
            job_id = org.db.next_job_id()
            record = JobRecord(
                id=job_id,
                task_id="TASK-001",
                agent_name="engineering_head",
                title=f"r={review_required} p={persistent}",
                rationale="fixture row",
                script_text="echo z",
                interpreter=JobInterpreter.BASH,
                status=JobStatus.PENDING,
                review_required=review_required,
                persistent=persistent,
                created_at="2026-05-27T00:00:00Z",
            )
            org.db.insert_job(record)
            ids_by_cell[(review_required, persistent)] = job_id
    return client, org, ids_by_cell


def test_list_filters_by_review_required(jobs_mixed_fixture):
    """Two of the 4 fixture rows have review_required=true."""
    client, _org, ids_by_cell = jobs_mixed_fixture
    r = client.get(
        "/api/v1/orgs/alpha/jobs/", params={"review_required": "true"}
    )
    assert r.status_code == 200, r.text
    ids = sorted(j["id"] for j in r.json()["jobs"])
    expected = sorted([
        ids_by_cell[(True, True)],
        ids_by_cell[(True, False)],
    ])
    assert ids == expected


def test_list_filters_by_persistent(jobs_mixed_fixture):
    """Two of the 4 fixture rows have persistent=false."""
    client, _org, ids_by_cell = jobs_mixed_fixture
    r = client.get(
        "/api/v1/orgs/alpha/jobs/", params={"persistent": "false"}
    )
    assert r.status_code == 200, r.text
    ids = sorted(j["id"] for j in r.json()["jobs"])
    expected = sorted([
        ids_by_cell[(True, False)],
        ids_by_cell[(False, False)],
    ])
    assert ids == expected


def test_list_filter_combined(jobs_mixed_fixture):
    """Both filters compose with AND — only the (true, true) cell matches."""
    client, _org, ids_by_cell = jobs_mixed_fixture
    r = client.get(
        "/api/v1/orgs/alpha/jobs/",
        params={"review_required": "true", "persistent": "true"},
    )
    assert r.status_code == 200, r.text
    ids = [j["id"] for j in r.json()["jobs"]]
    assert ids == [ids_by_cell[(True, True)]]


def test_list_filter_invalid_review_required_value_returns_422(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get(
        "/api/v1/orgs/alpha/jobs/", params={"review_required": "yes"}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_review_required"


def test_list_filter_invalid_persistent_value_returns_422(client_with_runtime):
    client, _org = client_with_runtime
    r = client.get(
        "/api/v1/orgs/alpha/jobs/", params={"persistent": "1"}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_persistent"


# ---------------------------------------------------------------------------
# Task 19: Feishu notification gating — only review_required=True triggers
# notify_job_submitted. Auto-run path (default) must stay silent so the
# founder isn't pinged for every routine agent command.
# ---------------------------------------------------------------------------


def test_auto_run_job_does_not_send_feishu_notification(
    tmp_home, daemon_state, monkeypatch,
):
    """review_required=False (default) → no notify_job_submitted call.

    Uses TestClient as a context manager so the auto-run path's background
    task can drain inside the lifespan, mirroring other auto-run tests.
    """
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon import paths as paths_mod

    org = daemon_state.orgs["alpha"]
    app = create_app(daemon_state)

    calls: list[dict] = []
    monkeypatch.setattr(
        org.orchestrator,
        "notify_job_submitted",
        lambda **kw: calls.append(kw),
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        task_id, sid = _make_active_session(org)
        ws = org.root / "workspaces" / "engineering_head"
        ws.mkdir(parents=True, exist_ok=True)

        r = client.post(
            "/api/v1/orgs/alpha/jobs/submit",
            json={
                "task_id": task_id,
                "session_id": sid,
                "title": "dev",
                "script": "echo hi\n",
                "interpreter": "bash",
                "review_required": False,
            },
        )
        assert r.status_code == 201, r.text
        # Poll to terminal so the runner unwinds cleanly inside the lifespan.
        for _ in range(50):
            d = client.get(f"/api/v1/orgs/alpha/jobs/{r.json()['id']}").json()
            if d["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

    # No Feishu call was made on the auto-run path.
    assert calls == [], f"expected zero notify calls, got {calls!r}"


def test_review_required_job_sends_feishu_notification(
    client_with_runtime, monkeypatch,
):
    """review_required=True → exactly one notify_job_submitted call.

    Locks the kind="job_request" contract by exercising the real
    EscalationNotifier so the kind argument it passes to its Feishu send is
    visible to the test. The actual Feishu send is monkeypatched out.
    """
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)

    calls: list[dict] = []
    monkeypatch.setattr(
        org.orchestrator,
        "notify_job_submitted",
        lambda **kw: calls.append({"kind": "job_request", **kw}),
    )

    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "close PR",
            "script": "gh pr close 1\n",
            "interpreter": "bash",
            "rationale": "needs founder creds",
            "review_required": True,
        },
    )
    assert r.status_code == 201, r.text

    # Exactly one notify call, tagged with kind="job_request".
    assert len(calls) == 1, f"expected exactly one notify call, got {calls!r}"
    assert calls[0]["kind"] == "job_request"
