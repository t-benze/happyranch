"""Slice B private acceptance checks; no daemon lifespan or live org.

Run: uv run --frozen python scripts/test_shared_artifacts_b.py --case manual-delete
The enforce case currently exposes the A-owned preview-only DELETE blocker.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from datetime import timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.models import ThreadRecord
from scripts.test_agent_retirement import PREFIX, fixture
from scripts.test_shared_artifacts import NOW, snapshot


async def manual_delete_preview_case(root: Path, *, enforce: bool) -> None:
    """Manual retirement must remain preview after automatic rollout gates pass."""
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org, client):
            org.db.insert_thread(ThreadRecord(id='THR-origin', subject='Synthetic origin'))
            put = await client.post(
                PREFIX + '/artifacts',
                params=dict(agent='worker', thread_id='THR-origin',
                            purpose='Persistent design source', retention='default'),
                files={'file': ('manual-preview.bin', b'synthetic retained source')},
            )
            assert put.status_code == 200, put.text
            # Exercise A's documented B seams, not a test-side retire implementation.
            # Synthetic gates never touch live config or establish source proof.
            if enforce:
                org.db._shared_artifact_reference_checker = lambda conn, row: 'absent'
                org.db._shared_artifact_effective_mode = lambda conn: 'enforce'
                with org.db.shared_artifact_transaction() as conn:
                    conn.execute(
                        "UPDATE shared_artifact_org_state SET rollout_started_at=?,"
                        "preview_acceptance='synthetic-preview',"
                        "inventory_review_id='synthetic-inventory',"
                        "source_protection_verified=1 WHERE id=1",
                        ((NOW - timedelta(days=8)).isoformat(),),
                    )
            before = snapshot(org)
            response = await client.delete(
                PREFIX + '/artifacts/manual-preview.bin', params={'agent': 'worker'},
            )
            row = org.db.get_shared_artifact('manual-preview.bin')
            print(json.dumps(dict(
                case='manual-delete-enforce' if enforce else 'manual-delete-default',
                status=response.status_code, response=response.json(),
                persisted_state=row['state'],
                recovery_started_at=row['recovery_started_at'],
                recoverable_until=row['recoverable_until'],
                snapshot_unchanged=snapshot(org) == before,
            ), sort_keys=True), flush=True)
            assert response.status_code == 200, response.text
            assert response.json().get('mutated') is False, (
                'manual DELETE must remain preview-only after automatic enforce gates; '
                + response.text
            )
            assert response.json()['effective_mode'] == 'preview', response.text
            assert snapshot(org) == before, 'manual DELETE changed lifecycle rows or bytes'
            assert row['state'] == 'active' and row['recovery_started_at'] is None
            events = org.db._conn.execute(
                "SELECT action,payload FROM audit_log WHERE task_id=? "
                "AND action IN ('artifact_preview','artifact_expire','artifact_delete')",
                ('artifact:manual-preview.bin',),
            ).fetchall()
            assert len(events) == 1 and events[0]['action'] == 'artifact_preview'
            assert json.loads(events[0]['payload'])['mutated'] is False
            print('PASS manual-delete-' + ('enforce' if enforce else 'default'))


async def main(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix='shared-artifact-B-') as directory:
        root = Path(directory)
        if case in ('manual-delete', 'manual-delete-default'):
            await manual_delete_preview_case(root / 'default', enforce=False)
        if case in ('manual-delete', 'manual-delete-enforce'):
            await manual_delete_preview_case(root / 'enforce', enforce=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=(
        'manual-delete', 'manual-delete-default', 'manual-delete-enforce',
    ), default='manual-delete')
    asyncio.run(main(parser.parse_args().case))
