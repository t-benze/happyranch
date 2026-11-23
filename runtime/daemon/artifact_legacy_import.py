"""Offline operator-only reviewed legacy classification; never unlinks bytes.

The existing org EX fence is acquired before Database construction. A running
daemon or retained native/session org holder therefore refuses this command.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from runtime.config import Settings
from runtime.daemon import runtimes
from runtime.infrastructure.artifact_store import ArtifactStore
from runtime.infrastructure.database import Database
from runtime.infrastructure.org_access_fence import OrgAccessFence, OrgAccessHeld
from runtime.infrastructure.shared_artifact_inventory import (
    Inventory, Review, authenticated_review, canonical, classification_gate,
    digest, item_preview, read_document, validate_pair,
)
from runtime.infrastructure.shared_artifact_references import (
    audit_protection, effective_mode, install_reference_seams, reference_outcomes,
)
from runtime.infrastructure.shared_artifact_service import SharedArtifactError, SharedArtifactService
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.prompt_loader import list_agents
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


class _PreviewRollback(Exception):
    pass


def _review_receipt(org: object, conn: object, inventory: Inventory, review: Review) -> dict:
    receipt = authenticated_review(conn, inventory, review, now=org.db._shared_artifact_service.clock())
    prior = conn.execute('SELECT * FROM shared_artifact_inventory_reviews WHERE review_id=?',
                         (receipt['review_id'],)).fetchone()
    if prior is None:
        org.db._shared_artifact_service.record_inventory_review_uncommitted(conn, review=receipt)
    elif dict(prior) != receipt:
        raise ValueError('immutable_review_receipt_mismatch')
    conn.execute('UPDATE shared_artifact_org_state SET inventory_review_id=? WHERE id=1', (receipt['review_id'],))
    return receipt


def preview(org: object, inventory: Inventory, review: Review | None, *, review_error: str | None = None) -> dict:
    """Roll back discoveries/receipt simulation; first-preview timing is B-owned."""
    db, svc = org.db, org.db._shared_artifact_service
    result = dict(org=org.slug, inventory_digest=digest(inventory), review_digest=digest(review) if review else None,
                  mode='dry-run', classification_reclaims_space=False, reclaimed_bytes=0,
                  explanation='All legacy bytes count immediately. Classification reclaims no space.',
                  blocked_reasons=[], items=[])
    deadline = time.monotonic() + 60
    try:
        with db.shared_artifact_transaction() as conn:
            try:
                used, reserved = svc.reconcile_usage_uncommitted(conn, deadline=deadline)
                result.update(used_bytes=used, reserved_bytes=reserved)
            except SharedArtifactError as exc:
                result['usage_error'] = exc.detail
                result['blocked_reasons'].append('complete_usage_reconciliation_required')
            reason = classification_gate(conn, svc.clock())
            if reason:
                result['blocked_reasons'].append(reason)
            try:
                if review is None:
                    raise ValueError(review_error or 'manager_review_required')
                validate_pair(inventory, review, org.slug)
                receipt = _review_receipt(org, conn, inventory, review)
                result['review_citation'] = receipt['ruling_citation']
            except ValueError as exc:
                result['blocked_reasons'].append(str(exc))
            mode, mode_reason = effective_mode(org, conn, now=svc.clock())
            result.update(effective_mode=mode, mode_reason=mode_reason)
            rows = [db._shared_artifact_uncommitted(conn, item.name) for item in inventory.items]
            outcomes = reference_outcomes(org, conn, [r for r in rows if r], deadline=deadline,
                                          origin=org.reference_origin)
            for item, row in zip(inventory.items, rows):
                reference = outcomes.get((row['name'], row['generation'])) if row else None
                entry, _ = item_preview(org, conn, item, deadline=deadline, reference=reference)
                if entry['outcome'] == 'eligible' and item.classification == 'retire' and mode != 'enforce':
                    entry.update(outcome='unclassified', reason=mode_reason or 'retirement_enforce_required')
                entry['will_apply'] = entry['outcome'] == 'eligible' and not result['blocked_reasons']
                result['items'].append(entry)
            names = {item.name for item in inventory.items}
            legacy = conn.execute(
                "SELECT g.name FROM shared_artifact_generations g JOIN shared_artifacts a "
                "ON a.name=g.name AND a.current_generation=g.generation WHERE g.state='legacy_unclassified' ORDER BY g.name LIMIT 100001").fetchall()
            if len(legacy) > 100000:
                result['blocked_reasons'].append('legacy_metadata_inventory_incomplete')
            result['unlisted_legacy'] = [r[0] for r in legacy if r[0] not in names]
            if time.monotonic() >= deadline:
                result['blocked_reasons'].append('preview_deadline_exhausted')
            if result['blocked_reasons']:
                for entry in result['items']:
                    entry['will_apply'] = False
            result['unlisted_reason'] = 'not_in_reviewed_inventory; remains legacy_unclassified'
            raise _PreviewRollback
    except _PreviewRollback:
        return result


def run_import(org: object, fence: OrgAccessFence, inventory: Inventory, review: Review | None, *,
               apply: bool = False, inventory_digest: str | None = None,
               review_digest: str | None = None, review_error: str | None = None) -> dict:
    """Single existing transaction per item, fresh byte/reference proof per COMMIT.

    There is no new retry/crash protocol: rerunning revalidates current state and
    leaves already classified items untouched. A owns the transaction/audit shape.
    """
    fence.assert_held()
    if not fence.exclusive or fence.root != org.root or inventory.org != org.slug:
        raise ValueError('exclusive_org_fence_required')
    if apply and (inventory_digest != digest(inventory) or review is None or review_digest != digest(review)):
        raise SharedArtifactError(409, 'exact_inventory_and_review_digests_required')
    result = preview(org, inventory, review, review_error=review_error)
    if not apply:
        return result
    result['mode'] = 'apply'
    if result['blocked_reasons']:
        result['apply_refused'] = True
        return result
    db, svc = org.db, org.db._shared_artifact_service
    # Only metadata discovery; all physical bytes remain counted and in place.
    with db.shared_artifact_transaction() as conn:
        fence.assert_held()
        if classification_gate(conn, svc.clock()):
            raise ValueError('classification_gate_changed')
        svc.reconcile_usage_uncommitted(conn)
        # The existing EX interval excludes other cooperative org writers.
        # Persist one authenticated immutable receipt, rather than reserializing
        # the entire manifest/review for every item in a large inventory.
        receipt = _review_receipt(org, conn, inventory, review)
    result['items'] = []
    for item in inventory.items:
        with db.shared_artifact_transaction() as conn:
            fence.assert_held()
            if classification_gate(conn, svc.clock()):
                raise ValueError('classification_gate_changed')
            entry, metadata = item_preview(org, conn, item, deadline=time.monotonic() + 30)
            row = db._shared_artifact_uncommitted(conn, item.name)
            if row and entry.get('reference_outcome') != 'absent' and entry.get('reference_outcome'):
                audit_protection(org, row, entry['reference_outcome'], entry['references'])
            mode, reason = effective_mode(org, conn, now=svc.clock())
            if entry['outcome'] == 'eligible' and item.classification == 'retire' and mode != 'enforce':
                entry.update(outcome='unclassified', reason=reason or 'retirement_enforce_required')
            if entry['outcome'] == 'eligible':
                classified = svc.classify_legacy_uncommitted(conn, name=item.name, metadata=metadata,
                    review_id=receipt['review_id'], inventory_digest=receipt['inventory_digest'],
                    expected_hash=item.sha256, expected_size=item.size_bytes)
                if item.classification == 'retire':
                    row = db._shared_artifact_uncommitted(conn, item.name)
                    classified = svc.lifecycle_transition_uncommitted(conn, name=item.name,
                        generation=row['generation'], expected_revision=row['revision'], action='retire',
                        reason='reviewed_legacy_retirement', reference_outcome=entry['reference_outcome'],
                        effective_mode=mode, operation_id=str(uuid.uuid4()), actor=receipt['reviewer_agent_id'])
                    if not classified.get('mutated'):
                        # Never leave an active null-expiry intermediate state.
                        raise ValueError('retirement_transition_refused')
                entry.update(outcome='classified', state=classified['state'], mutated=True)
            elif row and row['state'] == 'legacy_unclassified':
                svc._event(row, 'artifact_preview', receipt['reviewer_agent_id'],
                    inventory_digest=receipt['inventory_digest'], review_id=receipt['review_id'],
                    mutated=False, outcome='legacy_unclassified', reason=entry['reason'])
            result['items'].append(entry)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', required=True)
    parser.add_argument('--runtime', type=Path, help='Absolute runtime root; defaults to registered active runtime')
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default: exact preview, artifact transactions rolled back')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--inventory-digest')
    parser.add_argument('--review-digest')
    args = parser.parse_args(argv)
    db = fence = None
    try:
        if re.fullmatch('[a-z0-9-]{1,40}', args.org) is None:
            raise ValueError('invalid_org')
        inventory = read_document(args.inventory, Inventory)
        if inventory.org != args.org:
            raise ValueError('inventory_org_mismatch')
        review, review_error = None, None
        try:
            review = read_document(args.review, Review)
        except (OSError, ValueError):
            review_error = 'manager_review_missing_or_invalid'
        if args.apply and (args.inventory_digest != digest(inventory) or review is None
                           or args.review_digest != digest(review)):
            raise SharedArtifactError(409, 'exact_inventory_and_review_digests_required')
        runtime = args.runtime or runtimes.load().active
        if runtime is None or not runtime.is_absolute():
            raise ValueError('absolute_runtime_required')
        root = runtime / 'orgs' / args.org
        # No DB constructor, schema installer or artifact opener before EX.
        fence = OrgAccessFence.acquire(root, exclusive=True)
        RuntimeDir.load(runtime)
        if fence.database_identity is None:
            raise ValueError('existing_org_database_required')
        db = Database(root / 'happyranch.db', acquisition_timeout=0)
        fence.assert_held()
        teams = TeamsRegistry.load(root)
        settings = Settings()
        org = SimpleNamespace(root=root, slug=args.org, db=db,
            manager_ids={teams.executable_manager_for_team(t) for t in teams.teams()} - {None},
            reference_origin=f'http://{settings.daemon_bind_host}:{settings.daemon_port}')
        SharedArtifactService(db, ArtifactStore.open_existing(root / 'artifacts'),
                              known_agents={agent.name for agent in list_agents(OrgPaths(root))})
        install_reference_seams(org, origin=org.reference_origin)
        result = run_import(org, fence, inventory, review, apply=args.apply,
            inventory_digest=args.inventory_digest, review_digest=args.review_digest, review_error=review_error)
        print(canonical(result))
        return 2 if result.get('apply_refused') else 0
    except (OSError, ValueError, sqlite3.Error, OrgAccessHeld) as exc:
        code = exc.detail['code'] if isinstance(exc, SharedArtifactError) else (
            str(exc) if isinstance(exc, OrgAccessHeld) else 'legacy_import_refused')
        print(json.dumps(dict(error=code, exception=type(exc).__name__,
                              classification_may_have_committed=bool(args.apply))))
        return 2
    finally:
        # No child/worker is launched: database closure ends this owned interval.
        try:
            if db is not None:
                db.close()
        finally:
            if fence is not None:
                fence.close()


if __name__ == '__main__':
    sys.exit(main())
