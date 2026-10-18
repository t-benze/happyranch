"""Private mapping owner composition; native owners remain read-only.

Historical capture validity is separate from today's session, Pause, move and
published configuration. No source result, callback, claim or task is created.
"""
from __future__ import annotations

import base64
import json
import hashlib
from datetime import datetime

from runtime.workflows._coding_source_capture import _decode_capture
from runtime.workflows.coding_authority_history import _Budget, _decoded
from runtime.workflows.coding_source_history import (
    _AUDIT_COLUMNS, _AUDIT_TYPES, _RESULT_COLUMNS, _RESULT_TYPES, _RESULT_SQL,
    _check, _inventory, _projection, _session_start, _task,
)

_CAPTURE_SQL = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
    "WHERE action='coding_source_admission' AND id>=? ORDER BY id LIMIT ?")


def native_owner(org, principal, conn):
    """Authenticate the actual nonallocating manager invocation in this snapshot."""
    principal.revalidate()
    _check(principal.principal_kind == 'agent' and principal.proof_kind == 'task_session'
           and principal.org_slug == org.slug and principal.principal_id != 'founder')
    issuer = dict(kind='agent', principal=principal.principal_id, task_id=principal.task_id,
                  session_id=principal.session_id, result_id=None)
    budget = _Budget()
    task = _task(conn, principal.task_id, budget)
    _check(task._text('assigned_agent') == principal.principal_id
           and task._text('team') == principal.team_slug
           and task._text('current_session_id') == principal.session_id
           and task._text('task_type') == 'task' and task._text('status') == 'in_progress')
    for name in ('block_kind', 'cancelled_at', 'active_chain', 'active_fanout', 'blocked_on_job_ids'):
        _check(task._value(name) is None)
    chain = [task]
    seen = {principal.task_id}
    while chain[-1]._text('parent_task_id') is not None:
        parent = chain[-1]._text('parent_task_id')
        _check(len(chain) < 20 and parent not in seen)
        seen.add(parent)
        row = _task(conn, parent, budget)
        _check(row._text('team') == principal.team_slug and row._value('cancelled_at') is None
               and row._text('status') not in ('failed', 'cancelled', 'completed', 'superseded'))
        chain.append(row)
    start = _session_start(conn, issuer, budget)
    # Complete same-SID inventory: no callback is borrowed as operation authority.
    _check(not list(_inventory(conn, _RESULT_SQL, (principal.task_id, principal.session_id),
                              _RESULT_COLUMNS, _RESULT_TYPES, budget)))
    sessions = org.sessions
    _check(sessions.get_context_by_session(principal.session_id) ==
           (org.slug, principal.task_id, principal.principal_id)
           and sessions.get_active(principal.task_id, principal.principal_id) == principal.session_id
           and not sessions.is_recovery_session(principal.task_id, principal.principal_id, principal.session_id))
    pause = org.db._task_pause_store
    root, journal = pause.held_uncommitted(principal.task_id)
    _check(journal is not None and root['id'] == chain[-1]._text('id'))
    owners = [owner for owner in pause._live.values()
              if (owner.task.id, owner.agent, owner.session_id) ==
              (principal.task_id, principal.principal_id, principal.session_id)]
    _check(len(owners) == 1)
    owner = owners[0]
    _check(owner.orch is org.orchestrator and owner.owner in ('ordinary', 'v2')
           and owner.admitted and owner.attempt_committed and owner.unsettled)
    entries = [entry for entry in journal['journal']['entries'] if entry['id'] == owner.identity]
    _check(len(entries) == 1)
    entry = entries[0]
    _check(entry['org'] == org.slug and entry['task_id'] == principal.task_id
           and entry['agent'] == principal.principal_id and entry['session_id'] == principal.session_id
           and entry['owner'] == owner.owner and entry['phase'] == 'running'
           and entry['generation'] == journal['generation'] and not journal['held']
           and not entry['context'].get('execution_unknown')
           and not entry['context'].get('producer_settled')
           and task._value('orchestration_step_count') == entry['fingerprint']['count'] + 1
           and task._text('brief') is not None
           and hashlib.sha256(task._value('brief')).hexdigest() == entry['fingerprint']['brief_digest'])
    org.orchestrator._team_moves.check_task_uncommitted(org.db.get_task(principal.task_id))
    return issuer, tuple(chain), start, owner


def _encode_record(record):
    return dict(columns=[dict(name=c.name, storage_class=c.storage_class,
        value=base64.b64encode(c.value).decode('ascii') if c.storage_class == 'text' else c.value)
        for c in record.columns])


def capture_body(org, principal, conn, *, admission_id, mapping_id, raw, now, max_revise_rounds):
    issuer, chain, start, owner = native_owner(org, principal, conn)
    task = chain[0]
    return dict(format='workflow-coding-source-admission@1', phase='source', org_slug=org.slug,
        source_admission_id=admission_id, native_owner=dict(table='workflow_coding_mappings', id=mapping_id),
        command_bytes_base64=base64.b64encode(raw).decode('ascii'), task_record=_encode_record(task),
        ancestor_records=[_encode_record(row) for row in chain[1:]],
        session_start_audit_id=start._value('id'), result=None, callback_admission_audit_id=None,
        native_claim=None, owner_invocation_id=owner.identity,
        budgets=dict(source_step=task._value('orchestration_step_count'),
            source_revision=task._value('revision_count'), session_timeout_seconds=task._value('session_timeout_seconds'),
            max_revise_rounds=max_revise_rounds, coding_attempt_limit=64, coding_event_limit=4096,
            coding_revision_limit=3, coding_same_principal_retry_limit=1), observed_at=now)


