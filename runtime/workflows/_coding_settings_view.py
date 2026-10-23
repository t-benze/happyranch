"""Settings projections from a freshly authenticated local complete inventory."""
from __future__ import annotations

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
    entries = () if history is None else history.mappings
    human = tuple(e for e in entries if type(e.source_capture) is HumanPublication)
    current = dict(generation=0, admission='disabled', release=None, mapping=None,
                   snapshot_digest=None, mapping_generation=0 if not entries else entries[-1].generation)
    configuration = None
    if human:
        latest = human[-1]
        snapshot = latest.source_capture.payload['after']
        current = dict(generation=snapshot['generation'], admission=snapshot['admission'],
            release=snapshot['release'], mapping=snapshot['mapping'],
            snapshot_digest=_hash(_canonical(snapshot)), mapping_generation=latest.generation)
        configuration = account(json.loads(latest.mapping_bytes)['mapping'], budget)
    selected = None
    if pinned is not None:
        _closed(pinned, 'generation snapshot_digest mapping')
        # Strict field equality, including bool-versus-int and complete pin shape.
        matches = []
        for entry in human:
            budget.step()
            snapshot = entry.source_capture.payload['after']
            candidate = dict(generation=snapshot['generation'],
                snapshot_digest=_hash(_canonical(snapshot)), mapping=snapshot['mapping'])
            if _canonical(candidate) == _canonical(pinned):
                matches.append(snapshot)
        _check(len(matches) == 1)
        selected = account(matches[0], budget)
    return SettingsResolution(_model(EffectiveSettings, current).model_dump(),
                              configuration, human, selected)


def receipt(entry, *, replayed: bool) -> dict:
    publication = entry.source_capture
    snapshot = publication.payload['after']
    return _model(SettingsReceipt, dict(format='workflow-coding-settings-receipt@1',
        operation_key=entry.operation_key, request_digest=entry.request_digest,
        snapshot=snapshot, snapshot_digest=_hash(_canonical(snapshot)),
        publication_audit_id=publication.audit_rows[0]['id'], replayed=replayed)).model_dump()
