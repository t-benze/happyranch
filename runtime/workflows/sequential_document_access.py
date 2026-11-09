"""Sequential document policy, consumed only inside the dispatcher's owned scope.

The caller authenticates original native/terminal/outer records. This module
derives addressed delivery and typed provenance, never semantic truth, private
custody, repository access or evidence from an author's capability claims.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from runtime.workflows.authority import AuthorityAdmissionCapture
from runtime.workflows.sequential_contracts import Limits, Node, OutputRef, SETTLEMENT_CHECK, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition, _json, parse_start, validate_start
from runtime.workflows.sequential_engine import SequentialRefusal, _bound_agents, check_evidence_size
from runtime.workflows.sequential_history import _ReadBudget, _WIRE_TOKENS, _bytes, _parse, _selection


_CHECKS = {'immutable-document@1': 'immutable-document@1',
           'document-review@1': 'independent-document-review@1'}


def _require(condition: bool, code: str = 'sequential_carrier_identity_conflict') -> None:
    if not condition:
        raise SequentialRefusal(code)


@dataclass(frozen=True)
class DocumentWitness:
    recipients: tuple[str, ...]
    capability_receipts: tuple[dict[str, str], ...]
    verified_checks: tuple[str, ...]


def document_only(compiled: CompiledDefinition, budget: _ReadBudget) -> bool:
    """The entire immutable graph must select the registered document subset."""
    for node in compiled.definition.nodes:
        budget.step()
        if node.output_contract.type not in _CHECKS or node.completion_checks != (_CHECKS[node.output_contract.type],):
            return False
    return True


def document_recipients(compiled: CompiledDefinition, slot: str, budget: _ReadBudget) -> tuple[str, ...]:
    """Producer plus all declared current consumers, including optional inputs."""
    roles = set()
    for node in compiled.definition.nodes:
        budget.step()
        if node.output_contract.slot == slot:
            roles.add(node.role)
        for item in node.inputs:
            budget.step()
            if item.source == 'current.' + slot:
                roles.add(node.role)
    return tuple(sorted(roles))


def document_binding_context(run: dict, visit: dict, compiled: CompiledDefinition,
                             capture: AuthorityAdmissionCapture, budget: _ReadBudget
                             ) -> tuple[StartCommand, dict[str, dict]]:
    """Recheck original pins and fixed principals against fresh owned authority.

    Charge the existing entry compiler and authority decoder without cloning
    either tree here. Lexical tokens conservatively charge their bounded walks;
    the authority snapshot itself is decoded only by the unchanged bound helper.
    """
    _require(hashlib.sha256(run['start_bytes']).hexdigest() == run['start_digest'])
    for raw in (compiled.raw, capture.ready.snapshot_bytes):
        budget.reserve(48 * len(raw) + 512)
        for _ in _WIRE_TOKENS.finditer(raw):
            budget.step()
    start = _parse(run['start_bytes'], parse_start, budget)
    original = _parse(run['original_limits_bytes'], lambda raw: Limits.model_validate(_json(raw, 16384)), budget)
    validate_start(start, compiled, original_limits=original)
    bound = _bound_agents(compiled, start, capture)
    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
    _require(bound[node.role]['name'] == visit['principal'], 'sequential_output_access_unavailable')
    budget.work(len(bound) + sum(len(requirement.independent_of) for _, requirement in compiled.definition.roles))
    return start, bound


def _selected_inputs(visit: dict, node: Node, compiled: CompiledDefinition,
                     bound: dict[str, dict], budget: _ReadBudget) -> tuple[OutputRef, ...]:
    refs = _selection(visit, budget)
    producers = {item.output_contract.slot: item for item in compiled.definition.nodes}
    budget.work(len(producers))
    declared = {item.source[8:]: item for item in node.inputs if item.source.startswith('current.')}
    budget.work(len(node.inputs))
    selected_slots = {ref.slot for ref in refs}
    _require(all(not item.required or slot in selected_slots for slot, item in declared.items()))
    for ref in refs:
        budget.step()
        producer = producers.get(ref.slot)
        _require(ref.slot in declared and producer is not None
                 and ref.contract == producer.output_contract.type and ref.contract in _CHECKS
                 and ref.producer_visit != visit['id'] and ref.epoch <= visit['epoch'])
        # This node declares current.slot, so its role is necessarily a
        # recipient. Rewalking every upstream consumer here is duplicate work.
        _require(node.role in bound and producer.role in bound, 'sequential_output_access_unavailable')
    return refs


def build_document_witness(*, org_slug: str, run: dict, visit: dict, node: Node,
                           compiled: CompiledDefinition, bound: dict[str, dict],
                           result: dict, outcome: str, content_digest: str,
                           carrier_digest: str, terminal_digest: str, outer_digest: str,
                           budget: _ReadBudget) -> DocumentWitness:
    """No upstream content reads; acceptance authenticates selection before COMMIT."""
    refs = _selected_inputs(visit, node, compiled, bound, budget)
    recipients = document_recipients(compiled, node.output_contract.slot, budget)
    _require(bool(recipients) and all(role in bound for role in recipients), 'sequential_output_access_unavailable')
    _require(result['status'] == 'completed' and type(result['id']) is int and result['id'] > 0
             and (result['task_id'], result['agent'], result['session_id']) ==
                 (visit['task_id'], bound[node.role]['name'], visit['session_id']))
    capability = 'sequential_capability_unavailable'
    if node.output_contract.type == 'immutable-document@1':
        _require(outcome == 'produced' and result['verdict'] is None, capability)
    else:
        _require(node.output_contract.type == 'document-review@1'
                 and outcome == result['verdict'] == 'APPROVE', capability)
        subjects = [ref for ref in refs if ref.contract == 'immutable-document@1']
        producers = {item.output_contract.slot: item.role for item in compiled.definition.nodes}
        budget.work(len(refs) + len(producers))
        _require(bool(subjects) and all(bound[producers[ref.slot]]['name'] != result['agent'] for ref in subjects), capability)
    preimage = dict(format='workflow-sequential-document-witness@1', org_slug=org_slug,
        run_id=run['id'], definition=compiled.pin.model_dump(mode='json'), start_digest=run['start_digest'],
        visit_id=visit['id'], node=node.id, role=node.role, epoch=visit['epoch'],
        task_id=visit['task_id'], principal=result['agent'], session_id=visit['session_id'], result_id=result['id'],
        carrier_digest=carrier_digest, input_selection_digest=visit['input_selection_digest'],
        slot=node.output_contract.slot, contract=node.output_contract.type, content_digest=content_digest,
        recipients=[dict(role=role, principal=bound[role]['name']) for role in recipients],
        terminal_digest=terminal_digest, outer_digest=outer_digest)
    # Includes canonical/model/container temporaries, but never content or a
    # second definition/authority snapshot. Fresh generation/time are excluded.
    budget.reserve(262144)
    receipts = []
    for check in node.completion_checks:
        budget.step()
        raw = _bytes(dict(preimage, check=check))
        check_evidence_size(raw)
        receipts.append(dict(check=check, evidence_digest=hashlib.sha256(raw).hexdigest()))
    budget.check()
    return DocumentWitness(recipients, tuple(receipts), (SETTLEMENT_CHECK, *node.completion_checks))


def authorize_document_input(*, compiled: CompiledDefinition, node: Node, ref: OutputRef,
                             output: dict, stored_recipients: dict, bound: dict[str, dict],
                             principal: str, budget: _ReadBudget) -> None:
    """Use the already acquired exact selected row, never a latest document."""
    expected = document_recipients(compiled, ref.slot, budget)
    producers = {item.output_contract.slot: item for item in compiled.definition.nodes}
    budget.work(len(producers) + len(node.inputs))
    producer = producers.get(ref.slot)
    _require(producer is not None and producer.output_contract.type == ref.contract
             and ref.contract in _CHECKS and producer.role in bound
             and any(item.source == 'current.' + ref.slot for item in node.inputs))
    _require(bound[node.role]['name'] == principal and node.role in expected
             and stored_recipients == dict(roles=list(expected)), 'sequential_output_access_unavailable')
    _require((output['visit_id'], output['slot'], output['contract'], output['epoch'], output['task_id'],
              output['session_id'], output['result_id'], output['content_digest']) ==
             (ref.producer_visit, ref.slot, ref.contract, ref.epoch, ref.task_id, ref.session_id, ref.result_id, ref.digest))


def document_supplement(node: Node) -> str:
    common = ('Sequential document supplement (trusted): capability_claims must be empty. '
              'Inline bytes use the existing native task-result audience; role-private custody is unavailable. '
              'References, JSON and URLs grant no repository access or effect. ')
    if node.output_contract.type == 'immutable-document@1':
        return common + ('Author exact UTF8 document bytes; use outcome produced, completed status and null verdict. '
                         'For BLOCK use empty content and your own causal findings; preserve native failure causes.')
    if node.output_contract.type == 'document-review@1':
        return common + ('Review only addressed selected document inputs. Use APPROVE, REQUEST_CHANGES or BLOCK with matching verdict. '
                         'APPROVE content is a nonempty canonical JSON object; no subject field is required or trusted. '
                         'REQUEST_CHANGES/BLOCK content is empty with your own findings. Preserve native failed/blocked causes. '
                         'Syntax and process success do not establish semantic truth.')
    return ''
