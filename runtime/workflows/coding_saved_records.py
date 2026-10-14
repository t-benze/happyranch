"""Unmounted K2a2a saved coding grammars, never current-use authorization.

These readers do no store, roster, object, provider or network I/O. A parsed
record is no authentic issuer, active grant, admission or valid native/J graph.
Captures reconstruct historical native snapshots and profiles; selected grant
preimages terminate in the original Founder command. Contained equalities are
checked here, while native row authenticity and current eligibility require
separate relational consumers. There is no resolver or trusted flag.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from runtime.models import AuthorityPolicySelector
from runtime.workflows.artifacts import Token
from runtime.workflows.authority import WorkflowAuthorityError, validate_authority_snapshot
from runtime.workflows.release_templates import CODING_DEFINITION_DIGEST
from runtime.workflows.repository_artifacts import (
    Component, RepositoryAdoption, RepositoryAdoptionProof, RepositoryAuthority,
    RepositoryBinding, RepositoryId, RepositoryIssuer, RepositoryRecipient,
)

COMMAND_LIMIT = 2 * 1048576
VALUE_LIMIT = 1048576
INTEGER_MAX = 9223372036854775807
ROLES = ('implementer', 'code_reviewer', 'qa_engineer', 'agent_manager')
ACTIONS = (
    'import-release-coding', 'publish-coding-mapping', 'adopt-local-store',
    'read-repository-artifact', 'start-coding', 'implement-code-change',
    'review-code-change', 'verify-code-change', 'observe-coding-ci',
    'merge-coding-approved-head', 'replace-coding-stage',
)
_TOKEN = TypeAdapter(Token)
_COMPONENT = TypeAdapter(Component)
_REPOSITORY = TypeAdapter(RepositoryId)


class CodingSavedRecordError(ValueError):
    """Categorical saved-data refusal, with no raw private input in the error."""


@dataclass(frozen=True)
class SavedCodingRecord:
    """Exact checked preimage; no serialization, current-use or authority API."""

    raw: bytes
    command_bytes: bytes | None = None


class CodingRepositoryRecipient(RepositoryRecipient):
    """Coding's closed underscore RoleKeys, without changing K1 acceptance."""

    role: Literal['implementer', 'code_reviewer', 'qa_engineer', 'agent_manager']
    kind: Literal['agent']


class CodingRepositoryBinding(RepositoryBinding):
    # Inherit K1's org/key/origin-derived ID, namespace, uniqueness and owner
    # membership equalities. Only this context uses the four coding RoleKeys.
    recipients: list[CodingRepositoryRecipient] = Field(min_length=4, max_length=4)

    @model_validator(mode='after')
    def coding_order(self) -> CodingRepositoryBinding:
        _check(tuple(r.role for r in self.recipients) == ROLES)
        return self


def _check(condition: bool) -> None:
    if not condition:
        raise CodingSavedRecordError('coding_saved_record_invalid')


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        _check(key not in value)
        value[key] = item
    return value


def _constant(_: str) -> object:
    raise CodingSavedRecordError('coding_saved_record_invalid')


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _json(raw: bytes, limit: int) -> dict:
    try:
        _check(type(raw) is bytes and 0 < len(raw) <= limit)
        value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        _check(type(value) is dict and _canonical(value) == raw)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _closed(value: object, keys: str) -> dict:
    _check(type(value) is dict and set(value) == set(keys.split()))
    return value


