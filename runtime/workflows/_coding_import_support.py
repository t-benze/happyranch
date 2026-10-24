"""Native admission support for fixed release import; no capability producer.

Extracted from the former draft setup owner. Preserve Founder admission,
independent whole-schema references and SELECT-only snapshot ordering. Central
validation retains all supported historical families without requiring issuance.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Iterator

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplatePrincipal

if TYPE_CHECKING:
    from runtime.infrastructure.workflow_coding_schema import _CodingInventory


class CodingReleaseImportError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _capture(capture: AuthorityAdmissionCapture, admission_id: str, raw: bytes) -> dict:
    # Lossless coding adapter of the native capture, without document-only
    # cutover/actions. Full mirror and original snapshot are never filtered.
    fields = {'dependency': ('kind', 'org_namespace', 'profile_name', 'consumer_identity', 'bound_generation', 'state'),
              'store': ('kind', 'profile_name', 'generation', 'profile_digest', 'state'),
              'registry': ('kind', 'profile_name', 'published_generation')}
    ready = capture.ready
    return dict(pin=dict(namespace=ready.namespace, generation=ready.generation,
                         snapshot_digest=ready.snapshot_digest),
        admission_record_id=admission_id, snapshot_bytes_base64=base64.b64encode(ready.snapshot_bytes).decode('ascii'),
        profile_evidence=dict(ready=dict(namespace=ready.namespace, generation=ready.generation,
            snapshot_bytes_hex=ready.snapshot_bytes.hex(), snapshot_digest=ready.snapshot_digest),
            pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'),
                             capture.pointer, strict=True)),
            profiles=[dict(name=name, digest=digest) for name, digest in capture.profile_digests],
            mirror=[dict(zip(fields[item[0]], item, strict=True)) for item in capture.profile_mirror]),
        grant_bytes_base64=base64.b64encode(raw).decode('ascii'), grant_digest=hashlib.sha256(raw).hexdigest())


class CodingImportSupport:
    def __init__(self, org) -> None:
        self.org = org
        self.db = org.db

    def _principal(self, principal: WorkflowTemplatePrincipal) -> None:
        if (principal.org_slug != self.org.slug or principal.principal_kind != 'human'
                or principal.principal_id != 'founder' or principal.proof_kind != 'founder_bearer'
                or principal.task_id is not None or principal.session_id is not None):
            raise CodingReleaseImportError('human_only')
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
            raise CodingReleaseImportError('coding_release_import_schema_reference_unavailable')
        return references

    def _storage(self, conn: sqlite3.Connection, references: tuple[str, ...]) -> _CodingInventory:
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        # The private result is useful only to a local consumer that still owns
        # this exact snapshot. Never return an inventory after ending its read.
        if not conn.in_transaction:
            raise CodingReleaseImportError('coding_release_import_caller_transaction')
        layout, coding = _read_workflow_schema(conn, expected_org_slug=self.org.slug)
        if layout != 'J' or coding is None:
            raise CodingReleaseImportError('coding_release_import_schema_required')
        rows = conn.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')
        actual = hashlib.sha256('\n'.join(str(row[0]) for row in rows).encode()).hexdigest()
        if actual not in references:
            raise CodingReleaseImportError('coding_release_import_whole_schema_mismatch')
        # Central validation above includes complete retained setup, import,
        # mapping and source histories; no setup issuance is required.
        return coding

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
                raise CodingReleaseImportError('coding_release_import_caller_transaction')
            conn.execute('BEGIN')
            try:
                yield conn, capture
            finally:
                conn.rollback()

    def _discover(self) -> AuthorityAdmissionCapture | None:
        try:
            return self.org.workflow_authority.capture_admission()
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            return None
