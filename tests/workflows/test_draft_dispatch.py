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
from tests.daemon.test_workflow_activation_routes import BASE, activation_org, generic_activation_org, _snapshot, _assert_activation_org_layout
from tests.workflows.test_activation_store import activation_profile, _select_activation_profile


@pytest.fixture
def draft_host(request, activation_org, monkeypatch):
    """A bounded actual subprocess at the existing supervisor backend boundary.

    This is a unit causal control, never a hosted/provider acceptance receipt.
    Canonical org/template/cutover/admission and all draft producers stay real.
    """
    from runtime.platform.session_backend import RunningHandle, LaunchSpec
    from runtime.orchestrator.executors import ExecutorResult
    from tests.test_host_supervisor_lifecycle import FakeBackend, make_supervisor

    client, org, state, body = activation_org
    expected_layout = getattr(request.node, 'callspec', None)
    expected_layout = expected_layout.params.get('activation_org', 'G') if expected_layout else 'G'
    _assert_activation_org_layout(org.db.path, expected_layout, 'before host setup')
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


@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
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
@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
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
@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
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
                           warm_up_seconds=0, orchestrator=org.orchestrator, queue=state.queue)
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
@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
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


@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
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


def _live_periodic_tick(state, monkeypatch):
    from runtime.daemon import zombie_reaper
    original_sleep = asyncio.sleep
    async def end_after_tick(interval):
        if interval == 97:
            raise asyncio.CancelledError
        return await original_sleep(interval)
    with monkeypatch.context() as patch:
        patch.setattr(zombie_reaper, 'HEARTBEAT_INTERVAL_SECONDS', 0)
        patch.setattr(zombie_reaper.asyncio, 'sleep', end_after_tick)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(zombie_reaper.zombie_reaper_loop(state, interval_seconds=97))


@pytest.mark.parametrize('notification', ['initial-capacity', 'prelaunch-capacity', 'lost'])
def test_live_periodic_discovery_recovers_same_eligible_intent_after_notification_loss(
    draft_host, monkeypatch, notification,
):
    from runtime.models import WorkHourRecord, WorkHourMode, WorkHourStatus
    client, org, state, body, controls, observations, backend = draft_host
    original_reserve = org.workflow_drafts._reserve_launch
    original_enqueue = state.queue.enqueue
    occupied = []
    def occupy(agent):
        hour_id = org.db.work_hours.next_id()
        org.db.work_hours.insert(WorkHourRecord(id=hour_id, agent_name=agent,
            local_date='2026-10-06', slot='04:30', mode=WorkHourMode.CONTINUOUS,
            scheduled_for=datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)))
        org.db.work_hours.update(hour_id, status=WorkHourStatus.RUNNING,
                                started_at=datetime.now(timezone.utc))
        occupied.append(hour_id)
    async def busy_prelaunch(task_id, agent, session):
        occupy(agent)
        return await original_reserve(task_id, agent, session)
    def lost(*args, **kwargs):
        raise RuntimeError('lost internal queue notification')
    if notification == 'lost':
        monkeypatch.setattr(state.queue, 'enqueue', lost)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    if notification != 'lost':
        assert state.queue._queue.get_nowait() == ('alpha', task_id, None)
        state.queue._queue.task_done()
        if notification == 'initial-capacity':
            occupy('product_lead')
        else:
            monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', busy_prelaunch)
        org.orchestrator.run_step(task_id)
        assert state.queue._queue.qsize() == 0, 'capacity refusal immediately requeued a notification'
        assert backend.calls['launch'] == 0
        before = _snapshot(org)
        _live_periodic_tick(state, monkeypatch)
        assert _snapshot(org) == before
        assert state.queue._queue.qsize() == 0, 'ineligible draft was busy-loop requeued'
        for hour_id in occupied:
            org.db.work_hours.update(hour_id, status=WorkHourStatus.COMPLETED,
                                    ended_at=datetime.now(timezone.utc))
        monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', original_reserve)
    monkeypatch.setattr(state.queue, 'enqueue', original_enqueue)
    assert org.sessions.get_active(task_id, 'product_lead') is None
    before = _snapshot(org)
    assert client.post(BASE, json=body).status_code == 200
    assert _snapshot(org) == before and state.queue._queue.qsize() == 0
    assert client.get(f"{BASE}/{receipt['activation_id']}").json()['current_eligibility']['eligible']
    original_dedup = state.queue.enqueue_if_absent
    notified = []
    lease_violations = []
    def outside_ownership(slug, tid):
        if org.db._conn.in_transaction or org.db._lock._is_owned():
            lease_violations.append('SQLite ownership at notification')
        if org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone():
            lease_violations.append('org publication lease at notification')
        assert not org.db._conn.in_transaction and not org.db._lock._is_owned()
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
        notified.append((slug, tid))
        return original_dedup(slug, tid)
    monkeypatch.setattr(state.queue, 'enqueue_if_absent', outside_ownership)
    _live_periodic_tick(state, monkeypatch)
    _live_periodic_tick(state, monkeypatch)
    assert lease_violations == [], 'notification ran under durable ownership'
    assert state.queue._queue.qsize() == 1, 'live discovery must leave exactly one deduplicated notification'
    assert notified == [('alpha', task_id), ('alpha', task_id)]
    assert _snapshot(org) == before, 'discovery changed durable task/intent/generation'
    assert state.queue._queue.get_nowait() == ('alpha', task_id, None)
    state.queue._queue.task_done()
    # Even genuine duplicate deliveries cannot claim this attempt twice.
    org.orchestrator.run_step(task_id)
    org.orchestrator.run_step(task_id)
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['id'] == receipt['intent_id'] and intent['task_id'] == task_id
    assert intent['attempt_sequence'] == intent['assignment_generation'] == 1
    assert intent['state'] == 'completed'
    assert len(org.db.list_tasks()) == 1 and backend.calls['launch'] == 1
    assert org.sessions.get_active(task_id, 'product_lead') is None
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


