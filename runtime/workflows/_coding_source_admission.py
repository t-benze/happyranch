"""Private D1 source-operation replay and complete cold native composition.

An operation is metadata joining original command bytes to an already genuine
P mint. It grants no access and does not replace or recapture the ORIGINAL source.
All mixed-family work uses the caller's single bounded inventory and budget.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _encode as _reader_encode,
    _json as _reader_json,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


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
        'format org_slug principal authorization_id admission_id session_id command_bytes_base64 command_digest created_at', reader_budget=budget)
    _check(body['format'] == 'workflow-coding-source-operation@1' and body['org_slug'] == org_slug)
    for key in ('authorization_id', 'admission_id', 'session_id'):
        _token(body[key], reader_budget=budget)
    _timestamp(body['created_at'], reader_budget=budget)
    _timestamp(audit._text('timestamp', budget=budget), reader_budget=budget)
    _check(body['principal'] == 'agent:' + audit._text('agent', budget=budget)
           and datetime.fromisoformat(body['created_at'].replace('Z', '+00:00')) <=
               datetime.fromisoformat(audit._text('timestamp', budget=budget).replace('Z', '+00:00')))
    encoded = body['command_bytes_base64']
    _check(type(encoded) is str and len(encoded) <= 4 * ((65536 + 2) // 3))
    raw = _reader_b64decode(encoded, validate=True, budget=budget)
    _check(_reader_decode(_reader_b64encode(raw, budget=budget), 'ascii', budget=budget) == encoded and _digest(raw, budget=budget) == body['command_digest'])
    from runtime.workflows._coding_source_capture import _copy_bytes
    _copy_bytes(raw, budget)
    read_saved_source_command(raw, expected_org_slug=org_slug, budget=budget)
    body = dict(body, command_bytes=raw, command=_decoded(_reader_json(raw, budget=budget), budget))
    return body


def source_storage(conn, org_slug, budget, setups, captures, mappings):
    """Complete positive row expectations; unknown/future families still refuse."""
    expected, histories, identities, admissions = (_reader_reserve(budget, 128) or (_reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or {}, _reader_set(budget=budget), _reader_set(budget=budget)))
    rows = (_reader_reserve(budget, 64) or {})
    prefix = f'org/{org_slug}/coding-capability/'
    for row in _bounded_rows(conn, 'workflow_authorization_revisions', _COLUMNS,
            _reader_tuple(((_reader_reserve(budget, 80) or (kind,)) for kind in _TYPES), budget=budget), budget,
            where='source_pin=? OR substr(namespace,1,?)=?',
            args=(_reader_reserve(budget, 112) or ('workflow-coding-capability@1', len(prefix), prefix)), maximum=1024):
        values = budget.row(row, _TYPES, counted=True, charged=True)
        _check(values[0] not in rows)
        _reader_put(_reader_dict(zip(_COLUMNS, values, strict=True), budget=budget), rows, values[0], budget=budget)
    if not rows and mappings is None:
        return (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or {}))  # Ordinary nonmapping audit/watermark path remains unchanged.
    captures.load()
    operations = (_reader_reserve(budget, 64) or {})
    for operation, audit in captures.operations.values():
        budget.step()
        identity = operation['authorization_id']
        _check(identity in rows and identity not in operations)
        _reader_put((_reader_reserve(budget, 96) or (operation, audit)), operations, identity, budget=budget)
    _check(rows.keys() == operations.keys())
    for identity, native in rows.items():
        budget.step()
        history = _read(conn, org_slug, identity, budget=budget, setups=setups, inventory=captures)
        selected = history.native_records.capability_history.selected
        record = _decoded(_reader_json(selected.authority_bytes, budget=budget), budget)
        # ONLY these nonallocating source producers are supported. K3 and all
        # downstream capability subjects need their own producers/readers.
        _check(record['level'] == 'source' and record['issuer']['result_id'] is None)
        operation, audit = operations[identity]
        command = operation['command']
        _check(operation['principal'] == 'agent:' + record['issuer']['principal']
               and audit._text('task_id', budget=budget) == record['issuer']['task_id']
               and operation['session_id'] == record['issuer']['session_id']
               and operation['admission_id'] == selected.own_admission.id
               and operation['created_at'] == record['created_at']
               and command['parent_id'] == record['parent_id'] and command['action'] == record['action'])
        radius = dict(command['work'], provider_repository=None, pr_number=None,
                      expected_head=None, head_rule='own-development-output@1')
        _check(record['radius'] == radius)
        _check(native == (_reader_reserve(budget, 960) or dict(id=selected.authorization_id, namespace=selected.namespace,
            revision=selected.revision, authority_bytes=selected.authority_bytes,
            authority_digest=selected.authority_digest, source_pin=selected.source_pin,
            created_at=selected.created_at)))
        _check(mappings is not None)
        matching = _reader_list((entry for entry in mappings.mappings if entry.mapping_id == command['mapping']['id']), budget=budget)
        _check(len(matching) == 1)
        mapping = matching[0]
        _check(command['mapping'] == (_reader_reserve(budget, 448) or dict(id=mapping.mapping_id, generation=mapping.generation,
                                         digest=mapping.mapping_digest)))
        initial = history.initial_source
        original = history.source_capture
        root = _reader_json(history.native_records.capability_history.setup_history.selected.setup_bytes, budget=budget)
        mapping_body = _decoded(_reader_json(mapping.mapping_bytes, budget=budget), budget)
        _check(mapping_body['format'] == 'workflow-coding-mapping-record@1')
        roots = _decoded(_reader_json(_reader_b64decode(mapping_body['authority']['grant_bytes_base64'], validate=True, budget=budget), budget=budget), budget)['setups']
        _check(root in roots and mapping_body['mapping']['roles']['agent_manager'] == root['manager_selector'])
        bindings = _reader_list((item for item in mapping_body['mapping']['repository_bindings']
                    if item['binding']['repository_id'] == radius['repository_id']
                    and item['binding']['store_owner'] == radius['store_owner']), budget=budget)
        _check(len(bindings) == 1)
        index = mapping_body['mapping']['repository_bindings'].index(bindings[0])
        _check(mapping_body['adoptions'][index]['object_format'] == radius['object_format'])
        from runtime.workflows.coding_authority_history import _setup_configuration
        snapshot = _decoded(_reader_json(_reader_b64decode(record['authority']['snapshot_bytes_base64'], validate=True, budget=budget), budget=budget), budget)
        _setup_configuration(root, snapshot, budget=budget)
        manager = _reader_list((agent for agent in snapshot['agents'] if agent['name'] == record['issuer']['principal']), budget=budget)
        _check(len(manager) == 1 and bool(manager[0]['executor']))
        binding = bindings[0]['binding']
        origin = manager[0]['repos'].get(binding['repo_key'])
        _check(type(origin) is str and _digest(_reader_encode(origin, 'utf8', budget=budget), budget=budget) == binding['origin_digest'])
        for earlier in (_reader_reserve(budget, 112) or (mapping.created_at, root['created_at'], original.observed_at)):
            _check(datetime.fromisoformat(earlier.replace('Z', '+00:00')) <=
                   datetime.fromisoformat(record['created_at'].replace('Z', '+00:00')))
        if record['parent_id'] in rows:
            _check(record['parent_id'] in operations
                   and operations[record['parent_id']][1]._value('id') < audit._value('id')
                   and original.audit_record._value('id') < audit._value('id'))
        for row in (_reader_reserve(budget, 128 + 32 * (1 + len(original.ancestor_records))) or (original.task_record, *original.ancestor_records)):
            _check(datetime.fromisoformat(row._text('created_at', budget=budget).replace('Z', '+00:00')) <=
                   datetime.fromisoformat(original.observed_at.replace('Z', '+00:00')))
        _check(datetime.fromisoformat(history.native_records.session_start_audit_record._text('timestamp', budget=budget).replace('Z', '+00:00')) <=
               datetime.fromisoformat(original.observed_at.replace('Z', '+00:00')))
        if initial.authorization_id == identity:
            _check(audit._value('id') < original.audit_record._value('id'))
            _check(original.command_bytes == operation['command_bytes']
                   and original.observed_at == record['created_at'])
        # One original capture per initial P; no additional source capture may
        # point to a refinement or substitute its own mint admission.
        _check(original.native_owner.id == initial.authorization_id
               and original.source_admission_id == initial.own_admission.id)
        _reader_add(identities, identity, budget=budget)
        _reader_add(admissions, selected.own_admission.id, budget=budget)
        _reader_put((_reader_reserve(budget, 112) or (history, operation, mapping)), histories, identity, budget=budget)
        admission = selected.own_admission
        from runtime.workflows._coding_mapping_admission import audit_values
        expected_rows = (_reader_reserve(budget, 576) or [(_reader_reserve(budget, 96) or ('workflow_authorization_revisions', native)),
            (_reader_reserve(budget, 96) or ('workflow_admission_records', (_reader_reserve(budget, 704) or dict(id=admission.id, namespace=admission.namespace,
                generation=admission.generation, request_digest=admission.request_digest, admitted_by=admission.admitted_by)))),
            (_reader_reserve(budget, 96) or ('audit_log', audit_values(original, budget=budget))),
            (_reader_reserve(budget, 96) or ('audit_log', _reader_dict_pairs(((_reader_reserve(budget, 96) or (c.name,  audit._text(c.name, budget=budget) if c.storage_class == 'text' else c.value )) for c in audit.columns), budget=budget)))])
        for table, row in expected_rows:
            budget.step()
            key = (_reader_reserve(budget, 96) or (type(row['id']), row['id']))
            index = _reader_setdefault(expected, table, _reader_reserve(budget, 64) or {}, budget=budget)
            _check(key not in index or index[key] == row)
            _reader_put(row, index, key, budget=budget)
    _check(len(admissions) == len(identities))
    for capture in captures.all_captures:
        budget.step()
        if capture.native_owner.table == 'workflow_authorization_revisions':
            _check(capture.phase == 'source' and capture.result_record is None
                   and capture.native_owner.id in identities
                   and histories[capture.native_owner.id][0].initial_source.authorization_id == capture.native_owner.id)
    return (_reader_reserve(budget, 96) or (histories, expected))
