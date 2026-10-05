"""Real admission, task cancellation and draft lifecycle ownership boundaries."""
import json
import asyncio
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.workflow_schema import validate_workflow_schema
from tests.daemon.test_workflow_activation_routes import BASE, activation_org, _snapshot


@pytest.fixture
def draft_host(activation_org, monkeypatch):
    """A bounded actual subprocess at the existing supervisor backend boundary.

    This is a unit causal control, never a hosted/provider acceptance receipt.
    Canonical org/template/cutover/admission and all draft producers stay real.
    """
    from runtime.platform.session_backend import RunningHandle, LaunchSpec
    from runtime.orchestrator.executors import ExecutorResult
    from tests.test_host_supervisor_lifecycle import FakeBackend, make_supervisor

    client, org, state, body = activation_org
    controls = {'callback': True, 'quiescent': True, 'retry': False,
                'early_callback': False, 'prelaunch_callback': False, 'ack': True,
                'cancel_order': None, 'rate_limited': False}
    observations = []
    processes = []

    def callback(session_id):
        task = org.db.list_tasks(limit=1)[0]
        payload = {'agent': task.assigned_agent, 'session_id': session_id,
                   'status': 'completed', 'output_summary': 'authentic controlled draft', 'confidence': 83}
        response = client.post(f'/api/v1/orgs/alpha/tasks/{task.id}/completion', json=payload)
        observations.append(('callback_response', response.status_code))
        return task, payload, response

    class SubprocessBackend(FakeBackend):
        def launch(self, pending, spec):
            with self.lock:
                self.calls['launch'] += 1
            assert not org.db._conn.in_transaction
            assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
            intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
            assert intent['host_launch_started'] == 1
            assert intent['state'] == 'claimed' and intent['session_id']
            assert org.sessions.get_active(intent['task_id'], intent['assigned_principal']) == intent['session_id']
            proc = subprocess.Popen(spec.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(proc)
            if controls['early_callback']:
                intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
                task, payload, response = callback(intent['session_id'])
                assert response.status_code == 200, response.text
                observations.append(('early_callback', payload))
                assert org.db.get_task(task.id).status.value == 'in_progress'
                assert org.db.execute('SELECT state FROM workflow_draft_dispatch_intents').fetchone()[0] == 'claimed'
            if not controls['ack']:
                from runtime.platform.session_backend import BackendLaunchError
                raise BackendLaunchError('controlled loss after actual subprocess launch')
            self.last_running = RunningHandle(backend=self.name, token=pending.token,
                request_id=pending.request_id, root_pid=proc.pid, start_identity='controlled-unit-process', process=proc)
            return self.last_running

        def finish(self, running, reason, grace, **kwargs):
            running.process.wait(timeout=5)
            receipt = super().finish(running, reason, grace, **kwargs)
            return replace(receipt, quiescent=controls['quiescent'])

    class CallbackExecutor:
        def build_launch_spec(self, **kwargs):
            if controls['prelaunch_callback']:
                task, payload, response = callback(kwargs['session_id'])
                assert response.status_code == 409, response.text
                assert not org.db.execute('SELECT 1 FROM task_results').fetchone()
                observations.append(('prelaunch_refused', response.status_code))
            return LaunchSpec(argv=(sys.executable, '-c', 'pass'))

        def run(self, **kwargs):
            task = org.db.list_tasks(limit=1)[0]
            intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
            observations.append(('running', intent['state'], intent['host_launch_started'], intent['session_id']))
            assert intent['session_id'] == kwargs['session_id']
            kwargs['running'].process.communicate(timeout=5)
            if controls['cancel_order'] == 'before':
                response = client.post(f'/api/v1/orgs/alpha/tasks/{task.id}/cancel', json={})
                assert response.status_code == 200 and response.json()['pending'], response.text
            if controls['callback'] and not controls['early_callback']:
                task, payload, response = callback(kwargs['session_id'])
                assert response.status_code == (409 if controls['cancel_order'] == 'before' else 200), response.text
                observations.append(('callback', payload))
                active = org.sessions.get_active(task.id, task.assigned_agent)
                observations.append(('tracker_after_callback', active))
                assert active == kwargs['session_id']
                if controls['retry']:
                    before = _snapshot(org)
                    response = client.post(f'/api/v1/orgs/alpha/tasks/{task.id}/completion', json=payload)
                    assert response.status_code == 200, response.text
                    assert _snapshot(org) == before
                    response = client.post(f'/api/v1/orgs/alpha/tasks/{task.id}/completion',
                                           json={**payload, 'confidence': 82})
                    assert response.status_code == 409, response.text
                    assert _snapshot(org) == before
            if controls['cancel_order'] == 'after':
                response = client.post(f'/api/v1/orgs/alpha/tasks/{task.id}/cancel', json={})
                assert response.status_code == 200 and response.json()['pending'], response.text
            return ExecutorResult(success=not controls['rate_limited'], duration_seconds=0,
                                  rate_limited=controls['rate_limited'], session_id=kwargs['session_id'])

    backend = SubprocessBackend(name='controlled-unit-subprocess')
    supervisor, publisher = make_supervisor(backend=backend, max_retry_attempts=1, backoff_seconds=(0.0,))
    org.orchestrator.attach_host_supervisor(supervisor)
    monkeypatch.setattr(org.orchestrator, '_build_executor', lambda provider: CallbackExecutor())
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()

    worker = threading.Thread(target=serve)
    worker.start()
    assert started.wait(5)
    monkeypatch.setattr(org.orchestrator, '_main_loop', loop)
    try:
        yield client, org, state, body, controls, observations, backend
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(5)
        assert not worker.is_alive()
        loop.close()
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                stream.close()


def test_activation_notifies_only_after_commit_and_never_on_replay(activation_org, monkeypatch):
    client, org, state, body = activation_org
    observations = []
    original = state.queue.enqueue

    def observe(slug, task_id, **kwargs):
        snapshot = _snapshot(org)
        observations.append((slug, task_id, len(snapshot['workflow_draft_dispatch_intents']),
                             len(snapshot['workflow_draft_dispatch_events'])))
        return original(slug, task_id, **kwargs)

    monkeypatch.setattr(state.queue, 'enqueue', observe)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert observations == [('alpha', response.json()['root_task_id'], 1, 1)]
    assert client.post(BASE, json=body).status_code == 200
    assert len(observations) == 1


def test_queued_cancel_fences_intent_before_task_terminal_and_replays_read_only(activation_org):
    client, org, state, body = activation_org
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={})
    assert response.status_code == 200, response.text
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'cancelled' and intent['cancellation_requested'] == 1
    assert intent['session_id'] is None and intent['host_execution_id'] is None
    assert intent['host_launch_started'] == 0 and intent['final_result_id'] is None
    assert org.db.get_task(task_id).status.value == 'cancelled'
    events = [json.loads(row[0]) for row in org.db.execute(
        'SELECT event_bytes FROM workflow_draft_dispatch_events ORDER BY event_seq')]
    assert [e['event']['event_kind'] for e in events] == ['admitted', 'cancel_requested', 'cancelled']
    assert events[-1]['terminal_evidence'] is None
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')
    before = _snapshot(org)
    response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={})
    assert response.status_code == 200, response.text
    assert _snapshot(org) == before
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action='task_cancelled'", (task_id,)).fetchone()


