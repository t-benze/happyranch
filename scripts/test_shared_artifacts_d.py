"""Slice D finite private SQLite/filesystem checks; never starts a daemon.

Run: uv run --frozen python scripts/test_shared_artifacts_d.py [--case CASE]
This standalone venue is outside retired Python units and required CI checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.daemon.artifact_legacy_import import run_import
from runtime.infrastructure.artifact_store import ArtifactStore
from runtime.infrastructure.database import Database
from runtime.infrastructure.db.shared_artifacts import SHARED_ARTIFACT_SCHEMA
from runtime.infrastructure.org_access_fence import OrgAccessFence
from runtime.infrastructure.shared_artifact_inventory import (
    ACCEPTANCE_PREFIX, Inventory, Review, approval_digest, canonical, digest, read_document,
)
from runtime.infrastructure.shared_artifact_references import install_reference_seams
from runtime.infrastructure.shared_artifact_service import SharedArtifactService
from runtime.models import TaskRecord, TaskStatus
from runtime.runtime import RuntimeDir

NOW = datetime.now(timezone.utc) + timedelta(minutes=1)


@contextmanager
def fixture(root: Path, names: tuple[str, ...] = ('source.bin',)) -> Iterator[object]:
    home = root / 'daemon-home'
    home.mkdir(parents=True, mode=0o700)
    runtime = RuntimeDir.init(root / 'runtime')
    org_root = runtime.orgs_dir / 'alpha'
    (org_root / 'org' / 'agents').mkdir(parents=True)
    (org_root / 'org' / 'config.yaml').write_text('reviewer_agents: [dev_agent]\nshared_artifacts:\n  cleanup_mode: enforce\n')
    (org_root / 'org' / 'teams.yaml').write_text(
        'teams:\n  engineering:\n    manager: engineering_manager\n    workers: [dev_agent]\n')
    for name, role in (('dev_agent', 'worker'), ('engineering_manager', 'manager')):
        (org_root / 'org' / 'agents' / (name + '.md')).write_text(
            f'---\nname: {name}\nteam: engineering\nrole: {role}\nexecutor: codex\n---\nSynthetic actor.\n')
    store = ArtifactStore(org_root / 'artifacts')
    # Old bytes precede the new metadata schema/service.
    for name in names:
        store.put(name, ('synthetic legacy ' + name).encode())
    db = Database(org_root / 'happyranch.db')
    org = SimpleNamespace(root=org_root, slug='alpha', db=db, runtime=runtime.root,
                          home=home, manager_ids={'engineering_manager'},
                          reference_origin='http://127.0.0.1:8765')
    svc = SharedArtifactService(db, store, known_agents={'dev_agent', 'engineering_manager'}, clock=lambda: NOW)
    install_reference_seams(org, origin=org.reference_origin)
    db.insert_task(TaskRecord(id='TASK-18706', assigned_agent='dev_agent', status=TaskStatus.COMPLETED,
                              brief='Synthetic closed inventory origin'))
    with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
        try:
            yield org
        finally:
            org.db.close()


def item(org: object, name: str = 'source.bin', classification: str = 'default', **changes: object) -> dict:
    observed = org.db._shared_artifact_service.identity(org.root / 'artifacts' / name)
    return dict(dict(name=name, sha256=observed['sha256'], size_bytes=observed['size_bytes'],
        dependency_evidence=['Synthetic exact task/thread/source scan; rechecked by importer'],
        owner_agent_id='dev_agent', manager_agent_id='engineering_manager', origin_task_id='TASK-18706',
        origin_thread_id=None, purpose='Reusable downloadable synthetic design', classification=classification,
        reviewed_expires_at=None if classification in ('retire', 'undecided') else (NOW + timedelta(days=10)).isoformat(),
        reference_reason='Synthetic design reference' if classification == 'reference' else None), **changes)


def documents(org: object, items: list[dict]) -> tuple[Inventory, Review]:
    inventory = Inventory.model_validate(dict(version=1, org=org.slug, source_task_id='TASK-18706', items=items))
    review = Review.model_validate(dict(version=1, org=org.slug, inventory_digest=digest(inventory),
        decisions=[dict(name=i.name, classification=i.classification) for i in inventory.items],
        acceptance=dict(task_id='TASK-90001', result_id=1)))
    return inventory, review


def accept(org: object, review: Review, *, agent: str = 'engineering_manager', marker: str | None = None) -> Review:
    task_id = review.acceptance.task_id
    if org.db.get_task(task_id) is None:
        org.db.insert_task(TaskRecord(id=task_id, assigned_agent=agent, status=TaskStatus.COMPLETED,
            current_session_id='synthetic-manager-session', brief='Synthetic reviewed classification'))
    org.db.insert_task_result(task_id=task_id, agent=agent, session_id='synthetic-manager-session',
        output_summary=marker if marker is not None else ACCEPTANCE_PREFIX + approval_digest(review), confidence_score=90)
    result_id = org.db._conn.execute('SELECT MAX(id) FROM task_results').fetchone()[0]
    value = review.model_dump()
    value['acceptance']['result_id'] = result_id
    return Review.model_validate(value)


def gates(org: object, *, days: int = 8, accepted: bool = True, sources: bool = True) -> None:
    with org.db.shared_artifact_transaction() as conn:
        conn.execute('UPDATE shared_artifact_org_state SET rollout_started_at=?,preview_acceptance=?,source_protection_verified=? WHERE id=1',
            ((NOW - timedelta(days=days)).isoformat(), 'synthetic-preview-acceptance' if accepted else None, int(sources)))


def invoke(org: object, inventory: Inventory, review: Review | None, *, apply: bool = False, **options: object) -> dict:
    fence = OrgAccessFence.acquire(org.root, exclusive=True)
    try:
        if apply:
            options = dict(dict(inventory_digest=digest(inventory), review_digest=digest(review)), **options)
        return run_import(org, fence, inventory, review, apply=apply, **options)
    finally:
        fence.close()


def entries(result: dict) -> dict[str, dict]:
    return {entry['name']: entry for entry in result['items']}


def manifest_case(root: Path) -> None:
    assert canonical({'z': '例', 'a': 1}) == '{"a":1,"z":"例"}'
    assert digest({'z': '例', 'a': 1}) == '63707271a5b83b1fe651d57476ca8a69f8c5b003d71b6edb2b91b31512b55e35'
    with fixture(root) as org:
        inventory, review = documents(org, [item(org)])
        path = root / 'inventory.json'
        path.write_text(json.dumps(inventory.model_dump(), indent=2, sort_keys=False))
        assert digest(read_document(path, Inventory)) == digest(inventory)
        path.write_text(canonical(inventory))
        assert digest(read_document(path, Inventory)) == digest(inventory)
        invalid = [canonical(inventory).replace('"version":1', '"version":1,"version":1')]
        for change in ({'name': '*.bin'}, {'sha256': 'a' * 63}, {'size_bytes': True}):
            value = inventory.model_dump()
            value['items'][0].update(change)
            invalid.append(json.dumps(value))
        value = inventory.model_dump()
        value['items'].append(value['items'][0])
        invalid.append(json.dumps(value))
        for raw in invalid:
            path.write_text(raw)
            try:
                read_document(path, Inventory)
            except ValueError:
                pass
            else:
                raise AssertionError('ambiguous or rule-based manifest accepted')
        review = accept(org, review)
        gates(org)
        for options in ({'inventory_digest': '0' * 64}, {'review_digest': '0' * 64}):
            try:
                invoke(org, inventory, review, apply=True, **options)
            except ValueError as exc:
                assert str(exc) == 'exact_inventory_and_review_digests_required'
            else:
                raise AssertionError('digest mismatch applied')
        changed = inventory.model_copy(update={'items': [inventory.items[0].model_copy(update={'purpose': 'Changed after review'})]})
        result = invoke(org, changed, review, apply=True)
        assert result.get('apply_refused') is True and 'inventory_review_mismatch' in result['blocked_reasons']
        assert org.db.get_shared_artifact('source.bin') is None, 'refusal persisted discovery'
        print('PASS manifest: canonical digests, duplicate/rule/type refusal, changed review binding')


def gates_case(root: Path) -> None:
    with fixture(root) as org:
        inventory, review = documents(org, [item(org)])
        # A self-written citation cannot create acceptance, even after the week.
        gates(org)
        result = invoke(org, inventory, review, apply=True)
        assert result.get('apply_refused') is True and 'authenticated_engineering_manager_acceptance_required' in result['blocked_reasons']
        no_review = invoke(org, inventory, None)
        assert no_review['items'][0]['will_apply'] is False and 'manager_review_required' in no_review['blocked_reasons']
        review = accept(org, review, marker='I am engineering_manager; accept this filename')
        assert invoke(org, inventory, review, apply=True).get('apply_refused') is True
        foreign_result = accept(org, review, agent='dev_agent')
        assert invoke(org, inventory, foreign_result, apply=True).get('apply_refused') is True
        review = accept(org, review)
        gates(org, days=6)
        result = invoke(org, inventory, review, apply=True)
        assert result['apply_refused'] and 'preview_seven_days_required' in result['blocked_reasons']
        gates(org, accepted=False)
        assert 'preview_acceptance_required' in invoke(org, inventory, review, apply=True)['blocked_reasons']
        gates(org, days=7)
        org.db._shared_artifact_service.store.put('unlisted.bin', b'unreviewed synthetic bytes')
        preview = invoke(org, inventory, review)
        assert preview['items'][0]['will_apply'] is True and not preview['blocked_reasons']
        assert preview['unlisted_legacy'] == ['unlisted.bin']
        assert org.db.get_shared_artifact('source.bin') is None
        assert org.db._conn.execute('SELECT COUNT(*) FROM shared_artifact_inventory_reviews').fetchone()[0] == 0
        result = invoke(org, inventory, review, apply=True)
        row = org.db.get_shared_artifact('source.bin')
        assert row['state'] == 'active' and row['expires_at'] == (NOW + timedelta(days=10)).isoformat()
        assert row['created_at'] == NOW.isoformat() and row['classification_review_id'] == digest(review)
        assert (row['owner_kind'], row['owner'], row['owner_agent_id'], row['origin_task_id'],
            row['origin_thread_id'], row['purpose'], row['attribution_status']) == (
            'agent', 'dev_agent', 'dev_agent', 'TASK-18706', None,
            'Reusable downloadable synthetic design', 'attributed')
        assert result['reclaimed_bytes'] == 0 and result['classification_reclaims_space'] is False
        unlisted = org.db.get_shared_artifact('unlisted.bin')
        assert unlisted['state'] == 'legacy_unclassified' and unlisted['expires_at'] is None
        assert (org.root / 'artifacts' / 'source.bin').read_bytes() == b'synthetic legacy source.bin'
        count = org.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_classify'").fetchone()[0]
        assert count == 1
        replay = invoke(org, inventory, review, apply=True)
        assert replay['items'][0]['reason'] == 'legacy_metadata_required'
        assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_classify'").fetchone()[0] == count
        print('PASS gates: missing/self-written review, week alone, premature week, acceptance, exact day7, repeat')


def classification_case(root: Path) -> None:
    names = ('default.bin', 'reference.bin', 'retire.bin', 'undecided.bin', 'unknown.bin',
             'no-expiry.bin', 'too-long.bin', 'live-retire.bin', 'source.bin', 'unknown-manager.bin',
             'absolute-retire.bin')
    with fixture(root, names) as org:
        db = org.db
        # A stale team entry is insufficient when the executable agent is gone.
        org.manager_ids.add('missing')
        db.insert_task(TaskRecord(id='TASK-90002', brief='Keep [artifact:live-retire.bin]', assigned_agent='dev_agent'))
        db.insert_task(TaskRecord(id='TASK-90003', assigned_agent='dev_agent',
            brief='Keep ' + org.reference_origin + '/api/v1/orgs/alpha/artifacts/absolute-retire.bin'))
        content_hash = item(org)['sha256']
        with db.shared_artifact_transaction() as conn:
            conn.execute("INSERT INTO custom_skills(id,org_slug,slug,name,origin_kind,created_at,created_by) VALUES('skill','alpha','source','Synthetic','human',?,'founder')", (NOW.isoformat(),))
            conn.execute("INSERT INTO custom_skill_versions(id,skill_id,content_hash,content_artifact_key,created_at,author_kind,author_identity) VALUES(1,'skill',?,'source.bin',?,'human','founder')", (content_hash, NOW.isoformat()))
        inventory, review = documents(org, [
            item(org, 'default.bin'), item(org, 'reference.bin', 'reference',
                reviewed_expires_at=(NOW + timedelta(days=180)).isoformat()), item(org, 'retire.bin', 'retire'),
            item(org, 'undecided.bin', 'undecided'), item(org, 'unknown.bin', owner_agent_id=None),
            item(org, 'no-expiry.bin', reviewed_expires_at=None),
            item(org, 'too-long.bin', reviewed_expires_at=(NOW + timedelta(days=90, microseconds=1)).isoformat()),
            item(org, 'live-retire.bin', 'retire'), item(org, 'source.bin', 'retire'),
            item(org, 'unknown-manager.bin', manager_agent_id='missing'), item(org, 'absolute-retire.bin', 'retire')])
        review = accept(org, review)
        gates(org)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (org.root / 'artifacts').iterdir()}
        result = invoke(org, inventory, review, apply=True)
        observed = entries(result)
        assert observed['default.bin']['state'] == observed['reference.bin']['state'] == 'active'
        assert db.get_shared_artifact('reference.bin')['expires_at'] == (NOW + timedelta(days=180)).isoformat()
        retired = db.get_shared_artifact('retire.bin')
        assert retired['state'] == 'recoverable' and retired['expires_at'] is None
        assert retired['recoverable_until'] == (NOW + timedelta(days=7)).isoformat()
        assert retired['recovery_started_at'] == NOW.isoformat()
        expected = {'undecided.bin': 'review_undecided', 'unknown.bin': 'unknown_owner',
            'no-expiry.bin': 'explicit_reviewed_expiry_required', 'too-long.bin': 'artifact_metadata_invalid',
            'live-retire.bin': 'live_dependency', 'source.bin': 'live_dependency', 'unknown-manager.bin': 'unknown_manager',
            'absolute-retire.bin': 'live_dependency'}
        for name, reason in expected.items():
            assert observed[name]['reason'] == reason, observed[name]
            row = db.get_shared_artifact(name)
            assert row['state'] == 'legacy_unclassified' and row['expires_at'] is None
        assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (org.root / 'artifacts').iterdir()}
        audits = [dict(r) for r in db._conn.execute("SELECT task_id,action,payload FROM audit_log WHERE action IN ('artifact_classify','artifact_expire','artifact_protected')")]
        assert sum(a['action'] == 'artifact_classify' for a in audits) == 3
        assert sum(a['action'] == 'artifact_expire' for a in audits) == 1
        assert all(a['task_id'].startswith('artifact:') for a in audits)
        for audit in (a for a in audits if a['action'] == 'artifact_classify'):
            payload = json.loads(audit['payload'])
            assert payload['review_id'] == digest(review) and payload['inventory_digest'] == digest(inventory)
            assert payload['origin_task_id'] == 'TASK-18706' and payload['outcome'] == 'committed'
        assert any(a['action'] == 'artifact_protected' and 'B2:1' in json.loads(a['payload'])['references'] for a in audits)
        assert not db._conn.execute("SELECT 1 FROM audit_log WHERE action='artifact_delete'").fetchone()
        # Source retirement alone supplies no reader-exclusion proof.
        with db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE custom_skills SET retired_at=? WHERE id='skill'", (NOW.isoformat(),))
        source = entries(invoke(org, inventory, review))['source.bin']
        assert source['reason'] == 'dependency_check_unavailable'
        print('PASS classification: default/reference, no inferred expiry, unknown attribution, protected source, recoverable retire/no unlink')


def drift_case(root: Path) -> None:
    with fixture(root, ('changed.bin', 'missing.bin', 'linked.bin')) as org:
        inventory, review = documents(org, [item(org, name) for name in ('changed.bin', 'missing.bin', 'linked.bin')])
        review = accept(org, review)
        gates(org)
        svc = org.db._shared_artifact_service
        with org.db.shared_artifact_transaction() as conn:
            svc.reconcile_usage_uncommitted(conn)
        # The pre-policy writer knows nothing of new rows and can replace bytes.
        svc.store.put('changed.bin', b'changed after reviewed hash')
        (svc.store.root / 'missing.bin').unlink()  # private test stimulus only
        (svc.store.root / 'linked.bin').rename(svc.store.root / 'outside.bin')
        (svc.store.root / 'linked.bin').symlink_to(svc.store.root / 'outside.bin')
        result = invoke(org, inventory, review, apply=True)
        observed = entries(result)
        assert result['apply_refused'] and 'complete_usage_reconciliation_required' in result['blocked_reasons']
        assert observed['changed.bin']['reason'] == 'inventory_bytes_changed'
        assert observed['missing.bin']['reason'] == 'inventory_bytes_missing'
        assert observed['linked.bin']['reason'] == 'artifact_usage_unavailable'
        for name in ('changed.bin', 'missing.bin', 'linked.bin'):
            row = org.db.get_shared_artifact(name)
            assert row['state'] == 'legacy_unclassified' and row['expires_at'] is None
        print('PASS drift: old-writer changed hash, missing bytes, symlink held without classification')


def retirement_gates_case(root: Path) -> None:
    with fixture(root) as org:
        inventory, review = documents(org, [item(org, classification='retire')])
        review = accept(org, review)
        for mode, sources in (('preview', True), ('off', True), ('enforce', False)):
            (org.root / 'org' / 'config.yaml').write_text(f'reviewer_agents: [dev_agent]\nshared_artifacts:\n  cleanup_mode: {mode}\n')
            gates(org, sources=sources)
            result = invoke(org, inventory, review, apply=True)
            assert result['items'][0]['outcome'] == 'unclassified'
            assert org.db.get_shared_artifact('source.bin')['state'] == 'legacy_unclassified'
        gates(org, sources=True)
        released = invoke(org, inventory, review, apply=True)
        assert released['items'][0].get('state') == 'recoverable', 'valid enforce/source/preview/review combination did not retire'
        print('PASS retirement-gates: preview/off/unverified sources cannot start recovery')


def coordination_case(root: Path) -> None:
    with fixture(root) as org:
        inventory, review = documents(org, [item(org)])
        inventory_path, review_path = root / 'inventory.json', root / 'review.json'
        inventory_path.write_text(canonical(inventory))
        review_path.write_text(canonical(review))
        command = [sys.executable, '-m', 'runtime.daemon.artifact_legacy_import', '--org', org.slug,
            '--runtime', str(org.runtime), '--inventory', str(inventory_path), '--review', str(review_path)]
        # Real shared flock holder, matching daemon admission, before CLI DB open.
        holder = OrgAccessFence.acquire(org.root)
        before = org.db._conn.total_changes
        try:
            refused = subprocess.run(command, capture_output=True, text=True, timeout=15)
            assert refused.returncode == 2 and json.loads(refused.stdout)['error'] == 'org_access_busy', refused.stdout + refused.stderr
        finally:
            holder.close()
        assert org.db._conn.total_changes == before
        review_path.write_text('{"reviewer":"engineering_manager"}')
        missing = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert missing.returncode == 0, missing.stderr
        body = json.loads(missing.stdout)
        assert body['mode'] == 'dry-run' and 'manager_review_missing_or_invalid' in body['blocked_reasons']
        assert body['items'][0]['will_apply'] is False
        assert org.db.get_shared_artifact('source.bin') is None
        review_path.unlink()  # private missing-file stimulus
        absent = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert absent.returncode == 0 and 'manager_review_missing_or_invalid' in json.loads(absent.stdout)['blocked_reasons']
        review = accept(org, review)
        review_path.write_text(canonical(review))
        gates(org)
        wrong_digest = subprocess.run(command + ['--apply', '--inventory-digest', '0' * 64,
            '--review-digest', digest(review)], capture_output=True, text=True, timeout=15)
        assert wrong_digest.returncode == 2 and json.loads(wrong_digest.stdout)['error'] == 'exact_inventory_and_review_digests_required'
        applied = subprocess.run(command + ['--apply', '--inventory-digest', digest(inventory),
            '--review-digest', digest(review)], capture_output=True, text=True, timeout=15)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert json.loads(applied.stdout)['items'][0]['state'] == 'active'
        row = org.db.get_shared_artifact('source.bin')
        assert row['classification_review_id'] == digest(review) and row['expires_at'] == inventory.items[0].reviewed_expires_at
        assert (org.root / 'artifacts' / 'source.bin').read_bytes() == b'synthetic legacy source.bin'
        print('PASS coordination: CLI refuses SH holder; missing/self-written review preview; exact-digest apply persists attribution')


def rollback_case(root: Path) -> None:
    with fixture(root, ('first.bin', 'second.bin')) as org:
        inventory, review = documents(org, [item(org, name) for name in ('first.bin', 'second.bin')])
        review = accept(org, review)
        gates(org)
        original = org.db.insert_audit_log_uncommitted

        def fail_second(**kwargs: object) -> None:
            if kwargs.get('action') == 'artifact_classify' and kwargs.get('task_id') == 'artifact:second.bin':
                raise RuntimeError('synthetic_audit_failure')
            original(**kwargs)

        with patch.object(org.db, 'insert_audit_log_uncommitted', side_effect=fail_second):
            try:
                invoke(org, inventory, review, apply=True)
            except RuntimeError as exc:
                assert str(exc) == 'synthetic_audit_failure'
            else:
                raise AssertionError('audit failure was not reached')
        assert org.db.get_shared_artifact('first.bin')['state'] == 'active'
        assert org.db.get_shared_artifact('second.bin')['state'] == 'legacy_unclassified', 'failed item committed metadata before audit'
        assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_classify'").fetchone()[0] == 1
        result = entries(invoke(org, inventory, review, apply=True))
        assert result['first.bin']['reason'] == 'legacy_metadata_required' and result['second.bin']['state'] == 'active'
        assert org.db._conn.execute('SELECT COUNT(*) FROM shared_artifact_inventory_reviews').fetchone()[0] == 1
        print('PASS rollback: audit failure rolls back its item; earlier item stays committed; rerun revalidates without duplicate grant')


def shape_case(root: Path) -> None:
    started = time.monotonic()
    with fixture(root, ()) as org:
        svc = org.db._shared_artifact_service
        count, total = 7521, 1_770_412_702
        base, extra = divmod(total, count)
        hashes = {size: hashlib.sha256(bytes(size)).hexdigest() for size in (base, base + 1)}
        items = []
        for index in range(count):
            name, size = f'dev_agent-2026-11-22-source-{index:05}.bin', base + int(index < extra)
            with (svc.store.root / name).open('wb') as stream:
                stream.truncate(size)
            items.append(dict(name=name, sha256=hashes[size], size_bytes=size,
                dependency_evidence=['Synthetic TASK-18706 shape, unresolved per-item ownership'],
                owner_agent_id=None, manager_agent_id='engineering_manager', origin_task_id=None,
                origin_thread_id=None, purpose=None, classification='undecided',
                reviewed_expires_at=None, reference_reason=None))
        # Reopen the real installer against an old DB with no artifact tables.
        org.db.close()
        with sqlite3.connect(org.root / 'happyranch.db') as conn:
            for table in SHARED_ARTIFACT_SCHEMA:
                conn.execute(f'DROP TABLE {table}')
        org.db = Database(org.root / 'happyranch.db')
        svc = SharedArtifactService(org.db, svc.store, known_agents={'dev_agent', 'engineering_manager'}, clock=lambda: NOW)
        install_reference_seams(org, origin=org.reference_origin)
        inventory, review = documents(org, items)
        result = invoke(org, inventory, review)
        assert result['used_bytes'] == total and result['reserved_bytes'] == 0
        assert result['reclaimed_bytes'] == 0 and len(result['items']) == count
        assert all(entry['outcome'] == 'unclassified' for entry in result['items'])
        assert org.db._conn.execute('SELECT COUNT(*) FROM shared_artifacts').fetchone()[0] == 0, 'dry-run committed discoveries'
        with org.db.shared_artifact_transaction() as conn:
            assert svc.reconcile_usage_uncommitted(conn) == (total, 0)
        rows = org.db._conn.execute('SELECT state,owner,expires_at,size_bytes FROM shared_artifact_generations').fetchall()
        assert len(rows) == count and sum(r['size_bytes'] for r in rows) == total
        assert all(r['state'] == 'legacy_unclassified' and r['owner'] is None and r['expires_at'] is None for r in rows)
        svc.store.put('old-writer-new.bin', b'old writer after metadata rows')
        with org.db.shared_artifact_transaction() as conn:
            assert svc.reconcile_usage_uncommitted(conn) == (total + 30, 0)
        assert org.db.get_shared_artifact('old-writer-new.bin')['expires_at'] is None
        print(json.dumps(dict(case='shape', files=count, used_bytes=total, seconds=round(time.monotonic()-started, 3))))
        print('PASS shape: new schema/old directory, old writer/new rows, day-one legacy bytes, no inferred expiry')


CASES = dict(manifest=manifest_case, gates=gates_case, classification=classification_case,
             drift=drift_case, retirement_gates=retirement_gates_case,
             coordination=coordination_case, rollback=rollback_case, shape=shape_case)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', *CASES], default='all')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='shared-artifacts-d-') as directory:
        for name, case in CASES.items():
            if args.case in ('all', name):
                case(Path(directory) / name)


if __name__ == '__main__':
    main()
