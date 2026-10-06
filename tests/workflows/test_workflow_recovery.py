from unittest.mock import patch
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from runtime.daemon.__main__ import _sweep_on_startup
from runtime.models import TaskStatus
from tests.daemon.test_workflow_activation_routes import BASE, activation_org, _snapshot
from tests.workflows.test_activation_store import _rewrite_canonical_document


def test_startup_never_pid_fails_a_draft_root_with_missing_launch_closure(activation_org):
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    task_id = response.json()["root_task_id"]
    # This adverse mismatch is not a claimed/running positive fixture. It
    # represents malformed persisted ownership. PID absence is not quiescence.
    org.db.update_task(task_id, status=TaskStatus.IN_PROGRESS)
    before_events = [tuple(row) for row in org.db.execute("SELECT * FROM workflow_draft_dispatch_events")]
    _sweep_on_startup(org.db, state.queue, "alpha", org.orchestrator)
    assert org.db.get_task(task_id).status == TaskStatus.IN_PROGRESS
    assert [tuple(row) for row in org.db.execute("SELECT * FROM workflow_draft_dispatch_events")] == before_events
    assert not org.db.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action='daemon_restart_failure'", (task_id,)).fetchone()


def test_draft_root_never_enters_ordinary_manager_run_step(activation_org):
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    task_id = response.json()["root_task_id"]
    # Test-side external boundary control only: no host/provider acceptance is
    # synthesized. The assertion observes exclusion from the legacy consumer.
    with patch.object(org.orchestrator, "_run_agent", side_effect=RuntimeError("unexpected ordinary launch")) as launch:
        try:
            org.orchestrator.run_step(task_id)
        except RuntimeError:
            pass
        assert launch.call_count == 0
    task = org.db.get_task(task_id)
    assert task.status == TaskStatus.PENDING and task.orchestration_step_count == 0


@pytest.mark.parametrize('target,field,value', [
    ('authorization', 'template.compiler_pin', 'foreign-compiler'),
    ('binding', 'authority.namespace', 'org/foreign'),
    ('context', 'extra', 'private-corrupt-member'),
], ids=['authorization-pin', 'binding-authority', 'context-envelope'])
@pytest.mark.parametrize('consumer', ['enqueue', 'startup', 'run-step', 'reaper', 'cancel', 'portability'])
@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
def test_semantic_corruption_fences_every_owned_consumer_before_effects(
    activation_org, monkeypatch, target, field, value, consumer,
):
    from runtime.daemon.runner import enqueue_task
    from runtime.daemon.zombie_reaper import _sweep_org_zombies
    from runtime.workflows.recovery import classify_task

    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    task_id = receipt['root_task_id']
    _rewrite_canonical_document(org, receipt, target, field, value)
    before = _snapshot(org)
    effects = []
    # Observation at the real shipping consumer boundaries, not fake success:
    # these spies fail any attempt to enqueue/dispatch/reconcile corrupt work.
    monkeypatch.setattr(state.queue, 'enqueue', lambda *a, **kw: effects.append('enqueue'))
    monkeypatch.setattr(state.queue, 'enqueue_if_absent', lambda *a, **kw: effects.append('enqueue-if-absent'))
    monkeypatch.setattr(org.workflow_drafts, 'dispatch', lambda *a: effects.append('dispatch'))
    monkeypatch.setattr(org.workflow_drafts, 'reconcile', lambda *a: effects.append('reconcile'))
    if consumer == 'enqueue':
        enqueue_task(state, org.slug, task_id)
    elif consumer == 'startup':
        _sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)
    elif consumer == 'run-step':
        org.orchestrator.run_step(task_id)
    elif consumer == 'reaper':
        _sweep_org_zombies(org.db, now=datetime.now(timezone.utc), uptime=3600,
                           warm_up_seconds=0, orchestrator=org.orchestrator, queue=state.queue)
    else:
        transport = TestClient(client.app, raise_server_exceptions=False)
        try:
            transport.headers.update(client.headers)
            response = (transport.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={})
                        if consumer == 'cancel' else transport.post('/api/v1/orgs/alpha/reconcile-portability',
                            json={'candidate_task_id': task_id, 'disposition': 'cancel'}))
        finally:
            transport.close()
        assert response.status_code == 409, response.text
        assert response.json()['detail'] == {'code': 'workflow_reconciliation_required'}
        assert 'private-corrupt-member' not in response.text
    assert effects == [], effects
    assert classify_task(org.db, task_id, org_slug=org.slug).kind == 'reconciliation_required'
    assert _snapshot(org) == before
    assert org.db.get_task(task_id).status == TaskStatus.PENDING