def test_real_dispatch_result_and_quiescence_complete_without_manager_decision(draft_host):
    client, org, state, body, controls, observations, backend = draft_host
    controls['retry'] = True
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    assert [row[0] for row in observations] == ['running', 'callback_response', 'callback', 'tracker_after_callback'], observations
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'completed'
    assert intent['host_launch_started'] == 1 and intent['host_execution_id']
    assert type(intent['final_result_id']) is int and intent['session_id']
    assert next(row[1] for row in observations if row[0] == 'tracker_after_callback') == intent['session_id']
    assert org.db.get_task(task_id).status.value == 'completed'
    assert org.db.get_task(task_id).orchestration_step_count == 0
    assert org.sessions.get_active(task_id, 'product_lead') is None
    rows = [dict(row) for row in org.db.execute('SELECT * FROM workflow_draft_dispatch_events ORDER BY event_seq')]
    assert [row['event_kind'] for row in rows] == ['admitted', 'claimed', 'launch_reserved', 'running', 'callback_recorded', 'completed']
    assert rows[-1]['result_id'] is None
    assert json.loads(rows[-1]['event_bytes'])['result'] == json.loads(rows[-2]['event_bytes'])['result']
    assert json.loads(rows[-1]['event_bytes'])['terminal_evidence'] == {'host_quiescent': True}
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    assert backend.last_running.request_id == f"workflow-draft-host:{intent['id']}"
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')
    before = _snapshot(org)
    org.orchestrator.run_step(task_id)
    assert _snapshot(org) == before and backend.calls['launch'] == 1
    payload = next(row[1] for row in observations if row[0] == 'callback')
    db_payload = dict(task_id=task_id, agent='product_lead', session_id=payload['session_id'], status='completed',
                      output_summary=payload['output_summary'], confidence_score=83, risks_flagged=[])
    assert org.db.admit_task_completion_callback(**db_payload)
    assert not org.db.admit_task_completion_callback(**{**db_payload, 'confidence_score': 82})
    assert _snapshot(org) == before
    response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/completion', json=payload)
    assert response.status_code == 200, response.text
    assert _snapshot(org) == before
    changed = {**payload, 'output_summary': 'changed result'}
    response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/completion', json=changed)
    assert response.status_code == 409, response.text
    assert _snapshot(org) == before
    assert not org.db.execute('SELECT 1 FROM workflow_submissions').fetchone()
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action IN ('decision','task_failed')", (task_id,)).fetchone()


@pytest.mark.parametrize('gap', ['callback', 'quiescent'])
def test_possible_launch_or_missing_callback_stays_uncertain_and_never_relaunches(draft_host, gap):
    client, org, state, body, controls, observations, backend = draft_host
    controls[gap] = False
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert [row[0] for row in observations] == (['running'] if gap == 'callback' else ['running', 'callback_response', 'callback', 'tracker_after_callback']), observations
    assert intent['state'] == 'uncertain'
    assert intent['host_launch_started'] == 1 and intent['host_execution_id'] and intent['session_id']
    assert (intent['final_result_id'] is None) == (gap == 'callback')
    assert org.db.get_task(task_id).status.value == 'in_progress'
    before = _snapshot(org)
    org.workflow_drafts.reconcile(task_id)
    org.orchestrator.run_step(task_id)
    assert backend.calls['launch'] == 1
    assert _snapshot(org) == before
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_registered_session_refuses_before_reservation_and_early_callback_waits_for_handle(draft_host):
    client, org, state, body, controls, observations, backend = draft_host
    controls.update(prelaunch_callback=True, early_callback=True)
    receipt = client.post(BASE, json=body).json()
    org.orchestrator.run_step(receipt['root_task_id'])
    assert ('prelaunch_refused', 409) in observations
    assert any(row[0] == 'early_callback' for row in observations)
    rows = [dict(row) for row in org.db.execute('SELECT * FROM workflow_draft_dispatch_events ORDER BY event_seq')]
    assert [r['event_kind'] for r in rows] == ['admitted', 'claimed', 'launch_reserved', 'callback_recorded', 'running', 'completed']
    assert rows[3]['disposition'] == 'pending_host_acknowledgment'
    assert rows[3]['state_after'] == 'claimed' and rows[3]['session_id']
    assert json.loads(rows[3]['event_bytes'])['after']['host_execution_id'] is None
    assert org.db.get_task(receipt['root_task_id']).status.value == 'completed'
    assert backend.calls['launch'] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('cancel_order', ['before', 'after'])
