"""Native admission support for fixed release import; no capability producer.

Preserve Founder admission and SELECT-only snapshot ordering. Central scoped
storage validation retains supported historical families without requiring
issuance. Generic authority keeps its independent whole-database references.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
from contextlib import AbstractContextManager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Callable

from runtime.infrastructure.workflow_schema import _schema_read_outcomes
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


_selected_read: ContextVar[Any] = ContextVar('coding_selected_read', default=None)


class _CodingReadSignal(BaseException):
    """Only the unchanged profile generator transports this captured cause."""
    def __init__(self, frame: _CodingReadFrame) -> None:
        self.frame = frame
        super().__init__()


class _CodingReadFrame:
    """Custody for one selected coding read/access, including failed attachments.

    This is not an exception-attribute certificate. The settings consumer keeps
    its ORIGINAL access frame; nested frames join it only while that access is
    active. An old frame on a reused exception cannot qualify another interval.
    """
    def __init__(self, support: CodingImportSupport) -> None:
        self.support = support
        self.previous_owner = getattr(support, '_coding_read_frame', None)
        self.parent = _selected_read.get()
        self.children: list[_CodingReadFrame] = []
        self.primary = self.traceback = None
        self.primary_occurrence = self.exit_call = None
        self.records: list[dict] = []
        self.last_occurrences: dict[int, dict] = {}
        self.exits: list[dict] = []
        self.cleanups: list[dict] = []
        self.pending: list[dict] = []
        self.failed = self.finished = self.evidence_complete = False
        if self.parent is not None:
            self.parent.children.append(self)
        self.token = _selected_read.set(self)
        self.schema_outcomes: dict = dict(owners=[], records=[])
        self.schema_ingested = 0
        self.schema_token = _schema_read_outcomes.set(self.schema_outcomes)

    def record(self, phase: str, error: BaseException, *, later: bool = False,
               handled: bool = False, exit_record: dict | None = None) -> None:
        # Native capture precedes every attachment, diagnostic or virtual access.
        occurrence = dict(phase=phase, error=error,
            traceback=BaseException.__traceback__.__get__(error),
            arguments=BaseException.args.__get__(error),
            context=BaseException.__context__.__get__(error),
            cause=BaseException.__cause__.__get__(error),
            suppress_context=BaseException.__suppress_context__.__get__(error),
            previous=None, nested=None,
            previous_occurrence=None, handled=handled, observation='observation' in phase,
            source_occurrence=None)
        self.ingest_schema()
        self.records.append(occurrence)
        source = None
        if exit_record is not None:
            # An exit's phase/error identity cannot prove child transport. Only
            # the original child can publish a delivery during THIS exit call.
            occurrence['observation'] = False
            child = exit_record.get('read_frame')
            delivered = exit_record.get('delivery')
            if (child is not None and child.parent is self and child.exit_call is exit_record
                    and child.finished and delivered is not None
                    and delivered is child.primary_occurrence and delivered['error'] is error):
                source = occurrence['source_occurrence'] = delivered
                occurrence['observation'] = True
        self.evidence_complete = False
        self.support._coding_read_frame = self
        if self.primary is None and not handled:
            self.primary = error
            self.primary_occurrence = occurrence if source is None else source
            self.traceback = self.primary_occurrence['traceback']
        self.failed |= later
        values = BaseException.__dict__['__dict__'].__get__(error)
        occurrence['previous'] = values.get('_coding_read_frame')
        occurrence['previous_occurrence'] = self.last_occurrences.get(id(error), values.get('_coding_read_occurrence'))
        self.last_occurrences[id(error)] = occurrence  # Link, never deduplicate.
        occurrence['nested'] = (values.get('_workflow_authority_failure'),
                                values.get('_native_writer_disposition'))

    def enter(self, context: Any, phase: str, *, profile: bool = False) -> Any:
        kind = type(context)
        exit_record = dict(context=context, exit=kind.__exit__, phase=phase,
                           profile=profile, acquired=False, attempted=False, returned=False, suppressed=False)
        self.exits.append(exit_record)
        self.pending.append(exit_record)
        result = kind.__enter__(context)
        exit_record['acquired'] = True  # Only a returned enter owes an exit.
        return result

    async def aenter(self, context: Any, phase: str) -> Any:
        kind = type(context)
        exit_record = dict(context=context, exit=kind.__aexit__, phase=phase,
                           acquired=False, attempted=False, returned=False, suppressed=False)
        self.exits.append(exit_record)
        self.pending.append(exit_record)
        result = await kind.__aenter__(context)
        exit_record['acquired'] = True
        return result

    def _args(self, exit_record: dict) -> tuple:
        if self.primary is None:
            return None, None, None
        if exit_record.get('profile', False):
            # contextlib may restore an inbound traceback virtually. The actual
            # cause/native traceback stays in this frame throughout both nested
            # profile generators; only this private signal crosses them.
            signal = _CodingReadSignal(self)
            return type(signal), signal, None
        return type(self.primary), self.primary, self.traceback

    def close_one(self) -> None:
        exit_record = self.pending.pop()
        if not exit_record['acquired']:
            self.failed |= exit_record.get('handled_control') is not True
            # Unknown enter is never an owed release/retry. The original
            # specifically handled profile refusal owes no outer profile exit.
            return
        exit_record['attempted'] = True
        child = exit_record.get('read_frame')
        exit_record['delivery'] = None
        if child is not None:
            child.exit_call = exit_record
        try:
            result = exit_record['exit'](exit_record['context'], *self._args(exit_record))
            exit_record['returned'] = True
            exit_record['suppressed'] = bool(result)
        except BaseException as error:
            self.record(exit_record['phase'], error, later=True, exit_record=exit_record)
        finally:
            if child is not None:
                child.exit_call = None

    async def aclose_one(self) -> None:
        exit_record = self.pending.pop()
        if not exit_record['acquired']:
            self.failed = True
            return
        exit_record['attempted'] = True
        try:
            result = await exit_record['exit'](exit_record['context'], *self._args(exit_record))
            exit_record['returned'] = True
            exit_record['suppressed'] = bool(result)
        except BaseException as error:
            self.record(exit_record['phase'], error, later=True)

    def ingest_schema(self) -> None:
        # Only THIS original invocation's native channel is authoritative. An
        # attachment on a reused exception is historical, never current proof.
        for occurrence in self.schema_outcomes['records'][self.schema_ingested:]:
            self.records.append(occurrence)
            self.schema_ingested += 1
            if self.primary is None:
                self.primary = occurrence['error']
                self.primary_occurrence = occurrence
                self.traceback = occurrence['traceback']
            self.last_occurrences[id(occurrence['error'])] = occurrence
        if any(not owner.finished or not owner.evidence_complete
               for owner in self.schema_outcomes['owners']):
            self.failed = True
        self.evidence_complete = False

    def finish(self) -> None:
        self.ingest_schema()
        # Every occurrence is retained, even the same object thrown twice. A
        # failed attachment is captured once on this raw frame, never retried.
        for occurrence in tuple(self.records):
            error = occurrence['error']
            try:
                BaseException.__setattr__(error, '_coding_read_occurrence', occurrence)
                BaseException.__setattr__(error, '_coding_read_frame', self)
            except BaseException as attachment:
                self.record('attachment', attachment, later=True)
        if self.primary is not None and (self.failed or any(not c.evidence_complete for c in self.children)):
            try:
                BaseException.add_note(self.primary, 'selected coding read cleanup failed; actual occurrences retained by original frame')
            except BaseException as diagnostic:
                self.record('diagnostic', diagnostic, later=True)
        try:
            _schema_read_outcomes.reset(self.schema_token)
        except BaseException as restoration:
            self.record('schema context restoration', restoration, later=True)
        try:
            _selected_read.reset(self.token)
        except BaseException as restoration:
            self.record('selected context restoration', restoration, later=self.primary is not None)
        self.finished = True
        # Positive evidence is LAST and belongs only to this original frame.
        self.evidence_complete = (not self.failed and not self.pending
            and all((not e['acquired'] and e.get('handled_control') is True)
                or (e['acquired'] and e['attempted'] and e['returned'] and not e['suppressed'])
                for e in self.exits)
            and all(c['attempted'] and c['returned'] for c in self.cleanups)
            and all(c.evidence_complete for c in self.children))

    def clean(self, error: BaseException) -> bool:
        return self.finished and self.evidence_complete and self.primary is error

    def deliver(self, inbound: BaseException | None = None, traceback: Any = None) -> bool:
        if self.primary is None:
            return False
        error = self.primary
        first = self.primary_occurrence
        BaseException.args.__set__(error, first['arguments'])
        BaseException.__traceback__.__set__(error, self.traceback)
        BaseException.__context__.__set__(error, first['context'])
        BaseException.__cause__.__set__(error, first['cause'])
        BaseException.__suppress_context__.__set__(error, first['suppress_context'])
        if error is inbound:
            return False
        if self.finished and self.exit_call is not None:
            # Delivery is provenance, never positive cleanup evidence. Failed
            # attachments leave this source reachable on the original frames.
            self.exit_call['delivery'] = self.primary_occurrence
        raise error


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

    def _read(self, capture: AuthorityAdmissionCapture | None, *,
              validate_admission: Callable[[], None] | None = None) -> AbstractContextManager[tuple[sqlite3.Connection, AuthorityAdmissionCapture | None]]:
        """SELECT-only, no durable admission lease, no file reads in snapshot."""
        support = self

        class _Read:
            def __init__(self) -> None:
                self.frame = None
                self.conn = None
                self.began = False
                self.begin_pending = False
                self.rollback = None

            def __enter__(self) -> tuple[sqlite3.Connection, AuthorityAdmissionCapture | None]:
                frame = self.frame = _CodingReadFrame(support)
                if frame.parent is not None and frame.parent.pending:
                    original_exit = frame.parent.pending[-1]
                    if (original_exit['context'] is self and not original_exit['acquired']
                            and not original_exit['attempted']):
                        original_exit['read_frame'] = frame
                current = capture
                try:
                    if validate_admission is not None:
                        validate_admission()
                    authority = support.org.workflow_authority
                    if current is not None:
                        coordinator = authority._profile_coordinator
                        try:
                            for name in current.profile_names:
                                frame.enter(coordinator.profile_read(name), 'profile fence exit', profile=True)
                                if validate_admission is not None:
                                    validate_admission()
                        except ProfileCoordinatorError as control:
                            frame.record('revoked profile metadata fallback', control, handled=True)
                            if frame.pending and not frame.pending[-1]['acquired']:
                                frame.pending[-1]['handled_control'] = True
                            while frame.pending:
                                frame.close_one()
                            if frame.primary is not None:
                                frame.deliver()
                            current = None
                    frame.enter(authority._publisher_lock, 'publisher fence exit')
                    if validate_admission is not None:
                        validate_admission()
                    frame.enter(support.db._lock, 'database fence exit')
                    if validate_admission is not None:
                        validate_admission()
                    conn = self.conn = support.db._conn
                    if conn.in_transaction:
                        raise CodingReleaseImportError('coding_release_import_caller_transaction')
                    self.begin_pending = True
                    conn.execute('BEGIN')
                    self.begin_pending = False
                    self.began = True
                    self.rollback = dict(phase='read rollback', attempted=False, returned=False)
                    frame.cleanups.append(self.rollback)
                    if validate_admission is not None:
                        validate_admission()
                    return conn, current
                except BaseException as error:
                    frame.record('read acquisition observation', error)
                self.__exit__(None, None, None)
                raise AssertionError('unreachable selected read failure')

            def __exit__(self, kind: Any, error: BaseException | None, traceback: Any) -> bool:
                frame = self.frame
                frame.failed |= self.begin_pending
                if error is not None:
                    frame.record('read body observation', error)
                elif frame.primary is None and self.began and validate_admission is not None:
                    try:
                        validate_admission()
                    except BaseException as validation:
                        frame.record('read final validation', validation)
                if self.began:
                    self.began = False  # The original rollback is owed ONCE.
                    self.rollback['attempted'] = True
                    try:
                        self.conn.rollback()
                        self.rollback['returned'] = True
                    except BaseException as rollback:
                        frame.record('read rollback', rollback, later=True)
                while frame.pending:
                    frame.close_one()
                frame.finish()
                return frame.deliver(error, traceback)

        return _Read()

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
