"""THR304 finite attachment recovery cases; release-only retained integration source."""
from __future__ import annotations

from tests.helpers.integration_stub_guard.guard import require_parent_environment
require_parent_environment()

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from scripts.attachment_scoped_qa import PARAMETERS
from tests.helpers.attachment_feedback_fixture import (
    attachment_snapshot, attachment_supplement_driver, assert_prechild,
    await_condition, corrections, observe, attachment_authorizer,
    attachment_failure_adapter, attachment_fingerprint_comparisons,
)
from runtime.infrastructure.database import Database, LostClaim, PendingRetry, RetryClaim
from runtime.models import TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.orchestrator.run_step import _consume_completion_report

pytestmark=pytest.mark.integration


def delegate(refs=None,then=None):
    return {'action':'delegate','agent':'dev_agent','prompt':'Synthetic intended worker',
            **({'attachments':refs} if refs else {}),**({'then':then} if then else {})}


def fanout(refs=None,nested=None,position=0):
    children=[{'agent':'dev_agent','prompt':'Synthetic plain worker'},
              {'agent':'product_manager','prompt':'Synthetic carrier',
               'then':[{'agent':'dev_agent','prompt':'Synthetic later worker'}]}]
    if position==0:
        children.reverse()
    carrier=next(child for child in children if child.get('then'))
    if refs:
        children[position]['attachments']=refs
    if nested:
        carrier['then'][0]['attachments']=nested
    return {'action':'fanout','children':children,'width_cap_ack':2}


def missing():
    return {'storage_key':'missing-attachment-key','display_name':'missing.txt'}


def graph(driver,task_id,kind):
    snap=await_condition(lambda:(s if s['tasks'][0]['block_kind']=='delegated' else None)
        if (s:=driver.snapshot(task_id))['tasks'] else None)
    tasks=[row for row in snap['tasks'] if row['id']!=task_id]
    assert len(tasks)==(1 if kind=='D' else 2)
    assert all(row['parent_task_id']==task_id for row in tasks)
    assert len({row['id'] for row in tasks})==len(tasks)
    return snap


@pytest.mark.parametrize('variant',PARAMETERS['test_c01_delegate'])
def test_c01_delegate(variant,attachment_live_daemon):
    driver=attachment_live_daemon;task=driver.create();first=driver.launch(task)
    bad=delegate([missing()]) if variant=='direct' else delegate(then=[{'agent':'dev_agent','prompt':'later','attachments':[missing()]}])
    accepted,_=driver.submit(first,bad)
    driver.await_correction(task,1)
    second=driver.launch(task);assert second['session_id']!=first['session_id']
    refs=driver.upload('corrected')
    good=delegate(refs) if variant=='direct' else delegate(then=[{'agent':'dev_agent','prompt':'later','attachments':refs}])
    corrected,_=driver.submit(second,good);assert corrected['id']!=accepted['id']
    snap=graph(driver,task,'D')
    assert len(corrections(snap))==1
    if variant=='direct':
        assert len(snap['links'])==1 and snap['links'][0]['storage_key']==refs[0]['storage_key']
    else:
        child=next(row for row in snap['tasks'] if row['id']!=task)
        parent=next(row for row in snap['tasks'] if row['id']==task)
        assert refs[0]['storage_key'] in parent['active_chain'] and not snap['links']
        assert child['assigned_agent']=='dev_agent'


@pytest.mark.parametrize('variant',PARAMETERS['test_c02_initial_fanout'])
def test_c02_initial_fanout(variant,attachment_live_daemon):
    driver=attachment_live_daemon;task=driver.create();first=driver.launch(task)
    position=0 if variant.endswith('first') else 1
    bad=fanout(position=position)
    if variant.startswith('plain'):
        next(child for child in bad['children'] if not child.get('then'))['attachments']=[missing()]
    elif variant.startswith('carrier'):
        next(child for child in bad['children'] if child.get('then'))['attachments']=[missing()]
    else:
        carrier=next(child for child in bad['children'] if child.get('then'))
        carrier['then'][0]['attachments']=[missing()]
        if not variant.startswith('nested-only'):
            carrier['attachments']=driver.upload('valid-top')
    target=next(child for child in bad['children'] if (not child.get('then'))==variant.startswith('plain'))
    other=next(child for child in bad['children'] if child is not target)
    bad['children']=[target,other] if position==0 else [other,target]
    original,_=driver.submit(first,bad);driver.await_correction(task,1)
    second=driver.launch(task);assert second['session_id']!=first['session_id']
    top=driver.upload('top');nested=driver.upload('nested')
    good=fanout(top,nested,position)
    correction,_=driver.submit(second,good);assert correction['id']!=original['id']
    snap=graph(driver,task,'F')
    root=next(row for row in snap['tasks'] if row['id']==task)
    assert top[0]['storage_key'] in root['active_fanout'] and nested[0]['storage_key'] in root['active_fanout']
    carrier=next(row for row in snap['tasks'] if row['id']!=task and row['active_chain'])
    assert nested[0]['storage_key'] in carrier['active_chain']
    assert len(snap['links'])==1 and snap['links'][0]['storage_key']==top[0]['storage_key']
    with sqlite3.connect(OrgPaths(root=driver.root).db_path.as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE parent_task_id=?',(carrier['id'],)).fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM task_attachments WHERE storage_key=?',(nested[0]['storage_key'],)).fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM task_attachments WHERE storage_key=?',(top[0]['storage_key'],)).fetchone()[0]==1


@pytest.mark.parametrize('variant',PARAMETERS['test_c03_uniqueness_and_count'])
def test_c03_uniqueness_and_count(variant,attachment_live_daemon):
    driver=attachment_live_daemon;task=driver.create();launch=driver.launch(task)
    refs=driver.upload('counts',12)
    if variant.startswith('delegate'):
        n=6 if variant.endswith('six') else 5
        decision=delegate(refs[:n],then=[{'agent':'dev_agent','prompt':'later','attachments':refs[6:11]}] if variant.endswith('over-five') else None)
        bad=variant.endswith('six');kind='D'
    elif variant.startswith('fanout'):
        n=6 if variant.endswith('six') else 5
        decision=fanout(refs[:n],refs[6:11] if variant.endswith('over-five') else None)
        bad=variant.endswith('six');kind='F'
    elif variant in ('direct-direct','direct-then','then-then'):
        decision=delegate([refs[0],refs[0]]) if variant=='direct-direct' else delegate(
            [refs[0]] if variant=='direct-then' else None,
            then=[{'agent':'dev_agent','prompt':'leg','attachments':[refs[0]]}]
                +([{'agent':'dev_agent','prompt':'leg2','attachments':[refs[0]]}] if variant=='then-then' else []))
        bad=True;kind='D'
    else:
        decision=fanout();carrier=next(child for child in decision['children'] if child.get('then'))
        plain=next(child for child in decision['children'] if not child.get('then'))
        if variant=='sibling-sibling':
            carrier['attachments']=plain['attachments']=[refs[0]]
        elif variant=='carrier-nested':
            carrier['attachments']=carrier['then'][0]['attachments']=[refs[0]]
        elif variant=='nested-same-carrier':
            carrier['then'][0]['attachments']=[refs[0]]
            carrier['then'].append({'agent':'dev_agent','prompt':'another','attachments':[refs[0]]})
        elif variant=='nested-cross-siblings':
            carrier['then'][0]['attachments']=[refs[0]]
            plain['then']=[{'agent':'dev_agent','prompt':'another','attachments':[refs[0]]}]
        else:
            plain['attachments']=carrier['then'][0]['attachments']=[refs[0]]
        bad=True;kind='F'
    driver.submit(launch,decision)
    if bad:
        snap=driver.await_correction(task,1)
        code=corrections(snap)[0]['attachment_correction']['error_code']
        assert code==('too_many_attachments' if variant.endswith('six') else 'duplicate_attachment')
    else:
        snap=graph(driver,task,kind)
        assert not corrections(snap) and len(snap['links'])==5