def test_real_callback_cancel_orders_keep_owned_result_and_wait_for_finalized_containment(draft_host, cancel_order):
    client, org, state, body, controls, observations, backend = draft_host
    controls['cancel_order'] = cancel_order
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    assert ('callback_response', 409 if cancel_order == 'before' else 200) in observations
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'cancelled' and intent['cancellation_requested'] == 1
    assert (intent['final_result_id'] is not None) == (cancel_order == 'after')
    assert org.db.get_task(task_id).status.value == 'cancelled'
    assert org.sessions.get_active(task_id, 'product_lead') is None
    event = json.loads(org.db.execute('SELECT event_bytes FROM workflow_draft_dispatch_events ORDER BY event_seq DESC LIMIT 1').fetchone()[0])
    assert event['terminal_evidence'] == {'host_quiescent': True}
    before = _snapshot(org)
    assert client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={}).status_code == 200
    assert _snapshot(org) == before
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_lost_actual_launch_acknowledgment_survives_cold_reopen_without_second_launch(draft_host):
    from runtime.daemon.__main__ import _sweep_on_startup
    from runtime.daemon.org_state import OrgState
    client, org, state, body, controls, observations, backend = draft_host
    controls.update(early_callback=True, ack=False)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'uncertain' and intent['host_launch_started'] == 1
    assert intent['host_execution_id'] is None and intent['session_id'] and intent['final_result_id'] is not None
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 0
    assert org.db.get_task(task_id).status.value == 'in_progress'
    reopened = OrgState.load(root=org.root, slug=org.slug, settings=org.settings)
    try:
        before = _snapshot(reopened)
        _sweep_on_startup(reopened.db, state.queue, org.slug, reopened.orchestrator)
        reopened.orchestrator.run_step(task_id)
        assert _snapshot(reopened) == before
        assert backend.calls['launch'] == 1
        validate_workflow_schema(reopened.db._conn, expected_org_slug='alpha')
    finally:
        reopened.close()


@pytest.mark.parametrize('seam', ['http', 'database'])
@pytest.mark.parametrize('field,value', [
    ('confidence', 82), ('output_summary', 'changed draft'), ('risks_flagged', ['changed risk']),
    ('output_dir', 'output/foreign'), ('verdict', 'foreign verdict'), ('status', 'failed'),
], ids=['confidence', 'summary', 'risks', 'directory', 'verdict', 'status'])
def test_completion_retry_compares_every_supplied_result_field(draft_host, seam, field, value):
    client, org, state, body, controls, observations, backend = draft_host
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    payload = next(row[1] for row in observations if row[0] == 'callback')
    assert org.sessions.get_active(task_id, 'product_lead') is None
    assert org.db.get_task(task_id).status.value == 'completed'
    before = _snapshot(org)

    def send(candidate):
        if seam == 'http':
            return client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/completion', json=candidate).status_code
        candidate = {**candidate, 'confidence_score': candidate['confidence']}
        del candidate['confidence']
        return org.db.admit_task_completion_callback(task_id=task_id, risks_flagged=[], **candidate)

    assert send(payload) == (200 if seam == 'http' else True)
    assert _snapshot(org) == before
    changed = {**payload, field: value}
    if seam == 'database' and field == 'risks_flagged':
        # This override is the same defaulted field consumed by the HTTP body.
        changed_db = {**changed, 'confidence_score': changed['confidence']}
        del changed_db['confidence']
        accepted = org.db.admit_task_completion_callback(task_id=task_id, **changed_db)
    else:
        accepted = send(changed)
    assert accepted == (409 if seam == 'http' else False)
    assert _snapshot(org) == before
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1


