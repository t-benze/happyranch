"""Unmounted U5b2a live Final selection; no route, effect or capability producer.

The original native owner and current authority are bookended in one owned
transaction. U5a derives mandatory review/QA from full history at that SAME
boundary. Returned metadata is transient, not an effect lease or access proof.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_evidence import _current_authority, _read
from runtime.workflows.sequential_coding_final_records import (
    CodingFinalSelection, FinalNativePin, FinalResultPin, read_final_selection,
)
from runtime.workflows.sequential_coding_output import coding_binding_context
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal, CodingRefusalCause
from runtime.workflows.sequential_dispatch import _cancelled, _discover_result
from runtime.workflows.sequential_engine import SequentialRefusal
from runtime.workflows.sequential_history import _ReadBudget, _WIRE_TOKENS, _bytes, _decoded, _native_rows, _sql_budget


def _require(condition: bool, cause: CodingRefusalCause = CodingRefusalCause.FINAL_UNAVAILABLE) -> None:
    if not condition:
        raise CodingEvidenceRefusal(cause)


def _native_pause(conn: sqlite3.Connection, store: Any, task_id: str,
                  budget: _ReadBudget) -> tuple[str, dict]:
    # Preflight only the ancestor pointer projection, not full native task
    # rows/briefs. Native Pause retains journal interpretation and validation.
    root_id, seen = task_id, set()
    for _ in range(1024):
        _require(root_id not in seen)
        seen.add(root_id)
        rows = _native_rows(conn, 'tasks', ('parent_task_id',), (('text', 'null'),), root_id, budget)
        _require(len(rows) == 1)
        if rows[0][0] is None:
            break
        root_id = rows[0][0]
    else:
        raise CodingEvidenceRefusal(CodingRefusalCause.FINAL_UNAVAILABLE)
    sizes = conn.execute('SELECT typeof(admission_journal_json),length(CAST(admission_journal_json AS BLOB)),'
        'length(CAST(actor AS BLOB))+length(CAST(updated_at AS BLOB))+'
        'coalesce(length(CAST(paused_at AS BLOB)),0)+coalesce(length(CAST(resumed_at AS BLOB)),0) '
        'FROM task_pause_controls WHERE root_task_id=?', (root_id,)).fetchone()
    _require(sizes is not None and sizes[0] == 'text' and type(sizes[1]) is int
             and 0 < sizes[1] <= 1048576 and type(sizes[2]) is int and 0 <= sizes[2] <= 65536)
    budget.reserve(48 * sizes[1] + 4 * sizes[2] + 4096)
    pause = store.row_uncommitted(root_id)
    _require(pause is not None and type(pause['held']) is int and pause['held'] == 0)
    _decoded(pause, budget)
    return root_id, pause


def _live_owner(org: Any, dispatcher: Any, conn: sqlite3.Connection, task_id: str,
                principal: str, session_id: str, budget: _ReadBudget) -> tuple[Any, FinalNativePin]:
    """Use native ownership/tracker/host facts; never scan OS or probe access."""
    _require(not org._runner_admission_closed and org.orchestrator._db is org.db)
    _require(org.sessions.get_context_by_session(session_id) == (org.slug, task_id, principal)
             and org.sessions.get_active(task_id, principal) == session_id
             and not org.sessions.is_recovery_session(task_id, principal, session_id))
    store = getattr(org.db, '_task_pause_store', None)
    _require(store is not None)
    owners = []
    for owner in store._live.values():
        budget.step()
        if (owner.task.id, owner.agent, owner.session_id) == (task_id, principal, session_id):
            owners.append(owner)
    _require(len(owners) == 1)
    owner = owners[0]
    _require(owner.owner == 'sequential' and owner.workflow_dispatcher is dispatcher
             and owner.orch is org.orchestrator and owner.store is store
             and owner.admitted is True and owner.attempt_committed is True
             and owner.unsettled is True and owner.launch_observed is True
             and getattr(owner, 'native_terminal_observation', None) is None)
    root_id, pause = _native_pause(conn, store, task_id, budget)
    native = owner._entry(pause)
    _require(native['owner'] == 'sequential' and native['org'] == org.slug
             and native['root_task_id'] == root_id
             and (native['task_id'], native['agent'], native['session_id']) == (task_id, principal, session_id)
             and native['phase'] == 'running'
             and not native['context'].get('execution_unknown')
             and not native['context'].get('producer_settled'))
    request, running = getattr(owner, 'coding_request', None), getattr(owner, 'coding_running', None)
    _require(request is not None and running is not None
             and request.org == org.slug and request.agent_id == principal
             and type(request.retry_attempt) is int and request.retry_attempt == 0
             and request.logical_id == owner.draft_request_key == running.request_id
             and running.backend in ('linux-systemd-cgroup-v2', 'macos-process-group')
             and bool(running.token) and running.process is not None and running.process.returncode is None)
    paths = owner.orch._paths
    _require(paths.root == org.root and paths.workspaces_dir == org.root / 'workspaces')
    pin = FinalNativePin(root_task_id=root_id, invocation_id=owner.identity,
        request_id=request.logical_id, handle_id=running.token, backend=running.backend,
        # Pause/Resume advances control generation; the running invocation
        # retains its original admission generation in the validated journal.
        pause_generation=native['generation'], org_root=str(paths.root),
        workspace_root=str(paths.workspaces_dir / principal))
    _decoded(pin.model_dump(mode='json'), budget)
    return owner, pin


def select_final_uncommitted(conn: sqlite3.Connection, *, org: Any, dispatcher: Any,
        task_id: str, principal: str, session_id: str, change: bytes,
        capture: AuthorityAdmissionCapture, budget: _ReadBudget) -> CodingFinalSelection:
    """Internal caller owns authority/teams/DB locks AND original binding lease.

    SELECT-only. No selection/intent/action is persisted or exposed on refusal.
    All readers and both native bookends share the original finite allowance.
    """
    _require(type(conn) is sqlite3.Connection and conn.in_transaction
             and org.db._conn is conn and dispatcher is org.workflow_sequential_dispatch
             and dispatcher.org is org, CodingRefusalCause.AUTHORITY_UNVERIFIED)
    authority = org.workflow_authority
    _require(authority._db is org.db and authority._org_slug == org.slug and capture is not None,
             CodingRefusalCause.AUTHORITY_UNVERIFIED)
    # _entry installs/removes its SQL handler; resume the same budget afterward.
    run, visit, compiled = dispatcher._entry(conn, task_id, budget=budget)
    with _sql_budget(conn, budget):
        _current_authority(conn, authority, capture, budget)
        owner, native = _live_owner(org, dispatcher, conn, task_id, principal, session_id, budget)
        _require(not _cancelled(conn, run, visit) and _discover_result(conn, visit) is None)
        admissible = _read(conn, org_slug=org.slug, run_id=run['id'], task_id=task_id,
            principal=principal, session_id=session_id, change=change, capture=capture, budget=budget)
        evidence = admissible.evidence
        _, bound = coding_binding_context(run, visit, compiled, capture, budget)
        node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
        origin = bound[node.role]['repos'].get(evidence.change.subject.repo_key)
        _require(type(origin) is str and 0 < len(origin.encode('utf8')) <= 4096,
                 CodingRefusalCause.AUTHORITY_DENIED)
        _require(hashlib.sha256(origin.encode('utf8')).hexdigest() == evidence.change.subject.origin_digest,
                 CodingRefusalCause.AUTHORITY_DENIED)
        result = CodingFinalSelection(format='workflow-sequential-coding-final-selection@1',
            org_slug=org.slug, run_id=evidence.run_id, visit_id=evidence.final_visit,
            task_id=task_id, principal=principal, session_id=session_id,
            definition_digest=evidence.definition_digest, start_digest=evidence.start_digest,
            cas=evidence.cas, last_event_digest=evidence.last_event_digest,
            input_selection_digest=evidence.input_selection_digest,
            authority_generation=capture.ready.generation, authority_digest=capture.ready.snapshot_digest,
            change=evidence.change, contributors=evidence.contributors,
            results=tuple(FinalResultPin(ref=item.ref, principal=item.principal,
                carrier_digest=item.carrier_digest, terminal_digest=item.terminal_digest,
                outer_digest=item.outer_digest) for item in (admissible.development, admissible.review, admissible.qa)),
            native=native)
        # Bound serialization/model/readback work and decoded traversal BEFORE
        # exposure, retaining the original deadline/work/byte budget.
        budget.reserve(262144)
        raw = _bytes(result.model_dump(mode='json'))
        budget.reserve(96 * len(raw) + 4096)
        read_final_selection(raw)
        _decoded(result.model_dump(mode='json'), budget)
        _current_authority(conn, authority, capture, budget)
        closing_owner, closing_native = _live_owner(org, dispatcher, conn, task_id, principal, session_id, budget)
        _require(closing_owner is owner and closing_native == native
                 and not _cancelled(conn, run, visit) and _discover_result(conn, visit) is None)
        budget.check()
        return result


class SequentialCodingFinalSelection:
    """Internal worker-thread service. Deliberately not mounted by OrgState/U7."""

    def __init__(self, org: Any) -> None:
        self.org = org

    def select(self, *, task_id: str, principal: str, session_id: str, change: bytes) -> CodingFinalSelection:
        from runtime.orchestrator.task_pause import PauseControlError
        org = self.org
        budget = _ReadBudget()
        try:
            try:
                capture = org.workflow_authority.capture_admission()  # Outside all ownership/locks.
            except (WorkflowAuthorityError, ProfileCoordinatorError, OSError, ValueError):
                raise CodingEvidenceRefusal(CodingRefusalCause.AUTHORITY_UNVERIFIED) from None
            # Prepay admission_writer's own validator in addition to the two
            # explicitly charged U5a bookends. No new allowance at lock entry.
            budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
            for _ in _WIRE_TOKENS.finditer(capture.ready.snapshot_bytes):
                budget.step()
            for row in capture.profile_mirror:
                budget.candidate()
                budget.reserve(512 + 128 * len(row))
                for value in row:
                    budget.reserve(4 * len(value) + 512 if type(value) is str else 512)
            budget.work(len(capture.profile_names) * (len(capture.profile_digests) + 1))
            budget.check()

            async def select() -> CodingFinalSelection:
                async with org.workflow_authority._async_writer_lock:
                    async with org.teams_lock:
                        async with org.db_lock:
                            with org.sessions._get_binding_lease(task_id, principal):
                                with org.workflow_authority.admission_writer(capture) as conn:
                                    return select_final_uncommitted(conn, org=org,
                                        dispatcher=org.workflow_sequential_dispatch, task_id=task_id,
                                        principal=principal, session_id=session_id, change=change,
                                        capture=capture, budget=budget)
            result = org.workflow_sequential_dispatch._on_loop(select())
            budget.check()  # Include admission COMMIT/lease release before exposure.
            return result
        except CodingEvidenceRefusal:
            raise
        except (WorkflowAuthorityError, ProfileCoordinatorError, OSError):
            raise CodingEvidenceRefusal(CodingRefusalCause.AUTHORITY_UNVERIFIED) from None
        except SequentialRefusal as exc:
            if exc.code.startswith('workflow_seq_read_'):
                raise  # Never convert shared-budget exhaustion into malformed output.
            cause = (CodingRefusalCause.AUTHORITY_DENIED if exc.code == 'sequential_authority_denied'
                     else CodingRefusalCause.FINAL_UNAVAILABLE)
            raise CodingEvidenceRefusal(cause) from None
        except ValueError as exc:
            if str(exc).startswith('workflow_seq_read_'):
                raise
            raise CodingEvidenceRefusal(CodingRefusalCause.UNVERIFIED) from None
        except (PauseControlError, sqlite3.Error, TypeError, KeyError, AttributeError,
                StopIteration, UnicodeError, RecursionError):
            raise CodingEvidenceRefusal(CodingRefusalCause.UNVERIFIED) from None
