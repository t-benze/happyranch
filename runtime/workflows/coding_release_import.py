"""Explicit fixed coding release import, without coding allocation or activation."""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.workflows._coding_release_history import (
    NAMESPACE, PUBLISHER, ReleaseImportHistory,
    parse_import_command, publication_rows, read_release_import,
)
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows._coding_import_support import CodingImportSupport, CodingReleaseImportError, _capture
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.release_templates import (
    ReleaseTemplateError, ReleaseTemplateReceipt, coding_release_projection, read_coding_release,
)
from runtime.workflows.templates import WorkflowTemplatePrincipal


class ImportCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['workflow-coding-release-import-command@1']
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    expected_current_version: Annotated[int, Field(ge=0, le=1)]


class ImportCurrent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    state: Literal['admitted', 'unavailable']
    reason: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')] | None

    @model_validator(mode='after')
    def disposition(self) -> ImportCurrent:
        if (self.state == 'unavailable') != (self.reason is not None):
            raise ValueError('coding_release_import_receipt_invalid')
        return self


class ImportReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['workflow-coding-release-import-receipt@1']
    identity_id: str
    version_id: str
    admission_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
    request_digest: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    replayed: bool
    current: ImportCurrent
    release: ReleaseTemplateReceipt

    @model_validator(mode='after')
    def identities(self) -> ImportReceipt:
        from runtime.workflows.templates import _stable_id
        identity = _stable_id('workflow-template', NAMESPACE, 'coding')
        version = _stable_id('workflow-template-version', identity, 1, self.release.definition_digest)
        if self.identity_id != identity or self.version_id != version:
            raise ValueError('coding_release_import_receipt_invalid')
        return self


class CodingReleaseImportStore:
    def __init__(self, org) -> None:
        self.org = org
        self._support = CodingImportSupport(org)

    def _principal(self, principal: WorkflowTemplatePrincipal) -> None:
        # Reuse the genuine Founder predicate. Manager/source imports stay
        # unavailable in this Founder-only unit.
        self._support._principal(principal)

    def _current(self, conn, capture) -> dict:
        if capture is None:
            return dict(state='unavailable', reason='coding_release_import_authority_unavailable')
        try:
            authority = self.org.workflow_authority
            for name in capture.profile_names:
                authority._profile_coordinator._assert_no_active_operation(name)
            authority._validate_admission_uncommitted(conn, capture)
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            return dict(state='unavailable', reason='coding_release_import_authority_revoked')
        return dict(state='admitted', reason=None)

    def _receipt(self, conn, history: ReleaseImportHistory, capture, *, replayed: bool) -> dict:
        command = parse_import_command(history.command_bytes)
        return ImportReceipt.model_validate(dict(format='workflow-coding-release-import-receipt@1',
            identity_id=history.identity_id, version_id=history.version_id,
            admission_id=history.provenance['admission']['record']['id'],
            operation_key=command['operation_key'], request_digest=history.provenance['command_digest'],
            replayed=replayed, current=self._current(conn, capture),
            release=coding_release_projection(history.template().definition_bytes))).model_dump()

    def _replay(self, conn, raw: bytes, capture) -> dict | None:
        history = read_release_import(conn, expected_org_slug=self.org.slug)
        command = parse_import_command(raw)
        prior = conn.execute('SELECT template_identity_id FROM workflow_template_publish_operations '
            'WHERE org_slug=? AND principal=? AND operation_key=?',
            (self.org.slug, 'human:founder', command['operation_key'])).fetchone()
        if prior is not None:
            if (history is None or history.command_bytes != raw
                    or prior['template_identity_id'] != history.identity_id):
                raise CodingReleaseImportError('coding_release_import_operation_conflict')
            return self._receipt(conn, history, capture, replayed=True)
        if history is not None:
            raise CodingReleaseImportError('coding_release_import_already_imported')
        if command['expected_current_version'] != 0:
            raise CodingReleaseImportError('coding_release_import_version_stale')
        return None

    async def get(self, *, principal: WorkflowTemplatePrincipal) -> dict:
        self._principal(principal)  # Entitlement before private lookup/decode.
        references = self._support._schema_references()
        capture = self._support._discover()
        async with self.org.workflow_authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._support._read(capture) as (conn, current_capture):
                    self._support._storage(conn, references)
                    history = read_release_import(conn, expected_org_slug=self.org.slug)
                    if history is None:
                        raise CodingReleaseImportError('coding_release_import_not_found')
                    receipt = self._receipt(conn, history, current_capture, replayed=False)
                    self._principal(principal)
                    return receipt

    async def import_release(self, *, principal: WorkflowTemplatePrincipal, raw: bytes) -> dict:
        self._principal(principal)
        try:
            parse_import_command(raw)
        except ValueError:
            raise CodingReleaseImportError('coding_release_import_invalid') from None
        references = self._support._schema_references()
        # Package reads/validation and native discovery occur before waits and
        # outside writer locks; the final writer admits only these exact bytes.
        resource_error = None
        try:
            definition = read_coding_release()
        except ReleaseTemplateError as exc:
            resource_error = exc
            definition = None
        capture = self._support._discover()
        authority = self.org.workflow_authority
        async with authority._async_writer_lock:
            self._principal(principal)
            async with self.org.db_lock:
                self._principal(principal)
                with self._support._read(capture) as (conn, current_capture):
                    self._support._storage(conn, references)
                    replay = self._replay(conn, raw, current_capture)
                    self._principal(principal)
                    if replay is not None:
                        return replay
                if resource_error is not None:
                    raise resource_error
                if capture is None:
                    raise CodingReleaseImportError('coding_release_import_authority_unavailable')
                try:
                    with authority.admission_writer(capture) as conn:
                        self._principal(principal)
                        self._support._storage(conn, references)
                        replay = self._replay(conn, raw, capture)
                        if replay is not None:
                            self._principal(principal)
                            return replay
                        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        admission_id = 'coding-import-admission:' + uuid.uuid4().hex
                        digest = hashlib.sha256(raw).hexdigest()
                        admission = dict(id=admission_id, namespace=capture.ready.namespace,
                            generation=capture.ready.generation, request_digest=digest, admitted_by='human:founder')
                        provenance = dict(format='workflow-coding-release-import-provenance@1',
                            org_slug=self.org.slug, publisher=dict(PUBLISHER),
                            command_bytes_base64=base64.b64encode(raw).decode('ascii'), command_digest=digest,
                            admission=dict(record=admission, capture=_capture(capture, admission_id, raw)), created_at=now)
                        rows = publication_rows(org_slug=self.org.slug, definition=definition, provenance=provenance)
                        # Existing six native relations, one authentic transaction.
                        # All identifiers/columns/values derive from this closed
                        # server-selected family; no caller SQL or definition.
                        for table, row in rows.rows:
                            columns = ','.join(row)
                            placeholders = ','.join('?' for _ in row)
                            conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(row.values()))
                        self._support._storage(conn, references)  # Full cold closure before COMMIT.
                        history = read_release_import(conn, expected_org_slug=self.org.slug)
                        if history is None:
                            raise CodingReleaseImportError('coding_release_import_history_invalid')
                        receipt = self._receipt(conn, history, capture, replayed=False)
                        self._principal(principal)
                        authority._validate_admission_uncommitted(conn, capture)
                        return receipt
                except sqlite3.IntegrityError:
                    raise CodingReleaseImportError('coding_release_import_storage_conflict') from None
