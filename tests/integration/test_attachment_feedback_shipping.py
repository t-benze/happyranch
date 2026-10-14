"""Ordinary THR304 F1–F8 component integration at shipping result/SQL seams.

Run only through integration_parent in disposable hosted Ubuntu. These cases
make no process, scheduler, restart, simultaneous-race or later-chain claim.
"""
from __future__ import annotations

from tests.helpers.integration_stub_guard.guard import require_parent_environment

require_parent_environment()

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.orchestrator import Orchestrator, completion_report_from_result_row
from runtime.orchestrator import run_step
from runtime.orchestrator.teams import TeamManager, TeamsRegistry

pytestmark = pytest.mark.integration


class QueueEffects:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def put_nowait(self, slug: str, task_id: str, **kwargs: Any) -> None:
        self.items.append((slug, task_id))


class ShippingState:
    def __init__(self, root: Path) -> None:
        self.paths = OrgPaths(root=root)
        root.mkdir(parents=True)
        self.paths.agents_dir.mkdir(parents=True)
        for name in ('engineering_manager', 'dev_agent'):
            (self.paths.workspaces_dir / name).mkdir(parents=True)
            agent = AgentDef(name=name, team='engineering', role='manager',
                executor='claude', allow_rules=(), repos={}, enrolled_by=None,
                enrolled_at_task=None, enrolled_at=None, system_prompt='Synthetic agent',
                description='', model=None)
            (self.paths.agents_dir / f'{name}.md').write_text(render_agent_text(agent))
        self.teams = TeamsRegistry({'engineering': TeamManager(
            name='engineering_manager', team='engineering', workers=('dev_agent',))})
        self.queue = QueueEffects()
        self.notifications: list[dict] = []
        self.open()
        self.store = TaskAttachmentStore(self.paths.task_attachments_dir)

    def open(self) -> None:
        self.db = Database(self.paths.db_path)
        self.orch = Orchestrator(self.db, Settings(), self.paths, 'synthetic', self.teams)
        self.orch.attach_queue(self.queue)
        # Record the notification effect; no network or scheduler is part of these cases.
        self.orch.notify_escalated = lambda **fields: self.notifications.append(fields)

    def reopen(self) -> None:
        self.db.close()
        self.open()

    def task(self, parent: str | None = None) -> str:
        task_id = self.db.next_task_id()
        self.db.insert_task(TaskRecord(id=task_id, brief='Synthetic decision owner',
            assigned_agent='engineering_manager', parent_task_id=parent, task_type='task'))
        return task_id

    def refs(self, count: int = 1) -> list[dict]:
        refs = []
        for _ in range(count):
            key = 'synthetic-' + uuid4().hex
            self.store.put(key, b'ordinary synthetic attachment\n')
            refs.append({'storage_key': key, 'display_name': 'evidence.txt'})
        return refs

    def accepted(self, task_id: str, decision: dict) -> dict:
        task = self.db.get_task(task_id)
        assert task is not None
        assert self.db.try_claim_for_step(task_id, task.status, task.block_kind,
                                          task.orchestration_step_count + 1)
        session = 'sess-' + uuid4().hex
        self.db.update_task(task_id, current_session_id=session)
        assert self.db.admit_task_completion_callback(task_id=task_id,
            agent='engineering_manager', session_id=session,
            output_summary='Synthetic accepted decision', confidence_score=90,
            decision_json=json.dumps(decision), risks_flagged=[])
        row = next(row for row in self.db.get_task_results(task_id)
                   if row['session_id'] == session)
        assert type(row['id']) is int and row['id'] > 0
        return row

    def consume(self, row: dict) -> None:
        report = completion_report_from_result_row(row['task_id'], row,
                                                   fallback_agent=row['agent'])
        assert report.decision is not None
        run_step._consume_completion_report(self.orch, row['task_id'], report,
            result_row_id=row['id'], reclaim_terminal_worktree=False)

    def submit(self, task_id: str, decision: dict) -> dict:
        row = self.accepted(task_id, decision)
        self.consume(row)
        return row

    def rows(self, table: str) -> list[dict]:
        assert table in {'tasks', 'task_results', 'task_attachments', 'audit_log'}
        return [dict(row) for row in self.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]

    def corrections(self, task_id: str) -> list[dict]:
        return [json.loads(row['payload']) for row in self.rows('audit_log')
                if row['task_id'] == task_id and row['action'] == 'orchestration_step'
                and 'attachment_correction' in json.loads(row['payload'])]

    def footprint(self) -> dict:
        return {table: self.rows(table) for table in
                ('tasks', 'task_results', 'task_attachments', 'audit_log')}

    def children(self, task_id: str) -> list[dict]:
        return [row for row in self.rows('tasks') if row['parent_task_id'] == task_id]

    def prechild(self, task_id: str) -> None:
        assert not self.children(task_id)
        assert not self.rows('task_attachments')
        assert not any(row['action'] == 'task_attachment_added' for row in self.rows('audit_log'))
        task = self.db.get_task(task_id)
        assert task.active_chain is None and task.active_fanout is None


