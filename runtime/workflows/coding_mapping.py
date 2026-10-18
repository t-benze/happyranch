"""Explicit admitted mapping publication and local-store adoption.

No task/run/result allocation. Replay preserves full original command and IDs;
saved validity never restores current authority or adopts a store on a read.
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

from runtime.workflows._coding_mapping_admission import capture_body, native_owner
from runtime.workflows._coding_release_history import read_release_import
from runtime.workflows.coding_authority_history import _setup_configuration
from runtime.workflows.coding_mapping_history import read_mapping_storage
from runtime.workflows.coding_saved_records import (
    ROLES, CodingRepositoryBinding, read_saved_mapping, read_saved_mapping_command, read_saved_mapping_record,
    _agent_in_capture,
)
from runtime.workflows.coding_setup import CodingSetupStore, SetupManager, _b64, _canonical, _capture, _digest
from runtime.workflows.repository_artifact_objects import _Budget, _observe_adoption
from runtime.workflows.repository_artifacts import (
    Component, RepositoryAuthority, RepositoryId, RepositoryAdoption, RepositoryAdoptionChecks,
)


class CodingMappingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MappingRelease(_Closed):
    identity: Literal['release/happyranch/coding']
    version: Annotated[int, Field(ge=1, le=1)]
    definition_digest: Literal['6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8']


class MappingRoles(_Closed):
    implementer: SetupManager
    code_reviewer: SetupManager
    qa_engineer: SetupManager
    agent_manager: SetupManager


class MappingReplacements(_Closed):
    implementer: list[SetupManager] = Field(max_length=16)
    code_reviewer: list[SetupManager] = Field(max_length=16)
    qa_engineer: list[SetupManager] = Field(max_length=16)
    agent_manager: list[SetupManager] = Field(max_length=16)


class MappingSelector(_Closed):
    repository_id: RepositoryId
    store_owner: Component
    repo_key: Component
    policy: Literal['adopted-local-store@1']


class MappingInput(_Closed):
    release: MappingRelease
    roles: MappingRoles
    eligible_replacements: MappingReplacements
    repository_selectors: list[MappingSelector] = Field(max_length=8)
    authority: RepositoryAuthority


class MappingCommand(_Closed):
    format: Literal['workflow-coding-mapping-command@1']
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    expected_generation: Annotated[int, Field(ge=0, le=9223372036854775807)]
    mapping: MappingInput


class MappingBinding(_Closed):
    binding: CodingRepositoryBinding
    adoption: RepositoryAdoption


class MappingValue(_Closed):
    format: Literal['workflow-coding-mapping@1']
    release: MappingRelease
    roles: MappingRoles
    eligible_replacements: MappingReplacements
    repository_bindings: list[MappingBinding] = Field(max_length=8)
    authority: RepositoryAuthority


class MappingReceipt(_Closed):
    format: Literal['workflow-coding-mapping-receipt@1']
    id: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    generation: Annotated[int, Field(ge=1, le=9223372036854775807)]
    mapping_digest: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    mapping: MappingValue
    replayed: bool


def parse_mapping_command(raw: bytes, *, org_slug: str) -> dict:
    try:
        read_saved_mapping_command(raw, expected_org_slug=org_slug)
        command = json.loads(raw)
        MappingCommand.model_validate(command)
        return command
    except ValueError:
        raise CodingMappingError('coding_mapping_invalid_request') from None


def validate_mapping_receipt(value: dict, *, org_slug: str) -> dict:
    receipt = MappingReceipt.model_validate(value).model_dump()
    read_saved_mapping(_canonical(receipt['mapping']), expected_org_slug=org_slug)
    return receipt


class CodingMappingStore:
    def __init__(self, org):
        self.org = org
        self.db = org.db
        self.setup = CodingSetupStore(org)

    def _principal(self, principal):
        principal.revalidate()
        if (principal.org_slug != self.org.slug or principal.principal_kind != 'agent'
                or principal.proof_kind != 'task_session' or not principal.task_id or not principal.session_id):
            raise CodingMappingError('coding_mapping_manager_required')

    def _binding(self, principal):
        return self.org.sessions._get_binding_lease(principal.task_id, principal.principal_id)

    @contextmanager
    def _read(self, principal, capture, references):
        # A native threading lease MUST NOT span an await: the same event loop
        # may need to settle/replace this binding while preflight is in flight.
        with self._binding(principal):
            self._principal(principal)
            with self.setup._read(capture) as selected:
                self._structure(selected[0], references)
                native_owner(self.org, principal, selected[0])
                yield selected

    def _structure(self, conn, references):
        # Complete independent native schema identity precedes native/private
        # decoding. No candidate-derived reference or filtered object list.
        rows = conn.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')
        if _digest('\n'.join(str(row[0]) for row in rows).encode()) not in references:
            raise CodingMappingError('coding_mapping_whole_schema_mismatch')

    def _storage(self, conn, references):
        self.setup._storage(conn, references)
        if read_release_import(conn, expected_org_slug=self.org.slug) is None:
            raise CodingMappingError('coding_mapping_release_import_required')
        return read_mapping_storage(conn, expected_org_slug=self.org.slug)

    def _roots(self, conn, principal, capture, command, references):
        if capture is None:
            raise CodingMappingError('coding_mapping_authority_unavailable')
        native_owner(self.org, principal, conn)
        authority = self.org.workflow_authority
        authority._validate_admission_uncommitted(conn, capture)
        pin = dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                   snapshot_digest=capture.ready.snapshot_digest)
        if command['mapping']['authority'] != pin:
            raise CodingMappingError('coding_mapping_authority_stale')
        manager = command['mapping']['roles']['agent_manager']
        if manager != dict(principal=principal.principal_id, team=principal.team_slug):
            raise CodingMappingError('coding_mapping_manager_required')
        # Validate every role and replacement against CURRENT published policy.
        envelope = _capture(capture, 'unallocated', b'{}')
        snapshot = json.loads(capture.ready.snapshot_bytes)
        agents = {a['name']: a for a in snapshot['agents'] if a['status'] == 'active'}
        for role in ROLES:
            for candidate in [command['mapping']['roles'][role], *command['mapping']['eligible_replacements'][role]]:
                _agent_in_capture(envelope, candidate, 'manager' if role == 'agent_manager' else 'worker')
                if not agents[candidate['principal']]['executor']:
                    raise CodingMappingError('coding_mapping_role_revoked')
        storage = self.setup._storage(conn, references)
        roots = []
        repositories = sorted({s['repository_id'] for s in command['mapping']['repository_selectors']})
        for _, identity in storage.pointers:
            entry = next(item for item in storage.issuances if item.authorization_id == identity)
            record = json.loads(entry.authority_bytes)
            root = record['setup']
            if (root is not None and root['manager_selector'] == manager
                    and (not repositories or root['repository_id'] in repositories)):
                _setup_configuration(root, snapshot)
                if 'publish-coding-mapping' in root['actions']:
                    roots.append(root)
        if repositories:
            roots = [root for root in roots if root['repository_id'] in repositories]
            if sorted(root['repository_id'] for root in roots) != repositories:
                raise CodingMappingError('coding_mapping_setup_revoked')
        else:
            # Empty selectors have no setup selector in the accepted command:
            # only an unambiguous current manager setup can authorize them.
            if len(roots) != 1:
                raise CodingMappingError('coding_mapping_setup_selection_unavailable')
        roots.sort(key=lambda r: r['repository_id'])
        by_repo = {root['repository_id']: root for root in roots}
        selected = []
        for selector in command['mapping']['repository_selectors']:
            root = by_repo[selector['repository_id']]
            if ('adopt-local-store' not in root['actions']
                    or selector['store_owner'] not in root['scope']['store_owners']):
                raise CodingMappingError('coding_mapping_adoption_revoked')
            if selector['store_owner'] not in {role['principal'] for role in command['mapping']['roles'].values()}:
                raise CodingMappingError('coding_mapping_store_revoked')
            owner = agents.get(selector['store_owner'])
            origin = None if owner is None else owner['repos'].get(selector['repo_key'])
            if (not origin or not owner['executor']
                    or selector['repository_id'] != 'workflow-repository:' + _digest(_canonical(
                        [self.org.slug, selector['repo_key'], _digest(origin.encode('utf8'))]))):
                raise CodingMappingError('coding_mapping_store_revoked')
            selected.append((selector, origin, root['scope']['object_formats']))
        authority._validate_admission_uncommitted(conn, capture)
        return roots, selected

    def _receipt(self, entry, *, replayed):
        return validate_mapping_receipt(dict(format='workflow-coding-mapping-receipt@1', id=entry.mapping_id,
            generation=entry.generation, mapping_digest=entry.mapping_digest,
            mapping=json.loads(entry.mapping_bytes)['mapping'], replayed=replayed), org_slug=self.org.slug)

    def _replay(self, storage, principal, command, raw):
        if storage is not None:
            for entry in storage.mappings:
                if (entry.principal, entry.operation_key) == (principal.operation_principal, command['operation_key']):
                    if entry.command_bytes != raw:
                        raise CodingMappingError('coding_mapping_operation_conflict')
                    return self._receipt(entry, replayed=True)
        return None

    def _observe(self, selected, budget):
        # ONE K1 budget/deadline across all <=8 stores and both observations.
        observed = []
        for selector, origin, formats in selected:
            item = _observe_adoption(paths=self.org.orchestrator._paths, org_slug=self.org.slug,
                store_owner=selector['store_owner'], repo_key=selector['repo_key'],
                configured_origin=origin, budget=budget)
            if item['object_format'] not in formats:
                raise CodingMappingError('coding_mapping_object_format_revoked')
            observed.append(item)
        return observed

    async def get(self, *, principal):
        self._principal(principal)
        references = self.setup._schema_references()
        capture = self.org.workflow_authority.capture_admission()
        async with self.org.workflow_authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._read(principal, capture, references) as (conn, current):
                    storage = self._storage(conn, references)
                    if storage is None:
                        raise CodingMappingError('coding_mapping_not_found')
                    selected = storage.selected
                    command = json.loads(selected.command_bytes)
                    roots, stores = self._roots(conn, principal, current, command, references)
                    original = json.loads(selected.mapping_bytes)
                    saved_roots = json.loads(base64.b64decode(
                        original['authority']['grant_bytes_base64'], validate=True))['setups']
                    if saved_roots != roots:
                        raise CodingMappingError('coding_mapping_setup_revoked')
                budget = _Budget()
                observed = await asyncio.to_thread(self._observe, stores, budget)
                self._principal(principal)
                if self._observe(stores, budget) != observed:
                    raise CodingMappingError('coding_mapping_store_changed')
                with self._read(principal, capture, references) as (conn, current):
                    storage = self._storage(conn, references)
                    self._roots(conn, principal, current, command, references)
                    if storage is None or storage.selected.mapping_id != selected.mapping_id:
                        raise CodingMappingError('coding_mapping_generation_stale')
                    original = json.loads(selected.mapping_bytes)
                    for proof, now in zip(original['adoptions'], observed, strict=True):
                        if proof['store_identity'] != now['store_identity'] or proof['object_format'] != now['object_format']:
                            raise CodingMappingError('coding_mapping_store_revoked')
                    return self._receipt(selected, replayed=False)

    async def publish(self, *, principal, raw):
        self._principal(principal)
        command = parse_mapping_command(raw, org_slug=self.org.slug)
        references = self.setup._schema_references()
        authority = self.org.workflow_authority
        capture = self.setup._discover()
        async with authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._read(principal, capture, references) as (conn, current):
                    storage = self._storage(conn, references)
                    native_owner(self.org, principal, conn)
                    replay = self._replay(storage, principal, command, raw)
                    if replay is not None:
                        return replay  # No generation CAS, adoption or new authority.
                    roots, stores = self._roots(conn, principal, current, command, references)
                    generation = 0 if storage is None else storage.selected.generation
                    if generation != command['expected_generation']:
                        raise CodingMappingError('coding_mapping_generation_stale')
                # Source policy is read only for fresh admission, outside
                # SQLite ownership. Replay requires neither this file nor I/O.
                from runtime.orchestrator.org_config import load_org_config
                revise_limit = load_org_config(self.org.orchestrator._paths).max_revise_rounds
                budget = _Budget()
                observed = await asyncio.to_thread(self._observe, stores, budget)
                self._principal(principal)
                # Reopen every selected path and compare observations AFTER the
                # await, BEFORE BEGIN IMMEDIATE. No Git/object/file work inside
                # the native SQLite admission writer, and no await after this.
                if self._observe(stores, budget) != observed:
                    raise CodingMappingError('coding_mapping_store_changed')
                if load_org_config(self.org.orchestrator._paths).max_revise_rounds != revise_limit:
                    raise CodingMappingError('coding_mapping_policy_stale')
                with self._binding(principal), authority.admission_writer(capture) as conn:
                    self._structure(conn, references)
                    native_owner(self.org, principal, conn)
                    storage = self._storage(conn, references)
                    self._principal(principal)
                    replay = self._replay(storage, principal, command, raw)
                    if replay is not None:
                        return replay
                    current_roots, current_stores = self._roots(conn, principal, capture, command, references)
                    if current_roots != roots or current_stores != stores:
                        raise CodingMappingError('coding_mapping_setup_revoked')
                    generation = 0 if storage is None else storage.selected.generation
                    if generation != command['expected_generation']:
                        raise CodingMappingError('coding_mapping_generation_stale')
                    identity = 'coding-mapping:' + uuid.uuid4().hex
                    admission_id = 'coding-admission:' + uuid.uuid4().hex
                    grant = _canonical(dict(format='workflow-coding-mapping-grant@1', setups=roots))
                    envelope = _capture(capture, admission_id, grant)
                    pin = envelope['pin']
                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    issuer, _, _, _ = native_owner(self.org, principal, conn)
                    bindings, proofs = [], []
                    checks = {key: True for key in RepositoryAdoptionChecks.model_fields}
                    for (selector, _, _), observation in zip(stores, observed, strict=True):
                        operation = 'coding-adopt:' + _digest(_canonical([identity, selector['repository_id'], selector['store_owner']]))
                        proof = dict(format='workflow-repository-adoption-proof@1', repository_id=selector['repository_id'],
                            operation_id=operation, issuer=issuer, authority=pin, store_identity=observation['store_identity'],
                            configured_origin_digest=observation['origin_digest'], observed_origin_digest=observation['origin_digest'],
                            object_format=observation['object_format'], policy='adopted-local-store@1', checks=checks, observed_at=now)
                        binding = dict(format='workflow-repository-binding@1', action='read-repository-artifact',
                            org_slug=self.org.slug, repository_id=selector['repository_id'], repo_key=selector['repo_key'],
                            origin_digest=observation['origin_digest'], store_owner=selector['store_owner'], authority=pin,
                            recipients=[dict(role=role, kind='agent', **command['mapping']['roles'][role]) for role in ROLES],
                            reader='local-git-objects@1')
                        adoption = dict(format='workflow-repository-adoption@1', operation_id=operation,
                            store_identity_digest=_digest(_canonical(observation['store_identity'])), adopted_by=principal.principal_id,
                            authority=pin, policy='adopted-local-store@1')
                        bindings.append(dict(binding=binding, adoption=adoption))
                        proofs.append(proof)
                    mapping = dict(command['mapping'])
                    del mapping['repository_selectors']
                    mapping.update(format='workflow-coding-mapping@1', repository_bindings=bindings)
                    record = _canonical(dict(format='workflow-coding-mapping-record@1', command_bytes_base64=_b64(raw),
                        command_digest=_digest(raw), issuer=issuer, authority=envelope, adoptions=proofs, mapping=mapping, created_at=now))
                    read_saved_mapping_record(record, expected_org_slug=self.org.slug)
                    body = capture_body(self.org, principal, conn, admission_id=admission_id, mapping_id=identity,
                        raw=raw, now=now, max_revise_rounds=revise_limit)
                    if len(json.dumps(body).encode('utf8')) > 1048576:
                        raise CodingMappingError('coding_mapping_capture_bounds')
                    conn.execute('INSERT INTO workflow_admission_records (id,namespace,generation,request_digest,admitted_by) '
                        'VALUES (?,?,?,?,?)', (admission_id, pin['namespace'], pin['generation'], _digest(raw), principal.operation_principal))
                    conn.execute('INSERT INTO workflow_coding_mappings '
                        '(id,org_slug,release_identity,generation,principal,operation_key,request_digest,mapping_bytes,mapping_digest,created_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?)', (identity, self.org.slug, 'release/happyranch/coding', generation + 1,
                        principal.operation_principal, command['operation_key'], _digest(raw), record, _digest(record), now))
                    self.db.insert_audit_log_uncommitted(principal.task_id, principal.principal_id, 'coding_source_admission', body)
                    storage = self._storage(conn, references)  # FULL new cold closure before commit.
                    native_owner(self.org, principal, conn)
                    authority._validate_admission_uncommitted(conn, capture)
                    return self._receipt(storage.selected, replayed=False)
