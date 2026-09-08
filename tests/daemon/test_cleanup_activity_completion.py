import asyncio
import copy
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskStatus
from runtime.infrastructure.database import Database


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


def _new_owned_job(org_state, task_id: str) -> str:
    """Create the real isolated job-row shape used by the route guard."""
    job_id = org_state.db.next_job_id()
    org_state.db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="dev_agent",
        title="receipt guard fixture",
        rationale="exercise callback ownership validation",
        script_text="true",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
    return job_id


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
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload["cleanup_activity"]["reclaimed_bytes"] = None
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_reclaimed_bytes"
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []


@pytest.mark.parametrize(
    ("local_ci", "code"),
    [
        ([], "local_ci_not_object"),
        ({"command": "scripts/local_ci.sh all", "exit_code": True}, "local_ci_invalid"),
        ({"command": "scripts/local_ci.sh python", "exit_code": 0}, "local_ci_invalid"),
        ({"command": "scripts/local_ci.sh all", "exit_code": 1}, "local_ci_invalid"),
    ],
)
def test_cleanup_completion_local_ci_guard_rejects_before_pair_or_volatile_effects(app, org_state, auth_headers, local_ci, code) -> None:
    """G-ci: receipt-bearing callbacks use the shipping validator unchanged."""
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload["local_ci"] = local_ci
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"


def test_cleanup_completion_valid_local_ci_is_stored_verbatim_with_complete_pair(app, org_state, auth_headers) -> None:
    """G-ci positive: a supported evidence object reaches the compound writer."""
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload["local_ci"] = {"command": "scripts/local_ci.sh all", "exit_code": 0}
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 200
    rows = org_state.db.get_task_results(task_id)
    assert len(rows) == 1 and rows[0]["local_ci"] == '{"command": "scripts/local_ci.sh all", "exit_code": 0}'
    assert len([row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]) == 1


def test_cleanup_completion_nonmanager_self_evaluation_rejects_before_pair(app, org_state, auth_headers) -> None:
    """G-manager: nonmanager receipt callbacks fail at the existing availability guard."""
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload["manager_self_evaluation"] = {"outcome": "continue_same_root"}
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "manager_self_evaluation_not_available"
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"


@pytest.mark.parametrize(
    ("waiting_on_job_ids", "status", "expected_status", "expected_code"),
    [
        ([], "blocked", 400, "empty_waiting_on_job_ids"),
        (["JOB-any"], "completed", 400, "waiting_on_job_ids_requires_blocked"),
        (["JOB-missing"], "blocked", 404, "job_not_found"),
    ],
)
def test_cleanup_completion_waiting_job_guard_rejects_before_pair_or_volatile_effects(
    app, org_state, auth_headers, waiting_on_job_ids, status, expected_status, expected_code,
) -> None:
    """G-wait negatives use a valid receipt and reach the shipping job guard."""
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload.update({
        "status": status,
        "cleanup_activity": {**_receipt(), "outcome": "blocked" if status == "blocked" else "completed"},
        "waiting_on_job_ids": waiting_on_job_ids,
    })
    response = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"


def test_cleanup_completion_waiting_job_guard_rejects_foreign_job_without_effects(app, org_state, auth_headers) -> None:
    """G-wait ownership is derived from the real job row, not receipt input."""
    client = TestClient(app)
    owner_task, _ = _new_cleanup_task(client, org_state, auth_headers)
    foreign_job = _new_owned_job(org_state, owner_task)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload.update({
        "status": "blocked",
        "cleanup_activity": {**_receipt(), "outcome": "blocked"},
        "waiting_on_job_ids": [foreign_job],
    })
    response = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "job_not_owned_by_task", "job_id": foreign_job, "owner_task_id": owner_task,
    }
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"


def test_cleanup_completion_waiting_job_guard_dedupes_owned_jobs_into_complete_pair(app, org_state, auth_headers) -> None:
    """G-wait positive persists sorted deduped real owned jobs with one immutable pair."""
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    first = _new_owned_job(org_state, task_id)
    second = _new_owned_job(org_state, task_id)
    payload.update({
        "status": "blocked",
        "cleanup_activity": {**_receipt(), "outcome": "blocked"},
        "waiting_on_job_ids": [second, first, second, first],
    })
    response = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert response.status_code == 200
    results = org_state.db.get_task_results(task_id)
    audits = [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]
    assert len(results) == len(audits) == 1
    assert results[0]["waiting_on_job_ids"] == [first, second]
    assert audits[0]["payload"]["task_result_id"] == results[0]["id"]
    assert org_state.sessions.get_active(task_id, "dev_agent") is None