@pytest.fixture
def shipping(tmp_path: Path) -> Iterator[ShippingState]:
    state = ShippingState(tmp_path / 'synthetic-org')
    try:
        yield state
    finally:
        state.db.close()


def missing() -> dict:
    return {'storage_key': 'nonexistent-synthetic-key', 'display_name': 'missing.txt'}


def delegate(refs: list[dict] | None = None, nested: list[dict] | None = None) -> dict:
    return {'action': 'delegate', 'agent': 'dev_agent', 'prompt': 'Synthetic child',
            **({'attachments': refs} if refs else {}),
            **({'then': [{'agent': 'dev_agent', 'prompt': 'Later', 'attachments': nested}]}
               if nested else {})}


def fanout(refs: list[dict] | None = None, nested: list[dict] | None = None) -> dict:
    return {'action': 'fanout', 'width_cap_ack': 2, 'children': [
        {'agent': 'dev_agent', 'prompt': 'Plain', **({'attachments': refs} if refs else {})},
        {'agent': 'dev_agent', 'prompt': 'Carrier', 'then': [
            {'agent': 'dev_agent', 'prompt': 'Later',
             **({'attachments': nested} if nested else {})}]}]}


def assert_feedback(state: ShippingState, row: dict, code: str, remedy: str,
                    key: str | None = None, owner: str | None = None) -> None:
    entries = state.corrections(row['task_id'])
    assert len(entries) == 1
    marker = entries[0]['attachment_correction']
    assert marker['error_code'] == code and marker['opportunity'] == 1
    assert marker['accepted_result_id'] == row['id']
    assert marker['accepted_session_id'] == row['session_id']
    reason = entries[0]['decision']['reason']
    assert code in reason and remedy in reason and 'new authorized session/result' in reason
    if key is None:
        assert 'storage_key=' not in reason
    else:
        assert 'storage_key=' + json.dumps(key) in reason
    if owner is not None:
        assert 'claimed_by=' + json.dumps(owner) in reason
    assert state.db.get_task(row['task_id']).status == TaskStatus.PENDING
    before = state.footprint()
    state.consume(row)
    assert state.footprint() == before  # original INTEGER result consumes once


@pytest.mark.parametrize('variant', ['direct', 'nested-only', 'invalid-name', 'extension'])
def test_f1_actionable_delegate_rejection(shipping: ShippingState, variant: str) -> None:
    state = shipping
    task = state.task()
    if variant in ('direct', 'nested-only'):
        decision = delegate([missing()]) if variant == 'direct' else delegate(nested=[missing()])
        code, remedy, key = 'task_attachment_not_found', 'upload the permitted source anew', missing()['storage_key']
    else:
        ref = state.refs()[0]
        ref['display_name'] = 'unsafe/name.txt' if variant == 'invalid-name' else 'file.exe'
        decision = delegate([ref])
        code = 'invalid_attachment_display_name' if variant == 'invalid-name' else 'unsupported_attachment_type'
        remedy = 'safe supported filename' if variant == 'invalid-name' else 'allowlisted extension'
        key = None
    row = state.submit(task, decision)
    assert_feedback(state, row, code, remedy, key)
    state.prechild(task)