@pytest.mark.parametrize('variant',PARAMETERS['test_c04_error_classes'])
def test_c04_error_classes(variant,attachment_public_observer,attachment_live_daemon):
    driver=attachment_live_daemon;task=driver.create();launch=driver.launch(task)
    ref=driver.upload('classes')[0];refs=[ref]
    codes={'too-many':'too_many_attachments','duplicate':'duplicate_attachment','missing':'task_attachment_not_found',
           'claimed-same-tree':'attachment_already_claimed','unsafe-name':'invalid_attachment_display_name',
           'unsupported-suffix':'unsupported_attachment_type','directory':'task_attachment_not_found',
           'no-suffix':'unsupported_attachment_type','name-201':'invalid_attachment_display_name'}
    if variant=='too-many':
        refs=driver.upload('six',6)
    elif variant=='duplicate':
        refs=[ref,ref]
    elif variant=='missing':
        refs=[missing()]
    elif variant=='unsafe-name':
        refs=[{**ref,'display_name':'unsafe/name.txt'}]
    elif variant=='unsupported-suffix':
        refs=[{**ref,'display_name':'file.exe'}]
    elif variant=='no-suffix':
        refs=[{**ref,'display_name':'file'}]
    elif variant=='name-201':
        refs=[{**ref,'display_name':'x'*197+'.txt'}]
    elif variant=='directory':
        # Only the authorized synthetic scratch store path is modified.
        path=OrgPaths(root=driver.root).task_attachments_dir/ref['storage_key']
        path.unlink();path.mkdir(mode=0o700)
    elif variant=='claimed-same-tree':
        driver.submit(launch,delegate(refs));snap=graph(driver,task,'D')
        maker=next(row for row in snap['tasks'] if row['id']!=task)
        worker=driver.launch(maker['id']);driver.submit(worker,{'action':'done','summary':'synthetic completed maker'})
        launch=driver.launch(task)
        baseline=driver.snapshot(task)
        assert next(row for row in baseline['tasks'] if row['id']==maker['id'])['status']=='completed'
        maker_results=[row for row in baseline['child_results'] if row['task_id']==maker['id']]
        assert len(maker_results)==1 and type(maker_results[0]['id']) is int
        assert maker_results[0]['session_id']==worker['session_id']
        assert baseline['links'][0]['task_id']==maker['id'] and baseline['links'][0]['storage_key']==refs[0]['storage_key']
    elif variant=='invalid-key-subclass':
        refs=[{'storage_key':'../unsafe','display_name':'file.txt'}]
    else:
        # Public producer admission must reject malformed refs before consumer.
        bad={**delegate(),'attachments':[{'display_name':'missing-key.txt'}]}
        receipt=driver.submit(launch,bad,expect_success=False)
        assert receipt['exit']!=0
        assert not corrections(driver.snapshot(task))
        _fixed_exception_observations(driver,attachment_public_observer,'F','validation')
        return
    decision=delegate(refs)
    if variant=='claimed-same-tree':
        decision.update(agent='code_reviewer',expect_verdict='APPROVE')
    driver.submit(launch,decision)
    if variant=='invalid-key-subclass':
        snap=await_condition(lambda:(s if next(row for row in s['tasks'] if row['id']==task)['status']=='failed' else None)
            if (s:=driver.snapshot(task))['tasks'] else None)
        assert not corrections(snap)
    else:
        snap=driver.await_correction(task,1,baseline=baseline if variant=='claimed-same-tree' else None)
        payload=corrections(snap)[0]
        assert payload['attachment_correction']['error_code']==codes[variant]
        reason=payload['decision']['reason']
        assert codes[variant] in reason and 'authorized' in reason and 'replay' in reason
        if variant in ('unsafe-name','no-suffix','unsupported-suffix','name-201','too-many'):
            assert 'storage_key=' not in reason
        else:
            assert refs[0]['storage_key'] in reason
    placements={'invalid-key-subclass':('D','validation'),'no-suffix':('D','prepare'),'directory':('F','prepare')}
    if variant in placements:
        _fixed_exception_observations(driver,attachment_public_observer,*placements[variant])


def _staged_supplement(driver,observer,decision):
    # A second actual accepted result supplies the foreign-ID comparison;
    # its provider remains held until the same owned daemon closure.
    foreign_task=driver.create();foreign_launch=driver.launch(foreign_task)
    foreign_result,_=driver.submit(foreign_launch,decision,exit_provider=False)
    driver.foreign=(foreign_task,foreign_launch,foreign_result)
    task=driver.create();launch=driver.launch(task)
    result,body=driver.submit(launch,decision,exit_provider=False)
    observer.arm(task,result['id'],['before-gate'])
    driver.release_provider(launch)
    pause=observer.pause('before-gate')
    assert pause['result_id']==result['id'] and pause['task_id']==task
    observer.acknowledge_interruption('before-gate')
    closure=observer.interrupt_and_close(pause,driver.port)
    return task,launch,result,body,closure


@pytest.mark.parametrize('variant',PARAMETERS['test_c05_claim_loss'])
def test_c05_claim_loss(variant,attachment_public_observer,attachment_live_daemon):
    driver=attachment_live_daemon
    task,launch,result,body,closure=_staged_supplement(driver,attachment_public_observer,delegate([missing()]))
    with attachment_supplement_driver(driver,closure) as orch:
        db=orch._db
        # The real common body appends the original decision audit. Pause at
        # the actual attachment writer boundary, before any A transaction.
        report=completion_report_from_result_row(task,result,fallback_agent=launch['agent'])
        from tests.helpers.attachment_feedback_fixture import attachment_writer_barrier
        phase_name='before-A' if variant.startswith('before') or variant=='B-not-configured' else 'after-A'
        next_scheduler=None
        with attachment_writer_barrier(orch,task,report,result['id'],phase_name) as boundary:
            claim=boundary['claim']
            rival=Database(OrgPaths(root=driver.root).db_path)
            try:
                if variant.endswith('cancel'):
                    rival.update_task(task,status=TaskStatus.CANCELLED,block_kind=None,
                        note='cancelled by founder: synthetic contender',cancelled_at='2026-10-13T00:00:00+00:00')
                elif variant.endswith('rival-feedback'):
                    assert isinstance(rival.try_retry_feedback(claim,'genuine independent feedback contender'),PendingRetry)
                elif variant.endswith('new-step'):
                    if phase_name=='before-A':
                        assert isinstance(rival.try_retry_feedback(claim,'genuine new-step feedback'),PendingRetry)
                    from tests.helpers.attachment_feedback_fixture import genuine_scheduler_contender
                    next_scheduler=genuine_scheduler_contender(orch,driver,task)
                    assert next_scheduler[1]['session_id']!=claim.current_session_id
                    assert db.get_task(task).orchestration_step_count==claim.step+1
                elif variant=='B-not-configured':
                    orch._queue=None
                    foreign_task,foreign_launch,foreign_result=driver.foreign
                    foreign=RetryClaim.from_task(db.get_task(foreign_task),result_row_id=foreign_result['id'])
                    pending=attachment_fingerprint_comparisons(db,claim,foreign)
                    assert db.admit_retry_feedback(pending,None)=='not_configured'
                    before_failure=driver.snapshot(task)
                    entries=[]
                    def enqueue_raises():
                        assert db._conn.in_transaction
                        entries.append(True)
                        raise RuntimeError('fixed enqueue failure before insertion')
                    with pytest.raises(RuntimeError,match='fixed enqueue failure'):
                        db.admit_retry_feedback(pending,enqueue_raises)
                    assert entries==[True] and driver.snapshot(task)==before_failure
                    with sqlite3.connect(OrgPaths(root=driver.root).db_path,timeout=.1) as check:
                        check.execute('BEGIN IMMEDIATE');check.rollback()
                    observe('C5.enqueue-raises',trigger='real B enqueue callable throws before insertion',
                        expected='A retained, reservation released',observable='exact rows retained, independent writer reservation acquired',
                        defect='refunded opportunity or leaked lock')
                elif variant=='B-wins-then-cancel':
                    from runtime.daemon.queue import TaskQueue
                    orch._queue=TaskQueue()
                boundary['release'].set()
            finally:
                rival.close()
        if next_scheduler is not None:
            thread,actual_launch,errors=next_scheduler
            from scripts.attachment_scoped_qa import write_receipt
            write_receipt(driver.controls/f"command-{actual_launch['session_id']}.json",{'exit_without_callback':True},4096)
            thread.join(5);assert not thread.is_alive()
        snapshot=driver.snapshot(task)
        if phase_name=='before-A' and variant!='B-not-configured':
            assert not corrections(snapshot)
        else:
            assert len(corrections(snapshot))==1
        if variant=='after-A-cancel':
            observe('C7.ledger-integrity.lost-B',trigger='real A COMMIT then independent cancellation before B',
                expected='committed opportunity retained, no refund',observable='one real marker and winning cancelled projection',
                defect='lost enqueue refunds opportunity')
        if variant=='B-wins-then-cancel':
            assert len(boundary['queue_events'])==1 and boundary['queue_events'][0]['reservation'] is True
            queued=orch._queue._queue.get_nowait()
            assert queued[0:2]==(orch._slug,task)
            db.update_task(task,status=TaskStatus.CANCELLED,block_kind=None,note='cancelled by founder: after B',cancelled_at='2026-10-13T00:00:00+00:00')
            assert not db.try_claim_for_step(task,TaskStatus.PENDING,None,claim.step+1)


