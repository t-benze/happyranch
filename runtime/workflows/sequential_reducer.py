"""Pure sequential decisions. Trusted adapters supply authenticated evidence.

No IDs are allocated here. A decision is an intent, not an admitted task, receipt,
CAS commit or permission. The future owner must validate persisted history and
current authority, then atomically consume the result and allocate any successor.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, StrictBool, model_validator

from runtime.workflows.sequential_contracts import (
    Check, Closed, Counters, Digest, Edge, Epoch, Finding, HoldEdge, Limits,
    Name, NextEdge, OutputRef, Positive, ResultEnvelope, SETTLEMENT_CHECK,
    TerminalEdge, Token, _array, effective_limits,
)
from runtime.workflows.sequential_definition import (
    CompiledDefinition, SequentialContractError, _OUTPUTS, _return_invalidations,
)
from runtime.workflows.sequential_contracts import (
    GenericCounters, GenericEdge, GenericHoldEdge, GenericLimits, GenericNextEdge,
    GenericNodeCounters, GenericOutputRef, GenericResultEnvelope, VisitNumber,
)
from runtime.workflows.sequential_definition import (
    GenericCompiledDefinition, generic_limits, validate_generic_content,
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


class GenericActiveVisit(Closed):
    node: Name
    visit_id: Token
    task_id: Token
    session_id: Token
    ordinal: VisitNumber
    input_selection_digest: Digest


class GenericAuthenticatedResult(Closed):
    """Future adapter assertion of native binding, current access and settlement.

    S1 produces none of these proofs. Domain checks and unsigned capability
    claims have no place in this @2 interface.
    """

    task_id: Token
    session_id: Token
    result_id: Positive
    envelope: GenericResultEnvelope
    content: bytes
    settled: StrictBool


class GenericActivation(Closed):
    node: Name
    ordinal: VisitNumber
    inputs: Annotated[tuple[GenericOutputRef, ...], BeforeValidator(_array), Field(max_length=32)]


class GenericDecision(Closed):
    run_id: Token
    definition_digest: Digest
    source: GenericActiveVisit
    source_result: Positive
    result_digest: Digest
    outcome: Token
    before: GenericCounters
    after: GenericCounters
    edge: GenericEdge
    reason: Name
    activation: GenericActivation | None
    selections: Annotated[tuple[GenericOutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    invalidated: Annotated[tuple[GenericOutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_array), Field(max_length=2048)]

    @model_validator(mode='after')
    def matching_activation(self) -> Self:
        if isinstance(self.edge, GenericNextEdge):
            if (self.activation is None or self.activation.node != self.edge.node
                    or self.activation.ordinal != self.after.visits):
                raise ValueError('next decision requires matching activation')
        elif self.activation is not None:
            raise ValueError('non-next decision cannot activate')
        return self


class GenericRunState(Closed):
    """Pure trusted snapshot, not a schema2 reader or recovery/control writer."""

    run_id: Token
    definition_digest: Digest
    original_limits: GenericLimits
    lower_limits: GenericLimits
    counters: GenericCounters
    active_visit: GenericActiveVisit | None
    selections: Annotated[tuple[GenericOutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_array), Field(max_length=2048)]
    receipts: Annotated[tuple[GenericDecision, ...], BeforeValidator(_array), Field(max_length=4096)]


def _validate_generic_state(compiled: GenericCompiledDefinition, state: GenericRunState) -> None:
    _refuse(state.definition_digest == compiled.pin.definition_digest, 'state_definition_mismatch')
    nodes = {node.id: node for node in compiled.definition.nodes}
    counts = dict(state.counters.nodes)
    _refuse(counts.keys() == nodes.keys(), 'incomplete_node_counters')
    _refuse(sum(count.attempts for count in counts.values()) == state.counters.visits
            and sum(count.recoveries for count in counts.values()) == state.counters.recoveries
            and all(count.revisions + count.recoveries <= count.attempts if count.attempts else
                    count.revisions == count.recoveries == 0 for count in counts.values()), 'inconsistent_node_counters')
    outputs = {node.output_contract.slot: node.output_contract.type for node in nodes.values()}
    _refuse(len({ref.slot for ref in state.selections}) == len(state.selections), 'duplicate_selection')
    for ref in state.selections:
        _refuse(outputs.get(ref.slot) == ref.contract and ref.version <= state.counters.visits, 'invalid_current_selection')
    if state.active_visit is not None:
        visit = state.active_visit
        _refuse(visit.node in nodes and visit.ordinal == state.counters.visits
                and counts[visit.node].attempts > 0, 'invalid_active_visit')
    _refuse(len({item.source.visit_id for item in state.receipts}) == len(state.receipts)
            and len({item.source_result for item in state.receipts}) == len(state.receipts), 'duplicate_consumption')
    for item in state.receipts:
        _refuse(item.run_id == state.run_id and item.definition_digest == state.definition_digest, 'foreign_replay_receipt')


class GenericStorageCapacityError(SequentialContractError):
    """Closed no-effect refusal with storage positions, never provider data."""

    cause = 'storage_capacity'

    def __init__(self, event_count: int, post_append_limit: int, required_slots: int) -> None:
        self.event_count = event_count
        self.post_append_limit = post_append_limit
        self.required_slots = required_slots
        self.remaining = dict(storage=4096 - event_count, running=max(0, 4094 - event_count),
                              held=max(0, 4095 - event_count))
        super().__init__(self.cause)


def require_generic_event_capacity(event_count: int, *, resulting_state: str, cancel: bool = False) -> None:
    """Reserve two slots for outstanding Running work and one for Held work.

    A launched cancel uses one slot now and reserves its positive-closure finish.
    Its refusal is defensive for histories already outside the Running invariant.
    """
    ceiling = (4095 if resulting_state == 'running' else 4096) if cancel else {
        'running': 4094, 'held': 4095, 'finished': 4096}[resulting_state]
    if event_count + 1 > ceiling:
        raise GenericStorageCapacityError(event_count, ceiling,
                                          2 if cancel and resulting_state == 'running' else 1)


def reduce_generic_result(compiled: GenericCompiledDefinition, state: GenericRunState,
                          result: GenericAuthenticatedResult, *, storage_event_count: int | None = None) -> GenericDecision:
    """Pure @2 routing over generic bytes and finite node counters; no I/O.

    Invalid content refuses before hashing; a later acceptance writer owns its
    persisted hold. This function neither fabricates native proof nor recovers a
    stopped task. A declared hold/unknown outcome creates no output or successor.
    A persisted @2 acceptance caller supplies storage_event_count independently
    of result counters; the pure S1 ABI may omit it when no storage exists.
    """
    _validate_generic_state(compiled, state)
    envelope = result.envelope
    _refuse(envelope.run_id == state.run_id and envelope.definition_digest == state.definition_digest, 'result_run_mismatch')
    replay = next((item for item in state.receipts if item.source.visit_id == envelope.visit_id
                   or item.source_result == result.result_id), None)
    visit = replay.source if replay is not None else state.active_visit
    _refuse(visit is not None, 'no_active_visit')
    _refuse(envelope.visit_id == visit.visit_id and result.task_id == visit.task_id
            and result.session_id == visit.session_id
            and envelope.input_selection_digest == visit.input_selection_digest, 'result_visit_mismatch')
    node = next(node for node in compiled.definition.nodes if node.id == visit.node)
    if result.content:
        validate_generic_content(node.output_contract.type, result.content)
    digest = hashlib.sha256(json.dumps(dict(task_id=result.task_id, session_id=result.session_id,
        result_id=result.result_id, envelope=envelope.model_dump(mode='json'), settled=result.settled,
        content_digest=hashlib.sha256(result.content).hexdigest()), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode('utf8')).hexdigest()
    if replay is not None:
        _refuse(replay.source.visit_id == envelope.visit_id and replay.source_result == result.result_id
                and replay.result_digest == digest, 'conflicting_replay')
        return replay
    for finding in envelope.findings:
        _refuse(finding.source_visit == visit.visit_id and finding.source_result == result.result_id, 'finding_attribution_mismatch')
    limits = generic_limits(compiled, state.original_limits, state.lower_limits)
    before, after = state.counters, state.counters
    counts = dict(before.nodes)
    nodes = {item.id: item for item in compiled.definition.nodes}
    hold = GenericHoldEdge(action='hold', owner='initiator', remedy='no_automatic_retry')
    edge = dict(node.outcomes).get(envelope.outcome, hold)
    reason = 'declared_edge' if envelope.outcome in dict(node.outcomes) else 'undeclared_outcome'
    selections, invalidated, findings, activation = state.selections, (), state.findings, None
    bounded = (before.visits <= limits.max_visits and before.events < limits.max_events
        and before.recoveries <= limits.max_recoveries
        and all(count.revisions <= dict(limits.node_revisions)[key]
                and count.attempts <= nodes[key].limits.attempts
                and count.recoveries <= nodes[key].limits.recoveries for key, count in counts.items()))
    if not bounded:
        edge, reason = hold, 'limit_exhausted'
    else:
        after = GenericCounters(visits=before.visits, events=before.events + 1,
            recoveries=before.recoveries, nodes=counts)
        if not result.settled:
            edge, reason = hold, 'settlement_unverified'
        elif reason == 'declared_edge':
            advancing = isinstance(edge, (GenericNextEdge, TerminalEdge))
            expected = node.output_contract
            valid = bool(result.content) == bool(envelope.outputs)
            if advancing:
                valid = valid and len(envelope.outputs) == 1
            elif not result.content:
                valid = valid and bool(envelope.findings)
            for ref in envelope.outputs:
                valid = valid and (ref.slot == expected.slot and ref.contract == expected.type
                    and ref.version == visit.ordinal and ref.producer_visit == visit.visit_id
                    and ref.task_id == result.task_id and ref.session_id == result.session_id
                    and ref.result_id == result.result_id and ref.digest == hashlib.sha256(result.content).hexdigest())
            if not valid:
                edge, reason = hold, 'output_evidence_mismatch'
            else:
                _refuse(len(findings) + len(envelope.findings) <= 2048, 'findings_bounds')
                findings += envelope.findings
                if advancing:
                    selections = tuple(ref for ref in selections if ref.slot != expected.slot) + envelope.outputs
                if isinstance(edge, GenericNextEdge):
                    target = counts[edge.node]
                    revision = edge.consume_counter is not None
                    if revision:
                        # A correction retires stale selections even when its
                        # finite replacement allowance has already been spent.
                        forward = {key: {link.node for _, link in item.outcomes
                            if isinstance(link, GenericNextEdge) and link.consume_counter is None}
                            for key, item in nodes.items()}
                        slots = _return_invalidations(nodes, forward, edge.node)
                        invalidated = tuple(ref for ref in selections if ref.slot in slots)
                        selections = tuple(ref for ref in selections if ref.slot not in slots)
                    if storage_event_count is not None and storage_event_count + 1 > 4094:
                        edge, reason = hold, 'exhausted'
                    elif (before.visits >= limits.max_visits or after.events >= limits.max_events
                            or target.attempts >= nodes[edge.node].limits.attempts
                            or (revision and target.revisions >= dict(limits.node_revisions)[edge.node])):
                        edge, reason = hold, 'limit_exhausted'
                    else:
                        available = {ref.slot: ref for ref in selections}
                        selected = []
                        missing = False
                        for item in nodes[edge.node].inputs:
                            if item.source.startswith('current.'):
                                ref = available.get(item.source[8:])
                                missing |= ref is None and item.required
                                if ref is not None and ref not in selected:
                                    selected.append(ref)
                        if missing:
                            edge, reason = hold, 'required_input_unavailable'
                        else:
                            counts[edge.node] = GenericNodeCounters(revisions=target.revisions + int(revision),
                                attempts=target.attempts + 1, recoveries=target.recoveries)
                            after = GenericCounters(visits=before.visits + 1, events=after.events,
                                recoveries=before.recoveries, nodes=counts)
                            activation = GenericActivation(node=edge.node, ordinal=after.visits, inputs=tuple(selected))
                elif isinstance(edge, TerminalEdge):
                    terminal = dict(compiled.definition.terminal_conditions)[edge.condition]
                    if not any(ref.slot == terminal.output for ref in selections):
                        edge, reason = hold, 'terminal_evidence_unavailable'
    if storage_event_count is not None:
        require_generic_event_capacity(storage_event_count,
            resulting_state='running' if activation is not None else 'held' if isinstance(edge, GenericHoldEdge) else 'finished')
    return GenericDecision(run_id=state.run_id, definition_digest=state.definition_digest,
        source=visit, source_result=result.result_id, result_digest=digest, outcome=envelope.outcome,
        before=before, after=after, edge=edge, reason=reason, activation=activation,
        selections=selections, invalidated=invalidated, findings=findings)


def reduce_generic_control(compiled: GenericCompiledDefinition, command: object, *, run: dict,
                           counters: GenericCounters, limits: GenericLimits) -> str:
    """Fresh control rules. Immutable replay is authenticated first by the writer.

    counters.events counts results only; persisted sequence capacity is separate.
    """
    _refuse(run['state'] != 'finished', 'run_finished')
    _refuse(run['progress'] != 'cancelling', 'control_in_progress')
    if run['state'] == 'running':
        _refuse(command.expected_held_event_id is None, 'held_event_conflict')
        _refuse(command.action == 'cancel', 'run_not_held')
    else:
        _refuse(run['state'] == 'held' and command.expected_held_event_id == run['held_event_id']
                and command.expected_held_event_id is not None, 'held_event_conflict')
    _refuse(command.expected_cas == run['cas'], 'control_cas_conflict')
    if command.action == 'cancel':
        return 'cancel'
    node = next(item for item in compiled.definition.nodes if item.id == run['held_node_id'])
    count = dict(counters.nodes)[node.id]
    ceiling = dict(limits.node_revisions)[node.id]
    _refuse(run.get('hold_cause') != 'revision_limit_exhausted'
            and counters.events < limits.max_events and counters.visits < limits.max_visits
            and counters.recoveries < limits.max_recoveries and count.attempts < node.limits.attempts
            and count.recoveries < node.limits.recoveries
            and (count.revisions < ceiling or ceiling == 0 and count.revisions == 0), 'exhausted')
    require_generic_event_capacity(run['event_count'], resulting_state='running')
    return 'replace'