@pytest.mark.parametrize('variant', ['direct-invalid', 'nested-invalid', 'cross-list-duplicate'])
def test_f2_atomic_fanout_validation(shipping: ShippingState, variant: str) -> None:
    state = shipping
    task = state.task()
    valid = state.refs()
    decision = fanout(valid, [missing()] if variant == 'nested-invalid' else None)
    if variant == 'direct-invalid':
        decision['children'][1]['attachments'] = [missing()]
    elif variant == 'cross-list-duplicate':
        decision['children'][1]['then'][0]['attachments'] = valid
    row = state.submit(task, decision)
    duplicate = variant == 'cross-list-duplicate'
    assert_feedback(state, row, 'duplicate_attachment' if duplicate else 'task_attachment_not_found',
        'distinct fresh authorized uploads' if duplicate else 'upload the permitted source anew',
        valid[0]['storage_key'] if duplicate else missing()['storage_key'])
    state.prechild(task)


def claim(state: ShippingState, owner: str, ref: dict) -> None:
    state.db.insert_task_attachment(task_id=owner, ordinal=0,
        storage_key=ref['storage_key'], display_name=ref['display_name'],
        size_bytes=len(state.store.path_for(ref['storage_key']).read_bytes()),
        content_type='text/plain', uploaded_by='engineering_manager')


def test_f3_exclusive_same_tree_claim(shipping: ShippingState) -> None:
    state = shipping
    task = state.task()
    owner = state.task(parent=task)
    ref = state.refs()[0]
    claim(state, owner, ref)
    links = state.rows('task_attachments')
    row = state.submit(task, delegate([ref]))
    assert_feedback(state, row, 'attachment_already_claimed', 'output_dir/recall', ref['storage_key'], owner)
    assert state.rows('task_attachments') == links
    assert [child['id'] for child in state.children(task)] == [owner]
    assert state.db.get_task(task).active_chain is None


def test_f3_six_in_one_list(shipping: ShippingState) -> None:
    task = shipping.task()
    row = shipping.submit(task, delegate(shipping.refs(6)))
    assert_feedback(shipping, row, 'too_many_attachments', 'at most five attachments')
    shipping.prechild(task)


@pytest.mark.parametrize('nonroot', [False, True], ids=['root', 'decision-owning-nonroot'])
def test_f4_durable_shared_budget(shipping: ShippingState, nonroot: bool) -> None:
    state = shipping
    parent = state.task() if nonroot else None
    task = state.task(parent)
    accepted = []
    for index, decision in enumerate((delegate([missing()]), fanout(nested=[missing()]), delegate([missing()]))):
        accepted.append(state.submit(task, decision))
        assert len(state.corrections(task)) == min(index + 1, 2)
        state.prechild(task)
        if index < 2:
            assert state.db.get_task(task).status == TaskStatus.PENDING
            state.reopen()  # durability only; no daemon restart claim
    assert len({row['session_id'] for row in accepted}) == 3
    assert len({row['id'] for row in accepted}) == 3
    markers = [entry['attachment_correction'] for entry in state.corrections(task)]
    assert [marker['opportunity'] for marker in markers] == [1, 2]
    assert [marker['accepted_result_id'] for marker in markers] == [row['id'] for row in accepted[:2]]
    assert state.db.get_task(task).status == (TaskStatus.FAILED if nonroot else TaskStatus.ESCALATED)
    other = state.task()
    row = state.submit(other, delegate([missing()]))
    assert_feedback(state, row, 'task_attachment_not_found', 'upload the permitted source anew', missing()['storage_key'])


def test_f4_inconsistent_history_refuses_without_repair(shipping: ShippingState) -> None:
    state = shipping
    task = state.task()
    state.submit(task, delegate([missing()]))
    audit = next(row for row in state.rows('audit_log')
                 if 'attachment_correction' in json.loads(row['payload']))
    corrupt = json.loads(audit['payload'])
    corrupt['attachment_correction']['accepted_session_id'] = 'sess-wrong-accepted-association'
    state.db.execute('UPDATE audit_log SET payload=? WHERE id=?', (json.dumps(corrupt), audit['id']))
    state.db._conn.commit()
    row = state.accepted(task, fanout(nested=[missing()]))
    result_count = len(state.rows('task_results'))
    with pytest.raises(ValueError, match='attachment'):
        state.consume(row)
    assert len(state.rows('task_results')) == result_count
    assert state.corrections(task) == [corrupt]
    assert state.db.get_task(task).status == TaskStatus.IN_PROGRESS
    state.prechild(task)


