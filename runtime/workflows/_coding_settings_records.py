"""Closed human settings grammars; parsing conveys no publication authority."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _extend as _reader_extend,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
)

from runtime.infrastructure._workflow_reader import (
    _work as _reader_work, _reserve as _reader_reserve,
    _dump as _reader_dump, _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode, _decode as _reader_decode, _encode as _reader_encode,
)
import re

from typing import Annotated, Literal

from pydantic import Field, model_validator

from runtime.workflows._coding_role_records import (
    Closed, MappingPin, Release, RoleMapping,
)
from runtime.workflows.repository_artifacts import Component, RepositoryAuthority, RepositoryId
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


class SettingsRepositoryChoice(Closed):
    repository_id: RepositoryId
    repo_key: Component
    origin_digest: SHA256


class SettingsOptions(Closed):
    format: Literal['workflow-coding-settings-options@1']
    org_slug: str
    release: Release | None
    authority: RepositoryAuthority | None
    repository_choices: list[SettingsRepositoryChoice] = Field(max_length=1024)
    # Parser choices only; neither is an observed Git fact.
    object_formats: tuple[Literal['sha1'], Literal['sha256']]

    @model_validator(mode='after')
    def addressed(self) -> SettingsOptions:
        _org(self.org_slug)
        _check(self.authority is None or self.authority.namespace == 'org/' + self.org_slug)
        ids = [choice.repository_id for choice in self.repository_choices]
        _check(len(ids) == len(set(ids)))
        return self


def parse_command(raw: bytes, org: str, *, budget=None) -> dict:
    _org(org, reader_budget=budget)
    value = _reader_dump(_model(SettingsCommand, _json(raw, COMMAND_LIMIT, reader_budget=budget), reader_budget=budget), budget=budget)
    _check(value['org_slug'] == org)
    return value


def account(value: object, budget: _Budget) -> object:
    """Charge canonical copies and decoded traversal to the enclosing allowance."""
    # Bound traversal and reserve a conservative JSON/UTF8 copy allowance
    # BEFORE serialization, including escaped strings and container punctuation.
    _reader_reserve(budget, 128)
    pending, estimate = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 192) or [value], 0))
    while pending:
        budget.step()
        item = pending.pop()
        previous_estimate = estimate
        if type(item) is dict:
            estimate += 2 + max(0, len(item) - 1) + len(item)
            _reader_reserve(budget, 128 * len(item))
            _reader_extend(pending, item.keys(), budget=budget)
            _reader_extend(pending, item.values(), budget=budget)
        elif type(item) is list:
            estimate += 2 + max(0, len(item) - 1)
            _reader_reserve(budget, 64 * len(item))
            _reader_extend(pending, item, budget=budget)
        elif type(item) is str:
            estimate += 2 + 6 * len(item)
        elif type(item) is int:
            estimate += max(1, (item.bit_length() * 30103) // 100000 + 1) + (item < 0)
        elif type(item) is float:
            estimate += 32
        else:
            _check(item is None or type(item) is bool)
            estimate += 5
        _reader_reserve(budget, estimate - previous_estimate)
    raw = _canonical(value, reader_budget=budget)
    _check(len(raw) <= VALUE_LIMIT and len(raw) <= estimate)
    return value


def human_configuration(mapping: dict, snapshot: dict, budget: _Budget) -> None:
    """Version-selected cross-team eligibility, never a grant or native actor."""
    _reader_work((_reader_reserve(budget, 96) or (mapping, snapshot)), budget)
    _model(RoleMapping, mapping, reader_budget=budget)
    agents = _reader_dict_pairs(((_reader_reserve(budget, 96) or (a['name'],  a )) for a in snapshot['agents'] if a['status'] == 'active'), budget=budget)
    teams = _reader_dict_pairs(((_reader_reserve(budget, 96) or (t['name'],  t )) for t in snapshot['teams']), budget=budget)
    for role, binding in mapping['roles'].items():
        _reader_reserve(budget, 128 * (1 + len(mapping['eligible_replacements'][role])))
        for selector in [binding, *mapping['eligible_replacements'][role]]:
            budget.step()
            agent = agents.get(selector['principal'])
            team = teams.get(selector['team'])
            _check(agent is not None and team is not None and bool(agent['executor'])
                   and agent['team'] == selector['team'])
            if role == 'agent_manager':
                expected = (selector['principal'] if snapshot['schema_version'] == 1
                            else (_reader_reserve(budget, 320) or dict(kind='agent', principal=selector['principal'])))
                _check(agent['role'] == 'manager' and team['manager'] == expected)
            else:
                _check(agent['role'] == 'worker' and selector['principal'] in team['workers'])
            for reference in mapping['repository_references']:
                budget.step()
                origin = agent['repos'].get(reference['repo_key'])
                _check(type(origin) is str and bool(origin)
                       and _hash(_reader_encode(origin, 'utf8', budget=budget), reader_budget=budget) == reference['origin_digest']
                       and reference['repository_id'] == 'workflow-repository:' + _hash(_canonical(
                           (_reader_reserve(budget, 448) or [snapshot['org_slug'], reference['repo_key'], reference['origin_digest']]), reader_budget=budget), reader_budget=budget))


def read_human_record(raw: bytes, *, expected_org_slug: str, budget: _Budget | None = None) -> SavedCodingRecord:
    budget = _Budget() if budget is None else budget
    _check(type(budget) is _Budget)
    record = _closed(_json(raw, VALUE_LIMIT, reader_budget=budget),
        'format org_slug command_bytes_base64 command_digest issuer authority mapping created_at settings', reader_budget=budget)
    _check(record['format'] == HUMAN_RECORD and record['org_slug'] == expected_org_slug)
    account(record, budget)
    # Charge bounded decoded and canonical copies before the existing pure
    # capture parser traverses them. Its local ancestry cap is additional;
    # it never replaces the enclosing cumulative allowance.
    capture = record['authority']
    _closed(capture, 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest', reader_budget=budget)
    for encoded in (_reader_reserve(budget, 112) or (record['command_bytes_base64'], capture['grant_bytes_base64'],
                    capture['snapshot_bytes_base64'])):
        _check(type(encoded) is str and len(encoded) <= 4 * ((VALUE_LIMIT + 2) // 3))
        decoded = _reader_b64decode(encoded, validate=True, budget=budget)
        _check(_reader_decode(_reader_b64encode(decoded, budget=budget), 'ascii', budget=budget) == encoded)
        _reader_reserve(budget, 3 * len(decoded))
        account(_json(decoded, VALUE_LIMIT, reader_budget=budget), budget)
    command_raw, _ = _command_bytes(record, COMMAND_LIMIT, reader_budget=budget)
    command = parse_command(command_raw, expected_org_slug, budget=budget)
    _model(RoleMapping, record['mapping'], reader_budget=budget)
    _check(_canonical(record['mapping'], reader_budget=budget) == _canonical(command['mapping'], reader_budget=budget))
    _check(record['issuer'] == (_reader_reserve(budget, 448) or dict(kind='human', principal='founder', proof_kind='founder_bearer')))
    grant, _ = _capture(record['authority'], expected_org_slug, _Ancestry(), reader_budget=budget)
    _check(grant == command_raw)
    settings = _closed(record['settings'], 'generation admission previous_snapshot_digest', reader_budget=budget)
    _check(type(settings['generation']) is int
           and settings['generation'] == command['expected_generation'] + 1
           and settings['generation'] <= 9223372036854775807
           and settings['admission'] == command['admission'])
    if settings['previous_snapshot_digest'] is not None:
        _digest(settings['previous_snapshot_digest'], reader_budget=budget)
    # The disabled-retain-only exception requires the preceding full Mapping.
    # Only the relational history reader can establish it; no pure parser may
    # apply the old record@1 pin-equality predicate to this human family.
    _timestamp(record['created_at'], reader_budget=budget)
    _check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', record['created_at']) is not None)
    return SavedCodingRecord(raw, command_raw)


def captured_configuration(record: dict, command: dict, previous: dict | None,
                           budget: _Budget) -> None:
    _reader_work((_reader_reserve(budget, 112) or (record, command, previous)), budget)
    capture = record['authority']
    if command['admission'] == 'disabled' and previous == command['mapping']:
        return  # Complete previous history was authenticated by this caller.
    _check(command['mapping']['authority'] == capture['pin'])
    raw = _reader_b64decode(capture['snapshot_bytes_base64'], validate=True, budget=budget)
    _reader_reserve(budget, len(raw))
    snapshot = account(_json(raw, VALUE_LIMIT, reader_budget=budget), budget)
    human_configuration(command['mapping'], snapshot, budget)
