"""Unmounted U7c2a Final witness consumer; no result or publication writer.

The caller owns the original authority/teams/DB locks and transaction. The
complete callback reader authenticates the actual result, never a supplied
validated object. A returned witness is transient provenance, not admission,
effective access, remote truth or authority to repeat an action.
"""
from __future__ import annotations

import hashlib
import sqlite3

from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityCoordinator
from runtime.workflows.sequential_coding_final_callback import read_final_callback_uncommitted
from runtime.workflows.sequential_coding_final_observation import FINAL_OBSERVATION_LIMIT
from runtime.workflows.sequential_coding_output import CodingWitness, coding_binding_context
from runtime.workflows.sequential_contracts import SETTLEMENT_CHECK
from runtime.workflows.sequential_definition import CompiledDefinition
from runtime.workflows.sequential_document_access import document_recipients
from runtime.workflows.sequential_engine import SequentialRefusal, check_evidence_size
from runtime.workflows.sequential_history import _ReadBudget, _bytes


def _require(condition: bool) -> None:
    if not condition:
        raise SequentialRefusal('sequential_carrier_identity_conflict')


def build_final_witness_uncommitted(conn: sqlite3.Connection, *, org_slug: str,
        run: dict, visit: dict, compiled: CompiledDefinition, result: dict,
        content_digest: str, carrier_digest: str, terminal_digest: str, outer_digest: str,
        authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture,
        budget: _ReadBudget) -> CodingWitness:
    """Read the genuine selected callback, then derive addressed receipts.

    No production caller yet. U7c2b must supply compatible complete output
    history and same-transaction acceptance/return validation before mounting.
    All added work spends the caller's SAME allowance and original deadline;
    mandatory nested complete reads retain their existing R-M3-2 allowances.
    No refusal is caught, retried or converted into business-Held here.
    """
    budget.check()
    try:
        validated = read_final_callback_uncommitted(conn, org_slug=org_slug,
            run_id=run['id'], visit_id=visit['id'], task_id=visit['task_id'],
            principal=visit['principal'], session_id=visit['session_id'], result_id=result['id'],
            authority=authority, capture=capture, deadline=budget.deadline)
    finally:
        # The nested reader owns its hook only during its read. Return custody
        # to the original caller even on a genuine exhaustion/refusal.
        conn.set_progress_handler(budget.progress, 1000)
    budget.check()
    # Prepay the bounded observation model/JSON copies and their full traversal
    # BEFORE model_dump/serialization/hash. No encoding just to size a reserve.
    budget.reserve(48 * FINAL_OBSERVATION_LIMIT + 512)
    budget.work(FINAL_OBSERVATION_LIMIT)
    observation = validated.observation
    selected = observation.intent.selected
    raw = _bytes(observation.model_dump(mode='json'))
    _require(hashlib.sha256(raw).hexdigest() == content_digest)
    del raw
    _require(type(result['id']) is int and result['id'] > 0 and result['id'] == validated.result_id
             and result['status'] == 'completed' and result['verdict'] in (None, 'merged')
             and (result['task_id'], result['agent'], result['session_id']) ==
                 (visit['task_id'], visit['principal'], visit['session_id']))
    _require((carrier_digest, terminal_digest, outer_digest) ==
             (validated.carrier_digest, validated.terminal_digest, validated.outer_digest))
    _require((run['org_slug'], run['id'], run['start_digest'], run['cas'], run['epoch'],
              visit['run_id'], visit['id'], visit['input_selection_digest']) ==
             (org_slug, selected.run_id, selected.start_digest, selected.cas, selected.change.epoch,
              selected.run_id, selected.visit_id, selected.input_selection_digest)
             and compiled.pin.definition_digest == selected.definition_digest
             and visit['epoch'] == selected.change.epoch)
    budget.work(len(compiled.definition.nodes))
    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
    _require(node.output_contract.type == 'guarded-repository-merge@1')
    _, bound = coding_binding_context(run, visit, compiled, capture, budget)
    recipients = document_recipients(compiled, node.output_contract.slot, budget)
    _require(bool(recipients) and all(role in bound for role in recipients))
    # The output digest binds the ENTIRE original observation: immutable change,
    # original selection and intent, both literal lint records, successful
    # original command and independent remote receipt. Do not serialize those
    # nested trees again for each check or infer effective access from them.
    # Prepay the small definition/recipient/preimage copies before construction.
    budget.reserve(262144)
    budget.work(512)
    preimage = dict(format='workflow-sequential-coding-final-witness@1', org_slug=org_slug,
        run_id=run['id'], definition=compiled.pin.model_dump(mode='json'), start_digest=run['start_digest'],
        visit_id=visit['id'], node=node.id, role=node.role, epoch=visit['epoch'],
        task_id=visit['task_id'], principal=result['agent'], session_id=visit['session_id'], result_id=result['id'],
        carrier_digest=carrier_digest, input_selection_digest=visit['input_selection_digest'],
        slot=node.output_contract.slot, contract=node.output_contract.type, content_digest=content_digest,
        selection_digest=observation.selection_digest,
        recipients=[dict(role=role, principal=bound[role]['name']) for role in recipients],
        terminal_digest=terminal_digest, outer_digest=outer_digest)
    receipts = []
    for check in node.completion_checks:
        # Each canonical evidence copy/check/hash spends this SAME allowance.
        # check_evidence_size retains the original bound; it is not raised.
        budget.reserve(48 * 16384 + 512)
        budget.work(512)
        evidence = _bytes(dict(preimage, check=check))
        check_evidence_size(evidence)
        receipts.append(dict(check=check, evidence_digest=hashlib.sha256(evidence).hexdigest()))
    budget.check()
    return CodingWitness(recipients, tuple(receipts), (SETTLEMENT_CHECK, *node.completion_checks))
