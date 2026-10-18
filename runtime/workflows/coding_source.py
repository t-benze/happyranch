"""Explicit nonallocating D1 source capability mint and monotone refinements.

No callback, claim, run, stage, task or execution grant is created. Exact-head
reads and allocating starts remain unavailable until their genuine producers.
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.workflows._coding_mapping_admission import _MappingCaptures, capture_body, native_owner
from runtime.workflows._coding_source_admission import source_storage
from runtime.workflows.coding_authority_history import _Budget, _SetupHistoryReader, _read_setup_storage, _setup_configuration
from runtime.workflows.coding_mapping import CodingMappingStore
from runtime.workflows.coding_mapping_history import _read_mapping_storage
from runtime.workflows.coding_saved_records import (
    _agent_in_capture, _integer, read_saved_capability, read_saved_source_command,
)
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.orchestrator.task_pause import DeferredRootPause
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.workflows.coding_setup import SetupCurrent, _b64, _canonical, _capture, _digest
from runtime.workflows._coding_source_capture import _same_record
from runtime.workflows.repository_artifacts import Component, RepositoryId, RepositoryAdoption, RepositoryAdoptionProof

Token = Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class CodingSourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SourceMapping(_Closed):
    id: Token
    generation: Annotated[int, Field(ge=1, le=9223372036854775807)]
    digest: Digest


class SourceWork(_Closed):
    repository_id: RepositoryId
    store_owner: Component
    object_format: Literal['sha1', 'sha256']
    base: str
    paths: list[str] = Field(max_length=64)
    commands: list[list[str]] = Field(max_length=16)


class SourceCommand(_Closed):
    format: Literal['workflow-coding-source-command@1']
    operation_key: Token
    parent_id: Token
    mapping: SourceMapping
    action: Literal['import-release-coding', 'publish-coding-mapping', 'adopt-local-store']
    work: SourceWork


class SourceReceipt(_Closed):
    format: Literal['workflow-coding-source-receipt@1']
    authorization_id: Token
    admission_id: Token
    source_admission_id: Token
    operation_key: Token
    request_digest: Digest
    replayed: bool
    current: SetupCurrent


def parse_source_command(raw, *, org_slug):
    try:
        read_saved_source_command(raw, expected_org_slug=org_slug)
        value = json.loads(raw)
        SourceCommand.model_validate(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CodingSourceError('coding_source_invalid_request') from None


def read_source_storage(conn, org_slug):
    budget = _Budget()
    setups = _SetupHistoryReader(conn, org_slug, budget)
    setup_storage = _read_setup_storage(conn, expected_org_slug=org_slug, reader=setups)
    captures = _MappingCaptures(conn, org_slug, budget)
    mappings, _ = _read_mapping_storage(conn, expected_org_slug=org_slug, budget=budget, setups=setups, captures=captures)
    histories, _ = source_storage(conn, org_slug, budget, setups, captures, mappings)
    return setup_storage, mappings, histories


class CodingSourceStore:
    def __init__(self, org):
        self.org, self.db = org, org.db
        self.mapping = CodingMappingStore(org)
        self.setup = self.mapping.setup

    def _principal(self, principal):
        self.mapping._principal(principal)
        sessions = self.org.sessions
        if (sessions.get_context_by_session(principal.session_id) !=
                (self.org.slug, principal.task_id, principal.principal_id)
                or sessions.get_active(principal.task_id, principal.principal_id) != principal.session_id
                or sessions.is_recovery_session(principal.task_id, principal.principal_id, principal.session_id)):
            raise CodingSourceError('coding_source_manager_required')

    @contextmanager
    def _read(self, principal, capture, references):
        # Safe original replay is entitled by the actual current session, not
        # fresh mint readiness. Pause/revocation cannot restore authority.
        with self.mapping._binding(principal):
            self._principal(principal)
            with self.setup._read(capture) as selected:
                self.mapping._structure(selected[0], references)
                yield selected

    def _storage(self, conn, references):
        # Full independent native/schema/data reader before the selected private
        # source closure. Each complete closure shares its own single budget.
        self.setup._storage(conn, references)
        return read_source_storage(conn, self.org.slug)

    def _selection(self, conn, principal, capture, command, state, references):
        if capture is None:
            raise CodingSourceError('coding_source_authority_unavailable')
        issuer, chain, start, owner = native_owner(self.org, principal, conn)
        for row in chain:
            _integer(row._value('revision_count'))
            _integer(row._value('orchestration_step_count'))
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        setups, mappings, histories = state
        parent = None
        if command['parent_id'] in histories:
            history, _, _ = histories[command['parent_id']]
            parent = json.loads(history.native_records.capability_history.selected.authority_bytes)
            if parent['issuer'] != issuer or parent['level'] != 'source':
                raise CodingSourceError('coding_source_parent_unavailable')
            root_entry = history.native_records.capability_history.setup_history.selected
        else:
            found = [entry for entry in setups.issuances if entry.setup_bytes is not None
                     and json.loads(entry.setup_bytes)['id'] == command['parent_id']]
            if len(found) != 1:
                raise CodingSourceError('coding_source_parent_unavailable')
            root_entry = found[0]
        root = json.loads(root_entry.setup_bytes)
        if self.setup._disposition(conn, root_entry, setups, capture)['state'] != 'active':
            raise CodingSourceError('coding_source_setup_revoked')
        _setup_configuration(root, json.loads(capture.ready.snapshot_bytes))
        if root['manager_selector'] != dict(principal=principal.principal_id, team=principal.team_slug):
            raise CodingSourceError('coding_source_manager_revoked')
        if mappings is None or command['mapping'] != dict(id=mappings.selected.mapping_id,
                generation=mappings.selected.generation, digest=mappings.selected.mapping_digest):
            raise CodingSourceError('coding_source_mapping_stale')
        entry = mappings.selected
        mapped = json.loads(entry.mapping_bytes)
        roots, stores = self.mapping._roots(conn, principal, capture, json.loads(entry.command_bytes),
                                           references)
        if root not in roots or roots != json.loads(base64.b64decode(mapped['authority']['grant_bytes_base64'], validate=True))['setups']:
            raise CodingSourceError('coding_source_setup_revoked')
        work = command['work']
        candidates = [(index, item) for index, item in enumerate(mapped['mapping']['repository_bindings'])
            if item['binding']['repository_id'] == work['repository_id']
            and item['binding']['store_owner'] == work['store_owner']]
        if len(candidates) != 1:
            raise CodingSourceError('coding_source_store_unavailable')
        index, binding = candidates[0]
        selector, origin, _ = stores[index]
        proof = mapped['adoptions'][index]
        if proof['object_format'] != work['object_format']:
            raise CodingSourceError('coding_source_object_format_revoked')
        # Source's actual executable manager must have the SAME assigned
        # repository configuration; store ownership or text cannot supply it.
        snapshot = json.loads(capture.ready.snapshot_bytes)
        agents = {a['name']: a for a in snapshot['agents'] if a['status'] == 'active'}
        if agents[principal.principal_id]['repos'].get(selector['repo_key']) != origin:
            raise CodingSourceError('coding_source_repository_revoked')
        envelope = _capture(capture, 'unallocated', _canonical(root if parent is None else parent))
        _agent_in_capture(envelope, root['manager_selector'], 'manager')
        if parent is not None:
            history = histories[parent['id']][0]
            original = history.source_capture
            # Refine the SAME original owner/budget pool, never repin its
            # counters, ancestor chain, source SID or original limit records.
            if (original.owner_invocation_id != owner.identity
                    or len(original.ancestor_records) != len(chain) - 1):
                raise CodingSourceError('coding_source_original_owner_stale')
            for old, new in zip((original.task_record, *original.ancestor_records), chain, strict=True):
                for key in ('id', 'parent_task_id', 'assigned_agent', 'team', 'revision_count',
                            'orchestration_step_count', 'session_timeout_seconds', 'current_session_id'):
                    if old._value(key) != new._value(key):
                        raise CodingSourceError('coding_source_original_limits_stale')
        return issuer, chain, start, owner, root, parent, binding, proof, origin

    def _receipt(self, conn, principal, capture, state, selected, references, *, replayed):
        history, operation, _ = selected
        record = json.loads(history.native_records.capability_history.selected.authority_bytes)
        root_entry = history.native_records.capability_history.setup_history.selected
        disposition = self.setup._disposition(conn, root_entry, state[0], capture)
        if disposition['state'] == 'active':
            try:
                self._selection(conn, principal, capture, operation['command'], state, references)
            except (ValueError, KeyError, TypeError, WorkflowAuthorityError, ProfileCoordinatorError,
                    DeferredRootPause, DeferredTeamMove, PauseControlError):
                disposition = dict(state='revoked', reason='coding_source_current_admission_unavailable')
        return SourceReceipt.model_validate(dict(format='workflow-coding-source-receipt@1',
            authorization_id=record['id'], admission_id=record['authority']['admission_record_id'],
            source_admission_id=record['source_admission_id'], operation_key=operation['command']['operation_key'],
            request_digest=operation['command_digest'], replayed=replayed, current=disposition)).model_dump()

    def _own_operation(self, conn, principal, identity):
        # Select native audit ownership BEFORE decoding any P/private capture.
        from runtime.workflows.coding_source_history import _AUDIT_COLUMNS, _AUDIT_TYPES, _inventory, _projection
        from runtime.workflows._coding_source_admission import decode_operation
        query = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
            "WHERE action='coding_source_operation' AND task_id=? AND agent=? AND id>=? ORDER BY id LIMIT ?")
        budget, found = _Budget(), False
        for audit in _inventory(conn, query, (principal.task_id, principal.principal_id),
                                _AUDIT_COLUMNS, _AUDIT_TYPES, budget):
            operation = decode_operation(audit, self.org.slug, budget)
            if operation['authorization_id'] == identity and operation['session_id'] == principal.session_id:
                if found:
                    raise CodingSourceError('coding_source_operation_conflict')
                found = True
        if not found:
            raise CodingSourceError('coding_source_not_found')

    def _selected(self, principal, state, identity):
        selected = state[2].get(identity)
        if selected is None:
            raise CodingSourceError('coding_source_not_found')
        record = json.loads(selected[0].native_records.capability_history.selected.authority_bytes)
        if record['issuer'] != dict(kind='agent', principal=principal.principal_id,
                task_id=principal.task_id, session_id=principal.session_id, result_id=None):
            raise CodingSourceError('coding_source_not_found')
        return selected

    def _replay(self, conn, principal, command, raw, state, capture, references):
        for identity, selected in state[2].items():
            operation = selected[1]
            if (operation['principal'], operation['command']['operation_key']) == (
                    principal.operation_principal, command['operation_key']):
                if operation['command_bytes'] != raw:
                    raise CodingSourceError('coding_source_operation_conflict')
                self._selected(principal, state, identity)
                return self._receipt(conn, principal, capture, state, selected, references, replayed=True)
        return None

    async def get(self, *, principal, authorization_id):
        self._principal(principal)
        references = self.setup._schema_references()
        capture = self.setup._discover()
        async with self.org.workflow_authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                with self._read(principal, capture, references) as (conn, current):
                    self._own_operation(conn, principal, authorization_id)
                    state = self._storage(conn, references)
                    selected = self._selected(principal, state, authorization_id)
                    return self._receipt(conn, principal, current, state, selected, references, replayed=False)

    async def mint(self, *, principal, raw):
        self._principal(principal)
        command = parse_source_command(raw, org_slug=self.org.slug)
        references = self.setup._schema_references()
        authority = self.org.workflow_authority
        capture = self.setup._discover()
        async with authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                with self._read(principal, capture, references) as (conn, current):
                    state = self._storage(conn, references)
                    replay = self._replay(conn, principal, command, raw, state, current, references)
                    if replay is not None:
                        return replay
                    selected = self._selection(conn, principal, current, command, state, references)
                    issuer, chain, start, owner, root, parent, binding, proof, origin = selected
                    identity = 'coding-source:' + uuid.uuid4().hex
                    admission_id = 'coding-admission:' + uuid.uuid4().hex
                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    envelope = _capture(current, admission_id, _canonical(root if parent is None else parent))
                    record = dict(format='workflow-coding-capability@1', id=identity, parent_id=command['parent_id'],
                        level='source', issuer=issuer,
                        subject=dict(org_slug=self.org.slug, principal=principal.principal_id, team=principal.team_slug,
                            source_task_id=principal.task_id, source_session_id=principal.session_id,
                            instance_id=None, attempt_id=None, control_id=None, task_id=principal.task_id,
                            session_id=principal.session_id), action=command['action'],
                        radius=dict(command['work'], provider_repository=None, pr_number=None, expected_head=None,
                                    head_rule='own-development-output@1'), authority=envelope,
                        source_admission_id=admission_id if parent is None else parent['source_admission_id'], created_at=now)
                    value = _canonical(record)
                    read_saved_capability(value, expected_org_slug=self.org.slug)
                from runtime.orchestrator.org_config import load_org_config
                revise_limit = load_org_config(self.org.orchestrator._paths).max_revise_rounds
                from runtime.workflows.repository_artifact_objects import _verify_source_base, _observe_adoption, _Budget as ObjectBudget
                from runtime.workflows.coding_saved_records import CodingRepositoryBinding
                # One bounded supervised object-only operation verifies the fixed
                # base. It returns NO object content or exact-head entitlement.
                object_budget = ObjectBudget()
                await asyncio.to_thread(_verify_source_base, budget=object_budget, paths=self.org.orchestrator._paths,
                    binding=CodingRepositoryBinding.model_validate(binding['binding']),
                    adoption=RepositoryAdoption.model_validate(binding['adoption']),
                    proof=RepositoryAdoptionProof.model_validate(proof), base=command['work']['base'], configured_origin=origin)
                self._principal(principal)
                observation = _observe_adoption(paths=self.org.orchestrator._paths, org_slug=self.org.slug,
                    store_owner=command['work']['store_owner'], repo_key=binding['binding']['repo_key'],
                    configured_origin=origin, budget=object_budget)
                if observation['store_identity'] != proof['store_identity'] or observation['object_format'] != proof['object_format']:
                    raise CodingSourceError('coding_source_store_stale')
                if load_org_config(self.org.orchestrator._paths).max_revise_rounds != revise_limit:
                    raise CodingSourceError('coding_source_policy_stale')
                with self.mapping._binding(principal), authority.admission_writer(capture) as conn:
                    self.mapping._structure(conn, references)
                    self._principal(principal)
                    state = self._storage(conn, references)
                    replay = self._replay(conn, principal, command, raw, state, capture, references)
                    if replay is not None:
                        return replay
                    current = self._selection(conn, principal, capture, command, state, references)
                    if current[0] != issuer or current[3].identity != owner.identity or current[4:] != selected[4:]:
                        raise CodingSourceError('coding_source_admission_stale')
                    if len(chain) != len(current[1]):
                        raise CodingSourceError('coding_source_ancestry_stale')
                    for old, new in zip(chain, current[1], strict=True):
                        _same_record(old, new)  # ALL27 after the await, no timestamp exemption.
                    if current[2] != start:
                        raise CodingSourceError('coding_source_session_stale')
                    if parent is not None:
                        original = state[2][parent['id']][0].source_capture
                        if original.budgets.max_revise_rounds != revise_limit:
                            raise CodingSourceError('coding_source_original_policy_stale')
                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    record['created_at'] = now
                    value = _canonical(record)
                    read_saved_capability(value, expected_org_slug=self.org.slug)
                    conn.execute('INSERT INTO workflow_admission_records (id,namespace,generation,request_digest,admitted_by) '
                        'VALUES (?,?,?,?,?)', (admission_id, envelope['pin']['namespace'], envelope['pin']['generation'],
                        _digest(value), principal.operation_principal))
                    conn.execute('INSERT INTO workflow_authorization_revisions '
                        '(id,namespace,revision,authority_bytes,authority_digest,source_pin,created_at) VALUES (?,?,?,?,?,?,?)',
                        (identity, f'org/{self.org.slug}/coding-capability/{identity}', 1, value, _digest(value),
                         'workflow-coding-capability@1', now))
                    operation = dict(format='workflow-coding-source-operation@1', org_slug=self.org.slug,
                        principal=principal.operation_principal, authorization_id=identity, admission_id=admission_id, session_id=principal.session_id,
                        command_bytes_base64=_b64(raw), command_digest=_digest(raw), created_at=now)
                    self.db.insert_audit_log_uncommitted(principal.task_id, principal.principal_id,
                                                        'coding_source_operation', operation)
                    if parent is None:
                        body = capture_body(self.org, principal, conn, admission_id=admission_id,
                            mapping_id=identity, raw=raw, now=now, max_revise_rounds=revise_limit,
                            native_table='workflow_authorization_revisions')
                        if len(json.dumps(body).encode('utf8')) > 1048576:
                            raise CodingSourceError('coding_source_capture_bounds')
                        self.db.insert_audit_log_uncommitted(principal.task_id, principal.principal_id,
                                                            'coding_source_admission', body)
                    state = self._storage(conn, references)  # Independent complete cold closure BEFORE COMMIT.
                    self._selection(conn, principal, capture, command, state, references)
                    authority._validate_admission_uncommitted(conn, capture)
                    return self._receipt(conn, principal, capture, state, state[2][identity], references, replayed=False)