class _MappingCaptures:
    """One complete decoded fixed-action inventory per caller snapshot/budget."""

    def __init__(self, conn, org_slug, budget):
        self.conn, self.org_slug, self.budget = conn, org_slug, budget
        self.loaded = False
        self.by_admission, self.by_mapping, self.admission_uses = {}, {}, {}
        self.tasks, self.starts = {}, {}

    def load(self):
        if self.loaded:
            return
        owners = set()
        for audit in _inventory(self.conn, _CAPTURE_SQL, (), _AUDIT_COLUMNS, _AUDIT_TYPES, self.budget):
            capture = _decode_capture(audit, self.org_slug, self.budget)
            owner = (capture.phase, capture.native_owner.table, capture.native_owner.id)
            _check(owner not in owners)
            owners.add(owner)
            self.admission_uses[capture.source_admission_id] = (
                self.admission_uses.get(capture.source_admission_id, 0) + 1)
            if capture.phase == 'source':
                _check(capture.source_admission_id not in self.by_admission)
                self.by_admission[capture.source_admission_id] = capture
            if capture.native_owner.table == 'workflow_coding_mappings':
                _check(capture.native_owner.id not in self.by_mapping)
                self.by_mapping[capture.native_owner.id] = capture
        self.loaded = True


def mapping_capture(conn, org_slug, mapping_id, raw, record, budget, *, inventory=None):
    """Read the original mapping witness, never reconstruct old live permission."""
    cache = inventory if inventory is not None else _MappingCaptures(conn, org_slug, budget)
    cache.load()
    budget.step()
    selected = cache.by_mapping.get(mapping_id)
    admission = record['authority']['admission_record_id']
    _check(selected is not None and selected is cache.by_admission.get(admission)
           and cache.admission_uses.get(admission) == 1
           and selected.phase == 'source' and selected.source_admission_id == admission
           and selected.command_bytes == raw and selected.result_record is None
           and selected.observed_at == record['created_at'])
    task = selected.task_record
    issuer = record['issuer']
    _check(task._text('id') == issuer['task_id']
           and task._text('assigned_agent') == issuer['principal']
           and task._text('current_session_id') == issuer['session_id']
           and task._text('team') == record['mapping']['roles']['agent_manager']['team'])
    from runtime.workflows.coding_authority_history import _setup_configuration, _canonical, _digest
    snapshot = _decoded(json.loads(base64.b64decode(record['authority']['snapshot_bytes_base64'], validate=True)), budget)
    grant = _decoded(json.loads(base64.b64decode(record['authority']['grant_bytes_base64'], validate=True)), budget)
    for setup in grant['setups']:
        budget.step()
        _setup_configuration(setup, snapshot)
    agents = {agent['name']: agent for agent in snapshot['agents'] if agent['status'] == 'active'}
    for role, selector in record['mapping']['roles'].items():
        for candidate in [selector, *record['mapping']['eligible_replacements'][role]]:
            budget.step()
            _check(bool(agents[candidate['principal']]['executor']))
    for entry in record['mapping']['repository_bindings']:
        budget.step()
        binding = entry['binding']
        origin = agents[binding['store_owner']]['repos'].get(binding['repo_key'])
        _check(type(origin) is str and _digest(origin.encode('utf8')) == binding['origin_digest']
               and binding['repository_id'] == 'workflow-repository:' + _digest(_canonical(
                   [org_slug, binding['repo_key'], binding['origin_digest']])))
    start_key = (issuer['task_id'], issuer['session_id'], issuer['principal'])
    budget.step()
    if start_key not in cache.starts:
        cache.starts[start_key] = _session_start(conn, issuer, budget)
    start = cache.starts[start_key]
    _check(start._value('id') == selected.session_start_audit_id)
    observed = datetime.fromisoformat(selected.observed_at.replace('Z', '+00:00'))
    _check(datetime.fromisoformat(start._text('timestamp').replace('Z', '+00:00')) <= observed)
    for original in (task, *selected.ancestor_records):
        _check(datetime.fromisoformat(original._text('created_at').replace('Z', '+00:00')) <= observed)
    for original in (task, *selected.ancestor_records):
        identity = original._text('id')
        budget.step()
        if identity not in cache.tasks:
            cache.tasks[identity] = _task(conn, identity, budget)
        today = cache.tasks[identity]
        for name in ('id', 'created_at', 'parent_task_id', 'task_type'):
            _check(today._value(name) == original._value(name))
    return selected


def audit_values(capture):
    row = capture.audit_record
    return {c.name: row._text(c.name) if c.storage_class == 'text' else c.value for c in row.columns}