@pytest.mark.parametrize('variant',PARAMETERS['test_c06_receipts'])
def test_c06_receipts(variant,attachment_public_observer,attachment_live_daemon,request):
    driver=attachment_live_daemon;observer=attachment_public_observer
    if variant.startswith('ordinary'):
        task=driver.create();launch=driver.launch(task)
        decision=delegate([missing()]) if 'delegate' in variant else fanout([missing()])
        result,body=driver.submit(launch,decision)
        driver.await_correction(task,1)
        before=driver.snapshot(task)
        response=driver.repeat_callback(body)
        assert response.status_code in (200,409)
        assert driver.snapshot(task)==before
        if 'delegate' in variant:
            observe('C6.identity-controls.ordinary-duplicate',trigger='identical public callback repeat',expected='read-only duplicate',
                    observable='same exact persisted projection',defect='reconsumed ordinary result')
        return
    request.getfixturevalue('attachment_policy_bootstrap')
    normal=_normal_v2_controls(driver) if variant=='v2-applied' else None
    # A distinct genuinely admitted result supplies the foreign-result input.
    foreign_task=driver.create();foreign_launch=driver.launch(foreign_task)
    foreign_result,_=driver.submit(foreign_launch,delegate(),exit_provider=False)
    task=driver.create();first=driver.launch(task)
    assert first['self_evaluation'] is not None
    original,_=driver.submit(first,{'action':'escalate','reason':'Synthetic authorized bounded continuation'})
    second=driver.launch(task);assert second['session_id']!=first['session_id']
    r2,body=driver.submit(second,delegate([missing()]),exit_provider=False)
    if variant=='v2-applied':
        pause_phase='after-ACK-before-wrapper-return'
    elif variant=='v2-claimed-before-body':
        pause_phase='claimed-before-body'
    elif variant=='v2-claimed-after-A':
        pause_phase='after-A-before-B'
    else:
        pause_phase='before-ACK'
    observer.arm(task,r2['id'],['ready-before-claim',pause_phase])
    driver.release_provider(second)
    ready=observer.pause('ready-before-claim')
    setup=driver.snapshot(task)
    envelope=next(row for row in setup['envelopes'] if row['spending_result_id']==r2['id'])
    assert envelope['decision_state']=='ready' and envelope['result_id']==original['id']
    assert setup['attempts']
    from tests.helpers.attachment_feedback_fixture import v2_lineage_readback
    lineage=v2_lineage_readback(driver,task,first,original,second,r2)
    assert ready['report_sha256'] is not None and ready['causal_result_id']==original['id']
    # Provider exit is required independently before the consumer-owned interruption.
    assert (driver.controls/f"provider-exit-{second['session_id']}.json").exists()
    observer.release('ready-before-claim')
    pause=observer.pause(pause_phase)
    before=driver.snapshot(task)
    envelope=next(row for row in before['envelopes'] if row['spending_result_id']==r2['id'])
    assert envelope['decision_state']==('applied' if variant=='v2-applied' else 'claimed')
    if variant=='v2-applied':
        events=[row for row in before['audits'] if row['payload'] and json.loads(row['payload']).get('stage')=='decision_applied']
        assert len(events)==1
        event=json.loads(events[0]['payload'])
        assert event['spending_result_id']==r2['id'] and event['result_id']==original['id']
        assert event['generation_id']==lineage['dispatch'][0]['generation_id'] and event['report_digest']
        assert pause['report_sha256']==ready['report_sha256']
    if variant=='v2-claimed-before-body':
        assert not corrections(before)
    else:
        assert len(corrections(before))==1
    if variant in ('v2-claimed-after-A','v2-refused-repeat'):
        observe('C6.v2-claimed-after-A.'+('after-A-before-B' if variant=='v2-claimed-after-A' else 'before-ACK'),
            trigger='public source-bound '+pause_phase+' independent claimed+effect readback',
            expected='original claimed identity, committed A retained through interruption',
            observable='actual phase/token/causal/spending IDs and exact marker/result',
            defect='preACK and afterA conflated',public_pause=pause,projection_sha256=__import__('hashlib').sha256(json.dumps(before,sort_keys=True).encode()).hexdigest())
    observer.acknowledge_interruption(pause_phase)
    closure=observer.interrupt_and_close(pause,driver.port)
    with attachment_supplement_driver(driver,closure) as orch:
        report=completion_report_from_result_row(task,r2,fallback_agent=second['agent'])
        if variant=='v2-applied':
            from tests.helpers.attachment_feedback_fixture import consumer_observation,retain_node_receipt
            retain_node_receipt(driver.node,'postACK-public.json',{'pause':pause,'closure':closure,'event':event,'lineage_result':original['id']})
            with consumer_observation(orch,task,r2['id']) as entries:
                _consume_completion_report(orch,task,report,result_row_id=r2['id'],reclaim_terminal_worktree=False)
        else:
            from tests.helpers.attachment_feedback_fixture import consumer_observation
            with consumer_observation(orch,task,r2['id']):
                _consume_completion_report(orch,task,report,result_row_id=r2['id'],reclaim_terminal_worktree=False)
        after=driver.snapshot(task)
        current=next(row for row in after['envelopes'] if row['spending_result_id']==r2['id'])
        assert current['decision_state']==('applied' if variant=='v2-applied' else 'refused')
        assert after['results']==before['results'] and after['links']==before['links']
        assert after['child_results']==before['child_results']
        assert after['attempts']==before['attempts']
        assert [row for row in after['envelopes'] if row['spending_result_id']!=r2['id']]==[row for row in before['envelopes'] if row['spending_result_id']!=r2['id']]
        if variant=='v2-claimed-before-body':
            prior=next(row for row in before['tasks'] if row['id']==task)
            now=next(row for row in after['tasks'] if row['id']==task)
            assert prior['status']=='in_progress' and prior['block_kind'] is None
            assert now['status']=='escalated' and now['block_kind'] is None
            assert now['note']=='authority_v2_decision_dispatch_interrupted'
            assert {key:value for key,value in now.items() if key not in ('status','block_kind','note','updated_at')}=={key:value for key,value in prior.items() if key not in ('status','block_kind','note','updated_at')}
            assert [row for row in after['tasks'] if row['id']!=task]==[row for row in before['tasks'] if row['id']!=task]
        else:
            assert after['tasks']==before['tasks']
        assert corrections(after)==corrections(before)
        assert {key:value for key,value in current.items() if key not in ('decision_state','canonical_payload_json')}=={key:value for key,value in envelope.items() if key not in ('decision_state','canonical_payload_json')}
        prior_body=json.loads(envelope['canonical_payload_json']);current_body=json.loads(current['canonical_payload_json'])
        assert {key:value for key,value in prior_body.items() if key!='decision_state'}=={key:value for key,value in current_body.items() if key!='decision_state'}
        assert current_body['decision_state']==current['decision_state']
        if variant!='v2-applied':
            added=[row for row in after['audits'] if row not in before['audits']]
            interruptions=[row for row in added if row['payload'] and json.loads(row['payload']).get('stage')=='decision_dispatch_interrupted']
            assert len(interruptions)==1
            event_payload=json.loads(interruptions[0]['payload'])
            assert event_payload['spending_result_id']==r2['id'] and event_payload['result_id']==original['id']
            assert event_payload['generation_id']==lineage['dispatch'][0]['generation_id']
            escalations=[row for row in added if row['action']=='escalation']
            assert len(escalations)==(1 if variant=='v2-claimed-before-body' else 0)
            assert len(added)==1+len(escalations)
            assert all(row in after['audits'] for row in before['audits'])
        frozen=driver.snapshot(task)
        if variant!='v2-applied':
            with consumer_observation(orch,task,r2['id']):
                _consume_completion_report(orch,task,report,result_row_id=r2['id'],reclaim_terminal_worktree=False)
            assert driver.snapshot(task)==frozen
        if variant=='v2-applied':
            assert after==before
            retain_node_receipt(driver.node,'postACK-sameR2-consumer.json',{'result_id':r2['id'],'invocations':entries,
                'before_sha256':__import__('hashlib').sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
                'after_sha256':__import__('hashlib').sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()})
            observe('C6.v2-applied.public-after-ACK-interruption',trigger='public daemon interruption after independent applied+event readback',
                expected='SOLE SAME-R2 common consumer read-only skip',observable='all original rows identical, no refusal/body/A/ACK/effect',
                defect='applied receipt treated as replayable claimed state',public_pause=pause,public_closure=closure,
                supplemental_result_id=r2['id'])
            normal_task,normal_launch,normal_result,_=normal
            normal_report=completion_report_from_result_row(normal_task,normal_result,fallback_agent=normal_launch['agent'])
            normal_before=driver.snapshot(normal_task)
            with consumer_observation(orch,normal_task,normal_result['id']):
                _consume_completion_report(orch,normal_task,normal_report,result_row_id=normal_result['id'],reclaim_terminal_worktree=False)
            assert driver.snapshot(normal_task)==normal_before
            observe('C6.identity-controls.applied-repeat',trigger='distinct normal-applied control, exact persisted report',
                expected='read-only skip',observable='normal public ACK projection retained, zero body entries',defect='applied body replay')
            changed_report=normal_report.model_copy(update={'output_summary':normal_report.output_summary+' changed'})
            _consume_completion_report(orch,normal_task,changed_report,result_row_id=normal_result['id'],reclaim_terminal_worktree=False)
            _consume_completion_report(orch,normal_task,normal_report,result_row_id=foreign_result['id'],reclaim_terminal_worktree=False)
            assert driver.snapshot(normal_task)==normal_before
            observe('C6.identity-controls.foreign-result',trigger='real different-task admitted result ID and changed input material',
                expected='skip/refuse before body',observable='normal control exact rows remain',defect='foreign result/body admitted',foreign_result_id=foreign_result['id'])
        if variant=='v2-refused-repeat':
            observe('C6.identity-controls.refused-repeat',trigger='SAME-R2 refused repeat',expected='read-only skip',
                observable='exact frozen projection',defect='refused body replay')