def _model(model: type, value: object):
    try:
        record = model.model_validate(value, strict=True)
        _check(_canonical(record.model_dump()) == _canonical(value))
        return record
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _token(value: object, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    try:
        _TOKEN.validate_python(value, strict=True)
        _check(re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) is not None)
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _component(value: object) -> None:
    try:
        _COMPONENT.validate_python(value, strict=True)
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _repository(value: object) -> None:
    try:
        _REPOSITORY.validate_python(value, strict=True)
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _org(value: object) -> None:
    _check(type(value) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', value) is not None)


def _integer(value: object, minimum: int = 0, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    _check(type(value) is int and minimum <= value <= INTEGER_MAX)


def _digest(value: object) -> None:
    _check(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _b64(value: object, limit: int) -> bytes:
    _check(type(value) is str and 0 < len(value) <= 4 * ((limit + 2) // 3))
    try:
        raw = base64.b64decode(value, validate=True)
        _check(0 < len(raw) <= limit and base64.b64encode(raw).decode('ascii') == value)
        return raw
    except (ValueError, binascii.Error) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _timestamp(value: object) -> None:
    _check(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z', value) is not None)
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _array(value: object, maximum: int, *, minimum: int = 0) -> list:
    _check(type(value) is list and minimum <= len(value) <= maximum)
    return value


def _distinct(values: list) -> None:
    preimages = [_canonical(value) for value in values]
    _check(len(set(preimages)) == len(preimages))


def _operation(value: object) -> None:
    _token(value)
    _check(not value.startswith('coding-internal:'))


def _agent(value: object) -> dict:
    record = _closed(value, 'principal team')
    _token(record['principal'])
    _component(record['team'])
    _check(record['principal'] != 'founder')
    return record


def _roles(value: object, eligible: object) -> None:
    roles = _closed(value, ' '.join(ROLES))
    candidates = _closed(eligible, ' '.join(ROLES))
    for role in ROLES:
        _agent(roles[role])
        items = _array(candidates[role], 16)
        for item in items:
            _agent(item)
        _check(len({item['principal'] for item in items}) == len(items))
    _check(len({roles[role]['principal'] for role in ROLES}) == 4)


def _release(value: object) -> None:
    record = _closed(value, 'identity version definition_digest')
    _check(record['identity'] == 'release/happyranch/coding')
    _integer(record['version'], 1)
    _check(record['version'] == 1 and record['definition_digest'] == CODING_DEFINITION_DIGEST)


def _pin(value: object, expected_org_slug: str) -> RepositoryAuthority:
    pin = _model(RepositoryAuthority, value)
    _integer(pin.generation, 1)
    _check(pin.namespace == 'org/' + expected_org_slug)
    return pin


def _path(value: object) -> None:
    _check(type(value) is str)
    if value == '.':
        return
    try:
        raw = value.encode('utf8')
        parts = value.split('/')
        _check(0 < len(raw) <= 4096 and len(parts) <= 64
               and all(part not in ('', '.', '..') and part.casefold() != '.git' for part in parts)
               and not any(ord(c) < 32 or ord(c) == 127 for c in value) and '\\' not in value)
    except UnicodeError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _paths(value: object) -> None:
    items = _array(value, 64)
    for item in items:
        _path(item)
    _distinct(items)


def _commands(value: object) -> None:
    # An argv is data, never a shell command or an execution permission. The
    # enclosing canonical 1MiB value also bounds all argument bytes/depth.
    items = _array(value, 16)
    for argv in items:
        _check(type(argv) is list and bool(argv))
        for argument in argv:
            _check(type(argument) is str and '\x00' not in argument)
            try:
                _check(len(argument.encode('utf8')) <= VALUE_LIMIT)
            except UnicodeError as exc:
                raise CodingSavedRecordError('coding_saved_record_invalid') from exc
        _check(bool(argv[0]))
    _distinct(items)


def _actions(value: object) -> None:
    items = _array(value, 11, minimum=1)
    _check(all(type(action) is str and action in ACTIONS for action in items))
    _distinct(items)


def _oid(value: object, object_format: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    _check(type(value) is str and re.fullmatch(
        r'[0-9a-f]{' + ('40' if object_format == 'sha1' else '64') + '}', value) is not None)


def _provider(value: object) -> None:
    # Existing provider selection is an identity string, not a URL/store path.
    _check(type(value) is str and 0 < len(value.encode('utf8')) <= 256
           and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _scope(value: object) -> None:
    record = _closed(value, 'object_formats store_owners path_prefixes commands recipient_rule provider_rule original_limit_rule')
    formats = _array(record['object_formats'], 2, minimum=1)
    _check(all(type(item) is str and item in ('sha1', 'sha256') for item in formats))
    _distinct(formats)
    owners = _array(record['store_owners'], 16)
    for owner in owners:
        _component(owner)
        _check(owner != 'founder')
    _distinct(owners)
    _paths(record['path_prefixes'])
    _commands(record['commands'])
    _check(record['recipient_rule'] == 'mapping-eligible-current@1'
           and record['original_limit_rule'] == 'lower-native-original@1')
    if record['provider_rule'] is not None:
        provider = _closed(record['provider_rule'], 'provider_repository pr_rule')
        _provider(provider['provider_repository'])
        _check(provider['pr_rule'] == 'source-owned-approved-subject@1')


def _setup_scope(value: object) -> dict:
    record = _closed(value, 'repository_id manager_selector actions scope source_rule base_rule')
    _check(len(_canonical(record)) <= VALUE_LIMIT)
    _repository(record['repository_id'])
    _agent(record['manager_selector'])
    _actions(record['actions'])
    _scope(record['scope'])
    _check(record['source_rule'] == 'current-assigned-manager-decision@1'
           and record['base_rule'] == 'manager-select-local-base@1')
    return record


def _setup_command(value: object, expected_org_slug: str) -> dict:
    record = _closed(value, 'format operation_key setup_key expected_authorization_revision mode scope')
    _check(record['format'] == 'workflow-coding-capability-setup-command@1')
    _operation(record['operation_key'])
    _digest(record['setup_key'])
    _integer(record['expected_authorization_revision'])
    _check(type(record['mode']) is str and record['mode'] in ('issue', 'withdraw'))
    if record['mode'] == 'withdraw':
        _check(record['scope'] is None)
    else:
        scope = _setup_scope(record['scope'])
        _check(record['setup_key'] == _hash(_canonical(
            [expected_org_slug, scope['repository_id'], scope['manager_selector']])))
    return record


def _selector(value: object) -> dict:
    record = _closed(value, 'repository_id store_owner repo_key policy')
    _repository(record['repository_id'])
    _component(record['store_owner'])
    _check(record['store_owner'] != 'founder')
    _component(record['repo_key'])
    _check(record['policy'] == 'adopted-local-store@1')
    return record


def _mapping_command(value: object, expected_org_slug: str) -> dict:
    record = _closed(value, 'format operation_key expected_generation mapping')
    _check(record['format'] == 'workflow-coding-mapping-command@1')
    _operation(record['operation_key'])
    _integer(record['expected_generation'])
    mapping = _closed(record['mapping'], 'release roles eligible_replacements repository_selectors authority')
    _check(len(_canonical(mapping)) <= VALUE_LIMIT)
    _release(mapping['release'])
    _roles(mapping['roles'], mapping['eligible_replacements'])
    _pin(mapping['authority'], expected_org_slug)
    selectors = _array(mapping['repository_selectors'], 8)
    for selector in selectors:
        _selector(selector)
    _check(len({(s['repository_id'], s['store_owner']) for s in selectors}) == len(selectors))
    return record


def _binding(value: object, expected_org_slug: str, roles: dict | None = None) -> CodingRepositoryBinding:
    binding = _model(CodingRepositoryBinding, value)
    _check(binding.org_slug == expected_org_slug)
    _pin(binding.authority.model_dump(), expected_org_slug)
    if roles is not None:
        _check(all(r.principal == roles[r.role]['principal'] and r.team == roles[r.role]['team']
                   for r in binding.recipients))
    return binding


def _mapping(value: object, expected_org_slug: str) -> dict:
    record = _closed(value, 'format release roles eligible_replacements repository_bindings authority')
    _check(record['format'] == 'workflow-coding-mapping@1')
    _release(record['release'])
    _roles(record['roles'], record['eligible_replacements'])
    pin = _pin(record['authority'], expected_org_slug)
    entries = _array(record['repository_bindings'], 8)
    identities = []
    for entry in entries:
        _closed(entry, 'binding adoption')
        binding = _binding(entry['binding'], expected_org_slug, record['roles'])
        adoption = _model(RepositoryAdoption, entry['adoption'])
        _check(binding.authority == pin and adoption.authority == pin
               and adoption.adopted_by == record['roles']['agent_manager']['principal'])
        identities.append((binding.repository_id, binding.store_owner))
    _check(len(set(identities)) == len(identities))
    return record


def _source_text(value: object, *, namespace: bool = False) -> None:
    # Historical source names are verbatim UTF8, not Role/Token aliases.
    _check(type(value) is str and bool(value))
    try:
        _check(len(value.encode('utf8')) <= (256 if namespace else VALUE_LIMIT))
        if namespace:
            _check(bool(value.strip()) and '\x00' not in value)
    except UnicodeError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _ordered(values: list) -> None:
    _check(values == sorted(set(values)))


def _snapshot(snapshot: dict, expected_org_slug: str) -> None:
    """Retain the whole native v1/v2 projection, including policy/agent data."""
    version = snapshot.get('schema_version')
    _integer(version, 1)
    _check(version in (1, 2))
    keys = 'active_policy_selectors agents machine_global_profiles org_slug reviewer_agents schema_version teams'
    _closed(snapshot, keys + (' default_team task_default_team' if version == 2 else ''))
    _check(snapshot['org_slug'] == expected_org_slug)
    try:
        validate_authority_snapshot(snapshot)  # pure version-aware native rule
    except WorkflowAuthorityError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc
    agents = {}
    agent_names = []
    for item in _array(snapshot['agents'], VALUE_LIMIT):
        _check(type(item) is dict)
        keys = 'allow_rules description_digest executor model name repos role status system_prompt_digest team'
        _closed(item, keys + (' reasoning_effort' if 'reasoning_effort' in item else ''))
        for key in ('executor', 'name', 'role', 'team'):
            _source_text(item[key])
        if item['model'] is not None:
            _source_text(item['model'])
            _check(bool(item['model'].strip()))
        _check(item['role'] in ('worker', 'manager'))
        _check(re.fullmatch(r'[a-z0-9_]{1,64}', item['name']) is not None)
        _check(item['status'] in ('active', 'pending'))
        if 'reasoning_effort' in item:
            _source_text(item['reasoning_effort'])
            _check(bool(item['reasoning_effort'].strip()))
        for key in ('description_digest', 'system_prompt_digest'):
            _digest(item[key])
        for rule in _array(item['allow_rules'], VALUE_LIMIT):
            _source_text(rule)
        _check(type(item['repos']) is dict)
        for name, repository in item['repos'].items():
            _source_text(name)
            _check(re.fullmatch(r'[a-z0-9-]{1,32}', name) is not None)
            _source_text(repository)
        agent_names.append(item['name'])
        agents[item['name']] = item
    _ordered(agent_names)
    team_names = []
    workers_by_team = {}
    for team in _array(snapshot['teams'], VALUE_LIMIT):
        _closed(team, 'manager name workers')
        _source_text(team['name'])
        team_names.append(team['name'])
        workers = _array(team['workers'], VALUE_LIMIT)
        for worker in workers:
            _source_text(worker)
            _check(worker in agents and agents[worker]['role'] != 'manager'
                   and agents[worker]['team'] == team['name'])
        # Native v1 permits retained duplicate workers; v2 forbids them.
        # Both producers preserve sorted strings, not caller normalization.
        _check(workers == sorted(workers))
        if version == 2:
            _ordered(workers)
        workers_by_team[team['name']] = workers
        if version == 1:
            manager = team['manager']
            kind = 'agent'
        else:
            manager = team['manager']['principal']
            kind = team['manager']['kind']
        if kind == 'agent':
            _check(manager in agents and agents[manager]['status'] == 'active'
                   and agents[manager]['role'] == 'manager'
                   and agents[manager]['team'] == team['name'])
    _ordered(team_names)
    for agent in agents.values():
        if agent['status'] == 'active' and agent['role'] != 'manager':
            _check(agent['team'] in workers_by_team
                   and agent['name'] in workers_by_team[agent['team']])
    reviewers = _array(snapshot['reviewer_agents'], VALUE_LIMIT)
    for reviewer in reviewers:
        _source_text(reviewer)
        _check(reviewer in agents and agents[reviewer]['status'] == 'active')
    _distinct(reviewers)  # preserve native configured order, not alphabetical
    selector_teams = []
    for item in _array(snapshot['active_policy_selectors'], VALUE_LIMIT):
        _closed(item, 'selector team')
        _source_text(item['team'])
        selector_teams.append(item['team'])
        if item['selector'] is not None:
            selector = _model(AuthorityPolicySelector, item['selector'])
            _check(selector.team == item['team'])
    _check(selector_teams == team_names)


def _profiles(value: object, snapshot: dict, snapshot_raw: bytes,
              pin: RepositoryAuthority) -> None:
    evidence = _closed(value, 'ready pointer profiles mirror')
    ready = _closed(evidence['ready'], 'namespace generation snapshot_bytes_hex snapshot_digest')
    pointer = _closed(evidence['pointer'], 'current_generation journal_id snapshot_digest state profile_fence')
    _source_text(ready['namespace'], namespace=True)
    _integer(ready['generation'], 1)
    _digest(ready['snapshot_digest'])
    encoded = ready['snapshot_bytes_hex']
    _check(type(encoded) is str and 0 < len(encoded) <= 2 * VALUE_LIMIT
           and len(encoded) % 2 == 0 and re.fullmatch(r'[0-9a-f]+', encoded) is not None)
    _check(bytes.fromhex(encoded) == snapshot_raw
           and ready['namespace'] == pin.namespace and ready['generation'] == pin.generation
           and ready['snapshot_digest'] == pin.snapshot_digest)
    _integer(pointer['current_generation'], 1)
    _integer(pointer['profile_fence'])
    _token(pointer['journal_id'])
    _digest(pointer['snapshot_digest'])
    _check(pointer['state'] == 'ready' and pointer['current_generation'] == pin.generation
           and pointer['snapshot_digest'] == pin.snapshot_digest)
    projections = _array(snapshot['machine_global_profiles'], 1024)
    names = []
    for profile in projections:
        _closed(profile, 'consumers generation profile_digest profile_name')
        _source_text(profile['profile_name'])
        _integer(profile['generation'])
        _digest(profile['profile_digest'])
        consumers = _array(profile['consumers'], 16384)
        for consumer in consumers:
            _source_text(consumer, namespace=True)
        _ordered(consumers)
        names.append(profile['profile_name'])
    _ordered(names)
    profiles = _array(evidence['profiles'], 1024)
    for profile in profiles:
        _closed(profile, 'name digest')
        _source_text(profile['name'])
        _digest(profile['digest'])
    _check(profiles == [dict(name=p['profile_name'], digest=p['profile_digest']) for p in projections])
    keys, dependencies, stores, registry = [], [], {}, {}
    for item in _array(evidence['mirror'], 16384):
        _check(type(item) is dict and type(item.get('kind')) is str)
        kind = item['kind']
        _check(kind in ('dependency', 'store', 'registry'))
        fields = {'dependency': 'kind org_namespace profile_name consumer_identity bound_generation state',
                  'store': 'kind profile_name generation profile_digest state',
                  'registry': 'kind profile_name published_generation'}
        _closed(item, fields[kind])
        _source_text(item['profile_name'])
        if kind == 'dependency':
            _source_text(item['org_namespace'], namespace=True)
            _source_text(item['consumer_identity'], namespace=True)
            _integer(item['bound_generation'])
            _check(item['state'] in ('active', 'unbound', 'removed'))
            keys.append((0, item['org_namespace'], item['profile_name'], item['consumer_identity']))
            dependencies.append(item)
        elif kind == 'store':
            _integer(item['generation'])
            _digest(item['profile_digest'])
            _check(item['state'] in ('active', 'removed'))
            keys.append((1, item['profile_name']))
            stores[item['profile_name']] = item
        else:
            _integer(item['published_generation'])
            keys.append((2, item['profile_name']))
            registry[item['profile_name']] = item
    _ordered(keys)
    required = {}
    for dependency in dependencies:
        if dependency['org_namespace'] != pin.namespace or dependency['state'] == 'removed':
            continue
        name = dependency['profile_name']
        store, published = stores.get(name), registry.get(name)
        _check(dependency['state'] == 'active' and store is not None and published is not None)
        generation = dependency['bound_generation']
        _check(store['state'] == 'active' and store['generation'] == generation
               and published['published_generation'] == generation)
        profile = required.setdefault(name, dict(consumers=[], generation=generation,
                                                profile_digest=store['profile_digest'], profile_name=name))
        _check(profile['generation'] == generation and profile['profile_digest'] == store['profile_digest'])
        profile['consumers'].append(dependency['consumer_identity'])
    _check([required[name] for name in sorted(required)] == projections)


@dataclass
class _Ancestry:
    # Shared across M branches; depth/identities are local to each ancestry.
    decoded_bytes: int = 0

    def consume(self, raw: bytes) -> None:
        self.decoded_bytes += len(raw)
        _check(self.decoded_bytes <= 16 * VALUE_LIMIT)


def _capture(value: object, expected_org_slug: str, budget: _Ancestry) -> tuple[bytes, dict]:
    capture = _closed(value, 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest')
    _check(len(_canonical(capture)) <= VALUE_LIMIT)
    pin = _pin(capture['pin'], expected_org_slug)
    _token(capture['admission_record_id'])
    snapshot_raw = _b64(capture['snapshot_bytes_base64'], VALUE_LIMIT)
    snapshot = _json(snapshot_raw, VALUE_LIMIT)
    _check(_hash(snapshot_raw) == pin.snapshot_digest)
    _snapshot(snapshot, expected_org_slug)
    _profiles(capture['profile_evidence'], snapshot, snapshot_raw, pin)
    grant = _b64(capture['grant_bytes_base64'], VALUE_LIMIT)
    budget.consume(grant)
    _digest(capture['grant_digest'])
    _check(_hash(grant) == capture['grant_digest'])
    return grant, _json(grant, VALUE_LIMIT)


def _command_bytes(record: dict, limit: int = COMMAND_LIMIT) -> tuple[bytes, dict]:
    command = _b64(record['command_bytes_base64'], limit)
    _digest(record['command_digest'])
    _check(_hash(command) == record['command_digest'])
    return command, _json(command, limit)


def read_saved_setup_command(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Issue scope or explicit null withdraw; no issuer or current setup claim."""
    _org(expected_org_slug)
    _setup_command(_json(raw, COMMAND_LIMIT), expected_org_slug)
    return SavedCodingRecord(raw)


def read_saved_mapping_command(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Preserve every original selector and generation; never derive capsules."""
    _org(expected_org_slug)
    _mapping_command(_json(raw, COMMAND_LIMIT), expected_org_slug)
    return SavedCodingRecord(raw)


def read_saved_mapping(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Fixed shipped K0 digest and contextual coding capsules; no adoption I/O."""
    _org(expected_org_slug)
    _mapping(_json(raw, VALUE_LIMIT), expected_org_slug)
    return SavedCodingRecord(raw)


def read_saved_repository_binding(raw: bytes, *, expected_org_slug: str) -> CodingRepositoryBinding:
    _org(expected_org_slug)
    return _binding(_json(raw, VALUE_LIMIT), expected_org_slug)


def read_saved_mapping_record(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Complete command/adoption pairing and position-selected setup adapter."""
    _org(expected_org_slug)
    record = _closed(_json(raw, VALUE_LIMIT),
                     'format command_bytes_base64 command_digest issuer authority adoptions mapping created_at')
    _check(record['format'] == 'workflow-coding-mapping-record@1')
    command_raw, command_value = _command_bytes(record)
    command = _mapping_command(command_value, expected_org_slug)
    issuer = _model(RepositoryIssuer, record['issuer'])
    _check(issuer.result_id is None)
    mapping = _mapping(record['mapping'], expected_org_slug)
    _check(_canonical(command['mapping']['release']) == _canonical(mapping['release'])
           and command['mapping']['roles'] == mapping['roles']
           and command['mapping']['eligible_replacements'] == mapping['eligible_replacements']
           and command['mapping']['authority'] == mapping['authority']
           and issuer.principal == mapping['roles']['agent_manager']['principal'])
    selectors = command['mapping']['repository_selectors']
    entries = mapping['repository_bindings']
    proofs = _array(record['adoptions'], 8)
    _check(len(selectors) == len(entries) == len(proofs))
    for selector, entry, proof_value in zip(selectors, entries, proofs):
        binding = _binding(entry['binding'], expected_org_slug, mapping['roles'])
        adoption = _model(RepositoryAdoption, entry['adoption'])
        proof = _model(RepositoryAdoptionProof, proof_value)
        for key in ('repo_device', 'repo_inode', 'git_device', 'git_inode', 'objects_device', 'objects_inode'):
            _integer(getattr(proof.store_identity, key))
        _check(selector == dict(repository_id=binding.repository_id, store_owner=binding.store_owner,
                                repo_key=binding.repo_key, policy=adoption.policy)
               and proof.repository_id == binding.repository_id
               and proof.operation_id == adoption.operation_id
               and proof.issuer == issuer and proof.issuer.principal == adoption.adopted_by
               and proof.authority == adoption.authority == binding.authority
               and adoption.store_identity_digest == _hash(_canonical(proof.store_identity.model_dump()))
               and binding.origin_digest == proof.configured_origin_digest)
        _timestamp(record['created_at'])
        _check(datetime.fromisoformat(proof.observed_at.replace('Z', '+00:00'))
               <= datetime.fromisoformat(record['created_at'].replace('Z', '+00:00')))
    _timestamp(record['created_at'])
    capture = _closed(record['authority'], 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest')
    _check(capture['pin'] == mapping['authority'])
    budget = _Ancestry()
    _, grant = _capture(capture, expected_org_slug, budget)
    _closed(grant, 'format setups')
    _check(grant['format'] == 'workflow-coding-mapping-grant@1')
    setups = [_setup(value, expected_org_slug, budget) for value in _array(grant['setups'], 8, minimum=1)]
    repositories = [setup['repository_id'] for setup in setups]
    _ordered(repositories)
    _check(len({setup['id'] for setup in setups}) == len(setups))
    _check(len({setup['authority']['admission_record_id'] for setup in setups}) == len(setups))
    selected = sorted({selector['repository_id'] for selector in selectors})
    _check(repositories == selected if selected else len(setups) == 1)
    roots = {setup['repository_id']: setup for setup in setups}
    for setup in setups:
        _check(setup['manager_selector'] == mapping['roles']['agent_manager']
               and 'publish-coding-mapping' in setup['actions'])
        _before(setup, record)
        _check(setup['authority']['admission_record_id'] != capture['admission_record_id'])
    for selector, entry, proof in zip(selectors, entries, proofs):
        setup = roots[selector['repository_id']]
        _check('adopt-local-store' in setup['actions']
               and selector['store_owner'] in setup['scope']['store_owners']
               and proof['object_format'] in setup['scope']['object_formats'])
        _before(setup, {'created_at': proof['observed_at']})
        _check(entry['binding']['authority'] == entry['adoption']['authority'] == proof['authority'] == capture['pin'])
    _manager_in_capture(capture, mapping['roles']['agent_manager'])
    for role in ROLES:
        kind = 'manager' if role == 'agent_manager' else 'worker'
        _agent_in_capture(capture, mapping['roles'][role], kind)
        for candidate in mapping['eligible_replacements'][role]:
            _agent_in_capture(capture, candidate, kind)
    return SavedCodingRecord(raw, command_raw)


def _before(parent: dict, child: dict) -> None:
    _check(datetime.fromisoformat(parent['created_at'].replace('Z', '+00:00'))
           <= datetime.fromisoformat(child['created_at'].replace('Z', '+00:00')))


def _agent_in_capture(capture: dict, selector: dict, role: str | None = None) -> None:
    # The captured source projection is contained evidence, not today's roster.
    snapshot = _json(_b64(capture['snapshot_bytes_base64'], VALUE_LIMIT), VALUE_LIMIT)
    agents = [agent for agent in snapshot['agents'] if agent['name'] == selector['principal']]
    _check(len(agents) == 1 and agents[0]['status'] == 'active'
           and (role is None or agents[0]['role'] == role) and agents[0]['team'] == selector['team'])


def _manager_in_capture(capture: dict, selector: dict) -> None:
    _agent_in_capture(capture, selector, 'manager')
    snapshot = _json(_b64(capture['snapshot_bytes_base64'], VALUE_LIMIT), VALUE_LIMIT)
    teams = [team for team in snapshot['teams'] if team['name'] == selector['team']]
    _check(len(teams) == 1)
    manager = teams[0]['manager']
    _check(manager == (selector['principal'] if snapshot['schema_version'] == 1
                       else {'kind': 'agent', 'principal': selector['principal']}))


def _setup(value: object, expected_org_slug: str, budget: _Ancestry,
           seen: frozenset[str] = frozenset(), admissions: frozenset[str] = frozenset()) -> dict:
    record = _closed(value, 'format id org_slug repository_id manager_selector actions scope source_rule base_rule authority created_at')
    _check(len(_canonical(record)) <= VALUE_LIMIT)
    _check(record['format'] == 'workflow-coding-setup-authority@1'
           and record['org_slug'] == expected_org_slug)
    _token(record['id'])
    _check(record['id'] not in seen)
    _setup_scope({key: record[key] for key in (
        'repository_id', 'manager_selector', 'actions', 'scope', 'source_rule', 'base_rule')})
    _timestamp(record['created_at'])
    # U terminates only in complete original ISSUE Q, never another U/P/M.
    _, command_value = _capture(record['authority'], expected_org_slug, budget)
    _check(record['authority']['admission_record_id'] not in admissions)
    command = _setup_command(command_value, expected_org_slug)
    _check(command['mode'] == 'issue'
           and all(record[key] == item for key, item in command['scope'].items()))
    _manager_in_capture(record['authority'], record['manager_selector'])
    return record


def read_saved_setup(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    _org(expected_org_slug)
    _setup(_json(raw, VALUE_LIMIT), expected_org_slug, _Ancestry())
    return SavedCodingRecord(raw)


def read_saved_issuance(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Retain issue/withdraw distinction and original bytes; no inactive replay."""
    _org(expected_org_slug)
    record = _closed(_json(raw, VALUE_LIMIT),
                     'format command_bytes_base64 command_digest issuer mode setup admission created_at')
    _check(record['format'] == 'workflow-coding-capability-issuance@1')
    command_raw, command_value = _command_bytes(record)
    command = _setup_command(command_value, expected_org_slug)
    _check(record['mode'] == command['mode'])
    issuer = _closed(record['issuer'], 'principal_kind principal_id proof_kind')
    _check(issuer == {'principal_kind': 'human', 'principal_id': 'founder', 'proof_kind': 'founder_bearer'})
    _timestamp(record['created_at'])
    admission = _closed(record['admission'], 'record capture')
    native = _closed(admission['record'], 'id namespace generation request_digest admitted_by')
    _token(native['id'])
    _source_text(native['namespace'], namespace=True)
    _integer(native['generation'], 1)
    _digest(native['request_digest'])
    budget = _Ancestry()
    grant_raw, _ = _capture(admission['capture'], expected_org_slug, budget)
    pin = admission['capture']['pin']
    _check(native['id'] == admission['capture']['admission_record_id']
           and native['namespace'] == pin['namespace'] and native['generation'] == pin['generation']
           and native['request_digest'] == record['command_digest']
           and native['admitted_by'] == 'human:founder' and grant_raw == command_raw)
    if record['mode'] == 'withdraw':
        _check(record['setup'] is None)
    else:
        setup = _setup(record['setup'], expected_org_slug, budget)
        _check(all(setup[key] == item for key, item in command['scope'].items())
               and setup['created_at'] == record['created_at']
               and _canonical(setup['authority']) == _canonical(admission['capture']))
    return SavedCodingRecord(raw, command_raw)


def _capability(value: object, expected_org_slug: str, budget: _Ancestry,
                seen: frozenset[str] = frozenset(),
                admissions: frozenset[str] = frozenset()) -> tuple[dict, dict]:
    _check(len(seen) < 64)  # before decoding the next selected parent
    record = _closed(value,
                     'format id parent_id level issuer subject action radius authority source_admission_id created_at')
    _check(len(_canonical(record)) <= VALUE_LIMIT)
    _check(record['format'] == 'workflow-coding-capability@1')
    _token(record['id'])
    _check(record['id'] not in seen)
    _token(record['parent_id'])
    _check(record['parent_id'] != record['id'])
    _token(record['source_admission_id'])
    _timestamp(record['created_at'])
    _check(type(record['level']) is str and record['level'] in ('source', 'attempt', 'control', 'effect')
           and type(record['action']) is str and record['action'] in ACTIONS)
    issuer = _model(RepositoryIssuer, record['issuer'])
    _integer(issuer.result_id, 1, nullable=True)
    subject = _closed(record['subject'],
                      'org_slug principal team source_task_id source_session_id instance_id attempt_id control_id task_id session_id')
    _check(subject['org_slug'] == expected_org_slug)
    _agent({'principal': subject['principal'], 'team': subject['team']})
    for key in ('source_task_id', 'source_session_id', 'task_id'):
        _token(subject[key])
    for key in ('instance_id', 'attempt_id', 'control_id', 'session_id'):
        _token(subject[key], nullable=True)
    radius = _closed(record['radius'],
                     'repository_id store_owner object_format base paths commands provider_repository pr_number expected_head head_rule')
    _repository(radius['repository_id'])
    _check(type(radius['object_format']) is str and radius['object_format'] in ('sha1', 'sha256'))
    _oid(radius['base'], radius['object_format'])
    _paths(radius['paths'])
    _commands(radius['commands'])
    if radius['store_owner'] is not None:
        _component(radius['store_owner'])
        _check(radius['store_owner'] != 'founder')
    _check(type(radius['head_rule']) is str and radius['head_rule'] in ('own-development-output@1', 'exact-subject@1'))
    _oid(radius['expected_head'], radius['object_format'], nullable=True)
    _check((radius['head_rule'] == 'own-development-output@1') == (radius['expected_head'] is None))
    _integer(radius['pr_number'], 1, nullable=True)
    if radius['provider_repository'] is not None:
        _provider(radius['provider_repository'])
    _check((radius['provider_repository'] is None) == (radius['pr_number'] is None))
    level = record['level']
    action = record['action']
    if level == 'source':
        _check(subject['instance_id'] is None and subject['attempt_id'] is None and subject['control_id'] is None
               and subject['principal'] == issuer.principal and subject['task_id'] == issuer.task_id
               and subject['session_id'] == issuer.session_id
               and subject['source_task_id'] == issuer.task_id and subject['source_session_id'] == issuer.session_id)
        if action == 'start-coding':
            _integer(issuer.result_id, 1)
        else:
            _check(issuer.result_id is None)
    elif level == 'control':
        _check(subject['instance_id'] is not None and subject['control_id'] is not None
               and subject['attempt_id'] is None and action == 'replace-coding-stage')
    else:
        _check(subject['instance_id'] is not None and subject['attempt_id'] is not None
               and subject['control_id'] is None)
    if level == 'effect':
        _check(subject['session_id'] is not None and radius['expected_head'] is not None
               and radius['provider_repository'] is not None and radius['pr_number'] is not None
               and action in ('observe-coding-ci', 'merge-coding-approved-head'))
    else:
        # Server-owned provider/PR selection is first fixed by the effect;
        # an upstream null is never navigation-derived provider authority.
        _check(radius['provider_repository'] is None and radius['pr_number'] is None)
    if action in ('review-code-change', 'verify-code-change', 'observe-coding-ci', 'merge-coding-approved-head',
                  'read-repository-artifact'):
        _check(radius['head_rule'] == 'exact-subject@1')
    _, parent = _capture(record['authority'], expected_org_slug, budget)
    admission_id = record['authority']['admission_record_id']
    _check(admission_id not in admissions)
    admissions = admissions | {admission_id}
    chain = seen | {record['id']}
    # Selection is position/level-specific, with no try-one-format fallback.
    if level == 'source' and parent.get('format') == 'workflow-coding-setup-authority@1':
        root = _setup(parent, expected_org_slug, budget, chain, admissions)
        _check(record['parent_id'] == root['id']
               and record['source_admission_id'] == record['authority']['admission_record_id'])
        _before(root, record)
    else:
        _check(parent.get('format') == 'workflow-coding-capability@1')
        parent, root = _capability(parent, expected_org_slug, budget, chain, admissions)
        _parent_edge(record, parent)
    _root_radius(record, root)
    _manager_in_capture(record['authority'], root['manager_selector'])
    role = ('manager' if level in ('source', 'control', 'effect')
            or action in ('observe-coding-ci', 'merge-coding-approved-head')
            else None if action == 'read-repository-artifact' else 'worker')
    _agent_in_capture(record['authority'], {'principal': subject['principal'], 'team': subject['team']}, role)
    return record, root


def _path_subset(paths: list[str], maxima: list[str]) -> None:
    _check(all(any(prefix == '.' or path == prefix or path.startswith(prefix + '/')
                   for prefix in maxima) for path in paths))


def _root_radius(record: dict, root: dict) -> None:
    radius, subject, scope = record['radius'], record['subject'], root['scope']
    _check(radius['repository_id'] == root['repository_id']
           and radius['object_format'] in scope['object_formats']
           and record['action'] in root['actions'])
    _path_subset(radius['paths'], scope['path_prefixes'])
    _check(all(command in scope['commands'] for command in radius['commands']))
    if radius['store_owner'] is not None:
        _check(radius['store_owner'] in scope['store_owners'])
    if radius['provider_repository'] is not None:
        _check(scope['provider_rule'] is not None
               and radius['provider_repository'] == scope['provider_rule']['provider_repository'])
    _check(record['issuer']['principal'] == root['manager_selector']['principal'])
    _check(subject['source_task_id'] == record['issuer']['task_id']
           and subject['source_session_id'] == record['issuer']['session_id'])
    if record['level'] == 'source':
        _check({'principal': subject['principal'], 'team': subject['team']} == root['manager_selector'])
    elif record['level'] == 'control' or record['action'] in ('observe-coding-ci', 'merge-coding-approved-head'):
        _check({'principal': subject['principal'], 'team': subject['team']} == root['manager_selector'])
    elif record['action'] in ('implement-code-change', 'review-code-change', 'verify-code-change'):
        _check(subject['principal'] != root['manager_selector']['principal'])
    # Role membership of an arbitrary mapped recipient is external unless its
    # mapping is supplied. The contained manager identity above is decidable.
    level_actions = {
        'source': ('import-release-coding', 'publish-coding-mapping', 'adopt-local-store',
                   'read-repository-artifact', 'start-coding'),
        'attempt': ('implement-code-change', 'review-code-change', 'verify-code-change',
                    'read-repository-artifact', 'observe-coding-ci', 'merge-coding-approved-head'),
        'control': ('replace-coding-stage',),
        'effect': ('observe-coding-ci', 'merge-coding-approved-head'),
    }
    _check(record['action'] in level_actions[record['level']])
    if record['level'] != 'source':
        _integer(record['issuer']['result_id'], 1)  # genuine allocating source preimage
        _check(subject['task_id'] != subject['source_task_id']
               and (subject['session_id'] is None or subject['session_id'] != subject['source_session_id']))


def _parent_edge(child: dict, parent: dict) -> None:
    _check(child['parent_id'] == parent['id'] and child['id'] != parent['id']
           and child['source_admission_id'] == parent['source_admission_id']
           and child['issuer'] == parent['issuer'])
    _before(parent, child)
    subject, original = child['subject'], parent['subject']
    for key in ('org_slug', 'source_task_id', 'source_session_id'):
        _check(subject[key] == original[key])
    level, upstream = child['level'], parent['level']
    allowed = {'source': ('source',), 'attempt': ('source', 'attempt'),
               'control': ('source', 'control'), 'effect': ('attempt', 'effect')}
    _check(upstream in allowed[level])
    if level == upstream:
        _check(child['action'] == parent['action'])
        for key in subject:
            if key != 'session_id':
                _check(subject[key] == original[key])
        _check(original['session_id'] is None or subject['session_id'] == original['session_id'])
        if original['session_id'] is None and subject['session_id'] is not None:
            _check(level in ('attempt', 'control'))
    elif level == 'effect':
        _check(child['action'] == parent['action'] and subject == original
               and original['session_id'] is not None)
    else:
        # Source->stage/control is source-backed allocation, never an action
        # refinement of a historical attempt. Source owns the start result.
        _check(parent['action'] == 'start-coding' and subject['session_id'] is None)
    radius, maximum = child['radius'], parent['radius']
    for key in ('repository_id', 'object_format', 'base'):
        _check(radius[key] == maximum[key])
    _path_subset(radius['paths'], maximum['paths'])
    _check(all(command in maximum['commands'] for command in radius['commands']))
    for key in ('store_owner', 'provider_repository', 'pr_number', 'expected_head'):
        if maximum[key] is not None:
            _check(radius[key] == maximum[key])
        elif level == upstream:
            _check(radius[key] is None)
    if maximum['head_rule'] == 'exact-subject@1':
        _check(radius['head_rule'] == 'exact-subject@1')
    if level == upstream:
        _check(radius['head_rule'] == maximum['head_rule'])


def read_saved_capability(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Full bounded saved ancestry; queued/null SID is never an actor grant."""
    _org(expected_org_slug)
    _capability(_json(raw, VALUE_LIMIT), expected_org_slug, _Ancestry())
    return SavedCodingRecord(raw)
