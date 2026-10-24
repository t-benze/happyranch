"""Private D1 source-operation replay and complete cold native composition.

An operation is metadata joining original command bytes to an already genuine
P mint. It grants no access and does not replace or recapture the ORIGINAL source.
All mixed-family work uses the caller's single bounded inventory and budget.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from runtime.workflows.coding_authority_history import _bounded_rows, _canonical, _decoded, _digest
from runtime.workflows.coding_saved_records import (
    _closed, _token, _timestamp, read_saved_source_command,
)
from runtime.workflows.coding_source_history import _check, _json
from runtime.workflows.coding_source_admission_history import _read

_COLUMNS = ('id', 'namespace', 'revision', 'authority_bytes', 'authority_digest', 'source_pin', 'created_at')
_TYPES = ('text', 'text', 'integer', 'blob', 'text', 'text', 'text')


def decode_operation(audit, org_slug, budget):
    body = _closed(_json(audit._value('payload'), budget),
        'format org_slug principal authorization_id admission_id session_id command_bytes_base64 command_digest created_at')
    _check(body['format'] == 'workflow-coding-source-operation@1' and body['org_slug'] == org_slug)
    for key in ('authorization_id', 'admission_id', 'session_id'):
        _token(body[key])
    _timestamp(body['created_at'])
    _timestamp(audit._text('timestamp'))
    _check(body['principal'] == 'agent:' + audit._text('agent')
           and datetime.fromisoformat(body['created_at'].replace('Z', '+00:00')) <=
               datetime.fromisoformat(audit._text('timestamp').replace('Z', '+00:00')))
    encoded = body['command_bytes_base64']
    _check(type(encoded) is str and len(encoded) <= 4 * ((65536 + 2) // 3))
    raw = base64.b64decode(encoded, validate=True)
    _check(base64.b64encode(raw).decode('ascii') == encoded and _digest(raw) == body['command_digest'])
    from runtime.workflows._coding_source_capture import _copy_bytes
    _copy_bytes(raw, budget)
    read_saved_source_command(raw, expected_org_slug=org_slug)
    body = dict(body, command_bytes=raw, command=_decoded(json.loads(raw), budget))
    return body


def source_storage(conn, org_slug, budget, setups, captures, mappings):
    """Complete positive row expectations; unknown/future families still refuse."""
    expected, histories, identities, admissions = {}, {}, set(), set()
    rows = {}
    prefix = f'org/{org_slug}/coding-capability/'
    for row in _bounded_rows(conn, 'workflow_authorization_revisions', _COLUMNS,
            tuple((kind,) for kind in _TYPES), budget,
            where='source_pin=? OR substr(namespace,1,?)=?',
            args=('workflow-coding-capability@1', len(prefix), prefix), maximum=1024):
        values = budget.row(row, _TYPES, counted=True, charged=True)
        _check(values[0] not in rows)
        rows[values[0]] = dict(zip(_COLUMNS, values, strict=True))
    if not rows and mappings is None:
        return {}, {}  # Ordinary nonmapping audit/watermark path remains unchanged.
    captures.load()
    operations = {}
    for operation, audit in captures.operations.values():
        budget.step()
        identity = operation['authorization_id']
        _check(identity in rows and identity not in operations)
        operations[identity] = (operation, audit)
    _check(rows.keys() == operations.keys())
    for identity, native in rows.items():
        budget.step()
        history = _read(conn, org_slug, identity, budget=budget, setups=setups, inventory=captures)
        selected = history.native_records.capability_history.selected
        record = _decoded(json.loads(selected.authority_bytes), budget)
        # ONLY these nonallocating source producers are supported. K3 and all
        # downstream capability subjects need their own producers/readers.
        _check(record['level'] == 'source' and record['issuer']['result_id'] is None)
        operation, audit = operations[identity]
        command = operation['command']
        _check(operation['principal'] == 'agent:' + record['issuer']['principal']
               and audit._text('task_id') == record['issuer']['task_id']
               and operation['session_id'] == record['issuer']['session_id']
               and operation['admission_id'] == selected.own_admission.id
               and operation['created_at'] == record['created_at']
               and command['parent_id'] == record['parent_id'] and command['action'] == record['action'])
        radius = dict(command['work'], provider_repository=None, pr_number=None,
                      expected_head=None, head_rule='own-development-output@1')
        _check(record['radius'] == radius)
        _check(native == dict(id=selected.authorization_id, namespace=selected.namespace,
            revision=selected.revision, authority_bytes=selected.authority_bytes,
            authority_digest=selected.authority_digest, source_pin=selected.source_pin,
            created_at=selected.created_at))
        _check(mappings is not None)
        matching = [entry for entry in mappings.mappings if entry.mapping_id == command['mapping']['id']]
        _check(len(matching) == 1)
        mapping = matching[0]
        _check(command['mapping'] == dict(id=mapping.mapping_id, generation=mapping.generation,
                                         digest=mapping.mapping_digest))
        initial = history.initial_source
        original = history.source_capture
        root = json.loads(history.native_records.capability_history.setup_history.selected.setup_bytes)
        mapping_body = _decoded(json.loads(mapping.mapping_bytes), budget)
        _check(mapping_body['format'] == 'workflow-coding-mapping-record@1')
        roots = _decoded(json.loads(base64.b64decode(mapping_body['authority']['grant_bytes_base64'], validate=True)), budget)['setups']
        _check(root in roots and mapping_body['mapping']['roles']['agent_manager'] == root['manager_selector'])
        bindings = [item for item in mapping_body['mapping']['repository_bindings']
                    if item['binding']['repository_id'] == radius['repository_id']
                    and item['binding']['store_owner'] == radius['store_owner']]
        _check(len(bindings) == 1)
        index = mapping_body['mapping']['repository_bindings'].index(bindings[0])
        _check(mapping_body['adoptions'][index]['object_format'] == radius['object_format'])
        from runtime.workflows.coding_authority_history import _setup_configuration
        snapshot = _decoded(json.loads(base64.b64decode(record['authority']['snapshot_bytes_base64'], validate=True)), budget)
        _setup_configuration(root, snapshot)
        manager = [agent for agent in snapshot['agents'] if agent['name'] == record['issuer']['principal']]
        _check(len(manager) == 1 and bool(manager[0]['executor']))
        binding = bindings[0]['binding']
        origin = manager[0]['repos'].get(binding['repo_key'])
        _check(type(origin) is str and _digest(origin.encode('utf8')) == binding['origin_digest'])
        for earlier in (mapping.created_at, root['created_at'], original.observed_at):
            _check(datetime.fromisoformat(earlier.replace('Z', '+00:00')) <=
                   datetime.fromisoformat(record['created_at'].replace('Z', '+00:00')))
        if record['parent_id'] in rows:
            _check(record['parent_id'] in operations
                   and operations[record['parent_id']][1]._value('id') < audit._value('id')
                   and original.audit_record._value('id') < audit._value('id'))
        for row in (original.task_record, *original.ancestor_records):
            _check(datetime.fromisoformat(row._text('created_at').replace('Z', '+00:00')) <=
                   datetime.fromisoformat(original.observed_at.replace('Z', '+00:00')))
        _check(datetime.fromisoformat(history.native_records.session_start_audit_record._text('timestamp').replace('Z', '+00:00')) <=
               datetime.fromisoformat(original.observed_at.replace('Z', '+00:00')))
        if initial.authorization_id == identity:
            _check(audit._value('id') < original.audit_record._value('id'))
            _check(original.command_bytes == operation['command_bytes']
                   and original.observed_at == record['created_at'])
        # One original capture per initial P; no additional source capture may
        # point to a refinement or substitute its own mint admission.
        _check(original.native_owner.id == initial.authorization_id
               and original.source_admission_id == initial.own_admission.id)
        identities.add(identity)
        admissions.add(selected.own_admission.id)
        histories[identity] = (history, operation, mapping)
        admission = selected.own_admission
        from runtime.workflows._coding_mapping_admission import audit_values
        expected_rows = [('workflow_authorization_revisions', native),
            ('workflow_admission_records', dict(id=admission.id, namespace=admission.namespace,
                generation=admission.generation, request_digest=admission.request_digest, admitted_by=admission.admitted_by)),
            ('audit_log', audit_values(original)),
            ('audit_log', {c.name: audit._text(c.name) if c.storage_class == 'text' else c.value for c in audit.columns})]
        for table, row in expected_rows:
            budget.step()
            key = (type(row['id']), row['id'])
            index = expected.setdefault(table, {})
            _check(key not in index or index[key] == row)
            index[key] = row
    _check(len(admissions) == len(identities))
    for capture in captures.all_captures:
        budget.step()
        if capture.native_owner.table == 'workflow_authorization_revisions':
            _check(capture.phase == 'source' and capture.result_record is None
                   and capture.native_owner.id in identities
                   and histories[capture.native_owner.id][0].initial_source.authorization_id == capture.native_owner.id)
    return histories, expected
