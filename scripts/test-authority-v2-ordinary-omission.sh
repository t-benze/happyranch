#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PY'
import asyncio
import json
import sqlite3
import traceback
from functools import partial
from unittest.mock import patch
import tempfile
from pathlib import Path
from types import SimpleNamespace

from runtime.daemon.routes.tasks import CompletionBody, _completion_v2_evidence, submit_completion
from runtime.daemon.sessions import SessionTracker
from fastapi import HTTPException
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover
from runtime.infrastructure.workflow_task_ownership import install_task_ownership
from runtime.infrastructure.task_pause_controls import install_pause_schema, TaskPauseStore
from runtime.models import authority_policy_v2_attempt_id, AUTHORITY_POLICY_V2_CONTRACT_ID, AUTHORITY_POLICY_V2_CONTRACT_VERSION, AuthorityPolicyV2SessionBinding, TaskRecord, TaskStatus, authority_policy_v2_contract_digest
from runtime.orchestrator.active_authority_policy import load_session_policy_binding
from runtime.orchestrator.orchestrator import Orchestrator, completion_report_from_result_row
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority import refuse_authority_policy_v2_pre_final_on_startup
from runtime.orchestrator.run_step import _consume_completion_report


def fixture(path):
    db = Database(path)
    install_or_recover(db, expected_org_slug='scratch')
    install_task_ownership(db, org_slug='scratch')
    selector = db.ensure_authority_selector('Engineering')
    receipt = db.create_and_activate_authority_policy_v2(dict(
        team='Engineering', policy_id='scratch', title='Scratch policy',
        create_request_id='scratch-create', activation_request_id='scratch-activate',
        based_on_selector_id=selector.selector_id, expected_selector_id=selector.selector_id,
        action='bootstrap', what_to_escalate='Uncertain authority', what_not_to_escalate='Ordinary work',
    ))
    selector = db.get_authority_selector('Engineering')
    release = db.get_authority_policy_v2_release(receipt.release_id)
    db.insert_task(TaskRecord(id='TASK-1', brief='Synthetic callback', team='Engineering',
                             status=TaskStatus.IN_PROGRESS, assigned_agent='manager', current_session_id='scratch-session'))
    install_pause_schema(db, org_slug='scratch')
    db._task_pause_store = TaskPauseStore(db, 'scratch')
    db._task_pause_store.ensure_uncommitted('TASK-1')
    db._conn.commit()
    bind(db, 'scratch-session')
    return db


def bind(db, session):
    selector = db.get_authority_selector('Engineering')
    activation = db.get_authority_policy_v2_activation(selector.v2_activation_id)
    release = db.get_authority_policy_v2_release(activation.release_id)
    db.bind_authority_policy_v2_session(AuthorityPolicyV2SessionBinding(
        team='Engineering', root_task_id='TASK-1', manager_agent='manager', manager_session_id=session,
        selector_id=selector.selector_id, activation_epoch=selector.selector_epoch,
        release_id=release.release_id, policy_version=release.version, policy_digest=release.policy_digest,
        activation_id=selector.v2_activation_id, contract_id=AUTHORITY_POLICY_V2_CONTRACT_ID,
        contract_version=AUTHORITY_POLICY_V2_CONTRACT_VERSION, contract_digest=authority_policy_v2_contract_digest(),
        provider_id='synthetic', executor_kind='synthetic', model_id='synthetic',
    ))


def payload(db, decision, *, status='completed', summary='Synthetic result', session='scratch-session', evaluation=False):
    body = CompletionBody(session_id=session, agent='manager', status=status,
                          confidence=90, output_summary=summary, decision=decision)
    binding = load_session_policy_binding(db=db, task_id='TASK-1', session_id=body.session_id, agent_name=body.agent)
    if evaluation:
        fields = ('activation_id', 'contract_digest', 'contract_id', 'contract_version', 'executor_kind',
                  'model_id', 'policy_digest', 'policy_version', 'provider_id', 'release_id')
        body.manager_self_evaluation = {key: binding[key] for key in fields}
        body.manager_self_evaluation.update(root_task_id='TASK-1', manager_session_id=session,
            activation_epoch=binding['selector_epoch'],
            what_to_escalate=dict(applicability='does_not_apply', confidence=100, uncertainty_codes=[]),
            what_not_to_escalate=dict(applicability='applies', confidence=100, uncertainty_codes=[]))
    carrier, admission = _completion_v2_evidence(org=SimpleNamespace(), body=body, binding=binding, task_id='TASK-1')
    decision = dict(decision or {}, _manager_self_evaluation=carrier)
    return body, admission, dict(task_id='TASK-1', agent=body.agent, session_id=body.session_id,
        output_summary=body.output_summary, confidence_score=body.confidence, status=body.status,
        decision_json=json.dumps(decision), risks_flagged=[], output_dir=None, waiting_on_job_ids=None,
        verdict=None, local_ci_json=None, v2_admission=admission)