@pytest.mark.parametrize('variant',PARAMETERS['test_c07_two_opportunities'])
def test_c07_two_opportunities(variant,attachment_live_daemon,attachment_public_observer):
    driver=attachment_live_daemon
    if variant in ('root-DDD','nonroot-DDD'):
        _revise_cap_observation(driver,variant=='nonroot-DDD')
    if variant in ('root-FDF','nonroot-FDF'):
        _stop_owner_observation(driver,attachment_public_observer,variant=='nonroot-FDF')
        return
    if variant=='same-claim-contenders':
        from tests.helpers.attachment_feedback_fixture import attachment_writer_barrier
        task,launch,result,body,closure=_staged_supplement(driver,attachment_public_observer,delegate([missing()]))
        with attachment_supplement_driver(driver,closure) as orch:
            report=completion_report_from_result_row(task,result,fallback_agent=launch['agent'])
            with attachment_writer_barrier(orch,task,report,result['id'],'before-A') as boundary:
                claim=boundary['claim'];barrier=threading.Barrier(3);outcomes=[]
                def contender():
                    database=Database(OrgPaths(root=driver.root).db_path)
                    try:
                        barrier.wait(timeout=2)
                        outcomes.append(database.try_attachment_retry_feedback(claim,'genuine contender','task_attachment_not_found'))
                    finally:
                        database.close()
                threads=[threading.Thread(target=contender) for _ in range(2)]
                for thread in threads:thread.start()
                barrier.wait(timeout=2)
                for thread in threads:thread.join(timeout=3);assert not thread.is_alive()
                assert sum(isinstance(x,PendingRetry) for x in outcomes)==1
                assert sum(isinstance(x,LostClaim) for x in outcomes)==1
                boundary['release'].set()
        assert len(corrections(driver.snapshot(task)))==1
        observe('C7.ledger-integrity.duplicate-claim',trigger='two real simultaneous writers on original claim',
            expected='one real A, one lost claim',observable='one PendingRetry/one LostClaim and one marker',
            defect='non-atomic count/read/write')
        return
    task=driver.create()
    if variant.startswith('nonroot'):
        initial=driver.launch(task)
        driver.submit(initial,{'action':'fanout','children':[{'agent':'engineering_head','prompt':'Synthetic decision-owning nested manager'},
            {'agent':'dev_agent','prompt':'Synthetic waiting sibling'}],'width_cap_ack':2})
        parent=task
        snap=graph(driver,parent,'F')
        task=next(row['id'] for row in snap['tasks'] if row['id']!=parent and row['assigned_agent']=='engineering_head')
        assert next(row for row in snap['tasks'] if row['id']==task)['task_type']=='task'
    sequence='DD' if variant=='valid-after-two' else variant.split('-')[-1]
    result_ids=[];sessions=[]
    for index,kind in enumerate(sequence):
        launch=driver.launch(task);sessions.append(launch['session_id'])
        decision=delegate([missing()]) if kind=='D' else fanout([missing()])
        result,_=driver.submit(launch,decision);result_ids.append(result['id'])
        if index<2:
            driver.await_correction(task,index+1)
        if index==0 and sequence=='DFD':
            unrelated=driver.launch(task)
            before_unrelated=driver.snapshot(task)
            unrelated_result,_=driver.submit(unrelated,{'action':'delegate','agent':'code_reviewer','prompt':'Synthetic missing reviewer expectation',
                'then':[{'agent':'dev_agent','prompt':'Synthetic genuinely gated downstream'}]})
            # The real ordinary authoring path records untagged feedback and
            # launches another authorized SID without spending attachment count.
            untagged=await_condition(lambda:(s if len([row for row in s['results'] if row['session_id']==''])==2 else None) if (s:=driver.snapshot(task)) else None)
            assert_prechild(untagged,task,1)
            assert untagged['links']==before_unrelated['links']
            feedback=next(row for row in untagged['results'] if row['session_id']=='' and row['id']>unrelated_result['id'])
            assert 'expect_verdict' in feedback['output_summary']
            assert not any(json.loads(row['payload']).get('attachment_correction',{}).get('accepted_result_id')==unrelated_result['id'] for row in untagged['audits'] if row['payload'])
            if variant=='root-DFD':
                observe('C7.ledger-integrity.untagged-unrelated',trigger='real reviewer omission feedback',
                    expected='legacy feedback spends no attachment chance',observable='real generic result and unchanged marker count',
                    defect='charging text or untagged history')
    assert len(set(sessions))==len(sessions) and len(set(result_ids))==len(result_ids)
    if variant=='valid-after-two':
        launch=driver.launch(task);assert launch['session_id'] not in sessions
        result,body=driver.submit(launch,delegate(driver.upload('valid-after-two')),exit_provider=False)
        assert result['id'] not in result_ids
        observer=attachment_public_observer
        observer.arm(task,result['id'],['before-gate']);driver.release_provider(launch)
        pause=observer.pause('before-gate');observer.acknowledge_interruption('before-gate')
        closure=observer.interrupt_and_close(pause,driver.port)
        from tests.helpers.attachment_feedback_fixture import consume_staged
        with attachment_supplement_driver(driver,closure) as orch:
            _ledger_observations(orch,driver,task)
            consume_staged(orch,(task,launch,result,body))
        assert len(corrections(graph(driver,task,'D')))==2
    else:
        expected='failed' if variant.startswith('nonroot') else 'escalated'
        snap=await_condition(lambda:(s if next(row for row in s['tasks'] if row['id']==task)['status']==expected else None)
            if (s:=driver.snapshot(task))['tasks'] else None)
        assert len(corrections(snap))==2 and len(snap['results'])==(7 if sequence=='DFD' else 5)
        if expected=='escalated':
            escalation=[row for row in snap['audits'] if row['action']=='escalation']
            denominator=[row for row in snap['audits'] if row['action']=='authority_hook' and row['payload'] and
                         json.loads(row['payload']).get('reason_code')=='attachment_correction_exhausted']
            assert len(escalation)==len(denominator)==1
            assert json.loads(denominator[0]['payload'])['causal_escalation_audit_id']==escalation[0]['id']
        else:
            stopped=next(row for row in snap['tasks'] if row['id']==task)
            assert stopped['completed_at'] is not None and stopped['active_chain'] is None and stopped['active_fanout'] is None


