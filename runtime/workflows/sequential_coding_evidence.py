"""Unmounted, SELECT-only current Coding evidence reader (U5a).

Caller owns the original authority admission transaction/locks and coherent raw
SQLite connection. All native INTEGER callbacks and successful terminal/positive
outer proofs are re-read here; no caller-supplied receipt or verdict substitutes.
U5b must separately prove the selected genuine live Final invocation and consume
this policy again at each disclosure/publication/action. No executable selection,
intent, action, Git/provider I/O, transaction control or capability writer exists.
"""
from __future__ import annotations

import sqlite3

from runtime.infrastructure.workflow_sequential_ownership import read_sequential_owner
from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityCoordinator, WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_business_hold import BusinessHoldCause, hold_body, read_hold_rows
from runtime.workflows.sequential_coding_output import CodeChangeDeclaration, coding_binding_context
from runtime.workflows.sequential_coding_policy import (
    CodingAdmissibility, CodingEvidenceRefusal, CodingRefusalCause, CurrentCodingEvidence,
    SettledCodingResult, derive_coding_admissibility, read_code_assessment,
)
from runtime.workflows.sequential_contracts import Limits, OutputRef
from runtime.workflows.sequential_definition import CompiledDefinition, _json, parse_result
from runtime.workflows.sequential_engine import SequentialRefusal, check_output_size
from runtime.workflows.sequential_history import (
    _ReadBudget, _WIRE_TOKENS, _decode, _decoded, _definitions, _native_rows, _outputs, _parse, _run_history, _sql_budget,
)
from runtime.workflows.sequential_repository_subject import read_repository_subject
from runtime.workflows.sequential_run_validation import _prior_state, _read_selected, _selected_layout, _selected_settings, _types


def _require(condition: bool, cause: CodingRefusalCause = CodingRefusalCause.UNVERIFIED) -> None:
    if not condition:
        raise CodingEvidenceRefusal(cause)


def _current_authority(conn: sqlite3.Connection, authority: WorkflowAuthorityCoordinator,
                       capture: AuthorityAdmissionCapture, budget: _ReadBudget) -> None:
    # Prepay this validator's snapshot decode, mirror reconstruction and digest
    # map work on the SAME allowance, including removed mirror/profile rows.
    budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
    for _ in _WIRE_TOKENS.finditer(capture.ready.snapshot_bytes):
        budget.step()
    for row in capture.profile_mirror:
        budget.candidate()
        budget.reserve(512 + 128 * len(row))
        for value in row:
            budget.reserve(4 * len(value) + 512 if type(value) is str else 512)
    budget.work(len(capture.profile_names) * (len(capture.profile_digests) + 1))
    try:
        authority._validate_admission_uncommitted(conn, capture)
    except (WorkflowAuthorityError, ProfileCoordinatorError, OSError, ValueError):
        raise CodingEvidenceRefusal(CodingRefusalCause.AUTHORITY_UNVERIFIED) from None
    budget.check()


def _pinned(conn: sqlite3.Connection, metadata: dict, run: dict, budget: _ReadBudget) -> tuple:
    # Read admitted pins, never today's defaults/latest generation. Later
    # settings publication cannot replace or strand this immutable run.
    data = {}
    for table, where, args in (
        ('workflow_seq_definitions', 'id=?', (run['definition_id'],)),
        ('workflow_seq_settings', 'id=?', (run['settings_id'],)),
        ('workflow_seq_settings_operations', 'settings_id=?', (run['settings_id'],)),
    ):
        data[table] = _read_selected(conn, metadata, table, budget, where, args)
        _require(len(data[table]) == 1)
    _types(data, budget)
    definitions = _definitions(data, budget)
    settings, snapshots = _selected_settings(data, definitions, run['org_slug'], budget)
    setting = settings[run['settings_id']]
    _require(setting['definition_id'] == run['definition_id'])
    return definitions[run['definition_id']], setting, snapshots[run['settings_id']]


