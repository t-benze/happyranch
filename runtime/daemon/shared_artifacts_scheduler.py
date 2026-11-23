"""Daily bounded artifact lifecycle pass, separate from host/workspace cleanup."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from runtime.daemon.shared_artifacts_notices import NoticeUnavailable, SharedArtifactNotices
from runtime.infrastructure.shared_artifact_references import (
    audit_protection, effective_mode, install_reference_seams, reference_outcomes,
)
from runtime.infrastructure.shared_artifact_service import SharedArtifactError, _date, _iso
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config, resolve_org_timezone

logger = logging.getLogger(__name__)


def current_window(now: datetime, tz: object) -> str | None:
    """Latest real 04:00, fold=0; skip nonexistent local occurrences."""
    local = now.astimezone(tz)
    for offset in range(4):
        day = local.date()-timedelta(days=offset)
        wall = datetime(day.year,day.month,day.day,4,tzinfo=tz,fold=0)
        utc = wall.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) != wall.replace(tzinfo=None):
            continue
        if utc <= now:
            return _iso(utc)
    return None


def _audit(org: object, action: str, payload: dict) -> None:
    org.db.insert_audit_log_uncommitted(task_id='config:shared_artifacts',agent='daemon',
                                       action=action,payload=dict(source='system',**payload))


def finish_delete(org: object, operation_id: str, *, deadline: float,
                  stop: threading.Event | None = None, origin: str | None = None) -> bool:
    """Recheck under one writer fence through no-follow unlink and tombstone.

    Only an issued exact intent may enter. Missing bytes complete the old intent;
    changed identity is never retried against replacement bytes. A failed final
    audit/COMMIT leaves the already-durable intent explaining unavailable bytes.
    """
    db, svc = org.db, org.db._shared_artifact_service
    with db.shared_artifact_transaction() as conn:
        intent = conn.execute("SELECT * FROM shared_artifact_operations WHERE operation_id=? AND operation='delete' AND phase='issued'",(operation_id,)).fetchone()
        if not intent:
            return False
        row = db._shared_artifact_uncommitted(conn,intent['name'],intent['generation'])
        current = conn.execute('SELECT current_generation FROM shared_artifacts WHERE name=?',(intent['name'],)).fetchone()
        mode, _refusal = effective_mode(org,conn,now=svc.clock())
        if (not row or not current or current[0] != intent['generation']
            or row['revision'] != intent['expected_revision'] or mode != 'enforce'
            or row['state'] != 'recoverable' or time.monotonic() >= deadline
            or (stop and stop.is_set())):
            return False
        outcome, refs = reference_outcomes(org,conn,[row],deadline=deadline,origin=origin)[row['name'],row['generation']]
        audit_protection(org,row,outcome,refs)
        if outcome != 'absent':
            return False
        expected = json.loads(intent['filesystem_identity'])
        path = svc.store.path_for(row['name'])
        missing = False
        try:
            observed = svc.identity(path,deadline=deadline)
        except FileNotFoundError:
            # Distinguish a missing leaf from vanished/replaced ancestry.
            parent = path.parent
            while True:
                if parent.is_symlink() or not parent.is_dir():
                    raise SharedArtifactError(503,'artifact_delete_held',reason='ancestry_unavailable')
                if parent == svc.store.root:
                    break
                parent = parent.parent
            missing = True
        if not missing:
            if observed != expected or time.monotonic() >= deadline or (stop and stop.is_set()):
                svc._event(row,'artifact_protected','daemon',operation_id,source='system',
                           reason='delete_identity_changed',outcome='held')
                return False
            # Pin each directory without following links. The leaf's identity
            # is rechecked descriptor-relative immediately before unlink.
            parent_fd = os.open(svc.store.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                for part in path.relative_to(svc.store.root).parts[:-1]:
                    child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = child
                leaf = os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
                if (leaf.st_dev,leaf.st_ino,leaf.st_size,leaf.st_mtime_ns,leaf.st_nlink) != (
                    expected['device'],expected['inode'],expected['size_bytes'],expected['mtime_ns'],1):
                    raise SharedArtifactError(503,'artifact_delete_held',reason='final_identity_changed')
                os.unlink(path.name,dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        result = svc.lifecycle_transition_uncommitted(conn,name=row['name'],generation=row['generation'],
            expected_revision=row['revision'],action='deleted',reason='retention_recovery_elapsed',
            reference_outcome='absent',effective_mode='enforce',operation_id=operation_id,
            reclaimed_bytes=expected['size_bytes'])
        if missing:
            svc._event(row,'artifact_operation_reconciled','daemon',operation_id,source='system',
                       outcome='committed',reason='issued_delete_bytes_missing')
        return result['mutated']


def sweep(org: object, window: str, *, deadline: float | None = None,
          stop: threading.Event | None = None, origin: str | None = None) -> dict:
    """One current-window pass. No historical or incomplete destructive replay."""
    deadline = deadline if deadline is not None else time.monotonic()+60
    db, svc = org.db, org.db._shared_artifact_service
    now, run_id = svc.clock(), str(uuid.uuid4())
    result = dict(run_id=run_id,window_id=window,cutoff=_iso(now),cursor='',
                  complete=False,processed=0,expired=0,deleted=0,protected=0,notices=0,legacy=0)
    notices = SharedArtifactNotices(org)
    try:
        # Issued operations alone are restart-reconciled, even in a serviced
        # window. Current preview/off refuses new unlink in finish_delete.
        with db.shared_artifact_transaction() as conn:
            issued = [r[0] for r in conn.execute("SELECT operation_id FROM shared_artifact_operations WHERE operation='delete' AND phase='issued' ORDER BY operation_id LIMIT 100001")]
        if len(issued) > 100000:
            raise NoticeUnavailable('delete_journal_scan_incomplete')
        for operation_id in issued:
            if time.monotonic() >= deadline or (stop and stop.is_set()):
                raise NoticeUnavailable('sweep_budget_exhausted')
            result['deleted'] += int(finish_delete(org,operation_id,deadline=deadline,stop=stop,origin=origin))
        with db.shared_artifact_transaction() as conn:
            prior = conn.execute("SELECT 1 FROM audit_log WHERE task_id='config:shared_artifacts' AND action='artifact_sweep_started' AND json_extract(payload,'$.window_id')=? LIMIT 1",(window,)).fetchone()
            if prior:
                return dict(result,outcome='already_serviced')
            mode, refusal = effective_mode(org,conn,now=now)
            result['effective_mode'] = mode
            if refusal:
                _audit(org,'artifact_mode_refused',dict(effective_mode=mode,reason=refusal))
            _audit(org,'artifact_sweep_started',result)
        # The same deadline is passed through A's inventory/hash/quota work.
        with db.shared_artifact_transaction() as conn:
            quota = svc.quota_crossing_uncommitted(conn,deadline=deadline)
            rows = [dict(r) for r in conn.execute('SELECT g.* FROM shared_artifact_generations g JOIN shared_artifacts a ON a.name=g.name AND a.current_generation=g.generation ORDER BY g.name LIMIT 100001')]
            if len(rows) > 100000:
                raise NoticeUnavailable('artifact_inventory_incomplete')
            notices.deliver_quota(conn,rows,quota=quota,now=now,mode=mode,deadline=deadline)
            result.update(used_bytes=quota['used_bytes'],reserved_bytes=quota['reserved_bytes'])
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            delete_ids = []
            with db.shared_artifact_transaction() as conn:
                rows = [dict(r) for r in conn.execute('SELECT g.*,a.revision FROM shared_artifacts a JOIN shared_artifact_generations g ON g.name=a.name AND g.generation=a.current_generation WHERE g.name>? ORDER BY g.name LIMIT 250',(result['cursor'],))]
                if not rows:
                    result['complete'] = True
                    break
                # Entire batch proof collected before own metadata/audit writes.
                outcomes = reference_outcomes(org,conn,rows,deadline=deadline,origin=origin)
                for row in rows:
                    if time.monotonic() >= deadline or (stop and stop.is_set()):
                        break
                    outcome, refs = outcomes[row['name'],row['generation']]
                    audit_protection(org,row,outcome,refs)
                    result['protected'] += int(outcome != 'absent')
                    if outcome == 'indeterminate':
                        result['reference_incomplete'] = True
                    if (mode == 'enforce' and row['state'] == 'pending-attribution'
                        and now >= _date(row['created_at'])+timedelta(hours=24)):
                        svc.lifecycle_transition_uncommitted(conn,name=row['name'],generation=row['generation'],
                            expected_revision=row['revision'],action='timeout',reason='attribution_timeout',
                            reference_outcome=outcome,effective_mode=mode,operation_id=str(uuid.uuid4()))
                        row = db._shared_artifact_uncommitted(conn,row['name'],row['generation'])
                    if row['state'] == 'legacy_unclassified' or not row['expires_at']:
                        result['legacy'] += int(row['state'] == 'legacy_unclassified')
                    elif row['state'] in ('active','pending-attribution') and now >= _date(row['expires_at']):
                        transition = svc.lifecycle_transition_uncommitted(conn,name=row['name'],generation=row['generation'],
                            expected_revision=row['revision'],action='expire',reason='retention_expired',
                            reference_outcome=outcome,effective_mode=mode,operation_id=str(uuid.uuid4()))
                        result['expired'] += int(transition.get('mutated',False))
                        row = db._shared_artifact_uncommitted(conn,row['name'],row['generation'])
                    elif row['state'] == 'recoverable' and now >= _date(row['recoverable_until']):
                        if mode == 'enforce' and outcome == 'absent':
                            pending = conn.execute("SELECT operation_id FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')",(row['name'],)).fetchone()
                            if pending:
                                result['operation_pending'] = True
                                result['cursor'] = row['name']
                                result['processed'] += 1
                                continue
                            operation_id = str(uuid.uuid4())
                            svc.prepare_delete_uncommitted(conn,name=row['name'],generation=row['generation'],
                                expected_revision=row['revision'],reference_outcome=outcome,effective_mode=mode,
                                operation_id=operation_id,deadline=deadline)
                            delete_ids.append(operation_id)
                        elif mode == 'preview':
                            svc._event(row,'artifact_preview','daemon',source='system',
                                       mutated=False,outcome='would_delete',references=refs)
                    try:
                        result['notices'] += notices.deliver_file(conn,row,now=now,mode=mode,deadline=deadline)
                    except NoticeUnavailable:
                        result['notice_pending'] = True
                    result['cursor'] = row['name']
                    result['processed'] += 1
            # Intent COMMIT precedes the fresh final fence. No proof crosses it.
            for operation_id in delete_ids:
                result['deleted'] += int(finish_delete(org,operation_id,deadline=deadline,stop=stop,origin=origin))
        if result.get('notice_pending') or result.get('reference_incomplete') or result.get('operation_pending'):
            result['complete'] = False
        result['outcome'] = 'complete' if result['complete'] else 'incomplete'
    except (OSError,ValueError,sqlite3.Error) as exc:
        result.update(complete=False,outcome='incomplete',reason=type(exc).__name__)
    with db.shared_artifact_transaction() as conn:
        if result['complete']:
            conn.execute('UPDATE shared_artifact_org_state SET last_completed_window=? WHERE id=1',(window,))
            if result['effective_mode'] == 'preview':
                conn.execute('UPDATE shared_artifact_org_state SET rollout_started_at=COALESCE(rollout_started_at,?) WHERE id=1',(_iso(svc.clock()),))
        result['deadline_exhausted'] = time.monotonic() >= deadline
        _audit(org,'artifact_sweep_finished',result)
    return result


async def shared_artifacts_scheduler_loop(state: object) -> None:
    """Warm up; run bounded native work with org lifetime/cancellation custody."""
    await asyncio.sleep(30)
    while True:
        for org in state.orgs_snapshot().values():
            with org._runner_lifetime() as admitted:
                if not admitted:
                    continue
                try:
                    try:
                        config = load_org_config(OrgPaths(org.root))
                    except (OSError,ValueError):
                        with org.db.shared_artifact_transaction():
                            _audit(org,'artifact_mode_refused',dict(effective_mode='preview',reason='configuration_unavailable'))
                        continue
                    window = current_window(datetime.now(timezone.utc),resolve_org_timezone(config))
                    if window is None:
                        continue
                    from runtime.daemon.routes.artifacts import artifact_service
                    artifact_service(org)
                    origin = f'http://{org.settings.daemon_bind_host}:{org.settings.daemon_port}'
                    install_reference_seams(org,origin=origin)
                    stop = threading.Event()
                    async with org.db_lock:
                        work = asyncio.create_task(asyncio.to_thread(sweep,org,window,stop=stop,origin=origin))
                        try:
                            await asyncio.shield(work)
                        except asyncio.CancelledError:
                            stop.set()
                            while not work.done():
                                try:
                                    await asyncio.shield(work)
                                except asyncio.CancelledError:
                                    continue
                            work.result()
                            raise
                except (OSError,ValueError,sqlite3.Error):
                    logger.exception('Shared artifact pass held for org %s',org.slug)
        await asyncio.sleep(60)
