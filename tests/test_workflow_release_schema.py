"""Whole-DB legacy oracle and unchanged observed-only authority-v2 seam."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover, migrate_draft_schema
from runtime.models import TaskRecord
from runtime.orchestrator import authority
from runtime.orchestrator.authority_policy import ENGINEERING_PRE_ESCALATION_POLICY


def _evidence(db: Database) -> tuple[dict, list[str]]:
    task = TaskRecord(id='TASK-001', brief='oracle', assigned_agent='maker', team='engineering')
    db.insert_task(task)
    orch = SimpleNamespace(_db=db, _slug='alpha', _paths=None)
    return authority._server_evidence(orch, task, 'maker', ENGINEERING_PRE_ESCALATION_POLICY, {})


@pytest.mark.parametrize('layout', ['F', 'E'])
def test_shipping_oracle_selects_independent_complete_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str) -> None:
    monkeypatch.setattr(authority, '_release_schema_digest_cache', None)
    db = Database(tmp_path / 'happyranch.db')
    try:
        install_or_recover(db)
        if layout == 'E':
            with db.workflow_schema_transaction() as conn:
                migrate_draft_schema(conn, expected_org_slug='alpha')
        facts, matched = _evidence(db)
        assert not json.loads(facts['db_schema'])['drift']
        assert 'esc-schema-overloaded-column' not in matched
        assert authority._release_schema_digest(layout) == authority._live_schema_digest(db)
        other = 'E' if layout == 'F' else 'F'
        assert authority._release_schema_digest(other) != authority._live_schema_digest(db)
        assert authority._release_schema_digest(layout) == authority._live_schema_digest(db)
    finally:
        db.close()


@pytest.mark.parametrize('corruption', ['nonworkflow-object', 'partial-extension', 'wrong-marker', 'wrong-history', 'reference-error'])
def test_shipping_oracle_never_relearns_candidate_or_ignores_extra_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str) -> None:
    monkeypatch.setattr(authority, '_release_schema_digest_cache', None)
    db = Database(tmp_path / 'happyranch.db')
    try:
        install_or_recover(db)
        expected = authority._release_schema_digest('F')
        if corruption == 'nonworkflow-object':
            db.execute('CREATE TABLE unrelated_extra (id INTEGER)')
        elif corruption == 'partial-extension':
            db.execute('CREATE TABLE workflow_draft_adapter_versions (version INTEGER PRIMARY KEY CHECK(version=1))')
        elif corruption == 'wrong-marker':
            db.execute('DELETE FROM workflow_adapter_versions')
        elif corruption == 'wrong-history':
            db.execute("UPDATE workflow_cutover_events SET event_digest='forged'")
        else:
            # YES test-only production seam: independent reference constructor
            # failure, never a successful fake reference or candidate baseline.
            monkeypatch.setattr(authority, '_release_schema_digest_cache', None)
            monkeypatch.setattr(Database, '__init__', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('unavailable reference')))
        db._conn.commit()
        facts, matched = _evidence(db)
        assert json.loads(facts['db_schema'])['drift']
        assert 'esc-schema-overloaded-column' in matched
        if corruption != 'reference-error':
            assert authority._release_schema_digest('F') == expected
    finally:
        db.close()


def test_actual_v2_capture_includes_e_and_remains_observed_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        install_or_recover(db)
        before = authority.capture_authority_policy_v2_schema_observation(db)
        with db.workflow_schema_transaction() as conn:
            migrate_draft_schema(conn, expected_org_slug='alpha')
        def reference_forbidden(*args, **kwargs):
            raise AssertionError('v2 must never construct or compare a release reference')
        monkeypatch.setattr(authority, '_release_schema_digest', reference_forbidden)
        observed = authority.capture_authority_policy_v2_schema_observation(db)
        assert observed.object_count > before.object_count
        assert observed.raw_digest != before.raw_digest
        assert observed.inventory_digest != before.inventory_digest
        db.execute('CREATE TABLE unrelated_observed (id INTEGER)')
        db._conn.commit()
        extra = authority.capture_authority_policy_v2_schema_observation(db)
        assert extra.object_count == observed.object_count + 1
    finally:
        db.close()
