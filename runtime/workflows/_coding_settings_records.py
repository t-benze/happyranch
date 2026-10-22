"""Closed human settings grammars; parsing conveys no publication authority."""
from __future__ import annotations

import base64
import re

from typing import Annotated, Literal

from pydantic import Field, model_validator

from runtime.workflows._coding_role_records import (
    Closed, MappingPin, Release, RoleMapping,
)
from runtime.workflows.artifacts import SHA256, Token
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_saved_records import (
    VALUE_LIMIT, SavedCodingRecord, _Ancestry, _canonical, _capture, _check,
    _closed, _command_bytes, _digest, _hash, _json, _model, _org, _timestamp,
)

COMMAND_LIMIT = 65536
HUMAN_RECORD = 'workflow-coding-agent-read-role-record@2'
Generation = Annotated[int, Field(ge=0, le=9223372036854775807)]
Positive = Annotated[int, Field(ge=1, le=9223372036854775807)]
Admission = Literal['disabled', 'explicit']


class SettingsCommand(Closed):
    format: Literal['workflow-coding-settings-command@1']
    org_slug: str
    operation_key: Token
    expected_generation: Generation
    expected_mapping_generation: Generation
    admission: Admission
    mapping: RoleMapping

    @model_validator(mode='after')
    def addressed(self) -> SettingsCommand:
        _org(self.org_slug)
        _check(not self.operation_key.startswith('coding-internal:'))
        _check(self.mapping.authority.namespace == 'org/' + self.org_slug)
        return self


class SettingsSnapshot(Closed):
    format: Literal['workflow-coding-settings-snapshot@1']
    org_slug: str
    generation: Positive
    admission: Admission
    release: Release
    mapping: MappingPin
    operation_key: Token
    request_digest: SHA256
    previous_snapshot_digest: SHA256 | None
    published_at: str
    published_by: Literal['human:founder']

    @model_validator(mode='after')
    def addressed(self) -> SettingsSnapshot:
        _org(self.org_slug)
        _timestamp(self.published_at)
        _check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', self.published_at) is not None)
        _check(not self.operation_key.startswith('coding-internal:'))
        return self


class EffectiveSettings(Closed):
    generation: Generation
    admission: Admission
    release: Release | None
    mapping: MappingPin | None
    snapshot_digest: SHA256 | None
    mapping_generation: Generation


class SettingsReceipt(Closed):
    format: Literal['workflow-coding-settings-receipt@1']
    operation_key: Token
    request_digest: SHA256
    snapshot: SettingsSnapshot
    snapshot_digest: SHA256
    publication_audit_id: Positive
    replayed: bool


class SettingsView(Closed):
    format: Literal['workflow-coding-settings-view@1']
    org_slug: str
    current: EffectiveSettings
    configuration: RoleMapping | None
    operation: SettingsReceipt | None


class SettingsSave(Closed):
    format: Literal['workflow-coding-settings-save@1']
    org_slug: str
    operation: SettingsReceipt
    current: EffectiveSettings
    configuration: RoleMapping


def parse_command(raw: bytes, org: str) -> dict:
    _org(org)
    value = _model(SettingsCommand, _json(raw, COMMAND_LIMIT)).model_dump()
    _check(value['org_slug'] == org)
    return value