@pytest.mark.parametrize('variant',PARAMETERS['test_c08_transaction_boundary'])
def test_c08_transaction_boundary(variant,attachment_public_observer,attachment_live_daemon):
    driver=attachment_live_daemon;observer=attachment_public_observer
    if variant=='missing-before-prepare':
        _exists_stat_observations(driver,observer)
    elif variant=='competing-key-claim':
        _real_race_observations(driver,observer)
    elif variant=='allocated-child-collision':
        refs=driver.upload('rollback',4)
        recipes=[{'kind':kind,'mode':mode,'subid':subid,
                  'decision':delegate([refs[index]]) if kind=='D' else fanout([refs[index]])}
            for index,(kind,mode,subid) in enumerate([
                ('D','spawn-audit','C8.spawn-audit-deny.D'),('F','spawn-audit','C8.spawn-audit-deny.F'),
                ('D','spawn-COMMIT','C8.COMMIT-deny.spawn-D'),('F','spawn-COMMIT','C8.COMMIT-deny.spawn-F')])]
        _fault_batch(driver,observer,recipes)
    elif variant=='writer-lock-conflict':
        _stop_rollback_observations(driver,observer)
    else:
        _postcommit_observations(driver,observer)



def _fixed_exception_observations(driver,observer,kind,site):
    from tests.helpers.attachment_feedback_fixture import stage_case_batches,consume_staged
    from runtime.orchestrator import run_step as module
    from runtime.infrastructure import task_attachment_store as store
    class KnownLooking(ValueError):pass
    inputs={
        'non-JSON':ValueError('not-json'), 'JSON-scalar':ValueError('1'),
        'JSON-list':ValueError('[]'), 'unknown-code':ValueError('{"code":"unknown"}'),
        'missing-field':ValueError('{"code":"duplicate_attachment"}'),
        'wrong-key-type':ValueError('{"code":"duplicate_attachment","storage_key":1}'),
        'bool-max':ValueError('{"code":"too_many_attachments","max":true}'),
        'wrong-max':ValueError('{"code":"too_many_attachments","max":6}'),
        'extra-member':ValueError('{"code":"invalid_attachment_display_name","extra":1}'),
        'duplicate-member':ValueError('{"code":"invalid_attachment_display_name","code":"invalid_attachment_display_name"}'),
        'ValueError-subclass':KnownLooking('{"code":"invalid_attachment_display_name"}'),
        'nonstring-argument':ValueError(1), 'multiple-args':ValueError('one','two'),
    }
    refs=driver.upload('fixed-input')
    decisions=[delegate(refs) if kind=='D' else fanout(refs) for _ in inputs]
    for driver,staged,closure,offset in stage_case_batches(driver,observer,decisions):
        with attachment_supplement_driver(driver,closure) as orch:
            target=module if site=='prepare' or kind=='D' else store
            attr='_prepare_attachment_params' if site=='prepare' else '_validate_decision_attachments' if kind=='D' else 'validate_task_attachment_refs'
            for (label,exception),item in zip(list(inputs.items())[offset:offset+len(staged)],staged,strict=True):
                before=driver.snapshot(item[0]);caught=None
                with attachment_failure_adapter(target,attr,exception) as entries:
                    try:consume_staged(orch,item)
                    except (ValueError,TypeError,AttributeError,KeyError) as exc:caught=type(exc).__name__
                assert len(entries)==1
                after=driver.snapshot(item[0])
                assert not corrections(after) and not after['links'] and len(after['tasks'])==1
                assert after['results']==before['results']
                task=after['tasks'][0]
                assert task['active_chain'] is None and task['active_fanout'] is None
                # Existing D catches all ValueError; F preparation propagates;
                # F validation retains its old JSON/parser/hard-failure boundary.
                assert task['status']=='failed' or caught is not None
                observe(f'C4.errors.fixed.{kind}.{site}.{label}',trigger='fixed failure-only '+kind+'/'+site,
                    expected='original terminal/parser/propagation boundary, zero attachment charge or child',
                    observable='actual exception/terminal and exact accepted rows retained',
                    defect='broad attachment recovery catch',exception=type(exception).__name__,args=repr(exception.args),
                    propagated=caught,status=task['status'],result_id=item[2]['id'],
                    batch_prerequisites=getattr(driver,'batch_prerequisites',None))


def _nested_manager(driver):
    parent=driver.create();launch=driver.launch(parent)
    driver.submit(launch,{'action':'fanout','children':[
        {'agent':'engineering_head','prompt':'Synthetic actual nested decision owner'},
        {'agent':'dev_agent','prompt':'Synthetic bounded waiting sibling'}],'width_cap_ack':2})
    snapshot=graph(driver,parent,'F')
    child=next(row for row in snapshot['tasks'] if row['parent_task_id']==parent and row['assigned_agent']=='engineering_head')
    assert child['task_type']=='task'
    return child['id']


def _revise_cap_observation(driver,nonroot):
    task=_nested_manager(driver) if nonroot else driver.create()
    revisions=[];workers=[]
    for expected_revision in range(3):
        launch=driver.launch(task)
        accepted,_=driver.submit(launch,delegate())
        snap=await_condition(lambda:(s if next(row for row in s['tasks'] if row['id']==task)['block_kind']=='delegated' else None)
            if (s:=driver.snapshot(task))['tasks'] else None)
        parent=next(row for row in snap['tasks'] if row['id']==task)
        assert parent['revision_count']==expected_revision
        revisions.append(parent['revision_count'])
        child=next(row for row in snap['tasks'] if row['parent_task_id']==task and row['status'] not in ('completed','failed'))
        actual=driver.launch(child['id'])
        result,_=driver.submit(actual,{'action':'done','summary':'Synthetic genuine worker-of-record completion'})
        workers.append({'task':child['id'],'sid':actual['session_id'],'result':result['id'],'parent_result':accepted['id']})
    launch=driver.launch(task);driver.submit(launch,delegate())
    expected='failed' if nonroot else 'escalated'
    snap=await_condition(lambda:(s if next(row for row in s['tasks'] if row['id']==task)['status']==expected else None)
        if (s:=driver.snapshot(task))['tasks'] else None)
    parent=next(row for row in snap['tasks'] if row['id']==task)
    assert 'revise budget (2 rounds) exhausted' in parent['note']
    assert parent['revision_count']==2 and not corrections(snap)
    assert len([row for row in snap['tasks'] if row['parent_task_id']==task])==3
    if not nonroot:
        assert any(row['action']=='authority_hook' and 'runtime_revise_budget_exhausted' in (row['payload'] or '') for row in snap['audits'])
    observe('C7.positive-revise-cap.'+('nonroot' if nonroot else 'root'),
        trigger='real public delegate/worker callback cycles with configured positive cap2',
        expected='ordinary revision0,1,2 then existing cap stop, zero attachment charge',
        observable='actual worker task/result/SID, revisions, terminal projection and root denominator',
        defect='attachment budget resets or replaces revise limit',workers=workers,revisions=revisions)