def test_cleanup_completion_persists_correlated_nullable_receipt_and_legacy_absence(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 200
    result = org_state.db.get_task_results(task_id)
    audits = [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]
    assert len(result) == len(audits) == 1
    assert result[0]["session_id"] == payload["session_id"]
    assert audits[0]["payload"]["measured_before"] == _receipt()["measured_before"]
    assert audits[0]["payload"]["measured_after"] == _receipt()["measured_after"]
    assert audits[0]["payload"]["trigger_audit_id"] is not None


def test_cleanup_active_gate_precedes_session_and_duplicate(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    org_state.db.update_task(task_id, status=TaskStatus.CANCELLED)
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_not_active"
    assert org_state.db.get_task_results(task_id) == []


def test_cleanup_negative_active_and_session_gates_have_no_effects(app, org_state, auth_headers) -> None:
    """H2: active state wins; valid receipt context cannot cause side effects."""
    client = TestClient(app)
    for mutation, expected_status, expected in (("missing", 404, "unknown_task"), ("terminal", 409, "task_not_active"), ("cancelled", 409, "task_not_active"), ("unknown", 409, "unknown_session"), ("mismatch", 409, "session_mismatch")):
        task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
        if mutation == "missing":
            response = client.post(f"/api/v1/orgs/alpha/tasks/TASK-missing-{task_id}/completion", json=payload, headers=auth_headers)
        else:
            if mutation == "terminal":
                org_state.db.update_task(task_id, status=TaskStatus.COMPLETED)
            elif mutation == "cancelled":
                org_state.db.update_task(task_id, status=TaskStatus.CANCELLED)
            elif mutation == "unknown":
                org_state.sessions.clear(task_id, "dev_agent")
            else:
                payload["session_id"] = "sess-wrong"
            response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected
        assert org_state.db.get_task_results(task_id) == []
        assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []


def test_cleanup_persisted_duplicate_bypasses_changed_receipt_after_tracker_clear(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    assert client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers).status_code == 200
    changed = {**payload, "cleanup_activity": {**_receipt(), "reclaimed_bytes": None}}
    duplicate = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=changed, headers=auth_headers)
    assert duplicate.status_code == 200
    assert len(org_state.db.get_task_results(task_id)) == 1
    assert len([row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]) == 1


def test_cleanup_receipt_cannot_graft_onto_prior_ordinary_result(app, org_state, auth_headers) -> None:
    """H3: a receipt-less existing result remains receipt-less."""
    client = TestClient(app, raise_server_exceptions=False)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    org_state.db.insert_task_result(task_id=task_id, agent="dev_agent", session_id="sess-cleanup", output_summary="ordinary", confidence_score=80)
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 200
    assert [row["output_summary"] for row in org_state.db.get_task_results(task_id)] == ["ordinary"]
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []


def test_cleanup_compound_failure_has_no_volatile_effects(app, org_state, auth_headers, monkeypatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    original_clear = org_state.sessions.clear
    original_publish = org_state.event_bus.publish
    effects: list[str] = []

    def observe_clear(*args, **kwargs):
        effects.append("clear")
        return original_clear(*args, **kwargs)

    async def observe_publish(*args, **kwargs):
        effects.append("publish")
        return await original_publish(*args, **kwargs)

    original_result_insert = org_state.db._insert_task_result_uncommitted

    def write_then_fail(*args, **kwargs):
        original_result_insert(*args, **kwargs)
        raise RuntimeError("injected compound persistence failure after real result write")

    monkeypatch.setattr(org_state.sessions, "clear", observe_clear)
    monkeypatch.setattr(org_state.event_bus, "publish", observe_publish)
    monkeypatch.setattr(org_state.db, "_insert_task_result_uncommitted", write_then_fail)
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 500
    reopened = Database(org_state.db.db_path)
    assert reopened.get_task_results(task_id) == []
    assert [row for row in reopened.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert effects == []
    assert org_state.sessions.get_active(task_id, "dev_agent") == "sess-cleanup"


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
    committed_pair = (
        copy.deepcopy(org_state.db.get_task_results(task_id)),
        copy.deepcopy([r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "workspace_cleanup_completed"]),
    )

    monkeypatch.setattr(org_state.db, "insert_cleanup_completion", original)
    published: list[dict] = []
    original_publish = org_state.event_bus.publish

    async def observe_publish(received_task_id: str, event: dict) -> None:
        published.append({"task_id": received_task_id, **event})
        await original_publish(received_task_id, event)

    monkeypatch.setattr(org_state.event_bus, "publish", observe_publish)
    retry = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert retry.status_code == 200
    assert org_state.sessions.get_active(task_id, "dev_agent") is None
    assert published == [{"task_id": task_id, "type": "completion_reported", "agent": "dev_agent", "session_id": "sess-cleanup", "status": "completed"}]
    assert len(org_state.db.get_task_results(task_id)) == 1
    assert len([r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "workspace_cleanup_completed"]) == 1
    assert (org_state.db.get_task_results(task_id), [r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "workspace_cleanup_completed"]) == committed_pair


class _ObservedLock:
    """Test-only transparent wrapper that records real lock-acquire attempts."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.entered: list[asyncio.Event] = []
        self.entries = 0
        self.request_ids: list[int] = []
        self.callback_owns_lock = False

    async def __aenter__(self) -> "_ObservedLock":
        position = self.entries
        self.entries += 1
        self.request_ids.append(id(asyncio.current_task()))
        self.entered[position].set()
        # No test barrier may intervene here: observed callbacks are queued
        # on the original lock, rather than merely staged before it.
        await self._lock.acquire()
        self.callback_owns_lock = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        assert self.callback_owns_lock
        self.callback_owns_lock = False
        self._lock.release()


def _assert_no_cleanup_effects(org_state, task_id: str, effects: list[str]) -> None:
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert effects == []


def test_cleanup_locked_revalidation_rejects_mutated_authority(app, org_state, auth_headers, monkeypatch) -> None:
    """H4a-d: every mutable cleanup authority is rechecked after the real lock."""
    base_clear = org_state.sessions.clear
    base_publish = org_state.event_bus.publish

    async def exercise(mutate, expected_status: int, expected_code: str) -> None:
        actual_lock = asyncio.Lock()
        org_state.db_lock = actual_lock
        org_state.sessions.clear = base_clear
        org_state.event_bus.publish = base_publish
        task_id, payload = _new_cleanup_task(TestClient(app), org_state, auth_headers)
        observed = _ObservedLock(actual_lock)
        observed.entered.append(asyncio.Event())
        monkeypatch.setattr(org_state, "db_lock", observed)
        effects: list[str] = []
        def clear(*args, **kwargs):
            effects.append("clear")
            return base_clear(*args, **kwargs)

        async def publish(*args, **kwargs):
            effects.append("publish")
            return await base_publish(*args, **kwargs)

        monkeypatch.setattr(org_state.sessions, "clear", clear)
        monkeypatch.setattr(org_state.event_bus, "publish", publish)
        await actual_lock.acquire()
        fixture_owns_lock = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            request = asyncio.create_task(
                client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers),
                name="callback",
            )
            try:
                await asyncio.wait_for(observed.entered[0].wait(), timeout=2)
                mutate(task_id)
                expected_task = org_state.db.get_task(task_id)
                expected_session = org_state.sessions.get_active(task_id, "dev_agent")
                _assert_no_cleanup_effects(org_state, task_id, effects)
                actual_lock.release()
                fixture_owns_lock = False
                response = await asyncio.wait_for(request, timeout=2)
            finally:
                if fixture_owns_lock:
                    actual_lock.release()
                    fixture_owns_lock = False
                if not request.done():
                    request.cancel()
                    await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=2)
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
        _assert_no_cleanup_effects(org_state, task_id, effects)
        after = org_state.db.get_task(task_id)
        assert after.status == expected_task.status
        assert after.assigned_agent == expected_task.assigned_agent
        assert org_state.sessions.get_active(task_id, "dev_agent") == expected_session
        assert not observed.callback_owns_lock

    def cancelled(task_id: str) -> None:
        org_state.db.update_task(task_id, status=TaskStatus.CANCELLED)

    def reassigned(task_id: str) -> None:
        org_state.db.update_task(task_id, assigned_agent="qa_engineer")

    def replaced(task_id: str) -> None:
        org_state.sessions.set_active(task_id, "dev_agent", "sess-newer")

    def cleared(task_id: str) -> None:
        base_clear(task_id, "dev_agent")

    # The callback must never roll back the concurrently-mutated authority.
    asyncio.run(exercise(cancelled, 409, "task_not_active"))
    asyncio.run(exercise(reassigned, 400, "cleanup_context_unavailable"))
    asyncio.run(exercise(replaced, 409, "session_mismatch"))
    asyncio.run(exercise(cleared, 409, "session_mismatch"))


def test_cleanup_concurrent_callbacks_keep_complete_immutable_first_winner(app, org_state, auth_headers, monkeypatch) -> None:
    """H5: two actual HTTP callbacks contend at the real lock; only the ordered winner persists."""
    async def exercise() -> None:
        task_id, winner = _new_cleanup_task(TestClient(app), org_state, auth_headers)
        trigger_audit_id = next(row["id"] for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_triggered")
        loser = {**winner, "output_summary": "loser", "cleanup_activity": {**_receipt(), "ambiguity_summary": "different"}}
        actual_lock = asyncio.Lock()
        observed = _ObservedLock(actual_lock)
        observed.entered.extend((asyncio.Event(), asyncio.Event()))
        monkeypatch.setattr(org_state, "db_lock", observed)
        effects: list[str] = []
        real_clear = org_state.sessions.clear
        real_publish = org_state.event_bus.publish

        def clear(*args, **kwargs):
            effects.append("clear")
            return real_clear(*args, **kwargs)

        async def publish(*args, **kwargs):
            effects.append("publish")
            return await real_publish(*args, **kwargs)

        monkeypatch.setattr(org_state.sessions, "clear", clear)
        monkeypatch.setattr(org_state.event_bus, "publish", publish)
        await actual_lock.acquire()
        fixture_owns_lock = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=winner, headers=auth_headers), name="winner")
            second: asyncio.Task[httpx.Response] | None = None
            try:
                await asyncio.wait_for(observed.entered[0].wait(), timeout=2)
                second = asyncio.create_task(client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=loser, headers=auth_headers), name="loser")
                await asyncio.wait_for(observed.entered[1].wait(), timeout=2)
                _assert_no_cleanup_effects(org_state, task_id, effects)
                # asyncio.Lock FIFO admits the first actual acquire attempt
                # after this fixture releases its held original lock.
                actual_lock.release()
                fixture_owns_lock = False
                winner_response, loser_response = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
            finally:
                if fixture_owns_lock:
                    actual_lock.release()
                    fixture_owns_lock = False
                for request in (first, second):
                    if request is None:
                        continue
                    if not request.done():
                        request.cancel()
                await asyncio.wait_for(asyncio.gather(first, *(request for request in (second,) if request is not None), return_exceptions=True), timeout=2)
        assert winner_response.status_code == loser_response.status_code == 200
        result = org_state.db.get_task_results(task_id)
        audits = [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]
        assert len(result) == len(audits) == 1
        assert len(set(observed.request_ids)) == 2
        winner_payload = winner
        assert result[0]["output_summary"] == winner_payload["output_summary"]
        expected = {
            "receipt_version": 1, "task_id": task_id, "agent": "dev_agent",
            "session_id": "sess-cleanup", "task_result_id": result[0]["id"],
            "trigger_audit_id": trigger_audit_id,
            "run_number": 1,
            **{key: value for key, value in winner_payload["cleanup_activity"].items() if key != "version"},
            "manifest_digest": None, "ledger_digest": None,
        }
        assert audits[0]["payload"] == expected
        assert effects == ["clear", "publish", "clear", "publish"]
        assert not observed.callback_owns_lock

    asyncio.run(exercise())
