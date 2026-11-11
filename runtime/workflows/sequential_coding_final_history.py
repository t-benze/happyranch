"""Persisted Final provenance and original-transaction publication validation.

Historical proof never supplies a new action or a current access grant. The
complete reducer owns the prefix/edge; this reader authenticates its Final bytes
and receipts against the original callback and settled upstream results.
"""
from __future__ import annotations

import hashlib
import sqlite3

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityCoordinator
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.sequential_business_hold import BusinessHoldCause, _proof, hold_body
from runtime.workflows.sequential_coding_final_observation import FINAL_OBSERVATION_LIMIT, read_final_callback_content
from runtime.workflows.sequential_coding_final_output import final_witness
from runtime.workflows.sequential_coding_final_records import FinalResultPin
from runtime.workflows.sequential_coding_output import _settled_input
from runtime.workflows.sequential_coding_policy import CurrentCodingEvidence, derive_coding_admissibility
from runtime.workflows.sequential_contracts import Limits, OutputRef, ResultEnvelope, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition
from runtime.workflows.sequential_engine import _engine_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decode, _native_rows, _require, _sql_budget


def validate_final_output(conn: sqlite3.Connection, *, run: dict, visit: dict,
        compiled: CompiledDefinition, start: StartCommand, visits: list[dict], events: list[dict],
        outputs: dict[str, list[dict]], original: Limits, envelope: ResultEnvelope,
        verified_checks: tuple[str, ...], budget: _ReadBudget) -> None:
    """Called by complete/selected prefix replay on its SAME original allowance."""
    from runtime.workflows.sequential_dispatch import _carrier
    from runtime.workflows.sequential_repository_subject import read_repository_subject
    from runtime.workflows.sequential_run_validation import _prior_state

    budget.work(len(events) + len(visits) + len(compiled.definition.nodes) + len(start.bindings))
    anchor = next(item for item in events if item['id'] == visit['predecessor_event_id']) if visit['ordinal'] > 1 else events[0]
    rows = _engine_rows(conn, 'workflow_seq_outputs', budget, 'visit_id=?', (visit['id'],))
    _require(len(rows) == 1, 'final_output_missing')
    output = rows[0]
    _require(type(output['content_bytes']) is bytes and len(output['content_bytes']) <= FINAL_OBSERVATION_LIMIT,
             'final_output_invalid')
    # Prepay the closed observation parser/model/canonical copies BEFORE work.
    budget.reserve(48 * FINAL_OBSERVATION_LIMIT + 512)
    budget.work(len(output['content_bytes']))
    observation = read_final_callback_content(output['content_bytes'])
    selected = observation.intent.selected
    _require((selected.org_slug, selected.run_id, selected.visit_id, selected.task_id, selected.principal,
              selected.session_id, selected.definition_digest, selected.start_digest, selected.cas,
              selected.last_event_digest, selected.input_selection_digest, selected.change.epoch) ==
             (run['org_slug'], run['id'], visit['id'], visit['task_id'], visit['principal'], visit['session_id'],
              compiled.pin.definition_digest, run['start_digest'], anchor['sequence'] - 1,
              anchor['event_digest'], visit['input_selection_digest'], visit['epoch']), 'final_selection_conflict')
    subject = read_repository_subject(conn, run, compiled, budget)
    _require(subject is not None and selected.change.subject == subject.subject, 'final_subject_conflict')
    proof = hold_body(conn, run=run, visit=visit, compiled=compiled, result_id=visit['result_id'],
        cause=BusinessHoldCause.CAPABILITY, event=anchor, budget=budget)
    callbacks = []
    for row in _bounded_rows(conn, 'task_results', ('id',), (('integer',),), budget,
                             where='task_id=?', args=(visit['task_id'],), maximum=1):
        budget.reserve(512)
        callbacks.append(row[0])
    _require(callbacks == [visit['result_id']], 'final_callback_conflict')
    native = _native_rows(conn, 'task_results', ('output_summary', 'verdict', 'decision_json', 'waiting_on_job_ids'),
        (('text',), ('text', 'null'), ('null',), ('null',)), visit['result_id'], budget)
    _require(len(native) == 1, 'final_callback_missing')
    summary, verdict, _, _ = native[0]
    budget.reserve(48 * 4 * len(summary) + 512)
    budget.work(len(summary))
    node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
    carrier, content = _carrier(summary, contract=node.output_contract.type,
        checks=node.completion_checks, status='completed', verdict=verdict)
    _require(carrier['outcome'] == envelope.outcome == 'merged' and not carrier['capability_claims']
             and verdict in (None, 'merged') and content == output['content_bytes']
             and tuple(item.text for item in envelope.findings) == tuple(carrier['findings']), 'final_callback_conflict')
    terminal, _ = _proof(conn, run['id'], visit['id'], 'terminal', budget)
    pin = selected.native
    _require((pin.invocation_id, pin.request_id, pin.backend) ==
             (terminal['invocation_id'], terminal['request']['logical_id'], terminal['receipt']['backend'])
             and hashlib.sha256(_bytes(dict(token=pin.handle_id, request_id=pin.request_id,
                                           backend=pin.backend))).hexdigest() == terminal['handle_digest'],
             'final_native_conflict')
    key = visit['task_id']
    seen = set()
    while key is not None:
        budget.step()
        _require(key not in seen, 'final_root_conflict')
        budget.reserve(512)
        seen.add(key)
        parents = _native_rows(conn, 'tasks', ('parent_task_id',), (('text', 'null'),), key, budget)
        _require(len(parents) == 1, 'final_root_conflict')
        root, key = key, parents[0][0]
    _require(pin.root_task_id == root, 'final_root_conflict')
    # The policy uses ALL retained current slots, including outputs not declared
    # as addressed inputs by Final. Reconstruct its original prefix, never today's
    # selections or the observation's untrusted list of upstream receipts.
    count = sum(len(items) for items in outputs.values())
    budget.reserve(4096 * count + 512)
    budget.work(count + len(outputs) + len(events))
    refs_by_visit = {key: tuple(OutputRef.model_validate(item) for item in items)
                     for key, items in outputs.items()}
    prefix = [item for item in events if item['sequence'] <= anchor['sequence']]
    _, refs = _prior_state(run, compiled, start, original, visits, prefix, refs_by_visit, budget)
    budget.reserve(512 * (len(visits) + len(compiled.definition.nodes)) + 512)
    budget.work(len(visits) * (2 + max(1, len(visits).bit_length())) + len(compiled.definition.nodes))
    nodes = {item.id: item for item in compiled.definition.nodes}
    contributors = tuple(sorted({item['principal'] for item in visits
        if item['ordinal'] < visit['ordinal'] and nodes[item['node_id']].output_contract.type == 'code-change@1'}))
    results = []
    for ref in refs:
        budget.step()
        if ref.contract in ('code-change@1', 'independent-code-review@1', 'code-qa@1'):
            results.append(_settled_input(conn, run=run, compiled=compiled, ref=ref, budget=budget))
    budget.reserve(262144)
    budget.work(1024)
    admissible = derive_coding_admissibility(CurrentCodingEvidence(run['id'], visit['id'], visit['task_id'],
        visit['principal'], visit['session_id'], selected.change, contributors, tuple(results),
        compiled.pin.definition_digest, run['start_digest'], selected.cas, anchor['event_digest'],
        visit['input_selection_digest']))
    expected = tuple(FinalResultPin(ref=item.ref, principal=item.principal, carrier_digest=item.carrier_digest,
        terminal_digest=item.terminal_digest, outer_digest=item.outer_digest)
        for item in (admissible.development, admissible.review, admissible.qa))
    _require(selected.results == expected and selected.contributors == contributors, 'final_results_conflict')
    budget.reserve(16384)
    bound = {item.role: item.principal for item in start.bindings}
    witness = final_witness(run=run, visit=visit, compiled=compiled, observation=observation,
        result_id=visit['result_id'], content_digest=output['content_digest'],
        carrier_digest=proof['callback']['carrier_digest'], terminal_digest=proof['terminal_digest'],
        outer_digest=proof['outer_digest'], bound=bound, budget=budget)
    _require(_decode(output['recipients_bytes'], budget) == dict(roles=list(witness.recipients))
             and tuple(verified_checks) == witness.verified_checks
             and tuple(item.model_dump() for item in envelope.capability_receipts) == witness.capability_receipts,
             'final_witness_conflict')
    budget.check()


