"""Canonical Founder coding configuration; publication never enables execution.

No YAML, schema installer, task/session impersonation or launch owner belongs
here. The native reservation and authority lease retain their own boundaries.
"""
from __future__ import annotations

from runtime.daemon.startup_recovery import (
    StartupUnavailable, coding_admission_validation,
)

import sqlite3
from contextlib import AbstractAsyncContextManager, AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Any, Callable

from runtime.orchestrator.producer_move import native_writer
from runtime.workflows._coding_import_support import CodingImportSupport, _CodingReadFrame
from runtime.workflows._coding_settings_records import account, parse_command
from runtime.workflows._coding_settings_view import SettingsResolution, project, receipt
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_mapping_history import _read_mapping_storage
from runtime.workflows.profile_coordinator import ProfileCoordinatorError

if TYPE_CHECKING:
    from runtime.workflows.templates import WorkflowTemplatePrincipal


class CodingSettingsError(ValueError):
    def __init__(self, code: str, *, current=None, operation_key=None):
        self.code = code
        self.detail = dict(code=code)
        if current is not None:
            self.detail['current'] = current
        if operation_key is not None:
            self.detail['operation_key'] = operation_key
        super().__init__(code)


def _settings_has_notes(error: BaseException) -> bool:
    notes = BaseException.__dict__['__dict__'].__get__(error).get('__notes__')
    return notes is not None and (type(notes) is not list or len(notes) != 0)


def _resolve_history(history: Any, imported: Any, budget: _Budget, *,
                     pinned: dict | None = None) -> SettingsResolution:
    """Private core shared only by freshly reconstructed local inventories."""
    if history is not None and imported is None:
        raise ValueError('coding_settings_release_required')
    return project(history, budget, pinned=pinned)


def resolve_coding_settings(conn: sqlite3.Connection, org_slug: str, *,
                            budget: _Budget, pinned=None) -> SettingsResolution:
    """SELECT-only complete current/pinned closure in the caller's snapshot.

    Caller first admits the complete coding/shared schema. Current corruption always
    refuses; no old pin is substituted for healthy current state or new authority.
    No nested transaction, schema recursion, cache or per-component allowance.
    """
    try:
        if type(budget) is not _Budget or type(conn) is not sqlite3.Connection or not conn.in_transaction:
            raise ValueError('caller_owned_settings_read_required')
        history, _ = _read_mapping_storage(conn, expected_org_slug=org_slug, budget=budget)
        from runtime.workflows._coding_registered_release import read_registered_release
        imported = read_registered_release(conn, expected_org_slug=org_slug, budget=budget)
        return _resolve_history(history, imported, budget, pinned=pinned)
    except (ValueError, TypeError, KeyError, sqlite3.Error, RecursionError, OverflowError):
        raise CodingSettingsError('coding_settings_invalid') from None


