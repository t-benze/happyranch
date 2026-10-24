"""Append-only org role defaults and nonallocating exact-subject selection.

Existing native authority/session/Pause owners admit publication. Descriptors
carry identity only; agents use their existing authorized repository tools.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

from runtime.orchestrator import prompt_loader
from runtime.orchestrator.org_config import load_org_config
from runtime.workflows._coding_import_support import CodingImportSupport, _capture
from runtime.workflows._coding_release_history import read_release_import
from runtime.workflows._coding_role_admission import capture_body, native_owner
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows._coding_role_records import (
    RECORD_FORMAT, RoleReceipt, SelectionReceipt, configuration,
    parse_role_command, parse_selection, read_role_record,
)
from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.coding_mapping_history import (
    CodingMappingHistory, HistoricalCodingMapping, read_mapping_storage,
)
from runtime.workflows.coding_saved_records import ROLES, _canonical, _check, _hash, _roles
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplatePrincipal


class CodingRoleError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CodingRoleStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self.support = CodingImportSupport(org)

    def _principal(self, principal: WorkflowTemplatePrincipal) -> None:
        principal.revalidate()
        if (principal.org_slug != self.org.slug or principal.principal_kind != 'agent'
                or principal.proof_kind != 'task_session' or principal.principal_id == 'founder'
                or not principal.task_id or not principal.session_id
                or self.org.teams.teams_for_manager(principal.principal_id) != (principal.team_slug,)):
            raise CodingRoleError('coding_role_manager_required')

    def _checkpoint(self, principal: WorkflowTemplatePrincipal, references: tuple[str, ...]) -> tuple:
        """Fresh native actor/policy after a wait, without a lease across awaits.

        File-backed discovery precedes the short SELECT-only ownership interval.
        A revoked publication remains metadata-only for original replay; it cannot
        become current permission in _current or the final admission writer.
        """
        self._principal(principal)
        paths = self.org.orchestrator._paths
        actor = prompt_loader.load_agent(paths, principal.principal_id)
        if (actor is None or actor.role != 'manager' or not actor.executor
                or actor.team != principal.team_slug):
            raise CodingRoleError('coding_role_manager_revoked')
        revise_limit = load_org_config(paths).max_revise_rounds
        capture = self.support._discover()
        with self._read(principal, capture, references) as (conn, current):
            if current is not None:
                try:
                    self.org.workflow_authority._validate_admission_uncommitted(conn, current)
                except (WorkflowAuthorityError, ProfileCoordinatorError):
                    current = None
            return current, revise_limit

    @asynccontextmanager
    async def _access(self, principal: WorkflowTemplatePrincipal) -> AsyncIterator:
        """Sole service lock owner: authority -> teams -> DB -> binding/read.

        Public callers stream outside this interval. Existing roster writers and
        their awaited bootstrap/compensation retain their authority-first owner.
        Neither direct callers nor routes may enter with teams/authority held.
        """
        self._principal(principal)
        references = self.support._schema_references()
        async with self.org.workflow_authority._async_writer_lock:
            self._principal(principal)
            # Native checks keep DB -> binding ordering even at the first
            # checkpoint. Release DB before waiting for teams; existing team
            # writers may need it while finishing their authority interval.
            async with self.org.db_lock:
                self._checkpoint(principal, references)
            async with self.org.teams_lock:
                self._principal(principal)
                async with self.org.db_lock:
                    capture, revise_limit = self._checkpoint(principal, references)
                    yield references, capture, revise_limit

    def _structure(self, conn: sqlite3.Connection, references: tuple[str, ...]) -> None:
        rows = conn.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')
        if _hash('\n'.join(str(row[0]) for row in rows).encode()) not in references:
            raise CodingRoleError('coding_role_whole_schema_mismatch')

    @contextmanager
    def _read(self, principal: WorkflowTemplatePrincipal, capture: AuthorityAdmissionCapture | None,
              references: tuple[str, ...]) -> Iterator:
        # Never hold the native threading binding lease across an await.
        with self.org.sessions._get_binding_lease(principal.task_id, principal.principal_id):
            self._principal(principal)
            with self.support._read(capture) as (conn, current):
                self._structure(conn, references)
                native_owner(self.org, principal, conn)  # Before private decoding.
                yield conn, current

    def _storage(self, conn: sqlite3.Connection, references: tuple[str, ...]) -> CodingMappingHistory | None:
        self.support._storage(conn, references)  # Complete central cold/native/typed closure.
        if read_release_import(conn, expected_org_slug=self.org.slug) is None:
            raise CodingRoleError('coding_role_release_import_required')
        return read_mapping_storage(conn, expected_org_slug=self.org.slug)

    def _current(self, conn: sqlite3.Connection, principal: WorkflowTemplatePrincipal,
                 capture: AuthorityAdmissionCapture | None, mapping: dict, *, fresh: bool = False,
                 command: dict | None = None, budget: _Budget | None = None) -> None:
        self._principal(principal)
        native_owner(self.org, principal, conn)
        if capture is None:
            raise CodingRoleError('coding_role_authority_unavailable')
        authority = self.org.workflow_authority
        authority._validate_admission_uncommitted(conn, capture)
        if mapping['roles']['agent_manager'] != dict(principal=principal.principal_id, team=principal.team_slug):
            raise CodingRoleError('coding_role_manager_revoked')
        if fresh and mapping['authority'] != dict(namespace=capture.ready.namespace,
                generation=capture.ready.generation, snapshot_digest=capture.ready.snapshot_digest):
            raise CodingRoleError('coding_role_authority_stale')
        try:
            snapshot = json.loads(capture.ready.snapshot_bytes)
            if command is None:
                configuration(mapping, snapshot)
            else:
                from runtime.workflows._coding_settings_execution import _configuration
                _check(type(budget) is _Budget)
                _configuration(mapping, snapshot, command, budget)
        except ValueError:
            raise CodingRoleError('coding_role_eligibility_revoked') from None
        authority._validate_admission_uncommitted(conn, capture)

    def _receipt(self, conn: sqlite3.Connection, principal: WorkflowTemplatePrincipal,
                 capture: AuthorityAdmissionCapture | None, entry: HistoricalCodingMapping,
                 *, replayed: bool) -> dict:
        record = json.loads(entry.mapping_bytes)
        if record['format'] != RECORD_FORMAT:
            raise CodingRoleError('coding_role_agent_read_generation_required')
        current = dict(state='active', reason=None)
        try:
            self._current(conn, principal, capture, record['mapping'])
        except (CodingRoleError, WorkflowAuthorityError, ProfileCoordinatorError):
            current = dict(state='revoked', reason='coding_role_current_authority_revoked')
        return RoleReceipt.model_validate(dict(format='workflow-coding-mapping-receipt@2',
            id=entry.mapping_id, generation=entry.generation, mapping_digest=entry.mapping_digest,
            mapping=record['mapping'], replayed=replayed, current=current)).model_dump()

    def _replay(self, conn: sqlite3.Connection, principal: WorkflowTemplatePrincipal,
                capture: AuthorityAdmissionCapture | None, history: CodingMappingHistory | None,
                command: dict, raw: bytes) -> dict | None:
        for entry in (() if history is None else history.mappings):
            if (entry.principal, entry.operation_key) == (principal.operation_principal, command['operation_key']):
                if entry.command_bytes != raw:
                    raise CodingRoleError('coding_role_operation_conflict')
                return self._receipt(conn, principal, capture, entry, replayed=True)
        return None

    async def get(self, *, principal: WorkflowTemplatePrincipal) -> dict:
        return await self._select(principal=principal, raw=None)

    async def select(self, *, principal: WorkflowTemplatePrincipal, raw: bytes) -> dict:
        return await self._select(principal=principal, raw=raw)

    async def _select(self, *, principal: WorkflowTemplatePrincipal, raw: bytes | None) -> dict:
        async with self._access(principal) as (references, capture, _):
            with self._read(principal, capture, references) as (conn, current):
                try:
                    command = None if raw is None else parse_selection(raw)
                except ValueError:
                    raise CodingRoleError('coding_role_invalid_request') from None
                history = self._storage(conn, references)
                if history is None:
                    raise CodingRoleError('coding_role_not_found')
                entry = history.selected
                if command is not None:
                    entries = [item for item in history.mappings if item.mapping_id == command['mapping']['id']]
                    if len(entries) != 1:
                        raise CodingRoleError('coding_role_not_found')
                    entry = entries[0]
                    if command['mapping'] != dict(id=entry.mapping_id, generation=entry.generation,
                                                 digest=entry.mapping_digest):
                        raise CodingRoleError('coding_role_pin_conflict')
                receipt = self._receipt(conn, principal, current, entry, replayed=False)
                if receipt['current']['state'] != 'active':
                    raise CodingRoleError('coding_role_eligibility_revoked')
                if command is None:
                    self._current(conn, principal, current, receipt['mapping'])
                    return receipt
                mapping = receipt['mapping']
                roles = json.loads(_canonical(mapping['roles']))
                override = command['implementer_override']
                if override is not None:
                    if override not in mapping['eligible_replacements']['implementer']:
                        raise CodingRoleError('coding_role_override_revoked')
                    roles['implementer'] = override
                # Complete central storage now admits only K3a queued bootstrap
                # graphs. This independent selector allocates no work or grant;
                # start owns and verifies its initial genuine contributor graph.
                _roles(roles, {role: [] for role in ROLES})
                selected_mapping = {**mapping, 'roles': roles,
                    'eligible_replacements': {role: [] for role in ROLES}}
                self._current(conn, principal, current, selected_mapping)
                subject = command['subject']
                references_selected = [r for r in mapping['repository_references']
                    if r['repository_id'] == subject['repository']['id']
                    and r['object_format'] == subject['object_format']]
                if len(references_selected) != 1:
                    raise CodingRoleError('coding_role_repository_revoked')
                result = SelectionReceipt.model_validate(dict(format='workflow-coding-role-selection@1',
                    mapping=command['mapping'], roles=roles, subject=subject,
                    overridden=override is not None, coding_enabled=False)).model_dump()
                self._current(conn, principal, current, selected_mapping)
                return result

    async def publish(self, *, principal: WorkflowTemplatePrincipal, raw: bytes) -> dict:
        authority = self.org.workflow_authority
        async with self._access(principal) as (references, capture, revise_limit):
            with self._read(principal, capture, references) as (conn, current):
                try:
                    command = parse_role_command(raw, org_slug=self.org.slug)
                except ValueError:
                    raise CodingRoleError('coding_role_invalid_request') from None
                history = self._storage(conn, references)
                replay = self._replay(conn, principal, current, history, command, raw)
                if replay is not None:
                    return replay  # Original full bytes before fresh CAS/current pin.
                self._current(conn, principal, current, command['mapping'], fresh=True)
            if capture is None:
                raise CodingRoleError('coding_role_authority_unavailable')
            if load_org_config(self.org.orchestrator._paths).max_revise_rounds != revise_limit:
                raise CodingRoleError('coding_role_policy_stale')
            with self.org.sessions._get_binding_lease(principal.task_id, principal.principal_id):
                try:
                    with authority.admission_writer(capture) as conn:
                        self._principal(principal)
                        self._structure(conn, references)
                        native_owner(self.org, principal, conn)
                        history = self._storage(conn, references)
                        replay = self._replay(conn, principal, capture, history, command, raw)
                        if replay is not None:
                            return replay
                        if history is not None and any(
                                entry.principal == 'human:founder' for entry in history.mappings):
                            raise CodingRoleError('coding_settings_human_owned')
                        self._current(conn, principal, capture, command['mapping'], fresh=True)
                        generation = 0 if history is None else history.selected.generation
                        if generation != command['expected_generation']:
                            raise CodingRoleError('coding_role_generation_stale')
                        identity = 'coding-mapping:' + uuid.uuid4().hex
                        admission = 'coding-admission:' + uuid.uuid4().hex
                        envelope = _capture(capture, admission, raw)
                        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        issuer, _, _, _ = native_owner(self.org, principal, conn)
                        record = _canonical(dict(format=RECORD_FORMAT, org_slug=self.org.slug,
                            command_bytes_base64=base64.b64encode(raw).decode('ascii'),
                            command_digest=_hash(raw), issuer=issuer, authority=envelope,
                            mapping=command['mapping'], created_at=now))
                        read_role_record(record, expected_org_slug=self.org.slug)
                        body = capture_body(self.org, principal, conn, admission_id=admission,
                            mapping_id=identity, raw=raw, now=now, max_revise_rounds=revise_limit)
                        if len(json.dumps(body).encode('utf8')) > 1048576:
                            raise CodingRoleError('coding_role_capture_bounds')
                        pin = envelope['pin']
                        conn.execute('INSERT INTO workflow_admission_records '
                            '(id,namespace,generation,request_digest,admitted_by) VALUES (?,?,?,?,?)',
                            (admission, pin['namespace'], pin['generation'], _hash(raw), principal.operation_principal))
                        conn.execute('INSERT INTO workflow_coding_mappings '
                            '(id,org_slug,release_identity,generation,principal,operation_key,request_digest,mapping_bytes,mapping_digest,created_at) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?)', (identity, self.org.slug, 'release/happyranch/coding',
                            generation + 1, principal.operation_principal, command['operation_key'], _hash(raw),
                            record, _hash(record), now))
                        self.db.insert_audit_log_uncommitted(principal.task_id, principal.principal_id,
                            'coding_source_admission', body)
                        history = self._storage(conn, references)  # Full new cold closure before COMMIT.
                        self._current(conn, principal, capture, command['mapping'], fresh=True)
                        return self._receipt(conn, principal, capture, history.selected, replayed=False)
                except sqlite3.IntegrityError:
                    raise CodingRoleError('coding_role_storage_conflict') from None
