"""S04 ordinary source callback/recovery orderings; no installed acceptance."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Iterator

import httpx
import pytest

from runtime.daemon import paths
from runtime.models import TaskStatus
from runtime.orchestrator.run_step import (
    _consume_accepted_completion_recovery, _consume_completion_report,
)
from tests.test_memory_non_task_transport_shipping import shipping_venue as _source_venue
from tests.test_memory_retry_transport_shipping import _callback, _new_worker


@pytest.fixture
def arbitration_venue(test_settings, monkeypatch, tmp_path, request) -> Iterator:
    # Reuse the reviewed real OrgState/provider/CLI/HTTP venue and its bounded,
    # exception-safe resource ownership. Only the external provider is fake.
    with contextmanager(_source_venue.__wrapped__)(
        test_settings, monkeypatch, tmp_path, request,
    ) as venue:
        venue.org.orchestrator.attach_host_supervisor(None)
        yield venue
    assert venue.closed and venue.runs_closed
    assert all(f.done() for f in venue.futures)
    assert all(c.finished and c.conn.fileno() == -1 and c.wire.closed for c in venue.children)
    assert not venue.server_thread.is_alive() and not venue.daemon.orgs


class _TrafficBarrier:
    """Private ASGI observer controls real DB-lock wait or response delivery.

    It delegates every request to the unchanged application. It neither
    implements admission nor changes tracker, database, or callback payloads.
    """
    def __init__(self, venue, *, db_wait: bool = False, response_wait: bool = False):
        self.venue, self.db_wait, self.response_wait = venue, db_wait, response_wait
        self.entered, self.release = threading.Event(), threading.Event()
        self.statuses = []
        self.used = False
        self.app = venue.server.config.loaded_app
        venue.server.config.loaded_app = self

    async def __call__(self, scope, receive, send):
        if not scope.get('path', '').endswith('/completion') or self.used:
            await self.app(scope, receive, send)
            return
        self.used = True

        async def observed_send(message):
            if message['type'] == 'http.response.start':
                self.statuses.append(message['status'])
                if self.response_wait:
                    self.entered.set()
                    assert await asyncio.to_thread(self.release.wait, 10)
            await send(message)

        if not self.db_wait:
            await self.app(scope, receive, observed_send)
            return
        async with self.venue.org.db_lock:
            route = asyncio.create_task(self.app(scope, receive, observed_send))
            try:
                # Wait for the actual route's waiter on the actual org lock;
                # observing receipt of a request alone would not prove this edge.
                async with asyncio.timeout(5):
                    while not self.venue.org.db_lock._waiters:
                        if route.done():
                            await route
                            raise AssertionError('first POST did not reach real DB lock')
                        await asyncio.sleep(0)
                self.entered.set()
                assert await asyncio.to_thread(self.release.wait, 10)
            except BaseException:
                route.cancel()
                await asyncio.gather(route, return_exceptions=True)
                raise
        await route


def _origin(v):
    task = _new_worker(v, 'ordinary retirement callback arbitration')
    assert v.org.db.try_claim_for_step(task, TaskStatus.PENDING, None, 1)
    future = v.submit(v.org.orchestrator._run_agent, task, 'dev_agent', 'ordinary source')
    child = v.accept(future)
    sid = child.evidence['hint']
    assert v.org.db.get_task(task).current_session_id == sid
    assert v.org.sessions.get_context_by_session(sid) == ('alpha', task, 'dev_agent')
    assert sid != child.evidence['provider_id']
    return task, child, future


def _body(task, child):
    return {'session_id': child.evidence['hint'], 'agent': 'dev_agent',
            'status': 'completed', 'output_summary': 'arbitrated completion', 'confidence': 90}


def _post(v, task, body, timeout=10):
    # Exercise the real authenticated loopback route; do not print the bearer.
    return httpx.post(v.url + f'/tasks/{task}/completion', json=body, timeout=timeout,
                      headers={'Authorization': 'Bearer ' + paths.token_file().read_text().strip()})


def _timed_post(v, task, body):
    # The expected transport timeout is an observed value, so the resource
    # owner's joined future completes normally rather than replaying an
    # already-authenticated expected exception during teardown.
    try:
        return _post(v, task, body, 0.3)
    except httpx.ReadTimeout as error:
        return error


def _consume(v, task, report, *, recovery=False):
    rows = v.org.db.get_task_results(task)
    assert len(rows) == 1 and report is not None
    row = rows[0]
    if recovery:
        _consume_accepted_completion_recovery(
            v.org.orchestrator, task, report, agent='dev_agent',
            session_id=row['session_id'], result_row_id=row['id'],
        )
    else:
        _consume_completion_report(v.org.orchestrator, task, report, result_row_id=row['id'])
    assert v.org.db.get_task(task).status is TaskStatus.COMPLETED
    assert [(r['id'], r['session_id']) for r in v.org.db.get_task_results(task)] == [
        (row['id'], row['session_id'])]
    return row


def _recovery(v, task, origin):
    recovery_sid = 'runtime-recovery-' + origin.evidence['hint']
    now = datetime.now(timezone.utc)
    assert v.org.db.claim_task_completion_recovery(
        task_id=task, agent='dev_agent', origin_session_id=origin.evidence['hint'],
        recovery_session_id=recovery_sid, provider_session_id=origin.evidence['provider_id'],
        claimed_at=now.isoformat(), expires_at=(now + timedelta(seconds=120)).isoformat(),
    )
    future = v.submit(partial(
        v.org.orchestrator._run_agent, task, 'dev_agent', 'report prior work only',
        runtime_session_id=recovery_sid, resume_session_id=origin.evidence['provider_id'],
        origin_runtime_session_id=origin.evidence['hint'], recovery=True,
        recovery_deadline_monotonic=time.monotonic() + 120,
        timeout_seconds_override=120,
    ))
    child = v.accept(future)
    assert child.evidence['hint'] == recovery_sid
    assert v.org.db.get_task(task).current_session_id == recovery_sid
    assert v.org.sessions.is_recovery_session(task, 'dev_agent', recovery_sid)
    assert v.org.sessions.get_pid(task, 'dev_agent') == child.evidence['pid']
    assert recovery_sid not in (origin.evidence['hint'], origin.evidence['provider_id'], child.evidence['provider_id'])
    _callback(v, child, task)
    return child, future


@pytest.mark.parametrize('arbitration_venue', ['codex'], indirect=True)
@pytest.mark.parametrize('publication_first', [True, False], ids=['publish-before-retire', 'retire-before-publish'])
def test_recovery_publication_and_origin_retirement_arbitrate(arbitration_venue, publication_first):
    """v24: recovery tuple survives old finally; canonical callback consumes once.
    Regression: unconditional old cleanup erases recovery, or publish needs old
    tracker. Existing synthetic-generation keeper lacks durable claim/bootstrap/
    HTTP/consumer. Real production seams, private provider barriers, no hook.
    """
    v = arbitration_venue
    task, origin, origin_future = _origin(v)
    if publication_first:
        recovery, recovery_future = _recovery(v, task, origin)
        control = partial(recovery.finish, abort=True)
        v.org.sessions.set_cancel_control(task, 'dev_agent', recovery.evidence['hint'], control)
    origin.finish()
    result, report = origin_future.result(timeout=10)
    assert result.success and report is None
    assert result.session_id == origin.evidence['hint']
    assert result.agent_session_id == origin.evidence['provider_id']
    if not publication_first:
        assert v.org.sessions.get_active(task, 'dev_agent') is None
        # A genuine bootstrapped origin retired before its first POST remains
        # unpersisted/unknown. Refusal must still allow real recovery progress.
        refused = _post(v, task, _body(task, origin))
        assert refused.status_code == 409
        assert refused.json()['detail'] == {'code': 'unknown_session', 'task_id': task, 'agent': 'dev_agent'}
        assert v.org.db.get_task_results(task) == []
        recovery, recovery_future = _recovery(v, task, origin)
        control = partial(recovery.finish, abort=True)
        v.org.sessions.set_cancel_control(task, 'dev_agent', recovery.evidence['hint'], control)
    sid = recovery.evidence['hint']
    assert v.org.sessions.get_context_by_session(sid) == ('alpha', task, 'dev_agent')
    assert v.org.sessions.get_pid(task, 'dev_agent') == recovery.evidence['pid']
    assert v.org.sessions.get_cancel_control(task, 'dev_agent') is control
    recovery.command(recovery.completion)
    before = v.org.db.get_task_results(task)
    recovery.command(recovery.completion)  # exact canonical transport retry
    assert v.org.db.get_task_results(task) == before
    recovery.finish()
    recovery_result, recovery_report = recovery_future.result(timeout=10)
    assert recovery_result.success and recovery_result.session_id == sid
    row = _consume(v, task, recovery_report, recovery=True)
    _consume(v, task, recovery_report, recovery=True)  # consumption replay
    ledger = v.org.db.fetch_all_readonly('SELECT * FROM task_completion_recoveries WHERE task_id=?', (task,))
    assert len(ledger) == 1 and ledger[0]['state'] == 'callback_consumed'
    assert ledger[0]['accepted_result_id'] == row['id']
    assert not v.org.sessions.iter_active()
    print(json.dumps({'ordering': publication_first, 'origin': origin.evidence,
                      'recovery': recovery.evidence, 'result': row, 'ledger': [dict(r) for r in ledger]}))


@pytest.mark.parametrize('arbitration_venue', ['codex'], indirect=True)
def test_callback_admission_before_origin_retirement(arbitration_venue):
    """v24: admitted HTTP200 result consumed once after ordinary final exit.
    Regression: admission fails to persist or cleanup destroys accepted result.
    Shipping has no controlled real DB-lock waiter. Real route/lease/DB/consumer,
    no test-only production seam; ASGI wrapper only holds existing lock.
    """
    v = arbitration_venue
    task, child, future = _origin(v)
    gate = _TrafficBarrier(v, db_wait=True)
    admitted, finish_admission = threading.Event(), threading.Event()

    def observe_transaction(action, first, second, database, trigger):
        # Observe SQLite's actual BEGIN while the route holds the actual
        # binding lease. Every SQL operation retains SQLITE_OK unchanged.
        if action == sqlite3.SQLITE_TRANSACTION and first == 'BEGIN':
            admitted.set()
            assert finish_admission.wait(10)
        return sqlite3.SQLITE_OK

    try:
        post = v.submit(_post, v, task, _body(task, child))
        assert gate.entered.wait(5)
        assert v.org.sessions.get_active(task, 'dev_agent') == child.evidence['hint']
        v.org.db._conn.set_authorizer(observe_transaction)
        gate.release.set()
        assert admitted.wait(5)
        assert v.org.sessions.binding_lease(task, 'dev_agent').locked()
        child.finish()
        assert not future.done()
        assert v.org.sessions.get_active(task, 'dev_agent') == child.evidence['hint']
        finish_admission.set()
        response = post.result(timeout=10)
        assert response.status_code == 200 and response.json() == {'ok': True}
        assert gate.statuses == [200]
        result, report = future.result(timeout=10)
        assert result.success
        row = _consume(v, task, report)
        assert row['session_id'] == child.evidence['hint']
        assert not v.org.sessions.iter_active()
    finally:
        finish_admission.set()
        gate.release.set()
        v.org.db._conn.set_authorizer(None)


@pytest.mark.parametrize('arbitration_venue', ['codex'], indirect=True)
def test_persisted_response_timeout_retries_after_retirement(arbitration_venue):
    """v24: lost HTTP response retries200 with same durable result after exit.
    Regression: exact retired retry denied or duplicate allocated. Existing v2
    retry keepers do not lose response at real provider exit. Real HTTP/DB/owner/
    consumer, ASGI response-only barrier, no production hook or positive SID seed.
    """
    v = arbitration_venue
    task, child, future = _origin(v)
    gate = _TrafficBarrier(v, response_wait=True)
    body = _body(task, child)
    try:
        post = v.submit(_timed_post, v, task, body)
        assert gate.entered.wait(5) and gate.statuses == [200]
        assert isinstance(post.result(timeout=5), httpx.ReadTimeout)
        before = v.org.db.get_task_results(task)
        assert len(before) == 1
        child.finish()
        result, report = future.result(timeout=10)
        assert result.success and not v.org.sessions.iter_active()
        response = _post(v, task, body)
        assert response.status_code == 200 and response.json() == {'ok': True}
        assert v.org.db.get_task_results(task) == before
        _consume(v, task, report)
    finally:
        gate.release.set()


@pytest.mark.parametrize('arbitration_venue', ['codex'], indirect=True)
def test_first_callback_waiting_on_db_lock_loses_to_retirement_and_recovers(arbitration_venue):
    """v24: unpersisted retired origin409 session_mismatch; real run_step recovery completes.
    Regression: await guard trusts stale session or refusal leaves task stranded.
    Synthetic tracker keeper and ordinary recovery unit lack first HTTP DB-lock
    waiter. Real bootstrap/provider/route/claim/publication/consumer, no hook.
    """
    v = arbitration_venue
    task = _new_worker(v, 'first callback loses to ordinary exit')
    origin_future = v.submit(v.org.orchestrator.run_step, task)
    origin = v.accept(origin_future)
    gate = _TrafficBarrier(v, db_wait=True)
    try:
        post = v.submit(_post, v, task, _body(task, origin))
        assert gate.entered.wait(5)
        assert v.org.sessions.get_active(task, 'dev_agent') == origin.evidence['hint']
        assert v.org.db.get_task_results(task) == []
        origin.finish()
        # Real run_step's clean-omission branch must spend and publish the sole
        # recovery. No test-authored recovery claim or task/session mutation.
        recovery = v.accept(origin_future)
        sid = recovery.evidence['hint']
        assert sid != origin.evidence['hint']
        assert v.org.sessions.get_context_by_session(origin.evidence['hint']) is None
        assert v.org.sessions.is_recovery_session(task, 'dev_agent', sid)
        assert v.org.db.get_task(task).current_session_id == sid
        gate.release.set()
        response = post.result(timeout=10)
        assert response.status_code == 409
        assert response.json()['detail'] == {'code': 'session_mismatch', 'task_id': task, 'agent': 'dev_agent'}
        assert gate.statuses == [409] and v.org.db.get_task_results(task) == []
        _callback(v, recovery, task)
        recovery.finish()
        assert origin_future.result(timeout=10) is None
        assert v.org.db.get_task(task).status is TaskStatus.COMPLETED
        rows = v.org.db.get_task_results(task)
        assert len(rows) == 1 and rows[0]['session_id'] == sid
        ledger = v.org.db.fetch_all_readonly('SELECT * FROM task_completion_recoveries WHERE task_id=?', (task,))
        assert len(ledger) == 1 and ledger[0]['state'] == 'callback_consumed'
        assert ledger[0]['accepted_result_id'] == rows[0]['id']
        assert ledger[0]['origin_session_id'] == origin.evidence['hint']
        assert ledger[0]['provider_session_id'] == origin.evidence['provider_id']
        assert ledger[0]['recovery_session_id'] == sid
        assert not v.org.sessions.iter_active()
        print(json.dumps({'first_http': response.json(), 'origin': origin.evidence,
                          'recovery': recovery.evidence, 'result': rows,
                          'ledger': [dict(r) for r in ledger]}))
    finally:
        gate.release.set()
