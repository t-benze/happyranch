"""Dashboard refresh follows real daemon attachment and closure boundaries.

Retained integration source; run only through integration_parent in an
authorized disposable venue. These cases never launch an agent executor.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from pathlib import Path

import httpx
import pytest

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.org_state import OrgState, _OrgBusyError
from runtime.daemon.state import DaemonState
from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
from runtime.models import TaskRecord, TaskStatus
from runtime.runtime import RuntimeDir


pytestmark = pytest.mark.integration


@pytest.fixture
def dashboard_state(
    runtime_container: Path, tmp_home: Path, request: pytest.FixtureRequest,
) -> Iterator[tuple[DaemonState, asyncio.Runner]]:
    # Construct with no orgs, then materialize the existing synthetic roster.
    # The org must enter through add_org, never the startup org-loading loop.
    state = DaemonState.from_runtime(RuntimeDir.load(runtime_container), Settings())
    errors: list[dict] = []
    with asyncio.Runner() as runner:
        runner.get_loop().set_exception_handler(lambda loop, context: errors.append(context))
        try:
            assert state.orgs_snapshot() == {}
            root = request.getfixturevalue("runtime")
            assert not (root / "happyranch.db").exists()
            db = Database(root / "happyranch.db")
            try:
                initialize_complete_org_schema(db, expected_org_slug="test", layout="I")
            finally:
                db.close()
            yield state, runner
        finally:
            try:
                if not state._shutdown_complete:
                    runner.run(state.close_all())
                runner.run(asyncio.sleep(0))
                assert not errors, errors
            finally:
                if state.host_supervisor is not None:
                    state.host_supervisor.shutdown()
                state.metrics_store.close()


async def _warmed_org(state: DaemonState) -> OrgState:
    org = await state.add_org("test")
    task = org.dashboard_projection._refresh_task
    assert task is not None and not task.done(), "runtime attachment must start refresh"
    async with asyncio.timeout(10):
        while org.dashboard_projection.get_projection() is None:
            await asyncio.sleep(0.01)
    return org


def test_runtime_attachment_warms_persists_and_serves_dashboard(
    dashboard_state: tuple[DaemonState, asyncio.Runner],
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        from runtime.daemon import paths
        org = await _warmed_org(state)
        projection = org.dashboard_projection.get_projection()
        assert projection is not None
        persisted = json.loads((org.root / "dashboard_projection.json").read_text())
        assert persisted == projection.model_dump(mode="json")
        token = paths.ensure_token()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="http://127.0.0.1",
        ) as client:
            response = await client.get(
                "/api/v1/orgs/test/dashboard/summary",
                headers={"Authorization": "Bearer " + token},
            )
        assert response.status_code == 200, response.text
        expected = dict(projection.payload)
        expected.pop("server_now")
        expected["generated_at"] = persisted["generated_at"]
        actual = response.json()
        actual.pop("server_now")
        assert actual == expected

    runner.run(exercise())


def test_runtime_removal_reaps_scheduler_before_database_close(
    dashboard_state: tuple[DaemonState, asyncio.Runner], monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        org = await _warmed_org(state)
        mgr, task = org.dashboard_projection, org.dashboard_projection._refresh_task
        real_close = org.close
        closed: list[bool] = []

        def observed_close() -> None:
            assert task.done() and mgr._refresh_task is None
            real_close()
            closed.append(True)

        monkeypatch.setattr(org, "close", observed_close)
        await state.remove_org("test")
        assert closed == [True]
        assert "test" not in state.orgs_snapshot()
        assert task.done() and mgr._refresh_task is None
        assert task.cancelled() or task.exception() is None
        await asyncio.sleep(0)  # deliver any loop exception-handler notifications

    runner.run(exercise())


@pytest.mark.parametrize("refusal", ["active-task", "active-reader"])
def test_refused_runtime_removal_keeps_refresh_running(
    dashboard_state: tuple[DaemonState, asyncio.Runner], refusal: str,
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        org = await _warmed_org(state)
        mgr = org.dashboard_projection
        task = mgr._refresh_task
        if refusal == "active-task":
            await asyncio.to_thread(org.db.insert_task, TaskRecord(
                id="TASK-dashboard-busy", brief="Synthetic pending task; never enqueued",
            ))
            with pytest.raises(_OrgBusyError, match="active_tasks_in_flight"):
                await state.remove_org("test")
            assert mgr._refresh_task is task  # refusal precedes cancellation
            await asyncio.to_thread(org.db.update_task, "TASK-dashboard-busy", status=TaskStatus.COMPLETED)
        else:
            # A real captured reader refuses close AFTER the normal preflight.
            with state.capture_orgs(("test",)):
                with pytest.raises(_OrgBusyError, match="attachment_readers_in_flight"):
                    await state.remove_org("test")
            assert task.done()  # the cancelled scheduler was replaced on refusal
        assert state.get_org("test") is org
        assert mgr._refresh_task is not None and not mgr._refresh_task.done()
        assert org.db._conn.execute("SELECT 1").fetchone()[0] == 1
        await state.remove_org("test")
        assert mgr._refresh_task is None

    runner.run(exercise())


@pytest.mark.parametrize("closure", ["close-all", "lifespan"])
def test_runtime_attachment_is_reaped_on_shutdown(
    dashboard_state: tuple[DaemonState, asyncio.Runner], monkeypatch: pytest.MonkeyPatch,
    closure: str,
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        app = create_app(state)
        if closure == "close-all":
            org = await _warmed_org(state)
            mgr, task = org.dashboard_projection, org.dashboard_projection._refresh_task
            real_close = org.close

            def observed_close() -> None:
                assert task.done() and mgr._refresh_task is None
                real_close()

            monkeypatch.setattr(org, "close", observed_close)
            await state.close_all()
        else:
            # Lifespan starts with an EMPTY boot task list. Verify its own reap
            # completed BEFORE close_all can provide a second cleanup path.
            async with app.router.lifespan_context(app):
                org = await _warmed_org(state)
                mgr, task = org.dashboard_projection, org.dashboard_projection._refresh_task
                real_close_all = state.close_all

                async def observed_close_all() -> None:
                    assert task.done() and mgr._refresh_task is None
                    await real_close_all()

                monkeypatch.setattr(state, "close_all", observed_close_all)
        assert task.done() and mgr._refresh_task is None
        assert task.cancelled() or task.exception() is None
        assert state._shutdown_complete and state.orgs_snapshot() == {}

    runner.run(exercise())


def test_repeated_runtime_attachment_reuses_scheduler(
    dashboard_state: tuple[DaemonState, asyncio.Runner],
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        org = await _warmed_org(state)
        task = org.dashboard_projection._refresh_task
        projection = org.dashboard_projection.get_projection()
        attached_again = await state.add_org("test")
        assert attached_again is org
        assert org.dashboard_projection._refresh_task is task and not task.done()
        assert org.dashboard_projection.get_projection() is projection
        await state.remove_org("test")
        assert task.done() and org.dashboard_projection._refresh_task is None

    runner.run(exercise())


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_unsuccessful_attachment_does_not_start_refresh(
    dashboard_state: tuple[DaemonState, asyncio.Runner], monkeypatch: pytest.MonkeyPatch,
    failure: type[BaseException],
) -> None:
    state, runner = dashboard_state

    async def exercise() -> None:
        from runtime.daemon.queue import _AttachmentNotificationQueue
        real_join = _AttachmentNotificationQueue.join_puts

        async def failed_handoff(queue: _AttachmentNotificationQueue) -> None:
            await real_join(queue)
            raise failure("attachment handoff failed")

        with monkeypatch.context() as patch:
            patch.setattr(_AttachmentNotificationQueue, "join_puts", failed_handoff)
            with pytest.raises(failure):
                await state.add_org("test")
        # Publication occurred, but handoff failed: no refresh may outlive that
        # failed/cancelled attachment. Normal attachment ownership is unchanged.
        org = state.orgs_snapshot()["test"]
        assert org.dashboard_projection._refresh_task is None
        assert not (org.root / "dashboard_projection.json").exists()
        await asyncio.sleep(0)

    runner.run(exercise())