def account(value: object, budget: _Budget) -> object:
    """Charge canonical copies and decoded traversal to the enclosing allowance."""
    # Bound traversal and reserve a conservative JSON/UTF8 copy allowance
    # BEFORE serialization, including escaped strings and container punctuation.
    pending, estimate = [value], 0
    while pending:
        budget.step()
        item = pending.pop()
        if type(item) is dict:
            estimate += 2 + max(0, len(item) - 1) + len(item)
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            estimate += 2 + max(0, len(item) - 1)
            pending.extend(item)
        elif type(item) is str:
            estimate += 2 + 6 * len(item)
        elif type(item) is int:
            estimate += max(1, (item.bit_length() * 30103) // 100000 + 1) + (item < 0)
        elif type(item) is float:
            estimate += 32
        else:
            _check(item is None or type(item) is bool)
            estimate += 5
        _check(estimate <= budget.remaining_bytes)
    budget.consumed += estimate
    raw = _canonical(value)
    _check(len(raw) <= VALUE_LIMIT and len(raw) <= estimate)
    return value


def human_configuration(mapping: dict, snapshot: dict, budget: _Budget) -> None:
    """Version-selected cross-team eligibility, never a grant or native actor."""
    _model(RoleMapping, mapping)
    agents = {a['name']: a for a in snapshot['agents'] if a['status'] == 'active'}
    teams = {t['name']: t for t in snapshot['teams']}
    for role, binding in mapping['roles'].items():
        for selector in [binding, *mapping['eligible_replacements'][role]]:
            budget.step()
            agent = agents.get(selector['principal'])
            team = teams.get(selector['team'])
            _check(agent is not None and team is not None and bool(agent['executor'])
                   and agent['team'] == selector['team'])
            if role == 'agent_manager':
                expected = (selector['principal'] if snapshot['schema_version'] == 1
                            else dict(kind='agent', principal=selector['principal']))
                _check(agent['role'] == 'manager' and team['manager'] == expected)
            else:
                _check(agent['role'] == 'worker' and selector['principal'] in team['workers'])
            for reference in mapping['repository_references']:
                budget.step()
                origin = agent['repos'].get(reference['repo_key'])
                _check(type(origin) is str and bool(origin)
                       and _hash(origin.encode('utf8')) == reference['origin_digest']
                       and reference['repository_id'] == 'workflow-repository:' + _hash(_canonical(
                           [snapshot['org_slug'], reference['repo_key'], reference['origin_digest']])))


def read_human_record(raw: bytes, *, expected_org_slug: str, budget: _Budget | None = None) -> SavedCodingRecord:
    record = _closed(_json(raw, VALUE_LIMIT),
        'format org_slug command_bytes_base64 command_digest issuer authority mapping created_at settings')
    _check(record['format'] == HUMAN_RECORD and record['org_slug'] == expected_org_slug)
    budget = _Budget() if budget is None else budget
    _check(type(budget) is _Budget)
    account(record, budget)
    # Charge bounded decoded and canonical copies before the existing pure
    # capture parser traverses them. Its local ancestry cap is additional;
    # it never replaces the enclosing cumulative allowance.
    capture = record['authority']
    _closed(capture, 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest')
    for encoded in (record['command_bytes_base64'], capture['grant_bytes_base64'],
                    capture['snapshot_bytes_base64']):
        _check(type(encoded) is str and len(encoded) <= 4 * ((VALUE_LIMIT + 2) // 3))
        decoded = base64.b64decode(encoded, validate=True)
        _check(base64.b64encode(decoded).decode('ascii') == encoded)
        _check(3 * len(decoded) <= budget.remaining_bytes)
        budget.consumed += 3 * len(decoded)
        account(_json(decoded, VALUE_LIMIT), budget)
    command_raw, _ = _command_bytes(record, COMMAND_LIMIT)
    command = parse_command(command_raw, expected_org_slug)
    _model(RoleMapping, record['mapping'])
    _check(_canonical(record['mapping']) == _canonical(command['mapping']))
    _check(record['issuer'] == dict(kind='human', principal='founder', proof_kind='founder_bearer'))
    grant, _ = _capture(record['authority'], expected_org_slug, _Ancestry())
    _check(grant == command_raw)
    settings = _closed(record['settings'], 'generation admission previous_snapshot_digest')
    _check(type(settings['generation']) is int
           and settings['generation'] == command['expected_generation'] + 1
           and settings['generation'] <= 9223372036854775807
           and settings['admission'] == command['admission'])
    if settings['previous_snapshot_digest'] is not None:
        _digest(settings['previous_snapshot_digest'])
    # The disabled-retain-only exception requires the preceding full Mapping.
    # Only the relational history reader can establish it; no pure parser may
    # apply the old record@1 pin-equality predicate to this human family.
    _timestamp(record['created_at'])
    _check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', record['created_at']) is not None)
    return SavedCodingRecord(raw, command_raw)


def captured_configuration(record: dict, command: dict, previous: dict | None,
                           budget: _Budget) -> None:
    capture = record['authority']
    if command['admission'] == 'disabled' and previous == command['mapping']:
        return  # Complete previous history was authenticated by this caller.
    _check(command['mapping']['authority'] == capture['pin'])
    raw = base64.b64decode(capture['snapshot_bytes_base64'], validate=True)
    _check(len(raw) <= budget.remaining_bytes)
    budget.consumed += len(raw)
    snapshot = account(_json(raw, VALUE_LIMIT), budget)
    human_configuration(command['mapping'], snapshot, budget)