@pytest.mark.parametrize('seam', ['http', 'database'])
@pytest.mark.parametrize('damage', [
    'missing-row', 'changed-row', 'foreign-agent', 'foreign-session',
    'foreign-result-pointer', 'duplicate-row', 'cross-intent', 'cross-f5',
])
def test_completion_retry_requires_original_full_result_and_intent_closure(draft_host, seam, damage):
    client, org, state, body, controls, observations, backend = draft_host
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    payload = next(row[1] for row in observations if row[0] == 'callback')
    original_result = org.db.execute('SELECT final_result_id FROM workflow_draft_dispatch_intents').fetchone()[0]
    assert type(original_result) is int and original_result > 0
    conn = org.db._conn
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        if damage == 'missing-row':
            conn.execute('DELETE FROM task_results WHERE id=?', (original_result,))
        elif damage == 'changed-row':
            conn.execute('UPDATE task_results SET output_summary=? WHERE id=?', ('foreign result bytes', original_result))
        elif damage in {'foreign-agent', 'foreign-session'}:
            column = 'agent' if damage == 'foreign-agent' else 'session_id'
            conn.execute(f'UPDATE task_results SET {column}=? WHERE id=?', ('foreign', original_result))
        elif damage == 'foreign-result-pointer':
            conn.execute('UPDATE workflow_draft_dispatch_intents SET final_result_id=? WHERE id=?',
                         (original_result + 100, receipt['intent_id']))
        elif damage == 'duplicate-row':
            columns = ','.join(row[1] for row in conn.execute('PRAGMA table_info(task_results)') if row[1] != 'id')
            conn.execute(f'INSERT INTO task_results ({columns}) SELECT {columns} FROM task_results WHERE id=?', (original_result,))
        elif damage == 'cross-f5':
            # Incomplete adverse bridge only; no proposed F5 producer or receipt.
            conn.execute('INSERT INTO workflow_request_task_bridges VALUES (?,?,?,?,?,?,?,?,?,?)',
                         ('missing-request', 'missing-operation', receipt['instance_id'], task_id,
                          'product_lead', 1, payload['session_id'], str(original_result), 'completed',
                          datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.execute('PRAGMA foreign_keys=ON')
    if damage == 'cross-intent':
        other = client.post(BASE, json={**body, 'instance_id': 'other-draft', 'operation_key': 'other-activation'})
        assert other.status_code == 201, other.text
        task_id = other.json()['root_task_id']
        assert task_id != receipt['root_task_id']
    before = _snapshot(org)
    if seam == 'http':
        response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/completion', json=payload)
        assert response.status_code == 409, response.text
        assert 'foreign result bytes' not in response.text
    else:
        assert org.db.admit_task_completion_callback(task_id=task_id, agent='product_lead',
            session_id=payload['session_id'], output_summary=payload['output_summary'], confidence_score=83,
            risks_flagged=[]) is False
    assert _snapshot(org) == before
    assert backend.calls['launch'] == 1


@pytest.mark.parametrize('quiescent', [True, False], ids=['exact-quiescence', 'unavailable-quiescence'])
def test_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence(
    draft_host, monkeypatch, quiescent,
):
    from runtime.daemon.org_state import OrgState
    from runtime.workflows.cutover import WorkflowCutoverStore
    client, org, state, body, controls, observations, backend = draft_host
    controls['quiescent'] = quiescent
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    containment_entered = threading.Event()
    containment_release = threading.Event()
    failures = []
    original_finish = backend.finish
    original_controls = org.sessions.iter_task_cancel_controls
    cancel_fenced = threading.Event()
    results = {}

    def finish(*args, **kwargs):
        assert not org.db._conn.in_transaction
        containment_entered.set()
        assert containment_release.wait(10), 'actual containment barrier timed out'
        return original_finish(*args, **kwargs)

    def controls_after_fence(tid):
        assert not org.db._conn.in_transaction and not org.db._lock._is_owned()
        assert org.db.execute('SELECT cancellation_requested FROM workflow_draft_dispatch_intents').fetchone()[0] == 1
        cancel_fenced.set()
        yield from original_controls(tid)

    def launch():
        try:
            org.orchestrator.run_step(task_id)
        except BaseException as exc:
            failures.append(exc)

    def cancel():
        try:
            results['cancel'] = org.workflow_drafts.cancel(task_id)
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(backend, 'finish', finish)
    monkeypatch.setattr(org.sessions, 'iter_task_cancel_controls', controls_after_fence)
    host = threading.Thread(target=launch)
    cancellation = threading.Thread(target=cancel)
    host.start()
    try:
        assert containment_entered.wait(5)
        cancellation.start()
        assert cancel_fenced.wait(5)
        assert org.db.get_task(task_id).status.value == 'in_progress'
        intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
        assert intent['state'] == 'running' and intent['cancellation_requested'] == 1
        assert intent['final_result_id'] is not None and intent['host_execution_id']
        assert org.sessions.get_active(task_id, 'product_lead') == intent['session_id']
        assert backend.calls['finish'] == 0
        cutover = WorkflowCutoverStore(org.db, org_slug=org.slug)
        disabled = cutover.request(action='disable', operation_key='disable-during-finish', expected_generation=4)
        assert disabled['state'] == 'draining'
        assert next(b for b in disabled['blockers'] if b.get('record_id') == intent['id'])['code'] == 'cutover_running_work'
        before = _snapshot(org)
        assert cutover.get()['state'] == 'draining'
        assert _snapshot(org) == before
    finally:
        containment_release.set()
        host.join(5)
        if cancellation.ident is not None:
            cancellation.join(5)
        assert not host.is_alive() and not cancellation.is_alive()
    assert failures == [], failures
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    assert org.db.get_task(task_id).status.value == ('cancelled' if quiescent else 'in_progress')
    current = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert current['state'] == ('cancelled' if quiescent else 'uncertain')
    assert current['final_result_id'] == intent['final_result_id']
    projection = cutover.recover_authorized()
    assert projection['state'] == ('drained' if quiescent else 'draining')
    if not quiescent:
        assert next(b for b in projection['blockers'] if b.get('record_id') == intent['id'])['code'] == 'cutover_uncertain_work'
    reopened = OrgState.load(root=org.root, slug=org.slug, settings=org.settings)
    try:
        before = _snapshot(reopened)
        reopened.workflow_drafts.reconcile(task_id)
        reopened.orchestrator.run_step(task_id)
        response = client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={})
        assert response.status_code == 200, response.text
        assert response.json()['pending'] is (not quiescent)
        assert _snapshot(reopened) == before
        assert backend.calls['launch'] == 1
        validate_workflow_schema(reopened.db._conn, expected_org_slug='alpha')
    finally:
        reopened.close()


@pytest.mark.parametrize('kind', ['failed', 'blocked', 'delegate'])
def test_real_draft_callback_cannot_create_author_retry_or_manager_authority(draft_host, monkeypatch, kind):
    client, org, state, body, controls, observations, backend = draft_host
    controls['callback'] = False
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    if kind == 'blocked':
        from runtime.models import JobRecord
        org.db.insert_job(JobRecord(id='JOB-999999', task_id=task_id, agent_name='product_lead',
            title='owned pending control', rationale='unit negative wait callback', script_text='true',
            interpreter='bash', created_at=datetime.now(timezone.utc).isoformat()))
    original = org.workflow_drafts.observed_handle
    responses = []

    def callback_at_running(tid, agent, session, running):
        original(tid, agent, session, running)
        payload = dict(agent=agent, session_id=session, status='failed' if kind == 'failed' else 'completed',
                       output_summary='bounded document callback', confidence=83)
        if kind == 'blocked':
            payload.update(status='blocked', waiting_on_job_ids=['JOB-999999'])
        if kind == 'delegate':
            payload['decision'] = {'action': 'delegate', 'agent': 'dev_agent', 'prompt': 'unapproved coding task'}
        response = client.post(f'/api/v1/orgs/alpha/tasks/{tid}/completion', json=payload)
        responses.append(response)

    monkeypatch.setattr(org.workflow_drafts, 'observed_handle', callback_at_running)
    org.orchestrator.run_step(task_id)
    assert len(responses) == 1
    assert responses[0].status_code == (200 if kind == 'failed' else 409), responses[0].text
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == ('failed' if kind == 'failed' else 'uncertain')
    assert org.db.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == (1 if kind == 'failed' else 0)
    assert org.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
    assert org.db.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_intents').fetchone()[0] == 1
    assert not org.db.execute('SELECT 1 FROM workflow_submissions').fetchone()
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action IN ('decision','task_failed')", (task_id,)).fetchone()
    before = _snapshot(org)
    org.orchestrator.run_step(task_id)
    assert _snapshot(org) == before and backend.calls['launch'] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('boundary', ['result-insert', 'event', 'commit'])
def test_real_callback_write_failure_rolls_back_result_pointer_and_event_before_retry(
    draft_host, monkeypatch, boundary,
):
    import sqlite3
    client, org, state, body, controls, observations, backend = draft_host
    controls['callback'] = False
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    original_handle = org.workflow_drafts.observed_handle
    connection = org.db._conn
    original_insert = org.db._insert_task_result
    original_event = org.workflow_drafts._event
    failures = []
    replies = []
    rollback_observations = []

    class CommitFault:
        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            if connection.in_transaction and connection.execute('SELECT COUNT(*) FROM task_results').fetchone()[0]:
                failures.append('commit')
                raise sqlite3.OperationalError('controlled callback commit failure')
            connection.commit()

    def insert_then_fault(**kwargs):
        original_insert(**kwargs)
        failures.append('result-insert')
        raise sqlite3.OperationalError('controlled callback result failure')

    def event_then_fault(conn, intent, kind, **kwargs):
        original_event(conn, intent, kind, **kwargs)
        if kind == 'callback_recorded':
            failures.append('event')
            raise sqlite3.OperationalError('controlled callback event failure')

    def callback_at_running(tid, agent, session, running):
        original_handle(tid, agent, session, running)
        before = _snapshot(org)
        payload = dict(agent=agent, session_id=session, status='completed',
                       output_summary='authentic callback after rolled back failure', confidence=83)
        with monkeypatch.context() as scoped:
            if boundary == 'result-insert':
                scoped.setattr(org.db, '_insert_task_result', insert_then_fault)
            elif boundary == 'event':
                scoped.setattr(org.workflow_drafts, '_event', event_then_fault)
            else:
                scoped.setattr(org.db, '_conn', CommitFault())
            with pytest.raises(sqlite3.OperationalError, match='controlled callback'):
                client.post(f'/api/v1/orgs/alpha/tasks/{tid}/completion', json=payload)
        assert failures == [boundary]
        rollback_observations.append((before, _snapshot(org), connection.in_transaction))
        assert not connection.in_transaction
        assert _snapshot(org) == before, 'callback failure left durable result/pointer/event residue'
        assert org.sessions.get_active(tid, agent) == session
        response = client.post(f'/api/v1/orgs/alpha/tasks/{tid}/completion', json=payload)
        assert response.status_code == 200, response.text
        replies.append(response)

    monkeypatch.setattr(org.workflow_drafts, 'observed_handle', callback_at_running)
    org.orchestrator.run_step(task_id)
    assert len(rollback_observations) == 1
    original_rows, after_rows, transaction_open = rollback_observations[0]
    assert transaction_open is False
    assert after_rows == original_rows, 'callback failure left durable result/pointer/event residue'
    assert failures == [boundary] and len(replies) == 1
    assert org.db.get_task(task_id).status.value == 'completed'
    assert org.db.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 1
    assert org.db.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events WHERE event_kind='callback_recorded'").fetchone()[0] == 1
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    validate_workflow_schema(connection, expected_org_slug='alpha')


def test_claimed_no_launch_recovers_same_task_before_one_real_attempt(draft_host):
    from runtime.daemon.__main__ import _sweep_on_startup
    client, org, state, body, controls, observations, backend = draft_host
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    assert state.queue._queue.get_nowait() == ('alpha', task_id, None)
    claim = org.workflow_drafts._on_loop(org.workflow_drafts.claim(task_id))
    assert claim and backend.calls['launch'] == 0
    _sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'queued' and intent['session_id'] is None and intent['host_launch_started'] == 0
    assert org.db.get_task(task_id).status.value == 'pending'
    assert state.queue._queue.qsize() == 1
    queued = state.queue._queue.get_nowait()
    assert queued == ('alpha', task_id, None)
    org.orchestrator.run_step(queued[1])
    assert backend.calls['launch'] == 1 and org.db.get_task(task_id).status.value == 'completed'
    assert org.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
    assert org.db.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_intents').fetchone()[0] == 1
    assert [row[0] for row in org.db.execute('SELECT event_kind FROM workflow_draft_dispatch_events ORDER BY event_seq')][:4] == ['admitted', 'claimed', 'requeued', 'claimed']
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_workflow_rate_limit_cannot_consume_ordinary_supervisor_retry_budget(draft_host):
    client, org, state, body, controls, observations, backend = draft_host
    controls.update(callback=False, rate_limited=True)
    receipt = client.post(BASE, json=body).json()
    org.orchestrator.run_step(receipt['root_task_id'])
    assert len([item for item in observations if item[0] == 'running']) == 1
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    assert backend.calls['prepare'] == 1
    assert org.db.execute('SELECT state FROM workflow_draft_dispatch_intents').fetchone()[0] == 'uncertain'
    assert not org.db.execute('SELECT 1 FROM task_results').fetchone()


@pytest.mark.parametrize('consumer', ['reaper', 'portability'])
def test_owned_pid_ttl_consumers_cannot_terminalize_uncertain_draft(draft_host, consumer):
    from runtime.daemon.zombie_reaper import _sweep_org_zombies
    client, org, state, body, controls, observations, backend = draft_host
    controls.update(callback=False, quiescent=False)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    # Real on_started published this actual subprocess PID. The adverse
    # telemetry timestamps make ALL ordinary TTL predicates hold; they do
    # not supply any host-quiescence proof for the workflow owner.
    assert org.db.get_task(task_id).executor_pid == backend.last_running.root_pid
    assert backend.last_running.process.poll() is not None
    old = datetime.now(timezone.utc) - timedelta(days=1)
    org.db.update_task(task_id, last_heartbeat=old, zombie_flagged_at=old)
    before = _snapshot(org)
    if consumer == 'reaper':
        _sweep_org_zombies(org.db, now=datetime.now(timezone.utc), uptime=3600,
                           warm_up_seconds=0, orchestrator=org.orchestrator)
        assert org.db.get_task(task_id).status.value == 'in_progress'
        assert _snapshot(org) == before
    else:
        response = client.post('/api/v1/orgs/alpha/reconcile-portability',
                               json={'candidate_task_id': task_id, 'disposition': 'cancel'})
        assert org.db.get_task(task_id).status.value == 'in_progress'
        assert response.status_code == 200 and response.json()['pending'], response.text
        assert response.json()['cancellation_requested'] is True
    assert org.db.get_task(task_id).status.value == 'in_progress'
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'uncertain'
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action='zombie_cancelled'", (task_id,)).fetchone()
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('consumer', ['projection', 'claim'])
def test_real_active_author_keeps_other_admitted_draft_pending(draft_host, monkeypatch, consumer):
    from runtime.workflows.draft_dispatch import DraftOwnershipError
    client, org, state, body, controls, observations, backend = draft_host
    controls['callback'] = False
    running_response = client.post(BASE, json=body)
    assert running_response.status_code == 201, running_response.text
    running_id = running_response.json()['root_task_id']
    waiting_body = {**body, 'instance_id': 'another-design', 'operation_key': 'another-activation'}
    waiting = client.post(BASE, json=waiting_body)
    assert waiting.status_code == 201, waiting.text
    waiting_receipt = waiting.json()
    launched = threading.Event()
    release = threading.Event()
    errors = []
    real_launch = backend.launch

    def pause_actual_launch(pending, spec):
        running = real_launch(pending, spec)
        launched.set()
        assert release.wait(5), 'actual launch barrier timed out'
        return running

    monkeypatch.setattr(backend, 'launch', pause_actual_launch)

    def run_actual():
        try:
            org.orchestrator.run_step(running_id)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_actual)
    worker.start()
    try:
        assert launched.wait(5)
        active = list(org.sessions.iter_active())
        assert len(active) == 1 and active[0][:2] == (running_id, 'product_lead')
        assert org.db.get_task(running_id).current_session_id == active[0][2]
        before = _snapshot(org)
        if consumer == 'projection':
            shown = client.get(f"{BASE}/{waiting_receipt['activation_id']}")
            assert shown.status_code == 200, shown.text
            assert shown.json()['current_eligibility'] == {
                'eligible': False, 'blockers': ['workflow_activation_author_pending'],
            }
            assert shown.json()['pending'] and not shown.json()['execution_started']
            assert shown.json()['root_task_id'] == waiting_receipt['root_task_id']
        else:
            with pytest.raises(DraftOwnershipError, match='workflow_activation_author_pending'):
                org.workflow_drafts._on_loop(org.workflow_drafts.claim(waiting_receipt['root_task_id']))
        assert _snapshot(org) == before
        assert backend.calls['launch'] == 1
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()
    assert errors == [], errors
    assert org.db.get_task(waiting_receipt['root_task_id']).status.value == 'pending'
    assert org.db.execute('SELECT state FROM workflow_draft_dispatch_intents WHERE task_id=?',
                          (waiting_receipt['root_task_id'],)).fetchone()[0] == 'queued'
    validate_workflow_schema(org.db._conn, expected_org_slug=org.slug)