class Events:
    async def publish(self, *args):
        pass  # No listener, serving app, worker or external effect.


def org_for(db):
    return SimpleNamespace(db=db, slug='scratch', sessions=SessionTracker(), db_lock=asyncio.Lock(),
        teams=SimpleNamespace(is_team_manager=lambda agent: agent == 'manager'), event_bus=Events())


def replay(db, body):
    return asyncio.run(submit_completion('TASK-1', body, org_for(db)))


def refused(db, body):
    try:
        replay(db, body)
    except HTTPException as error:
        assert error.status_code == 409, error
        return error.detail['code']
    raise AssertionError('changed or contradictory callback acknowledged')


def row_for(db, session='scratch-session'):
    return db.get_latest_task_result('TASK-1', 'manager', session)


def admit(db, decision, **options):
    body, admission, kwargs = payload(db, decision, **options)
    assert db.admit_task_completion_callback(**kwargs), 'callback not admitted'
    return body, admission, kwargs, row_for(db, body.session_id)


def persisted_attempt(db, row):
    attempt = db.get_authority_policy_v2_attempt_for_result(row['id'])
    assert attempt is not None, 'required attempt missing'
    assert attempt.attempt_id in db._v2_live_attempt_owners
    assert '_authority_v2_admission' not in json.loads(row['decision_json'])
    return attempt


ORDINARY = {
    'delegate': {'action': 'delegate', 'agent': 'worker', 'prompt': 'Synthetic work'},
    'done': {'action': 'done', 'summary': 'Finished'},
    'fanout': {'action': 'fanout', 'children': [
        {'agent': 'worker-a', 'prompt': 'Part A'}, {'agent': 'worker-b', 'prompt': 'Part B'}], 'width_cap_ack': 2},
    'parallel': {'action': 'parallel', 'children': [
        {'agent': 'worker-a', 'prompt': 'Part A'}, {'agent': 'worker-b', 'prompt': 'Part B'}], 'width_cap_ack': 2},
    'blocked': {'action': 'done', 'summary': 'Waiting'},
}


def ordinary_case(db, name):
    body, admission, kwargs, row = admit(db, ORDINARY[name], status='blocked' if name == 'blocked' else 'completed')
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_attempts').fetchone()[0] == 0, name + ': attempt allocated'
    assert not [a for a in db.get_audit_logs('TASK-1') if a['action'] == 'authority_policy_v2_result_stage'], name
    marker = json.loads(row['decision_json'])['_authority_v2_admission']
    assert marker['omitted'] == 'ordinary' and marker['assessment_digest'] == admission['assessment_digest']
    assert not db._v2_live_attempt_owners
    assert db.admit_task_completion_callback(**kwargs), 'DB exact retry'
    assert replay(db, body) == {'ok': True}, 'route exact retry'
    assert row_for(db) == row, 'retry rewrote immutable result'
    assert len(db.get_task_results('TASK-1')) == 1
    injected = body.model_copy(update={'decision': dict(body.decision,
        _authority_v2_admission={'omitted': 'forged'}, _manager_self_evaluation={'forged': True})})
    assert replay(db, injected) == {'ok': True}, 'client reserved keys must be discarded'


def retained_case(db, decision, **options):
    body, admission, kwargs, row = admit(db, decision, **options)
    persisted_attempt(db, row)
    if decision is not None and decision.get('action') in {'supersede', 'workflow_start'}:
        assert completion_report_from_result_row('TASK-1', row).decision.action == decision['action']
    assert db.admit_task_completion_callback(**kwargs)
    if decision is None or decision.get('action') != 'workflow_start':
        assert replay(db, body) == {'ok': True}
    assert row_for(db) == row
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_attempts').fetchone()[0] == 1