def validate_final_publication(conn: sqlite3.Connection, *, org_slug: str, run_id: str,
        visit_id: str, task_id: str, result_id: int, terminal_edge: bool,
        compiled: CompiledDefinition, authority: WorkflowAuthorityCoordinator,
        capture: AuthorityAdmissionCapture, bound: dict[str, dict], budget: _ReadBudget) -> None:
    """Authenticate the whole target BEFORE the original admission COMMIT."""
    from runtime.workflows.sequential_mixed_history import read_selected_mixed_history_uncommitted
    from runtime.workflows.sequential_return import _returned
    from runtime.workflows.sequential_coding_evidence import _current_authority
    from runtime.workflows.sequential_repository_subject import read_repository_subject

    budget.check()
    try:
        read_selected_mixed_history_uncommitted(conn, org_slug=org_slug, task_id=task_id, deadline=budget.deadline)
    finally:
        conn.set_progress_handler(budget.progress, 1000)
    with _sql_budget(conn, budget):
        visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))
        _require(len(visits) == 1 and visits[0]['result_id'] == result_id, 'final_publication_conflict')
        runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
        _require(len(runs) == 1 and runs[0]['org_slug'] == org_slug, 'final_publication_conflict')
        budget.work(len(compiled.definition.nodes) + len(bound))
        subject = read_repository_subject(conn, runs[0], compiled, budget)
        node = next(item for item in compiled.definition.nodes if item.id == visits[0]['node_id'])
        _require(subject is not None, 'final_subject_conflict')
        _require(bound[node.role]['name'] == visits[0]['principal'], 'final_publication_conflict')
        origin = bound[node.role]['repos'].get(subject.subject.repo_key)
        budget.reserve(4 * 4096 + 512)
        budget.work(len(origin) if type(origin) is str and len(origin) <= 4096 else 1)
        raw_origin = origin.encode('utf8') if type(origin) is str and len(origin) <= 4096 else b''
        if (not 0 < len(raw_origin) <= 4096
                or hashlib.sha256(raw_origin).hexdigest() != subject.subject.origin_digest):
            from runtime.workflows.sequential_engine import SequentialRefusal
            raise SequentialRefusal('sequential_authority_denied')
        if terminal_edge:
            run = _returned(conn, run_id, budget)
            _require(run is not None, 'final_return_conflict')
            columns = ('status', 'completed_at', 'block_kind', 'final_output_summary', 'final_output_dir')
            kinds = (('text',), ('text',), ('null',), ('text', 'null'), ('text', 'null'))
            final = _native_rows(conn, 'tasks', columns, kinds, task_id, budget)
            _require(len(final) == 1 and final[0][0] == 'completed', 'final_return_conflict')
            for key in (run['source_task_id'], run['carrier_task_id']):
                _require(_native_rows(conn, 'tasks', columns, kinds, key, budget) == final, 'final_return_conflict')
        _current_authority(conn, authority, capture, budget)
        budget.check()
