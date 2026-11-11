"""SELECT-only Final callback validation (R-M3-3), consumed by U7c1.

Caller owns the original authority/teams/DB locks, capture and read transaction.
No action, acceptance, Held writer, transport or executable selection is returned.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityCoordinator
from runtime.workflows.sequential_business_hold import BusinessHoldCause, _proof, hold_body
from runtime.workflows.sequential_coding_evidence import _current_authority, _pinned
from runtime.workflows.sequential_coding_final import _native_pause
from runtime.workflows.sequential_coding_final_observation import CodingFinalObservation, read_final_callback_content
from runtime.workflows.sequential_coding_final_records import FinalResultPin
from runtime.workflows.sequential_coding_output import _settled_input, coding_binding_context
from runtime.workflows.sequential_coding_policy import (
    CodingEvidenceRefusal, CurrentCodingEvidence, derive_coding_admissibility,
)
from runtime.workflows.sequential_contracts import Limits, OutputRef
from runtime.workflows.sequential_dispatch import _carrier
from runtime.workflows.sequential_engine import SequentialRefusal
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decode, _decoded, _native_rows, _outputs, _sql_budget
from runtime.workflows.sequential_mixed_history import MixedTaskHistory, read_selected_mixed_history_uncommitted
from runtime.workflows.sequential_repository_subject import read_repository_subject
from runtime.workflows.sequential_run_validation import _prior_state, _read_selected, _selected_layout


class FinalObservationCause(StrEnum):
    MALFORMED = 'sequential_final_observation_malformed'
    STALE = 'sequential_final_observation_stale'
    FOREIGN = 'sequential_final_observation_foreign'
    REPLAYED = 'sequential_final_observation_replayed'
    CHANGED = 'sequential_final_observation_changed'
    DENIED = 'sequential_authority_denied'
    AUTHORITY_UNVERIFIED = 'sequential_authority_unverified'
    UNVERIFIED = 'sequential_final_observation_unverified'
    CANCELLED = 'sequential_final_observation_cancelled'
    HELD = 'sequential_final_observation_held'
    LIMIT = 'sequential_final_observation_read_exhausted'


class FinalObservationRefusal(SequentialRefusal):
    def __init__(self, cause: FinalObservationCause) -> None:
        self.cause = cause
        super().__init__(cause.value)


@dataclass(frozen=True)
class ValidatedFinalObservation:
    """Transient declaration plus original proof digests; no admission grant."""
    observation: CodingFinalObservation
    result_id: int
    carrier_digest: str
    terminal_digest: str
    outer_digest: str


def _require(condition: bool, cause: FinalObservationCause) -> None:
    if not condition:
        raise FinalObservationRefusal(cause)


def _unheld(conn: sqlite3.Connection, mixed: MixedTaskHistory, keys: tuple[str, ...],
            authority: WorkflowAuthorityCoordinator, budget: _ReadBudget) -> str:
    budget.reserve((512 + 128 * len(mixed.task_columns)) * len(mixed.tasks))
    tasks = {row[0]: dict(zip(mixed.task_columns, row, strict=True)) for row in mixed.tasks}
    budget.work(len(tasks))
    roots = {}
    # Check only the selected native paths; an unrelated stopped sibling is not
    # authority to cancel this visit. U6's complete oracle owns mixed history.
    for key in keys:
        seen_key = key
        seen = set()
        while key is not None:
            budget.step()
            _require(key in tasks and key not in seen, FinalObservationCause.UNVERIFIED)
            seen.add(key)
            task = tasks[key]
            _require(task['status'] != 'cancelled' and task['cancelled_at'] is None,
                     FinalObservationCause.CANCELLED)
            if task['parent_task_id'] is None:
                roots[seen_key] = key
            key = task['parent_task_id']
    # Original native Pause reader retains journal semantics and original
    # generation. Inspect the small held field first to classify a true hold.
    store = getattr(authority._db, '_task_pause_store', None)
    _require(store is not None, FinalObservationCause.AUTHORITY_UNVERIFIED)
    for key, root in roots.items():
        budget.step()
        state = conn.execute('SELECT typeof(held),held FROM task_pause_controls WHERE root_task_id=?', (root,)).fetchone()
        _require(state is not None and state[0] == 'integer' and state[1] in (0, 1),
                 FinalObservationCause.UNVERIFIED)
        _require(state[1] == 0, FinalObservationCause.HELD)
        actual, _ = _native_pause(conn, store, key, budget)
        _require(actual == root, FinalObservationCause.FOREIGN)
    return roots[keys[0]]


def _read(conn: sqlite3.Connection, *, org_slug: str, run_id: str, visit_id: str,
          task_id: str, principal: str, session_id: str, result_id: int,
          authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture,
          mixed: MixedTaskHistory, budget: _ReadBudget) -> ValidatedFinalObservation:
    metadata = _selected_layout(conn, budget)
    runs = _read_selected(conn, metadata, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    _require(len(runs) == 1 and runs[0]['org_slug'] == org_slug, FinalObservationCause.FOREIGN)
    run = runs[0]
    compiled, _, _ = _pinned(conn, metadata, run, budget)
    visits = _read_selected(conn, metadata, 'workflow_seq_visits', budget, 'run_id=?', (run_id,))
    events = _read_selected(conn, metadata, 'workflow_seq_events', budget, 'run_id=?', (run_id,))
    rows = _read_selected(conn, metadata, 'workflow_seq_outputs', budget, 'run_id=?', (run_id,))
    visits.sort(key=lambda row: row['ordinal'])
    events.sort(key=lambda row: row['sequence'])
    found = [visit for visit in visits if visit['id'] == visit_id]
    _require(len(found) == 1, FinalObservationCause.FOREIGN)
    visit = found[0]
    _require((visit['task_id'], visit['principal'], visit['session_id']) == (task_id, principal, session_id),
             FinalObservationCause.FOREIGN)
    _require(run['state'] != 'cancelled', FinalObservationCause.CANCELLED)
    _require(run['state'] != 'held', FinalObservationCause.HELD)
    root = _unheld(conn, mixed, (task_id, run['source_task_id'], run['carrier_task_id']), authority, budget)
    _require(visit['result_id'] is None and visit['state'] not in ('completed', 'returned'),
             FinalObservationCause.REPLAYED)
    _require(run['state'] == 'active' and run['active_visit_id'] == visit_id
             and (visit['state'], visit['selected']) == ('running', 1), FinalObservationCause.STALE)
    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
    _require(node.output_contract.type == 'guarded-repository-merge@1', FinalObservationCause.FOREIGN)
    budget.reserve(512 + 128 * len(mixed.results))
    budget.work(2 * len(mixed.results))
    _require(mixed.result(result_id, task_id=task_id, principal=principal, session_id=session_id)[4] == 'completed',
             FinalObservationCause.UNVERIFIED)
    _require([row[0] for row in mixed.results if row[1] == task_id] == [result_id], FinalObservationCause.REPLAYED)
    native = _native_rows(conn, 'tasks', ('status', 'cancelled_at', 'zombie_flagged_at',
        'active_chain', 'active_fanout', 'blocked_on_job_ids'),
        (('text',), ('null',), ('null',), ('null',), ('null',), ('null',)), task_id, budget)
    _require(native == [('completed', None, None, None, None, None)], FinalObservationCause.UNVERIFIED)
    event = next(item for item in events if item['id'] == visit['predecessor_event_id']) if visit['ordinal'] > 1 else events[0]
    proof = hold_body(conn, run=run, visit=visit, compiled=compiled, result_id=result_id,
        cause=BusinessHoldCause.CAPABILITY, event=event, budget=budget)
    callbacks = _native_rows(conn, 'task_results', ('output_summary', 'verdict', 'decision_json', 'waiting_on_job_ids'),
        (('text',), ('text', 'null'), ('null',), ('null',)), result_id, budget)
    _require(len(callbacks) == 1, FinalObservationCause.UNVERIFIED)
    summary, verdict, _, _ = callbacks[0]
    budget.reserve(128 * len(summary.encode('utf8')) + 4096)
    try:
        carrier, content = _carrier(summary, contract=node.output_contract.type,
            checks=node.completion_checks, status='completed', verdict=verdict)
        _require(carrier['outcome'] == 'merged' and not carrier['capability_claims'], FinalObservationCause.MALFORMED)
        observation = read_final_callback_content(content)
    except FinalObservationRefusal:
        raise
    except (SequentialRefusal, ValueError, TypeError, UnicodeError, RecursionError):
        raise FinalObservationRefusal(FinalObservationCause.MALFORMED) from None
    _decoded(observation.model_dump(mode='json'), budget)
    selected = observation.intent.selected
    _require((selected.org_slug, selected.run_id, selected.visit_id, selected.task_id, selected.principal, selected.session_id)
             == (org_slug, run_id, visit_id, task_id, principal, session_id), FinalObservationCause.FOREIGN)
    _require((selected.definition_digest, selected.start_digest, selected.cas, selected.last_event_digest,
              selected.input_selection_digest, selected.change.epoch) ==
             (compiled.pin.definition_digest, run['start_digest'], run['cas'], events[-1]['event_digest'],
              visit['input_selection_digest'], run['epoch']), FinalObservationCause.STALE)
    subject = read_repository_subject(conn, run, compiled, budget)
    _require(subject is not None, FinalObservationCause.AUTHORITY_UNVERIFIED)
    _require(selected.change.subject == subject.subject, FinalObservationCause.STALE)
    start, bound = coding_binding_context(run, visit, compiled, capture, budget)
    _require(bound[node.role]['role'] == 'manager'
             and dict(compiled.definition.roles)[node.role].eligibility == 'registered-team-manager',
             FinalObservationCause.DENIED)
    origin = bound[node.role]['repos'].get(subject.subject.repo_key)
    _require(type(origin) is str and 0 < len(origin.encode('utf8')) <= 4096
             and hashlib.sha256(origin.encode('utf8')).hexdigest() == subject.subject.origin_digest,
             FinalObservationCause.DENIED)
    _require((selected.authority_generation, selected.authority_digest) ==
             (capture.ready.generation, capture.ready.snapshot_digest), FinalObservationCause.CHANGED)
    terminal, _ = _proof(conn, run_id, visit_id, 'terminal', budget)
    pin = selected.native
    _require((pin.root_task_id, pin.org_root, pin.workspace_root, pin.invocation_id, pin.request_id, pin.backend) ==
             (root, str(authority._root), str(authority._root / 'workspaces' / principal),
              terminal['invocation_id'], terminal['request']['logical_id'], terminal['receipt']['backend'])
             and hashlib.sha256(_bytes(dict(token=pin.handle_id, request_id=pin.request_id, backend=pin.backend))).hexdigest()
                 == terminal['handle_digest'], FinalObservationCause.CHANGED)
    # Reconstruct selected results from the complete reducer prefix, never
    # from Final's declared inputs or the bundle's claims about upstream work.
    outputs = _outputs({'workflow_seq_outputs': rows}, {item['id']: item for item in visits},
        {run_id: run}, {run['definition_id']: compiled}, budget)
    refs = {key: tuple(OutputRef.model_validate(ref) for ref in values) for key, values in outputs.items()}
    original = Limits.model_validate(_decode(run['original_limits_bytes'], budget))
    _, current = _prior_state(run, compiled, start, original, visits, events, refs, budget)
    nodes = {item.id: item for item in compiled.definition.nodes}
    contributors = tuple(sorted({item['principal'] for item in visits
        if nodes[item['node_id']].output_contract.type == 'code-change@1'}))
    results = []
    for ref in current:
        budget.step()
        if ref.contract in ('code-change@1', 'independent-code-review@1', 'code-qa@1'):
            upstream = next(item for item in visits if item['id'] == ref.producer_visit)
            _require(bound[nodes[upstream['node_id']].role]['name'] == upstream['principal']
                     and bound[nodes[upstream['node_id']].role]['role'] == 'worker', FinalObservationCause.DENIED)
            results.append(_settled_input(conn, run=run, compiled=compiled, ref=ref, budget=budget))
    admissible = derive_coding_admissibility(CurrentCodingEvidence(run_id, visit_id, task_id, principal, session_id,
        selected.change, contributors, tuple(results), compiled.pin.definition_digest, run['start_digest'],
        run['cas'], events[-1]['event_digest'], visit['input_selection_digest']))
    expected = tuple(FinalResultPin(ref=item.ref, principal=item.principal, carrier_digest=item.carrier_digest,
        terminal_digest=item.terminal_digest, outer_digest=item.outer_digest)
        for item in (admissible.development, admissible.review, admissible.qa))
    _require(selected.results == expected and selected.contributors == contributors, FinalObservationCause.CHANGED)
    _current_authority(conn, authority, capture, budget)
    budget.check()
    return ValidatedFinalObservation(observation, result_id, proof['callback']['carrier_digest'],
                                    proof['terminal_digest'], proof['outer_digest'])


def read_final_callback_uncommitted(conn: sqlite3.Connection, *, org_slug: str, run_id: str, visit_id: str,
        task_id: str, principal: str, session_id: str, result_id: int,
        authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture,
        deadline: float | None = None) -> ValidatedFinalObservation:
    """Complete mixed oracle then selected proof read, with zero writes.

    R-M3-2: the mandatory complete read owns unchanged D2 A/B/C/L allowances;
    selected projection/validation owns one finite budget on the SAME original
    deadline. No retries, resetting, cached evidence or caller-supplied callback.
    U7c1 validates before retaining business-Held; U7c2 still owns admission.
    """
    budget = _ReadBudget() if deadline is None else _ReadBudget(deadline=deadline)
    try:
        _require(type(conn) is sqlite3.Connection and conn.in_transaction
                 and authority._db._conn is conn and authority._org_slug == org_slug and capture is not None,
                 FinalObservationCause.AUTHORITY_UNVERIFIED)
        _require(type(result_id) is int and result_id > 0, FinalObservationCause.FOREIGN)
        with _sql_budget(conn, budget):
            _current_authority(conn, authority, capture, budget)
        mixed = read_selected_mixed_history_uncommitted(conn, org_slug=org_slug, task_id=task_id,
                                                        deadline=budget.deadline)
        with _sql_budget(conn, budget):
            return _read(conn, org_slug=org_slug, run_id=run_id, visit_id=visit_id, task_id=task_id,
                principal=principal, session_id=session_id, result_id=result_id, authority=authority,
                capture=capture, mixed=mixed, budget=budget)
    except FinalObservationRefusal:
        raise
    except (CodingEvidenceRefusal, SequentialRefusal, ValueError) as exc:
        # The mounted reader's metadata allocator can report a generic history
        # error after exhausting this same byte allowance. Preserve its refusal.
        if deadline is not None and budget.remaining_bytes < 0:
            raise FinalObservationRefusal(FinalObservationCause.LIMIT) from None
        code = exc.code if isinstance(exc, SequentialRefusal) else str(exc)
        cause = (FinalObservationCause.LIMIT if code.startswith(('workflow_seq_read_', 'coding_read_'))
                 or 'exhausted' in code or 'limit_exceeded' in code else
                 FinalObservationCause.DENIED if code == 'sequential_authority_denied' else
                 FinalObservationCause.AUTHORITY_UNVERIFIED if code == 'sequential_authority_unverified' else
                 FinalObservationCause.STALE if code == 'sequential_coding_evidence_stale' else
                 FinalObservationCause.CHANGED if code.startswith('workflow_seq_business_hold_') else
                 FinalObservationCause.UNVERIFIED)
        raise FinalObservationRefusal(cause) from None
    except (sqlite3.Error, PauseControlError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError, RecursionError):
        raise FinalObservationRefusal(FinalObservationCause.UNVERIFIED) from None
