"""Version-aware mapping capture inventory and historical native containment.

K2c writes its own original capture through _coding_role_admission. Historical
validity does not restore current permission or create any task/result.
"""
from __future__ import annotations

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
    """One decoded fixed-action inventory per snapshot, complete by default."""

    def __init__(self, conn, org_slug, budget, *, task_ids: frozenset[str] | None = None,
                 authorization_ids: frozenset[str] | None = None):
        self.conn, self.org_slug, self.budget = conn, org_slug, budget
        self.selected = task_ids is not None or authorization_ids is not None
        self.task_ids = set() if task_ids is None else set(task_ids)
        self.authorization_ids = set() if authorization_ids is None else set(authorization_ids)
        self.loaded = False
        self.by_admission, self.by_mapping, self.admission_uses = {}, {}, {}
        self.tasks, self.starts = {}, {}
        self.operations = {}
        self.all_captures = []

    def _selected_audits(self) -> list:
        """Close native capabilities, admissions, owners and operation conflicts.

        Mapping history keeps its original complete org/release contract, so
        every native mapping witness remains in this inventory. Source operation
        keys live inside original base64 commands: all operations for a selected
        principal must be decoded to retain their original uniqueness check.
        """
        from runtime.workflows.coding_authority_history import _bounded_rows
        from runtime.workflows.coding_start_history import _in, _references
        from runtime.workflows.coding_revision import _copy_allowance
        from runtime.workflows._coding_settings_execution import _read_json
        from runtime.workflows.coding_saved_records import _check as saved_check
        identities, principals, results, links = set(self.authorization_ids), set(), set(), set()
        audits, capabilities = {}, set()
        _copy_allowance([sorted(self.task_ids), sorted(identities)], self.budget)
        # Do not narrow the unchanged mapping reader's shared contract.
        for row in _bounded_rows(self.conn, 'workflow_coding_mappings', ('id', 'mapping_bytes'),
                (('text',), ('blob',)), self.budget, where='org_slug=?', args=(self.org_slug,), maximum=1024):
            value = _read_json(row[1], self.budget)
            identities.add(row[0])
            if type(value.get('authority')) is dict and type(value['authority'].get('admission_record_id')) is str:
                identities.add(value['authority']['admission_record_id'])
        while True:
            before = (len(identities), len(self.task_ids), len(principals), len(results), len(links), len(audits), len(capabilities))
            selected, selected_args = _in('id', self.authorization_ids)
            reverse, reverse_args = _references('authority_bytes', identities | self.task_ids)
            excluded, excluded_args = _in('id', capabilities)
            prefix = f'org/{self.org_slug}/coding-capability/'
            for row in _bounded_rows(self.conn, 'workflow_authorization_revisions', ('id', 'authority_bytes'),
                    (('text',), ('blob',)), self.budget,
                    where=f'(({selected}) OR ((source_pin=? OR substr(namespace,1,?)=?) AND ({reverse}))) AND NOT ({excluded})',
                    args=selected_args + ('workflow-coding-capability@1', len(prefix), prefix) + reverse_args + excluded_args,
                    maximum=1024 - len(capabilities)):
                value = _read_json(row[1], self.budget)
                capabilities.add(row[0])
                self.authorization_ids.add(row[0])
                identities.add(row[0])
                if type(value.get('source_admission_id')) is str:
                    identities.add(value['source_admission_id'])
                parent = value.get('parent_id')
                if type(parent) is str and self.conn.execute(
                        'SELECT 1 FROM workflow_authorization_revisions WHERE id=? AND (source_pin=? OR substr(namespace,1,?)=?)',
                        (parent, 'workflow-coding-capability@1', len(prefix), prefix)).fetchone():
                    identities.add(parent)
                    self.authorization_ids.add(parent)
                if type(value.get('authority')) is dict and type(value['authority'].get('admission_record_id')) is str:
                    identities.add(value['authority']['admission_record_id'])
                if type(value.get('issuer')) is dict and type(value['issuer'].get('task_id')) is str:
                    self.task_ids.add(value['issuer']['task_id'])
            tasks, task_args = _in('task_id', self.task_ids)
            reverse, reverse_args = _references('payload', identities)
            owners, owner_args = _in("json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.native_owner.id')", results)
            allocated, allocated_args = _in("json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.result.id')", results)
            linked, linked_args = _in('id', links)
            actors, actor_args = _in("json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.principal')", principals)
            excluded, excluded_args = _in('id', set(audits))
            sql = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
                "WHERE action IN ('coding_source_admission','coding_source_operation') AND ("
                + f'({tasks}) OR ({reverse}) OR ({owners}) OR ({allocated}) OR ({linked}) OR ({actors})) AND NOT ({excluded})'
                + ' AND id>=? ORDER BY id LIMIT ?')
            args = task_args + reverse_args + owner_args + allocated_args + linked_args + actor_args + excluded_args
            for audit in _inventory(self.conn, sql, args, _AUDIT_COLUMNS, _AUDIT_TYPES, self.budget):
                from runtime.workflows.coding_source_history import _json
                value = _json(audit._value('payload'), self.budget)
                saved_check(type(value) is dict)
                audits[audit._value('id')] = audit
                self.task_ids.add(audit._text('task_id'))
                for key in ('authorization_id', 'admission_id', 'source_admission_id'):
                    if type(value.get(key)) is str:
                        identities.add(value[key])
                if type(value.get('authorization_id')) is str:
                    self.authorization_ids.add(value['authorization_id'])
                if audit._text('action') == 'coding_source_operation' and type(value.get('principal')) is str:
                    principals.add(value['principal'])
                owner = value.get('native_owner')
                if type(owner) is dict:
                    if type(owner.get('id')) is str:
                        identities.add(owner['id'])
                        if owner.get('table') == 'workflow_authorization_revisions':
                            self.authorization_ids.add(owner['id'])
                    elif type(owner.get('id')) is int:
                        results.add(owner['id'])
                result = value.get('result')
                if type(result) is dict and type(result.get('id')) is int:
                    results.add(result['id'])
                if type(value.get('callback_admission_audit_id')) is int:
                    links.add(value['callback_admission_audit_id'])
            after = (len(identities), len(self.task_ids), len(principals), len(results), len(links), len(audits), len(capabilities))
            if before == after:
                return [audits[key] for key in sorted(audits)]

    def load(self):
        if self.loaded:
            return
        owners = set()
        audits = (self._selected_audits() if self.selected else
                  _inventory(self.conn, _CAPTURE_SQL, (), _AUDIT_COLUMNS, _AUDIT_TYPES, self.budget))
        for audit in audits:
            if audit._text('action') == 'coding_source_operation':
                from runtime.workflows._coding_source_admission import decode_operation
                operation = decode_operation(audit, self.org_slug, self.budget)
                key = (operation['principal'], operation['command']['operation_key'])
                _check(key not in self.operations)
                self.operations[key] = (operation, audit)
                continue
            capture = _decode_capture(audit, self.org_slug, self.budget)
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
    task = selected.task_record
    issuer = record['issuer']
    _check(task._text('id') == issuer['task_id']
           and task._text('assigned_agent') == issuer['principal']
           and task._text('current_session_id') == issuer['session_id']
           and task._text('team') == record['mapping']['roles']['agent_manager']['team'])
    from runtime.workflows.coding_authority_history import _setup_configuration, _canonical, _digest
    snapshot = _decoded(json.loads(base64.b64decode(record['authority']['snapshot_bytes_base64'], validate=True)), budget)
    if record['format'] == 'workflow-coding-agent-read-role-record@1':
        from runtime.workflows._coding_role_records import configuration
        configuration(record['mapping'], snapshot)
    else:
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