@pytest.mark.parametrize('kind', ['delegate', 'fanout'])
def test_f5_fresh_correction_and_once_only_graph(shipping: ShippingState, kind: str) -> None:
    state = shipping
    task = state.task()
    originals = [state.submit(task, delegate([missing()])),
                 state.submit(task, fanout(nested=[missing()]))]
    direct, nested = state.refs(5), state.refs()
    good = delegate(direct, nested) if kind == 'delegate' else fanout(direct, nested)
    if kind == 'fanout':
        # Direct refs belong to the carrier, with no first-leg reclaim.
        good['children'][1]['attachments'] = good['children'][0].pop('attachments')
    corrected = state.submit(task, good)
    assert corrected['session_id'] not in {row['session_id'] for row in originals}
    assert corrected['id'] not in {row['id'] for row in originals}
    children = state.children(task)
    assert len(children) == (1 if kind == 'delegate' else 2)
    all_new = [row for row in state.rows('tasks') if row['id'] != task]
    assert len(all_new) == (1 if kind == 'delegate' else 3)
    owner = children[0] if kind == 'delegate' else next(child for child in children if child['active_chain'])
    links = state.rows('task_attachments')
    assert len(links) == 5
    assert {link['storage_key'] for link in links} == {ref['storage_key'] for ref in direct}
    assert {link['task_id'] for link in links} == {owner['id']}
    assert len([row for row in state.rows('audit_log') if row['action'] == 'task_attachment_added']) == 5
    chain_owner = state.db.get_task(task) if kind == 'delegate' else state.db.get_task(owner['id'])
    assert nested[0]['storage_key'] in chain_owner.active_chain
    assert state.db.get_task_attachment_by_storage_key(nested[0]['storage_key']) is None
    if kind == 'fanout':
        first_legs = state.children(owner['id'])
        assert len(first_legs) == 1 and not state.db.list_task_attachments(first_legs[0]['id'])
        assert {ref.storage_key for ref in state.db.resolve_ancestor_attachments(first_legs[0]['id'])} == {ref['storage_key'] for ref in direct}
        assert nested[0]['storage_key'] in state.db.get_task(task).active_fanout
    assert len(state.corrections(task)) == 2
    before = state.footprint()
    for row in (*originals, corrected, corrected):
        state.consume(row)
    assert state.footprint() == before


@pytest.mark.parametrize('boundary', ['before-A', 'after-A-before-B'])
def test_f6_original_claim_loss(shipping: ShippingState, monkeypatch: pytest.MonkeyPatch,
                                boundary: str) -> None:
    state = shipping
    task = state.task()
    row = state.accepted(task, delegate([missing()]))
    method = 'try_attachment_retry_feedback' if boundary == 'before-A' else 'admit_retry_feedback'
    original = getattr(state.db, method)
    calls = []

    def replace_owner(*args: Any, **kwargs: Any) -> Any:
        calls.append(True)
        state.db.update_task(task, assigned_agent='dev_agent', current_session_id='sess-replacement')
        return original(*args, **kwargs)

    monkeypatch.setattr(state.db, method, replace_owner)
    state.consume(row)
    assert calls == [True]
    assert len(state.corrections(task)) == (0 if boundary == 'before-A' else 1)
    assert len(state.rows('task_results')) == (1 if boundary == 'before-A' else 2)
    assert state.db.get_task(task).assigned_agent == 'dev_agent'
    assert state.db.get_task(task).current_session_id == 'sess-replacement'
    assert not state.queue.items
    state.prechild(task)


