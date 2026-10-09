"""Naming core assertions with explicit finite business selections.

Historical direct nodes retain their original domain bodies. The current
naming entry selects literal case arguments without iterating that inventory.
OrgState/SQL observations are core evidence, distinct from live-daemon/browser
proof in test_identity_names_e2e.py. This authoring leg executes neither.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from runtime.identities import registry as names, schema
from runtime.identities.schema import NamingError
from runtime.infrastructure.database import Database
from runtime.infrastructure import workflow_schema
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes import agents
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text, AgentParseError
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.runtime import RuntimeDir
from tests.integration.identity_names_owned_cases import (
    ASSETS, ConnectionFault, cases, owned_org, seed, release_seed, snapshot,
    naming_snapshot, record, rename, enroll, founder_create, terminate, owned_child,
    emit_case, digest,
)

pytestmark = pytest.mark.integration


def run(work):
    return asyncio.run(work)


def test_a1_install_migrate_reopen(tmp_path, monkeypatch):
    node = 'test_a1_install_migrate_reopen'
    for case in cases(node):
        target = tmp_path / case
        if case.startswith(('attach-', 'operator-')):
            match = re.fullmatch(r'(attach|operator)-(fresh|v0|v2|v2-organic)-(.*)-(absent|complete)', case)
            mode, history, route, complete = match.groups()
            layout = route if mode == 'attach' else route.split('-')[1]
            with owned_org(target, history=history, layout=layout, names=complete=='complete') as org:
                before = naming_snapshot(org)
                if mode == 'operator':
                    if layout in ('F','E'):
                        from tests.workflows.test_submission_schema import _legacy_graph
                        _legacy_graph(org.db,layout,joined=True)
                    retained=snapshot(org)['rows']
                    migration, source, outcome = route.split('-')
                    if complete == 'absent':
                        with org.db.workflow_schema_transaction() as conn:
                            conn.execute('DROP TABLE identity_name_claims')
                            conn.execute('DROP TABLE identity_name_owners')
                            conn.execute('DROP TABLE identity_name_schema')
                        before = naming_snapshot(org)
                    org.close()
                    # Invoke the shipping operator script, not a model migration.
                    import subprocess, sys
                    script = 'draft' if migration == 'draft' else 'submission'
                    command = [sys.executable, f'scripts/migrate_workflow_{script}_schema.py',
                               '--runtime-root',str(org.root.parent.parent),'--org','alpha']
                    result = subprocess.run(command, capture_output=True, timeout=15)
                    assert result.returncode == 0, result.stdout+result.stderr
                    org.db = Database(OrgPaths(org.root).db_path)
                    assert naming_snapshot(org) == before
                    after=snapshot(org)['rows']
                    for table,rows in retained.items():
                        if complete=='absent' and table.startswith('identity_name_'):continue
                        observed=after[table]
                        if table=='workflow_events' and source!='G' and outcome=='G':observed=tuple(row[:3]+row[4:] for row in observed)
                        assert observed==rows,table
                    with org.db.coherent_read_view() as conn:
                        assert workflow_schema.validate_workflow_schema(conn,expected_org_slug='alpha') == (source if outcome=='ready' else outcome)
                        workflow_schema._validate_release_database(conn, source if outcome=='ready' else outcome)
                else:
                    from runtime.orchestrator.authority import _release_schema_digest, _live_schema_digest
                    assert _live_schema_digest(org.db) == _release_schema_digest(layout,history,1)
                    if complete == 'complete':
                        assert names.classify(org,'Sam').revision == 7
                org.close()
                org.db = Database(OrgPaths(org.root).db_path)
                reopened = OrgState.load(slug='alpha',root=org.root,settings=org.settings)
                try:
                    assert reopened.naming_readiness == 'ready'
                    # Absent operator source gets its first org-owned install only on reopen.
                    if not (mode=='operator' and complete=='absent'):
                        assert naming_snapshot(reopened) == before
                    assert names.refresh_names(reopened)
                    settled = naming_snapshot(reopened)
                    assert names.refresh_names(reopened) and naming_snapshot(reopened)==settled
                    indexes={r[0] for r in reopened.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
                    assert {'idx_cleanup_tasks_agent_created_id','idx_cleanup_trigger_task_agent','idx_cleanup_results_task_agent_id'} <= indexes
                    record(node,case,reopened,tmp_path,logical_preservation=True,physical_page_equality_claim=False)
                finally: reopened.close()
        elif case in ('generic-excluded','runtime-audit-excluded'):
            target.mkdir()
            db=Database(target / ('runtime-audit.db' if case.startswith('runtime') else 'generic.db'))
            try:
                assert not any(r[0].startswith('identity_name_') for r in db.execute('SELECT name FROM sqlite_master'))
                db.close()
                emit_case(node,case,{'naming_tables':0,'receipt_root':str(tmp_path/'receipts')})
            finally: db.close()
        elif case == 'flat-v1-no-write-refusal':
            target.mkdir(); marker=target/'happyranch.yaml'
            marker.write_text('schema_version: 1\n')
            before=marker.read_bytes()
            with pytest.raises(ValueError): RuntimeDir.load(target)
            assert marker.read_bytes()==before and list(target.iterdir())==[marker]
            emit_case(node,case,{'marker_sha256':hashlib.sha256(before).hexdigest(),'receipt_root':str(tmp_path/'receipts')})
        elif case == 'fresh-post-org':
            from runtime.daemon.state import DaemonState
            from runtime.daemon.routes.orgs import init_org, InitOrgBody
            from runtime.config import Settings
            runtime=RuntimeDir.init(target/'runtime')
            state=DaemonState.from_runtime(runtime,Settings())
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon=state)))
            result=run(init_org(InitOrgBody(slug='alpha'),request))
            org=state.orgs['alpha']
            try:
                assert result['slug']=='alpha' and org.naming_readiness=='ready'
                assert workflow_schema.validate_workflow_schema(org.db._conn,expected_org_slug='alpha')=='G'
                record(node,case,org,tmp_path)
            finally: org.close()
        else:
            assert case in ('install-C01','install-C02')
            with owned_org(target) as org:
                with org.db.workflow_schema_transaction() as conn:
                    for table in ('identity_name_claims','identity_name_owners','identity_name_schema'):conn.execute(f'DROP TABLE {table}')
                original=org.db._conn
                fault=ConnectionFault(original,'COMMIT',after=case=='install-C02')
                org.db._conn=fault
                assert not names.refresh_names(org,install=True)
                org.db._conn=original
                present=schema.naming_version(original,org_slug='alpha')
                assert present==int(case=='install-C02')
                assert names.refresh_names(org,install=True)
                assert names.classify(org,'maker').revision==1
                record(node,case,org,tmp_path,committed_before_loss=bool(present))


def test_a3_label_boundaries(tmp_path):
    node='test_a3_label_boundaries'
    invalid={'empty':'','65':'x'*65,'editable-_label-no-residue':'_label','leading-hyphen':'-a',
             'space':'A B','dot':'A.B','brackets':'[A]','unicode':'名字','trailing-newline':'Alex\n',
             'leading-space-no-trim':' Alex','trailing-space-no-trim':'Alex '}
    for case in cases(node):
        with owned_org(tmp_path/case,worker='_worker' if case.startswith('underscore-') else 'maker') as org:
            if case in invalid or case=='underscore-own-id-edit-refused':
                before=snapshot(org)
                with pytest.raises(NamingError,match='invalid_identity_label'):
                    run(rename(org,invalid.get(case,'_worker'),name='_worker' if case.startswith('underscore') else 'maker'))
                assert snapshot(org)==before
            elif case in ('valid1','valid64'):
                label='A' if case=='valid1' else 'A'+'x'*63
                result=run(rename(org,label));assert result.current_label==label and result.revision==2
            elif case=='over64-cold-admission-refused':
                definition=prompt_loader.load_agent(OrgPaths(org.root),'maker')
                before=snapshot(org)
                with pytest.raises(AgentParseError):parse_agent_text(render_agent_text(replace(definition,name='x'*65)),expected_name='x'*65)
                assert snapshot(org)==before
            elif case=='underscore-id-default-rename-reopen':
                assert names.classify(org,'_worker').current_label=='_worker'
                run(rename(org,'Alex',name='_worker'))
                expected=naming_snapshot(org)
                org.close(); reopened=OrgState.load(slug=org.slug,root=org.root,settings=org.settings)
                try:
                    assert naming_snapshot(reopened)==expected
                    assert names.classify(reopened,'_worker').classification=='id'
                    assert names.classify(reopened,'Alex').canonical_id=='_worker'
                finally:reopened.close()
                org.db=Database(OrgPaths(org.root).db_path)
            else:
                assert case in ('foreign-owner-id-exception-refused','arbitrary-underscore-claim-refused')
                if case.startswith('foreign'):
                    paths=OrgPaths(org.root)
                    definition=prompt_loader.load_agent(paths,'maker')
                    (paths.agents_dir/'_worker.md').write_text(render_agent_text(replace(definition,name='_worker')))
                    org.teams.add_worker('engineering','_worker')
                    assert names.refresh_names(org)
                with org.db.coherent_read_view() as conn: owners,claims=schema.read_rows(conn)
                bad_token='_worker' if case.startswith('foreign') else '_label'
                corrupt=tuple(c for c in claims if c[0]!=bad_token)+((bad_token,'agent','maker',0,1),)
                before=snapshot(org)
                with pytest.raises(NamingError):names.validate_rows(owners,corrupt)
                assert snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a3_typed_owners(tmp_path):
    node='test_a3_typed_owners'
    for case in cases(node):
        life=case if case in ('pending','terminated') else 'active'
        with owned_org(tmp_path/case,lifecycle=life) as org:
            if case=='absent-denied':
                run(agents.reject_agent(org.slug,'maker',org)) if life=='pending' else None
                path=OrgPaths(org.root).agents_dir/'maker.md';path.unlink();org.teams.remove_worker('engineering','maker')
                assert names.refresh_names(org)
                before=snapshot(org)
                with pytest.raises(NamingError,match='identity_owner_absent'):run(rename(org,'Alex'))
                assert snapshot(org)==before
            elif case in ('active','pending','terminated'):
                result=run(rename(org,'Alex'));assert result.lifecycle==life
                assert names.classify(org,'Alex').kind=='agent'
            elif case.startswith('founder-'):
                result=run(rename(org,'Alex',name='founder',kind='founder'))
                assert result.kind=='founder' and names.classify(org,'founder').classification=='id'
                if case=='founder-former':
                    run(rename(org,'Sam',name='founder',kind='founder'))
                    assert names.classify(org,'Alex').classification=='former'
                elif case=='founder-reclaim':
                    run(rename(org,'Sam',name='founder',kind='founder'));run(rename(org,'Alex',name='founder',kind='founder'))
                    assert names.classify(org,'Alex').kind=='founder'
            elif case=='no-op':
                before=snapshot(org);run(rename(org,'maker'));assert snapshot(org)==before
            elif case in ('case-only','own-id','own-former'):
                run(rename(org,'Alex'))
                label={'case-only':'aLEX','own-id':'maker','own-former':'Alex'}[case]
                if case=='own-former':run(rename(org,'Sam'))
                result=run(rename(org,label));assert result.current_label==label
            else:
                if case in ('foreign-current','foreign-former'):
                    run(rename(org,'Sam',name='manager'))
                    if case=='foreign-former':run(rename(org,'Alex',name='manager'))
                    label='sAM'
                else:label='manager' if case=='foreign-id' else 'founder'
                before=snapshot(org)
                with pytest.raises(NamingError,match='identity_name_unavailable'):run(rename(org,label))
                assert snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a3_rename_cas(tmp_path, case_ids=None):
    node='test_a3_rename_cas'
    for case in cases(node, case_ids):
        with owned_org(tmp_path/case) as org:
            async def contend():
                async with org.workflow_authority.async_writer_interval(publisher='identity_name_changed'):
                    first=asyncio.create_task(rename(org,'Sam',revision=1))
                    second=asyncio.create_task(rename(org,'Sam' if case=='same-owner-same-label' else 'sAM' if case=='two-owners-sAM' else 'Alex' if case=='same-owner-different-label' else 'Sam',name='manager' if case.startswith('two-owners') else 'maker',revision=1))
                    queued=asyncio.Event()
                    asyncio.get_running_loop().call_soon(queued.set)
                    await queued.wait()
                    assert len([w for w in org.workflow_authority._async_writer_lock._waiters if not w.done()])==2
                return await asyncio.gather(first,second,return_exceptions=True)
            results=run(contend())
            assert sum(isinstance(r,names.Subject) for r in results)==1
            loser=next(r for r in results if isinstance(r,NamingError))
            assert loser.code==('identity_name_unavailable' if case.startswith('two-owners') else 'stale_identity_revision')
            assert org.db.execute("SELECT count(*) FROM audit_log WHERE action='identity_name_changed'").fetchone()[0]==1
            record(node,case,org,tmp_path,loser=loser.code)


def test_a4_transaction_faults(tmp_path):
    node='test_a4_transaction_faults'
    needles={'C03a':'INSERT INTO identity_name_claims','C03b':'UPDATE identity_name_claims SET permanent=1',
             'C03c':'UPDATE identity_name_owners SET current_label','C03d':'INSERT INTO audit_log','C04-lost-internal-response':'COMMIT'}
    for case in cases(node):
        with owned_org(tmp_path/case) as org:
            before=snapshot(org);original=org.db._conn
            fault=ConnectionFault(original,needles[case],arm_on='UPDATE identity_name_owners SET current_label' if case.startswith('C04') else None)
            org.db._conn=fault
            with pytest.raises(RuntimeError):run(rename(org,'Alex',revision=1))
            org.db._conn=original
            assert fault.fired
            if case.startswith('C03'):
                assert snapshot(org)==before
            else:
                assert names.classify(org,'Alex').revision==2
                settled=snapshot(org)
                with pytest.raises(NamingError,match='stale_identity_revision'):run(rename(org,'Alex',revision=1))
                assert snapshot(org)==settled
            record(node,case,org,tmp_path)


def test_a5_enroll_rename_race(tmp_path,monkeypatch, case_ids=None):
    node='test_a5_enroll_rename_race'
    # Fault only host bootstrap, after real file/team/selector creation. The
    # route's documented retained-active error is observable, not success.
    from runtime.orchestrator.context_builder import ContextBuilder
    def bootstrap_fail(*args,**kwargs):raise RuntimeError('retained bootstrap fault')
    monkeypatch.setattr(ContextBuilder,'ensure_workspace_ready',bootstrap_fail)
    for case in cases(node, case_ids):
        with owned_org(tmp_path/case) as org:
            creator=(enroll(org,'sam') if case.startswith('manager-enroll') else founder_create(org,'sam','manager' if case.startswith('founder-manager') else 'worker'))
            async def race():
                async with org.workflow_authority.async_writer_interval(publisher='identity_name_changed'):
                    operations=[rename(org,'Sam',revision=1),creator]
                    if case.endswith('create-first'):operations.reverse()
                    tasks=[asyncio.create_task(c) for c in operations]
                    queued=asyncio.Event()
                    asyncio.get_running_loop().call_soon(queued.set)
                    await queued.wait()
                    assert len([w for w in org.workflow_authority._async_writer_lock._waiters if not w.done()])==2
                return await asyncio.gather(*tasks,return_exceptions=True)
            outcomes=run(race())
            if case.endswith('rename-first'):
                assert names.classify(org,'Sam').canonical_id=='maker'
                assert not (OrgPaths(org.root).agents_dir/'sam.md').exists()
                assert not (OrgPaths(org.root).pending_agents_dir/'sam.md').exists()
                assert org.teams.team_for_agent('sam') is None
                assert not (org.root/'workspaces/sam').exists()
                assert org.db.execute("SELECT count(*) FROM audit_log WHERE action='identity_name_changed'").fetchone()[0]==1
                assert any(isinstance(r,HTTPException) and r.status_code==409 for r in outcomes)
            else:
                assert names.classify(org,'sam').canonical_id=='sam'
                assert names.classify(org,'maker').current_label=='maker'
                assert any(isinstance(r,NamingError) and r.code=='identity_name_unavailable' for r in outcomes)
            record(node,case,org,tmp_path,outcomes=[type(r).__name__ for r in outcomes])


def test_a5_collision_isolation(tmp_path):
    node='test_a5_collision_isolation'
    for case in cases(node):
        with owned_org(tmp_path/case) as org:
            if case=='beta-isolation':
                with owned_org(tmp_path/case/'beta',slug='beta') as beta:
                    run(rename(org,'Sam'));run(enroll(beta,'sam'))
                    assert names.classify(org,'Sam').canonical_id=='maker'
                    assert names.classify(beta,'sam').canonical_id=='sam'
            else:
                token='founder'
                if case.startswith('foreign'):
                    run(rename(org,'Sam'))
                    if case=='foreign-former':run(rename(org,'Alex'))
                    token='sam'
                before=snapshot(org)
                with pytest.raises(HTTPException) as refusal:run(enroll(org,token))
                assert refusal.value.status_code==409 and snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a6_pending_reuse(tmp_path,monkeypatch, case_ids=None):
    node='test_a6_pending_reuse'
    for case in cases(node, case_ids):
        with owned_org(tmp_path/case,lifecycle='pending') as org:
            if case in ('chosen-reject-dormant','same-id-chosen-spelling','stale-ABA'):
                run(rename(org,'Alex'));run(rename(org,'Sam'))
            if case=='approve-retained-bootstrap':
                from runtime.orchestrator.context_builder import ContextBuilder
                def fail(*a,**k):raise RuntimeError('bootstrap failed')
                with monkeypatch.context() as local:
                    local.setattr(ContextBuilder,'ensure_workspace_ready',fail)
                    with pytest.raises(RuntimeError):run(agents.approve_agent(org.slug,'maker',org))
                assert names.classify(org,'maker').lifecycle=='active'
            elif case=='C08-injected-residue':
                prompt_loader.reject_agent(OrgPaths(org.root),'maker');org.teams.remove_worker('engineering','maker')
                assert names.refresh_names(org)
                assert names.classify(org,'maker') is None
            else:
                old=names.classify(org,'maker').revision
                run(agents.reject_agent(org.slug,'maker',org))
                claims={r[0] for r in org.db.execute('SELECT * FROM identity_name_claims')}
                if case in ('chosen-reject-dormant','same-id-chosen-spelling','stale-ABA'):
                    assert {'maker','alex','sam'} <= claims
                    assert names.classify(org,'Sam').classification=='former'
                else:assert 'maker' not in claims
                if case.startswith('same-id') or case=='stale-ABA':
                    run(enroll(org,'maker'))
                    current=names.classify(org,'maker')
                    assert current.current_label==('maker' if case=='same-id-default' else 'Sam')
                    assert current.revision>old
                    before=snapshot(org)
                    with pytest.raises(NamingError,match='stale_identity_revision'):run(rename(org,'Next',revision=old))
                    assert snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a6_approve_reject_race(tmp_path,monkeypatch):
    node='test_a6_approve_reject_race'
    from runtime.orchestrator.context_builder import ContextBuilder
    def fail(*a,**k):raise RuntimeError('retained bootstrap fault')
    monkeypatch.setattr(ContextBuilder,'ensure_workspace_ready',fail)
    for case in cases(node):
        with owned_org(tmp_path/case,lifecycle='pending') as org:
            async def race():
                await org.workflow_authority._async_writer_lock.acquire()
                operations=[agents.approve_agent(org.slug,'maker',org),agents.reject_agent(org.slug,'maker',org)]
                if case=='reject-first':operations.reverse()
                pending=[asyncio.create_task(c) for c in operations]
                queued=asyncio.Event()
                asyncio.get_running_loop().call_soon(queued.set)
                await queued.wait()
                assert len([w for w in org.workflow_authority._async_writer_lock._waiters if not w.done()])==2
                org.workflow_authority._async_writer_lock.release()
                return await asyncio.gather(*pending,return_exceptions=True)
            outcomes=run(race())
            if case=='approve-first':assert names.classify(org,'maker').lifecycle=='active'
            else:assert names.classify(org,'maker') is None
            assert any(isinstance(outcome,HTTPException) for outcome in outcomes)
            record(node,case,org,tmp_path,outcomes=[type(r).__name__ for r in outcomes])


def test_a2_admission_readiness(tmp_path,monkeypatch):
    node='test_a2_admission_readiness'
    for case in cases(node):
        lifecycle='pending' if case in ('same-id-reject','same-id-approve') else 'active'
        with owned_org(tmp_path/case,lifecycle=lifecycle) as org:
            before_names=naming_snapshot(org)
            conn=org.db._conn
            if case in ('partial','newer','foreign-org','extra-naming-index','extra-trigger','extra-column','extra-unrelated-object','fk-corrupt','row-corrupt'):
                conn.execute('PRAGMA foreign_keys=OFF');conn.execute('PRAGMA ignore_check_constraints=ON')
                statements={'partial':'DROP TABLE identity_name_claims','newer':'UPDATE identity_name_schema SET version=2',
                            'foreign-org':"UPDATE identity_name_schema SET org_slug='beta'",
                            'extra-naming-index':'CREATE INDEX identity_name_extra ON identity_name_owners(revision)',
                            'extra-trigger':'CREATE TRIGGER identity_name_extra AFTER INSERT ON identity_name_owners BEGIN SELECT 1; END',
                            'extra-column':'ALTER TABLE identity_name_owners ADD COLUMN extra TEXT',
                            'extra-unrelated-object':'CREATE TABLE unexpected_object(value TEXT)',
                            'fk-corrupt':"UPDATE identity_name_claims SET owner_id='unknown' WHERE normalized_name='maker'",
                            'row-corrupt':"UPDATE identity_name_owners SET revision=0 WHERE canonical_id='maker'"}
                conn.execute(statements[case]);conn.commit()
                conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA ignore_check_constraints=OFF')
                damaged=snapshot(org)
                if case=='extra-unrelated-object':
                    with pytest.raises(ValueError,match='whole_database_mismatch'):workflow_schema._validate_release_database(conn,'F')
                else:
                    assert not names.refresh_names(org)
                    assert org.naming_readiness=='unavailable'
                    expected={'partial':'naming_schema_layout_mismatch','newer':'naming_schema_marker_mismatch',
                              'foreign-org':'naming_schema_marker_mismatch','extra-naming-index':'naming_schema_layout_mismatch',
                              'extra-trigger':'naming_schema_layout_mismatch','extra-column':'naming_schema_layout_mismatch',
                              'fk-corrupt':'naming_claim_corrupt','row-corrupt':'naming_owner_corrupt'}[case]
                    assert org.naming_diagnostic==expected
                assert snapshot(org)==damaged
            elif case.startswith('same-id') or case in ('id-read-callback','new-enroll-refused','new-create-refused'):
                # Naming-only marker ownership error, without corrupting the
                # canonical inputs consumed by original same-ID authority guards.
                conn.execute("UPDATE identity_name_schema SET org_slug='beta'");conn.commit()
                before_names=naming_snapshot(org)
                assert not names.refresh_names(org)
                before=snapshot(org)
                if case.startswith('new'):
                    operation=enroll(org,'new_id') if case=='new-enroll-refused' else founder_create(org,'new_id')
                    with pytest.raises(HTTPException) as error:run(operation)
                    assert error.value.detail['code']=='naming_unavailable'
                    assert snapshot(org)==before
                elif case=='same-id-reject':
                    run(agents.reject_agent(org.slug,'maker',org));assert prompt_loader.load_pending_agent(OrgPaths(org.root),'maker') is None
                elif case=='same-id-approve':
                    from runtime.orchestrator.context_builder import ContextBuilder
                    def fail(*a,**k):raise RuntimeError('bootstrap retained')
                    with monkeypatch.context() as local:
                        local.setattr(ContextBuilder,'ensure_workspace_ready',fail)
                        with pytest.raises(RuntimeError):run(agents.approve_agent(org.slug,'maker',org))
                    assert prompt_loader.load_agent(OrgPaths(org.root),'maker') is not None
                elif case=='same-id-terminate':
                    run(terminate(org));assert prompt_loader.is_terminated(OrgPaths(org.root),'maker')
                elif case=='same-id-update':
                    _,rev=prompt_loader.load_agent_with_revision(OrgPaths(org.root),'maker')
                    operation=agents.manage_agent(org.slug,agents.ManageAgentBody(action='update',name='maker',task_id='TASK-OWNER',session_id='sess-owner',description='changed',expected_revision=rev),org)
                    run(operation);assert prompt_loader.load_agent(OrgPaths(org.root),'maker').description=='changed'
                else:
                    assert prompt_loader.load_agent(OrgPaths(org.root),'maker').name=='maker'
                    from runtime.models import TaskRecord
                    org.db.insert_task(TaskRecord(id='TASK-ID',assigned_agent='maker',brief='existing ID callback',team='engineering'))
                    org.sessions.set_active('TASK-ID','maker','sess-id')
                    # Existing real callback primitive, attributable exact owner.
                    assert org.sessions.get_active('TASK-ID','maker')=='sess-id'
                    from runtime.daemon.routes.tasks import submit_completion,CompletionBody
                    org.db.update_task('TASK-ID',status='in_progress',current_session_id='sess-id')
                    run(submit_completion('TASK-ID',CompletionBody(session_id='sess-id',agent='maker',status='completed',confidence=90,output_summary='unchanged ID'),org))
                    assert org.db.get_latest_task_result('TASK-ID','maker','sess-id')['agent']=='maker'
                assert naming_snapshot(org)==before_names
            else:
                paths=OrgPaths(org.root)
                if case=='duplicate-lifecycle':
                    paths.pending_agents_dir.mkdir();(paths.pending_agents_dir/'maker.md').write_bytes((paths.agents_dir/'maker.md').read_bytes())
                elif case=='folded-founder':
                    agent=prompt_loader.load_agent(paths,'maker');(paths.agents_dir/'founder.md').write_text(render_agent_text(replace(agent,name='founder')))
                elif case=='filename-mismatch':(paths.agents_dir/'wrong.md').write_bytes((paths.agents_dir/'maker.md').read_bytes())
                elif case=='malformed':(paths.agents_dir/'broken.md').write_bytes(b'---\nwrong: [\n---')
                elif case=='changing-capture':
                    original=names._scan;calls=0
                    def moving(*args):
                        nonlocal calls
                        result=original(*args);calls+=1
                        if calls==1:(paths.agents_dir/'maker.md').write_bytes((paths.agents_dir/'maker.md').read_bytes()+b'\n')
                        return result
                    with monkeypatch.context() as local:
                        local.setattr(names,'_scan',moving)
                        assert not names.refresh_names(org)
                    assert naming_snapshot(org)==before_names
                    record(node,case,org,tmp_path);continue
                elif case=='bound-definitions':
                    agent=prompt_loader.load_agent(paths,'maker')
                    for i in range(4097):(paths.agents_dir/f'worker{i}.md').write_text(render_agent_text(replace(agent,name=f'worker{i}')))
                elif case=='bound-single-file':(paths.agents_dir/'maker.md').write_bytes(b'x'*(1024**2+1))
                elif case=='bound-file-total':
                    agent=prompt_loader.load_agent(paths,'maker')
                    for i in range(33):
                        raw=render_agent_text(replace(agent,name=f'worker{i}')).encode()
                        (paths.agents_dir/f'worker{i}.md').write_bytes(raw+b'x'*(1024**2-len(raw)))
                elif case=='bound-claims':
                    conn.executemany('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)',((f'label{i}','agent','maker',0,1) for i in range(65537)));conn.commit()
                elif case=='bound-row-total':
                    conn.execute('PRAGMA ignore_check_constraints=ON')
                    conn.execute('UPDATE identity_name_owners SET current_label=? WHERE canonical_id=?',('x'*(32*1024**2+1),'maker'));conn.commit()
                    conn.execute('PRAGMA ignore_check_constraints=OFF')
                elif case=='bound-scan-deadline':
                    clock=iter([0.0,3.0])
                    with monkeypatch.context() as local:
                        local.setattr(names.time,'monotonic',lambda:next(clock,3.0))
                        assert not names.refresh_names(org)
                    assert org.naming_diagnostic=='naming_scan_deadline'
                    record(node,case,org,tmp_path);continue
                else:raise AssertionError(case)
                damaged=snapshot(org)
                assert not names.refresh_names(org)
                expected={'duplicate-lifecycle':'naming_duplicate_or_founder_id','folded-founder':'naming_duplicate_or_founder_id',
                          'filename-mismatch':'naming_canonical_unavailable','malformed':'naming_canonical_unavailable',
                          'bound-definitions':'naming_definition_bound','bound-single-file':'naming_file_bound_or_type',
                          'bound-file-total':'naming_file_total_bound','bound-claims':'naming_row_bound','bound-row-total':'naming_row_bound'}[case]
                assert org.naming_diagnostic==expected
                assert snapshot(org)==damaged
            record(node,case,org,tmp_path)


def test_a7_terminate_bootstrap(tmp_path,monkeypatch):
    node='test_a7_terminate_bootstrap'
    for case in cases(node):
        life='terminated' if case=='terminated-rename' else 'active'
        with owned_org(tmp_path/case,lifecycle=life) as org:
            if case=='terminated-rename':
                assert run(rename(org,'Alex')).lifecycle=='terminated'
            elif case=='absent-rename':
                (OrgPaths(org.root).agents_dir/'maker.md').unlink();org.teams.remove_worker('engineering','maker');names.refresh_names(org)
                before=snapshot(org)
                with pytest.raises(NamingError,match='identity_owner_absent'):run(rename(org,'Alex'))
                assert snapshot(org)==before
            elif case in ('create-bootstrap-retained','approve-bootstrap-retained'):
                from runtime.orchestrator.context_builder import ContextBuilder
                def fail(*a,**k):raise RuntimeError('bootstrap retained')
                with monkeypatch.context() as local:
                    local.setattr(ContextBuilder,'ensure_workspace_ready',fail)
                    if case.startswith('create'):
                        with pytest.raises(RuntimeError):run(founder_create(org,'new_id'))
                        assert names.classify(org,'new_id').lifecycle=='active'
                    else:
                        run(enroll(org,'pending_id'))
                        with pytest.raises(RuntimeError):run(agents.approve_agent(org.slug,'pending_id',org))
                        assert names.classify(org,'pending_id').lifecycle=='active'
            elif case in ('workspace-failure','cleanup-failure'):
                before_names=naming_snapshot(org)
                def fail(*a,**k):raise OSError('owned failure')
                with monkeypatch.context() as local:
                    if case=='workspace-failure':local.setattr(agents,'_move_dir_atomically',fail)
                    else:local.setattr(org.db,'terminate_agent_cleanups',fail)
                    with pytest.raises(HTTPException) as error:run(terminate(org))
                assert error.value.status_code==500
                assert prompt_loader.load_agent(OrgPaths(org.root),'maker') is not None
                assert org.teams.team_for_agent('maker')=='engineering'
                assert naming_snapshot(org)==before_names
            else:
                from runtime.models import TaskRecord,JobRecord
                if case=='manager-refusal':subject='manager'
                else:subject='maker'
                if case=='live-task':org.db.insert_task(TaskRecord(id='TASK-LIVE',assigned_agent='maker',brief='live',team='engineering'))
                elif case=='job':org.db.insert_job(JobRecord(id='JOB-LIVE',agent_name='maker',task_id='TASK-OWNER',title='live',rationale='owned fixture',script_text='true',interpreter='bash',status='pending',created_at='2026-10-09T00:00:00+00:00'))
                elif case=='session':
                    # Actual started thread invocation is the supported session
                    # quiescence contract (ordinary active session alone is not).
                    from runtime.models import ThreadInvocationPurpose,ThreadRecord
                    org.db.insert_thread(ThreadRecord(id='THR-LIVE',subject='owned live invocation'))
                    token=org.db.mint_thread_invocation(thread_id='THR-LIVE',agent_name='maker',triggering_seq=1,purpose=ThreadInvocationPurpose.REPLY)
                    org.db.stamp_invocation_started(token.invocation_token,session_id='sess-live')
                elif case=='schedule':
                    from tests.integration.identity_names_owned_cases import seed_cleanup
                    seed_cleanup(org)
                    org.db.schedules.update('SCHEDULE-001', status='firing')
                elif case=='archive-collision':
                    folder=OrgPaths(org.root).agents_dir/'_terminated';folder.mkdir();(folder/'maker.md').write_bytes((OrgPaths(org.root).agents_dir/'maker.md').read_bytes())
                before=snapshot(org)
                with pytest.raises(HTTPException) as error:run(terminate(org,subject))
                assert error.value.status_code==409
                assert snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a8_crash_cuts(tmp_path, case_ids=None):
    from tests.integration.identity_names_owned_cases import crash_case
    node='test_a8_crash_cuts'
    for case in cases(node, case_ids):
        parent=tmp_path/case
        lifecycle='pending' if case in ('C08','C12','C13','C14','C15a','C15b') else 'active'
        with owned_org(parent,lifecycle=lifecycle) as org:
            root=org.root;before=naming_snapshot(org);org.close()
            with owned_child(crash_case,(str(root),case),parent/'ownership.json') as (process,pipe):
                assert pipe.poll(25), f'actual cut not reached: {case}; exit={process.exitcode}'
                observation=pipe.recv()
                assert observation['cut']==case and observation['pid']==process.pid
                process.kill();process.join(5)
                assert process.exitcode == -9 and not process.is_alive()
            # No old process/control survives into genuinely fresh construction.
            assert json.loads((parent/'ownership.json').read_text())['reaped'] is True
            org.db=Database(OrgPaths(root).db_path)
            durable_before=naming_snapshot(org)
            if case.startswith('C03') or case=='C01':
                assert durable_before==({} if case=='C01' else before)
            if case=='C04':
                assert names.classify(org,'Alex').revision==2
            baseline_error=None;reopened=None
            try:reopened=OrgState.load(slug='alpha',root=root,settings=org.settings)
            except (ValueError,OrgConsistencyError) as error:baseline_error=type(error).__name__
            expected_unavailable={'C06','C09','C16','C17a','C17b','C19c','C19d','C19f',
                                  'T1-remove-after-save','T2-remove-after-save','T2-rollback-before-save'}
            if case.startswith('T1-') and 'rollback' in case:
                assert baseline_error=='OrgConsistencyError'
            else:
                assert reopened is not None, (case,baseline_error)
                try:
                    # Cold disk truth differs from in-process memory at C19b.
                    expected='unavailable' if case in expected_unavailable or (case.startswith(('T1-add-before','T2-add-before'))) else 'ready'
                    assert reopened.naming_readiness==expected,(case,reopened.naming_diagnostic)
                    if case in ('C17c','C17d','C18a','C18b','C19a'):
                        assert names.classify(reopened,'maker').lifecycle=='terminated'
                        status=reopened.db.schedules.get('SCHEDULE-001').status.value
                        assert status==('cancelled' if case in ('C18a','C18b') else 'armed')
                        cancellations=reopened.db.execute("SELECT count(*) FROM audit_log WHERE action='schedule_cancelled'").fetchone()[0]
                        assert cancellations==int(case in ('C18a','C18b'))
                    if case=='C19e':
                        assert names.classify(reopened,'maker').lifecycle=='active'
                        assert not (root/'workspaces/maker').exists()
                        assert (root/'workspaces/_terminated/maker').is_dir()
                        assert reopened.db.schedules.get('SCHEDULE-001').status.value=='armed'
                    stable=naming_snapshot(reopened)
                    if expected=='ready':
                        assert names.refresh_names(reopened) and naming_snapshot(reopened)==stable
                    else:
                        refusal_before = snapshot(reopened)
                        with pytest.raises(NamingError, match='naming_unavailable'):
                            run(rename(reopened, 'RefusedCutName', revision=1))
                        assert snapshot(reopened) == refusal_before
                    reopened.close()
                    emit_case(node,case,{'durable_naming_sha256':digest(stable),'readiness':reopened.naming_readiness,
                                        'child_exit':-9,'reaped':True,'original_cut_snapshot_sha256':digest(observation['snapshot']),
                                        'receipt_root':str(tmp_path/'receipts')})
                finally:reopened.close()
            if baseline_error:
                emit_case(node,case,{'baseline_error':baseline_error,'child_exit':-9,'reaped':True,'receipt_root':str(tmp_path/'receipts')})


def test_a9_attach_restart_compatibility(tmp_path,monkeypatch):
    node='test_a9_attach_restart_compatibility'
    for case in cases(node):
        with owned_org(tmp_path/case) as org:
            run(rename(org,'Sam'));before=naming_snapshot(org)
            if case in ('cold','sequential-restart'):
                org.close()
                reopened=OrgState.load(slug='alpha',root=org.root,settings=org.settings)
                try:assert naming_snapshot(reopened)==before and reopened.naming_readiness=='ready'
                finally:reopened.close()
                org.db=Database(OrgPaths(org.root).db_path)
            elif case=='dynamic':
                from runtime.daemon.state import DaemonState
                runtime=RuntimeDir.load(org.root.parent.parent)
                org.close();state=DaemonState.from_runtime(runtime,org.settings)
                loaded=state.orgs.pop('alpha');loaded.close()
                attached=run(state.add_org('alpha'))
                try:assert naming_snapshot(attached)==before and attached.naming_readiness=='ready'
                finally:attached.close()
                org.db=Database(OrgPaths(org.root).db_path)
            else:
                from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter
                workspace=org.root/'workspaces/maker'
                (workspace/'agent.yaml').write_text('executor: claude\nrepos: {}\nmodel: chosen-model\n')
                org.naming_readiness='unavailable'
                if case=='yaml-owned-compensation':
                    original_replace=os.replace
                    def fail_owned(source,dest,*a,**k):
                        if str(dest).endswith('/agents/maker.md'):raise OSError('owned replace fault')
                        return original_replace(source,dest,*a,**k)
                    with monkeypatch.context() as local:
                        local.setattr(os,'replace',fail_owned)
                        result=migrate_agent_yaml_to_frontmatter(OrgPaths(org.root),workflow_authority=org.workflow_authority)
                    assert 'error' in result['maker']
                else:
                    if case=='profile-after-batch':
                        from runtime.daemon.state import DaemonState
                        org.close()
                        state=DaemonState.from_runtime(RuntimeDir.load(org.root.parent.parent),org.settings)
                        org=state.orgs['alpha']
                        org.workflow_authority._profile_coordinator=state.profile_coordinator
                    result=migrate_agent_yaml_to_frontmatter(OrgPaths(org.root),workflow_authority=org.workflow_authority)
                    assert prompt_loader.load_agent(OrgPaths(org.root),'maker').model=='chosen-model'
                    assert not (workspace/'agent.yaml').exists()
                    assert (workspace/'.agent_yaml_consumed').exists()
                assert names.refresh_names(org)
                assert naming_snapshot(org)==before
            record(node,case,org,tmp_path)


def test_a10_bound_callback_continuity(tmp_path,tmp_home,monkeypatch,fake_claude,fake_codex,fake_opencode):
    """Real shipping _run_agent -> registered stub -> source CLI -> real callback.

    A naming-local ASGI barrier observes a genuine callback before its route;
    production exports, provider ABI and the isolated parent remain unchanged.
    """
    import socket
    import uvicorn
    import httpx
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.daemon import paths as daemon_paths
    from runtime.config import Settings
    from runtime.models import TaskRecord,TaskStatus
    from runtime.orchestrator.context_builder import ContextBuilder
    from tests.helpers.completion_plan import completion_prelude
    from tests.helpers.deterministic_plan import DeterministicPlan
    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    node='test_a10_bound_callback_continuity'
    for case in cases(node):
        location=tmp_path/case
        runtime=RuntimeDir.init(location/'runtime');root=runtime.orgs_dir/'alpha'
        paths=seed(root,executor=case)
        # Explicit ABI: Claude4 positional args; Codex/OpenCode3 with actual
        # principal pinned in the plan, never read from a nonexistent fourth.
        plan=DeterministicPlan(location/'plan.sh')
        prefix='task_id="$1"\nsession_id="$2"\n'
        prefix+='agent="$3"\norg_slug="$4"\n' if case=='claude' else 'org_slug="$3"\nagent="maker"\n'
        plan.write_text('#!/bin/sh\nset -eu\n'+prefix+completion_prelude()+'report_completion "$agent" "real owned naming callback"\n')
        monkeypatch.setenv('FAKE_'+case.upper()+'_PLAN',str(plan))
        ownership={'parent_pid':os.getpid(),'case':case,'sessions_limit':16,'aggregate_limit':64,
                   'process_limit':128,'memory_bytes':4*1024**3,'writable_bytes':512*1024**2,
                   'deadline_seconds':30,'plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest()}
        (location/'ownership.json').write_text(json.dumps(ownership))
        state=DaemonState.from_runtime(runtime,Settings());org=state.orgs['alpha']
        ContextBuilder(org.settings,paths,slug='alpha').ensure_workspace_ready(root/'workspaces/maker','maker','You are maker.',provider=case)
        org.db.insert_task(TaskRecord(id='TASK-CALLBACK',assigned_agent='maker',team='engineering',status=TaskStatus.IN_PROGRESS,brief='held real callback'))
        app=create_app(state)
        async def continuity():
            deadline=asyncio.get_running_loop().time()+30
            def remaining(limit, reserve=0):
                budget=min(limit,deadline-asyncio.get_running_loop().time()-reserve)
                assert budget>0,'owned callback deadline exhausted'
                return budget
            arrived=asyncio.Event();release=asyncio.Event();first=True
            async def held_app(scope,receive,send):
                nonlocal first
                if scope.get('type')=='http' and scope.get('path','').endswith('/TASK-CALLBACK/completion') and first:
                    first=False;arrived.set();await release.wait()
                await app(scope,receive,send)
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
            port=sock.getsockname()[1]
            daemon_paths.port_file().write_text(str(port))
            server=uvicorn.Server(uvicorn.Config(held_app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
            serve=asyncio.create_task(server.serve(sockets=[sock]))
            worker=asyncio.create_task(asyncio.to_thread(org.orchestrator._run_agent,'TASK-CALLBACK','maker','naming callback continuity',runtime_session_id='sess-naming-'+case,timeout_seconds_override=20))
            try:
                await asyncio.wait_for(arrived.wait(),remaining(15,8))
                session=org.db.get_task('TASK-CALLBACK').current_session_id
                assert session and org.sessions.get_active('TASK-CALLBACK','maker')==session
                before=snapshot(org)
                authority=org.workflow_authority.verify_admission_ready()
                await rename(org,'Alex')
                after=snapshot(org)
                assert after['files']==before['files'] and after['teams']==before['teams'] and after['schema']==before['schema']
                for table,rows in before['rows'].items():
                    if table == 'sqlite_sequence':
                        # Only the required rename audit append advances its
                        # AUTOINCREMENT sequence; every unrelated counter stays exact.
                        expected = tuple((row[0], row[1], row[2] + 1)
                                         if row[1] == 'audit_log' else row for row in rows)
                        assert after['rows'][table] == expected
                    elif not table.startswith('identity_name_') and table != 'audit_log':
                        assert after['rows'][table] == rows
                assert len(after['rows']['audit_log']) == len(before['rows']['audit_log']) + 1
                assert after['rows']['audit_log'][:-1] == before['rows']['audit_log']
                audits = org.db.execute(
                    "SELECT action, payload FROM audit_log ORDER BY id DESC LIMIT 1").fetchall()
                assert audits[0][0] == 'identity_name_changed'
                assert json.loads(audits[0][1]) == {
                    'org_slug': 'alpha', 'kind': 'agent', 'canonical_id': 'maker',
                    'old_label': 'maker', 'new_label': 'Alex', 'old_revision': 1,
                    'new_revision': 2, 'source': 'founder', 'actor': 'founder',
                }
                assert org.workflow_authority.verify_admission_ready()==authority
                assert org.sessions.get_active('TASK-CALLBACK','maker')==session
                async with httpx.AsyncClient() as client:
                    forged=await client.post(f'http://127.0.0.1:{port}/api/v1/orgs/alpha/tasks/TASK-CALLBACK/completion',
                        headers={'Authorization':'Bearer '+daemon_paths.read_token()},
                        json={'session_id':session,'agent':'Alex','status':'completed','confidence':90,'output_summary':'forged label'},timeout=remaining(5,8))
                    assert forged.status_code==409 and not org.db.get_latest_task_result('TASK-CALLBACK','Alex',session)
                release.set()
                result,report=await asyncio.wait_for(asyncio.shield(worker),remaining(8,5))
                assert result.success and report is not None and report.agent=='maker'
                row=org.db.get_latest_task_result('TASK-CALLBACK','maker',session)
                assert row['agent']=='maker' and row['session_id']==session and row['output_summary']=='real owned naming callback'
                assert_launch_witness(case)
                return session
            finally:
                release.set()
                try:await asyncio.wait_for(asyncio.shield(worker),remaining(8,3))
                finally:
                    server.should_exit=True
                    try:await asyncio.wait_for(serve,remaining(3))
                    finally:
                        if not serve.done():
                            serve.cancel()
                            await asyncio.gather(serve,return_exceptions=True)
                        sock.close()
                        daemon_paths.port_file().unlink(missing_ok=True)
        try:
            session=run(continuity())
            ownership.update(session_ids=[session],callback_cli_witness=True,server_closed=True,worker_joined=True,cleanup_verified_absent=True)
            (location/'ownership.json').write_text(json.dumps(ownership))
            org.close()
            emit_case(node,case,{'session_id':session,'canonical_agent':'maker','callback_cli_witness':True,
                                'cleanup_verified_absent':True,'receipt_root':str(tmp_path/'receipts')},[session])
        finally:org.close()


def test_scenario1_existing_org_upgrade_and_underscore(tmp_path):
    """One complete legacy org containing all three lifecycle definitions."""
    from runtime.models import TaskRecord
    with owned_org(tmp_path, worker='_worker', history='v0', layout='F') as org:
        paths = OrgPaths(org.root)
        definition = prompt_loader.load_agent(paths, '_worker')
        for key, directory in (('waiting', paths.pending_agents_dir),
                               ('retired', paths.agents_dir / '_terminated')):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f'{key}.md').write_text(render_agent_text(replace(definition, name=key)))
        org.teams.add_worker('engineering', 'waiting')
        org.db.insert_task(TaskRecord(id='TASK-HISTORY', assigned_agent='_worker',
                                     brief='immutable old content', team='engineering'))
        # Remove only the newly installed naming extension to model pre-upgrade
        # persisted org state. Independent legacy SQL remains the reference.
        with org.db.workflow_schema_transaction() as conn:
            for table in ('identity_name_claims', 'identity_name_owners', 'identity_name_schema'):
                conn.execute(f'DROP TABLE {table}')
        before = snapshot(org)
        org.close()
        reopened = OrgState.load(slug='alpha', root=org.root, settings=org.settings)
        try:
            assert reopened.naming_readiness == 'ready', reopened.naming_diagnostic
            from runtime.orchestrator.authority import _live_schema_digest, _release_schema_digest
            assert _live_schema_digest(reopened.db) == _release_schema_digest('F', 'v0', 1)
            after = snapshot(reopened)
            assert after['files'] == before['files'] and after['teams'] == before['teams']
            for table, rows in before['rows'].items():
                assert after['rows'][table] == rows, table
            for key, life in (('_worker', 'active'), ('waiting', 'pending'), ('retired', 'terminated')):
                subject = names.classify(reopened, key)
                assert subject.canonical_id == key and subject.current_label == key
                assert subject.lifecycle == life and subject.revision == 1
            installed = naming_snapshot(reopened)
            root, settings = reopened.root, reopened.settings
        finally:
            reopened.close()
        again = OrgState.load(slug='alpha', root=root, settings=settings)
        try:
            assert naming_snapshot(again) == installed
            assert names.classify(again, '_WORKER').canonical_id == '_worker'
            assert again.db.get_task('TASK-HISTORY').assigned_agent == '_worker'
        finally:
            again.close()


def test_scenario4_folded_collision_and_second_org(tmp_path):
    with owned_org(tmp_path / 'alpha') as alpha, owned_org(tmp_path / 'beta', slug='beta') as beta:
        run(rename(alpha, 'Sam'))
        run(rename(alpha, 'Alex'))
        run(rename(alpha, 'Current', name='manager'))
        for token in ('aLEX', 'sam', 'cURRENT', 'MANAGER', 'FOUNDER'):
            before = snapshot(alpha), snapshot(beta)
            with pytest.raises(NamingError, match='identity_name_unavailable'):
                run(rename(alpha, token, name='maker' if token in ('cURRENT', 'MANAGER', 'FOUNDER') else 'manager'))
            assert (snapshot(alpha), snapshot(beta)) == before
        frozen = snapshot(alpha)
        result = run(rename(beta, 'Sam'))
        assert result.canonical_id == 'maker' and result.revision == 2
        assert snapshot(alpha) == frozen
        assert names.classify(alpha, 'Sam').classification == 'former'
        assert names.classify(beta, 'sAM').classification == 'current'


def test_scenario11_one_daemon_async_contention(tmp_path, monkeypatch):
    """Two rename outcomes and both rename/enrollment orderings, finite."""
    test_a3_rename_cas(tmp_path / 'cas', case_ids=(
        'same-owner-different-label', 'two-owners-sAM'))
    test_a5_enroll_rename_race(tmp_path / 'enroll', monkeypatch, case_ids=(
        'manager-enroll-rename-first', 'manager-enroll-create-first'))


def test_scenario12_interrupted_lifecycle_and_reservations(tmp_path, monkeypatch):
    """Actual persisted cuts, then reject/re-enroll ownership and CAS checks."""
    test_a8_crash_cuts(tmp_path / 'cuts', case_ids=('C08', 'C12', 'C17b', 'C17c'))
    test_a6_pending_reuse(tmp_path / 'reuse', monkeypatch, case_ids=(
        'default-reject-release', 'chosen-reject-dormant',
        'same-id-chosen-spelling', 'stale-ABA'))
