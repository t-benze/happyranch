"""Native admission support for fixed release import; no capability producer.

Preserve Founder admission and SELECT-only snapshot ordering. Central scoped
storage validation retains supported historical families without requiring
issuance. Generic authority keeps its independent whole-database references.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Callable, Iterator

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplatePrincipal

if TYPE_CHECKING:
    from runtime.infrastructure.workflow_coding_schema import _CodingInventory
    from runtime.workflows.coding_authority_history import _Budget


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
    def __init__(self, org, owner=None) -> None:
        self.owner = owner
        self.org = org
        self.db = org.db

    def _principal(self, principal: WorkflowTemplatePrincipal) -> None:
        if (principal.org_slug != self.org.slug or principal.principal_kind != 'human'
                or principal.principal_id != 'founder' or principal.proof_kind != 'founder_bearer'
                or principal.task_id is not None or principal.session_id is not None):
            raise CodingReleaseImportError('human_only')
        principal.revalidate()

    def _schema_references(self) -> tuple[str, ...]:
        # Retain the existing pre-admission call boundary. Coding storage is
        # validated on the actual snapshot, independently of unrelated native
        # table ALTER chronology. Generic authority keeps whole-DB references.
        return ()

    def _storage(self, conn: sqlite3.Connection, references: tuple[str, ...], *,
                 budget: _Budget | None = None) -> _CodingInventory:
        """Reconstruct locally; supplied allowance retains all source/target work."""
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        if not conn.in_transaction:
            raise CodingReleaseImportError('coding_release_import_caller_transaction')
        _, coding = _read_workflow_schema(conn, expected_org_slug=self.org.slug, budget=budget)
        if coding is None:
            raise CodingReleaseImportError('coding_release_import_schema_required')
        return coding

    @contextmanager
    def _read(self, capture: AuthorityAdmissionCapture | None, *,
              validate_admission: Callable[[], None] | None = None) -> Iterator[tuple[sqlite3.Connection, AuthorityAdmissionCapture | None]]:
        """SELECT-only, no durable admission lease, no file reads in snapshot."""
        if validate_admission is not None:
            validate_admission()
        authority = self.org.workflow_authority
        with ExitStack() as fences:
            if capture is not None:
                coordinator = authority._profile_coordinator
                try:
                    for name in capture.profile_names:
                        fences.enter_context(coordinator.profile_read(name))
                        if validate_admission is not None:
                            validate_admission()
                except ProfileCoordinatorError:
                    # Safe original metadata remains readable under revocation;
                    # no failed profile lease becomes active authority.
                    fences.close()
                    capture = None
            fences.enter_context(authority._publisher_lock)
            if validate_admission is not None:
                validate_admission()
            fences.enter_context(self.db._lock)
            if validate_admission is not None:
                validate_admission()
            conn = self.db._conn
            if conn.in_transaction:
                raise CodingReleaseImportError('coding_release_import_caller_transaction')
            conn.execute('BEGIN')
            try:
                if validate_admission is not None:
                    validate_admission()
                yield conn, capture
            finally:
                conn.rollback()

    def _discover(self, *, validate_admission: Callable[[], None] | None = None) -> AuthorityAdmissionCapture | None:
        if validate_admission is not None:
            validate_admission()
        try:
            capture = self.org.workflow_authority.capture_admission()
            if validate_admission is not None:
                validate_admission()
            return capture
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            return None
