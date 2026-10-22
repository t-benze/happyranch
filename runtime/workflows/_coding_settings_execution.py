"""Private version-selected execution predicates over local coding inventories.

No storage, transaction, readiness or authority owner lives here. Callers must
reconstruct mappings/imports in their own snapshot; no result is cached.
"""
from __future__ import annotations

from typing import Any

from runtime.workflows.coding_saved_records import _canonical, _check


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
        configuration(mapping, snapshot)


def _current_configuration(mapping: dict, snapshot: dict, command: dict, budget: Any) -> None:
    from runtime.workflows.coding_start_records import effective_mapping
    # Unselected defaults/replacements remain historical configuration, not
    # dependencies of this admitted run. Old native predicates stay literal.
    if not _settings_family(command):
        _configuration(mapping, snapshot, command, budget)
    _configuration(effective_mapping(mapping, command), snapshot, command, budget)


def _fresh_settings(state: Any, command: dict) -> None:
    _check(_settings_family(command))
    pin = dict(**command['settings'], mapping=command['mapping'])
    _check(state.pinned is not None and state.pinned['admission'] == 'explicit'
           and state.pinned['release'] == command['release']
           and state.current['admission'] == 'explicit'
           and _canonical(pin) == _canonical({key: state.current[key] for key in pin}))


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
    entries = [entry for entry in mappings.mappings if entry.mapping_id == command['mapping']['id']]
    _check(len(entries) == 1)
    entry = entries[0]
    _check(_canonical(command['mapping']) == _canonical(dict(
        id=entry.mapping_id, generation=entry.generation, digest=entry.mapping_digest)))
    from runtime.workflows._coding_settings_records import account
    record = account(_json(entry.mapping_bytes, 1048576), budget)
    _check(record['format'] == (HUMAN_RECORD if new else RECORD_FORMAT))
    pin = dict(**command['settings'], mapping=command['mapping']) if new else None
    state = _resolve_history(mappings, imported, budget, pinned=pin)
    if new:
        _check(state.pinned is not None and state.pinned['admission'] == 'explicit'
               and state.pinned['release'] == command['release'] == record['mapping']['release'])
    if fresh:
        _fresh_settings(state, command)
    return state


def _native_format(command: dict, stage: str) -> str:
    if stage == 'development':
        return ('workflow-coding-native-admission@2' if _settings_family(command)
                else 'workflow-coding-native-admission@1')
    _settings_family(command)
    return {'code_review': 'workflow-coding-review-admission@1',
            'qa': 'workflow-coding-qa-admission@1',
            'manager_closeout': 'workflow-coding-manager-admission@1'}[stage]