@pytest.mark.parametrize('kind', ['delegate', 'fanout'])
def test_f7_transactional_attachment_owner_recheck(shipping: ShippingState,
        monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    state = shipping
    task = state.task()
    ref = state.refs()[0]
    owner = state.task(parent=task)
    row = state.accepted(task, delegate([ref]) if kind == 'delegate' else fanout([ref], state.refs()))
    method = 'try_delegate' if kind == 'delegate' else 'try_delegate_many'
    original = getattr(state.db, method)
    baseline = {}

    def ownership_change(*args: Any, **kwargs: Any) -> Any:
        claim(state, owner, ref)  # after actual prevalidation, before real BEGIN
        baseline.update(state.footprint())
        return original(*args, **kwargs)

    monkeypatch.setattr(state.db, method, ownership_change)
    with pytest.raises(sqlite3.IntegrityError, match='task_attachments.storage_key'):
        state.consume(row)
    assert state.footprint() == baseline
    assert state.db.get_task_attachment_by_storage_key(ref['storage_key']).task_id == owner
    assert not state.corrections(task) and not state.children(owner)
    assert state.db.get_task(task).active_chain is None and state.db.get_task(task).active_fanout is None
    assert state.db.get_task(task).revision_count == 0
    assert not state.queue.items


def test_f7_late_fanout_precommit_failure_rolls_back_every_write(shipping: ShippingState,
        monkeypatch: pytest.MonkeyPatch) -> None:
    state = shipping
    task = state.task()
    good = fanout(state.refs(), state.refs())
    good['children'][1]['attachments'] = state.refs()
    row = state.accepted(task, good)
    original = state.db.try_delegate_many
    connection = state.db._conn
    baseline = {}
    observed = []

    class FailCommit:
        def __getattr__(self, name: str) -> Any:
            return getattr(connection, name)

        def commit(self) -> None:
            assert connection.in_transaction
            assert len(state.children(task)) == 2
            assert len(state.rows('tasks')) == 4  # root, two immediate, one first leg
            assert len(state.rows('task_attachments')) == 2
            assert len([audit for audit in state.rows('audit_log') if audit['action'] == 'task_attachment_added']) == 2
            assert state.db.get_task(task).active_fanout is not None
            assert any(child['active_chain'] for child in state.children(task))
            observed.append(True)
            raise sqlite3.OperationalError('synthetic late precommit failure')

    def fail_after_real_writes(*args: Any, **kwargs: Any) -> Any:
        baseline.update(state.footprint())  # callback and initial-decision audit already committed
        with monkeypatch.context() as patch:
            patch.setattr(state.db, '_conn', FailCommit())
            return original(*args, **kwargs)

    monkeypatch.setattr(state.db, 'try_delegate_many', fail_after_real_writes)
    with pytest.raises(sqlite3.OperationalError, match='late precommit failure'):
        state.consume(row)
    assert observed == [True] and state.footprint() == baseline
    assert len(state.rows('task_results')) == 1
    assert len([audit for audit in state.rows('audit_log') if audit['action'] == 'orchestration_step']) == 1
    assert state.db.get_task(task).revision_count == 0
    assert not state.corrections(task) and not state.queue.items
    state.prechild(task)


@pytest.mark.parametrize('variant', ['unknown', 'malformed-known', 'subclass'])
def test_f8_strict_prechild_exception_boundary(shipping: ShippingState,
        monkeypatch: pytest.MonkeyPatch, variant: str) -> None:
    state = shipping
    task = state.task()
    row = state.accepted(task, delegate(state.refs()))

    class Lookalike(ValueError):
        pass

    exc = (ValueError('unknown synthetic failure') if variant == 'unknown' else
           ValueError(json.dumps({'code': 'task_attachment_not_found'})) if variant == 'malformed-known' else
           Lookalike(json.dumps({'code': 'task_attachment_not_found', 'storage_key': 'synthetic'})))

    def fail_validation(*args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(run_step, '_validate_decision_attachments', fail_validation)
    state.consume(row)  # existing delegate disposition is FAILED with original exception text
    assert state.db.get_task(task).status == TaskStatus.FAILED
    assert str(exc) in state.db.get_task(task).note
    assert len(state.rows('task_results')) == 1 and not state.corrections(task)
    state.prechild(task)


def test_f8_postcommit_effect_failure_keeps_graph(shipping: ShippingState,
        monkeypatch: pytest.MonkeyPatch) -> None:
    state = shipping
    task = state.task()
    row = state.accepted(task, fanout(state.refs(), state.refs()))
    observed = []

    def fail_logging(**kwargs: Any) -> None:
        assert len(state.children(task)) == 2
        assert len(state.rows('tasks')) == 4
        assert state.db.get_task(task).active_fanout is not None
        observed.append(state.footprint())
        raise ValueError(json.dumps({'code': 'task_attachment_not_found', 'storage_key': 'postcommit'}))

    monkeypatch.setattr(state.orch._audit, 'log_fanout_spawned', fail_logging)
    with pytest.raises(ValueError, match='postcommit'):
        state.consume(row)
    assert len(observed) == 1 and state.footprint() == observed[0]
    assert len(state.rows('task_results')) == 1 and not state.corrections(task)
    assert not state.queue.items
