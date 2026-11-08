"""Pure bounded sequential definition compiler/validator, with no catalog I/O.

This module knows registered contract identifiers, never preset/node identities.
Static admissibility is not capability acceptance, execution readiness or a grant.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeVar

from pydantic import ValidationError

from runtime.workflows.sequential_contracts import (
    CAPABILITY_ABI, COMPILER_ABI, INTERPRETER_ABI, VALIDATOR_ABI,
    Closed, DEFINITION_LIMIT, RESULT_LIMIT, Definition, DefinitionPin, Limits,
    NextEdge, Node, ResultEnvelope, SETTLEMENT_CHECK, StartCommand, TerminalEdge,
    TransitionRecord, effective_limits,
)


class SequentialContractError(ValueError):
    """Bounded categorical refusal; raw inputs are not included in diagnostics."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SequentialContractError(code)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _no_constant(value: str) -> object:
    raise SequentialContractError("nonfinite_json")


def _no_float(value: str) -> object:
    # This ABI contains no floating point fields.
    raise SequentialContractError("noninteger_number")


def _integer(value: str) -> int:
    _require(len(value.lstrip("-")) <= 19, "integer_bounds")
    return int(value)


def _json(raw: bytes, maximum: int) -> dict:
    _require(type(raw) is bytes and 0 < len(raw) <= maximum, "encoded_bounds")
    # A finite depth pre-scan bounds parser/validator recursion without decoding
    # a deeply nested object first. Strings/escaped quotes do not count as JSON.
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            _require(depth <= 24, "json_depth")
        elif byte in (93, 125):
            depth -= 1
            _require(depth >= 0, "invalid_json")
    try:
        value = json.loads(raw.decode("utf8"), object_pairs_hook=_unique_object,
                           parse_int=_integer, parse_float=_no_float, parse_constant=_no_constant)
        _require(type(value) is dict, "expected_object")
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf8")
        _require(raw == canonical, "noncanonical_bytes")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SequentialContractError):
            raise
        raise SequentialContractError("invalid_json") from exc
    return value


Record = TypeVar("Record", bound=Closed)


def _record(model: type[Record], value: dict) -> Record:
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise SequentialContractError("closed_record") from exc


# Closed trusted registry: data selects checks but cannot define implementations.
# expected success outcome, witness check, permitted outcomes, required checks.
_OUTPUTS = MappingProxyType({
    "immutable-document@1": ("produced", "immutable-document@1", frozenset(("produced", "BLOCK")), frozenset(("immutable-document@1",))),
    "code-change@1": ("produced", "code-change@1", frozenset(("produced", "BLOCK")), frozenset(("repository-pre-edit@1", "code-change@1"))),
    "document-review@1": ("APPROVE", "independent-document-review@1", frozenset(("APPROVE", "REQUEST_CHANGES", "BLOCK")), frozenset(("independent-document-review@1",))),
    "independent-code-review@1": ("APPROVE", "independent-code-review@1", frozenset(("APPROVE", "REQUEST_CHANGES", "BLOCK")), frozenset(("independent-code-review@1",))),
    "code-qa@1": ("PASS", "code-qa@1", frozenset(("PASS", "REVISE", "BLOCK")), frozenset(("code-qa@1",))),
    "guarded-repository-merge@1": ("merged", "guarded-repository-merge@1", frozenset(("merged", "held", "uncertain")), frozenset(("guarded-repository-merge@1",))),
})