def _ledger_observations(orch,driver,task):
    from tests.helpers.attachment_feedback_fixture import attachment_history_failure_only_read_adapter,independent_projection
    db=orch._db
    variants={
        'duplicate-key':['malformed-json','duplicate-key','wrong-version','bool-version','wrong-code','bool-ordinal','bool-id','extra-marker'],
        'nonstandard-constant':['nonstandard-constant'], 'ordinal-gap':['ordinal-gap','third-ordinal','wrong-step'],
        'duplicate-accepted':['duplicate-accepted','cross-role'], 'duplicate-feedback':['duplicate-feedback'],
        'orphan-result':['orphan-result','missing-accepted'],
        'status-mismatch':['status-mismatch','feedback-status','feedback-confidence','feedback-sid','feedback-decision','accepted-decision'],
        'owner-mismatch':['owner-mismatch','wrong-task','wrong-actor','wrong-sid'],
        'reason-mismatch':['reason-mismatch','wrong-action'], 'ambiguous-step':['ambiguous-step'],
    }
    baseline=independent_projection(driver,task)
    positive=db._attachment_correction_history_txn(task)
    assert [row['opportunity'] for row in positive]==[1,2]
    for subid,inputs in variants.items():
        assertions=[]
        for variant in inputs:
            with attachment_history_failure_only_read_adapter(db,task,variant):
                with pytest.raises(ValueError):db._attachment_correction_history_txn(task)
            assert independent_projection(driver,task)==baseline
            assertions.append(variant)
        observe('C7.ledger-integrity.'+subid,trigger='SYNTHETIC INTEGRITY NEGATIVE read copies',
            expected='strict refusal, no durable mutation or queue',observable='ValueError and identical independent projection',
            defect='ambiguous ledger accepted or historical bytes rewritten',variants=assertions)


def _stop_owner_observation(driver,observer,nonroot):
    from tests.helpers.attachment_feedback_fixture import source_barrier
    from runtime.orchestrator import run_step as module
    task=_nested_manager(driver) if nonroot else driver.create()
    for ordinal,kind in enumerate('FD',1):
        launch=driver.launch(task);driver.submit(launch,delegate([missing()]) if kind=='D' else fanout([missing()]))
        driver.await_correction(task,ordinal)
    launch=driver.launch(task)
    decision=fanout([missing()]);decision['action']='parallel'
    result,body=driver.submit(launch,decision,exit_provider=False)
    assert body['decision']['action']=='parallel'
    assert json.loads(result['decision_json'])['action']=='fanout'
    report=completion_report_from_result_row(task,result,fallback_agent=launch['agent'])
    assert report.decision.action=='fanout'
    before_stop=driver.snapshot(task)
    assert len(corrections(before_stop))==2
    assert {row['attachment_correction']['accepted_session_id'] for row in corrections(before_stop)}=={row['session_id'] for row in before_stop['results'] if row['session_id'] and row['id']!=result['id']}
    observer.arm(task,result['id'],['before-gate']);driver.release_provider(launch)
    pause=observer.pause('before-gate');observer.acknowledge_interruption('before-gate')
    closure=observer.interrupt_and_close(pause,driver.port)
    with attachment_supplement_driver(driver,closure) as orch:
        staged=(task,launch,result,body)
        # Return from real A-or-stop writer, before any owned tail initiation.
        with source_barrier(orch,staged,function='_attachment_feedback_and_reenqueue',
                anchor='try_attachment_retry_feedback',after=True) as boundary:
            stopped=boundary['locals']['outcome']
            assert module._attachment_stop_owner_current(orch,stopped)
            before=driver.snapshot(task)
            rival=Database(OrgPaths(root=driver.root).db_path)
            try:
                # Existing supported task UPDATE is a genuine independent writer;
                # this is explicitly lifecycle comparison, not public cancel API.
                rival.update_task(task,status=TaskStatus.FAILED,block_kind=None,
                    note='cancelled by founder: synthetic independent stop contender',
                    cancelled_at='2026-10-13T00:00:00+00:00')
            finally:rival.close()
            won=driver.snapshot(task)
            assert not module._attachment_stop_owner_current(orch,stopped)
            boundary['release'].set()
        after=driver.snapshot(task)
        assert after==won and len(corrections(before))==len(corrections(after))==2
        assert len(after['results'])==5
        observe('C7.stop-owner-loss.'+('nonroot' if nonroot else 'root'),
            trigger='independent lifecycle writer commits after real stop before tails',
            expected='all subsequent stale tail initiations suppressed; committed stop retained in history',
            observable='exact contender projection remains, real stopped predicate changes true to false, no new audit/result',
            defect='note-only/stale stop ownership fence',stop_audit_pair=stopped.audit_pair,
            limitations='external cross-process/cross-await delivery after initiation remains outside fence')


def _fault_batch(driver,observer,recipes):
    """Each recipe owns a fresh publicly admitted report and one fixed fault."""
    from tests.helpers.attachment_feedback_fixture import stage_cases,source_barrier
    staged,closure=stage_cases(driver,observer,[recipe['decision'] for recipe in recipes])
    with attachment_supplement_driver(driver,closure) as orch:
        for recipe,item in zip(recipes,staged,strict=True):
            task=item[0];kind=recipe['kind'];mode=recipe['mode']
            baseline=driver.snapshot(task)
            if mode.startswith('A'):
                function='_attachment_feedback_and_reenqueue';anchor='try_attachment_retry_feedback'
            else:
                function='_consume_completion_report_body' if kind=='D' else '_spawn_fanout_children'
                anchor='try_delegate' if kind=='D' else 'try_delegate_many'
            with source_barrier(orch,item,function=function,anchor=anchor) as boundary:
                with attachment_authorizer(orch._db,commit='COMMIT' in mode,audit='audit' in mode) as evidence:
                    boundary['release'].set()
                    # Join here so authorizer remains installed through the real attempt.
                    await_condition(lambda:bool(boundary['errors']))
                statements,denials=evidence
            after=driver.snapshot(task)
            assert denials and any('BEGIN IMMEDIATE' in sql for sql in statements)
            assert any('ROLLBACK' in sql for sql in statements)
            assert after['tasks']==baseline['tasks'] and after['results']==baseline['results'] and after['links']==baseline['links']
            assert not corrections(after)
            assert any('INSERT' in sql for sql in statements)
            observe(recipe['subid'],trigger='victim-only SQLite preparation '+mode+' denial',
                expected='real rollback of losing transaction only',observable='BEGIN/prior writes/denial/ROLLBACK and independent exact row projection',
                defect='split transaction, partial graph or premature success',denials=denials,trace=statements,
                exception=boundary['errors'],result_id=item[2]['id'])