@pytest.mark.parametrize('status', list(TaskStatus), ids=lambda status: status.value)
@pytest.mark.parametrize('damage', ['missing-intent', 'missing-event', 'dual-bridge', 'incomplete-f5'])
def test_incomplete_ownership_fences_all_status_branches_and_consumers(
    activation_org, monkeypatch, status, damage,
):
    from runtime.daemon.runner import enqueue_task
    from runtime.daemon.zombie_reaper import _sweep_org_zombies
    from runtime.workflows.recovery import classify_task

    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    task_id = receipt['root_task_id']
    conn = org.db._conn
    # Deliberately damaged records, never a successful F5/submission fixture.
    # Disable FK checks only for this negative on-disk corruption control.
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        if damage in {'missing-intent', 'incomplete-f5'}:
            conn.execute('DELETE FROM workflow_draft_dispatch_intents WHERE id=?', (receipt['intent_id'],))
        if damage == 'missing-event':
            conn.execute('DELETE FROM workflow_draft_dispatch_events WHERE intent_id=?', (receipt['intent_id'],))
        if damage in {'dual-bridge', 'incomplete-f5'}:
            conn.execute('INSERT INTO workflow_request_task_bridges VALUES (?,?,?,?,?,?,?,?,?,?)',
                         ('missing-request', 'missing-operation', receipt['instance_id'], task_id,
                          'product_lead', 1, None, None, 'queued', datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.execute('PRAGMA foreign_keys=ON')
    org.db.update_task(task_id, status=status, executor_pid=99999999,
                       last_heartbeat=datetime(2000, 1, 1, tzinfo=timezone.utc),
                       zombie_flagged_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    before = _snapshot(org)
    effects = []
    monkeypatch.setattr(state.queue, 'enqueue', lambda *a, **kw: effects.append('enqueue'))
    monkeypatch.setattr(state.queue, 'enqueue_if_absent', lambda *a, **kw: effects.append('discover'))
    monkeypatch.setattr(org.workflow_drafts, 'dispatch', lambda *a: effects.append('dispatch'))
    monkeypatch.setattr(org.workflow_drafts, 'reconcile', lambda *a: effects.append('reconcile'))
    monkeypatch.setattr(org.orchestrator, '_run_agent', lambda *a, **kw: effects.append('legacy-launch'))
    assert classify_task(org.db, task_id, org_slug=org.slug).kind == 'reconciliation_required'
    for consumer in ('enqueue', 'startup', 'run-step', 'reaper', 'cancel', 'portability'):
        if consumer == 'enqueue':
            enqueue_task(state, org.slug, task_id)
        elif consumer == 'startup':
            _sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)
        elif consumer == 'run-step':
            org.orchestrator.run_step(task_id)
        elif consumer == 'reaper':
            _sweep_org_zombies(org.db, now=datetime.now(timezone.utc), uptime=3600,
                               warm_up_seconds=0, orchestrator=org.orchestrator, queue=state.queue)
        else:
            response = (client.post(f'/api/v1/orgs/alpha/tasks/{task_id}/cancel', json={})
                        if consumer == 'cancel' else client.post('/api/v1/orgs/alpha/reconcile-portability',
                            json={'candidate_task_id': task_id, 'disposition': 'cancel'}))
            assert response.status_code == 409, (consumer, response.text)
            assert response.json()['detail'] == {'code': 'workflow_reconciliation_required'}
        assert effects == [], (consumer, effects)
        assert _snapshot(org) == before, consumer


def test_task_name_type_and_corrupt_unrelated_workflow_do_not_claim_ordinary_task(activation_org):
    from runtime.models import TaskRecord
    from runtime.workflows.recovery import classify_task, recover_owned_task

    client, org, state, body = activation_org
    receipt = client.post(BASE, json=body).json()
    org.db.execute('DELETE FROM workflow_draft_dispatch_events WHERE intent_id=?', (receipt['intent_id'],))
    org.db._conn.commit()
    ordinary = TaskRecord(id=org.db.next_task_id(), brief='workflow-initial-draft:foreign:1',
                          assigned_agent='product_lead', team='product', task_type='subtask')
    org.db.insert_task(ordinary)
    before = _snapshot(org)
    assert classify_task(org.db, ordinary.id, org_slug=org.slug).kind == 'legacy'
    assert recover_owned_task(org.db, state.queue, org.slug, ordinary.id,
                              orchestrator=org.orchestrator) is False
    assert _snapshot(org) == before
