"""Read-only plural GenericStartCommand composition for native callback admission."""
from __future__ import annotations

import json
from typing import Annotated, Literal

import httpx
from pydantic import Field, field_serializer, model_validator

from runtime.workflows.sequential_assignments import validate_assignment_keys
from runtime.workflows.sequential_contracts import (
    BindingClaim, Closed, GenericLimits, GenericStepOverride, InputValue, SettingsPin, Text, Token,
)
from runtime.workflows.sequential_definition import generic_limits, parse_generic_start, validate_generic_start
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _generic_limits_value
from runtime.workflows.sequential_release import generic_start_catalog
from runtime.workflows.sequential_start import _batch_decision
from runtime.workflows.sequential_start_discovery import StartContextV2


class ComposeMemberV2(Closed):
    definition: Annotated[str, Field(min_length=1, max_length=160)]
    settings: SettingsPin
    operation_key: Token
    request: Text
    inputs: Annotated[list[InputValue], Field(max_length=32)]
    role_overrides: Annotated[list[BindingClaim], Field(max_length=16)]
    step_overrides: Annotated[list[GenericStepOverride], Field(max_length=32)]
    lower_limits: GenericLimits

    @field_serializer('lower_limits')
    def limits_value(self, value: GenericLimits) -> dict:
        return _generic_limits_value(value)


class ComposeInputV2(Closed):
    format: Literal['workflow-sequential-start-compose@2']
    workflows: Annotated[list[ComposeMemberV2], Field(min_length=1, max_length=8)]
    consume_notifications: Annotated[list[Token], Field(max_length=32)] = Field(default_factory=list)

    @model_validator(mode='after')
    def distinct_keys(self) -> ComposeInputV2:
        if (len({item.operation_key for item in self.workflows}) != len(self.workflows)
                or len(set(self.consume_notifications)) != len(self.consume_notifications)):
            raise ValueError('duplicate batch key')
        return self


def compose_v2(client: httpx.Client, org: str, sid: str, command: ComposeInputV2) -> bytes:
    # One bounded GET for the catalog, not a renewed per-member discovery ledger.
    with client.stream('GET', f'/api/v1/orgs/{org}/workflows/sequential/start-context/v2',
            params=[('session_id', sid)]) as response:
        raw = bytearray()
        for chunk in response.iter_bytes():
            if len(raw) + len(chunk) > 262144:
                raise ValueError('sequential_start_context_too_large')
            raw.extend(chunk)
        if response.status_code != 200:
            try:
                code = json.loads(raw)['detail']['code']
            except (ValueError, KeyError, TypeError):
                code = 'unavailable'
            if type(code) is not str or len(code) > 128 or not code.replace('_', '').isascii() or not code.replace('_', '').isalpha():
                code = 'unavailable'
            raise ValueError(f'sequential_start_context_refused:{response.status_code}:{code}')
        context = StartContextV2.model_validate_json(bytes(raw), strict=True)
    selected = {item.selector: item for item in context.definitions}
    if len(selected) != len(context.definitions):
        raise ValueError('sequential_definition_conflict')
    if not set(command.consume_notifications) <= set(context.delivered_notifications):
        raise ValueError('sequential_notification_not_delivered')
    workflows = []
    for member in command.workflows:
        value = selected.get(member.definition)
        if value is None:
            raise ValueError('sequential_definition_unavailable')
        if value.settings is None or (value.settings.generation, value.settings.snapshot_digest) != (
                member.settings.generation, member.settings.snapshot_digest):
            raise ValueError('sequential_settings_conflict:409')
        if value.unavailable_cause is not None or value.admission != 'Allowed' or value.limits is None:
            raise ValueError(value.unavailable_cause or 'sequential_start_unavailable')
        compiled, = generic_start_catalog(member.definition)
        if compiled.pin != value.pin:
            raise ValueError('sequential_definition_conflict')
        # Preserve omission/value/null for target executor options, including
        # within each step. Never materialize omitted options as explicit null.
        start = parse_generic_start(_bytes(dict(format=context.start_format,
            **member.model_dump(mode='json', exclude={'definition'}, exclude_unset=True),
            definition=value.pin.model_dump(mode='json'))))
        validate_assignment_keys(compiled, start)
        validate_generic_start(start, compiled, original_limits=value.limits)
        if (any(getattr(start.lower_limits, key) > getattr(value.limits, key) for key in ('max_visits', 'max_events', 'max_recoveries'))
                or generic_limits(compiled, start.lower_limits) != generic_limits(compiled, value.limits, start.lower_limits)):
            raise ValueError('sequential_lower_limits_exceed_settings')
        principals = {role: requirement.default_agent for role, requirement in compiled.definition.roles}
        principals.update({item.role: item.principal for item in value.defaults})
        principals.update({item.role: item.principal for item in start.role_overrides})
        if any(principals[role] is None or any(principals[role] == principals[other]
                for other in requirement.independent_of) for role, requirement in compiled.definition.roles):
            raise ValueError('sequential_assignment_independence')
        workflows.append(dict(start.model_dump(mode='json', exclude_unset=True),
                              lower_limits=_generic_limits_value(start.lower_limits)))
    decision = dict(action='workflow_start', workflows=workflows)
    if 'consume_notifications' in command.model_fields_set:
        decision['consume_notifications'] = command.consume_notifications
    raw = _bytes(decision)
    _batch_decision(raw, _ReadBudget())
    return raw