def _exists_stat_observations(driver,observer):
    from tests.helpers.attachment_feedback_fixture import stage_cases,source_barrier
    refs=driver.upload('exists-stat',2)
    decisions=[delegate([refs[0]]),fanout([refs[1]])]
    fault_decisions=[delegate([missing()]),fanout([missing()]),delegate([missing()])]
    staged,closure=stage_cases(driver,observer,decisions+fault_decisions)
    with attachment_supplement_driver(driver,closure) as orch:
        for kind,item,ref in zip(('D','F'),staged[:2],refs,strict=True):
            baseline=driver.snapshot(item[0])
            with source_barrier(orch,item,function='_prepare_attachment_params',anchor='stat') as boundary:
                path=boundary['locals']['path']
                info=path.stat()
                assert path==OrgPaths(root=driver.root).task_attachments_dir/ref['storage_key'] and path.is_file()
                path.unlink()
                boundary['release'].set()
            after=driver.snapshot(item[0])
            assert any(error[0]=='FileNotFoundError' for error in boundary['errors'])
            assert after['tasks']==baseline['tasks'] and after['results']==baseline['results'] and not after['links']
            assert not corrections(after)
            observe('C8.exists-stat.'+kind,trigger='actual exists returned true, owned inode unlinked before unchanged stat',
                expected='raw FileNotFoundError preserved, no recovery/child',
                observable='source/line/inode/handshake/error and unchanged durable projection',
                defect='raw OSError swallowed as recoverable structured absence',inode=info.st_ino,device=info.st_dev,
                source=boundary['source'],line=boundary['line'],errors=boundary['errors'])
        _fault_staged(orch,driver,staged[2:], [('D','A-audit','C8.A-audit-deny.D'),('F','A-audit','C8.A-audit-deny.F'),('D','A-COMMIT','C8.COMMIT-deny.A')])


def _postcommit_observations(driver,observer):
    from tests.helpers.attachment_feedback_fixture import stage_cases,consume_staged
    from runtime.orchestrator import run_step as module
    refs=driver.upload('postcommit',3)
    parent=driver.create();launch=driver.launch(parent)
    driver.submit(launch,fanout([refs[1]]));public_graph=graph(driver,parent,'F')
    worker=next(row for row in public_graph['tasks'] if row['parent_task_id']==parent and not row['active_chain'])
    worker_launch=driver.launch(worker['id'])
    worker_result,worker_body=driver.submit(worker_launch,{'action':'done','summary':'Synthetic genuine postgraph completion'},exit_provider=False)
    recipes=[('fanout_spawned',fanout([refs[0]])),('queue',delegate([refs[2]]))]
    staged,closure=stage_cases(driver,observer,[decision for _,decision in recipes])
    recipes.append(('history',None));staged.append((worker['id'],worker_launch,worker_result,worker_body))
    with attachment_supplement_driver(driver,closure) as orch:
        for (label,_),item in zip(recipes,staged,strict=True):
            target=orch._audit if label=='fanout_spawned' else orch if label=='history' else module
            name='log_fanout_spawned' if label=='fanout_spawned' else '_update_task_history' if label=='history' else '_enqueue_task_generation_aware'
            committed=[]
            def predicate(*args,**kwargs):
                if label=='history' and (not args or args[0]!=item[0]):return False
                snapshot=driver.snapshot(item[0])
                if label=='history':
                    snapshot=driver.snapshot(parent)
                    assert next(row for row in snapshot['tasks'] if row['id']==item[0])['status']=='completed'
                    assert snapshot['links']==public_graph['links'] and len(snapshot['tasks'])==3
                else:
                    assert len(snapshot['tasks'])>=2 and len(snapshot['links'])==1
                    assert next(row for row in snapshot['tasks'] if row['id']==item[0])['block_kind']=='delegated'
                committed.append(snapshot)
                return True
            caught=None
            with attachment_failure_adapter(target,name,RuntimeError('fixed postcommit '+label),predicate=predicate) as calls:
                try:consume_staged(orch,item)
                except RuntimeError as exc:caught=str(exc)
            assert calls and committed
            after=driver.snapshot(parent if label=='history' else item[0])
            assert after['tasks']==committed[0]['tasks'] and after['links']==committed[0]['links']
            assert after['results']==committed[0]['results'] and not corrections(after)
            # Physical disappearance AFTER commit cannot undo committed metadata.
            index={'fanout_spawned':0,'history':1,'queue':2}[label]
            path=OrgPaths(root=driver.root).task_attachments_dir/refs[index]['storage_key']
            path.unlink();assert driver.snapshot(parent if label=='history' else item[0])==after
            observe('C8.postcommit.'+label,trigger='fixed failure at actual '+name+' AFTER independent graph COMMIT readback',
                expected='committed graph/link/chain/fanout retained, no feedback/retry',
                observable='same row projections before/after fault and post-COMMIT unlink',
                defect='broad attachment catch or false postcommit rollback',propagated=caught)


def _fault_staged(orch,driver,staged,recipes):
    from tests.helpers.attachment_feedback_fixture import source_barrier
    for item,(kind,mode,subid) in zip(staged,recipes,strict=True):
        baseline=driver.snapshot(item[0])
        with source_barrier(orch,item,function='_attachment_feedback_and_reenqueue',anchor='try_attachment_retry_feedback') as boundary:
            with attachment_authorizer(orch._db,commit='COMMIT' in mode,audit='audit' in mode) as evidence:
                boundary['release'].set();await_condition(lambda:bool(boundary['errors']))
            statements,denials=evidence
        after=driver.snapshot(item[0])
        assert denials and after['tasks']==baseline['tasks'] and after['results']==baseline['results']
        assert not corrections(after) and not after['links']
        assert any('BEGIN IMMEDIATE' in row for row in statements) and any('ROLLBACK' in row for row in statements)
        assert any('INSERT INTO task_results' in row for row in statements)
        observe(subid,trigger='real '+kind+' feedback transaction '+mode+' preparation denial',
            expected='all A writes rolled back, original callback/decision retained',
            observable='actual prior result write/denial/ROLLBACK and independent unchanged projection',
            defect='non-atomic feedback or refunded committed chance',trace=statements,denials=denials)


def _real_race_observations(driver,observer):
    from tests.helpers.attachment_feedback_fixture import stage_case_batches,source_barrier
    from runtime.models import TaskRecord
    scenarios=('missing-before-prepare','competing-key-claim','allocated-child-collision','writer-lock-conflict','missing-after-prepare')
    refs=driver.upload('real-races',10)
    recipes=[(kind,scenario,refs[index]) for index,(kind,scenario) in enumerate((kind,scenario) for kind in ('D','F') for scenario in scenarios)]
    decisions=[]
    for kind,scenario,ref in recipes:
        decision=delegate([ref]) if kind=='D' else fanout([ref])
        if kind=='F':
            # The failing attachment is on the later child: earlier sibling insert
            # is in the SAME transaction and must roll back on later failure.
            plain=next(child for child in decision['children'] if not child.get('then'))
            carrier=next(child for child in decision['children'] if child.get('then'))
            carrier.pop('attachments',None);plain['attachments']=[ref]
            decision['children']=[carrier,plain]
        decisions.append(decision)
    facts={'D':[],'F':[]}
    for driver,staged,closure,offset in stage_case_batches(driver,observer,decisions):
        with attachment_supplement_driver(driver,closure) as orch:
            for item,(kind,scenario,ref) in zip(staged,recipes[offset:offset+len(staged)],strict=True):
                baseline=driver.snapshot(item[0]);path=OrgPaths(root=driver.root).task_attachments_dir/ref['storage_key']
                # Preparation return is after actual stat; missing-before uses call.
                if scenario=='missing-before-prepare':
                    function='_prepare_attachment_params';event='call'
                else:
                    function='_consume_completion_report_body' if kind=='D' else '_spawn_fanout_children';event='line'
                from contextlib import nullcontext
                if scenario=='missing-before-prepare':
                    # Confined failure-only owned file unlink BEFORE real prepare
                    # through the validation return boundary, not forged existence.
                    function='_validate_decision_attachments' if kind=='D' else '_prepare_attachment_params'
                    event='return' if kind=='D' else 'call'
                if event=='call':
                    barrier=preparation_call_barrier(orch,item)
                else:
                    barrier=source_barrier(orch,item,function=function,
                        anchor='try_delegate' if kind=='D' else 'try_delegate_many',event=event)
                rival=None
                with barrier as boundary:
                    if scenario in ('missing-before-prepare','missing-after-prepare'):
                        path.unlink()
                    elif scenario=='competing-key-claim':
                        rival=Database(OrgPaths(root=driver.root).db_path)
                        rival.insert_task_attachment(task_id=item[0],ordinal=0,storage_key=ref['storage_key'],
                            display_name=ref['display_name'],size_bytes=path.stat().st_size,content_type='text/plain',uploaded_by=item[1]['agent'])
                    elif scenario=='allocated-child-collision':
                        rival=Database(OrgPaths(root=driver.root).db_path)
                        allocated=boundary['locals'].get('child_id') if kind=='D' else boundary['locals']['children_ids'][-1]
                        rival.insert_task(TaskRecord(id=allocated,brief='actual allocated contender',team='engineering',
                            assigned_agent='dev_agent',parent_task_id=item[0],status=TaskStatus.PENDING,task_type='subtask'))
                    else:
                        rival=sqlite3.connect(OrgPaths(root=driver.root).db_path,timeout=.1)
                        rival.execute('BEGIN IMMEDIATE');orch._db._conn.execute('PRAGMA busy_timeout=50')
                    contender=driver.snapshot(item[0]);boundary['release'].set()
                    if scenario=='writer-lock-conflict':await_condition(lambda:bool(boundary['errors']))
                if rival is not None:
                    if scenario=='writer-lock-conflict':rival.rollback()
                    rival.close()
                after=driver.snapshot(item[0])
                if scenario=='missing-before-prepare':
                    assert len(corrections(after))==1 and not after['links'] and len(after['tasks'])==1
                elif scenario=='missing-after-prepare':
                    assert len(after['links'])==1 and len(after['tasks'])==(2 if kind=='D' else 3)
                    assert not corrections(after)
                else:
                    assert after['tasks']==contender['tasks'] and after['links']==contender['links']
                    assert not corrections(after) and boundary['errors']
                assert after['results']==baseline['results'] or scenario=='missing-before-prepare'
                facts[kind].append({'scenario':scenario,'claim_result':item[2]['id'],'errors':boundary['errors'],
                    'batch_prerequisites':getattr(driver,'batch_prerequisites',None),
                    'before_hash':__import__('hashlib').sha256(json.dumps(contender,sort_keys=True).encode()).hexdigest(),
                    'after_hash':__import__('hashlib').sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()})
    for kind in ('D','F'):
        observe('C8.real-races.'+kind,trigger='real independent key/ID/writer and owned phase unlink contenders',
            expected='loser rollback only, legitimate winner/committed graph retained',
            observable='source boundary handshakes, independent COMMIT/rows/errors for all five placements',
            defect='partial sibling insert, winner deletion or false file-transaction guarantee',placements=facts[kind])