@pytest.mark.parametrize('mismatch', ['same-value-foreign-request', 'retry-attempt'])
def test_finalized_outcome_requires_exact_original_host_request(draft_host, monkeypatch, mismatch):
    client, org, state, body, controls, observations, backend = draft_host
    original = org.workflow_drafts.terminal
    finalized = []

    def foreign_outcome(task_id, agent, session_id, outcome, **kwargs):
        if outcome is not None:
            finalized.append(outcome)
            outcome = (replace(outcome, request=replace(outcome.request))
                       if mismatch == 'same-value-foreign-request' else replace(outcome, attempt=1))
        return original(task_id, agent, session_id, outcome, **kwargs)

    monkeypatch.setattr(org.workflow_drafts, 'terminal', foreign_outcome)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    assert len(finalized) == 1 and finalized[0].receipt.quiescent is True
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'uncertain', 'foreign terminal outcome completed the draft'
    assert type(intent['final_result_id']) is int
    assert org.db.get_task(task_id).status.value == 'in_progress'
    assert not org.db.execute("SELECT 1 FROM workflow_draft_dispatch_events WHERE event_kind='completed'").fetchone()
    shown = client.get(f"{BASE}/{receipt['activation_id']}")
    assert shown.status_code == 200 and shown.json()['reconciliation_required'] is True
    before = _snapshot(org)
    org.orchestrator.run_step(task_id)
    assert _snapshot(org) == before and backend.calls['launch'] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_observed_handle_requires_exact_durable_host_effect(draft_host, monkeypatch):
    client, org, state, body, controls, observations, backend = draft_host
    original = org.workflow_drafts.observed_handle
    handles = []

    def foreign_handle(task_id, agent, session_id, running):
        handles.append(running)
        return original(task_id, agent, session_id, replace(running, request_id='foreign-host-effect'))

    monkeypatch.setattr(org.workflow_drafts, 'observed_handle', foreign_handle)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    assert len(handles) == 1 and handles[0].process.poll() is not None
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'uncertain', 'foreign handle acknowledged the draft host effect'
    assert intent['host_execution_id'] is None and intent['final_result_id'] is None
    assert observations == []
    assert org.db.get_task(task_id).status.value == 'in_progress'
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_duplicate_genuine_finalizer_is_read_only_before_tracker_release(draft_host, monkeypatch):
    client, org, state, body, controls, observations, backend = draft_host
    original = org.workflow_drafts.terminal
    finalized = []
    replayed_snapshots = []

    def duplicate(task_id, agent, session_id, outcome, **kwargs):
        original(task_id, agent, session_id, outcome, **kwargs)
        if outcome is not None:
            assert org.sessions.get_active(task_id, agent) == session_id
            before = _snapshot(org)
            original(task_id, agent, session_id, outcome, **kwargs)
            after = _snapshot(org)
            replayed_snapshots.append((before, after))
            assert after == before, 'duplicate finalizer wrote another terminal event'
            finalized.append(outcome)

    monkeypatch.setattr(org.workflow_drafts, 'terminal', duplicate)
    receipt = client.post(BASE, json=body).json()
    org.orchestrator.run_step(receipt['root_task_id'])
    assert len(replayed_snapshots) == 1
    assert replayed_snapshots[0][0] == replayed_snapshots[0][1], 'duplicate finalizer changed retained task/domain rows'
    assert len(finalized) == 1
    assert org.db.get_task(receipt['root_task_id']).status.value == 'completed'
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    assert org.db.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events WHERE event_kind='completed'").fetchone()[0] == 1
    assert org.sessions.get_active(receipt['root_task_id'], 'product_lead') is None