def _settled(conn: sqlite3.Connection, *, run: dict, visit: dict, output: dict,
             compiled: CompiledDefinition, events: list[dict], budget: _ReadBudget) -> SettledCodingResult:
    # Reuse the U4b1 SELECT-only original proof authenticator. The reconstructed
    # hold body is discarded; no hold/observation is persisted by this reader.
    event = next(item for item in events if item['id'] == visit['predecessor_event_id']) if visit['ordinal'] > 1 else events[0]
    proof = hold_body(conn, run=run, visit=visit, compiled=compiled, result_id=visit['result_id'],
        cause=BusinessHoldCause.CAPABILITY, event=event, budget=budget)
    _require(visit['state'] == 'completed' and visit['selected'] == 0)
    native = _native_rows(conn, 'tasks', ('status', 'cancelled_at', 'zombie_flagged_at'),
        (('text',), ('null',), ('null',)), visit['task_id'], budget)
    _require(native == [('completed', None, None)])
    rows = _native_rows(conn, 'task_results', ('output_summary', 'verdict', 'decision_json', 'waiting_on_job_ids'),
        (('text',), ('text', 'null'), ('null',), ('null',)), visit['result_id'], budget)
    _require(len(rows) == 1)
    summary, verdict, _, _ = rows[0]
    node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
    # The original, closed callback parser remains the carrier owner. Import
    # locally so a future dispatcher consumer does not introduce an import cycle.
    from runtime.workflows.sequential_dispatch import _carrier
    budget.reserve(32 * len(summary.encode('utf8')) + 4096)
    carrier, content = _carrier(summary, contract=node.output_contract.type,
        checks=node.completion_checks, status='completed', verdict=verdict)
    envelope = _parse(visit['result_bytes'], parse_result, budget)
    _require(not carrier['capability_claims'] and carrier['outcome'] == envelope.outcome
             and content == output['content_bytes'] and len(envelope.outputs) == 1)
    if output['contract'] == 'code-change@1':
        budget.reserve(96 * len(content) + 4096)
        _json(content, 49152)
        change = CodeChangeDeclaration.model_validate_json(content, strict=True)
    else:
        budget.reserve(96 * len(content) + 4096)
        change = read_code_assessment(content).change
    _decoded(change.model_dump(mode='json'), budget)
    return SettledCodingResult(run['id'], envelope.outputs[0], visit['principal'], change,
        envelope.outcome, verdict, proof['callback']['carrier_digest'],
        proof['terminal_digest'], proof['outer_digest'])


