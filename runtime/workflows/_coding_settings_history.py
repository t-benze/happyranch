"""Independent bounded human publication closure, within caller-owned reads."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from runtime.workflows._coding_settings_records import (
    HUMAN_RECORD, SettingsSnapshot, account, captured_configuration, parse_command,
)
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.coding_saved_records import (
    VALUE_LIMIT, _canonical, _check, _closed, _hash, _json, _model,
)

AUDIT_COLUMNS = ('id', 'task_id', 'agent', 'action', 'payload', 'timestamp')
AUDIT_TYPES = ('integer', 'text', 'text', 'text', 'text', 'text')


@dataclass(frozen=True)
class HumanPublication:
    payload: dict
    command_bytes: bytes
    audit_rows: tuple[dict, dict]


def _audit_rows(conn, budget, *, where, args=()):
    for row in _bounded_rows(conn, 'audit_log', AUDIT_COLUMNS,
            tuple((kind,) for kind in AUDIT_TYPES), budget,
            where=where, args=args, maximum=1024):
        values = budget.row(row, AUDIT_TYPES, counted=True, charged=True)
        _check(type(values[0]) is int and values[0] > 0)
        yield dict(zip(AUDIT_COLUMNS, values, strict=True))


def publications(conn, org, budget) -> dict[str, HumanPublication]:
    """Scan both audit families independently, including orphans and bad owners."""
    configs = {}
    for row in _audit_rows(conn, budget, where="action='org_config_write'"):
        budget.step()
        from runtime.workflows.coding_saved_records import _pairs, _constant
        value = account(json.loads(row['payload'], object_pairs_hook=_pairs,
                                  parse_constant=_constant), budget)
        if row['task_id'] != 'config:coding' and not (
                type(value) is dict and value.get('section') == 'coding'):
            continue
        # Existing generic config audit uses ordinary JSON; preserve exact
        # native bytes and reject duplicate members without requiring canonical
        # spacing for the envelope owned by that existing family.
        _closed(value, 'section tiers before after')
        _check(row['task_id'] == 'config:coding' and row['agent'] == 'founder'
               and value['section'] == 'coding' and type(value['after']) is dict)
        after = _model(SettingsSnapshot, value['after']).model_dump()
        _check(after['org_slug'] == org and after['operation_key'] not in configs)
        configs[after['operation_key']] = (row, value)
    result = {}
    keys, admissions = set(), set()
    for row in _audit_rows(conn, budget, where="action='coding_settings_publication'"):
        value = account(_json(row['payload'].encode('utf8'), VALUE_LIMIT), budget)
        _closed(value, 'format org_slug principal operation_key request_digest command_bytes_base64 '
                       'mapping_id mapping_generation mapping_digest admission_id before after')
        _check(value['format'] == 'workflow-coding-settings-publication@1'
               and value['org_slug'] == org and value['principal'] == 'human:founder'
               and row['task_id'] == 'config:coding' and row['agent'] == 'founder')
        _check(type(value['command_bytes_base64']) is str
               and len(value['command_bytes_base64']) <= 4 * ((65536 + 2) // 3))
        raw = base64.b64decode(value['command_bytes_base64'], validate=True)
        _check(len(raw) <= budget.remaining_bytes)
        budget.consumed += len(raw)
        _check(base64.b64encode(raw).decode('ascii') == value['command_bytes_base64'])
        command = account(parse_command(raw, org), budget)
        key = command['operation_key']
        _check(key == value['operation_key'] and value['request_digest'] == _hash(raw)
               and key not in keys and value['mapping_id'] not in result
               and value['admission_id'] not in admissions)
        after = _model(SettingsSnapshot, value['after']).model_dump()
        if value['before'] is not None:
            _model(SettingsSnapshot, value['before'])
        config = configs.pop(key, None)
        _check(config is not None)
        config_row, config_value = config
        before = value['before']
        tiers = sorted(k for k in after if before is None or before[k] != after[k])
        _check(_canonical(config_value) == _canonical(dict(section='coding', tiers=tiers, before=before, after=after))
               and row['timestamp'] == config_row['timestamp'] == after['published_at']
               and config_row['id'] == row['id'] + 1)
        keys.add(key)
        admissions.add(value['admission_id'])
        result[value['mapping_id']] = HumanPublication(value, raw, (row, config_row))
    _check(not configs)
    # New owner uses a dedicated ID prefix. It never borrows a native admission;
    # independently scanning finds admission rows left without a publication.
    actual = set()
    for row in _bounded_rows(conn, 'workflow_admission_records',
            ('id', 'namespace', 'generation', 'request_digest', 'admitted_by'),
            (('text',), ('text',), ('integer',), ('text',), ('text',)), budget,
            where="substr(id,1,25)='coding-settings-admission:'", maximum=1024):
        values = budget.row(row, ('text', 'text', 'integer', 'text', 'text'), counted=True, charged=True)
        _check(values[0] not in actual and values[1] == 'org/' + org
               and values[2] > 0 and values[4] == 'human:founder')
        actual.add(values[0])
    _check(actual == admissions)
    return result


def snapshot_for(values: tuple, record: dict, command: dict, previous: dict | None) -> dict:
    snapshot = dict(format='workflow-coding-settings-snapshot@1', org_slug=values[1],
        generation=command['expected_generation'] + 1, admission=command['admission'],
        release=command['mapping']['release'],
        mapping=dict(id=values[0], generation=values[3], digest=values[8]),
        operation_key=command['operation_key'], request_digest=values[6],
        previous_snapshot_digest=None if previous is None else _hash(_canonical(previous)),
        published_at=values[9], published_by='human:founder')
    _model(SettingsSnapshot, snapshot)
    _check(record['settings'] == dict(generation=snapshot['generation'],
        admission=snapshot['admission'], previous_snapshot_digest=snapshot['previous_snapshot_digest']))
    return snapshot


def human_mapping(values, record, command, previous_mapping, previous_snapshot,
                  selected: HumanPublication, budget) -> dict:
    """Original captured authority versus complete preceding mapping equality."""
    _check(record['format'] == HUMAN_RECORD and selected.command_bytes == _canonical(command)
           and re.fullmatch(r'coding-mapping:[0-9a-f]{32}', values[0]) is not None
           and re.fullmatch(r'coding-settings-admission:[0-9a-f]{32}',
                            record['authority']['admission_record_id']) is not None)
    captured_configuration(record, command, previous_mapping, budget)
    snapshot = account(snapshot_for(values, record, command, previous_snapshot), budget)
    expected = dict(format='workflow-coding-settings-publication@1', org_slug=values[1],
        principal='human:founder', operation_key=values[5], request_digest=values[6],
        command_bytes_base64=record['command_bytes_base64'], mapping_id=values[0],
        mapping_generation=values[3], mapping_digest=values[8],
        admission_id=record['authority']['admission_record_id'], before=previous_snapshot, after=snapshot)
    _check(_canonical(selected.payload) == _canonical(expected))
    _check(snapshot['generation'] == (1 if previous_snapshot is None else previous_snapshot['generation'] + 1))
    return snapshot


def current_snapshot(conn, org, budget, history, captures) -> dict | None:
    """Validate the one current pointer even when no mapping row exists."""
    captures.load()
    expected = None
    if history is not None:
        for entry in history.mappings:
            if type(entry.source_capture) is HumanPublication:
                expected = entry.source_capture.payload['after']
    rows = list(_bounded_rows(conn, 'org_settings',
        ('section', 'value_json', 'updated_at', 'updated_by'),
        (('text',), ('text',), ('text',), ('text',)), budget,
        where="section='coding'", maximum=1))
    _check(len(rows) == (0 if expected is None else 1))
    if rows:
        section, raw, at, by = budget.row(rows[0], ('text',) * 4, counted=True, charged=True)
        actual = account(_json(raw.encode('utf8'), VALUE_LIMIT), budget)
        _model(SettingsSnapshot, actual)
        _check(section == 'coding' and _canonical(actual) == _canonical(expected) and expected['org_slug'] == org
               and at == expected['published_at'] and by == 'founder')
    if history is None:
        _check(not captures.by_mapping)
    return expected