def preparation_call_barrier(orch,item):
    # This observer variant uses a call event BEFORE any prepare instruction;
    # source_barrier accepts only a bound actual code object.
    from tests.helpers.attachment_feedback_fixture import source_barrier
    return source_barrier(orch,item,function='_prepare_attachment_params',anchor='',event='call')


def _stop_rollback_observations(driver,observer):
    from tests.helpers.attachment_feedback_fixture import stage_cases,source_barrier
    tasks=[driver.create(),_nested_manager(driver),driver.create()]
    staged=[]
    for task in tasks:
        for ordinal in (1,2):
            launch=driver.launch(task);driver.submit(launch,delegate([missing()]))
            driver.await_correction(task,ordinal)
        launch=driver.launch(task);result,body=driver.submit(launch,delegate([missing()]),exit_provider=False)
        staged.append((task,launch,result,body))
    last=staged[-1];observer.arm(last[0],last[2]['id'],['before-gate']);driver.release_provider(last[1])
    pause=observer.pause('before-gate');observer.acknowledge_interruption('before-gate')
    closure=observer.interrupt_and_close(pause,driver.port)
    with attachment_supplement_driver(driver,closure) as orch:
        for item,label in zip(staged,('stop-root','stop-nonroot','second-denominator'),strict=True):
            baseline=driver.snapshot(item[0]);statements=[]
            with source_barrier(orch,item,function='_attachment_feedback_and_reenqueue',anchor='try_attachment_retry_feedback') as boundary:
                if label=='second-denominator':
                    def predicate(*args,**kwargs):
                        action=kwargs.get('action',args[2] if len(args)>2 else None)
                        payload=kwargs.get('payload',args[3] if len(args)>3 else None)
                        if action!='authority_hook':return False
                        assert payload['outcome']=='not_applicable' and type(payload['causal_escalation_audit_id']) is int
                        row=orch._db._conn.execute('SELECT action FROM audit_log WHERE id=?',(payload['causal_escalation_audit_id'],)).fetchone()
                        assert row['action']=='escalation' and orch._db._conn.in_transaction
                        return True
                    orch._db._conn.set_trace_callback(lambda sql:statements.append(sql[:4096]))
                    with attachment_failure_adapter(orch._db,'insert_audit_log_uncommitted',RuntimeError('fixed second denominator'),predicate=predicate) as calls:
                        boundary['release'].set();await_condition(lambda:bool(boundary['errors']))
                    orch._db._conn.set_trace_callback(None);assert calls==[True]
                else:
                    with attachment_authorizer(orch._db,commit=True) as evidence:
                        boundary['release'].set();await_condition(lambda:bool(boundary['errors']))
                    statements,denials=evidence;assert denials
            after=driver.snapshot(item[0])
            assert after['tasks']==baseline['tasks'] and after['results']==baseline['results']
            assert len(corrections(after))==2 and after['links']==baseline['links']
            assert any('ROLLBACK' in sql for sql in statements) and not orch._db._conn.in_transaction
            observe('C8.stop-second-denominator' if label=='second-denominator' else 'C8.COMMIT-deny.'+label,
                trigger='count2 real original-claim stop transaction '+label+' fixed denial',
                expected='whole stop rolled back, two genuine prior opportunities preserved',
                observable='real UPDATE/first audit/denial/ROLLBACK plus independent unchanged task/results/links',
                defect='non-atomic stop or audit pair',trace=statements,errors=boundary['errors'])


def _normal_v2_controls(driver):
    """Normal public ACK/repeat/correction; separate from public interruption."""
    task=driver.create();first=driver.launch(task)
    original,_=driver.submit(first,{'action':'escalate','reason':'Synthetic authorized continuation'})
    second=driver.launch(task)
    r2,body=driver.submit(second,delegate([missing()]))
    driver.await_correction(task,1)
    before=await_condition(lambda:(s if any(row['spending_result_id']==r2['id'] and row['decision_state']=='applied' for row in s['envelopes']) else None)
        if (s:=driver.snapshot(task))['envelopes'] else None)
    repeat=driver.repeat_callback(body);assert repeat.status_code in (200,409)
    assert driver.snapshot(task)==before
    observe('C6.identity-controls.v2-duplicate',trigger='normal public SAME-SID identical callback after real ACK',
        expected='duplicate read-only',observable='exact rows preserved including applied receipt',defect='v2 callback reconsumption')
    changed={**body,'summary':'different same-SID material'}
    response=driver.repeat_callback(changed)
    assert response.status_code in (400,409) and driver.snapshot(task)==before
    observe('C6.identity-controls.changed-callback',trigger='public SAME-SID changed material callback',
        expected='existing conflict/refusal, zero body effects',observable='HTTP conflict and unchanged result/receipt projection',
        defect='callback material replacement',status=response.status_code)
    third=driver.launch(task);assert third['session_id'] not in (first['session_id'],second['session_id'])
    actual,_=driver.submit(third,delegate(driver.upload('v2-new-correction')))
    assert actual['id'] not in (original['id'],r2['id'])
    after=graph(driver,task,'D');assert len(corrections(after))==1
    observe('C6.identity-controls.new-correction',trigger='genuine normally scheduled later SID/result with fresh authorized upload',
        expected='authenticated terminal-lineage correction creates intended child once',
        observable='actual distinct sessions/INTEGER results, one child/link and one retained marker',
        defect='feedback substituted as callback or terminal receipt prevents genuine correction',
        causal_result=original['id'],spending_result=r2['id'],new_result=actual['id'],new_sid=third['session_id'])
    return task,second,r2,body