def continuation(db):
    # Real stage writers produce authenticated E/N/D, no daemon or launch.
    db.update_task('TASK-1', status=TaskStatus.IN_PROGRESS, current_session_id='later-session')
    bind(db, 'later-session')
    body, admission, kwargs, row = admit(db, {'action': 'escalate', 'reason': 'Synthetic routine'},
                                       session='later-session', evaluation=True)
    attempt = persisted_attempt(db, row)
    # Existing production evidence-reader binding. A synthetic constant models
    # an unchanged permission surface; this is not permission enforcement proof.
    db.bind_authority_policy_v2_permission_surface_reader(lambda agent: 'a' * 64)
    stage_kwargs = dict(root_task_id='TASK-1', manager_agent='manager', manager_session_id=body.session_id,
        result_id=row['id'], origin_boot_id=attempt.origin_boot_id, owner_attempt_id=attempt.owner_attempt_id)
    for method, expected in (
        ('claim_authority_policy_v2_candidate', 'claimed'),
        ('audit_authority_policy_v2_candidate_claim', 'claim_audited'),
        ('evaluate_authority_policy_v2_candidate', 'evaluated'),
        ('audit_authority_policy_v2_candidate_evaluation', 'evaluation_audited'),
        ('consume_authority_policy_v2_candidate', 'consumed'),
        ('audit_authority_policy_v2_candidate_consumption', 'consumed_audited'),
        ('finalize_authority_policy_v2_continuation', 'continued'),
    ):
        outcome = getattr(db, method)(**stage_kwargs)
        assert outcome.status == expected, (method, outcome)
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_continue_envelopes').fetchone()[0] == 1
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_root_dispatch').fetchone()[0] == 1
    assert db.authority_policy_v2_completion_dispatch_context(root_task_id='TASK-1', result_row_id=None).kind != 'no_v2'
    return row


def later_retry(db):
    body, admission, kwargs, row = admit(db, ORDINARY['done'])
    assert db.get_authority_policy_v2_attempt_for_result(row['id']) is None
    assert json.loads(row['decision_json'])['_authority_v2_admission']['omitted'] == 'ordinary'
    continuation(db)
    assert replay(db, body) == {'ok': True}, 'lineage invalidated original omission'
    assert row_for(db) == row
    # Preserve the writer's existing current-session guard on an active retry.
    db.update_task('TASK-1', status=TaskStatus.IN_PROGRESS, current_session_id='scratch-session')
    assert db.admit_task_completion_callback(**kwargs)


def lineage_case(db, name):
    causal = continuation(db)
    orch = object.__new__(Orchestrator)
    orch._db, orch._audit = db, AuditLogger(db)
    orch._log_step_result('TASK-1', None, completion_report_from_result_row('TASK-1', causal), result_row_id=causal['id'])
    attempt = db.get_authority_policy_v2_attempt_for_result(causal['id'])
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    identity = dict(root_task_id='TASK-1', manager_agent='manager', manager_session_id='later-session', result_id=causal['id'])
    publication = db.claim_authority_policy_v2_notification_publication(**identity)
    assert publication.status == 'claimed', publication
    reserved = db.try_claim_v2_continuation_generation(**identity,
        generation_id=publication.generation_id, next_session_id='scratch-session')
    assert reserved.status == 'claimed', reserved
    settled = db.settle_v2_continuation_generation_admission(**identity,
        generation_id=publication.generation_id, next_session_id='scratch-session')
    assert settled.status in {'settled', 'already_settled_exact'}, settled
    # Real DB reservation and settlement only; no session/process is launched.
    body, admission, kwargs, row = admit(db, ORDINARY[name], status='blocked' if name == 'blocked' else 'completed')
    persisted_attempt(db, row)
    assert db.authority_policy_v2_completion_dispatch_context(root_task_id='TASK-1', result_row_id=row['id']).kind == 'reserved'
    assert db.admit_task_completion_callback(**kwargs)
    assert replay(db, body) == {'ok': True}
    if name == 'done':
        orch = SimpleNamespace(_parse_next_step=partial(Orchestrator._parse_next_step, None))
        stats = refuse_authority_policy_v2_pre_final_on_startup(db, orchestrator=orch)
        attempt = db.get_authority_policy_v2_attempt_for_result(row['id'])
        assert attempt.stage == 'admitted' and attempt.finalization_state == 'unfinalized', attempt
        assert stats is not None and 'TASK-1' not in stats, stats
        assert db.get_task('TASK-1').status == TaskStatus.IN_PROGRESS