class CodingSettingsStore:
    def __init__(self, org):
        self.org, self.db = org, org.db
        self._startup_state = self._startup_org = self._startup_recovery = None
        self.support = CodingImportSupport(org, self)
        from runtime.daemon.startup_recovery import capture_coding_settings
        capture_coding_settings(self)

    def _principal(self, principal, *, validate_admission: Callable[[], None] | None = None):
        validate = coding_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        self.support._principal(principal)

    def _access(self, principal, *, validate_admission: Callable[[], None] | None = None) -> AbstractAsyncContextManager:
        store = self

        class _Access:
            async def __aenter__(self) -> tuple:
                self.frame = frame = _CodingReadFrame(store.support)
                try:
                    validate = coding_admission_validation(store) if validate_admission is None else validate_admission
                    store._principal(principal, validate_admission=validate)
                    references = store.support._schema_references()
                    await frame.aenter(store.org.workflow_authority._async_writer_lock, 'authority async exit')
                    validate()
                    store._principal(principal, validate_admission=validate)
                    await frame.aenter(store.org.teams_lock, 'teams async exit')
                    validate()
                    store._principal(principal, validate_admission=validate)
                    await frame.aenter(store.org.db_lock, 'database async exit')
                    validate()
                    capture = store.support._discover(validate_admission=validate)
                    validate()
                    store._principal(principal, validate_admission=validate)
                    return references, capture
                except BaseException as error:
                    frame.record('settings access acquisition observation', error)
                await self.__aexit__(None, None, None)
                raise AssertionError('unreachable selected settings access failure')

            async def __aexit__(self, kind: Any, error: BaseException | None, traceback: Any) -> bool:
                frame = self.frame
                if error is not None:
                    frame.record('settings access body observation', error)
                while frame.pending:
                    await frame.aclose_one()
                frame.finish()
                return frame.deliver(error, traceback)

        return _Access()

    def _writer(self, *, validate_admission: Callable[[], None] | None = None) -> AbstractContextManager[Any]:
        store = self

        class _Writer:
            def __init__(self) -> None:
                self.native = None
                self.writer = None
                self.acquired = False

            def __enter__(self) -> Any:
                locks = _CodingReadFrame(store.support)
                try:
                    validate = coding_admission_validation(store) if validate_admission is None else validate_admission
                    from runtime.orchestrator.producer_move import owner_for
                    admission = owner_for(store.db)
                    locks.enter(admission.move.gate if admission is not None else nullcontext(), 'reservation gate exit')
                    locks.enter(store.db._lock, 'reservation database exit')
                    validate()
                    self.native = native_writer(store.db, publisher='org_settings', validate_admission=validate)
                    self.writer = type(self.native).__enter__(self.native)
                    self.acquired = True
                except BaseException as error:
                    locks.record('reservation admission observation', error)
                while locks.pending:
                    locks.close_one()
                locks.finish()
                if locks.primary is not None:
                    if self.acquired:
                        self.acquired = False
                        self.writer.uncertain = True
                        try:
                            type(self.native).__exit__(self.native, type(locks.primary), locks.primary, locks.traceback)
                        except BaseException as later:
                            # A different native first cause remains in its own
                            # disposition/frame, nested in this original frame.
                            locks.record('reservation exit observation', later, later=True)
                    locks.deliver()
                return self.writer

            def __exit__(self, kind: Any, error: BaseException | None, traceback: Any) -> bool:
                if self.acquired:
                    self.acquired = False
                    # The original native owner captures this SAME cause before
                    # any formatting/attachment/settlement. No generator/stack
                    # adapter or post-settlement certification is interposed.
                    return type(self.native).__exit__(self.native, kind, error, traceback)
                return False

        return _Writer()

    def _state(self, conn, references, budget):
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        # Existing complete-schema API already accepts a caller allowance.
        # Compose directly rather than resetting CodingImportSupport._storage.
        _, inventory = _read_workflow_schema(conn, expected_org_slug=self.org.slug, budget=budget)
        if inventory is None:
            raise CodingSettingsError('coding_settings_invalid')
        return project(inventory.mappings, inventory.budget), inventory

    def _view(self, state, *, operation_key=None):
        operation = None
        if operation_key is not None:
            matches = [e for e in state.publications if e.operation_key == operation_key]
            if len(matches) != 1:
                raise CodingSettingsError('coding_settings_operation_not_found')
            operation = receipt(matches[0], replayed=True)
        return dict(format='workflow-coding-settings-view@1', org_slug=self.org.slug,
            current=state.current, configuration=state.configuration, operation=operation)

    async def get(self, *, principal, operation_key=None):
        validate = coding_admission_validation(self)
        budget = _Budget()
        try:
            async with self._access(principal, validate_admission=validate) as (references, capture):
                with self.support._read(capture, validate_admission=validate) as (conn, _):
                    self._principal(principal, validate_admission=validate)
                    state, _ = self._state(conn, references, budget)
                    result = self._view(state, operation_key=operation_key)
                    self._principal(principal, validate_admission=validate)
                    return result
        except StartupUnavailable:
            raise
        except CodingSettingsError:
            raise
        except sqlite3.Error:
            raise CodingSettingsError('coding_settings_publication_uncertain',
                                      operation_key=operation_key) from None
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            raise CodingSettingsError('coding_settings_invalid') from None

    async def options(self, *, principal: WorkflowTemplatePrincipal) -> dict:
        validate = coding_admission_validation(self)
        budget = _Budget()
        try:
            async with self._access(principal, validate_admission=validate) as (references, capture):
                with self.support._read(capture, validate_admission=validate) as (conn, admitted_capture):
                    self._principal(principal, validate_admission=validate)
                    _, inventory = self._state(conn, references, budget)
                    from runtime.workflows._coding_settings_options import project_options
                    result = project_options(self, conn, admitted_capture, inventory)
                    self._principal(principal, validate_admission=validate)
                    return result
        except StartupUnavailable:
            raise
        except CodingSettingsError:
            raise
        except (sqlite3.Error, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            raise CodingSettingsError('coding_settings_invalid') from None

    def _replay(self, state, command, raw):
        for entry in state.publications:
            if entry.operation_key != command['operation_key']:
                continue
            if entry.command_bytes != raw:
                raise CodingSettingsError('coding_settings_operation_conflict', current=state.current)
            return dict(format='workflow-coding-settings-save@1', org_slug=self.org.slug,
                operation=receipt(entry, replayed=True), current=state.current,
                configuration=state.configuration)
        return None

    async def publish(self, *, principal, raw: bytes):
        validate = coding_admission_validation(self)
        self._principal(principal, validate_admission=validate)
        try:
            command = parse_command(raw, self.org.slug)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            raise CodingSettingsError('coding_settings_invalid_request') from None
        key = command['operation_key']
        budget = _Budget()
        account(command, budget)
        writer = None
        access = None
        try:
            with self._writer(validate_admission=validate) as writer:
                business_entered = False
                try:
                    access = self._access(principal, validate_admission=validate)
                    async with access as (references, capture):
                        # Original replay does not need fresh authority or CAS.
                        with self.support._read(capture, validate_admission=validate) as (conn, _):
                            state, _ = self._state(conn, references, budget)
                            replay = self._replay(state, command, raw)
                            if replay is not None:
                                self._principal(principal, validate_admission=validate)
                                return replay
                        if capture is None:
                            raise CodingSettingsError('coding_settings_authority_unavailable', current=state.current)
                        from runtime.workflows._coding_settings_publication import append
                        result = None
                        validation_error = None
                        try:
                            business_entered = True
                            with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
                                try:
                                    self._principal(principal, validate_admission=validate)
                                    state, inventory = self._state(conn, references, budget)
                                    replay = self._replay(state, command, raw)
                                    if replay is not None:
                                        result = replay
                                    else:
                                        append(self, conn, principal, capture, command, raw, state, inventory)
                                        # The old inventory is discarded after mutation.
                                        state, inventory = self._state(conn, references, budget)
                                        account(state.current, inventory.budget)
                                        result = dict(format='workflow-coding-settings-save@1',
                                            org_slug=self.org.slug, current=state.current,
                                            configuration=state.configuration,
                                            operation=receipt(state.publications[-1], replayed=False))
                                    self._principal(principal, validate_admission=validate)
                                    self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
                                except (CodingSettingsError, ValueError, TypeError, KeyError) as exc:
                                    validation_error = exc
                                    raise
                        except BaseException as exc:
                            access.frame.record('admission business observation', exc)
                            evidence = BaseException.__dict__['__dict__'].__get__(exc)
                            # Original rollback and lease release must both finish.
                            # Selected cleanup preserves the primary with notes on
                            # failure; identity/transaction state alone is not proof.
                            if (exc is validation_error and not self.db._conn.in_transaction
                                    and evidence.get('_admission_rollback_complete') is True
                                    and evidence.get('_admission_release_complete') is True
                                    and not _settings_has_notes(exc)
                                    and evidence.get('_admission_rollback_secondary') is None
                                    and evidence.get('_admission_release_secondary') is None
                                    and evidence.get('_admission_fence_secondary') is None):
                                writer.no_effect_error = exc
                            else:
                                writer.uncertain = True
                            raise
                        return result  # COMMIT + lease release, then native settlement.
                except BaseException as exc:
                    if (not business_entered and issubclass(type(exc), (ValueError, TypeError, KeyError))
                            and access is not None and access.frame.clean(exc)
                            and not self.db._conn.in_transaction):
                        writer.no_effect_error = exc
                    elif not business_entered:
                        writer.uncertain = True
                    elif access is None or not access.frame.clean(exc):
                        writer.no_effect_error = None
                        writer.uncertain = True
                    raise
        except StartupUnavailable:
            raise
        except CodingSettingsError as exc:
            if (writer is not None and writer.uncertain
                    or (writer is None or writer.no_effect_error is not exc) and _settings_has_notes(exc)):
                raise CodingSettingsError('coding_settings_publication_uncertain', operation_key=key) from None
            raise
        except (WorkflowAuthorityError, ProfileCoordinatorError, sqlite3.Error):
            raise CodingSettingsError('coding_settings_publication_uncertain', operation_key=key) from None
        except (ValueError, TypeError, KeyError) as exc:
            code = ('coding_settings_invalid' if writer is not None and writer.no_effect_error is exc
                    and not writer.uncertain else 'coding_settings_publication_uncertain')
            raise CodingSettingsError(code, **(dict(operation_key=key)
                if code.endswith('uncertain') else {})) from None
        except Exception:
            raise CodingSettingsError('coding_settings_publication_uncertain', operation_key=key) from None
