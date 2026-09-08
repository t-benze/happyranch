import asyncio

import httpx
from fastapi.testclient import TestClient
from runtime.models import TaskStatus


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
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    payload["cleanup_activity"]["reclaimed_bytes"] = None
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_reclaimed_bytes"
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []


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


def test_cleanup_persisted_duplicate_bypasses_changed_receipt_after_tracker_clear(app, org_state, auth_headers) -> None:
    client = TestClient(app)
    task_id, payload = _new_cleanup_task(client, org_state, auth_headers)
    assert client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers).status_code == 200
    changed = {**payload, "cleanup_activity": {**_receipt(), "reclaimed_bytes": None}}
    duplicate = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=changed, headers=auth_headers)
    assert duplicate.status_code == 200
    assert len(org_state.db.get_task_results(task_id)) == 1
    assert len([row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]) == 1


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

    def fail_writer(**kwargs):
        raise RuntimeError("injected compound persistence failure")

    monkeypatch.setattr(org_state.sessions, "clear", observe_clear)
    monkeypatch.setattr(org_state.event_bus, "publish", observe_publish)
    monkeypatch.setattr(org_state.db, "insert_cleanup_completion", fail_writer)
    response = client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers)
    assert response.status_code == 500
    assert org_state.db.get_task_results(task_id) == []
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


class _ObservedLock:
    """Test-only transparent async-lock wrapper with bounded acquire probes."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.entered: list[asyncio.Event] = []
        self.entries = 0
        self.permit: list[asyncio.Event] = []

    async def __aenter__(self) -> "_ObservedLock":
        position = self.entries
        self.entries += 1
        self.entered[position].set()
        await self.permit[position].wait()
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args: object) -> None:
        self._lock.release()


def _assert_no_cleanup_effects(org_state, task_id: str, effects: list[str]) -> None:
    assert org_state.db.get_task_results(task_id) == []
    assert [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"] == []
    assert effects == []


def test_cleanup_locked_revalidation_rejects_mutated_authority(app, org_state, auth_headers, monkeypatch) -> None:
    """H4a-d: every mutable cleanup authority is rechecked after the real lock."""
    real_db_lock = org_state.db_lock
    base_clear = org_state.sessions.clear
    base_publish = org_state.event_bus.publish

    async def exercise(mutate, expected_status: int, expected_code: str) -> None:
        org_state.db_lock = real_db_lock
        org_state.sessions.clear = base_clear
        org_state.event_bus.publish = base_publish
        task_id, payload = _new_cleanup_task(TestClient(app), org_state, auth_headers)
        observed = _ObservedLock(real_db_lock)
        permitted = asyncio.Event()
        observed.entered.append(asyncio.Event())
        observed.permit.append(permitted)
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            request = asyncio.create_task(
                client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload, headers=auth_headers),
                name="callback",
            )
            try:
                await asyncio.wait_for(observed.entered[0].wait(), timeout=2)
                mutate(task_id)
                permitted.set()
                response = await asyncio.wait_for(request, timeout=2)
            finally:
                permitted.set()
                if not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
        _assert_no_cleanup_effects(org_state, task_id, effects)

    def cancelled(task_id: str) -> None:
        org_state.db.update_task(task_id, status=TaskStatus.CANCELLED)

    def reassigned(task_id: str) -> None:
        org_state.db.update_task(task_id, assigned_agent="qa_engineer")

    def replaced(task_id: str) -> None:
        org_state.sessions.set_active(task_id, "dev_agent", "sess-newer")

    def cleared(task_id: str) -> None:
        base_clear(task_id, "dev_agent")

    asyncio.run(exercise(cancelled, 409, "task_not_active"))
    asyncio.run(exercise(reassigned, 400, "cleanup_context_unavailable"))
    asyncio.run(exercise(replaced, 409, "session_mismatch"))
    asyncio.run(exercise(cleared, 409, "session_mismatch"))


def test_cleanup_concurrent_callbacks_keep_complete_immutable_first_winner(app, org_state, auth_headers, monkeypatch) -> None:
    """H5: two actual HTTP callbacks contend at the real lock; only the ordered winner persists."""
    async def exercise() -> None:
        task_id, winner = _new_cleanup_task(TestClient(app), org_state, auth_headers)
        loser = {**winner, "output_summary": "loser", "cleanup_activity": {**_receipt(), "ambiguity_summary": "different"}}
        actual_lock = org_state.db_lock
        observed = _ObservedLock(actual_lock)
        observed.entered.extend((asyncio.Event(), asyncio.Event()))
        observed.permit.extend((asyncio.Event(), asyncio.Event()))
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=winner, headers=auth_headers), name="winner")
            second = asyncio.create_task(client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=loser, headers=auth_headers), name="loser")
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in observed.entered)), timeout=2)
                _assert_no_cleanup_effects(org_state, task_id, effects)
                # The first acquire entry is deterministically admitted first;
                # whichever request occupied that entry becomes the immutable winner.
                observed.permit[0].set()
                actual_lock.release()
                done, pending = await asyncio.wait({first, second}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
                assert len(done) == 1
                winner_request = done.pop()
                winner_response = winner_request.result()
                observed.permit[1].set()
                loser_request = pending.pop()
                loser_response = await asyncio.wait_for(loser_request, timeout=2)
            finally:
                for gate in observed.permit:
                    gate.set()
                if actual_lock.locked():
                    actual_lock.release()
                await asyncio.gather(first, second, return_exceptions=True)
        assert winner_response.status_code == loser_response.status_code == 200
        result = org_state.db.get_task_results(task_id)
        audits = [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "workspace_cleanup_completed"]
        assert len(result) == len(audits) == 1
        winner_payload = winner if winner_request is first else loser
        assert result[0]["output_summary"] == winner_payload["output_summary"]
        expected = {
            "receipt_version": 1, "task_id": task_id, "agent": "dev_agent",
            "session_id": "sess-cleanup", "task_result_id": result[0]["id"],
            "trigger_audit_id": audits[0]["payload"]["trigger_audit_id"],
            "run_number": 1,
            **{key: value for key, value in winner_payload["cleanup_activity"].items() if key != "version"},
            "manifest_digest": None, "ledger_digest": None,
        }
        assert audits[0]["payload"] == expected
        assert effects == ["clear", "publish", "clear", "publish"]

    asyncio.run(exercise())
