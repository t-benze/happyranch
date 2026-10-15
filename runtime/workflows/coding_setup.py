"""Founder setup producer; no source/task allocation or repository access.

Cold history and present authority are separate. Every write is owned by the
native admission writer; selected reads/replays use a coherent read snapshot
without durable leases. No file/object I/O occurs inside either transaction.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from typing import Annotated, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.coding_authority_history import (
    CodingAuthorityHistoryError, HistoricalSetupIssuance, SetupStorage,
    _setup_configuration, read_setup_storage,
)
from runtime.workflows.coding_saved_records import (
    ACTIONS, CodingSavedRecordError, read_saved_issuance, read_saved_setup_command,
)
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.repository_artifacts import Component, RepositoryId
from runtime.workflows.templates import WorkflowTemplatePrincipal


class CodingSetupError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SetupManager(_Closed):
    principal: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    team: Component


class SetupProvider(_Closed):
    provider_repository: Annotated[str, Field(min_length=1, max_length=256)]
    pr_rule: Literal['source-owned-approved-subject@1']


class SetupRadius(_Closed):
    object_formats: list[Literal['sha1', 'sha256']] = Field(min_length=1, max_length=2)
    store_owners: list[Component] = Field(max_length=16)
    path_prefixes: list[str] = Field(max_length=64)
    commands: list[list[str]] = Field(max_length=16)
    recipient_rule: Literal['mapping-eligible-current@1']
    provider_rule: SetupProvider | None
    original_limit_rule: Literal['lower-native-original@1']


class SetupScope(_Closed):
    repository_id: RepositoryId
    manager_selector: SetupManager
    actions: list[Literal[
        'import-release-coding', 'publish-coding-mapping', 'adopt-local-store',
        'read-repository-artifact', 'start-coding', 'implement-code-change',
        'review-code-change', 'verify-code-change', 'observe-coding-ci',
        'merge-coding-approved-head', 'replace-coding-stage',
    ]] = Field(min_length=1, max_length=len(ACTIONS))
    scope: SetupRadius
    source_rule: Literal['current-assigned-manager-decision@1']
    base_rule: Literal['manager-select-local-base@1']


class SetupCommand(_Closed):
    format: Literal['workflow-coding-capability-setup-command@1']
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    setup_key: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    expected_authorization_revision: Annotated[int, Field(ge=0, le=9223372036854775807)]
    mode: Literal['issue', 'withdraw']
    scope: SetupScope | None


class SetupCurrent(_Closed):
    state: Literal['active', 'inactive', 'revoked']
    reason: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')] | None

    @model_validator(mode='after')
    def disposition(self) -> SetupCurrent:
        if (self.state == 'revoked') != (self.reason is not None):
            raise ValueError('coding_setup_receipt_invalid')
        return self


class SetupReceipt(_Closed):
    format: Literal['workflow-coding-capability-setup-receipt@1']
    authorization_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    setup_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')] | None
    revision: Annotated[int, Field(ge=1, le=9223372036854775807)]
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    request_digest: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    replayed: bool
    current: SetupCurrent


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


def parse_setup_command(raw: bytes, *, org_slug: str) -> dict:
    try:
        read_saved_setup_command(raw, expected_org_slug=org_slug)
        command = json.loads(raw)
        SetupCommand.model_validate(command)
        return command
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CodingSetupError('coding_setup_invalid_request') from None


def _capture(capture: AuthorityAdmissionCapture, admission_id: str, raw: bytes) -> dict:
    # Lossless coding adapter of the native capture, without document-only
    # cutover/actions. Full mirror and original snapshot are never filtered.
    fields = {'dependency': ('kind', 'org_namespace', 'profile_name', 'consumer_identity', 'bound_generation', 'state'),
              'store': ('kind', 'profile_name', 'generation', 'profile_digest', 'state'),
              'registry': ('kind', 'profile_name', 'published_generation')}
    ready = capture.ready
    return dict(pin=dict(namespace=ready.namespace, generation=ready.generation,
                         snapshot_digest=ready.snapshot_digest),
        admission_record_id=admission_id, snapshot_bytes_base64=_b64(ready.snapshot_bytes),
        profile_evidence=dict(ready=dict(namespace=ready.namespace, generation=ready.generation,
            snapshot_bytes_hex=ready.snapshot_bytes.hex(), snapshot_digest=ready.snapshot_digest),
            pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'),
                             capture.pointer, strict=True)),
            profiles=[dict(name=name, digest=digest) for name, digest in capture.profile_digests],
            mirror=[dict(zip(fields[item[0]], item, strict=True)) for item in capture.profile_mirror]),
        grant_bytes_base64=_b64(raw), grant_digest=_digest(raw))


def _scope_current(scope: dict, capture: AuthorityAdmissionCapture) -> None:
    try:
        _setup_configuration(scope, json.loads(capture.ready.snapshot_bytes))
    except CodingAuthorityHistoryError:
        raise CodingSetupError('coding_setup_configuration_revoked') from None


class CodingSetupStore:
    def __init__(self, org) -> None:
        self.org = org
        self.db = org.db

    def _principal(self, principal: WorkflowTemplatePrincipal) -> None:
        if (principal.org_slug != self.org.slug or principal.principal_kind != 'human'
                or principal.principal_id != 'founder' or principal.proof_kind != 'founder_bearer'
                or principal.task_id is not None or principal.session_id is not None):
            raise CodingSetupError('human_only')
        principal.revalidate()

    def _schema_references(self) -> tuple[str, ...]:
        # Independent whole-native references may perform file/DB construction.
        # Resolve them BEFORE either read snapshot or admission ownership.
        from runtime.identities.schema import validate_names
        from runtime.infrastructure.task_pause_controls import validate_pause_schema
        from runtime.infrastructure.team_move_store import validate_team_move_schema
        from runtime.orchestrator.authority import _RELEASE_REFERENCE_HISTORIES, _release_schema_digest
        with self.db._lock:
            conn = self.db._conn
            names = validate_names(conn, org_slug=self.org.slug)
            pause = validate_pause_schema(conn, org_slug=self.org.slug, allow_absent=True)
            move = validate_team_move_schema(conn, org_slug=self.org.slug)
        references = tuple(_release_schema_digest('J', history, names, pause_layout=pause,
                           team_move_layout=move) for history in _RELEASE_REFERENCE_HISTORIES)
        if 'unavailable' in references:
            raise CodingSetupError('coding_setup_schema_reference_unavailable')
        return references

    def _storage(self, conn: sqlite3.Connection, references: tuple[str, ...]) -> SetupStorage:
        from runtime.infrastructure.workflow_schema import validate_workflow_schema
        if validate_workflow_schema(conn, expected_org_slug=self.org.slug) != 'J':
            raise CodingSetupError('coding_setup_schema_required')
        rows = conn.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')
        actual = _digest('\n'.join(str(row[0]) for row in rows).encode())
        if actual not in references:
            raise CodingSetupError('coding_setup_whole_schema_mismatch')
        return read_setup_storage(conn, expected_org_slug=self.org.slug)

    @contextmanager
    def _read(self, capture: AuthorityAdmissionCapture | None) -> Iterator[tuple[sqlite3.Connection, AuthorityAdmissionCapture | None]]:
        """SELECT-only, no durable admission lease, no file reads in snapshot."""
        authority = self.org.workflow_authority
        with ExitStack() as fences:
            if capture is not None:
                coordinator = authority._profile_coordinator
                try:
                    for name in capture.profile_names:
                        fences.enter_context(coordinator.profile_read(name))
                except ProfileCoordinatorError:
                    # Safe original metadata remains readable under revocation;
                    # no failed profile lease becomes active authority.
                    fences.close()
                    capture = None
            fences.enter_context(authority._publisher_lock)
            fences.enter_context(self.db._lock)
            conn = self.db._conn
            if conn.in_transaction:
                raise CodingSetupError('coding_setup_caller_transaction')
            conn.execute('BEGIN')
            try:
                yield conn, capture
            finally:
                conn.rollback()

    def _disposition(self, conn: sqlite3.Connection, entry: HistoricalSetupIssuance,
                     storage: SetupStorage, capture: AuthorityAdmissionCapture | None) -> dict:
        pointer = dict(storage.pointers)[entry.namespace]
        selected = next(item for item in storage.issuances if item.authorization_id == pointer)
        current = json.loads(selected.authority_bytes)
        if current['mode'] == 'withdraw':
            return dict(state='inactive', reason=None)
        if selected.authorization_id != entry.authorization_id:
            return dict(state='revoked', reason='coding_setup_replaced')
        if capture is None:
            return dict(state='revoked', reason='coding_setup_authority_unavailable')
        try:
            coordinator = self.org.workflow_authority._profile_coordinator
            for name in capture.profile_names:
                coordinator._assert_no_active_operation(name)
            self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
            _scope_current(current['setup'], capture)
            self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        except (WorkflowAuthorityError, ProfileCoordinatorError, CodingSetupError):
            return dict(state='revoked', reason='coding_setup_authority_revoked')
        return dict(state='active', reason=None)

    def _receipt(self, conn: sqlite3.Connection, entry: HistoricalSetupIssuance,
                 storage: SetupStorage, capture: AuthorityAdmissionCapture | None, *, replayed: bool) -> dict:
        record = json.loads(entry.authority_bytes)
        command = json.loads(base64.b64decode(record['command_bytes_base64'], validate=True))
        return SetupReceipt.model_validate(dict(format='workflow-coding-capability-setup-receipt@1',
            authorization_id=entry.authorization_id, setup_id=record['setup']['id'] if record['setup'] else None,
            revision=entry.revision, operation_key=command['operation_key'], request_digest=record['command_digest'],
            replayed=replayed, current=self._disposition(conn, entry, storage, capture))).model_dump()

    def _replay(self, conn: sqlite3.Connection, storage: SetupStorage, command: dict,
                raw: bytes, capture: AuthorityAdmissionCapture | None) -> dict | None:
        for entry in storage.issuances:
            record = json.loads(entry.authority_bytes)
            original = base64.b64decode(record['command_bytes_base64'], validate=True)
            if json.loads(original)['operation_key'] == command['operation_key']:
                if original != raw:
                    raise CodingSetupError('coding_setup_operation_conflict')
                return self._receipt(conn, entry, storage, capture, replayed=True)
        return None

    def _discover(self) -> AuthorityAdmissionCapture | None:
        try:
            return self.org.workflow_authority.capture_admission()
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            return None

    async def get(self, *, principal: WorkflowTemplatePrincipal, setup_key: str) -> dict:
        self._principal(principal)  # Entitlement precedes private lookup/decode.
        if type(setup_key) is not str or re.fullmatch(r'[0-9a-f]{64}', setup_key) is None:
            raise CodingSetupError('coding_setup_invalid_request')
        references = self._schema_references()
        capture = self._discover()
        async with self.org.workflow_authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._read(capture) as (conn, current_capture):
                    storage = self._storage(conn, references)
                    namespace = f'org/{self.org.slug}/coding-setup/{setup_key}'
                    pointer = dict(storage.pointers).get(namespace)
                    if pointer is None:
                        raise CodingSetupError('coding_setup_not_found')
                    entry = next(item for item in storage.issuances if item.authorization_id == pointer)
                    result = self._receipt(conn, entry, storage, current_capture, replayed=False)
                    self._principal(principal)
                    return result

    async def set(self, *, principal: WorkflowTemplatePrincipal, raw: bytes) -> dict:
        self._principal(principal)
        command = parse_setup_command(raw, org_slug=self.org.slug)
        references = self._schema_references()
        capture = self._discover()  # Discovery/file reads before waits/writers.
        authority = self.org.workflow_authority
        async with authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._read(capture) as (conn, current_capture):
                    storage = self._storage(conn, references)
                    replay = self._replay(conn, storage, command, raw, current_capture)
                    self._principal(principal)
                    if replay is not None:
                        return replay
                if capture is None:
                    raise CodingSetupError('coding_setup_authority_unavailable')
                try:
                    with authority.admission_writer(capture) as conn:
                        self._principal(principal)
                        storage = self._storage(conn, references)
                        replay = self._replay(conn, storage, command, raw, capture)
                        if replay is not None:
                            return replay
                        namespace = f'org/{self.org.slug}/coding-setup/{command["setup_key"]}'
                        previous = [entry for entry in storage.issuances if entry.namespace == namespace]
                        if len(previous) != command['expected_authorization_revision']:
                            raise CodingSetupError('coding_setup_revision_stale')
                        if command['mode'] == 'withdraw' and not previous:
                            raise CodingSetupError('coding_setup_not_found')
                        if command['mode'] == 'issue':
                            _scope_current(command['scope'], capture)
                        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        admission_id = 'coding-admission:' + uuid.uuid4().hex
                        identity = 'coding-issuance:' + uuid.uuid4().hex
                        envelope = _capture(capture, admission_id, raw)
                        admission = dict(id=admission_id, namespace=capture.ready.namespace,
                            generation=capture.ready.generation, request_digest=_digest(raw), admitted_by='human:founder')
                        setup = None if command['mode'] == 'withdraw' else dict(command['scope'],
                            format='workflow-coding-setup-authority@1', id='coding-setup:' + uuid.uuid4().hex,
                            org_slug=self.org.slug, authority=envelope, created_at=now)
                        issuance = _canonical(dict(format='workflow-coding-capability-issuance@1',
                            command_bytes_base64=_b64(raw), command_digest=_digest(raw), issuer=principal.provenance,
                            mode=command['mode'], setup=setup, admission=dict(record=admission, capture=envelope), created_at=now))
                        read_saved_issuance(issuance, expected_org_slug=self.org.slug)
                        # One caller-owned admission transaction includes all
                        # five authentic native columns, immutable revision and
                        # active pointer (withdraw is an immutable tombstone).
                        conn.execute('INSERT INTO workflow_admission_records '
                            '(id,namespace,generation,request_digest,admitted_by) VALUES (?,?,?,?,?)',
                            tuple(admission[key] for key in ('id', 'namespace', 'generation', 'request_digest', 'admitted_by')))
                        conn.execute('INSERT INTO workflow_authorization_revisions '
                            '(id,namespace,revision,authority_bytes,authority_digest,source_pin,created_at) '
                            'VALUES (?,?,?,?,?,?,?)', (identity, namespace, len(previous) + 1, issuance,
                            _digest(issuance), 'coding-capability-issuer@1', now))
                        if previous:
                            changed = conn.execute('UPDATE workflow_active_authorizations SET authorization_revision_id=? '
                                'WHERE namespace=? AND authorization_revision_id=?',
                                (identity, namespace, previous[-1].authorization_id)).rowcount
                            if changed != 1:
                                raise CodingSetupError('coding_setup_revision_stale')
                        else:
                            conn.execute('INSERT INTO workflow_active_authorizations '
                                '(namespace,authorization_revision_id) VALUES (?,?)', (namespace, identity))
                        storage = self._storage(conn, references)  # Complete new cold closure before commit.
                        entry = next(item for item in storage.issuances if item.authorization_id == identity)
                        receipt = self._receipt(conn, entry, storage, capture, replayed=False)
                        self._principal(principal)
                        authority._validate_admission_uncommitted(conn, capture)
                        return receipt
                except CodingSavedRecordError:
                    raise CodingSetupError('coding_setup_capture_unavailable') from None
                except sqlite3.IntegrityError:
                    raise CodingSetupError('coding_setup_storage_conflict') from None
