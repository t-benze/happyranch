"""Settings projections from a freshly authenticated local complete inventory."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
)

from runtime.infrastructure._workflow_reader import (
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _dump as _reader_dump,
    _json as _reader_json,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


import json
from dataclasses import dataclass

from runtime.workflows._coding_settings_history import HumanPublication
from runtime.workflows._coding_settings_records import (
    EffectiveSettings, SettingsReceipt, account,
)
from runtime.workflows.coding_saved_records import _canonical, _check, _closed, _hash, _model


@dataclass(frozen=True)
class SettingsResolution:
    current: dict
    configuration: dict | None
    publications: tuple
    pinned: dict | None = None


def project(history, budget, *, pinned=None) -> SettingsResolution:
    """Private same-snapshot projection, never a caller-supplied after-image."""
    _reader_work((_reader_reserve(budget, 96) or (history, pinned)), budget)
    entries = () if history is None else history.mappings
    human = _reader_tuple((e for e in entries if type(e.source_capture) is HumanPublication), budget=budget)
    current = (_reader_reserve(budget, 832) or dict(generation=0, admission='disabled', release=None, mapping=None,
                   snapshot_digest=None, mapping_generation=0 if not entries else entries[-1].generation))
    configuration = None
    if human:
        latest = human[-1]
        snapshot = latest.source_capture.payload['after']
        current = (_reader_reserve(budget, 832) or dict(generation=snapshot['generation'], admission=snapshot['admission'],
            release=snapshot['release'], mapping=snapshot['mapping'],
            snapshot_digest=_hash(_canonical(snapshot, reader_budget=budget), reader_budget=budget), mapping_generation=latest.generation))
        configuration = account(_reader_json(latest.mapping_bytes, budget=budget)['mapping'], budget)
    selected = None
    if pinned is not None:
        _reader_work(pinned, budget)
        _closed(pinned, 'generation snapshot_digest mapping', reader_budget=budget)
        # Strict field equality, including bool-versus-int and complete pin shape.
        matches = (_reader_reserve(budget, 64) or [])
        for entry in human:
            budget.step()
            snapshot = entry.source_capture.payload['after']
            candidate = (_reader_reserve(budget, 448) or dict(generation=snapshot['generation'],
                snapshot_digest=_hash(_canonical(snapshot, reader_budget=budget), reader_budget=budget), mapping=snapshot['mapping']))
            if _canonical(candidate, reader_budget=budget) == _canonical(pinned, reader_budget=budget):
                _reader_append(matches, snapshot, budget=budget)
        _check(len(matches) == 1)
        selected = account(matches[0], budget)
    return SettingsResolution(_reader_dump(_model(EffectiveSettings, current, reader_budget=budget), budget=budget),
                              configuration, human, selected)


def receipt(entry, *, replayed: bool) -> dict:
    publication = entry.source_capture
    snapshot = publication.payload['after']
    return _model(SettingsReceipt, dict(format='workflow-coding-settings-receipt@1',
        operation_key=entry.operation_key, request_digest=entry.request_digest,
        snapshot=snapshot, snapshot_digest=_hash(_canonical(snapshot)),
        publication_audit_id=publication.audit_rows[0]['id'], replayed=replayed)).model_dump()