def _read(conn: sqlite3.Connection, *, org_slug: str, run_id: str, task_id: str,
          principal: str, session_id: str, change: bytes, capture: AuthorityAdmissionCapture,
          budget: _ReadBudget) -> CodingAdmissibility:
    metadata = _selected_layout(conn, budget)
    runs = _read_selected(conn, metadata, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    _require(len(runs) == 1 and runs[0]['org_slug'] == org_slug)
    run = runs[0]
    compiled, settings, snapshot = _pinned(conn, metadata, run, budget)
    data = {table: _read_selected(conn, metadata, table, budget, 'run_id=?', (run_id,))
            for table in ('workflow_seq_visits', 'workflow_seq_events', 'workflow_seq_outputs')}
    _types(data, budget)
    visits, events, rows = (data[table] for table in
        ('workflow_seq_visits', 'workflow_seq_events', 'workflow_seq_outputs'))
    visits.sort(key=lambda row: row['ordinal'])
    events.sort(key=lambda row: row['sequence'])
    for row in rows:
        check_output_size(row['contract'], row['content_bytes'])
    outputs = _outputs({'workflow_seq_outputs': rows}, {visit['id']: visit for visit in visits},
                       {run_id: run}, {run['definition_id']: compiled}, budget)
    # Full selected-prefix replay, not the incremental validator's trust in old
    # writes: authenticate every result/envelope/edge/return/invalidation/CAS.
    _run_history(conn, run, compiled, settings, snapshot, visits, events, outputs, budget,
                 read_hold_rows(conn, run_id, budget))
    selected = [visit for visit in visits if visit['id'] == run['active_visit_id']]
    _require(len(selected) == 1, CodingRefusalCause.FINAL_UNAVAILABLE)
    final = selected[0]
    node = next(item for item in compiled.definition.nodes if item.id == final['node_id'])
    _require(run['state'] == 'active' and final['state'] == 'running' and final['selected'] == 1
             and final['result_id'] is None and node.output_contract.type == 'guarded-repository-merge@1'
             and (final['task_id'], final['principal'], final['session_id']) == (task_id, principal, session_id),
             CodingRefusalCause.FINAL_UNAVAILABLE)
    _require(read_sequential_owner(conn, task_id=task_id, org_slug=org_slug) == ('seq_visit', final['id'], 'running'))
    native = _native_rows(conn, 'tasks', ('assigned_agent', 'current_session_id', 'status', 'cancelled_at',
        'zombie_flagged_at', 'active_chain', 'active_fanout', 'blocked_on_job_ids'),
        (('text',), ('text',), ('text',), ('null',), ('null',), ('null',), ('null',), ('null',)), task_id, budget)
    _require(native == [(principal, session_id, 'in_progress', None, None, None, None, None)],
             CodingRefusalCause.FINAL_UNAVAILABLE)
    start, bound = coding_binding_context(run, final, compiled, capture, budget)
    _require(bound[node.role]['role'] == 'manager'
             and dict(compiled.definition.roles)[node.role].eligibility == 'registered-team-manager',
             CodingRefusalCause.AUTHORITY_DENIED)
    budget.reserve(96 * len(change) + 4096)
    _json(change, 49152)
    declaration = CodeChangeDeclaration.model_validate_json(change, strict=True)
    subject = read_repository_subject(conn, run, compiled, budget)
    _require(subject is not None, CodingRefusalCause.AUTHORITY_UNVERIFIED)
    _require(declaration.subject == subject.subject and declaration.epoch == run['epoch'], CodingRefusalCause.STALE)
    # Existing or uncertain owned effects never supply a second selection.
    budget.step()
    _require(conn.execute("SELECT 1 FROM workflow_seq_evidence WHERE run_id=? AND kind IN ('intent','action') LIMIT 1",
                          (run_id,)).fetchone() is None, CodingRefusalCause.EFFECT_UNCERTAIN)
    original = Limits.model_validate(_decode(run['original_limits_bytes'], budget))
    refs = {key: tuple(OutputRef.model_validate(ref) for ref in values) for key, values in outputs.items()}
    _, current = _prior_state(run, compiled, start, original, visits, events, refs, budget)
    by_visit = {visit['id']: visit for visit in visits}
    nodes = {item.id: item for item in compiled.definition.nodes}
    contributors = tuple(sorted({visit['principal'] for visit in visits
        if nodes[visit['node_id']].output_contract.type == 'code-change@1'}))
    results = []
    for ref in current:
        budget.step()
        if ref.contract not in ('code-change@1', 'independent-code-review@1', 'code-qa@1'):
            continue
        output = [row for row in rows if row['visit_id'] == ref.producer_visit and row['slot'] == ref.slot]
        _require(len(output) == 1)
        visit = by_visit[ref.producer_visit]
        _require(bound[nodes[visit['node_id']].role]['name'] == visit['principal'], CodingRefusalCause.AUTHORITY_DENIED)
        _require(bound[nodes[visit['node_id']].role]['role'] == 'worker', CodingRefusalCause.AUTHORITY_DENIED)
        result = _settled(conn, run=run, visit=visit, output=output[0], compiled=compiled, events=events, budget=budget)
        _require(result.ref == ref)
        results.append(result)
    budget.check()
    return derive_coding_admissibility(CurrentCodingEvidence(run_id=run_id, final_visit=final['id'],
        final_task=task_id, final_principal=principal, final_session=session_id, change=declaration,
        contributors=contributors, results=tuple(results), definition_digest=compiled.pin.definition_digest,
        start_digest=run['start_digest'], cas=run['cas'], last_event_digest=events[-1]['event_digest'],
        input_selection_digest=final['input_selection_digest']))


def read_current_coding_evidence_uncommitted(conn: sqlite3.Connection, *, org_slug: str, run_id: str,
        task_id: str, principal: str, session_id: str, change: bytes,
        authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture) -> CodingAdmissibility:
    """Recheck current authority and derive evidence inside its owned transaction.

    One original five-second/64-MiB/65536-step/SQL budget covers the selected
    schema, full run replay, native proofs, parsing and final validation. Budget
    limits are cooperative, not hard memory/time guarantees. This call writes
    nothing and never proves actual host liveness or repository access. Refusal
    returns no admissibility value; downstream U5b must record BLOCK -> Held.
    """
    try:
        _require(type(conn) is sqlite3.Connection and conn.in_transaction)
        _require(authority._db._conn is conn and authority._org_slug == org_slug,
                 CodingRefusalCause.AUTHORITY_UNVERIFIED)
        _require(capture is not None, CodingRefusalCause.AUTHORITY_UNVERIFIED)
        budget = _ReadBudget()
        with _sql_budget(conn, budget):
            _current_authority(conn, authority, capture, budget)
            result = _read(conn, org_slug=org_slug, run_id=run_id, task_id=task_id,
                principal=principal, session_id=session_id, change=change, capture=capture, budget=budget)
            _current_authority(conn, authority, capture, budget)
            budget.check()
        return result
    except CodingEvidenceRefusal:
        raise
    except (WorkflowAuthorityError, ProfileCoordinatorError):
        raise CodingEvidenceRefusal(CodingRefusalCause.AUTHORITY_UNVERIFIED) from None
    except SequentialRefusal as exc:
        cause = (CodingRefusalCause.AUTHORITY_DENIED if exc.code == 'sequential_authority_denied'
                 else CodingRefusalCause.UNVERIFIED)
        raise CodingEvidenceRefusal(cause) from None
    except (sqlite3.Error, ValueError, TypeError, KeyError, StopIteration, UnicodeError, RecursionError):
        raise CodingEvidenceRefusal(CodingRefusalCause.UNVERIFIED) from None
