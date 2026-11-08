"""Pure sequential decisions. Trusted adapters supply authenticated evidence.

No IDs are allocated here. A decision is an intent, not an admitted task, receipt,
CAS commit or permission. The future owner must validate persisted history and
current authority, then atomically consume the result and allocate any successor.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from runtime.workflows.sequential_contracts import (
    Check, Closed, Counters, Digest, Edge, Epoch, Finding, HoldEdge, Limits,
    Name, NextEdge, OutputRef, Positive, ResultEnvelope, SETTLEMENT_CHECK,
    TerminalEdge, Token, _array, effective_limits,
)
from runtime.workflows.sequential_definition import (
    CompiledDefinition, SequentialContractError, _OUTPUTS, _return_invalidations,
)


class ActiveVisit(Closed):
    node: Name
    visit_id: Token
    task_id: Token
    session_id: Token
    epoch: Epoch
    input_selection_digest: Digest


class AuthenticatedResult(Closed):
    """Adapter assertion, never authentication of untrusted model data.

    The adapter must prove the full INTEGER callback, current authority, subject,
    output integrity/access, and positive original native AND outer settlement.
    verified_checks names trusted current-subject checks, not unsigned reports.
    """

    task_id: Token
    session_id: Token
    result_id: Positive
    envelope: ResultEnvelope
    verified_checks: Annotated[
        tuple[Check | Literal["native-success-and-positive-outer-settlement@1"], ...],
        BeforeValidator(_array), Field(min_length=1, max_length=9),
    ]

    @model_validator(mode="after")
    def distinct_checks(self) -> Self:
        if len(set(self.verified_checks)) != len(self.verified_checks):
            raise ValueError("duplicate verified check")
        return self


class Activation(Closed):
    """Next-node intent only; contains no task/visit allocation or authority."""

    node: Name
    epoch: Epoch
    inputs: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]


class Decision(Closed):
    run_id: Token
    definition_digest: Digest
    source: ActiveVisit
    source_result: Positive
    result_digest: Digest
    outcome: Token
    before: Counters
    after: Counters
    edge: Edge
    reason: Name
    activation: Activation | None
    selections: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    invalidated: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_array), Field(max_length=2048)]

    @model_validator(mode="after")
    def activation_matches_edge(self) -> Self:
        if isinstance(self.edge, NextEdge):
            if (self.activation is None or self.activation.node != self.edge.node
                    or self.activation.epoch != self.after.epoch):
                raise ValueError("next decision requires matching activation")
        elif self.activation is not None:
            raise ValueError("non-next decision cannot activate")
        return self


class RunState(Closed):
    """Validated owner snapshot. Original/lower limits are immutable run pins.

    receipts contains previously committed decisions, never proposed decisions.
    A future complete reader must authenticate their history, not trust this model
    as a storage validator. Selections contain only previously validated evidence.
    """

    run_id: Token
    definition_digest: Digest
    original_limits: Limits
    lower_limits: Limits
    counters: Counters
    active_visit: ActiveVisit | None
    selections: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_array), Field(max_length=2048)]
    receipts: Annotated[tuple[Decision, ...], BeforeValidator(_array), Field(max_length=4096)]


def effective_run_limits(compiled: CompiledDefinition, state: RunState) -> Limits:
    """Immutable effective ceilings (remaining counts are rendered separately)."""
    return effective_limits(compiled.definition.limits, state.original_limits, state.lower_limits)


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise SequentialContractError(code)


def _result_digest(result: AuthenticatedResult) -> str:
    # Include trusted binding/checks as well as the full envelope. Reordering or
    # changing any supplied field conflicts; no callback identity is inferred.
    raw = json.dumps(result.model_dump(mode="json"), sort_keys=True,
                     separators=(",", ":"), ensure_ascii=False).encode("utf8")
    return hashlib.sha256(raw).hexdigest()


def _validate_state(compiled: CompiledDefinition, state: RunState) -> None:
    _refuse(state.definition_digest == compiled.pin.definition_digest, "state_definition_mismatch")
    nodes = {node.id: node for node in compiled.definition.nodes}
    outputs = {node.output_contract.slot: node.output_contract.type for node in nodes.values()}
    _refuse(len({ref.slot for ref in state.selections}) == len(state.selections), "duplicate_selection")
    for ref in state.selections:
        _refuse(outputs.get(ref.slot) == ref.contract and ref.epoch <= state.counters.epoch,
                "invalid_current_selection")
    visit = state.active_visit
    if visit is not None:
        _refuse(visit.node in nodes and visit.epoch == state.counters.epoch, "invalid_active_visit")
    keys = [(item.source.visit_id, item.source_result) for item in state.receipts]
    _refuse(len(set(keys)) == len(keys)
            and len({item.source.visit_id for item in state.receipts}) == len(keys)
            and len({item.source_result for item in state.receipts}) == len(keys), "duplicate_consumption")
    for item in state.receipts:
        _refuse(item.run_id == state.run_id and item.definition_digest == state.definition_digest,
                "foreign_replay_receipt")


def _activation_inputs(compiled: CompiledDefinition, node_id: str,
                       selections: tuple[OutputRef, ...]) -> tuple[OutputRef, ...] | None:
    node = next(node for node in compiled.definition.nodes if node.id == node_id)
    available = {ref.slot: ref for ref in selections}
    selected = []
    for item in node.inputs:
        if item.source.startswith("current."):
            ref = available.get(item.source[8:])
            if ref is None and item.required:
                return None
            if ref is not None and ref not in selected:
                selected.append(ref)
    return tuple(selected)


def reduce_result(compiled: CompiledDefinition, state: RunState,
                  result: AuthenticatedResult) -> Decision:
    """Select one declared edge or a truthful hold, with deterministic replay.

    No I/O, native proof production, history adoption or side effect occurs. The
    supplied state and adapter evidence are trusted inputs, not wire audiences.
    Conflicting replay/provenance refuses; invalid domain evidence holds without
    selecting outputs or creating a successor. Limits are never replenished.
    """
    _validate_state(compiled, state)
    envelope = result.envelope
    _refuse(envelope.run_id == state.run_id
            and envelope.definition_digest == state.definition_digest, "result_run_mismatch")
    digest = _result_digest(result)
    for receipt in state.receipts:
        if (receipt.source.visit_id == envelope.visit_id
                or receipt.source_result == result.result_id):
            _refuse(receipt.source.visit_id == envelope.visit_id
                    and receipt.source_result == result.result_id
                    and receipt.result_digest == digest, "conflicting_replay")
            return receipt
    visit = state.active_visit
    _refuse(visit is not None, "no_active_visit")
    _refuse(envelope.visit_id == visit.visit_id and result.task_id == visit.task_id
            and result.session_id == visit.session_id
            and envelope.input_selection_digest == visit.input_selection_digest,
            "result_visit_mismatch")
    for finding in envelope.findings:
        _refuse(finding.source_visit == visit.visit_id
                and finding.source_result == result.result_id, "finding_attribution_mismatch")
    node = next(node for node in compiled.definition.nodes if node.id == visit.node)
    before = state.counters
    limits = effective_run_limits(compiled, state)
    hold = HoldEdge(action="hold", owner="accountable_manager", remedy="no_automatic_retry")
    edge = dict(node.outcomes).get(envelope.outcome, hold)
    reason = "declared_edge" if envelope.outcome in dict(node.outcomes) else "undeclared_outcome"
    after = before
    selections, invalidated, findings = state.selections, (), state.findings
    activation = None
    checks = set(result.verified_checks)
    bounded = (before.events < limits.max_events and before.visits <= limits.max_visits
               and before.epoch <= limits.max_content_epochs
               and before.content_returns <= limits.content_returns)
    if not bounded:
        edge, reason = hold, "limit_exhausted"
    else:
        after = Counters(epoch=before.epoch, content_returns=before.content_returns,
                         visits=before.visits, events=before.events + 1)
        if SETTLEMENT_CHECK not in checks:
            edge, reason = hold, "settlement_unverified"
        elif reason != "undeclared_outcome":
            success = envelope.outcome == _OUTPUTS[node.output_contract.type][0]
            if success:
                expected = node.output_contract
                valid = (len(envelope.outputs) == 1
                         and set(node.completion_checks) <= checks
                         and {ref.check for ref in envelope.capability_receipts} == set(node.completion_checks))
                for ref in envelope.outputs:
                    valid = valid and (ref.slot == expected.slot and ref.contract == expected.type
                                       and ref.epoch == before.epoch and ref.producer_visit == visit.visit_id
                                       and ref.task_id == result.task_id and ref.session_id == result.session_id
                                       and ref.result_id == result.result_id)
                if not valid:
                    edge, reason = hold, "output_evidence_mismatch"
                else:
                    selections = tuple(ref for ref in selections if ref.slot != expected.slot) + envelope.outputs
            if reason == "declared_edge":
                findings = findings + envelope.findings
                if isinstance(edge, NextEdge):
                    returning = edge.consume_counter is not None
                    if returning:
                        # Retire stale evidence even if no new producer can run.
                        nodes = {item.id: item for item in compiled.definition.nodes}
                        forward = {key: {link.node for _, link in item.outcomes
                                         if isinstance(link, NextEdge) and link.consume_counter is None}
                                   for key, item in nodes.items()}
                        slots = _return_invalidations(nodes, forward, edge.node)
                        invalidated = tuple(ref for ref in selections if ref.slot in slots)
                        selections = tuple(ref for ref in selections if ref.slot not in slots)
                    if (before.visits >= limits.max_visits or after.events >= limits.max_events
                            or (returning and (before.content_returns >= limits.content_returns
                                              or before.epoch >= limits.max_content_epochs))):
                        edge, reason = hold, "limit_exhausted"
                    else:
                        after = Counters(epoch=before.epoch + int(returning),
                                         content_returns=before.content_returns + int(returning),
                                         visits=before.visits + 1, events=after.events)
                        inputs = _activation_inputs(compiled, edge.node, selections)
                        if inputs is None:
                            # Preserve spent event/return counters; reserve no visit.
                            after = Counters(epoch=after.epoch, content_returns=after.content_returns,
                                             visits=before.visits, events=after.events)
                            edge, reason = hold, "required_input_unavailable"
                        else:
                            activation = Activation(node=edge.node, epoch=after.epoch, inputs=inputs)
                elif isinstance(edge, TerminalEdge):
                    terminal = dict(compiled.definition.terminal_conditions)[edge.condition]
                    if not any(ref.slot == terminal.output for ref in selections):
                        edge, reason = hold, "terminal_evidence_unavailable"
    return Decision(run_id=state.run_id, definition_digest=state.definition_digest,
                    source=visit, source_result=result.result_id, result_digest=digest, outcome=envelope.outcome,
                    before=before, after=after, edge=edge, reason=reason, activation=activation,
                    selections=selections, invalidated=invalidated, findings=findings)