def _acyclic(nodes: dict[str, Node], forward: dict[str, set[str]]) -> None:
    incoming = dict.fromkeys(nodes, 0)
    for targets in forward.values():
        for target in targets:
            incoming[target] += 1
    ready = [key for key, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        source = ready.pop()
        visited += 1
        for target in forward[source]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    _require(visited == len(nodes), "cycle_without_consumed_counter")


def _return_invalidations(nodes: dict[str, Node], forward: dict[str, set[str]], target: str) -> set[str]:
    """Retire the revised producer and every control/data-dependent selection."""
    affected = {target}
    for _ in range(len(nodes)):
        slots = {nodes[key].output_contract.slot for key in affected}
        updated = affected | {child for key in affected for child in forward[key]}
        updated |= {key for key, node in nodes.items()
                    if any(item.source.startswith("current.") and item.source[8:] in slots
                           for item in node.inputs)}
        if updated == affected:
            return slots
        affected = updated
    return {nodes[key].output_contract.slot for key in affected}


def _available_inputs(definition: Definition, nodes: dict[str, Node], slots: set[str],
                      forward: dict[str, set[str]]) -> dict[str, set[str]]:
    """Must analysis: intersection over ALL predecessors, including return kills.

    Start at the top of the finite slot lattice; each iteration can only remove
    slots. At most nodes*slots removals, plus one final observation, are possible.
    Returns retire the target producer and its control/data dependents. Unaffected
    prerequisites remain available, so a bounded return need not target entry.
    """
    kills = {edge.node: _return_invalidations(nodes, forward, edge.node)
             for node in nodes.values() for _, edge in node.outcomes
             if isinstance(edge, NextEdge) and edge.consume_counter}
    available = {key: set(slots) for key in nodes}
    available[definition.entry_node] = set()
    for _ in range(len(nodes) * len(slots) + 2):
        updated = {key: set(slots) for key in nodes}
        updated[definition.entry_node] = set()
        for source, node in nodes.items():
            for _, edge in node.outcomes:
                if isinstance(edge, NextEdge):
                    incoming = (available[source] - kills[edge.node] if edge.consume_counter else
                                available[source] | {node.output_contract.slot})
                    updated[edge.node] &= incoming
        if updated == available:
            return available
        available = updated
    raise SequentialContractError("input_analysis_bounds")


def _graph(definition: Definition) -> None:
    nodes = {x.id: x for x in definition.nodes}
    roles = dict(definition.roles)
    inputs = {x.name: x for x in definition.required_inputs}
    terminals = dict(definition.terminal_conditions)
    outputs = {x.output_contract.slot: x for x in definition.nodes}
    _require(len(nodes) == len(definition.nodes), "duplicate_node")
    _require(len(inputs) == len(definition.required_inputs), "duplicate_run_input")
    _require(len(outputs) == len(nodes), "duplicate_output_slot")
    _require(definition.entry_node in nodes, "missing_entry")
    _require("request" in inputs and inputs["request"].required, "required_request")
    for name, requirement in roles.items():
        _require(name not in requirement.independent_of
                 and set(requirement.independent_of) <= roles.keys(), "invalid_independence")
    forward: dict[str, set[str]] = {key: set() for key in nodes}
    successors: dict[str, set[str]] = {key: set() for key in nodes}
    used_terminals: set[str] = set()
    for node in nodes.values():
        _require(node.role in roles, "missing_role")
        success, _, permitted, required = _OUTPUTS[node.output_contract.type]
        _require(success in dict(node.outcomes), "missing_success_outcome")
        _require(set(dict(node.outcomes)) <= permitted, "unsupported_outcome")
        _require(set(node.completion_checks) == required, "incompatible_completion_checks")
        for item in node.inputs:
            if item.source.startswith("run."):
                name = item.source[4:]
                _require(name in inputs, "missing_run_input")
                _require(not item.required or inputs[name].required, "optional_run_input_required")
            elif item.source.startswith("current."):
                _require(item.source[8:] in outputs, "missing_output_input")
            else:
                _require(not item.required, "initial_findings_unavailable")
        for outcome, edge in node.outcomes:
            if isinstance(edge, NextEdge):
                _require(edge.node in nodes, "missing_successor")
                returning = edge.consume_counter is not None
                _require(returning == (outcome in ("REQUEST_CHANGES", "REVISE")), "invalid_return_outcome")
                _require(returning or outcome == success, "refusal_cannot_advance")
                successors[node.id].add(edge.node)
                if not returning:
                    forward[node.id].add(edge.node)
            elif isinstance(edge, TerminalEdge):
                _require(outcome == success and edge.condition in terminals, "invalid_terminal_edge")
                used_terminals.add(edge.condition)
    _acyclic(nodes, forward)
    reachable, pending = set(), [definition.entry_node]
    while pending:
        source = pending.pop()
        if source not in reachable:
            reachable.add(source)
            pending.extend(successors[source] - reachable)
    _require(reachable == nodes.keys(), "unreachable_node")
    _require(used_terminals == terminals.keys(), "unused_terminal_condition")
    available = _available_inputs(definition, nodes, set(outputs), forward)
    for node in nodes.values():
        for item in node.inputs:
            if item.required and item.source.startswith("current."):
                _require(item.source[8:] in available[node.id], "input_without_dominating_producer")
        for _, edge in node.outcomes:
            if not isinstance(edge, TerminalEdge):
                continue
            terminal = terminals[edge.condition]
            _require(terminal.output in available[node.id] | {node.output_contract.slot}, "terminal_output_unavailable")
            producer = outputs[terminal.output]
            witness = _OUTPUTS[producer.output_contract.type][1]
            _require(set(terminal.requires) == {witness, SETTLEMENT_CHECK}
                     and len(terminal.requires) == 2, "terminal_without_typed_settlement_witness")
    # Bounded reachability also ensures at least one success path fits the
    # definition's own visit/epoch/return ceilings. Runtime lower limits may hold.
    pending_states = [(definition.entry_node, 0, 1)]
    seen: set[tuple[str, int]] = set()
    terminal_reachable = False
    while pending_states:
        source, returns, visits = pending_states.pop(0)
        if (source, returns) in seen:
            continue
        seen.add((source, returns))
        for _, edge in nodes[source].outcomes:
            if isinstance(edge, TerminalEdge):
                terminal_reachable = True
            elif isinstance(edge, NextEdge):
                count = returns + int(edge.consume_counter is not None)
                if (count <= definition.limits.content_returns
                        and count < definition.limits.max_content_epochs
                        and visits < definition.limits.max_visits):
                    pending_states.append((edge.node, count, visits + 1))
    _require(terminal_reachable, "no_terminal_path_within_limits")


@dataclass(frozen=True)
class CompiledDefinition:
    """Result of compile_definition; callers must not construct trust from a pin."""

    raw: bytes
    pin: DefinitionPin
    definition: Definition


def compile_definition(raw: bytes, *, source_digest: str) -> CompiledDefinition:
    """Validate exact saved bytes and compile without registration or any I/O."""
    definition = _record(Definition, _json(raw, DEFINITION_LIMIT))
    _graph(definition)
    pin = _record(DefinitionPin, dict(identity=definition.identity, version=definition.version,
                                    definition_digest=hashlib.sha256(raw).hexdigest(),
                                    source_digest=source_digest, compiler=COMPILER_ABI,
                                    validator=VALIDATOR_ABI, interpreter=INTERPRETER_ABI,
                                    capabilities=CAPABILITY_ABI))
    return CompiledDefinition(raw=raw, pin=pin, definition=definition)


def parse_start(raw: bytes) -> StartCommand:
    return _record(StartCommand, _json(raw, DEFINITION_LIMIT))


def validate_start(command: StartCommand, compiled: CompiledDefinition, *, original_limits: Limits) -> Limits:
    """Pure completeness/independence checks, NOT admission or principal proof."""
    _require(command.definition == compiled.pin, "definition_pin_mismatch")
    definition = compiled.definition
    bindings = {x.role: x.principal for x in command.bindings}
    _require(bindings.keys() == dict(definition.roles).keys(), "incomplete_bindings")
    for role, requirement in definition.roles:
        _require(all(bindings[role] != bindings[other] for other in requirement.independent_of),
                 "nonindependent_bindings")
    supplied = {x.name: x for x in command.inputs}
    declared = {x.name: x for x in definition.required_inputs if x.name != "request"}
    _require(supplied.keys() <= declared.keys(), "unknown_start_input")
    for name, requirement in declared.items():
        _require(not requirement.required or name in supplied, "missing_start_input")
        if name in supplied:
            _require(supplied[name].contract == requirement.contract, "input_contract_mismatch")
    return effective_limits(definition.limits, original_limits, command.lower_limits)


def parse_result(raw: bytes) -> ResultEnvelope:
    """Closed bounded syntax only; U1b/trusted adapters must check selected evidence."""
    return _record(ResultEnvelope, _json(raw, RESULT_LIMIT))


def parse_transition(raw: bytes) -> TransitionRecord:
    """Closed bounded syntax only; no historical acceptance or CAS claim."""
    return _record(TransitionRecord, _json(raw, DEFINITION_LIMIT))
