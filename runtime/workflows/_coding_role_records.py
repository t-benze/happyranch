"""Closed agent-read role records; parsing never confers repository authority."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
)

from runtime.infrastructure._workflow_reader import (
    _work as _reader_work, _reserve as _reader_reserve,
    _dump as _reader_dump, _b64decode as _reader_b64decode, _encode as _reader_encode,
)
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.workflows.artifacts import CodeChangeDescriptor, SHA256, Token
from runtime.workflows.coding_saved_records import (
    ROLES, VALUE_LIMIT, SavedCodingRecord, _Ancestry, _canonical, _capture,
    _check, _closed, _command_bytes, _hash, _json, _manager_in_capture,
    _model, _org, _roles, _timestamp,
)
from runtime.workflows.repository_artifacts import Component, RepositoryAuthority, RepositoryId, RepositoryIssuer

COMMAND_LIMIT = 65536
RECORD_FORMAT = 'workflow-coding-agent-read-role-record@1'


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AgentBinding(Closed):
    principal: Token
    team: Component


class Roles(Closed):
    implementer: AgentBinding
    code_reviewer: AgentBinding
    qa_engineer: AgentBinding
    agent_manager: AgentBinding


class Replacements(Closed):
    implementer: list[AgentBinding] = Field(max_length=16)
    code_reviewer: list[AgentBinding] = Field(max_length=16)
    qa_engineer: list[AgentBinding] = Field(max_length=16)
    agent_manager: list[AgentBinding] = Field(max_length=16)


class Release(Closed):
    identity: Literal['release/happyranch/coding']
    version: Annotated[int, Field(ge=1, le=1)]
    definition_digest: Literal['6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8']


class RepositoryReference(Closed):
    repository_id: RepositoryId
    repo_key: Component
    origin_digest: SHA256
    object_format: Literal['sha1', 'sha256']


class RoleMapping(Closed):
    release: Release
    roles: Roles
    eligible_replacements: Replacements
    repository_references: list[RepositoryReference] = Field(min_length=1, max_length=8)
    authority: RepositoryAuthority

    @model_validator(mode='after')
    def independent(self) -> RoleMapping:
        _roles(self.roles.model_dump(), self.eligible_replacements.model_dump())
        # Every replacement remains a possible single-slot independent binding.
        for role in ROLES:
            others = {getattr(self.roles, key).principal for key in ROLES if key != role}
            for candidate in getattr(self.eligible_replacements, role):
                _check(candidate.principal not in others)
        ids = [item.repository_id for item in self.repository_references]
        _check(len(ids) == len(set(ids)))
        return self


class RoleCommand(Closed):
    format: Literal['workflow-coding-mapping-command@2']
    operation_key: Token
    expected_generation: Annotated[int, Field(ge=0, lt=9223372036854775807)]
    mapping: RoleMapping

    @model_validator(mode='after')
    def public_key(self) -> RoleCommand:
        _check(not self.operation_key.startswith('coding-internal:'))
        return self


class MappingPin(Closed):
    id: Token
    generation: Annotated[int, Field(ge=1, le=9223372036854775807)]
    digest: SHA256


class SelectionCommand(Closed):
    format: Literal['workflow-coding-role-selection-command@1']
    mapping: MappingPin
    implementer_override: AgentBinding | None
    subject: CodeChangeDescriptor

    @model_validator(mode='after')
    def comparison_required(self) -> SelectionCommand:
        _check(self.subject.comparison is not None)
        return self


class Current(Closed):
    state: Literal['active', 'revoked']
    reason: Token | None

    @model_validator(mode='after')
    def reason_matches(self) -> Current:
        _check((self.state == 'revoked') == (self.reason is not None))
        return self


class RoleReceipt(Closed):
    format: Literal['workflow-coding-mapping-receipt@2']
    id: Token
    generation: Annotated[int, Field(ge=1, le=9223372036854775807)]
    mapping_digest: SHA256
    mapping: RoleMapping
    replayed: bool
    current: Current


class SelectionReceipt(Closed):
    format: Literal['workflow-coding-role-selection@1']
    mapping: MappingPin
    roles: Roles
    subject: CodeChangeDescriptor
    overridden: bool
    coding_enabled: Literal[False]


def parse_role_command(raw: bytes, *, org_slug: str, budget=None) -> dict:
    _org(org_slug, reader_budget=budget)
    command = _reader_dump(_model(RoleCommand, _json(raw, COMMAND_LIMIT, reader_budget=budget), reader_budget=budget), budget=budget)
    _check(command['mapping']['authority']['namespace'] == 'org/' + org_slug)
    return command


def parse_selection(raw: bytes) -> dict:
    return _model(SelectionCommand, _json(raw, COMMAND_LIMIT)).model_dump()


def configuration(mapping: dict, snapshot: dict, *, budget=None) -> None:
    """Same existing published roster/repository policy at capture and current use.

    The mapping manager is separately bound to the authentic issuer/current caller.
    This same predicate checks original captured authority and current use.
    No filesystem/Git/storage/object inspection. An origin digest identifies a
    configured reference; it never proves access or inspection by an agent.
    """
    _reader_work((_reader_reserve(budget, 96) or (mapping, snapshot)), budget)
    _model(RoleMapping, mapping, reader_budget=budget)
    agents = _reader_dict_pairs(((_reader_reserve(budget, 96) or (a['name'],  a )) for a in snapshot['agents'] if a['status'] == 'active'), budget=budget)
    teams = _reader_dict_pairs(((_reader_reserve(budget, 96) or (team['name'],  team )) for team in snapshot['teams']), budget=budget)
    manager = mapping['roles']['agent_manager']
    owner = agents.get(manager['principal'])
    team = teams.get(manager['team'])
    expected = manager['principal'] if snapshot['schema_version'] == 1 else (_reader_reserve(budget, 320) or dict(
        kind='agent', principal=manager['principal']))
    _check(owner is not None and team is not None and bool(owner['executor'])
           and owner['role'] == 'manager' and owner['team'] == manager['team']
           and team['manager'] == expected)
    # Existing ordinary manager actions are confined to the manager's own team.
    # Shared org/repository membership is not cross-team routing authority.
    for role in ROLES:
        _reader_reserve(budget, 128 * (1 + len(mapping['eligible_replacements'][role])))
        for selector in [mapping['roles'][role], *mapping['eligible_replacements'][role]]:
            _check(selector['team'] == manager['team'])
            agent = agents.get(selector['principal'])
            team = teams.get(selector['team'])
            _check(agent is not None and team is not None and bool(agent['executor'])
                   and agent['team'] == selector['team'])
            if role == 'agent_manager':
                registered_manager = team['manager']
                expected = selector['principal'] if snapshot['schema_version'] == 1 else (_reader_reserve(budget, 320) or dict(
                    kind='agent', principal=selector['principal']))
                _check(agent['role'] == 'manager' and registered_manager == expected)
            else:
                _check(agent['role'] == 'worker' and selector['principal'] in team['workers'])
            for reference in mapping['repository_references']:
                _reader_work((_reader_reserve(budget, 96) or (selector, reference)), budget)
                origin = agent['repos'].get(reference['repo_key'])
                _check(type(origin) is str and bool(origin)
                       and _hash(_reader_encode(origin, 'utf8', budget=budget), reader_budget=budget) == reference['origin_digest']
                       and reference['repository_id'] == 'workflow-repository:' + _hash(_canonical(
                           (_reader_reserve(budget, 448) or [snapshot['org_slug'], reference['repo_key'], reference['origin_digest']]), reader_budget=budget), reader_budget=budget))


def read_role_record(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    _org(expected_org_slug, reader_budget=budget)
    record = _closed(_json(raw, VALUE_LIMIT, reader_budget=budget),
        'format org_slug command_bytes_base64 command_digest issuer authority mapping created_at', reader_budget=budget)
    _check(record['format'] == RECORD_FORMAT and record['org_slug'] == expected_org_slug)
    command_raw, _ = _command_bytes(record, COMMAND_LIMIT, reader_budget=budget)
    command = parse_role_command(command_raw, org_slug=expected_org_slug, budget=budget)
    _check(record['mapping'] == command['mapping'])
    issuer = _model(RepositoryIssuer, record['issuer'], reader_budget=budget)
    _check(issuer.result_id is None and issuer.principal == record['mapping']['roles']['agent_manager']['principal'])
    grant, _ = _capture(record['authority'], expected_org_slug, _Ancestry(), reader_budget=budget)
    _check(grant == command_raw and record['authority']['pin'] == record['mapping']['authority'])
    _timestamp(record['created_at'], reader_budget=budget)
    _manager_in_capture(record['authority'], record['mapping']['roles']['agent_manager'], reader_budget=budget)
    snapshot = _json(_reader_b64decode(record['authority']['snapshot_bytes_base64'], validate=True, budget=budget), VALUE_LIMIT, reader_budget=budget)
    configuration(record['mapping'], snapshot, budget=budget)
    return SavedCodingRecord(raw, command_raw)
