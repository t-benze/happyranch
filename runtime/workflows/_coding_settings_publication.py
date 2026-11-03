"""Synchronous uncommitted append, owned only by the settings admission writer."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone

from runtime.workflows._coding_import_support import _capture
from runtime.workflows._coding_settings_records import (
    HUMAN_RECORD, account, human_configuration, read_human_record,
)
from runtime.workflows._coding_settings_history import snapshot_for
from runtime.workflows.coding_saved_records import VALUE_LIMIT, _canonical, _hash


def append(store, conn, principal, capture, command, raw, state, inventory):
    from runtime.workflows.coding_settings import CodingSettingsError
    budget = inventory.budget
    current = state.current
    if (command['expected_generation'] != current['generation']
            or command['expected_mapping_generation'] != current['mapping_generation']):
        raise CodingSettingsError('coding_settings_generation_stale', current=current)
    if max(current['generation'], current['mapping_generation']) == 9223372036854775807:
        raise CodingSettingsError('coding_settings_generation_stale', current=current)
    if inventory.release is None:
        raise CodingSettingsError('coding_settings_release_required', current=current)
    previous_mapping = (None if inventory.mappings is None else
        account(json.loads(inventory.mappings.selected.mapping_bytes)['mapping'], budget))
    store._principal(principal)
    authority = store.org.workflow_authority
    authority._validate_admission_uncommitted(conn, capture)
    mapping = command['mapping']
    # A pure disable may retain an authenticated complete historical Mapping.
    # It still needs current human publication authority, but no revoked actor
    # becomes a new executable principal or acquires a repository grant.
    if not (command['admission'] == 'disabled' and mapping == previous_mapping):
        if not store.org.workflow_coding_builtin.available(inventory.release):
            raise CodingSettingsError('coding_settings_release_required', current=current)
        pin = dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                   snapshot_digest=capture.ready.snapshot_digest)
        if mapping['authority'] != pin:
            raise CodingSettingsError('coding_settings_authority_unavailable', current=current)
        try:
            human_configuration(mapping, account(json.loads(capture.ready.snapshot_bytes), budget), budget)
        except ValueError:
            raise CodingSettingsError('coding_settings_eligibility_revoked', current=current) from None
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    identity = 'coding-mapping:' + uuid.uuid4().hex
    admission = 'coding-settings-admission:' + uuid.uuid4().hex
    previous = None if not state.publications else state.publications[-1].source_capture.payload['after']
    if inventory.mappings is not None and now < inventory.mappings.selected.created_at:
        raise CodingSettingsError('coding_settings_publication_uncertain', operation_key=command['operation_key'])
    envelope = _capture(capture, admission, raw)
    record = dict(format=HUMAN_RECORD, org_slug=store.org.slug,
        command_bytes_base64=base64.b64encode(raw).decode('ascii'), command_digest=_hash(raw),
        issuer=dict(kind='human', principal='founder', proof_kind='founder_bearer'),
        authority=envelope, mapping=mapping, created_at=now,
        settings=dict(generation=current['generation'] + 1, admission=command['admission'],
            previous_snapshot_digest=current['snapshot_digest']))
    record = account(record, budget)
    encoded = _canonical(record)
    if len(encoded) > VALUE_LIMIT:
        raise CodingSettingsError('coding_settings_invalid_request')
    read_human_record(encoded, expected_org_slug=store.org.slug, budget=budget)
    values = (identity, store.org.slug, 'release/happyranch/coding', current['mapping_generation'] + 1,
              'human:founder', command['operation_key'], _hash(raw), encoded, _hash(encoded), now)
    after = snapshot_for(values, record, command, previous)
    publication = dict(format='workflow-coding-settings-publication@1', org_slug=store.org.slug,
        principal='human:founder', operation_key=command['operation_key'], request_digest=_hash(raw),
        command_bytes_base64=record['command_bytes_base64'], mapping_id=identity,
        mapping_generation=values[3], mapping_digest=values[8], admission_id=admission,
        before=previous, after=after)
    tiers = sorted(k for k in after if previous is None or previous[k] != after[k])
    config = dict(section='coding', tiers=tiers, before=previous, after=after)
    pin = envelope['pin']
    conn.execute('INSERT INTO workflow_admission_records '
        '(id,namespace,generation,request_digest,admitted_by) VALUES (?,?,?,?,?)',
        (admission, pin['namespace'], pin['generation'], _hash(raw), 'human:founder'))
    conn.execute('INSERT INTO workflow_coding_mappings '
        '(id,org_slug,release_identity,generation,principal,operation_key,request_digest,'
        'mapping_bytes,mapping_digest,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', values)
    for action, payload in (('coding_settings_publication', publication), ('org_config_write', config)):
        encoded_payload = _canonical(account(payload, budget)).decode('utf8')
        conn.execute('INSERT INTO audit_log (task_id,agent,action,payload,timestamp) VALUES (?,?,?,?,?)',
                     ('config:coding', 'founder', action, encoded_payload, now))
    encoded_snapshot = _canonical(account(after, budget)).decode('utf8')
    conn.execute('INSERT INTO org_settings(section,value_json,updated_at,updated_by) VALUES (?,?,?,?) '
        'ON CONFLICT(section) DO UPDATE SET value_json=excluded.value_json,'
        'updated_at=excluded.updated_at,updated_by=excluded.updated_by',
        ('coding', encoded_snapshot, now, 'founder'))
    store._principal(principal)
    authority._validate_admission_uncommitted(conn, capture)
