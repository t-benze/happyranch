"""Private version-selected execution predicates over local coding inventories.

No storage, transaction, readiness or authority owner lives here. Callers must
reconstruct mappings/imports in their own snapshot; no result is cached.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _put as _reader_put,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


from typing import Any

from runtime.workflows.coding_saved_records import _canonical, _check


def _read_json(raw: bytes, budget: Any, limit: int = 1048576) -> Any:
    """Reserve bounded decoded/canonical copies BEFORE nested JSON expansion."""
    from runtime.workflows.coding_authority_history import _decoded
    from runtime.workflows.coding_saved_records import _json
    _check(type(raw) is bytes and 0 < len(raw) <= limit)
    charge = 4 * len(raw)
    _reader_reserve(budget, charge)
    return _decoded(_json(raw, limit, reader_budget=budget), budget)


def _read_authority(value: dict, org: str, budget: Any) -> tuple[bytes, dict]:
    """Charge nested captures to the SAME allowance; retain original predicates."""
    import base64
    from runtime.workflows._coding_settings_records import account
    from runtime.workflows.coding_saved_records import _Ancestry, _capture, _closed
    _closed(value, 'pin admission_record_id snapshot_bytes_base64 profile_evidence '
                   'grant_bytes_base64 grant_digest', reader_budget=budget)
    account(value, budget)
    decoded = (_reader_reserve(budget, 64) or {})
    for field in ('snapshot_bytes_base64', 'grant_bytes_base64'):
        text = value[field]
        _check(type(text) is str and len(text) <= 4 * ((1048576 + 2) // 3))
        # Includes base64/UTF8 copies made by the original pure capture reader.
        charge = 4 * len(text)
        _reader_reserve(budget, charge)
        _reader_put(_read_json(_reader_b64decode(text, validate=True, budget=budget), budget), decoded, field, budget=budget)
    grant, _ = _capture(value, org, _Ancestry(), reader_budget=budget)
    return (_reader_reserve(budget, 96) or (grant, decoded['snapshot_bytes_base64']))


def _settings_family(command: dict) -> bool:
    tag = command['format']
    _check(tag in ('workflow-coding-start@1', 'workflow-coding-start@2'))
    _check(('settings' in command) == (tag == 'workflow-coding-start@2'))
    return tag == 'workflow-coding-start@2'


def _configuration(mapping: dict, snapshot: dict, command: dict, budget: Any) -> None:
    if _settings_family(command):
        from runtime.workflows._coding_settings_records import human_configuration
        human_configuration(mapping, snapshot, budget)
    else:
        from runtime.workflows._coding_role_records import configuration
        configuration(mapping, snapshot, budget=budget)


def _current_configuration(mapping: dict, snapshot: dict, command: dict, budget: Any) -> None:
    from runtime.workflows.coding_start_records import effective_mapping
    from runtime.workflows._coding_settings_records import account
    account(snapshot, budget)
    account(mapping, budget)
    # Unselected defaults/replacements remain historical configuration, not
    # dependencies of this admitted run. Old native predicates stay literal.
    if not _settings_family(command):
        _configuration(mapping, snapshot, command, budget)
    _configuration(effective_mapping(mapping, command, budget=budget), snapshot, command, budget)


def _fresh_settings(state: Any, command: dict, *, budget=None) -> None:
    _check(_settings_family(command))
    pin = dict(**command['settings'], mapping=command['mapping'])
    _check(state.pinned is not None and state.pinned['admission'] == 'explicit'
           and state.pinned['release'] == command['release']
           and state.current['admission'] == 'explicit'
           and _canonical(pin, reader_budget=budget) == _canonical(_reader_dict_pairs(((_reader_reserve(budget, 96) or (key,  state.current[key] )) for key in pin), budget=budget), reader_budget=budget))


def _resolve_start(command: dict, mappings: Any, imported: Any, budget: Any, *,
                   fresh: bool = False) -> Any:
    """Resolve pins from the enclosing independently reconstructed history.

    Current pointer validity is established by the complete mapping reader,
    including for old starts. Continuations select their original publication;
    they do not require today's admission or defaults to equal those pins.
    """
    from runtime.workflows._coding_settings_records import HUMAN_RECORD
    from runtime.workflows._coding_role_records import RECORD_FORMAT
    from runtime.workflows.coding_settings import _resolve_history
    from runtime.workflows.coding_saved_records import _json

    new = _settings_family(command)
    _check(mappings is not None and imported is not None)
    entries = _reader_list((entry for entry in mappings.mappings if entry.mapping_id == command['mapping']['id']), budget=budget)
    _check(len(entries) == 1)
    entry = entries[0]
    _check(_canonical(command['mapping'], reader_budget=budget) == _canonical((_reader_reserve(budget, 448) or dict(
        id=entry.mapping_id, generation=entry.generation, digest=entry.mapping_digest)), reader_budget=budget))
    from runtime.workflows._coding_settings_records import account
    record = account(_read_json(entry.mapping_bytes, budget, 1048576), budget)
    _check(record['format'] == (HUMAN_RECORD if new else RECORD_FORMAT))
    pin = dict(**command['settings'], mapping=command['mapping']) if new else None
    state = _resolve_history(mappings, imported, budget, pinned=pin)
    if new:
        _check(state.pinned is not None and state.pinned['admission'] == 'explicit'
               and state.pinned['release'] == command['release'] == record['mapping']['release'])
    if fresh:
        _fresh_settings(state, command, budget=budget)
    return state


def _native_format(command: dict, stage: str) -> str:
    if stage == 'development':
        return ('workflow-coding-native-admission@2' if _settings_family(command)
                else 'workflow-coding-native-admission@1')
    _settings_family(command)
    return {'code_review': 'workflow-coding-review-admission@1',
            'qa': 'workflow-coding-qa-admission@1',
            'manager_closeout': 'workflow-coding-manager-admission@1'}[stage]
