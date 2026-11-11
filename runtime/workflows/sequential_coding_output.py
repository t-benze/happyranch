"""ACCESS-1 Coding declarations and addressed current-authority checks.

No Git/provider I/O: the selected downstream agent proves effective access by
its own exact-head operation. These witnesses establish bounded provenance,
never PR existence, diff scope, remote head, review/QA or merge permission.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from runtime.workflows.authority import AuthorityAdmissionCapture
from runtime.workflows.sequential_contracts import Closed, Epoch, Node, OutputRef, Positive, SETTLEMENT_CHECK, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition, _json
from runtime.workflows.sequential_document_access import document_binding_context, document_recipients
from runtime.workflows.sequential_engine import SequentialRefusal, check_evidence_size
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decoded
from runtime.workflows.sequential_repository_records import RepositoryChangeSubject
from runtime.workflows.sequential_repository_subject import read_repository_subject


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
            'Review/QA capabilities and final-manager guarded merge remain U5.')


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


def build_coding_witness(conn: sqlite3.Connection, *, org_slug: str, run: dict, visit: dict,
                         node: Node, compiled: CompiledDefinition, bound: dict[str, dict],
                         result: dict, outcome: str, content: bytes, content_digest: str,
                         carrier_digest: str, terminal_digest: str, outer_digest: str,
                         budget: _ReadBudget) -> CodingWitness:
    _require(node.output_contract.type == 'code-change@1' and outcome == 'produced'
             and result['status'] == 'completed' and result['verdict'] is None,
             'sequential_code_change_invalid')
    _require(type(result['id']) is int and result['id'] > 0
             and (result['task_id'], result['agent'], result['session_id']) ==
                 (visit['task_id'], bound[node.role]['name'], visit['session_id']),
             'sequential_carrier_identity_conflict')
    declaration, subject_digest = _declaration(conn, run=run, compiled=compiled,
        content=content, epoch=visit['epoch'], budget=budget)
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
    else:
        # Other Coding producers remain U5; no arbitrary saved output is disclosed.
        raise SequentialRefusal('sequential_output_access_unavailable')