def _independent_profile_probe(coordinator, name, *, busy):
    source = """
import sys
from pathlib import Path
from runtime.workflows.profile_coordinator import ProfileCoordinator,ProfileCoordinatorError
c=ProfileCoordinator(daemon_home=Path(sys.argv[1]),orgs={})
try:
    with c.profile_read(sys.argv[2]): print('ACQUIRED')
except ProfileCoordinatorError as e:
    print(e.code)
"""
    result = subprocess.run([sys.executable, '-c', source, str(coordinator._daemon_home), name],
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ('profile_coordinator_busy' if busy else 'ACQUIRED'), result.stdout


@pytest.mark.parametrize('writer', ['profile', 'disable'])
@pytest.mark.parametrize('first_owner', ['writer', 'prelaunch'])
def test_actual_profile_or_disable_writer_and_prelaunch_reservation_both_winners(
    draft_host, activation_profile, monkeypatch, writer, first_owner,
):
    import sqlite3
    from contextlib import contextmanager
    from runtime.workflows.cutover import WorkflowCutoverStore
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError
    client, org, state, body, controls, observations, backend = draft_host
    profile_client, profile_org, profile_state, profile_body, name = activation_profile
    assert org is profile_org and state is profile_state
    _select_activation_profile(client, org, body, name)
    coordinator = state.profile_coordinator
    store = WorkflowCutoverStore(org.db, org_slug=org.slug)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    capture_ready = threading.Event()
    capture_release = threading.Event()
    reserved = threading.Event()
    reserve_release = threading.Event()
    writer_entered = threading.Event()
    writer_release = threading.Event()
    writer_done = threading.Event()
    failures, refusals, no_write, order = [], [], [], []
    original_capture = org.workflow_authority.capture_admission
    original_reserve = org.workflow_drafts._reserve_launch
    original_event = org.workflow_drafts._event
    original_advance = store._advance
    original_launch = backend.launch
    prelaunch_capture = []
    phase = threading.local()
    lease_order = []
    original_profile = coordinator.profile_read
    original_acquire = org.workflow_authority._acquire_lease
    original_release = org.workflow_authority._release_lease
    original_transaction = org.workflow_authority._admission_transaction

    @contextmanager
    def profile_lease(profile):
        active = getattr(phase, 'prelaunch', False)
        with original_profile(profile):
            if active: lease_order.append('profile')
            try:
                yield
            finally:
                if active: lease_order.append('profile-release')

    def org_lease(owner):
        if getattr(phase, 'prelaunch', False):
            assert lease_order[-1] == 'profile', 'org ownership preceded selected profile ownership'
            assert not org.db._conn.in_transaction
        original_acquire(owner)
        if getattr(phase, 'prelaunch', False): lease_order.append('org')

    def org_release(owner):
        original_release(owner)
        if getattr(phase, 'prelaunch', False):
            assert not org.db._conn.in_transaction
            lease_order.append('org-release')

    @contextmanager
    def transaction():
        active = getattr(phase, 'prelaunch', False)
        try:
            with original_transaction() as conn:
                if active:
                    assert lease_order[-1] == 'org'
                    assert org.workflow_authority._publisher_lock._is_owned()
                    assert org.db._lock._is_owned() and conn.in_transaction
                    assert conn.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
                    lease_order.append('sqlite')
                yield conn
        finally:
            if active: lease_order.append('sqlite-release')

    def capture():
        value = original_capture()
        task = org.db.get_task(task_id)
        if task.current_session_id and first_owner == 'writer':
            assert not org.db._conn.in_transaction
            prelaunch_capture.append(value)
            capture_ready.set()
            assert capture_release.wait(12), 'prelaunch capture barrier timed out'
            no_write.append(_snapshot(org))
        return value

    async def reserve(tid, agent, session):
        assert org.sessions.get_active(tid, agent) == session
        phase.prelaunch = True
        try:
            return await original_reserve(tid, agent, session)
        except Exception as exc:
            refusals.append(getattr(exc, 'code', str(exc)))
            assert no_write and _snapshot(org) == no_write[-1], 'refused prelaunch wrote durable state'
            assert not org.db._conn.in_transaction
            assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
            raise
        finally:
            phase.prelaunch = False

    def event(conn, intent, kind, **kwargs):
        original_event(conn, intent, kind, **kwargs)
        if kind == 'launch_reserved':
            order.append('prelaunch')
            reserved.set()
            if first_owner == 'prelaunch':
                assert reserve_release.wait(12), 'reservation commit barrier timed out'

    def advance(conn, marker, events, **kwargs):
        original_advance(conn, marker, events, **kwargs)
        if first_owner == 'writer' and marker['generation'] == 4:
            writer_entered.set()
            assert writer_release.wait(12), 'disable writer barrier timed out'

    def external_launch(pending, spec):
        if writer == 'disable' and first_owner == 'prelaunch':
            assert writer_done.wait(8), 'competing disable did not finish after reservation commit'
        assert not org.db._conn.in_transaction and not org.db._lock._is_owned()
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
        _independent_profile_probe(coordinator, name, busy=False)
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT host_launch_started,session_id FROM workflow_draft_dispatch_intents').fetchone() == (1, org.db.get_task(task_id).current_session_id)
        order.append('host')
        return original_launch(pending, spec)

    def run():
        try:
            org.orchestrator.run_step(task_id)
        except BaseException as exc:
            failures.append(exc)

    def write():
        try:
            if writer == 'profile':
                with coordinator.operation([name], operation_kind='rebind', publisher='actual-prelaunch-writer'):
                    writer_entered.set()
                    if first_owner == 'writer':
                        assert writer_release.wait(12), 'profile writer barrier timed out'
                order.append('writer')
            else:
                result = store.request(action='disable', operation_key='actual-prelaunch-disable', expected_generation=4)
                assert result['state'] in {'disable_requested', 'draining', 'drained'}
                order.append('writer')
        except BaseException as exc:
            failures.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(coordinator, 'profile_read', profile_lease)
    monkeypatch.setattr(org.workflow_authority, '_acquire_lease', org_lease)
    monkeypatch.setattr(org.workflow_authority, '_release_lease', org_release)
    monkeypatch.setattr(org.workflow_authority, '_admission_transaction', transaction)
    monkeypatch.setattr(org.workflow_authority, 'capture_admission', capture)
    monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', reserve)
    monkeypatch.setattr(org.workflow_drafts, '_event', event)
    monkeypatch.setattr(store, '_advance', advance)
    monkeypatch.setattr(backend, 'launch', external_launch)
    host_thread = threading.Thread(target=run)
    writer_thread = threading.Thread(target=write)
    host_thread.start()
    try:
        if first_owner == 'writer':
            assert capture_ready.wait(8)
            writer_thread.start()
            assert writer_entered.wait(8)
            # The authentic writer owns a real selected flock or SQLite
            # transaction; a separate process/reader observes exclusion and
            # the previously committed no-launch claim, never a fake gate.
            _independent_profile_probe(coordinator, name, busy=(writer == 'profile'))
            with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
                assert reader.execute('SELECT state,host_launch_started FROM workflow_draft_dispatch_intents').fetchone() == ('claimed', 0)
                assert reader.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events WHERE event_kind='launch_reserved'").fetchone()[0] == 0
            writer_release.set()
            writer_thread.join(8)
            assert not writer_thread.is_alive() and failures == [], failures
            capture_release.set()
        else:
            assert reserved.wait(8)
            _independent_profile_probe(coordinator, name, busy=True)
            with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
                assert reader.execute('SELECT state,host_launch_started FROM workflow_draft_dispatch_intents').fetchone() == ('claimed', 0)
                assert reader.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events WHERE event_kind='launch_reserved'").fetchone()[0] == 0
            assert backend.calls['launch'] == 0
            if writer == 'profile':
                with pytest.raises(ProfileCoordinatorError, match='profile_coordinator_busy'):
                    with coordinator.operation([name], operation_kind='rebind', publisher='actual-prelaunch-loser'):
                        pytest.fail('profile writer entered beneath prelaunch reservation')
                reserve_release.set()
                host_thread.join(8)
                assert not host_thread.is_alive() and failures == [], failures
                # Fresh supported retry is legal after all selected/org/writer
                # ownership is released; it does not retroactively cancel.
                writer_thread.start()
            else:
                real_lock = org.db._lock
                attempted = threading.Event()
                class ObservedLock:
                    def acquire(self, *args, **kwargs):
                        if threading.current_thread() is writer_thread:
                            attempted.set()
                        return real_lock.acquire(*args, **kwargs)
                    def release(self): return real_lock.release()
                    def __enter__(self): self.acquire(); return self
                    def __exit__(self, *args): self.release()
                    def __getattr__(self, key): return getattr(real_lock, key)
                monkeypatch.setattr(org.db, '_lock', ObservedLock())
                writer_thread.start()
                assert attempted.wait(8), 'disable never contended for actual SQLite owner'
                assert not writer_entered.is_set() and backend.calls['launch'] == 0
                reserve_release.set()
        host_thread.join(10)
        writer_thread.join(10)
        assert not host_thread.is_alive() and not writer_thread.is_alive()
        assert failures == [], failures
    finally:
        capture_release.set(); writer_release.set(); reserve_release.set()
        host_thread.join(10)
        if writer_thread.ident is not None: writer_thread.join(10)
        assert not host_thread.is_alive() and not writer_thread.is_alive()
    _independent_profile_probe(coordinator, name, busy=False)
    assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
    assert org.sessions.get_active(task_id, 'product_lead') is None
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['id'] == receipt['intent_id'] and intent['attempt_sequence'] == intent['assignment_generation'] == 1
    if first_owner == 'writer':
        assert len(prelaunch_capture) == 1 and len(refusals) == 1, refusals
        assert refusals[0] in {'workflow_activation_authority_stale', 'workflow_new_runs_disabled'}
        assert not reserved.is_set() and backend.calls['launch'] == 0
        assert intent['host_launch_started'] == 0 and intent['host_execution_id'] is None
        assert not org.db.execute("SELECT 1 FROM workflow_draft_dispatch_events WHERE event_kind='launch_reserved'").fetchone()
    else:
        assert refusals == [] and backend.calls['launch'] == 1 and order.index('prelaunch') < order.index('host')
        assert intent['host_launch_started'] == 1 and intent['state'] == 'completed'
        assert type(intent['final_result_id']) is int
    assert lease_order == ['profile', 'org', 'sqlite', 'sqlite-release', 'org-release', 'profile-release'], lease_order
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('closure', [
    'cache', 'pointer-fence', 'journal-state', 'journal-bytes', 'journal-digest',
    'dependency-generation', 'dependency-state', 'store-generation', 'store-digest',
    'store-state', 'registry-generation', 'operation', 'captured-global-digest',
])
def test_actual_prelaunch_revalidates_entire_captured_profile_authority_closure(
    draft_host, activation_profile, monkeypatch, closure,
):
    client, org, state, body, controls, host_observations, backend = draft_host
    _, profile_org, _, _, name = activation_profile
    assert org is profile_org
    _select_activation_profile(client, org, body, name)
    with state.profile_coordinator.operation([name], operation_kind='rebind', publisher='prelaunch-closure-baseline'):
        pass
    ready = org.workflow_authority.verify_admission_ready()
    body['authority'] = dict(namespace=ready.namespace, generation=ready.generation, snapshot_digest=ready.snapshot_digest)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    original_capture = org.workflow_authority.capture_admission
    original_reserve = org.workflow_drafts._reserve_launch
    observations, originals, saved_cache, refusals = [], [], [], []
    def corrupt_after_actual_capture():
        capture = original_capture()
        if not org.db.get_task(task_id).current_session_id:
            return capture
        originals.append(_snapshot(org))
        saved_cache.append(dict(org.workflow_authority._cache))
        assert not org.db._conn.in_transaction
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
        conn = org.db._conn
        journal_id = capture.pointer[1]
        if closure == 'cache':
            org.workflow_authority._cache[ready.namespace] = (ready.generation, '0' * 64)
        elif closure == 'pointer-fence':
            conn.execute('UPDATE workflow_authority_pointers SET profile_fence=profile_fence+1 WHERE namespace=?', (ready.namespace,))
        elif closure == 'journal-state':
            conn.execute("UPDATE workflow_publication_journals SET state='prepared' WHERE id=?", (journal_id,))
        elif closure == 'journal-bytes':
            conn.execute('UPDATE workflow_publication_journals SET snapshot_bytes=? WHERE id=?', (b'private-corrupt-snapshot', journal_id))
        elif closure == 'journal-digest':
            conn.execute('UPDATE workflow_publication_journals SET snapshot_digest=? WHERE id=?', ('0' * 64, journal_id))
        elif closure == 'dependency-generation':
            conn.execute('UPDATE workflow_profile_dependencies SET bound_generation=bound_generation+1 WHERE profile_name=?', (name,))
        elif closure == 'dependency-state':
            conn.execute("UPDATE workflow_profile_dependencies SET state='unbound' WHERE profile_name=?", (name,))
        elif closure == 'store-generation':
            conn.execute('UPDATE workflow_profile_store SET generation=generation+1 WHERE profile_name=?', (name,))
        elif closure == 'store-digest':
            conn.execute('UPDATE workflow_profile_store SET profile_digest=? WHERE profile_name=?', ('0' * 64, name))
        elif closure == 'store-state':
            conn.execute("UPDATE workflow_profile_store SET state='removed' WHERE profile_name=?", (name,))
        elif closure == 'registry-generation':
            conn.execute('UPDATE workflow_profile_registry SET published_generation=published_generation+1 WHERE profile_name=?', (name,))
        elif closure == 'operation':
            conn.execute("UPDATE workflow_profile_operations SET state='captured' WHERE id=(SELECT id FROM workflow_profile_operations WHERE profile_name=? ORDER BY rowid DESC LIMIT 1)", (name,))
        else:
            capture = replace(capture, profile_digests=((name, '0' * 64),))
        conn.commit()
        observations.append(_snapshot(org))
        return capture


    async def observed_refusal(tid, agent, session):
        assert org.sessions.get_active(tid, agent) == session
        try:
            return await original_reserve(tid, agent, session)
        except Exception as exc:
            refusals.append(getattr(exc, 'code', str(exc)))
            assert len(observations) == 1
            assert _snapshot(org) == observations[0], 'prelaunch closure refusal changed persisted graph'
            assert backend.calls['launch'] == 0 and not org.db._conn.in_transaction
            assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
            _independent_profile_probe(state.profile_coordinator, name, busy=False)
            raise
        finally:
            # Exact fixture restoration follows the no-write refusal oracle.
            # Only deliberately corrupted prelaunch authority rows are reset;
            # no real lifecycle event/claim/result is edited or fabricated.
            if originals:
                conn = org.db._conn
                for table in ('workflow_authority_pointers', 'workflow_publication_journals',
                              'workflow_profile_dependencies', 'workflow_profile_store',
                              'workflow_profile_registry', 'workflow_profile_operations'):
                    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                    rowids = [row[0] for row in conn.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid')]
                    assert len(rowids) == len(originals[0][table])
                    for rowid, row in zip(rowids, originals[0][table]):
                        assignments = ','.join(f'"{col}"=?' for col in columns)
                        conn.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid=?', (*row, rowid))
                conn.commit()
                org.workflow_authority._cache.clear()
                org.workflow_authority._cache.update(saved_cache[0])
                restored = _snapshot(org)
                authority_tables = ('workflow_authority_pointers', 'workflow_publication_journals',
                                    'workflow_profile_dependencies', 'workflow_profile_store',
                                    'workflow_profile_registry', 'workflow_profile_operations')
                assert all(restored[table] == originals[0][table] for table in authority_tables), 'negative authority fixture was not exactly restored'
                assert org.workflow_authority._cache == saved_cache[0]

    monkeypatch.setattr(org.workflow_authority, 'capture_admission', corrupt_after_actual_capture)
    monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', observed_refusal)
    org.orchestrator.run_step(task_id)
    expected = ('authority_pointer_not_ready' if closure == 'cache' else
                'authority_pointer_journal_invalid' if closure in {'journal-bytes', 'journal-digest'} else
                'profile_operation_in_progress:captured' if closure == 'operation' else
                'workflow_activation_authority_stale')
    assert refusals == [expected], refusals
    assert backend.calls['launch'] == 0 and host_observations == []
    assert org.sessions.get_active(task_id, 'product_lead') is None
    assert task_id not in org.workflow_drafts._live
    assert not org.db.execute("SELECT 1 FROM workflow_draft_dispatch_events WHERE event_kind='launch_reserved'").fetchone()
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('selection', ['empty-to-selected', 'selected-to-empty'])
def test_actual_prelaunch_refuses_supported_selected_target_change_after_capture(
    draft_host, activation_profile, monkeypatch, selection,
):
    from contextlib import contextmanager
    client, org, state, body, controls, observations, backend = draft_host
    _, profile_org, _, _, name = activation_profile
    assert profile_org is org
    if selection == 'selected-to-empty':
        _select_activation_profile(client, org, body, name)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    original_capture = org.workflow_authority.capture_admission
    original_read = state.profile_coordinator.profile_read
    original_reserve = org.workflow_drafts._reserve_launch
    captures, acquired, after_writer, refusals = [], [], [], []

    def changed_capture():
        capture = original_capture()
        if not org.db.get_task(task_id).current_session_id:
            return capture
        assert not org.db._conn.in_transaction
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
        captures.append(capture)
        _select_activation_profile(client, org, body, name if selection == 'empty-to-selected' else 'claude')
        after_writer.append(_snapshot(org))
        acquired.clear()
        return capture

    @contextmanager
    def observed_profile(name):
        acquired.append(name)
        with original_read(name):
            yield

    async def observed_reserve(tid, agent, session):
        assert org.sessions.get_active(tid, agent) == session
        try:
            return await original_reserve(tid, agent, session)
        except Exception as exc:
            refusals.append(getattr(exc, 'code', str(exc)))
            assert _snapshot(org) == after_writer[0], 'selected-target refusal changed durable prelaunch state'
            assert acquired == list(captures[0].profile_names), 'stale capture leased a newly selected target'
            assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
            _independent_profile_probe(state.profile_coordinator, name, busy=False)
            raise

    monkeypatch.setattr(org.workflow_authority, 'capture_admission', changed_capture)
    monkeypatch.setattr(state.profile_coordinator, 'profile_read', observed_profile)
    monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', observed_reserve)
    org.orchestrator.run_step(task_id)
    assert refusals == ['workflow_activation_authority_stale'], refusals
    assert len(captures) == 1 and backend.calls['launch'] == 0
    assert not org.db.execute("SELECT 1 FROM workflow_draft_dispatch_events WHERE event_kind='launch_reserved'").fetchone()
    assert task_id not in org.workflow_drafts._live
    assert org.sessions.get_active(task_id, 'product_lead') is None
    monkeypatch.setattr(org.workflow_authority, 'capture_admission', original_capture)
    fresh = original_capture()
    assert fresh.ready.generation > captures[0].ready.generation
    assert fresh.profile_names == (() if selection == 'selected-to-empty' else (name,))
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('live_state', ['claimed', 'running', 'uncertain'])
def test_live_periodic_discovery_never_replays_a_live_or_possible_launch(draft_host, monkeypatch, live_state):
    client, org, state, body, controls, observations, backend = draft_host
    entered, release = threading.Event(), threading.Event()
    errors = []
    if live_state == 'uncertain':
        controls.update(callback=False, quiescent=False)
    original_reserve = org.workflow_drafts._reserve_launch
    original_handle = org.workflow_drafts.observed_handle
    async def held_claim(tid, agent, session):
        assert org.sessions.get_active(tid, agent) == session
        entered.set()
        assert await asyncio.to_thread(release.wait, 8), 'live claimed barrier timed out'
        return await original_reserve(tid, agent, session)
    def held_running(*args):
        original_handle(*args)
        entered.set()
        assert release.wait(8), 'live running barrier timed out'
    if live_state == 'claimed':
        monkeypatch.setattr(org.workflow_drafts, '_reserve_launch', held_claim)
    elif live_state == 'running':
        monkeypatch.setattr(org.workflow_drafts, 'observed_handle', held_running)
    receipt = client.post(BASE, json=body).json()
    task_id = receipt['root_task_id']
    assert state.queue._queue.get_nowait() == ('alpha', task_id, None)
    state.queue._queue.task_done()
    def run():
        try: org.orchestrator.run_step(task_id)
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        if live_state != 'uncertain':
            assert entered.wait(5)
        else:
            worker.join(8)
            assert not worker.is_alive() and errors == []
        assert org.db.execute('SELECT state FROM workflow_draft_dispatch_intents').fetchone()[0] == live_state
        before = _snapshot(org)
        launches = backend.calls['launch']
        _live_periodic_tick(state, monkeypatch)
        _live_periodic_tick(state, monkeypatch)
        assert state.queue._queue.qsize() == 0, 'live or possible launch was made replayable'
        assert _snapshot(org) == before and backend.calls['launch'] == launches
    finally:
        release.set()
        worker.join(8)
        assert not worker.is_alive()
    assert errors == [], errors
    assert task_id not in org.workflow_drafts._live
    assert org.sessions.get_active(task_id, 'product_lead') is None
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == ('uncertain' if live_state == 'uncertain' else 'completed')
    shown = client.get(f"{BASE}/{receipt['activation_id']}").json()
    assert shown['pending'] is (live_state == 'uncertain')
    assert shown['reconciliation_required'] is (live_state == 'uncertain')
    assert backend.calls['launch'] == 1
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.fixture
def generic_draft_host(generic_activation_org, monkeypatch):
    """Exact receipt ownership; only contained external process execution is controlled."""
    from runtime.platform.session_backend import RunningHandle, LaunchSpec, BackendLaunchError
    from runtime.orchestrator.executors import ExecutorResult
    from tests.test_host_supervisor_lifecycle import FakeBackend, make_supervisor

    client, org, state, cases = generic_activation_org
    control = {"receipt": None, "callback": True, "quiescent": True, "ack": True,
               "prelaunch": []}
    observations, processes = [], []

    def owned(session, *, reserved=True):
        receipt = control["receipt"]
        assert receipt is not None
        task = org.db.get_task(receipt["root_task_id"])
        intent = dict(org.db.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?",
                                     (receipt["intent_id"],)).fetchone())
        assert intent["task_id"] == task.id and intent["assigned_principal"] == task.assigned_agent
        assert task.current_session_id == session
        if reserved:
            assert intent["session_id"] == session
        else:
            # bind_session publishes only the task/tracker generation. The
            # intent records it when reserve_launch commits, after spec build.
            assert intent["state"] == "claimed" and intent["host_launch_started"] == 0
            assert intent["session_id"] is None and intent["host_execution_id"] is None
        assert org.sessions.get_active(task.id, task.assigned_agent) == session
        return task, intent

    def callback(session):
        task, intent = owned(session)
        payload = dict(agent=task.assigned_agent, session_id=session, status="completed",
                       output_summary="Receipt-bound immutable document draft", confidence=83)
        response = client.post(f"/api/v1/orgs/alpha/tasks/{task.id}/completion", json=payload)
        assert response.status_code == 200, response.text
        observations.append((task.id, session, payload))
        before = _snapshot(org)
        assert client.post(f"/api/v1/orgs/alpha/tasks/{task.id}/completion", json=payload).status_code == 200
        assert _snapshot(org) == before
        assert client.post(f"/api/v1/orgs/alpha/tasks/{task.id}/completion",
                           json={**payload, "confidence": 82}).status_code == 409
        assert _snapshot(org) == before

    class Backend(FakeBackend):
        def launch(self, pending, spec):
            self.calls["launch"] += 1
            receipt = control["receipt"]
            intent = dict(org.db.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?",
                                         (receipt["intent_id"],)).fetchone())
            task, intent = owned(intent["session_id"])
            assert not org.db._conn.in_transaction
            assert not org.db.execute("SELECT 1 FROM workflow_publication_leases").fetchone()
            assert intent["state"] == "claimed" and intent["host_launch_started"] == 1
            assert pending.request_id == f"workflow-draft-host:{receipt['intent_id']}"
            proc = subprocess.Popen(spec.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(proc)
            if not control["ack"]:
                callback(intent["session_id"])
                raise BackendLaunchError("controlled loss after receipt-bound process spawn")
            self.last_running = RunningHandle(backend=self.name, token=pending.token,
                request_id=pending.request_id, root_pid=proc.pid, start_identity="controlled-unit-process", process=proc)
            return self.last_running

        def finish(self, running, reason, grace, **kwargs):
            running.process.wait(timeout=5)
            return replace(super().finish(running, reason, grace, **kwargs), quiescent=control["quiescent"])

    class Executor:
        def build_launch_spec(self, **kwargs):
            task, intent = owned(kwargs["session_id"], reserved=False)
            control["prelaunch"].append((task.id, kwargs["session_id"], intent["id"],
                                         intent["activation_revision"], intent["state"]))
            return LaunchSpec(argv=(sys.executable, "-c", "pass"))

        def run(self, **kwargs):
            task, intent = owned(kwargs["session_id"])
            assert intent["state"] == "running" and intent["host_execution_id"]
            kwargs["running"].process.communicate(timeout=5)
            if control["callback"]:
                callback(kwargs["session_id"])
            return ExecutorResult(success=True, duration_seconds=0, session_id=kwargs["session_id"])

    backend = Backend(name="controlled-unit-subprocess")
    supervisor, publisher = make_supervisor(backend=backend, max_retry_attempts=1, backoff_seconds=(0.0,))

    def evidence():
        """Bounded lossless fixture evidence, attributed to the actual receipt."""
        import base64
        from dataclasses import asdict
        receipt = control["receipt"]
        task_id, intent_id = receipt["root_task_id"], receipt["intent_id"]
        queries = {
            "task": ("SELECT * FROM tasks WHERE id=?", (task_id,)),
            "intent": ("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (intent_id,)),
            "events": ("SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq", (intent_id,)),
            "results": ("SELECT * FROM task_results WHERE task_id=? ORDER BY id", (task_id,)),
            "audit": ("SELECT * FROM audit_log WHERE task_id=? ORDER BY id", (task_id,)),
        }
        with org.db._lock:
            records = {name: [dict(row) for row in org.db.execute(sql, args)]
                       for name, (sql, args) in queries.items()}
        records.update(receipt=receipt, prelaunch=control["prelaunch"],
                       sessions=list(org.sessions.iter_active()), backend_calls=backend.calls,
                       host_receipts=[asdict(row) for row in publisher.receipts])
        def encode_bytes(value):
            if not isinstance(value, bytes):
                raise TypeError(f"unsupported fixture evidence type: {type(value).__name__}")
            return {"encoding": "base64", "bytes": len(value),
                    "data": base64.b64encode(value).decode("ascii")}
        encoded = json.dumps(records, sort_keys=True, default=encode_bytes, allow_nan=False)
        assert len(encoded.encode("utf8")) <= 131072, "fixture evidence exceeds lossless diagnostic bound"
        return encoded

    control["evidence"] = evidence
    org.orchestrator.attach_host_supervisor(supervisor)
    monkeypatch.setattr(org.orchestrator, "_build_executor", lambda provider: Executor())
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    def serve():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()
    worker = threading.Thread(target=serve)
    worker.start()
    try:
        assert ready.wait(5)
        monkeypatch.setattr(org.orchestrator, "_main_loop", loop)
        yield client, org, state, cases, control, observations, backend
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


def test_generic_serial_drafts_use_receipt_bound_host_and_real_results(generic_draft_host, activation_org):
    from runtime.workflows.recovery import classify_task
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    client, org, state, cases, control, observations, backend = generic_draft_host
    legacy = activation_org[3]
    ddl = [tuple(row) for row in org.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name")]
    results, roots, sessions = [], [], []
    for ordinal, name in enumerate(["legacy", "product", "proposal", "A", "Z"], start=1):
        body = legacy if name == "legacy" else cases[name][0]
        response = client.post(BASE, json=body)
        assert response.status_code == 201, (name, response.text)
        receipt = response.json()
        control["receipt"] = receipt
        task_id, intent_id = receipt["root_task_id"], receipt["intent_id"]
        author = "product_lead" if name in {"legacy", "product"} else "dev_agent"
        task = org.db.get_task(task_id)
        assert task.assigned_agent == author and task.orchestration_step_count == 0
        assert classify_task(org.db, task_id, org_slug="alpha").kind == "draft"
        org.orchestrator.run_step(task_id)
        print("generic-draft-producer-evidence=" + control["evidence"]())
        intent = dict(org.db.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (intent_id,)).fetchone())
        assert intent["state"] == "completed" and intent["task_id"] == task_id, control["evidence"]()
        assert control["prelaunch"][-1] == (task_id, intent["session_id"], intent_id,
                                           intent["activation_revision"], "claimed")
        assert type(intent["final_result_id"]) is int
        result = org.db.execute("SELECT * FROM task_results WHERE id=?", (intent["final_result_id"],)).fetchone()
        assert result["task_id"] == task_id and result["agent"] == author
        assert result["session_id"] == intent["session_id"] and result["status"] == "completed"
        assert org.db.get_task(task_id).status.value == "completed"
        assert org.db.get_task(task_id).orchestration_step_count == 0
        assert org.sessions.get_active(task_id, author) is None
        events = [dict(row) for row in org.db.execute(
            "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq", (intent_id,))]
        assert [row["event_kind"] for row in events] == ["admitted", "claimed", "launch_reserved", "running", "callback_recorded", "completed"]
        assert json.loads(events[-1]["event_bytes"])["terminal_evidence"] == {"host_quiescent": True}
        assert json.loads(events[-1]["event_bytes"])["result"] == json.loads(events[-2]["event_bytes"])["result"]
        assert backend.calls["launch"] == backend.calls["finish"] == ordinal
        assert backend.last_running.request_id == f"workflow-draft-host:{intent_id}"
        assert not org.db.execute("SELECT 1 FROM workflow_submissions").fetchone()
        assert not org.db.execute("SELECT 1 FROM workflow_review_requests").fetchone()
        assert not org.db.execute("SELECT 1 FROM workflow_review_receipts").fetchone()
        assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action IN ('decision','task_failed')", (task_id,)).fetchone()
        validate_workflow_schema(org.db._conn, expected_org_slug="alpha")
        principal = WorkflowTemplatePrincipal.founder(org_slug="alpha", team_slug="", revalidate=lambda: None)
        projected = org.workflow_activations.get(principal=principal, activation_id=receipt["activation_id"])
        assert projected["state"] == "completed" and projected["pending"] is False
        before = _snapshot(org)
        org.orchestrator.run_step(task_id)
        replay = client.post(BASE, json=body)
        assert replay.status_code == 200 and replay.json()["root_task_id"] == task_id
        assert _snapshot(org) == before
        results.append(intent["final_result_id"])
        roots.append(task_id)
        sessions.append(intent["session_id"])
        assert [tuple(row) for row in org.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name")] == ddl
    assert len(set(results)) == len(set(roots)) == len(set(sessions)) == 5
    assert len(observations) == 5
    assert org.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 5
    assert org.db.execute("SELECT COUNT(*) FROM task_results").fetchone()[0] == 5
    assert backend.calls["launch"] == backend.calls["finish"] == 5


@pytest.mark.parametrize("name", ["A", "Z"])
@pytest.mark.parametrize("gap", ["callback", "quiescent", "ack"])
def test_generic_missing_host_evidence_preserves_uncertainty(generic_draft_host, name, gap):
    client, org, state, cases, control, observations, backend = generic_draft_host
    control[gap] = False
    body = cases[name][0]
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    control["receipt"] = receipt
    org.orchestrator.run_step(receipt["root_task_id"])
    print("generic-draft-producer-evidence=" + control["evidence"]())
    intent = dict(org.db.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (receipt["intent_id"],)).fetchone())
    assert intent["state"] == "uncertain" and intent["host_launch_started"] == 1, control["evidence"]()
    assert control["prelaunch"] == [(receipt["root_task_id"], intent["session_id"], receipt["intent_id"],
                                      intent["activation_revision"], "claimed")]
    assert bool(intent["host_execution_id"]) == (gap != "ack")
    assert (intent["final_result_id"] is None) == (gap == "callback")
    assert org.db.get_task(receipt["root_task_id"]).status.value == "in_progress"
    before = _snapshot(org)
    org.workflow_drafts.reconcile(receipt["root_task_id"])
    org.orchestrator.run_step(receipt["root_task_id"])
    assert _snapshot(org) == before and backend.calls["launch"] == 1
    assert not org.db.execute("SELECT 1 FROM workflow_submissions").fetchone()
    validate_workflow_schema(org.db._conn, expected_org_slug="alpha")