def changed_case(db, field):
    body, admission, kwargs, row = admit(db, ORDINARY['done'])
    changes = {
        'carrier': {'manager_self_evaluation': None},
        'summary': {'output_summary': 'Changed'}, 'status': {'status': 'blocked'},
        'confidence': {'confidence': 89}, 'verdict': {'verdict': 'Changed'},
        'risks': {'risks_flagged': ['Changed']}, 'output': {'output_dir': 'changed'},
        'decision': {'decision': ORDINARY['delegate']},
        'wait_ids': {'waiting_on_job_ids': ['JOB-synthetic']},
        'local_ci': {'local_ci': {'command': 'scripts/local_ci.sh all', 'exit_code': 0}},
    }
    changed = CompletionBody.model_validate({**body.model_dump(exclude_unset=True), **changes[field]})
    assert refused(db, changed) in {'v2_attempt_missing', 'v2_attempt_payload_mismatch'}
    _, _, changed_kwargs = payload(db, changed.decision, status=changed.status, summary=changed.output_summary)
    changed_kwargs.update(confidence_score=changed.confidence, risks_flagged=changed.risks_flagged,
        output_dir=changed.output_dir, verdict=changed.verdict, waiting_on_job_ids=changed.waiting_on_job_ids or None,
        local_ci_json=json.dumps(changed.local_ci) if changed.local_ci is not None else None)
    if field == 'carrier':
        carrier, incoming = _completion_v2_evidence(org=SimpleNamespace(), body=changed,
            binding=load_session_policy_binding(db=db, task_id='TASK-1', session_id=changed.session_id, agent_name=changed.agent), task_id='TASK-1')
        changed_kwargs['v2_admission'] = incoming
        changed_kwargs['decision_json'] = json.dumps(dict(changed.decision, _manager_self_evaluation=carrier))
    assert not db.admit_task_completion_callback(**changed_kwargs), field
    assert row_for(db) == row


def contradiction_case(db, kind):
    body, admission, kwargs, row = admit(db, ORDINARY['done'])
    if kind == 'orphan_attempt':
        db._conn.execute('BEGIN IMMEDIATE')
        db._insert_authority_policy_v2_attempt_uncommitted(task_id='TASK-1', agent='manager', session_id=body.session_id,
            result_id=row['id'], admission=admission, now=row['created_at'])
        db._conn.commit()
        db._conn.execute("DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'")
        db._conn.commit()
        assert db.get_authority_policy_v2_attempt_for_result(row['id']) is None
    elif kind in {'orphan_audit', 'foreign_audit', 'malformed_audit', 'contradictory_audit', 'duplicate_audit'}:
        db.insert_audit_log(task_id='foreign' if kind in {'foreign_audit', 'malformed_audit'} else 'TASK-1', agent='manager',
            action='authority_policy_v2_result_stage', payload={'stage': 'admitted', 'result_id': row['id']})
        if kind == 'contradictory_audit':
            attempt_id = authority_policy_v2_attempt_id(manager_agent='manager', manager_session_id=body.session_id,
                result_id=row['id'], root_task_id='TASK-1', team='Engineering')
            db._conn.execute("UPDATE audit_log SET payload=? WHERE action='authority_policy_v2_result_stage'",
                (json.dumps(dict(stage='admitted', result_id=row['id'] + 1, attempt_id=attempt_id)),))
            db._conn.commit()
        if kind == 'duplicate_audit':
            db._conn.execute("UPDATE audit_log SET payload=? WHERE action='authority_policy_v2_result_stage'",
                ('{"stage":"admitted","result_id":' + str(row['id']) + ',"result_id":999}',))
            db._conn.commit()
        if kind == 'malformed_audit':
            db._conn.execute("UPDATE audit_log SET payload='{' WHERE action='authority_policy_v2_result_stage'")
            db._conn.commit()
    elif kind in {'marker_missing', 'marker_binding', 'marker_digest', 'carrier_tamper'}:
        decision = json.loads(row['decision_json'])
        if kind == 'marker_missing':
            decision.pop('_authority_v2_admission')
        elif kind == 'marker_binding':
            decision['_authority_v2_admission']['binding_id'] = 'forged'
        elif kind == 'marker_digest':
            decision['_authority_v2_admission']['assessment_digest'] = '0' * 64
        else:
            decision['_manager_self_evaluation'] = {'_error_code': 'null_assessment'}
        db._conn.execute('UPDATE task_results SET decision_json=? WHERE id=?', (json.dumps(decision), row['id']))
        db._conn.commit()
    elif kind == 'read_error':
        db._conn.set_authorizer(lambda action, table, *args:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'authority_policy_v2_attempts' else sqlite3.SQLITE_OK)
    try:
        assert refused(db, body) == 'v2_attempt_missing'
        assert not db.admit_task_completion_callback(**kwargs)
    finally:
        db._conn.set_authorizer(None)


