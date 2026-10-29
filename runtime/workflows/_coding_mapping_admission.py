"""Version-aware mapping capture inventory and historical native containment.

K2c writes its own original capture through _coding_role_admission. Historical
validity does not restore current permission or create any task/result.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _put as _reader_put,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _encode as _reader_encode,
    _json as _reader_json,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


import base64
import json
from datetime import datetime

from runtime.workflows._coding_source_capture import _decode_capture
from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_source_history import (
    _AUDIT_COLUMNS, _AUDIT_TYPES,
    _check, _inventory, _projection, _session_start, _task,
)

_CAPTURE_SQL = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
    "WHERE action IN ('coding_source_admission','coding_source_operation') AND id>=? ORDER BY id LIMIT ?")


class _MappingCaptures:
    """One complete decoded fixed-action inventory per caller snapshot/budget."""

    def __init__(self, conn, org_slug, budget):
        self.conn, self.org_slug, self.budget = (_reader_reserve(budget, 112) or (conn, org_slug, budget))
        _reader_reserve(budget, 1024)
        self.loaded = False
        self.by_admission, self.by_mapping, self.admission_uses = (_reader_reserve(budget, 112) or (_reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or {}))
        self.tasks, self.starts = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or {}))
        self.operations = (_reader_reserve(budget, 64) or {})
        self.all_captures = (_reader_reserve(budget, 64) or [])

    def load(self):
        if self.loaded:
            return
        owners = set()
        for audit in _inventory(self.conn, _CAPTURE_SQL, (), _AUDIT_COLUMNS, _AUDIT_TYPES, self.budget):
            if audit._text('action', budget=self.budget) == 'coding_source_operation':
                from runtime.workflows._coding_source_admission import decode_operation
                operation = decode_operation(audit, self.org_slug, self.budget)
                key = (operation['principal'], operation['command']['operation_key'])
                _check(key not in self.operations)
                self.operations[key] = (operation, audit)
                continue
            capture = _decode_capture(audit, self.org_slug, self.budget)
            _reader_work(capture, self.budget)
            self.all_captures.append(capture)
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
        from runtime.workflows._coding_settings_history import publications
        human = publications(self.conn, self.org_slug, self.budget)
        _check(not self.by_mapping.keys() & human.keys())
        self.by_mapping.update(human)
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
    _reader_work((_reader_reserve(budget, 112) or (raw, record, selected)), budget)
    task = selected.task_record
    issuer = record['issuer']
    _check(task._text('id', budget=budget) == issuer['task_id']
           and task._text('assigned_agent', budget=budget) == issuer['principal']
           and task._text('current_session_id', budget=budget) == issuer['session_id']
           and task._text('team', budget=budget) == record['mapping']['roles']['agent_manager']['team'])
    from runtime.workflows.coding_authority_history import _setup_configuration, _canonical, _digest
    snapshot = _decoded(_reader_json(_reader_b64decode(record['authority']['snapshot_bytes_base64'], validate=True, budget=budget), budget), budget)
    if record['format'] == 'workflow-coding-agent-read-role-record@1':
        from runtime.workflows._coding_role_records import configuration
        configuration(record['mapping'], snapshot, budget=budget)
    else:
        grant = _decoded(_reader_json(_reader_b64decode(record['authority']['grant_bytes_base64'], validate=True, budget=budget), budget), budget)
        for setup in grant['setups']:
            budget.step()
            _setup_configuration(setup, snapshot, budget=budget)
        agents = _reader_dict_pairs(((_reader_reserve(budget, 96) or (agent['name'],  agent )) for agent in snapshot['agents'] if agent['status'] == 'active'), budget=budget)
        for role, selector in record['mapping']['roles'].items():
            for candidate in [selector, *record['mapping']['eligible_replacements'][role]]:
                budget.step()
                _check(bool(agents[candidate['principal']]['executor']))
        for entry in record['mapping']['repository_bindings']:
            budget.step()
            binding = entry['binding']
            origin = agents[binding['store_owner']]['repos'].get(binding['repo_key'])
            _check(type(origin) is str and _digest(_reader_encode(origin, 'utf8', budget=budget), budget=budget) == binding['origin_digest']
                   and binding['repository_id'] == 'workflow-repository:' + _digest(_canonical(
                       (_reader_reserve(budget, 448) or [org_slug, binding['repo_key'], binding['origin_digest']]), budget=budget), budget=budget))
    start_key = (_reader_reserve(budget, 112) or (issuer['task_id'], issuer['session_id'], issuer['principal']))
    budget.step()
    if start_key not in cache.starts:
        _reader_put(_session_start(conn, issuer, budget), cache.starts, start_key, budget=budget)
    start = cache.starts[start_key]
    _check(start._value('id') == selected.session_start_audit_id)
    observed = datetime.fromisoformat(selected.observed_at.replace('Z', '+00:00'))
    _check(datetime.fromisoformat(start._text('timestamp', budget=budget).replace('Z', '+00:00')) <= observed)
    for original in (_reader_reserve(budget, 128 + 32 * (1 + len(selected.ancestor_records))) or (task, *selected.ancestor_records)):
        _check(datetime.fromisoformat(original._text('created_at', budget=budget).replace('Z', '+00:00')) <= observed)
    for original in (_reader_reserve(budget, 128 + 32 * (1 + len(selected.ancestor_records))) or (task, *selected.ancestor_records)):
        identity = original._text('id', budget=budget)
        budget.step()
        if identity not in cache.tasks:
            _reader_put(_task(conn, identity, budget), cache.tasks, identity, budget=budget)
        today = cache.tasks[identity]
        for name in ('id', 'created_at', 'parent_task_id', 'task_type'):
            _check(today._value(name) == original._value(name))
    return selected


def audit_values(capture, *, budget=None):
    _reader_work(capture, budget)
    row = capture.audit_record
    return _reader_dict_pairs(((_reader_reserve(budget, 96) or (c.name,  row._text(c.name, budget=budget) if c.storage_class == 'text' else c.value )) for c in row.columns), budget=budget)
