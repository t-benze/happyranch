"""Org-owned Shared Artifacts admission and lifecycle seam (no scheduler).

Generic ArtifactStore stays policy-free. All service SQL joins an explicit
writer owner; filesystem publication is journaled before replacing public bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Callable

from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName, MAX_ARTIFACT_BYTES
from runtime.infrastructure.audit_logger import AuditLogger

QUOTA_BYTES = 5_000_000_000
ALERT_BYTES = 4_000_000_000
DEFAULT_DAYS = 90
REFERENCE_DAYS = 180
RECOVERY_DAYS = 7
PROVISIONAL_HOURS = 24
SCAN_SECONDS = 60
SCAN_ENTRIES = 100_000


class SharedArtifactError(ValueError):
    def __init__(self, status: int, code: str, **details: object) -> None:
        super().__init__(code)
        self.status = status
        self.detail = {'code': code, **details}


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _date(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
            raise ValueError
        return instant
    except (ValueError, TypeError, AttributeError) as exc:
        raise SharedArtifactError(422, 'artifact_metadata_invalid', fields={'expires_at': 'UTC instant required'}) from exc


class SharedArtifactService:
    def __init__(
        self, db: object, store: ArtifactStore, *, clock: Callable[[], datetime] | None = None,
        known_agents: set[str] | None = None,
        manager_for_thread: Callable[[str], str | None] | None = None,
        download_prefix: str = "artifacts",
    ) -> None:
        self.db = db
        db._shared_artifact_service = self
        self.store = store
        self.clock = clock or _utc
        self.known_agents = known_agents
        self.download_prefix = download_prefix
        self.manager_for_thread = manager_for_thread or (lambda _thread: None)
        self.audit = AuditLogger(db)

    def metadata(
        self, *, agent: str, task_id: str | None = None, thread_id: str | None = None,
        purpose: str | None = None, retention: str | None = None,
        reason: str | None = None, expires_at: str | None = None,
        provisional: bool = False, system_purpose: str | None = None,
    ) -> dict:
        """Validate complete provenance before durable bytes or metadata writes."""
        now = self.clock()
        supplied = (task_id, thread_id, purpose, retention, reason, expires_at)
        if provisional and agent == 'founder' and all(v is None for v in supplied):
            return dict(owner_kind='human', owner='founder', owner_agent_id='engineering_manager',
                        origin_task_id=None, origin_thread_id=None, purpose=None,
                        retention_class='default', reference_reason=None, created_at=_iso(now),
                        expires_at=None, state='pending-attribution', attribution_status='pending')
        missing = [key for key, value in (('agent', agent), ('purpose', purpose or system_purpose),
                                         ('retention', retention)) if not value or not value.strip()]
        if not task_id and not thread_id and not system_purpose:
            missing.append('task_or_thread')
        if missing:
            raise SharedArtifactError(422, 'artifact_metadata_required', missing_fields=missing)
        invalid = {}
        if agent != 'founder' and self.known_agents is not None and agent not in self.known_agents:
            invalid['agent'] = 'unknown permanent agent ID'
        if bool(task_id) == bool(thread_id) and not (system_purpose and not task_id and not thread_id):
            invalid['origin'] = 'exactly one same-org task or thread required'
        for getter,value in ((self.db.get_task,task_id),(self.db.get_thread,thread_id)):
            if value and getter(value) is None:
                invalid['origin'] = 'unknown same-org origin'
        if retention not in ('default', 'reference'):
            invalid['retention'] = 'default or reference required'
        if retention == 'reference' and not (reason and reason.strip()):
            invalid['reason'] = 'nonblank reference reason required'
        if invalid:
            raise SharedArtifactError(422, 'artifact_metadata_invalid', fields=invalid)
        expiry = self.expiry(expires_at, retention, now)
        return dict(owner_kind='human' if agent == 'founder' else 'agent', owner=agent,
                    owner_agent_id=(self.manager_for_thread(thread_id) if thread_id else None) or 'engineering_manager' if agent == 'founder' else agent,
                    origin_task_id=task_id, origin_thread_id=thread_id,
                    purpose=(purpose or system_purpose).strip(), retention_class=retention,
                    reference_reason=reason.strip() if reason else None, created_at=_iso(now),
                    expires_at=expiry, requested_expires_at=expires_at, state='active', attribution_status='system-derived' if system_purpose else 'attributed',
                    system_derived=int(bool(system_purpose)))

    @staticmethod
    def expiry(value: str | None, retention: str, now: datetime) -> str:
        maximum = timedelta(days=REFERENCE_DAYS if retention == 'reference' else DEFAULT_DAYS)
        instant = _date(value) if value is not None else now + maximum
        if not now < instant <= now + maximum:
            raise SharedArtifactError(422, 'artifact_metadata_invalid', fields={'expires_at': 'outside bounded retention grant'})
        return _iso(instant)

    def identity(self, path: Path, *, deadline: float | None = None) -> dict:
        """No-follow literal ancestry and regular, singly linked byte identity."""
        deadline = deadline if deadline is not None else time.monotonic()+SCAN_SECONDS
        if not path.is_relative_to(self.store.root):
            raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='path_outside_store')
        for parent in (path, *path.parents):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='symlink')
            if parent == self.store.root:
                break
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='non_regular_or_alias')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        digest = hashlib.sha256()
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='identity_changed')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if time.monotonic() >= deadline:
                        raise SharedArtifactError(503,'artifact_usage_unavailable',reason='identity_scan_incomplete')
                    digest.update(chunk)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='identity_changed')
        finally:
            os.close(fd)
        return dict(device=before.st_dev, inode=before.st_ino, size_bytes=before.st_size,
                    sha256=digest.hexdigest(), mtime_ns=before.st_mtime_ns)

    def _row(self, conn: object, name: str, generation: int | None = None) -> dict | None:
        row = self.db._shared_artifact_uncommitted(conn,name,generation)
        if row is None and generation is None and conn.execute('SELECT 1 FROM shared_artifacts WHERE name=?',(name,)).fetchone():
            raise SharedArtifactError(503,'artifact_unavailable',name=name,state='reconciliation_pending',reason='generation_metadata_missing')
        return row

    @staticmethod
    def _insert(conn: object, table: str, values: dict) -> None:
        conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))

    def _event(self, row: dict, action: str, agent: str, operation_id: str | None = None, *, source: str = "manual", **extra: object) -> None:
        keys = ('generation', 'owner_kind', 'owner', 'owner_agent_id', 'origin_task_id',
                'origin_thread_id', 'state', 'reason', 'size_bytes', 'expires_at')
        mode_provider = getattr(self.db,'_shared_artifact_effective_mode',None)
        effective_mode = mode_provider(self.db._conn) if mode_provider else 'preview'
        self.audit.log_artifact_transition(name=row['name'], action=action, agent=agent,
            payload={**{key: row.get(key) for key in keys}, 'operation_id': operation_id,
                     'source': source,
                     'before_state':None,'after_state':row.get('state'),'effective_mode':effective_mode,
                     'outcome':'committed', **extra})

    def reconcile_usage_uncommitted(self, conn: object, *, deadline: float | None = None) -> tuple[int, int]:
        """Bounded complete no-follow scan. Discover legacy; never infer its TTL."""
        deadline = deadline if deadline is not None else time.monotonic() + SCAN_SECONDS
        public = {}
        internal = {}
        count = 0
        stack = [self.store.root]
        pending = conn.execute("SELECT * FROM shared_artifact_operations WHERE phase NOT IN ('committed','aborted') LIMIT ?",(SCAN_ENTRIES+1,)).fetchall()
        if len(pending) > SCAN_ENTRIES:
            raise SharedArtifactError(503,'artifact_usage_unavailable',reason='operation_scan_incomplete')
        writing = {operation['operation_id'] for operation in pending if operation['phase'] == 'reserved'}
        try:
            while stack:
                folder = stack.pop()
                if folder.is_symlink():
                    raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='symlink')
                with os.scandir(folder) as entries:
                    for entry in entries:
                        count += 1
                        if count > SCAN_ENTRIES or time.monotonic() >= deadline:
                            raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='scan_incomplete')
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                            continue
                        name = path.relative_to(self.store.root).as_posix()
                        if path.parent == self.store.root/'.shared-artifact-staging' and path.name in writing:
                            # A writer may still be filling this inode outside the SQL
                            # fence. Physical observation + its remaining reservation
                            # equals the full admitted size; do not hash changing bytes.
                            info = entry.stat(follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise SharedArtifactError(503,'artifact_usage_unavailable',reason='unsafe_staging')
                            identity = dict(device=info.st_dev,inode=info.st_ino,size_bytes=info.st_size,
                                            sha256=None,mtime_ns=info.st_mtime_ns)
                        else:
                            identity = self.identity(path,deadline=deadline)
                        if any(part.startswith('.') for part in Path(name).parts):
                            internal[name] = identity
                            continue
                        self.store.validate_name(name)
                        public[name] = identity
            reserved = 0
            for operation in pending:
                owned = json.loads(operation['filesystem_identity'] or '{}')
                staged = internal.get(f".shared-artifact-staging/{operation['operation_id']}")
                published = public.get(operation['name'])
                observed = next((item for item in (staged, published) if item and item == owned), None)
                if staged and operation['phase'] == 'reserved':
                    observed = staged
                reserved += max(0, operation['reserved_bytes'] - (observed['size_bytes'] if observed else 0))
            pending_names = {operation['name'] for operation in pending}
            for name, observed in public.items():
                if time.monotonic() >= deadline:
                    raise SharedArtifactError(503,'artifact_usage_unavailable',reason='scan_incomplete')
                row = self._row(conn, name)
                if row is None and name not in pending_names:
                    self._insert(conn, 'shared_artifacts', dict(name=name,current_generation=1,revision=1))
                    self._insert(conn, 'shared_artifact_generations', dict(name=name,generation=1,
                        state='legacy_unclassified',size_bytes=observed['size_bytes'],sha256=observed['sha256'],
                        reason='discovered_without_committed_metadata'))
                elif row and name not in pending_names and (row['sha256'],row['size_bytes']) != (observed['sha256'],observed['size_bytes']):
                    raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='known_bytes_changed', name=name)
            # Missing known bytes must not become a silent quota discount.
            for row in conn.execute("SELECT g.* FROM shared_artifacts a JOIN shared_artifact_generations g ON g.name=a.name AND g.generation=a.current_generation WHERE g.state!='deleted'"):
                if row['name'] not in public and row['name'] not in pending_names:
                    raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='known_bytes_missing', name=row['name'])
            used = sum(i['size_bytes'] for i in (*public.values(), *internal.values()))
            conn.execute("UPDATE shared_artifact_org_state SET reconciliation_status='complete' WHERE id=1")
            return used, reserved
        except (OSError, ValueError) as exc:
            if isinstance(exc, SharedArtifactError):
                raise
            raise SharedArtifactError(503, 'artifact_usage_unavailable', reason='scan_unavailable') from exc

    def _admit(self, conn: object, name: str, size: int, agent: str) -> None:
        used, reserved = self.reconcile_usage_uncommitted(conn)
        payload = dict(name=name, used_bytes=used, reserved_bytes=reserved, requested_bytes=size,
                       quota_bytes=QUOTA_BYTES, advice='Use supported renew/retire/classification workflows; downloads remain available.')
        if used + reserved + size > QUOTA_BYTES:
            raise SharedArtifactError(413, 'artifact_quota_exceeded', **payload)
        self.audit.log_artifact_quota(action='artifact_quota_check', agent=agent, payload=payload)

    def _live_dependency(self, conn: object, name: str) -> bool:
        """B supplies a complete reference proof under this same writer fence.

        No scanner installed is indeterminate, never proof of absence. This
        source-only A seam cannot activate destructive replacement by itself.
        """
        row = self._row(conn,name)
        checker = getattr(self.db,'_shared_artifact_reference_checker',None)
        if checker is None:
            raise SharedArtifactError(503,'artifact_reference_check_unavailable',name=name)
        try:
            outcome = checker(conn,row)
        except Exception as exc:
            raise SharedArtifactError(503,'artifact_reference_check_unavailable',name=name) from exc
        if outcome not in ('absent','live'):
            raise SharedArtifactError(503,'artifact_reference_check_unavailable',name=name)
        return outcome == 'live'

    def _prepare_row(self, conn: object, name: str, content: bytes, metadata: dict, operation_id: str, *, custom: bool = False) -> dict:
        self.store.validate_name(name)
        self.store._check_case_collision(name)
        target = self.store.path_for(name)
        if target.is_dir():
            raise InvalidArtifactName(f'name_collides_with_existing_artifact: {name!r}')
        parent = target.parent
        while parent != self.store.root:
            if parent.exists() and not parent.is_dir():
                raise InvalidArtifactName(f'name_collides_with_existing_artifact: {name!r}')
            parent = parent.parent
        for table,key in (('tasks','origin_task_id'),('threads','origin_thread_id')):
            if metadata.get(key) and not conn.execute(f'SELECT 1 FROM {table} WHERE id=?',(metadata[key],)).fetchone():
                raise SharedArtifactError(422,'artifact_metadata_invalid',fields={'origin':'unknown same-org origin'})
        if len(content) > MAX_ARTIFACT_BYTES:
            raise SharedArtifactError(413, 'artifact_too_large', max_bytes=MAX_ARTIFACT_BYTES)
        if conn.execute("SELECT 1 FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')",(name,)).fetchone():
            raise SharedArtifactError(503,'artifact_operation_pending',name=name)
        self._admit(conn, name, len(content), metadata['owner'])
        current = self._row(conn, name)
        if current:
            if current['state'] != 'active' or custom:
                raise SharedArtifactError(409, 'artifact_name_retained', name=name)
            if self._live_dependency(conn, name):
                raise SharedArtifactError(409, 'artifact_live_dependency', name=name)
        generation = current['generation'] + 1 if current else 1
        row = dict(name=name, generation=generation, **metadata, size_bytes=len(content),
                   sha256=hashlib.sha256(content).hexdigest())
        self._insert(conn, 'shared_artifact_generations', row)
        now = _iso(self.clock())
        self._insert(conn, 'shared_artifact_operations', dict(operation_id=operation_id,name=name,
            generation=generation,expected_revision=current['revision'] if current else 0,
            expected_hash=current['sha256'] if current else None,operation='put',phase='reserved',
            reserved_bytes=len(content),before_state=current['state'] if current else None,
            after_state=row['state'],request_digest=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest(),
            created_at=now,updated_at=now,metadata_json=json.dumps(row)))
        if not current:
            self._insert(conn,'shared_artifacts',dict(name=name,current_generation=generation,revision=1))
        self._event(row,'artifact_put_intent',metadata['owner'],operation_id,outcome='reserved')
        return row

    def put(self, name: str, content: bytes, metadata: dict, *, operation_id: str | None = None) -> dict:
        operation_id = operation_id or str(uuid.uuid4())
        # UUID prevents paths/aliases in the private journal namespace.
        try:
            if str(uuid.UUID(operation_id)) != operation_id:
                raise ValueError
        except ValueError as exc:
            raise SharedArtifactError(422,'artifact_metadata_invalid',fields={'operation_id':'canonical UUID required'}) from exc
        with self.db.shared_artifact_transaction() as conn:
            prior = conn.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(operation_id,)).fetchone()
            if prior:
                saved = json.loads(prior['metadata_json'])
                if saved['name'] != name or saved['sha256'] != hashlib.sha256(content).hexdigest() or any(saved.get(k) != v for k,v in metadata.items() if k not in ('created_at','expires_at')):
                    raise SharedArtifactError(409,'artifact_operation_conflict')
                if prior['phase'] == 'committed':
                    return {**self.projection(saved),'operation_id':operation_id}
                if prior['phase'] != 'aborted':
                    raise SharedArtifactError(503,'artifact_operation_pending',operation_id=operation_id)
                self._admit(conn,name,len(content),metadata['owner'])
                conn.execute("UPDATE shared_artifact_operations SET phase='reserved',reserved_bytes=?,failure=NULL,filesystem_identity=NULL WHERE operation_id=?",(len(content),operation_id))
                conn.execute('UPDATE shared_artifact_generations SET state=?,reason=NULL,deleted_at=NULL WHERE name=? AND generation=?',(saved['state'],name,saved['generation']))
                row = saved
            else:
                row = self._prepare_row(conn,name,content,metadata,operation_id)
        stage = self.store.root / '.shared-artifact-staging' / operation_id
        local_identity = None
        try:
            stage.parent.mkdir(exist_ok=True)
            if stage.parent.is_symlink():
                raise SharedArtifactError(503,'artifact_usage_unavailable',reason='staging_symlink')
            with stage.open('xb') as stream:
                empty_identity = self.identity(stage)
                local_identity = empty_identity
                with self.db.shared_artifact_transaction() as conn:
                    conn.execute('UPDATE shared_artifact_operations SET filesystem_identity=?,updated_at=? WHERE operation_id=?',
                                 (json.dumps(empty_identity),_iso(self.clock()),operation_id))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            identity = self.identity(stage)
            with self.db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE shared_artifact_operations SET phase='prepared',filesystem_identity=?,updated_at=? WHERE operation_id=? AND phase='reserved'",(json.dumps(identity),_iso(self.clock()),operation_id))
                self._event(row,'artifact_put_intent',metadata['owner'],operation_id,outcome='prepared')
            target = self.store.path_for(name)
            target.parent.mkdir(parents=True,exist_ok=True)
            with self.db.shared_artifact_transaction() as conn:
                # Keep the writer fence through exact identity/CAS and publication.
                operation = conn.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(operation_id,)).fetchone()
                current = self._row(conn,name)
                if not operation['expected_hash'] and target.exists():
                    raise SharedArtifactError(503,'artifact_usage_unavailable',reason='target_occupied')
                if operation['expected_revision'] and current['revision'] != operation['expected_revision']:
                    raise SharedArtifactError(409,'artifact_revision_changed',name=name)
                if operation['expected_hash'] and self.identity(target)['sha256'] != operation['expected_hash']:
                    raise SharedArtifactError(503,'artifact_usage_unavailable',reason='replacement_identity_changed')
                if operation['expected_hash'] and self._live_dependency(conn,name):
                    raise SharedArtifactError(409,'artifact_live_dependency',name=name)
                if self.identity(stage) != identity:
                    raise SharedArtifactError(503,'artifact_usage_unavailable',reason='staging_identity_changed')
                self.store._check_case_collision(name)
                os.replace(stage,target)
                self._finalize_put(conn,operation,row)
        except BaseException as exc:
            # Never remove public bytes after rename; its durable intent must reconcile.
            with self.db.shared_artifact_transaction() as conn:
                self._abort_owned_stage_uncommitted(conn,operation_id,local_identity=local_identity)
                conn.execute('UPDATE shared_artifact_operations SET failure=?,updated_at=? WHERE operation_id=?',
                             (type(exc).__name__,_iso(self.clock()),operation_id))
                self._event(row,'artifact_operation_failed',metadata['owner'],operation_id,outcome='uncertain')
            raise
        return {**self.projection(row),'operation_id':operation_id}

    def _abort_owned_stage_uncommitted(self, conn: object, operation_id: str, *, local_identity: dict | None = None) -> bool:
        operation = conn.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(operation_id,)).fetchone()
        expected = json.loads(operation['filesystem_identity'] or '{}') or local_identity
        stage = self.store.root/'.shared-artifact-staging'/operation_id
        target = self.store.path_for(operation['name'])
        # A missing stage after publication is uncertainty, never rollback permission.
        if not stage.exists():
            if expected and target.exists() and self.identity(target) == expected:
                return False
            if operation['phase'] != 'reserved':
                return False
        else:
            observed = self.identity(stage)
            if not expected or (observed['device'],observed['inode']) != (expected['device'],expected['inode']):
                return False
            if operation['phase'] == 'prepared' and observed != expected:
                return False
            if operation['phase'] == 'reserved' and local_identity is None:
                intended = json.loads(operation['metadata_json'] or '{}')
                hashes = {(expected.get('size_bytes'),expected.get('sha256')),
                          (intended.get('size_bytes'),intended.get('sha256'))}
                if (observed['size_bytes'],observed['sha256']) not in hashes:
                    return False
            stage.unlink()
        conn.execute("UPDATE shared_artifact_operations SET phase='aborted',reserved_bytes=0 WHERE operation_id=?",(operation_id,))
        conn.execute("UPDATE shared_artifact_generations SET state='deleted',reason='upload_aborted',deleted_at=? WHERE name=? AND generation=?",(_iso(self.clock()),operation['name'],operation['generation']))
        return True

    def _finalize_put(self, conn: object, operation: object, row: dict) -> None:
        if operation['expected_revision']:
            conn.execute("UPDATE shared_artifact_generations SET state='deleted',reason='replaced',deleted_at=? WHERE name=? AND generation=?",(_iso(self.clock()),row['name'],row['generation']-1))
            conn.execute('UPDATE shared_artifacts SET current_generation=?,revision=revision+1 WHERE name=?',(row['generation'],row['name']))
        conn.execute("UPDATE shared_artifact_operations SET phase='committed',reserved_bytes=0,updated_at=?,failure=NULL WHERE operation_id=?",(_iso(self.clock()),operation['operation_id']))
        self._event(row,'artifact_put',row['owner'],operation['operation_id'],outcome='committed')
        if row['state'] == 'pending-attribution':
            self._event(row,'artifact_attribution_pending',row['owner'],operation['operation_id'])

    def reserve_compose_uploads(self, uploads: list[dict]) -> None:
        """Persist custody before the caller BEGIN can publish any bytes.

        Caller holds Database._lock through these receipts, the composite
        transaction and compensation. A rollback restores the durable intents.
        """
        if not uploads:
            return
        for owned in uploads:
            try:
                owned['path'].lstat()
            except FileNotFoundError:
                continue
            raise SharedArtifactError(503,'artifact_operation_pending',reason='compose_target_occupied')
        with self.db.shared_artifact_transaction() as conn:
            for owned in uploads:
                if conn.execute("SELECT 1 FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')",(owned['name'],)).fetchone():
                    raise SharedArtifactError(503,'artifact_operation_pending',name=owned['name'])
                metadata = dict(owner=owned['owner'],size_bytes=len(owned['content']),
                    sha256=hashlib.sha256(owned['content']).hexdigest(),
                    thread_id=owned.get('thread_id'),attachment_id=owned.get('attachment_id'))
                self._insert(conn,'shared_artifact_operations',dict(operation_id=owned['operation_id'],
                    name=owned['name'],generation=1,operation='compose_private' if owned['private'] else 'compose',
                    phase='reserved',reserved_bytes=0,created_at=_iso(self.clock()),updated_at=_iso(self.clock()),
                    metadata_json=json.dumps(metadata)))

    @staticmethod
    def private_compose_identity(path: Path) -> tuple:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('private compose identity unavailable')
        return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)

    def compensate_compose_uploads(self, uploads: list[dict], *, private_store: object) -> None:
        if not uploads:
            return
        with self.db.shared_artifact_transaction() as conn:
            for owned in uploads:
                operation = conn.execute('SELECT phase FROM shared_artifact_operations WHERE operation_id=?',
                    (owned['operation_id'],)).fetchone()
                # A COMMIT that took effect before raising is not permission to
                # compensate committed bytes (private success removes its intent).
                if operation is None or operation['phase'] == 'committed':
                    continue
                path = owned['path']
                try:
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        if owned['identity'] is None:
                            raise OSError('compose identity was not captured')
                        observed = self.private_compose_identity(path) if owned['private'] else self.identity(path)
                        if observed != owned['identity']:
                            raise OSError('compose identity changed')
                        if owned['private']:
                            private_store.delete(owned['thread_id'],owned['attachment_id'])
                        else:
                            self.store.delete(owned['name'])
                        try:
                            path.lstat()
                        except FileNotFoundError:
                            pass
                        else:
                            raise OSError('compose cleanup did not remove bytes')
                except (OSError,SharedArtifactError):
                    conn.execute("UPDATE shared_artifact_operations SET phase='uncertain',failure='compose_compensation_unproven',updated_at=? WHERE operation_id=?",
                        (_iso(self.clock()),owned['operation_id']))
                else:
                    conn.execute('DELETE FROM shared_artifact_operations WHERE operation_id=?',(owned['operation_id'],))

    def admit_in_caller_transaction(
        self, conn: object, *, name: str, content: bytes, actor_kind: str, actor: str,
        skill_slug: str, version_id: int, task_id: str | None,
    ) -> dict:
        """Custom-skill version INSERT already happened. NEVER BEGIN/COMMIT here.

        Caller owns blob write and compensation through COMMIT. This single named
        system-derived origin exception does not exempt quota/journaling/expiry.
        """
        if conn is not self.db._conn or not conn.in_transaction:
            raise RuntimeError('artifact_admission_requires_caller_transaction')
        metadata = self.metadata(agent='founder' if actor_kind == 'human' else actor,
            task_id=task_id, purpose=f'canonical skill {skill_slug} version {version_id}',
            retention='default', system_purpose=f'canonical skill {skill_slug} version {version_id}' if not task_id else None)
        metadata['protected_source'] = 1
        operation_id = str(uuid.uuid4())
        row = self._prepare_row(conn,name,content,metadata,operation_id,custom=True)
        conn.execute("UPDATE shared_artifact_operations SET phase='committed',reserved_bytes=0 WHERE operation_id=?",(operation_id,))
        self._event(row,'artifact_put',metadata['owner'],operation_id,outcome='caller_commit_pending')
        return row

    def reconcile_operation(self, operation_id: str) -> dict:
        """Retry only positively identified journaled publication; never a peer file.

        Reserved-without-identity cannot prove staging custody: hold it. A prepared
        exact inode at target finalizes without another rename/generation/event.
        """
        with self.db.shared_artifact_transaction() as conn:
            operation = conn.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(operation_id,)).fetchone()
            if not operation:
                raise SharedArtifactError(404,'artifact_operation_not_found')
            if operation['operation'] in ('compose','compose_private'):
                raise SharedArtifactError(503,'artifact_operation_pending',operation_id=operation_id,reason='exclusive_owned_recovery_required')
            row = json.loads(operation['metadata_json'])
            if operation['phase'] == 'committed':
                return self.projection(row)
            expected = json.loads(operation['filesystem_identity'] or '{}')
            target = self.store.path_for(operation['name'])
            if expected and target.exists() and self.identity(target) == expected:
                self._finalize_put(conn,operation,row)
                self._event(row,'artifact_operation_reconciled','daemon',operation_id,source='system',outcome='committed')
                return self.projection(row)
            stage = self.store.root/'.shared-artifact-staging'/operation_id
            if operation['phase'] == 'prepared' and expected and stage.exists() and self.identity(stage) == expected:
                if operation['expected_hash']:
                    if self.identity(target)['sha256'] != operation['expected_hash'] or self._live_dependency(conn,operation['name']):
                        raise SharedArtifactError(503,'artifact_operation_pending',reason='replacement_fence_changed')
                elif target.exists():
                    raise SharedArtifactError(503,'artifact_operation_pending',reason='target_occupied')
                target.parent.mkdir(parents=True,exist_ok=True)
                os.replace(stage,target)
                self._finalize_put(conn,operation,row)
                self._event(row,'artifact_operation_reconciled','daemon',operation_id,source='system',outcome='committed')
                return self.projection(row)
            if operation['phase'] == 'reserved' and self._abort_owned_stage_uncommitted(conn,operation_id):
                self._event(row,'artifact_operation_reconciled','daemon',operation_id,source='system',outcome='aborted')
                return {**self.projection(row),'state':'aborted'}
            raise SharedArtifactError(503,'artifact_operation_pending',operation_id=operation_id,reason='exclusive_owned_recovery_required')

    def _timeout(self, conn: object, row: dict) -> dict:
        if row['state'] == 'pending-attribution' and not row.get('timeout_at') and self.clock() >= _date(row['created_at']) + timedelta(hours=PROVISIONAL_HOURS):
            timeout = _date(row['created_at']) + timedelta(hours=PROVISIONAL_HOURS)
            row.update(expires_at=_iso(_date(row['created_at'])+timedelta(days=DEFAULT_DAYS)),timeout_at=_iso(timeout))
            conn.execute('UPDATE shared_artifact_generations SET expires_at=?,timeout_at=? WHERE name=? AND generation=?',(row['expires_at'],row['timeout_at'],row['name'],row['generation']))
            conn.execute('INSERT OR IGNORE INTO shared_artifact_notice_receipts(name,generation,expires_at,notice_kind,delivery_state,payload_json) VALUES(?,?,?,?,?,?)',
                (row['name'],row['generation'],row['expires_at'],'attribution_timeout','pending',json.dumps({'owner_agent_id':row['owner_agent_id'],'timeout_at':row['timeout_at']})))
            self._event(row,'artifact_attribution_timeout','daemon',source='system',outcome='routing_pending')
        return row

    def lookup(self, name: str, generation: int | None = None, *, conn: object | None = None) -> dict:
        self.store.validate_name(name)
        if conn is None:
            refusal = None
            with self.db.shared_artifact_transaction() as owned:
                try:
                    result = self.lookup(name,generation,conn=owned)
                except SharedArtifactError as exc:
                    if exc.status != 410:
                        raise
                    refusal = exc
            if refusal:
                raise refusal
            return result
        row = self._row(conn,name,generation)
        if not row:
            pending = conn.execute("SELECT operation_id FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')",(name,)).fetchone()
            if pending:
                raise SharedArtifactError(503,'artifact_operation_pending',name=name,operation_id=pending['operation_id'],reason='compose_compensation_unproven')
            if generation is not None:
                raise SharedArtifactError(404,'artifact_not_found',name=name,generation=generation)
            path = self.store.path_for(name)
            if path.is_file():
                observed = self.identity(path)
                self._insert(conn,'shared_artifacts',dict(name=name,current_generation=1,revision=1))
                self._insert(conn,'shared_artifact_generations',dict(name=name,generation=1,
                    state='legacy_unclassified',size_bytes=observed['size_bytes'],sha256=observed['sha256'],
                    reason='discovered_without_committed_metadata'))
            else:
                self.reconcile_usage_uncommitted(conn)
            row = self._row(conn,name)
            if not row:
                raise SharedArtifactError(404,'artifact_not_found',name=name)
        if row['state'] not in ('active','recoverable','deleted','pending-attribution','legacy_unclassified'):
            raise SharedArtifactError(503,'artifact_unavailable',name=name,state='reconciliation_pending',reason='unknown_metadata_state')
        row = self._timeout(conn,row)
        pending = conn.execute("SELECT operation_id,operation FROM shared_artifact_operations WHERE name=? AND generation=? AND phase NOT IN ('committed','aborted')",(name,row['generation'])).fetchone()
        if pending:
            raise SharedArtifactError(503,'artifact_operation_pending',**{**self.projection(row),'operation_id':pending['operation_id'],
                'state':'publication_pending' if pending['operation'] == 'put' else row['state'],
                'reason':'publication_incomplete' if pending['operation'] == 'put' else row['reason']})
        # B installs the complete same-transaction mode/reference proof adapter.
        checker = getattr(self.db,'_shared_artifact_reference_checker',None)
        mode = getattr(self.db,'_shared_artifact_effective_mode',None)
        if row['state'] in ('active','pending-attribution') and row['expires_at'] and self.clock() >= _date(row['expires_at']) and checker and mode:
            try:
                outcome = checker(conn,row)
            except Exception:
                outcome = 'indeterminate'
            transition = self.lifecycle_transition_uncommitted(conn,name=name,generation=row['generation'],
                expected_revision=row['revision'],action='expire',reason='retention_expired',
                reference_outcome=outcome,effective_mode=mode(conn),operation_id=str(uuid.uuid4()))
            if outcome not in ('absent','live') and transition.get('effective_mode') != 'preview':
                raise SharedArtifactError(503,'artifact_unavailable',**{**self.projection(row),'state':'reconciliation_pending','reason':'dependency_check_unavailable'})
            row = self._row(conn,name,generation)
        if row['state'] in ('recoverable','deleted'):
            raise SharedArtifactError(410,'artifact_unavailable',**self.projection(row))
        try:
            identity = self.identity(self.store.path_for(name))
        except FileNotFoundError as exc:
            raise SharedArtifactError(410,'artifact_unavailable',**{**self.projection(row),'state':'storage_missing','reason':'storage_missing','can_restore':False}) from exc
        if (identity['sha256'],identity['size_bytes']) != (row['sha256'],row['size_bytes']):
            raise SharedArtifactError(503,'artifact_unavailable',**{**self.projection(row),'state':'reconciliation_pending','reason':'known_bytes_changed'})
        row['_filesystem_identity'] = identity
        # A does not activate expiry. B owns mode/reference-fenced transitions.
        # A date alone never creates an unrecorded unavailable/recovery interval.
        return row

    def open_download(self, name: str, generation: int | None = None) -> tuple[BinaryIO,dict]:
        """Pin verified bytes before releasing the generation writer fence."""
        self.lookup(name,generation)
        stream = None
        try:
            with self.db.shared_artifact_transaction() as conn:
                row = self.lookup(name,generation,conn=conn)
                expected = row['_filesystem_identity']
                fd = os.open(self.store.path_for(name),os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    opened = os.fstat(fd)
                    if (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns) != (expected['device'],expected['inode'],expected['size_bytes'],expected['mtime_ns']):
                        raise SharedArtifactError(503,'artifact_unavailable',name=name,state='reconciliation_pending',reason='download_identity_changed')
                    stream = os.fdopen(fd,'rb')
                except BaseException:
                    os.close(fd)
                    raise
                try:
                    metadata = self.projection(row)
                except BaseException:
                    stream.close()
                    raise
        except BaseException:
            if stream is not None:
                stream.close()
            raise
        return stream,metadata

    def bind_uncommitted(self, conn: object, *, name: str, generation: int | None, thread_id: str) -> dict:
        row = self.lookup(name,generation,conn=conn)
        if row['state'] != 'pending-attribution':
            return row
        if row['expires_at'] and self.clock() >= _date(row['expires_at']):
            raise SharedArtifactError(409,'artifact_restore_required',**self.projection(row))
        now = _iso(self.clock())
        manager = self.manager_for_thread(thread_id) or 'engineering_manager'
        row.update(origin_thread_id=thread_id,purpose='thread attachment',state='active',retention_class='default',reference_reason=None,
                   attribution_status='bound',bound_at=now,owner_agent_id=manager,
                   expires_at=_iso(self.clock()+timedelta(days=DEFAULT_DAYS)))
        conn.execute("UPDATE shared_artifact_generations SET origin_thread_id=?,purpose=?,state='active',retention_class='default',reference_reason=NULL,attribution_status='bound',bound_at=?,owner_agent_id=?,expires_at=? WHERE name=? AND generation=?",(thread_id,row['purpose'],now,manager,row['expires_at'],name,row['generation']))
        conn.execute('UPDATE shared_artifacts SET revision=revision+1 WHERE name=?',(name,))
        self._event(row,'artifact_attribution_bound','founder',before_state='pending-attribution',after_state='active')
        return row

    def renew(self, name: str, *, agent: str, continuing_use: str, expires_at: str,
              retention: str | None = None, reason: str | None = None, restore: bool = False) -> dict:
        self.store.validate_name(name)
        if not agent.strip() or not continuing_use.strip():
            raise SharedArtifactError(422,'artifact_metadata_required',missing_fields=['agent','continuing_use'])
        with self.db.shared_artifact_transaction() as conn:
            row = self._row(conn,name)
            if not row:
                raise SharedArtifactError(404,'artifact_not_found',name=name)
            if conn.execute("SELECT 1 FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')",(name,)).fetchone():
                raise SharedArtifactError(503,'artifact_operation_pending',name=name)
            if row['state'] == 'deleted':
                raise SharedArtifactError(410,'artifact_unavailable',**self.projection(row))
            if restore:
                if row['state'] != 'recoverable' or not row['recoverable_until'] or self.clock() >= _date(row['recoverable_until']):
                    raise SharedArtifactError(409,'artifact_restore_unavailable',**self.projection(row))
                try:
                    identity = self.identity(self.store.path_for(name))
                except FileNotFoundError as exc:
                    raise SharedArtifactError(410,'artifact_unavailable',**{**self.projection(row),'state':'storage_missing','reason':'storage_missing','can_restore':False}) from exc
                if identity['sha256'] != row['sha256']:
                    raise SharedArtifactError(503,'artifact_unavailable',**{**self.projection(row),'state':'reconciliation_pending','reason':'restore_hash_changed'})
            elif row['state'] == 'legacy_unclassified':
                raise SharedArtifactError(409,'artifact_classification_required',**self.projection(row))
            elif row['state'] != 'active':
                raise SharedArtifactError(409,'artifact_restore_required',**self.projection(row))
            selected = retention or row['retention_class']
            selected_reason = reason if reason is not None else row['reference_reason']
            if selected not in ('default','reference') or (selected == 'reference' and not (selected_reason and selected_reason.strip())):
                raise SharedArtifactError(422,'artifact_metadata_invalid',fields={'retention':'reference requires named owner and reason'})
            expiry = self.expiry(expires_at,selected,self.clock())
            before = row['expires_at']
            previous_deadline = row['recoverable_until']
            previous_state = row['state']
            next_state = 'pending-attribution' if row['attribution_status'] == 'pending' and not row['bound_at'] else 'active'
            conn.execute("UPDATE shared_artifact_generations SET state=?,expires_at=?,renewed_at=?,continuing_use=?,retention_class=?,reference_reason=?,reason=NULL,expired_on=NULL,recovery_started_at=NULL,recoverable_until=NULL WHERE name=? AND generation=?",(next_state,expiry,_iso(self.clock()),continuing_use,selected,selected_reason,name,row['generation']))
            conn.execute('UPDATE shared_artifacts SET revision=revision+1 WHERE name=?',(name,))
            row.update(state=next_state,expires_at=expiry,retention_class=selected,reference_reason=selected_reason,
                       expired_on=None,recovery_started_at=None,recoverable_until=None,reason=None,renewed_at=_iso(self.clock()))
            self._event(row,'artifact_restore' if restore else 'artifact_renew',agent,continuing_use=continuing_use,previous_expiry=before,previous_deadline=previous_deadline,
                        verified_hash=row['sha256'] if restore else None,before_state=previous_state,outcome='committed')
            return self.projection(row)

    def projection(self, row: dict) -> dict:
        keys = ('name','generation','owner_kind','owner','owner_agent_id','origin_task_id','origin_thread_id',
                'purpose','retention_class','reference_reason','expires_at','state','expired_on',
                'recovery_started_at','recoverable_until','deleted_at','reason','hold_reason','size_bytes',
                'attribution_status','bound_at','timeout_at')
        return {**{key:row.get(key) for key in keys},
                'can_restore': bool(row.get('state') == 'recoverable' and row.get('recoverable_until') and self.clock() < _date(row['recoverable_until'])),
                'modified_at':row.get('renewed_at') or row.get('created_at'),
                'download_url':f"{self.download_prefix}/{row['name']}?generation={row['generation']}" if row.get('generation') else f"{self.download_prefix}/{row['name']}"}

    def retire(self, name: str, *, agent: str) -> dict:
        """Manual DELETE uses B's same mode/reference gates; never unlinks here."""
        self.lookup(name)
        with self.db.shared_artifact_transaction() as conn:
            row = self._row(conn,name)
            checker = getattr(self.db,'_shared_artifact_reference_checker',None)
            mode = getattr(self.db,'_shared_artifact_effective_mode',None)
            return self.lifecycle_transition_uncommitted(conn,name=name,generation=row['generation'],
                expected_revision=row['revision'],action='retire',reason='manual_retirement',
                reference_outcome=checker(conn,row) if checker else 'indeterminate',
                effective_mode=mode(conn) if mode else 'preview',operation_id=str(uuid.uuid4()),actor=agent,operation_source='manual')

    def prepare_delete_uncommitted(
        self, conn: object, *, name: str, generation: int, expected_revision: int,
        reference_outcome: str, effective_mode: str, operation_id: str,
        deadline: float | None = None,
    ) -> dict:
        """B persists this exact intent before its separately fenced unlink."""
        if conn is not self.db._conn or not conn.in_transaction:
            raise RuntimeError('artifact_delete_intent_requires_transaction')
        row = self._row(conn,name,generation)
        rollout = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
        if (not row or row['revision'] != expected_revision or row['state'] != 'recoverable'
            or not row['recoverable_until'] or self.clock() < _date(row['recoverable_until'])
            or effective_mode != 'enforce' or reference_outcome != 'absent'
            or not rollout['preview_acceptance'] or not rollout['inventory_review_id']
            or not rollout['source_protection_verified'] or not rollout['rollout_started_at']
            or self.clock() < _date(rollout['rollout_started_at'])+timedelta(days=RECOVERY_DAYS)):
            raise SharedArtifactError(409,'artifact_delete_held')
        prior = conn.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(operation_id,)).fetchone()
        if prior:
            if (prior['name'],prior['generation'],prior['operation']) != (name,generation,'delete'):
                raise SharedArtifactError(409,'artifact_operation_conflict')
            return json.loads(prior['filesystem_identity'])
        identity = self.identity(self.store.path_for(name),deadline=deadline)
        if (identity['sha256'],identity['size_bytes']) != (row['sha256'],row['size_bytes']):
            raise SharedArtifactError(503,'artifact_usage_unavailable',reason='delete_identity_changed')
        now = _iso(self.clock())
        self._insert(conn,'shared_artifact_operations',dict(operation_id=operation_id,name=name,
            generation=generation,expected_revision=expected_revision,expected_hash=row['sha256'],
            operation='delete',phase='issued',reserved_bytes=0,before_state='recoverable',
            after_state='deleted',filesystem_identity=json.dumps(identity),created_at=now,updated_at=now))
        self._event(row,'artifact_delete_intent','daemon',operation_id,source='system',outcome='issued',effective_mode='enforce')
        return identity

    def quota_crossing_uncommitted(self, conn: object, *, deadline: float | None = None) -> dict:
        """B's durable crossing identity; delivery remains pending until its transaction."""
        used,reserved = self.reconcile_usage_uncommitted(conn,deadline=deadline)
        state = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
        above = used+reserved >= ALERT_BYTES
        sequence = state['crossing_sequence']
        crossing = above and not state['quota_above']
        if crossing:
            sequence += 1
        conn.execute('UPDATE shared_artifact_org_state SET quota_above=?,crossing_sequence=? WHERE id=1',
                     (int(above),sequence))
        return dict(used_bytes=used,reserved_bytes=reserved,quota_bytes=QUOTA_BYTES,
                    above=above,crossing=crossing,crossing_sequence=sequence)

    def lifecycle_transition_uncommitted(
        self, conn: object, *, name: str, generation: int, expected_revision: int,
        action: str, reason: str, reference_outcome: str, effective_mode: str,
        operation_id: str, actor: str = 'daemon', operation_source: str = 'system',
        reclaimed_bytes: int | None = None,
    ) -> dict:
        """B's state-only fence. Never unlinks bytes or activates a scheduler.

        B must obtain its complete reference proof under this same writer fence.
        A requires the persisted rollout gates as well as that negative proof.
        """
        if not conn.in_transaction:
            raise RuntimeError('artifact_transition_requires_transaction')
        row = self._row(conn,name,generation)
        if not row:
            raise SharedArtifactError(404,'artifact_not_found',name=name)
        if row['revision'] != expected_revision:
            raise SharedArtifactError(409,'artifact_revision_changed',name=name)
        if action == 'timeout':
            return self._timeout(conn,row)
        rollout = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
        allowed = (effective_mode == 'enforce' and rollout['preview_acceptance']
                   and rollout['inventory_review_id'] and rollout['source_protection_verified']
                   and rollout['rollout_started_at']
                   and self.clock() >= _date(rollout['rollout_started_at'])+timedelta(days=RECOVERY_DAYS))
        if not allowed or row['state'] == 'legacy_unclassified':
            self._event(row,'artifact_preview',actor,operation_id,source=operation_source,mutated=False,
                        effective_mode='preview',outcome='would_'+action,reason=reason)
            return {**self.projection(row),'mutated':False,'effective_mode':'preview'}
        if reference_outcome != 'absent':
            conn.execute('UPDATE shared_artifact_generations SET hold_reason=? WHERE name=? AND generation=?',
                         (reason,name,generation))
            self._event(row,'artifact_protected',actor,operation_id,source=operation_source,reason=reason,
                        outcome=reference_outcome)
            return {**self.projection(row),'protected':reference_outcome == 'live','mutated':False}
        now = _iso(self.clock())
        if action in ('expire','retire'):
            if row['state'] not in ('active','pending-attribution') or (action == 'expire' and (not row['expires_at'] or self.clock() < _date(row['expires_at']))):
                raise SharedArtifactError(409,'artifact_transition_invalid')
            deadline = _iso(self.clock()+timedelta(days=RECOVERY_DAYS))
            conn.execute("UPDATE shared_artifact_generations SET state='recoverable',expired_on=?,recovery_started_at=?,recoverable_until=?,reason=?,hold_reason=NULL WHERE name=? AND generation=?",
                         (row['expires_at'] if action == 'expire' else now,now,deadline,reason,name,generation))
            row.update(state='recoverable',expired_on=row['expires_at'] if action == 'expire' else now,recovery_started_at=now,
                       recoverable_until=deadline,reason=reason,hold_reason=None)
        elif action == 'deleted':
            if row['state'] != 'recoverable' or self.clock() < _date(row['recoverable_until']):
                raise SharedArtifactError(409,'artifact_transition_invalid')
            if self.store.path_for(name).exists():
                raise SharedArtifactError(409,'artifact_bytes_still_present')
            # B owns the journaled unlink and must retain its exact intent identity.
            intent = conn.execute("SELECT 1 FROM shared_artifact_operations WHERE operation_id=? AND name=? AND generation=? AND operation='delete' AND phase='issued'",(operation_id,name,generation)).fetchone()
            if not intent:
                raise SharedArtifactError(409,'artifact_delete_intent_required')
            conn.execute("UPDATE shared_artifact_generations SET state='deleted',deleted_at=?,reason=?,hold_reason=NULL WHERE name=? AND generation=?",(now,reason,name,generation))
            row.update(state='deleted',deleted_at=now,reason=reason,hold_reason=None)
            conn.execute("UPDATE shared_artifact_operations SET phase='committed',updated_at=? WHERE operation_id=?",(now,operation_id))
        else:
            raise SharedArtifactError(422,'artifact_transition_invalid')
        conn.execute('UPDATE shared_artifacts SET revision=revision+1 WHERE name=?',(name,))
        self._event(row,'artifact_expire' if action in ('expire','retire') else 'artifact_delete',actor,operation_id,source=operation_source,
                    effective_mode=effective_mode,outcome='committed',
                    **({'reclaimed_bytes':reclaimed_bytes} if reclaimed_bytes is not None else {}))
        return {**self.projection(row),'mutated':True}

    def record_inventory_review_uncommitted(self, conn: object, *, review: dict) -> None:
        """D's immutable review receipt; validation of manifests belongs to D."""
        if not conn.in_transaction:
            raise RuntimeError('artifact_review_requires_transaction')
        self._insert(conn,'shared_artifact_inventory_reviews',review)

    def classify_legacy_uncommitted(self, conn: object, *, name: str, metadata: dict,
                                    review_id: str, inventory_digest: str, expected_hash: str,
                                    expected_size: int | None = None) -> dict:
        """D applies an exactly reviewed item after both inventory and preview gates."""
        if conn is not self.db._conn or not conn.in_transaction:
            raise RuntimeError('artifact_classification_requires_transaction')
        review = conn.execute('SELECT * FROM shared_artifact_inventory_reviews WHERE review_id=?',(review_id,)).fetchone()
        rollout = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
        if (not review or review['inventory_digest'] != inventory_digest or not rollout['preview_acceptance']
            or not rollout['rollout_started_at'] or self.clock() < _date(rollout['rollout_started_at'])+timedelta(days=RECOVERY_DAYS)):
            raise SharedArtifactError(409,'artifact_classification_review_required')
        row = self._row(conn,name)
        if not row or row['state'] != 'legacy_unclassified':
            raise SharedArtifactError(409,'artifact_classification_invalid')
        self.store.validate_name(name)
        observed = self.identity(self.store.root / name)
        if (row['sha256'] != expected_hash or observed['sha256'] != expected_hash
            or (expected_size is not None and (row['size_bytes'] != expected_size or observed['size_bytes'] != expected_size))):
            raise SharedArtifactError(409,'artifact_inventory_changed')
        permitted = {'owner_kind','owner','owner_agent_id','origin_task_id','origin_thread_id',
                     'purpose','retention_class','reference_reason','created_at','expires_at','state','attribution_status','requested_expires_at'}
        if not set(metadata).issubset(permitted):
            raise SharedArtifactError(422,'artifact_classification_invalid')
        values = {**metadata,'classification_review_id':review_id}
        conn.execute('UPDATE shared_artifact_generations SET '+','.join(f'{key}=?' for key in values)+' WHERE name=? AND generation=?',
                     (*values.values(),name,row['generation']))
        conn.execute('UPDATE shared_artifacts SET revision=revision+1 WHERE name=?',(name,))
        row.update(values)
        self._event(row,'artifact_classify',review['reviewer_agent_id'],inventory_digest=inventory_digest,
                    review_id=review_id,outcome='committed')
        return self.projection(row)