def attribution_case(db, mode):
    kwargs = payload(db, ORDINARY['done'])[2]
    if mode == 'legacy':
        kwargs['v2_admission'] = None
        kwargs['decision_json'] = json.dumps(ORDINARY['done'])
    elif mode == 'attempt':
        kwargs = payload(db, {'action': 'escalate', 'reason': 'Synthetic'})[2]
    assert db.admit_task_completion_callback(**kwargs)
    row = row_for(db)
    report = completion_report_from_result_row('TASK-1', row)
    if mode == 'omitted':
        assert db.get_authority_policy_v2_attempt_for_result(row['id']) is None
        assert json.loads(row['decision_json'])['_authority_v2_admission']['omitted'] == 'ordinary'
    observations = []
    orch = object.__new__(Orchestrator)
    orch._db, orch._audit = db, AuditLogger(db)
    orch._memory_collection = SimpleNamespace(reconcile_acceptance=observations.append)
    db.update_task('TASK-1', current_session_id='different-current-session')
    orch._log_step_result('TASK-1', None, report, result_row_id=row['id'])
    audit = [a for a in db.get_audit_logs('TASK-1') if a['action'] == 'completion_report'][-1]
    expected = report.model_dump()
    if mode != 'legacy':
        expected.update(_result_row_id=row['id'], _result_session_id=row['session_id'])
    assert audit['payload'] == expected, (mode, audit['payload'])
    assert observations == [row['id']], 'census hook changed'


def hook_case(db):
    body, admission, kwargs, row = admit(db, ORDINARY['done'])
    report = completion_report_from_result_row('TASK-1', row)
    # Defensive input disagrees with the persisted ordinary decision.
    report.decision = report.decision.model_copy(update={'action': 'escalate', 'reason': 'Synthetic mismatch'})
    orch = object.__new__(Orchestrator)
    orch._db, orch._audit, orch._slug = db, AuditLogger(db), 'scratch'
    orch._paths = OrgPaths(root=Path('/nonexistent-scratch-org'))
    notices = []
    orch.notify_escalated = lambda **kw: notices.append(kw)
    _consume_completion_report(orch, 'TASK-1', report, result_row_id=row['id'], reclaim_terminal_worktree=False)
    assert db.get_task('TASK-1').status == TaskStatus.ESCALATED
    assert len(notices) == 1
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_candidates').fetchone()[0] == 0
    assert db._conn.execute('SELECT count(*) FROM authority_policy_v2_attempts').fetchone()[0] == 0
    audits = db.get_audit_logs('TASK-1')
    assert len([a for a in audits if a['action'] == 'escalation']) == 1
    assert any(a['payload'].get('outcome') == 'capture_failure' for a in audits), audits


def route_admission_case(db):
    body, _, _ = payload(db, ORDINARY['done'])
    org = org_for(db)
    # Synthetic tracker registration only; no runtime session or process starts.
    org.sessions.set_active('TASK-1', 'manager', body.session_id)
    assert asyncio.run(submit_completion('TASK-1', body, org)) == {'ok': True}
    assert org.sessions.get_active('TASK-1', 'manager') is None
    row = row_for(db)
    assert json.loads(row['decision_json'])['_authority_v2_admission']['omitted'] == 'ordinary'
    assert db.get_authority_policy_v2_attempt_for_result(row['id']) is None
    assert not db._v2_live_attempt_owners


