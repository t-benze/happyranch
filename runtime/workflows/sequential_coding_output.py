"""ACCESS-1 Coding declarations and addressed current-authority checks.

No Git/provider I/O: the selected downstream agent proves effective access by
its own exact-head operation. These witnesses establish bounded provenance,
never PR existence, diff scope, remote head, semantic truth or merge permission.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, model_validator

from runtime.workflows.authority import AuthorityAdmissionCapture
from runtime.workflows.sequential_contracts import Closed, Epoch, Node, OutputRef, Positive, SETTLEMENT_CHECK, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition, _json
from runtime.workflows.sequential_document_access import document_binding_context, document_recipients
from runtime.workflows.sequential_engine import SequentialRefusal, _engine_rows, _identity, check_evidence_size
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decoded, _selection
from runtime.workflows.sequential_repository_records import RepositoryChangeSubject
from runtime.workflows.sequential_repository_subject import read_repository_subject


if TYPE_CHECKING:
    from runtime.workflows.sequential_coding_policy import SettledCodingResult


class PullRequestDeclaration(Closed):
    repository: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', max_length=256)]
    number: Positive
    url: Annotated[str, Field(max_length=512)]

    @model_validator(mode='after')
    def identity(self) -> Self:
        if (any(part in ('.', '..') for part in self.repository.split('/'))
                or self.url != f'https://github.com/{self.repository}/pull/{self.number}'):
            raise ValueError('sequential_code_change_invalid')
        return self


class CodeChangeDeclaration(Closed):
    format: Literal['workflow-sequential-code-change@1']
    subject: RepositoryChangeSubject
    epoch: Epoch
    head: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
    pr: PullRequestDeclaration


@dataclass(frozen=True)
class CodingWitness:
    recipients: tuple[str, ...]
    capability_receipts: tuple[dict[str, str], ...]
    verified_checks: tuple[str, ...]


def _require(value: bool, code: str) -> None:
    if not value:
        raise SequentialRefusal(code)


def coding_graph(compiled: CompiledDefinition) -> bool:
    return any(node.output_contract.type == 'code-change@1' for node in compiled.definition.nodes)


def coding_assessment_supplement(node: Node) -> str:
    outcomes = ('APPROVE, REQUEST_CHANGES or BLOCK' if node.output_contract.type == 'independent-code-review@1'
                else 'PASS, REVISE or BLOCK')
    return ('Sequential Coding assessment supplement (trusted): independently assess only the addressed exact '
            'repository/origin/base/scope/head/PR/epoch. Prove effective access with your own exact-head fetch '
            'in this live session; metadata or another agent assessment is not access proof. '
            'Use ' + outcomes + ' with matching verdict. APPROVE/PASS uses completed status and canonical JSON '
            'content with exactly format=workflow-sequential-code-assessment@1 and change (the entire unchanged '
            'selected workflow-sequential-code-change@1 declaration). Negative outcomes have empty content and '
            'own causal findings. capability_claims must be empty. An unavailable required operation uses BLOCK '
            'and distinguishes denied from unverified. Preserve native failure causes and your genuine FINAL '
            'callback. No new credential, permission, daemon fetch or Final merge capability follows.')


def coding_supplement() -> str:
    return ('Sequential Coding declaration supplement (trusted): capability_claims must be empty. '
            'For produced/completed/null verdict, content is canonical JSON with exactly format='
            'workflow-sequential-code-change@1, subject (the entire original committed subject object), '
            'epoch (this visit content epoch), head (full lowercase 40-hex SHA1), and pr containing exactly '
            'repository (owner/repo), number (positive INTEGER), url (https://github.com/owner/repo/pull/number). '
            'For BLOCK use empty content/claims and your own causal findings. '
            'These declarations do not prove PR existence/head, diff scope or effective access. '
            'ACCESS-1 THR-336 msg131: daemon rechecks current authority and unchanged subject only. '
            'Agents share one OS user and one gh credential; the runtime cannot independently observe '
            'per-agent effective access. Downstream agents must prove access through their own exact-head '
            'operation; unavailable/denied operations require BLOCK with that distinction. '
            'Review/QA use the addressed assessment supplement; final-manager guarded merge remains unavailable.')


def coding_binding_context(run: dict, visit: dict, compiled: CompiledDefinition,
                           capture: AuthorityAdmissionCapture, budget: _ReadBudget
                           ) -> tuple[StartCommand, dict[str, dict]]:
    """Reuse original pins/bindings/eligibility validation, without access claims."""
    try:
        return document_binding_context(run, visit, compiled, capture, budget)
    except SequentialRefusal as exc:
        if exc.code in ('role_unavailable', 'role_ineligible', 'sequential_output_access_unavailable'):
            raise SequentialRefusal('sequential_authority_denied') from None
        raise
    except ValueError as exc:
        if str(exc) in ('workflow_seq_role_unavailable', 'workflow_seq_role_ineligible'):
            raise SequentialRefusal('sequential_authority_denied') from None
        raise


def _declaration(conn: sqlite3.Connection, *, run: dict, compiled: CompiledDefinition,
                 content: bytes, epoch: int, budget: _ReadBudget) -> tuple[CodeChangeDeclaration, str]:
    # Reserve bounded parser/model/container copies on the original scope budget.
    budget.reserve(96 * len(content) + 4096)
    try:
        _json(content, 49152)
        declaration = CodeChangeDeclaration.model_validate_json(content, strict=True)
    except (ValueError, TypeError, UnicodeError):
        raise SequentialRefusal('sequential_code_change_invalid') from None
    # Charge the decoded walk outside the malformed-output handler so exhaustion stays a reader refusal.
    _decoded(declaration.model_dump(mode='json'), budget)
    receipt = read_repository_subject(conn, run, compiled, budget)
    _require(receipt is not None, 'sequential_repository_subject_unverified')
    _require(declaration.subject == receipt.subject and declaration.epoch == epoch,
             'sequential_repository_subject_changed')
    # Check exact full SHA1 syntax independently of the shared subject grammar.
    _require(re.fullmatch(r'[0-9a-f]{40}', declaration.subject.base) is not None,
             'sequential_code_change_invalid')
    budget.reserve(65536)
    return declaration, hashlib.sha256(_bytes(receipt.model_dump())).hexdigest()


def _assessment_declaration(conn: sqlite3.Connection, *, run: dict, compiled: CompiledDefinition,
                            content: bytes, epoch: int, budget: _ReadBudget) -> tuple[CodeChangeDeclaration, str]:
    # Local import preserves the policy -> declaration dependency; use U5a's
    # closed syntax, with all parsing/copies/traversal on the ORIGINAL budget.
    from runtime.workflows.sequential_coding_policy import read_code_assessment
    budget.reserve(96 * len(content) + 4096)
    try:
        assessment = read_code_assessment(content)
    except (ValueError, TypeError, UnicodeError):
        raise SequentialRefusal('sequential_code_change_invalid') from None
    _decoded(assessment.model_dump(mode='json'), budget)
    return _declaration(conn, run=run, compiled=compiled, content=_bytes(assessment.change.model_dump()),
                        epoch=epoch, budget=budget)


def _settled_input(conn: sqlite3.Connection, *, run: dict, compiled: CompiledDefinition,
                   ref: OutputRef, budget: _ReadBudget) -> SettledCodingResult:
    from runtime.workflows.sequential_coding_evidence import _settled
    from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=? AND run_id=?', (ref.producer_visit, run['id']))
    outputs = _engine_rows(conn, 'workflow_seq_outputs', budget, 'id=? AND run_id=?',
                           (_identity('output', ref.producer_visit, ref.slot), run['id']))
    _require(len(visits) == len(outputs) == 1, 'sequential_repository_subject_unverified')
    visit, output = visits[0], outputs[0]
    _require((visit['task_id'], visit['session_id'], visit['result_id'], visit['epoch']) ==
             (ref.task_id, ref.session_id, ref.result_id, ref.epoch),
             'sequential_repository_subject_unverified')
    _require((output['visit_id'], output['slot'], output['contract'], output['epoch'], output['task_id'],
              output['session_id'], output['result_id'], output['content_digest']) ==
             (ref.producer_visit, ref.slot, ref.contract, ref.epoch, ref.task_id, ref.session_id, ref.result_id, ref.digest)
             and hashlib.sha256(output['content_bytes']).hexdigest() == ref.digest,
             'sequential_repository_subject_unverified')
    node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
    _require(node.output_contract.type == ref.contract and node.output_contract.slot == ref.slot,
             'sequential_repository_subject_unverified')
    events = _engine_rows(conn, 'workflow_seq_events', budget, 'run_id=? AND sequence=?' if visit['ordinal'] == 1
        else 'run_id=? AND id=?', (run['id'], 1 if visit['ordinal'] == 1 else visit['predecessor_event_id']))
    _require(len(events) == 1, 'sequential_repository_subject_unverified')
    try:
        settled = _settled(conn, run=run, visit=visit, output=output, compiled=compiled, events=events, budget=budget)
    except CodingEvidenceRefusal:
        raise SequentialRefusal('sequential_repository_subject_unverified') from None
    except ValueError as exc:
        # Reader exhaustion is never malformed agent content or a fresh budget.
        if str(exc).startswith('workflow_seq_read_'):
            raise
        raise SequentialRefusal('sequential_repository_subject_unverified') from None
    _require(settled.ref == ref and settled.outcome == {'code-change@1': 'produced',
        'independent-code-review@1': 'APPROVE', 'code-qa@1': 'PASS'}.get(ref.contract)
        and settled.verdict == (None if ref.contract == 'code-change@1' else settled.outcome),
        'sequential_repository_subject_unverified')
    return settled


def _assessment_inputs(conn: sqlite3.Connection, *, run: dict, visit: dict, node: Node,
                       compiled: CompiledDefinition, bound: dict[str, dict],
                       declaration: CodeChangeDeclaration, budget: _ReadBudget) -> None:
    refs = _selection(visit, budget)
    subjects = [ref for ref in refs if ref.contract == 'code-change@1']
    _require(len(subjects) == 1, 'sequential_repository_subject_unverified')
    producers = {item.output_contract.slot: item for item in compiled.definition.nodes}
    budget.work(len(producers) + len(node.inputs) + len(refs))
    for ref in refs:
        _require(any(item.source == 'current.' + ref.slot for item in node.inputs),
                 'sequential_repository_subject_unverified')
        if ref.contract not in ('code-change@1', 'independent-code-review@1', 'code-qa@1'):
            continue
        settled = _settled_input(conn, run=run, compiled=compiled, ref=ref, budget=budget)
        _require(settled.change == declaration and ref.epoch == visit['epoch'],
                 'sequential_repository_subject_changed')
        producer = producers[ref.slot]
        _require(bound[producer.role]['name'] == settled.principal
                 and bound[producer.role]['role'] == 'worker', 'sequential_authority_denied')
        _require(settled.principal != visit['principal'], 'sequential_capability_unavailable')
    # Retain every native code-change contributor, including invalidated epochs.
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=?', (run['id'],))
    nodes = {item.id: item for item in compiled.definition.nodes}
    budget.work(len(visits) + len(nodes))
    _require(bound[node.role]['role'] == 'worker' and all(item['principal'] != visit['principal']
        for item in visits if nodes[item['node_id']].output_contract.type == 'code-change@1'),
        'sequential_capability_unavailable')


def build_coding_witness(conn: sqlite3.Connection, *, org_slug: str, run: dict, visit: dict,
                         node: Node, compiled: CompiledDefinition, bound: dict[str, dict],
                         result: dict, outcome: str, content: bytes, content_digest: str,
                         carrier_digest: str, terminal_digest: str, outer_digest: str,
                         budget: _ReadBudget) -> CodingWitness:
    expected = {'code-change@1': 'produced', 'independent-code-review@1': 'APPROVE', 'code-qa@1': 'PASS'}
    _require(outcome == expected.get(node.output_contract.type)
             and result['status'] == 'completed'
             and result['verdict'] == (None if outcome == 'produced' else outcome),
             'sequential_code_change_invalid')
    _require(type(result['id']) is int and result['id'] > 0
             and (result['task_id'], result['agent'], result['session_id']) ==
                 (visit['task_id'], bound[node.role]['name'], visit['session_id']),
             'sequential_carrier_identity_conflict')
    reader = _declaration if node.output_contract.type == 'code-change@1' else _assessment_declaration
    declaration, subject_digest = reader(conn, run=run, compiled=compiled,
        content=content, epoch=visit['epoch'], budget=budget)
    if node.output_contract.type != 'code-change@1':
        _assessment_inputs(conn, run=run, visit=visit, node=node, compiled=compiled, bound=bound,
                           declaration=declaration, budget=budget)
    recipients = document_recipients(compiled, node.output_contract.slot, budget)
    _require(bool(recipients) and all(role in bound for role in recipients), 'sequential_authority_denied')
    preimage = dict(format='workflow-sequential-coding-witness@1', org_slug=org_slug,
        run_id=run['id'], definition=compiled.pin.model_dump(mode='json'), start_digest=run['start_digest'],
        visit_id=visit['id'], node=node.id, role=node.role, epoch=visit['epoch'],
        task_id=visit['task_id'], principal=result['agent'], session_id=visit['session_id'], result_id=result['id'],
        carrier_digest=carrier_digest, input_selection_digest=visit['input_selection_digest'],
        slot=node.output_contract.slot, contract=node.output_contract.type, content_digest=content_digest,
        subject_digest=subject_digest, head=declaration.head, pr=declaration.pr.model_dump(),
        recipients=[dict(role=role, principal=bound[role]['name']) for role in recipients],
        terminal_digest=terminal_digest, outer_digest=outer_digest)
    budget.reserve(262144)
    receipts = []
    for check in node.completion_checks:
        budget.step()
        raw = _bytes(dict(preimage, check=check))
        check_evidence_size(raw)
        receipts.append(dict(check=check, evidence_digest=hashlib.sha256(raw).hexdigest()))
    budget.check()
    return CodingWitness(recipients, tuple(receipts), (SETTLEMENT_CHECK, *node.completion_checks))


def authorize_coding_input(conn: sqlite3.Connection, *, run: dict, visit: dict, compiled: CompiledDefinition,
                           node: Node, ref: OutputRef, output: dict, stored_recipients: dict,
                           bound: dict[str, dict], principal: str, budget: _ReadBudget) -> None:
    """Address the original selection only, after a fresh owned authority check."""
    expected = document_recipients(compiled, ref.slot, budget)
    budget.work(len(compiled.definition.nodes) + len(node.inputs))
    producer = next((item for item in compiled.definition.nodes if item.output_contract.slot == ref.slot), None)
    _require(producer is not None and producer.output_contract.type == ref.contract
             and ref.producer_visit != visit['id'] and ref.epoch <= visit['epoch'] and producer.role in bound,
             'sequential_carrier_identity_conflict')
    _require(bound[node.role]['name'] == principal and node.role in expected
             and stored_recipients == dict(roles=list(expected)), 'sequential_authority_denied')
    _require(any(item.source == 'current.' + ref.slot for item in node.inputs),
             'sequential_carrier_identity_conflict')
    if ref.contract == 'code-change@1':
        _declaration(conn, run=run, compiled=compiled, content=output['content_bytes'],
                     epoch=ref.epoch, budget=budget)
    elif ref.contract in ('independent-code-review@1', 'code-qa@1'):
        declaration, _ = _assessment_declaration(conn, run=run, compiled=compiled, content=output['content_bytes'],
                                                 epoch=visit['epoch'], budget=budget)
        settled = _settled_input(conn, run=run, compiled=compiled, ref=ref, budget=budget)
        _require(ref.epoch == visit['epoch'] and settled.change == declaration,
                 'sequential_repository_subject_changed')
        _require(settled.principal == bound[producer.role]['name'], 'sequential_authority_denied')
        for selected in _selection(visit, budget):
            if selected.contract == 'code-change@1':
                current = _settled_input(conn, run=run, compiled=compiled, ref=selected, budget=budget)
                _require(current.change == declaration and selected.epoch == visit['epoch'],
                         'sequential_repository_subject_changed')
    else:
        # Final remains unavailable: assessment provenance grants no merge capability.
        raise SequentialRefusal('sequential_output_access_unavailable')
