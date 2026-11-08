"""Sequential v1 syntax only; these records confer no runtime authority.

Maps compile to immutable pairs and arrays to tuples. The definition's original
canonical bytes, rather than a serialization of this compiled view, own its pin.
No historical Coding/document records or producers import this family.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt,
    StringConstraints, model_validator,
)

DEFINITION_LIMIT = 1048576
RESULT_LIMIT = 65536
COMPILER_ABI = "workflow-sequential-compiler@1"
VALIDATOR_ABI = "workflow-sequential-validator@1"
INTERPRETER_ABI = "workflow-sequential-interpreter@1"
CAPABILITY_ABI = "workflow-sequential-capabilities@1"
SETTLEMENT_CHECK = "native-success-and-positive-outer-settlement@1"

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Token = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[str, StringConstraints(pattern=r"^release/[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9][a-z0-9_-]{0,63}$")]
Positive = Annotated[StrictInt, Field(ge=1, le=9223372036854775807)]
Epoch = Annotated[StrictInt, Field(ge=1, le=3)]
OutcomeName = Literal["produced", "APPROVE", "REQUEST_CHANGES", "BLOCK", "PASS", "REVISE", "merged", "held", "uncertain"]
OutputType = Literal["immutable-document@1", "code-change@1", "document-review@1", "independent-code-review@1", "code-qa@1", "guarded-repository-merge@1"]
Check = Literal["immutable-document@1", "repository-pre-edit@1", "code-change@1", "independent-document-review@1", "independent-code-review@1", "code-qa@1", "guarded-repository-merge@1"]
TerminalCheck = Literal["immutable-document@1", "code-change@1", "independent-document-review@1", "independent-code-review@1", "code-qa@1", "guarded-repository-merge@1", "native-success-and-positive-outer-settlement@1"]


def _array(value: object) -> tuple:
    if type(value) not in (list, tuple):
        raise ValueError("expected array")
    return tuple(value)


def _pairs(value: object) -> tuple:
    if type(value) is not dict:
        raise ValueError("expected named object")
    return tuple(value.items())


def _text(value: str, maximum: int) -> str:
    if not value.strip() or "\x00" in value or len(value.encode("utf8")) > maximum:
        raise ValueError("text bounds")
    return value


Text = Annotated[str, StringConstraints(min_length=1, max_length=16384)]
Names = Annotated[tuple[Name, ...], BeforeValidator(_array), Field(max_length=16)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Limits(Closed):
    content_returns: Annotated[StrictInt, Field(ge=0, le=2)]
    max_content_epochs: Epoch
    max_visits: Annotated[StrictInt, Field(ge=1, le=64)]
    max_events: Annotated[StrictInt, Field(ge=1, le=4096)]


def effective_limits(definition: Limits, original: Limits, requested: Limits) -> Limits:
    """Intersect original allowances; a new key/visit cannot replenish them.

    Epoch and return bounds are independent ceilings, so lower either safely
    constrains the other. The pure reducer owns counter consumption/exhaustion.
    """
    epochs = min(x.max_content_epochs for x in (definition, original, requested))
    returns = min(epochs - 1, *(x.content_returns for x in (definition, original, requested)))
    return Limits(content_returns=returns, max_content_epochs=min(epochs, returns + 1),
                  max_visits=min(x.max_visits for x in (definition, original, requested)),
                  max_events=min(x.max_events for x in (definition, original, requested)))


class RequiredInput(Closed):
    name: Name
    contract: Literal["bounded-text@1"]
    required: StrictBool


class RoleRequirement(Closed):
    kind: Literal["agent"]
    executable: StrictBool
    eligibility: Literal["worker", "registered-team-manager"]
    independent_of: Names

    @model_validator(mode="after")
    def executable_agent(self) -> Self:
        if self.executable is not True or len(set(self.independent_of)) != len(self.independent_of):
            raise ValueError("executable agent and distinct independence requirements required")
        return self


class NodeInput(Closed):
    name: Name
    source: Annotated[str, StringConstraints(pattern=r"^(run\.[a-z][a-z0-9_]{0,63}|current\.[a-z][a-z0-9_]{0,63}|returned_findings)$")]
    required: StrictBool = True


class OutputContract(Closed):
    slot: Name
    type: OutputType


class NextEdge(Closed):
    action: Literal["next"]
    node: Name
    consume_counter: Literal["content_returns"] | None = None
    invalidate_dependents: StrictBool = False

    @model_validator(mode="after")
    def return_is_explicit(self) -> Self:
        if (self.consume_counter is not None) != self.invalidate_dependents:
            raise ValueError("return requires counter consumption and evidence invalidation")
        return self


class HoldEdge(Closed):
    action: Literal["hold"]
    owner: Literal["accountable_manager"]
    remedy: Literal["no_automatic_retry"]


class FailEdge(Closed):
    action: Literal["fail"]
    code: Name


class TerminalEdge(Closed):
    action: Literal["terminal"]
    condition: Name


Edge = Annotated[NextEdge | HoldEdge | FailEdge | TerminalEdge, Field(discriminator="action")]


class Node(Closed):
    id: Name
    role: Name
    responsibilities: Annotated[tuple[Text, ...], BeforeValidator(_array), Field(min_length=1, max_length=16)]
    inputs: Annotated[tuple[NodeInput, ...], BeforeValidator(_array), Field(max_length=32)]
    output_contract: OutputContract
    completion_checks: Annotated[tuple[Check, ...], BeforeValidator(_array), Field(min_length=1, max_length=8)]
    outcomes: Annotated[tuple[tuple[OutcomeName, Edge], ...], BeforeValidator(_pairs), Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def local_bounds(self) -> Self:
        for text in self.responsibilities:
            _text(text, 16384)
        if len({x.name for x in self.inputs}) != len(self.inputs):
            raise ValueError("duplicate node input")
        if len(set(self.completion_checks)) != len(self.completion_checks):
            raise ValueError("duplicate completion check")
        return self


class TerminalCondition(Closed):
    output: Name
    requires: Annotated[tuple[TerminalCheck, ...], BeforeValidator(_array), Field(min_length=2, max_length=8)]


class Definition(Closed):
    """Deeply immutable compiled view of the original, closed JSON definition."""

    format: Literal["workflow-sequential-definition@1"]
    identity: Identity
    version: Positive
    description: Text
    required_inputs: Annotated[tuple[RequiredInput, ...], BeforeValidator(_array), Field(min_length=1, max_length=32)]
    roles: Annotated[tuple[tuple[Name, RoleRequirement], ...], BeforeValidator(_pairs), Field(min_length=1, max_length=16)]
    entry_node: Name
    nodes: Annotated[tuple[Node, ...], BeforeValidator(_array), Field(min_length=1, max_length=32)]
    limits: Limits
    terminal_conditions: Annotated[tuple[tuple[Name, TerminalCondition], ...], BeforeValidator(_pairs), Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def bounded_description(self) -> Self:
        _text(self.description, 16384)
        return self


class DefinitionPin(Closed):
    identity: Identity
    version: Positive
    definition_digest: Digest
    source_digest: Digest
    compiler: Literal["workflow-sequential-compiler@1"]
    validator: Literal["workflow-sequential-validator@1"]
    interpreter: Literal["workflow-sequential-interpreter@1"]
    capabilities: Literal["workflow-sequential-capabilities@1"]


class SettingsPin(Closed):
    generation: Positive
    snapshot_digest: Digest


class BindingClaim(Closed):
    """Requested permanent identity only; admission must prove actual authority."""

    role: Name
    principal: Token


class InputValue(Closed):
    name: Name
    contract: Literal["bounded-text@1"]
    value: Text

    @model_validator(mode="after")
    def bounded_value(self) -> Self:
        _text(self.value, 16384)
        return self


class StartCommand(Closed):
    format: Literal["workflow-sequential-start@1"]
    operation_key: Token
    definition: DefinitionPin
    settings: SettingsPin
    request: Text
    inputs: Annotated[tuple[InputValue, ...], BeforeValidator(_array), Field(max_length=32)]
    bindings: Annotated[tuple[BindingClaim, ...], BeforeValidator(_array), Field(min_length=1, max_length=16)]
    lower_limits: Limits

    @model_validator(mode="after")
    def unique_claims(self) -> Self:
        _text(self.request, 16384)
        if (len({x.name for x in self.inputs}) != len(self.inputs)
                or len({x.role for x in self.bindings}) != len(self.bindings)
                or any(x.name == "request" for x in self.inputs)):
            raise ValueError("duplicate inputs/bindings or reserved request input")
        return self


class OutputRef(Closed):
    """Reference syntax, never a claim of native provenance or access."""

    slot: Name
    contract: OutputType
    epoch: Epoch
    producer_visit: Token
    task_id: Token
    session_id: Token
    result_id: Positive
    digest: Digest


class Finding(Closed):
    source_visit: Token
    source_result: Positive
    text: Text

    @model_validator(mode="after")
    def bounded_finding(self) -> Self:
        _text(self.text, 16384)
        return self


class CapabilityRef(Closed):
    check: Check
    evidence_digest: Digest


class ResultEnvelope(Closed):
    format: Literal["workflow-sequential-result@1"]
    run_id: Token
    visit_id: Token
    definition_digest: Digest
    input_selection_digest: Digest
    outcome: Token
    outputs: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_array), Field(max_length=32)]
    capability_receipts: Annotated[tuple[CapabilityRef, ...], BeforeValidator(_array), Field(max_length=8)]

    @model_validator(mode="after")
    def discriminated_payload(self) -> Self:
        if len({x.slot for x in self.outputs}) != len(self.outputs):
            raise ValueError("duplicate output selection")
        if len({x.check for x in self.capability_receipts}) != len(self.capability_receipts):
            raise ValueError("duplicate capability selection")
        success = self.outcome in ("produced", "APPROVE", "PASS", "merged")
        if success != bool(self.outputs) or (not success and not self.findings):
            raise ValueError("outcome requires typed outputs or attributed refusal/return findings")
        return self


class Counters(Closed):
    epoch: Epoch
    content_returns: Annotated[StrictInt, Field(ge=0, le=2)]
    visits: Annotated[StrictInt, Field(ge=1, le=64)]
    events: Annotated[StrictInt, Field(ge=0, le=4096)]

    @model_validator(mode="after")
    def epoch_tracks_returns(self) -> Self:
        if self.epoch != self.content_returns + 1:
            raise ValueError("epoch/return mismatch")
        return self


class SuccessorRef(Closed):
    node: Name
    visit_id: Token
    task_id: Token


class TransitionRecord(Closed):
    """Persistable transition syntax; compare it with the pure reducer decision.

    These identifiers are allocated by a future owning writer, never by a
    definition. Parsing does not prove CAS, settlement, uniqueness or history.
    """

    format: Literal["workflow-sequential-transition@1"]
    run_id: Token
    definition_digest: Digest
    source_visit: Token
    source_result: Positive
    result_digest: Digest
    output_digest: Digest | None
    node: Name
    outcome: OutcomeName
    prior_cas: Annotated[StrictInt, Field(ge=0, le=9223372036854775807)]
    prior_event_digest: Digest
    before: Counters
    after: Counters
    invalidated: Annotated[tuple[OutputRef, ...], BeforeValidator(_array), Field(max_length=32)]
    edge: Edge
    successor: SuccessorRef | None

    @model_validator(mode="after")
    def local_transition_shape(self) -> Self:
        if isinstance(self.edge, NextEdge):
            if self.successor is None or self.successor.node != self.edge.node:
                raise ValueError("next edge needs its selected successor")
        elif self.successor is not None:
            raise ValueError("non-next edge cannot allocate a successor")
        return self
