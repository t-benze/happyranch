"""Closed K3a commands and agent-read pins; no repository or execution grant."""
from __future__ import annotations

import json
from typing import Annotated, Literal
from pydantic import Field, StrictInt, field_validator, model_validator

from runtime.workflows._coding_role_records import AgentBinding, Closed, MappingPin, Release, Roles
from runtime.workflows.artifacts import Token
from runtime.workflows.coding_saved_records import (
    _check, _constant, _json, _model, _oid, _operation, _pairs, _paths, _repository,
)

START_LIMIT = 2097152


class CodingWork(Closed):
    brief: str
    repository_id: str
    object_format: Literal['sha1', 'sha256']
    base: str
    paths: list[str] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def bounded(self) -> CodingWork:
        _check(0 < len(self.brief.encode('utf8')) <= 16384 and '\x00' not in self.brief)
        _repository(self.repository_id)
        _oid(self.base, self.object_format)
        _paths(self.paths)
        return self


class CodingStart(Closed):
    format: Literal['workflow-coding-start@1']
    operation_key: Token
    release: Release
    mapping: MappingPin
    work: CodingWork
    implementer_override: AgentBinding | None

    @model_validator(mode='after')
    def public_key(self) -> CodingStart:
        _operation(self.operation_key)
        return self


class CodingStartDecision(Closed):
    action: Literal['workflow_start']
    workflow: CodingStart


class CodingReadiness(Closed):
    enabled: Literal[False]
    reason: Literal['coding_downstream_unavailable']

    @field_validator('enabled', mode='before')
    @classmethod
    def disabled_only(cls, value: object) -> bool:
        _check(type(value) is bool and value is False)
        return value


class QueuedAttempt(Closed):
    id: Token
    task_id: Token
    stage: Literal['development']
    state: Literal['queued', 'claimed', 'running', 'uncertain', 'blocked', 'failed', 'completed']
    principal: Token


class QueuedEvent(Closed):
    id: Token
    kind: Literal['run_started', 'session_bound', 'launch_reserved', 'launch_observed', 'callback_recorded', 'terminal_recorded', 'cleanup_settled', 'held', 'host_reconciled', 'manager_control_queued']
    event_seq: Annotated[StrictInt, Field(ge=1, le=4096)]


class CodingRunReceipt(Closed):
    format: Literal['workflow-coding-run@1']
    instance_id: Token
    release: Release
    mapping: MappingPin
    effective_roles: Roles
    cas_version: Annotated[StrictInt, Field(ge=1, le=4096)]
    current_revision: Annotated[StrictInt, Field(ge=0, le=0)]
    target_revision: Annotated[StrictInt, Field(ge=1, le=1)]
    state: Literal['development', 'held']
    current_attempt: QueuedAttempt
    history: list[QueuedEvent] = Field(min_length=1, max_length=4096)
    closeout: None
    readiness: CodingReadiness


def parse_start(raw: bytes) -> dict:
    return _model(CodingStart, _json(raw, START_LIMIT)).model_dump()


def parse_decision(raw: bytes) -> dict:
    # The native callback owner preserves json.dumps spacing and key order.
    # Retain those bytes separately; only the nested command is canonicalized.
    _check(type(raw) is bytes and 0 < len(raw) <= START_LIMIT)
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    return _model(CodingStartDecision, value).model_dump()


def effective_mapping(mapping: dict, command: dict) -> dict:
    """Select initial work without inventing a developer head or subject."""
    import copy
    selected = copy.deepcopy(mapping)
    override = command['implementer_override']
    if override is not None:
        _check(override in mapping['eligible_replacements']['implementer'])
        selected['roles']['implementer'] = override
    selected['eligible_replacements'] = {key: [] for key in selected['roles']}
    references = [r for r in mapping['repository_references']
        if (r['repository_id'], r['object_format']) ==
           (command['work']['repository_id'], command['work']['object_format'])]
    _check(len(references) == 1 and command['release'] == mapping['release'])
    # RoleMapping performs distinct-principal and repository-reference checks.
    from runtime.workflows._coding_role_records import RoleMapping
    RoleMapping.model_validate(selected)
    return selected


def parse_native_decision(raw: bytes) -> dict:
    # Existing native callback route owns this reserved carrier. Preserve the
    # entire original result and fingerprint; it is never caller authority.
    _check(type(raw) is bytes and 0 < len(raw) <= 1048576)
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    _check(type(value) is dict)
    value.pop('_manager_self_evaluation', None)
    return _model(CodingStartDecision, value).model_dump()


def start_decision_schema() -> dict:
    """Closed nested callback schema without dangling OpenAPI component refs."""
    schema = CodingStartDecision.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value: object) -> object:
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return inline(schema)