def exception_case(db, seam):
    body, admission, kwargs = payload(db, ORDINARY['done'])
    if seam == 'parser':
        with patch('runtime.orchestrator.orchestrator.completion_report_from_result_row', side_effect=ValueError('synthetic')):
            assert db.admit_task_completion_callback(**kwargs)
    else:
        db._conn.set_authorizer(lambda action, table, *args:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'authority_policy_v2_root_dispatch' else sqlite3.SQLITE_OK)
        try:
            assert db.admit_task_completion_callback(**kwargs)
        finally:
            db._conn.set_authorizer(None)
    persisted_attempt(db, row_for(db))


with tempfile.TemporaryDirectory(prefix='authority-omission-') as scratch:
    cases = []
    cases += [('ordinary/' + name, partial(ordinary_case, name=name)) for name in ORDINARY]
    cases += [('retained/' + name, partial(retained_case, decision=decision, **options)) for name, decision, options in (
        ('escalate', {'action': 'escalate', 'reason': 'Synthetic'}, {}),
        ('blocked-escalate', {'action': 'escalate', 'reason': 'Synthetic'}, {'status': 'blocked'}),
        ('prose', None, {}), ('malformed', {'action': 'unknown'}, {}), ('missing', None, {'summary': ''}),
        ('blocked-missing', None, {'status': 'blocked'}),
        ('supersede', {'action': 'supersede', 'successor_brief': 'Synthetic', 'rationale': 'Synthetic',
            'attestation': dict(recovery_reason='Synthetic', policy_product_intent_unchanged=True,
                no_budget_or_external_commitment=True, no_permission_or_cross_team_change=True,
                no_schema_auth_security_privacy_or_data_access_change=True, no_unresolved_founder_gate=True)}, {}),
        ('workflow_start', {'action': 'workflow_start', 'workflow': dict(format='workflow-coding-start@1',
            operation_key='scratch-start', release=dict(identity='release/happyranch/coding', version=1,
                definition_digest='6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8'),
            mapping=dict(id='scratch', generation=1, digest='a' * 64), implementer_override=None,
            work=dict(brief='Synthetic', repository_id='workflow-repository:' + 'a' * 64, object_format='sha1', base='a' * 40, paths=['runtime']))}, {}),
    )]
    cases += [('lineage/' + name, partial(lineage_case, name=name)) for name in ORDINARY]
    cases += [('replay/later-v2-live', later_retry)]
    cases += [('changed/' + field, partial(changed_case, field=field)) for field in (
        'carrier', 'summary', 'status', 'confidence', 'verdict', 'risks', 'output', 'decision', 'wait_ids', 'local_ci')]
    cases += [('contradiction/' + kind, partial(contradiction_case, kind=kind)) for kind in (
        'orphan_attempt', 'orphan_audit', 'foreign_audit', 'malformed_audit', 'contradictory_audit', 'duplicate_audit', 'marker_missing',
        'marker_binding', 'marker_digest', 'carrier_tamper', 'read_error')]
    cases += [('attribution/' + mode, partial(attribution_case, mode=mode)) for mode in ('omitted', 'attempt', 'legacy')]
    cases += [('hook/missing-attempt', hook_case), ('route/fresh-ordinary', route_admission_case)]
    cases += [('exception/' + seam, partial(exception_case, seam=seam)) for seam in ('parser', 'lineage-read')]
    failures = []
    for index, (name, run) in enumerate(cases):
        db = fixture(Path(scratch) / (str(index) + '.db'))
        try:
            run(db)
        except Exception as error:
            failures.append(name)
            print('FAIL', name, type(error).__name__, str(error))
            traceback.print_exc()
        else:
            print('PASS', name)
        finally:
            db.close()
    print('RESULT', len(cases) - len(failures), 'passed;', len(failures), 'failed;', len(cases), 'cases')
    print('NOT RUN: live-daemon/Product-E2E proof; no serving daemon or Product E2E invoked')
    print('NOT RUN: successful workflow_start route and owned draft/f5/coding-attempt/seq_visit callbacks; require configured workflow/native launch and host evidence; no launch/evidence forging')
    assert not failures, failures
PY