def test_actual_prelaunch_work_hour_defers_same_intent_before_external_launch(draft_host, monkeypatch):
    from runtime.models import WorkHourRecord, WorkHourMode, WorkHourStatus
    client, org, state, body, controls, observations, backend = draft_host
    original = org.workflow_drafts._reserve_launch
    registered = []
    refusals = []

    async def occupied_before_reservation(task_id, agent, session_id):
        assert org.sessions.get_active(task_id, agent) == session_id
        assert not org.db._conn.in_transaction
        hour_id = org.db.work_hours.next_id()
        org.db.work_hours.insert(WorkHourRecord(id=hour_id, agent_name=agent,
            local_date='2026-10-06', slot='04:30', mode=WorkHourMode.CONTINUOUS,
            scheduled_for=datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)))
        org.db.work_hours.update(hour_id, status=WorkHourStatus.RUNNING,
                                started_at=datetime.now(timezone.utc))
        registered.append((hour_id, session_id))
        try:
            return await original(task_id, agent, session_id)
        except Exception as exc:
            refusals.append((type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', occupied_before_reservation)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    org.orchestrator.run_step(task_id)
    assert len(registered) == 1
    assert refusals == [('DraftOwnershipError', 'workflow_activation_author_pending')], refusals
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == 'queued', 'busy prelaunch terminalized the authentic drafting attempt'
    assert intent['id'] == receipt['intent_id'] and intent['task_id'] == task_id
    assert intent['attempt_sequence'] == intent['assignment_generation'] == 1
    assert intent['session_id'] is None and intent['host_launch_started'] == 0
    assert intent['host_execution_id'] is None and intent['final_result_id'] is None
    assert org.db.get_task(task_id).status.value == 'pending'
    assert org.db.get_task(task_id).current_session_id is None
    assert org.sessions.get_active(task_id, 'product_lead') is None
    assert backend.calls['launch'] == 0 and backend.calls['finish'] == 0
    assert not org.db.execute('SELECT 1 FROM task_results').fetchone()
    assert [r[0] for r in org.db.execute('SELECT event_kind FROM workflow_draft_dispatch_events ORDER BY event_seq')] == ['admitted', 'claimed', 'requeued']
    assert observations == []
    before = _snapshot(org)
    shown = client.get(f"{BASE}/{receipt['activation_id']}").json()
    assert shown['pending'] and not shown['execution_started']
    assert shown['current_eligibility']['blockers'] == ['workflow_activation_author_pending']
    assert _snapshot(org) == before
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('first_writer', ['callback', 'cancel'])
def test_callback_cancel_writer_contenders_commit_one_owned_order(draft_host, monkeypatch, first_writer):
    import sqlite3
    client, org, state, body, controls, observations, backend = draft_host
    controls['callback'] = False
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    host_ready = threading.Event()
    host_release = threading.Event()
    writer_entered = threading.Event()
    writer_release = threading.Event()
    contender_entered = threading.Event()
    failures = []
    results = {}
    original_handle = org.workflow_drafts.observed_handle
    original_event = org.workflow_drafts._event
    original_controls = org.sessions.iter_task_cancel_controls
    real_lock = org.db._lock

    def hold_real_handle(*args):
        original_handle(*args)
        host_ready.set()
        assert host_release.wait(8), 'host acknowledgment contention barrier timed out'

    def hold_first_event(conn, intent, kind, **kwargs):
        original_event(conn, intent, kind, **kwargs)
        if kind == ('callback_recorded' if first_writer == 'callback' else 'cancel_requested'):
            writer_entered.set()
            assert writer_release.wait(5), 'callback/cancel writer barrier timed out'

    def observe_controls(tid):
        assert not org.db._conn.in_transaction and not real_lock._is_owned()
        assert org.db.execute('SELECT cancellation_requested FROM workflow_draft_dispatch_intents WHERE task_id=?',
                              (tid,)).fetchone()[0] == 1
        for agent, control in original_controls(tid):
            yield agent, control

    def launch():
        try:
            org.orchestrator.run_step(task_id)
        except BaseException as exc:
            failures.append(exc)

    def callback():
        try:
            async def admitted():
                async with org.db_lock:
                    with org.sessions.binding_lease(task_id, 'product_lead'):
                        session = org.sessions.get_active(task_id, 'product_lead')
                        results['session'] = session
                        results['callback'] = org.db.admit_task_completion_callback(
                            task_id=task_id, agent='product_lead', session_id=session,
                            status='completed', output_summary='concurrent authentic draft',
                            confidence_score=83, risks_flagged=[],
                        )
            org.workflow_drafts._on_loop(admitted())
        except BaseException as exc:
            failures.append(exc)

    def cancel():
        try:
            results['cancel'] = org.workflow_drafts.cancel(task_id)
        except BaseException as exc:
            failures.append(exc)

    callback_thread = threading.Thread(target=callback)
    cancel_thread = threading.Thread(target=cancel)
    first, second = ((callback_thread, cancel_thread) if first_writer == 'callback'
                     else (cancel_thread, callback_thread))

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            if threading.current_thread() is second or (first_writer == 'cancel' and threading.current_thread() is loop_thread):
                contender_entered.set()
            return real_lock.acquire(*args, **kwargs)

        def release(self):
            return real_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

        def __getattr__(self, name):
            return getattr(real_lock, name)

    # The callback executes on the existing real orchestrator loop; observe
    # its actual lock acquisition, not the launching caller thread's wait.
    loop_thread = next(t for t in threading.enumerate() if t.ident == org.orchestrator._main_loop._thread_id)
    monkeypatch.setattr(org.db, '_lock', ObservedLock())
    monkeypatch.setattr(org.workflow_drafts, 'observed_handle', hold_real_handle)
    monkeypatch.setattr(org.workflow_drafts, '_event', hold_first_event)
    monkeypatch.setattr(org.sessions, 'iter_task_cancel_controls', observe_controls)
    host_thread = threading.Thread(target=launch)
    host_thread.start()
    try:
        assert host_ready.wait(5)
        first.start()
        assert writer_entered.wait(5)
        contender_entered.clear()
        second.start()
        assert contender_entered.wait(5), 'second writer never contended for actual Database ownership'
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT state,cancellation_requested,final_result_id FROM workflow_draft_dispatch_intents').fetchone() == ('running', 0, None)
            assert reader.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0
            assert [r[0] for r in reader.execute('SELECT event_kind FROM workflow_draft_dispatch_events ORDER BY event_seq')] == ['admitted', 'claimed', 'launch_reserved', 'running']
        writer_release.set()
        first.join(5)
        second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert failures == [], failures
        assert results['callback'] is (first_writer == 'callback')
        assert results['cancel']['pending'] is True and results['cancel']['cancellation_requested'] is True
        assert org.db.get_task(task_id).status.value == 'in_progress'
        assert org.db.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == (1 if first_writer == 'callback' else 0)
        # Real disable fences NEW/prelaunch work, while this genuine admitted
        # handle stays cancellation-pending until finalized containment.
        disabled = client.post('/api/v1/orgs/alpha/workflows/cutover/requests',
                               json={'operation_key': 'disable-during-containment', 'action': 'disable',
                                     'expected_generation': 4})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()['state'] == 'draining'
        blocker = next(b for b in disabled.json()['blockers'] if b.get('record_id') == receipt['intent_id'])
        assert blocker['code'] == 'cutover_running_work'
        assert org.sessions.get_active(task_id, 'product_lead') == results['session']
        before = _snapshot(org)
        if first_writer == 'callback':
            assert org.db.admit_task_completion_callback(task_id=task_id, agent='product_lead',
                session_id=results['session'], status='completed', output_summary='concurrent authentic draft',
                confidence_score=83, risks_flagged=[])
        assert org.workflow_drafts.cancel(task_id)['pending'] is True
        assert _snapshot(org) == before
    finally:
        writer_release.set()
        host_release.set()
        for worker in (first, second, host_thread):
            if worker.ident is not None:
                worker.join(5)
                assert not worker.is_alive()
    assert failures == [], failures
    assert org.db.get_task(task_id).status.value == 'cancelled'
    rows = [r[0] for r in org.db.execute('SELECT event_kind FROM workflow_draft_dispatch_events ORDER BY event_seq')]
    assert rows == (['admitted', 'claimed', 'launch_reserved', 'running', 'callback_recorded', 'cancel_requested', 'cancelled']
                    if first_writer == 'callback' else ['admitted', 'claimed', 'launch_reserved', 'running', 'cancel_requested', 'cancelled'])
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    projection = client.get('/api/v1/orgs/alpha/workflows/cutover')
    assert projection.status_code == 200 and projection.json()['blockers'] == [], projection.text
    # Projection is read-only: only the actual reconciler moves to drained.
    from runtime.workflows.cutover import WorkflowCutoverStore
    assert WorkflowCutoverStore(org.db, org_slug=org.slug).recover_authorized()['state'] == 'drained'
    assert not org.db.execute('SELECT 1 FROM workflow_submissions').fetchone()
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action IN ('decision','task_failed','daemon_restart_failure')", (task_id,)).fetchone()
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_lost_notification_cold_discovery_and_duplicate_enqueue_keep_original_attempt(draft_host, monkeypatch):
    from runtime.daemon.__main__ import _sweep_on_startup
    from runtime.daemon.org_state import OrgState
    client, org, state, body, controls, observations, backend = draft_host
    original_enqueue = state.queue.enqueue

    def lost_notification(*args, **kwargs):
        raise RuntimeError('test-side lost postcommit queue notification')

    monkeypatch.setattr(state.queue, 'enqueue', lost_notification)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    task_id = receipt['root_task_id']
    assert state.queue._queue.qsize() == 0 and backend.calls['launch'] == 0
    before = _snapshot(org)
    replay = client.post(BASE, json=body)
    assert replay.status_code == 200 and replay.json()['root_task_id'] == task_id
    assert _snapshot(org) == before and state.queue._queue.qsize() == 0
    monkeypatch.setattr(state.queue, 'enqueue', original_enqueue)
    reopened = OrgState.load(slug=org.slug, root=org.root, settings=org.settings)
    try:
        with state.profile_coordinator.dynamic_org_attachment(reopened):
            pass
        for _ in range(2):
            _sweep_on_startup(reopened.db, state.queue, org.slug, reopened.orchestrator)
        assert state.queue._queue.qsize() == 1, 'durable lost notification was not rediscovered exactly once'
        assert state.queue._queue.get_nowait() == ('alpha', task_id, None)
        assert _snapshot(reopened) == before
    finally:
        reopened.close()
    # Duplicate actual worker notifications race through the same durable
    # claim owner; a completed original task never creates another intent.
    org.orchestrator.run_step(task_id)
    after = _snapshot(org)
    org.orchestrator.run_step(task_id)
    assert _snapshot(org) == after
    assert backend.calls['launch'] == 1 and backend.calls['finish'] == 1
    assert org.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
    assert org.db.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_intents').fetchone()[0] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def test_independent_dispatch_claim_observes_busy_then_same_original_cas(activation_org, monkeypatch):
    import sqlite3
    from runtime.daemon.org_state import OrgState
    from runtime.workflows.authority import WorkflowAuthorityError
    client, org, state, body = activation_org
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    reopened = OrgState.load(slug=org.slug, root=org.root, settings=org.settings)
    lease_held = threading.Event()
    lease_release = threading.Event()
    results = []
    errors = []
    original_release = org.workflow_authority._release_lease

    def hold_committed_lease(owner):
        lease_held.set()
        assert lease_release.wait(5), 'independent claim lease barrier timed out'
        original_release(owner)

    def first_claim():
        try:
            results.append(asyncio.run(org.workflow_drafts.claim(task_id)))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=first_claim)
    try:
        with state.profile_coordinator.dynamic_org_attachment(reopened):
            pass
        monkeypatch.setattr(org.workflow_authority, '_release_lease', hold_committed_lease)
        thread.start()
        assert lease_held.wait(5)
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT state,host_launch_started,session_id,final_result_id FROM workflow_draft_dispatch_intents').fetchone() == ('claimed', 0, None, None)
            assert reader.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0] == 1
        before = _snapshot(reopened)
        with pytest.raises(WorkflowAuthorityError, match='publication_lease_busy'):
            asyncio.run(reopened.workflow_drafts.claim(task_id))
        assert _snapshot(reopened) == before
        lease_release.set()
        thread.join(5)
        assert not thread.is_alive() and errors == [] and len(results) == 1 and results[0]
        before = _snapshot(reopened)
        assert asyncio.run(reopened.workflow_drafts.claim(task_id)) is None
        assert _snapshot(reopened) == before
        assert reopened.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
        assert reopened.db.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events WHERE event_kind='claimed'").fetchone()[0] == 1
        assert reopened.db.execute('SELECT claim_token FROM workflow_draft_dispatch_intents').fetchone()[0] == results[0]
        validate_workflow_schema(reopened.db._conn, expected_org_slug='alpha')
    finally:
        lease_release.set()
        if thread.ident is not None:
            thread.join(5)
            assert not thread.is_alive()
        reopened.close()
