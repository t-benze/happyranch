"""Independent bounded human publication closure, within caller-owned reads."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _put as _reader_put,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import _raw_rows as _reader_raw_rows

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _dump as _reader_dump,
    _encode as _reader_encode,
    _json as _reader_json,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


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


def _coding_config_candidate(task_id, raw, *, budget=None):
    """Top-level coding section/scope, including duplicate and truncated members.

    Other sections may legitimately contain arbitrary nested section values.
    This lexer only selects; the original closed publication reader validates.
    """
    _reader_work((_reader_reserve(budget, 96) or (task_id, raw)), budget)
    if task_id == 'config:coding':
        return True
    if not isinstance(raw, (_reader_reserve(budget, 96) or (str, bytes))):
        return False
    from runtime.infrastructure.workflow_coding_schema import _metadata_string
    text = _reader_decode(raw, json.detect_encoding(raw), errors='replace', budget=budget) if isinstance(raw, bytes) else raw
    index, depth = 0, 0
    while index < len(text):
        char = text[index]
        if char == '"':
            key, index, complete = _metadata_string(text, index, budget)
            if depth != 1 or not complete or key != 'section':
                continue
            following = index
            while following < len(text) and text[following] in ' \t\r\n':
                following += 1
            if following == len(text) or text[following] != ':':
                continue
            following += 1
            while following < len(text) and text[following] in ' \t\r\n':
                following += 1
            if following < len(text) and text[following] == '"':
                value, index, complete = _metadata_string(text, following, budget)
                if value == 'coding' or (not complete and 'coding'.startswith(value)):
                    return True
            continue
        if char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
        index += 1
    return False


def _audit_rows(conn, budget, *, where, args=(), configs=False):
    if configs:
        candidates = (rowid for rowid, task_id, raw in _reader_raw_rows(conn,
            'SELECT rowid,task_id,payload FROM audit_log WHERE ' + where, args, budget=budget)
            if _coding_config_candidate(task_id, raw, budget=budget))
        selections = ((_reader_reserve(budget, 96) or ('rowid=?', (_reader_reserve(budget, 80) or (rowid,)))) for rowid in candidates)
    else:
        selections = (_reader_reserve(budget, 80) or ((_reader_reserve(budget, 96) or (where, args)),))
    count = 0
    for selected, parameters in selections:
        for row in _bounded_rows(conn, 'audit_log', AUDIT_COLUMNS,
                _reader_tuple(((_reader_reserve(budget, 80) or (kind,)) for kind in AUDIT_TYPES), budget=budget), budget,
                where=selected, args=parameters, maximum=1024 - count):
            count += 1
            values = budget.row(row, AUDIT_TYPES, counted=True, charged=True)
            _check(type(values[0]) is int and values[0] > 0)
            _reader_work(values, budget)
            yield _reader_dict(zip(AUDIT_COLUMNS, values, strict=True), budget=budget)


def publications(conn, org, budget) -> dict[str, HumanPublication]:
    """Scan both audit families independently, including orphans and bad owners."""
    configs = (_reader_reserve(budget, 64) or {})
    for row in _audit_rows(conn, budget, where="action='org_config_write'", configs=True):
        budget.step()
        from runtime.workflows.coding_saved_records import _pairs, _constant
        value = account(_reader_json(row['payload'], object_pairs_hook=_pairs,
                                  parse_constant=_constant, budget=budget), budget)
        if row['task_id'] != 'config:coding' and not (
                type(value) is dict and value.get('section') == 'coding'):
            continue
        # Existing generic config audit uses ordinary JSON; preserve exact
        # native bytes and reject duplicate members without requiring canonical
        # spacing for the envelope owned by that existing family.
        _closed(value, 'section tiers before after', reader_budget=budget)
        _check(row['task_id'] == 'config:coding' and row['agent'] == 'founder'
               and value['section'] == 'coding' and type(value['after']) is dict)
        after = _reader_dump(_model(SettingsSnapshot, value['after'], reader_budget=budget), budget=budget)
        _check(after['org_slug'] == org and after['operation_key'] not in configs)
        _reader_work((_reader_reserve(budget, 96) or (row, value)), budget)
        _reader_put((_reader_reserve(budget, 96) or (row, value)), configs, after['operation_key'], budget=budget)
    result = (_reader_reserve(budget, 64) or {})
    keys, admissions = (_reader_reserve(budget, 96) or (_reader_set(budget=budget), _reader_set(budget=budget)))
    for row in _audit_rows(conn, budget, where="action='coding_settings_publication'"):
        value = account(_json(_reader_encode(row['payload'], 'utf8', budget=budget), VALUE_LIMIT, reader_budget=budget), budget)
        _closed(value, 'format org_slug principal operation_key request_digest command_bytes_base64 '
                       'mapping_id mapping_generation mapping_digest admission_id before after', reader_budget=budget)
        _check(value['format'] == 'workflow-coding-settings-publication@1'
               and value['org_slug'] == org and value['principal'] == 'human:founder'
               and row['task_id'] == 'config:coding' and row['agent'] == 'founder')
        _check(type(value['command_bytes_base64']) is str
               and len(value['command_bytes_base64']) <= 4 * ((65536 + 2) // 3))
        raw = _reader_b64decode(value['command_bytes_base64'], validate=True, budget=budget)
        _reader_reserve(budget, len(raw))
        _check(_reader_decode(_reader_b64encode(raw, budget=budget), 'ascii', budget=budget) == value['command_bytes_base64'])
        command = account(parse_command(raw, org, budget=budget), budget)
        key = command['operation_key']
        _check(key == value['operation_key'] and value['request_digest'] == _hash(raw, reader_budget=budget)
               and key not in keys and value['mapping_id'] not in result
               and value['admission_id'] not in admissions)
        after = _reader_dump(_model(SettingsSnapshot, value['after'], reader_budget=budget), budget=budget)
        if value['before'] is not None:
            _model(SettingsSnapshot, value['before'], reader_budget=budget)
        config = configs.pop(key, None)
        _check(config is not None)
        config_row, config_value = config
        before = value['before']
        tiers = _reader_sorted((k for k in after if before is None or before[k] != after[k]), budget=budget)
        _check(_canonical(config_value, reader_budget=budget) == _canonical((_reader_reserve(budget, 576) or dict(section='coding', tiers=tiers, before=before, after=after)), reader_budget=budget)
               and row['timestamp'] == config_row['timestamp'] == after['published_at']
               and config_row['id'] == row['id'] + 1)
        _reader_add(keys, key, budget=budget)
        _reader_add(admissions, value['admission_id'], budget=budget)
        _reader_work((_reader_reserve(budget, 128) or (value, raw, row, config_row)), budget)
        _reader_put(HumanPublication(value, raw, (_reader_reserve(budget, 96) or (row, config_row))), result, value['mapping_id'], budget=budget)
    _check(not configs)
    # New owner uses a dedicated ID prefix. It never borrows a native admission;
    # independently scanning finds admission rows left without a publication.
    actual = _reader_set(budget=budget)
    for row in _bounded_rows(conn, 'workflow_admission_records',
            ('id', 'namespace', 'generation', 'request_digest', 'admitted_by'),
            (('text',), ('text',), ('integer',), ('text',), ('text',)), budget,
            where="substr(id,1,26)='coding-settings-admission:'", maximum=1024):
        values = budget.row(row, ('text', 'text', 'integer', 'text', 'text'), counted=True, charged=True)
        _check(values[0] not in actual and values[1] == 'org/' + org
               and values[2] > 0 and values[4] == 'human:founder')
        _reader_add(actual, values[0], budget=budget)
    _check(actual == admissions)
    return result


def snapshot_for(values: tuple, record: dict, command: dict, previous: dict | None, *, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 128) or (values, record, command, previous)), budget)
    snapshot = (_reader_reserve(budget, 1472) or dict(format='workflow-coding-settings-snapshot@1', org_slug=values[1],
        generation=command['expected_generation'] + 1, admission=command['admission'],
        release=command['mapping']['release'],
        mapping=(_reader_reserve(budget, 448) or dict(id=values[0], generation=values[3], digest=values[8])),
        operation_key=command['operation_key'], request_digest=values[6],
        previous_snapshot_digest=None if previous is None else _hash(_canonical(previous, reader_budget=budget), reader_budget=budget),
        published_at=values[9], published_by='human:founder'))
    _model(SettingsSnapshot, snapshot, reader_budget=budget)
    _check(record['settings'] == (_reader_reserve(budget, 448) or dict(generation=snapshot['generation'],
        admission=snapshot['admission'], previous_snapshot_digest=snapshot['previous_snapshot_digest'])))
    return snapshot


def human_mapping(values, record, command, previous_mapping, previous_snapshot,
                  selected: HumanPublication, budget) -> dict:
    """Original captured authority versus complete preceding mapping equality."""
    _reader_work((_reader_reserve(budget, 160) or (values, record, command, previous_mapping, previous_snapshot, selected)), budget)
    _check(record['format'] == HUMAN_RECORD and selected.command_bytes == _canonical(command, reader_budget=budget)
           and re.fullmatch(r'coding-mapping:[0-9a-f]{32}', values[0]) is not None
           and re.fullmatch(r'coding-settings-admission:[0-9a-f]{32}',
                            record['authority']['admission_record_id']) is not None)
    captured_configuration(record, command, previous_mapping, budget)
    snapshot = account(snapshot_for(values, record, command, previous_snapshot, budget=budget), budget)
    expected = (_reader_reserve(budget, 1600) or dict(format='workflow-coding-settings-publication@1', org_slug=values[1],
        principal='human:founder', operation_key=values[5], request_digest=values[6],
        command_bytes_base64=record['command_bytes_base64'], mapping_id=values[0],
        mapping_generation=values[3], mapping_digest=values[8],
        admission_id=record['authority']['admission_record_id'], before=previous_snapshot, after=snapshot))
    _check(_canonical(selected.payload, reader_budget=budget) == _canonical(expected, reader_budget=budget))
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
    rows = _reader_list(_bounded_rows(conn, 'org_settings',
        ('section', 'value_json', 'updated_at', 'updated_by'),
        (('text',), ('text',), ('text',), ('text',)), budget,
        where="section='coding'", maximum=1), budget=budget)
    _check(len(rows) == (0 if expected is None else 1))
    if rows:
        section, raw, at, by = budget.row(rows[0], ('text',) * 4, counted=True, charged=True)
        actual = account(_json(_reader_encode(raw, 'utf8', budget=budget), VALUE_LIMIT, reader_budget=budget), budget)
        _model(SettingsSnapshot, actual, reader_budget=budget)
        _check(section == 'coding' and _canonical(actual, reader_budget=budget) == _canonical(expected, reader_budget=budget) and expected['org_slug'] == org
               and at == expected['published_at'] and by == 'founder')
    if history is None:
        _check(not captures.by_mapping)
    return expected
