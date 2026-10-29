"""Saved coding grammars, consumed by K2b setup; never current authorization.

These readers do no store, roster, object, provider or network I/O. A parsed
record is no authentic issuer, active grant, admission or valid native/J graph.
Captures reconstruct historical native snapshots and profiles; selected grant
preimages terminate in the original Founder command. Contained equalities are
checked here, while native row authenticity and current eligibility require
separate relational consumers. There is no resolver or trusted flag.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _json_dumps as _reader_json_dumps,
)

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _ReaderBudgetExhausted,
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _encode as _reader_encode,
    _fromhex as _reader_fromhex,
    _sha256 as _reader_sha256,
)


from runtime.infrastructure._workflow_reader import (
    _work as _reader_work, _json as _reader_json,
    _reserve as _reader_reserve, _dump as _reader_dump,
)

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


def _canonical(value: object, *, reader_budget=None) -> bytes:
    _reader_reserve(reader_budget, 0)
    return _reader_encode(_reader_json_dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False, budget=reader_budget), 'utf8', budget=reader_budget)


def _json(raw: bytes, limit: int, *, reader_budget=None) -> dict:
    try:
        _reader_reserve(reader_budget, 128)
        _check(type(raw) is bytes and 0 < len(raw) <= limit)
        _reader_work(raw, reader_budget)
        value = _reader_json(_reader_decode(raw, 'utf8', budget=reader_budget), reader_budget, object_pairs_hook=_pairs,
                             parse_constant=_constant)
        _check(type(value) is dict and _canonical(value, reader_budget=reader_budget) == raw)
        return value
    except _ReaderBudgetExhausted:
        raise
    except ((ValueError, TypeError, UnicodeError, RecursionError)) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _closed(value: object, keys: str, *, reader_budget=None) -> dict:
    if reader_budget is not None and type(value) is dict:
        _reader_reserve(reader_budget, 128 * len(value) + 128 * len(keys))
        for key in value:
            _reader_work(key, reader_budget)
    _check(type(value) is dict and _reader_set(value, budget=reader_budget) == _reader_set(keys.split(), budget=reader_budget))
    return value


def _model(model: type, value: object, *, reader_budget=None):
    _reader_work(value, reader_budget, copies=2)
    try:
        _reader_reserve(reader_budget, 128)
        record = model.model_validate(value, strict=True)
        _reader_work(record, reader_budget)
        _check(_canonical(_reader_dump(record, budget=reader_budget), reader_budget=reader_budget) ==
               _canonical(value, reader_budget=reader_budget))
        return record
    except _ReaderBudgetExhausted:
        raise
    except ((ValueError, TypeError, UnicodeError, RecursionError)) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _token(value: object, *, nullable: bool = False, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    if value is None and nullable:
        return
    try:
        _TOKEN.validate_python(value, strict=True)
        _check(re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) is not None)
    except _ReaderBudgetExhausted:
        raise
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _component(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    try:
        _COMPONENT.validate_python(value, strict=True)
    except _ReaderBudgetExhausted:
        raise
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _repository(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    try:
        _REPOSITORY.validate_python(value, strict=True)
    except _ReaderBudgetExhausted:
        raise
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _org(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    _check(type(value) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', value) is not None)


def _integer(value: object, minimum: int = 0, *, nullable: bool = False, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    if value is None and nullable:
        return
    _check(type(value) is int and minimum <= value <= INTEGER_MAX)


def _digest(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    _check(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def _hash(raw: bytes, *, reader_budget=None) -> str:
    _reader_reserve(reader_budget, 0)
    return _reader_sha256(raw, budget=reader_budget).hexdigest()


def _b64(value: object, limit: int, *, reader_budget=None) -> bytes:
    _reader_work(value, reader_budget, copies=2)
    _check(type(value) is str and 0 < len(value) <= 4 * ((limit + 2) // 3))
    try:
        _reader_reserve(reader_budget, 96)
        raw = _reader_b64decode(value, validate=True, budget=reader_budget)
        _check(0 < len(raw) <= limit and _reader_decode(_reader_b64encode(raw, budget=reader_budget), 'ascii', budget=reader_budget) == value)
        return raw
    except _ReaderBudgetExhausted:
        raise
    except ((ValueError, binascii.Error)) as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _timestamp(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    _check(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z', value) is not None)
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except _ReaderBudgetExhausted:
        raise
    except ValueError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _array(value: object, maximum: int, *, minimum: int = 0, reader_budget=None) -> list:
    if reader_budget is not None and type(value) is list:
        _reader_reserve(reader_budget, 64 + 64 * len(value))
    _check(type(value) is list and minimum <= len(value) <= maximum)
    return value


def _distinct(values: list, *, reader_budget=None) -> None:
    _reader_work(values, reader_budget)
    preimages = _reader_list((_canonical(value, reader_budget=reader_budget) for value in values), budget=reader_budget)
    _check(len(_reader_set(preimages, budget=reader_budget)) == len(preimages))


def _operation(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    _token(value, reader_budget=reader_budget)
    _check(not value.startswith('coding-internal:'))


def _agent(value: object, *, reader_budget=None) -> dict:
    _reader_work(value, reader_budget)
    record = _closed(value, 'principal team', reader_budget=reader_budget)
    _token(record['principal'], reader_budget=reader_budget)
    _component(record['team'], reader_budget=reader_budget)
    _check(record['principal'] != 'founder')
    return record


def _roles(value: object, eligible: object, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, eligible)), reader_budget)
    roles = _closed(value, ' '.join(ROLES), reader_budget=reader_budget)
    candidates = _closed(eligible, ' '.join(ROLES), reader_budget=reader_budget)
    for role in ROLES:
        _agent(roles[role], reader_budget=reader_budget)
        items = _array(candidates[role], 16, reader_budget=reader_budget)
        for item in items:
            _agent(item, reader_budget=reader_budget)
        _check(len(_reader_set((item['principal'] for item in items), budget=reader_budget)) == len(items))
    _check(len(_reader_set((roles[role]['principal'] for role in ROLES), budget=reader_budget)) == 4)


def _release(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    record = _closed(value, 'identity version definition_digest', reader_budget=reader_budget)
    _check(record['identity'] == 'release/happyranch/coding')
    _integer(record['version'], 1, reader_budget=reader_budget)
    _check(record['version'] == 1 and record['definition_digest'] == CODING_DEFINITION_DIGEST)


def _pin(value: object, expected_org_slug: str, *, reader_budget=None) -> RepositoryAuthority:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    pin = _model(RepositoryAuthority, value, reader_budget=reader_budget)
    _integer(pin.generation, 1, reader_budget=reader_budget)
    _check(pin.namespace == 'org/' + expected_org_slug)
    return pin


def _path(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    _check(type(value) is str)
    if value == '.':
        return
    try:
        raw = _reader_encode(value, 'utf8', budget=reader_budget)
        parts = value.split('/')
        _check(0 < len(raw) <= 4096 and len(parts) <= 64
               and _reader_all_true((part not in ('', '.', '..') and part.casefold() != '.git' for part in parts), budget=reader_budget)
               and not _reader_any_true((ord(c) < 32 or ord(c) == 127 for c in value), budget=reader_budget) and '\\' not in value)
    except UnicodeError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _paths(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    items = _array(value, 64, reader_budget=reader_budget)
    for item in items:
        _path(item, reader_budget=reader_budget)
    _distinct(items, reader_budget=reader_budget)


def _commands(value: object, *, reader_budget=None) -> None:
    # An argv is data, never a shell command or an execution permission. The
    # enclosing canonical 1MiB value also bounds all argument bytes/depth.
    _reader_work(value, reader_budget)
    items = _array(value, 16, reader_budget=reader_budget)
    for argv in items:
        _check(type(argv) is list and bool(argv))
        for argument in argv:
            _check(type(argument) is str and '\x00' not in argument)
            try:
                _check(len(_reader_encode(argument, 'utf8', budget=reader_budget)) <= VALUE_LIMIT)
            except UnicodeError as exc:
                raise CodingSavedRecordError('coding_saved_record_invalid') from exc
        _check(bool(argv[0]))
    _distinct(items, reader_budget=reader_budget)


def _actions(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    items = _array(value, 11, minimum=1, reader_budget=reader_budget)
    _check(_reader_all_true((type(action) is str and action in ACTIONS for action in items), budget=reader_budget))
    _distinct(items, reader_budget=reader_budget)


def _oid(value: object, object_format: str, *, nullable: bool = False, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    if value is None and nullable:
        return
    _check(type(value) is str and re.fullmatch(
        r'[0-9a-f]{' + ('40' if object_format == 'sha1' else '64') + '}', value) is not None)


def _provider(value: object, *, reader_budget=None) -> None:
    # Existing provider selection is an identity string, not a URL/store path.
    _reader_work(value, reader_budget)
    _check(type(value) is str and 0 < len(_reader_encode(value, 'utf8', budget=reader_budget)) <= 256
           and not _reader_any_true((ord(c) < 32 or ord(c) == 127 for c in value), budget=reader_budget))


def _scope(value: object, *, reader_budget=None) -> None:
    _reader_work(value, reader_budget)
    record = _closed(value, 'object_formats store_owners path_prefixes commands recipient_rule provider_rule original_limit_rule', reader_budget=reader_budget)
    formats = _array(record['object_formats'], 2, minimum=1, reader_budget=reader_budget)
    _check(_reader_all_true((type(item) is str and item in ('sha1', 'sha256') for item in formats), budget=reader_budget))
    _distinct(formats, reader_budget=reader_budget)
    owners = _array(record['store_owners'], 16, reader_budget=reader_budget)
    for owner in owners:
        _component(owner, reader_budget=reader_budget)
        _check(owner != 'founder')
    _distinct(owners, reader_budget=reader_budget)
    _paths(record['path_prefixes'], reader_budget=reader_budget)
    _commands(record['commands'], reader_budget=reader_budget)
    _check(record['recipient_rule'] == 'mapping-eligible-current@1'
           and record['original_limit_rule'] == 'lower-native-original@1')
    if record['provider_rule'] is not None:
        provider = _closed(record['provider_rule'], 'provider_repository pr_rule', reader_budget=reader_budget)
        _provider(provider['provider_repository'], reader_budget=reader_budget)
        _check(provider['pr_rule'] == 'source-owned-approved-subject@1')


def _setup_scope(value: object, *, reader_budget=None) -> dict:
    _reader_work(value, reader_budget)
    record = _closed(value, 'repository_id manager_selector actions scope source_rule base_rule', reader_budget=reader_budget)
    _check(len(_canonical(record, reader_budget=reader_budget)) <= VALUE_LIMIT)
    _repository(record['repository_id'], reader_budget=reader_budget)
    _agent(record['manager_selector'], reader_budget=reader_budget)
    _actions(record['actions'], reader_budget=reader_budget)
    _scope(record['scope'], reader_budget=reader_budget)
    _check(record['source_rule'] == 'current-assigned-manager-decision@1'
           and record['base_rule'] == 'manager-select-local-base@1')
    return record


def _setup_command(value: object, expected_org_slug: str, *, reader_budget=None) -> dict:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    record = _closed(value, 'format operation_key setup_key expected_authorization_revision mode scope', reader_budget=reader_budget)
    _check(record['format'] == 'workflow-coding-capability-setup-command@1')
    _operation(record['operation_key'], reader_budget=reader_budget)
    _digest(record['setup_key'], reader_budget=reader_budget)
    _integer(record['expected_authorization_revision'], reader_budget=reader_budget)
    _check(type(record['mode']) is str and record['mode'] in ('issue', 'withdraw'))
    if record['mode'] == 'withdraw':
        _check(record['scope'] is None)
    else:
        scope = _setup_scope(record['scope'], reader_budget=reader_budget)
        _check(record['setup_key'] == _hash(_canonical(
            (_reader_reserve(reader_budget, 448) or [expected_org_slug, scope['repository_id'], scope['manager_selector']]), reader_budget=reader_budget), reader_budget=reader_budget))
    return record


def _selector(value: object, *, reader_budget=None) -> dict:
    _reader_work(value, reader_budget)
    record = _closed(value, 'repository_id store_owner repo_key policy', reader_budget=reader_budget)
    _repository(record['repository_id'], reader_budget=reader_budget)
    _component(record['store_owner'], reader_budget=reader_budget)
    _check(record['store_owner'] != 'founder')
    _component(record['repo_key'], reader_budget=reader_budget)
    _check(record['policy'] == 'adopted-local-store@1')
    return record


def _mapping_command(value: object, expected_org_slug: str, *, reader_budget=None) -> dict:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    record = _closed(value, 'format operation_key expected_generation mapping', reader_budget=reader_budget)
    _check(record['format'] == 'workflow-coding-mapping-command@1')
    _operation(record['operation_key'], reader_budget=reader_budget)
    _integer(record['expected_generation'], reader_budget=reader_budget)
    mapping = _closed(record['mapping'], 'release roles eligible_replacements repository_selectors authority', reader_budget=reader_budget)
    _check(len(_canonical(mapping, reader_budget=reader_budget)) <= VALUE_LIMIT)
    _release(mapping['release'], reader_budget=reader_budget)
    _roles(mapping['roles'], mapping['eligible_replacements'], reader_budget=reader_budget)
    _pin(mapping['authority'], expected_org_slug, reader_budget=reader_budget)
    selectors = _array(mapping['repository_selectors'], 8, reader_budget=reader_budget)
    for selector in selectors:
        _selector(selector, reader_budget=reader_budget)
    _check(len(_reader_set(((_reader_reserve(reader_budget, 96) or (s['repository_id'], s['store_owner'])) for s in selectors), budget=reader_budget)) == len(selectors))
    return record


def _binding(value: object, expected_org_slug: str, roles: dict | None = None, *, reader_budget=None) -> CodingRepositoryBinding:
    _reader_work((_reader_reserve(reader_budget, 112) or (value, expected_org_slug, roles)), reader_budget)
    binding = _model(CodingRepositoryBinding, value, reader_budget=reader_budget)
    _check(binding.org_slug == expected_org_slug)
    _pin(_reader_dump(binding.authority, budget=reader_budget), expected_org_slug, reader_budget=reader_budget)
    if roles is not None:
        _check(_reader_all_true((r.principal == roles[r.role]['principal'] and r.team == roles[r.role]['team']
                   for r in binding.recipients), budget=reader_budget))
    return binding


def _mapping(value: object, expected_org_slug: str, *, reader_budget=None) -> dict:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    record = _closed(value, 'format release roles eligible_replacements repository_bindings authority', reader_budget=reader_budget)
    _check(record['format'] == 'workflow-coding-mapping@1')
    _release(record['release'], reader_budget=reader_budget)
    _roles(record['roles'], record['eligible_replacements'], reader_budget=reader_budget)
    pin = _pin(record['authority'], expected_org_slug, reader_budget=reader_budget)
    entries = _array(record['repository_bindings'], 8, reader_budget=reader_budget)
    identities = (_reader_reserve(reader_budget, 64) or [])
    for entry in entries:
        _closed(entry, 'binding adoption', reader_budget=reader_budget)
        binding = _binding(entry['binding'], expected_org_slug, record['roles'], reader_budget=reader_budget)
        adoption = _model(RepositoryAdoption, entry['adoption'], reader_budget=reader_budget)
        _check(binding.authority == pin and adoption.authority == pin
               and adoption.adopted_by == record['roles']['agent_manager']['principal'])
        _reader_append(identities, (_reader_reserve(reader_budget, 96) or (binding.repository_id, binding.store_owner)), budget=reader_budget)
    _check(len(_reader_set(identities, budget=reader_budget)) == len(identities))
    return record


def _source_text(value: object, *, namespace: bool = False, reader_budget=None) -> None:
    # Historical source names are verbatim UTF8, not Role/Token aliases.
    _reader_work(value, reader_budget)
    _check(type(value) is str and bool(value))
    try:
        _check(len(_reader_encode(value, 'utf8', budget=reader_budget)) <= (256 if namespace else VALUE_LIMIT))
        if namespace:
            _check(bool(value.strip()) and '\x00' not in value)
    except UnicodeError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc


def _ordered(values: list, *, reader_budget=None) -> None:
    _reader_work(values, reader_budget)
    _check(values == _reader_sorted(_reader_set(values, budget=reader_budget), budget=reader_budget))


def _snapshot(snapshot: dict, expected_org_slug: str, *, reader_budget=None) -> None:
    """Retain the whole native v1/v2 projection, including policy/agent data."""
    _reader_work((_reader_reserve(reader_budget, 96) or (snapshot, expected_org_slug)), reader_budget)
    version = snapshot.get('schema_version')
    _integer(version, 1, reader_budget=reader_budget)
    _check(version in (1, 2))
    keys = 'active_policy_selectors agents machine_global_profiles org_slug reviewer_agents schema_version teams'
    _closed(snapshot, keys + (' default_team task_default_team' if version == 2 else ''), reader_budget=reader_budget)
    _check(snapshot['org_slug'] == expected_org_slug)
    try:
        validate_authority_snapshot(snapshot, budget=reader_budget)  # pure version-aware native rule
    except WorkflowAuthorityError as exc:
        raise CodingSavedRecordError('coding_saved_record_invalid') from exc
    agents = (_reader_reserve(reader_budget, 64) or {})
    agent_names = (_reader_reserve(reader_budget, 64) or [])
    for item in _array(snapshot['agents'], VALUE_LIMIT, reader_budget=reader_budget):
        _check(type(item) is dict)
        keys = 'allow_rules description_digest executor model name repos role status system_prompt_digest team'
        _closed(item, keys + (' reasoning_effort' if 'reasoning_effort' in item else ''), reader_budget=reader_budget)
        for key in ('executor', 'name', 'role', 'team'):
            _source_text(item[key], reader_budget=reader_budget)
        if item['model'] is not None:
            _source_text(item['model'], reader_budget=reader_budget)
            _check(bool(item['model'].strip()))
        _check(item['role'] in ('worker', 'manager'))
        _check(re.fullmatch(r'[a-z0-9_]{1,64}', item['name']) is not None)
        _check(item['status'] in ('active', 'pending'))
        if 'reasoning_effort' in item:
            _source_text(item['reasoning_effort'], reader_budget=reader_budget)
            _check(bool(item['reasoning_effort'].strip()))
        for key in ('description_digest', 'system_prompt_digest'):
            _digest(item[key], reader_budget=reader_budget)
        for rule in _array(item['allow_rules'], VALUE_LIMIT, reader_budget=reader_budget):
            _source_text(rule, reader_budget=reader_budget)
        _check(type(item['repos']) is dict)
        for name, repository in item['repos'].items():
            _source_text(name, reader_budget=reader_budget)
            _check(re.fullmatch(r'[a-z0-9-]{1,32}', name) is not None)
            _source_text(repository, reader_budget=reader_budget)
        _reader_append(agent_names, item['name'], budget=reader_budget)
        _reader_put(item, agents, item['name'], budget=reader_budget)
    _ordered(agent_names, reader_budget=reader_budget)
    team_names = (_reader_reserve(reader_budget, 64) or [])
    workers_by_team = (_reader_reserve(reader_budget, 64) or {})
    for team in _array(snapshot['teams'], VALUE_LIMIT, reader_budget=reader_budget):
        _closed(team, 'manager name workers', reader_budget=reader_budget)
        _source_text(team['name'], reader_budget=reader_budget)
        _reader_append(team_names, team['name'], budget=reader_budget)
        workers = _array(team['workers'], VALUE_LIMIT, reader_budget=reader_budget)
        for worker in workers:
            _source_text(worker, reader_budget=reader_budget)
            _check(worker in agents and agents[worker]['role'] != 'manager'
                   and agents[worker]['team'] == team['name'])
        # Native v1 permits retained duplicate workers; v2 forbids them.
        # Both producers preserve sorted strings, not caller normalization.
        _check(workers == _reader_sorted(workers, budget=reader_budget))
        if version == 2:
            _ordered(workers, reader_budget=reader_budget)
        _reader_put(workers, workers_by_team, team['name'], budget=reader_budget)
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
    _ordered(team_names, reader_budget=reader_budget)
    for agent in agents.values():
        if agent['status'] == 'active' and agent['role'] != 'manager':
            _check(agent['team'] in workers_by_team
                   and agent['name'] in workers_by_team[agent['team']])
    reviewers = _array(snapshot['reviewer_agents'], VALUE_LIMIT, reader_budget=reader_budget)
    for reviewer in reviewers:
        _source_text(reviewer, reader_budget=reader_budget)
        _check(reviewer in agents and agents[reviewer]['status'] == 'active')
    _distinct(reviewers, reader_budget=reader_budget)  # preserve native configured order, not alphabetical
    selector_teams = (_reader_reserve(reader_budget, 64) or [])
    for item in _array(snapshot['active_policy_selectors'], VALUE_LIMIT, reader_budget=reader_budget):
        _closed(item, 'selector team', reader_budget=reader_budget)
        _source_text(item['team'], reader_budget=reader_budget)
        _reader_append(selector_teams, item['team'], budget=reader_budget)
        if item['selector'] is not None:
            selector = _model(AuthorityPolicySelector, item['selector'], reader_budget=reader_budget)
            _check(selector.team == item['team'])
    _check(selector_teams == team_names)


def _profiles(value: object, snapshot: dict, snapshot_raw: bytes,
              pin: RepositoryAuthority, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 128) or (value, snapshot, snapshot_raw, pin)), reader_budget)
    evidence = _closed(value, 'ready pointer profiles mirror', reader_budget=reader_budget)
    ready = _closed(evidence['ready'], 'namespace generation snapshot_bytes_hex snapshot_digest', reader_budget=reader_budget)
    pointer = _closed(evidence['pointer'], 'current_generation journal_id snapshot_digest state profile_fence', reader_budget=reader_budget)
    _source_text(ready['namespace'], namespace=True, reader_budget=reader_budget)
    _integer(ready['generation'], 1, reader_budget=reader_budget)
    _digest(ready['snapshot_digest'], reader_budget=reader_budget)
    encoded = ready['snapshot_bytes_hex']
    _check(type(encoded) is str and 0 < len(encoded) <= 2 * VALUE_LIMIT
           and len(encoded) % 2 == 0 and re.fullmatch(r'[0-9a-f]+', encoded) is not None)
    _check(_reader_fromhex(encoded, budget=reader_budget) == snapshot_raw
           and ready['namespace'] == pin.namespace and ready['generation'] == pin.generation
           and ready['snapshot_digest'] == pin.snapshot_digest)
    _integer(pointer['current_generation'], 1, reader_budget=reader_budget)
    _integer(pointer['profile_fence'], reader_budget=reader_budget)
    _token(pointer['journal_id'], reader_budget=reader_budget)
    _digest(pointer['snapshot_digest'], reader_budget=reader_budget)
    _check(pointer['state'] == 'ready' and pointer['current_generation'] == pin.generation
           and pointer['snapshot_digest'] == pin.snapshot_digest)
    projections = _array(snapshot['machine_global_profiles'], 1024, reader_budget=reader_budget)
    names = (_reader_reserve(reader_budget, 64) or [])
    for profile in projections:
        _closed(profile, 'consumers generation profile_digest profile_name', reader_budget=reader_budget)
        _source_text(profile['profile_name'], reader_budget=reader_budget)
        _integer(profile['generation'], reader_budget=reader_budget)
        _digest(profile['profile_digest'], reader_budget=reader_budget)
        consumers = _array(profile['consumers'], 16384, reader_budget=reader_budget)
        for consumer in consumers:
            _source_text(consumer, namespace=True, reader_budget=reader_budget)
        _ordered(consumers, reader_budget=reader_budget)
        _reader_append(names, profile['profile_name'], budget=reader_budget)
    _ordered(names, reader_budget=reader_budget)
    profiles = _array(evidence['profiles'], 1024, reader_budget=reader_budget)
    for profile in profiles:
        _closed(profile, 'name digest', reader_budget=reader_budget)
        _source_text(profile['name'], reader_budget=reader_budget)
        _digest(profile['digest'], reader_budget=reader_budget)
    _check(profiles == _reader_list(((_reader_reserve(reader_budget, 320) or dict(name=p['profile_name'], digest=p['profile_digest'])) for p in projections), budget=reader_budget))
    keys, dependencies, stores, registry = (_reader_reserve(reader_budget, 128) or (_reader_reserve(reader_budget, 64) or [], _reader_reserve(reader_budget, 64) or [], _reader_reserve(reader_budget, 64) or {}, _reader_reserve(reader_budget, 64) or {}))
    for item in _array(evidence['mirror'], 16384, reader_budget=reader_budget):
        _check(type(item) is dict and type(item.get('kind')) is str)
        kind = item['kind']
        _check(kind in ('dependency', 'store', 'registry'))
        fields = (_reader_reserve(reader_budget, 448) or {'dependency': 'kind org_namespace profile_name consumer_identity bound_generation state',
                  'store': 'kind profile_name generation profile_digest state',
                  'registry': 'kind profile_name published_generation'})
        _closed(item, fields[kind], reader_budget=reader_budget)
        _source_text(item['profile_name'], reader_budget=reader_budget)
        if kind == 'dependency':
            _source_text(item['org_namespace'], namespace=True, reader_budget=reader_budget)
            _source_text(item['consumer_identity'], namespace=True, reader_budget=reader_budget)
            _integer(item['bound_generation'], reader_budget=reader_budget)
            _check(item['state'] in ('active', 'unbound', 'removed'))
            _reader_append(keys, (_reader_reserve(reader_budget, 128) or (0, item['org_namespace'], item['profile_name'], item['consumer_identity'])), budget=reader_budget)
            _reader_append(dependencies, item, budget=reader_budget)
        elif kind == 'store':
            _integer(item['generation'], reader_budget=reader_budget)
            _digest(item['profile_digest'], reader_budget=reader_budget)
            _check(item['state'] in ('active', 'removed'))
            _reader_append(keys, (_reader_reserve(reader_budget, 96) or (1, item['profile_name'])), budget=reader_budget)
            _reader_put(item, stores, item['profile_name'], budget=reader_budget)
        else:
            _integer(item['published_generation'], reader_budget=reader_budget)
            _reader_append(keys, (_reader_reserve(reader_budget, 96) or (2, item['profile_name'])), budget=reader_budget)
            _reader_put(item, registry, item['profile_name'], budget=reader_budget)
    _ordered(keys, reader_budget=reader_budget)
    required = (_reader_reserve(reader_budget, 64) or {})
    for dependency in dependencies:
        if dependency['org_namespace'] != pin.namespace or dependency['state'] == 'removed':
            continue
        name = dependency['profile_name']
        store, published = (_reader_reserve(reader_budget, 96) or (stores.get(name), registry.get(name)))
        _check(dependency['state'] == 'active' and store is not None and published is not None)
        generation = dependency['bound_generation']
        _check(store['state'] == 'active' and store['generation'] == generation
               and published['published_generation'] == generation)
        profile = _reader_setdefault(required, name, _reader_reserve(reader_budget, 576) or dict(consumers=(_reader_reserve(reader_budget, 64) or []), generation=generation,
                                                profile_digest=store['profile_digest'], profile_name=name), budget=reader_budget)
        _check(profile['generation'] == generation and profile['profile_digest'] == store['profile_digest'])
        _reader_append(profile['consumers'], dependency['consumer_identity'], budget=reader_budget)
    _check(_reader_list((required[name] for name in _reader_sorted(required, budget=reader_budget)), budget=reader_budget) == projections)


@dataclass
class _Ancestry:
    # Shared across M branches; depth/identities are local to each ancestry.
    decoded_bytes: int = 0

    def consume(self, raw: bytes) -> None:
        self.decoded_bytes += len(raw)
        _check(self.decoded_bytes <= 16 * VALUE_LIMIT)


def _capture(value: object, expected_org_slug: str, ancestry: _Ancestry, *, reader_budget=None) -> tuple[bytes, dict]:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    capture = _closed(value, 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest', reader_budget=reader_budget)
    _check(len(_canonical(capture, reader_budget=reader_budget)) <= VALUE_LIMIT)
    pin = _pin(capture['pin'], expected_org_slug, reader_budget=reader_budget)
    _token(capture['admission_record_id'], reader_budget=reader_budget)
    snapshot_raw = _b64(capture['snapshot_bytes_base64'], VALUE_LIMIT, reader_budget=reader_budget)
    snapshot = _json(snapshot_raw, VALUE_LIMIT, reader_budget=reader_budget)
    _check(_hash(snapshot_raw, reader_budget=reader_budget) == pin.snapshot_digest)
    _snapshot(snapshot, expected_org_slug, reader_budget=reader_budget)
    _profiles(capture['profile_evidence'], snapshot, snapshot_raw, pin, reader_budget=reader_budget)
    grant = _b64(capture['grant_bytes_base64'], VALUE_LIMIT, reader_budget=reader_budget)
    ancestry.consume(grant)
    _digest(capture['grant_digest'], reader_budget=reader_budget)
    _check(_hash(grant, reader_budget=reader_budget) == capture['grant_digest'])
    return (_reader_reserve(reader_budget, 96) or (grant, _json(grant, VALUE_LIMIT, reader_budget=reader_budget)))


def _command_bytes(record: dict, limit: int = COMMAND_LIMIT, *, reader_budget=None) -> tuple[bytes, dict]:
    _reader_work(record, reader_budget)
    command = _b64(record['command_bytes_base64'], limit, reader_budget=reader_budget)
    _digest(record['command_digest'], reader_budget=reader_budget)
    _check(_hash(command, reader_budget=reader_budget) == record['command_digest'])
    return (_reader_reserve(reader_budget, 96) or (command, _json(command, limit, reader_budget=reader_budget)))


def read_saved_setup_command(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Issue scope or explicit null withdraw; no issuer or current setup claim."""
    _org(expected_org_slug, reader_budget=budget)
    _setup_command(_json(raw, COMMAND_LIMIT, reader_budget=budget), expected_org_slug, reader_budget=budget)
    return SavedCodingRecord(raw)


def read_saved_mapping_command(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Preserve every original selector and generation; never derive capsules."""
    _org(expected_org_slug, reader_budget=budget)
    _mapping_command(_json(raw, COMMAND_LIMIT, reader_budget=budget), expected_org_slug, reader_budget=budget)
    return SavedCodingRecord(raw)


def read_saved_mapping(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Fixed shipped K0 digest and contextual coding capsules; no adoption I/O."""
    _org(expected_org_slug, reader_budget=budget)
    _mapping(_json(raw, VALUE_LIMIT, reader_budget=budget), expected_org_slug, reader_budget=budget)
    return SavedCodingRecord(raw)


def read_saved_repository_binding(raw: bytes, *, expected_org_slug: str, budget=None) -> CodingRepositoryBinding:
    _org(expected_org_slug, reader_budget=budget)
    return _binding(_json(raw, VALUE_LIMIT, reader_budget=budget), expected_org_slug, reader_budget=budget)


def read_saved_mapping_record(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Complete command/adoption pairing and position-selected setup adapter."""
    _org(expected_org_slug, reader_budget=budget)
    value = _json(raw, VALUE_LIMIT, reader_budget=budget)
    if value.get('format') == 'workflow-coding-agent-read-role-record@2':
        from runtime.workflows._coding_settings_records import read_human_record
        return read_human_record(raw, expected_org_slug=expected_org_slug, budget=budget)
    if value.get('format') == 'workflow-coding-agent-read-role-record@1':
        from runtime.workflows._coding_role_records import read_role_record
        return read_role_record(raw, expected_org_slug=expected_org_slug, budget=budget)
    record = _closed(value,
                     'format command_bytes_base64 command_digest issuer authority adoptions mapping created_at', reader_budget=budget)
    _check(record['format'] == 'workflow-coding-mapping-record@1')
    command_raw, command_value = _command_bytes(record, reader_budget=budget)
    command = _mapping_command(command_value, expected_org_slug, reader_budget=budget)
    issuer = _model(RepositoryIssuer, record['issuer'], reader_budget=budget)
    _check(issuer.result_id is None)
    mapping = _mapping(record['mapping'], expected_org_slug, reader_budget=budget)
    _check(_canonical(command['mapping']['release'], reader_budget=budget) == _canonical(mapping['release'], reader_budget=budget)
           and command['mapping']['roles'] == mapping['roles']
           and command['mapping']['eligible_replacements'] == mapping['eligible_replacements']
           and command['mapping']['authority'] == mapping['authority']
           and issuer.principal == mapping['roles']['agent_manager']['principal'])
    selectors = command['mapping']['repository_selectors']
    entries = mapping['repository_bindings']
    proofs = _array(record['adoptions'], 8, reader_budget=budget)
    _check(len(selectors) == len(entries) == len(proofs))
    for selector, entry, proof_value in zip(selectors, entries, proofs):
        binding = _binding(entry['binding'], expected_org_slug, mapping['roles'], reader_budget=budget)
        adoption = _model(RepositoryAdoption, entry['adoption'], reader_budget=budget)
        proof = _model(RepositoryAdoptionProof, proof_value, reader_budget=budget)
        for key in ('repo_device', 'repo_inode', 'git_device', 'git_inode', 'objects_device', 'objects_inode'):
            _integer(getattr(proof.store_identity, key), reader_budget=budget)
        _check(selector == (_reader_reserve(budget, 576) or dict(repository_id=binding.repository_id, store_owner=binding.store_owner,
                                repo_key=binding.repo_key, policy=adoption.policy))
               and proof.repository_id == binding.repository_id
               and proof.operation_id == adoption.operation_id
               and proof.issuer == issuer and proof.issuer.principal == adoption.adopted_by
               and proof.authority == adoption.authority == binding.authority
               and adoption.store_identity_digest == _hash(_canonical(_reader_dump(proof.store_identity, budget=budget), reader_budget=budget), reader_budget=budget)
               and binding.origin_digest == proof.configured_origin_digest)
        _timestamp(record['created_at'], reader_budget=budget)
        _check(datetime.fromisoformat(proof.observed_at.replace('Z', '+00:00'))
               <= datetime.fromisoformat(record['created_at'].replace('Z', '+00:00')))
    _timestamp(record['created_at'], reader_budget=budget)
    capture = _closed(record['authority'], 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest', reader_budget=budget)
    _check(capture['pin'] == mapping['authority'])
    ancestry = _Ancestry()
    _, grant = _capture(capture, expected_org_slug, ancestry, reader_budget=budget)
    _closed(grant, 'format setups', reader_budget=budget)
    _check(grant['format'] == 'workflow-coding-mapping-grant@1')
    setups = _reader_list((_setup(value, expected_org_slug, ancestry, reader_budget=budget) for value in _array(grant['setups'], 8, minimum=1, reader_budget=budget)), budget=budget)
    repositories = _reader_list((setup['repository_id'] for setup in setups), budget=budget)
    _ordered(repositories, reader_budget=budget)
    _check(len(_reader_set((setup['id'] for setup in setups), budget=budget)) == len(setups))
    _check(len(_reader_set((setup['authority']['admission_record_id'] for setup in setups), budget=budget)) == len(setups))
    selected = _reader_sorted(_reader_set((selector['repository_id'] for selector in selectors), budget=budget), budget=budget)
    _check(repositories == selected if selected else len(setups) == 1)
    roots = _reader_dict_pairs(((_reader_reserve(budget, 96) or (setup['repository_id'],  setup )) for setup in setups), budget=budget)
    for setup in setups:
        _check(setup['manager_selector'] == mapping['roles']['agent_manager']
               and 'publish-coding-mapping' in setup['actions'])
        _before(setup, record, reader_budget=budget)
        _check(setup['authority']['admission_record_id'] != capture['admission_record_id'])
    for selector, entry, proof in zip(selectors, entries, proofs):
        setup = roots[selector['repository_id']]
        _check('adopt-local-store' in setup['actions']
               and selector['store_owner'] in setup['scope']['store_owners']
               and proof['object_format'] in setup['scope']['object_formats'])
        _before(setup, (_reader_reserve(budget, 192) or {'created_at': proof['observed_at']}), reader_budget=budget)
        _check(entry['binding']['authority'] == entry['adoption']['authority'] == proof['authority'] == capture['pin'])
    _manager_in_capture(capture, mapping['roles']['agent_manager'], reader_budget=budget)
    for role in ROLES:
        kind = 'manager' if role == 'agent_manager' else 'worker'
        _agent_in_capture(capture, mapping['roles'][role], kind, reader_budget=budget)
        for candidate in mapping['eligible_replacements'][role]:
            _agent_in_capture(capture, candidate, kind, reader_budget=budget)
    return SavedCodingRecord(raw, command_raw)


def _before(parent: dict, child: dict, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (parent, child)), reader_budget)
    _check(datetime.fromisoformat(parent['created_at'].replace('Z', '+00:00'))
           <= datetime.fromisoformat(child['created_at'].replace('Z', '+00:00')))


def _agent_in_capture(capture: dict, selector: dict, role: str | None = None, *, reader_budget=None) -> None:
    # The captured source projection is contained evidence, not today's roster.
    _reader_work((_reader_reserve(reader_budget, 112) or (capture, selector, role)), reader_budget)
    snapshot = _json(_b64(capture['snapshot_bytes_base64'], VALUE_LIMIT, reader_budget=reader_budget), VALUE_LIMIT, reader_budget=reader_budget)
    agents = _reader_list((agent for agent in snapshot['agents'] if agent['name'] == selector['principal']), budget=reader_budget)
    _check(len(agents) == 1 and agents[0]['status'] == 'active'
           and (role is None or agents[0]['role'] == role) and agents[0]['team'] == selector['team'])


def _manager_in_capture(capture: dict, selector: dict, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (capture, selector)), reader_budget)
    _agent_in_capture(capture, selector, 'manager', reader_budget=reader_budget)
    snapshot = _json(_b64(capture['snapshot_bytes_base64'], VALUE_LIMIT, reader_budget=reader_budget), VALUE_LIMIT, reader_budget=reader_budget)
    teams = _reader_list((team for team in snapshot['teams'] if team['name'] == selector['team']), budget=reader_budget)
    _check(len(teams) == 1)
    manager = teams[0]['manager']
    _check(manager == (selector['principal'] if snapshot['schema_version'] == 1
                       else (_reader_reserve(reader_budget, 320) or {'kind': 'agent', 'principal': selector['principal']})))


def _setup(value: object, expected_org_slug: str, ancestry: _Ancestry,
           seen: frozenset[str] = frozenset(), admissions: frozenset[str] = frozenset(), *, reader_budget=None) -> dict:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    record = _closed(value, 'format id org_slug repository_id manager_selector actions scope source_rule base_rule authority created_at', reader_budget=reader_budget)
    _check(len(_canonical(record, reader_budget=reader_budget)) <= VALUE_LIMIT)
    _check(record['format'] == 'workflow-coding-setup-authority@1'
           and record['org_slug'] == expected_org_slug)
    _token(record['id'], reader_budget=reader_budget)
    _check(record['id'] not in seen)
    _setup_scope(_reader_dict_pairs(((_reader_reserve(reader_budget, 96) or (key,  record[key] )) for key in (
        'repository_id', 'manager_selector', 'actions', 'scope', 'source_rule', 'base_rule')), budget=reader_budget), reader_budget=reader_budget)
    _timestamp(record['created_at'], reader_budget=reader_budget)
    # U terminates only in complete original ISSUE Q, never another U/P/M.
    _, command_value = _capture(record['authority'], expected_org_slug, ancestry, reader_budget=reader_budget)
    _check(record['authority']['admission_record_id'] not in admissions)
    command = _setup_command(command_value, expected_org_slug, reader_budget=reader_budget)
    _check(command['mode'] == 'issue'
           and _reader_all_true((record[key] == item for key, item in command['scope'].items()), budget=reader_budget))
    _manager_in_capture(record['authority'], record['manager_selector'], reader_budget=reader_budget)
    return record


def read_saved_setup(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    _org(expected_org_slug, reader_budget=budget)
    _setup(_json(raw, VALUE_LIMIT, reader_budget=budget), expected_org_slug, _Ancestry(), reader_budget=budget)
    return SavedCodingRecord(raw)


def read_saved_issuance(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Retain issue/withdraw distinction and original bytes; no inactive replay."""
    _org(expected_org_slug, reader_budget=budget)
    record = _closed(_json(raw, VALUE_LIMIT, reader_budget=budget),
                     'format command_bytes_base64 command_digest issuer mode setup admission created_at', reader_budget=budget)
    _check(record['format'] == 'workflow-coding-capability-issuance@1')
    command_raw, command_value = _command_bytes(record, reader_budget=budget)
    command = _setup_command(command_value, expected_org_slug, reader_budget=budget)
    _check(record['mode'] == command['mode'])
    issuer = _closed(record['issuer'], 'principal_kind principal_id proof_kind', reader_budget=budget)
    _check(issuer == (_reader_reserve(budget, 448) or {'principal_kind': 'human', 'principal_id': 'founder', 'proof_kind': 'founder_bearer'}))
    _timestamp(record['created_at'], reader_budget=budget)
    admission = _closed(record['admission'], 'record capture', reader_budget=budget)
    native = _closed(admission['record'], 'id namespace generation request_digest admitted_by', reader_budget=budget)
    _token(native['id'], reader_budget=budget)
    _source_text(native['namespace'], namespace=True, reader_budget=budget)
    _integer(native['generation'], 1, reader_budget=budget)
    _digest(native['request_digest'], reader_budget=budget)
    ancestry = _Ancestry()
    grant_raw, _ = _capture(admission['capture'], expected_org_slug, ancestry, reader_budget=budget)
    pin = admission['capture']['pin']
    _check(native['id'] == admission['capture']['admission_record_id']
           and native['namespace'] == pin['namespace'] and native['generation'] == pin['generation']
           and native['request_digest'] == record['command_digest']
           and native['admitted_by'] == 'human:founder' and grant_raw == command_raw)
    if record['mode'] == 'withdraw':
        _check(record['setup'] is None)
    else:
        setup = _setup(record['setup'], expected_org_slug, ancestry, reader_budget=budget)
        _check(_reader_all_true((setup[key] == item for key, item in command['scope'].items()), budget=budget)
               and setup['created_at'] == record['created_at']
               and _canonical(setup['authority'], reader_budget=budget) == _canonical(admission['capture'], reader_budget=budget))
    return SavedCodingRecord(raw, command_raw)


def _capability(value: object, expected_org_slug: str, ancestry: _Ancestry,
                seen: frozenset[str] = frozenset(),
                admissions: frozenset[str] = frozenset(), *, reader_budget=None) -> tuple[dict, dict]:
    _reader_work((_reader_reserve(reader_budget, 96) or (value, expected_org_slug)), reader_budget)
    _check(len(seen) < 64)  # before decoding the next selected parent
    record = _closed(value,
                     'format id parent_id level issuer subject action radius authority source_admission_id created_at', reader_budget=reader_budget)
    _check(len(_canonical(record, reader_budget=reader_budget)) <= VALUE_LIMIT)
    _check(record['format'] == 'workflow-coding-capability@1')
    _token(record['id'], reader_budget=reader_budget)
    _check(record['id'] not in seen)
    _token(record['parent_id'], reader_budget=reader_budget)
    _check(record['parent_id'] != record['id'])
    _token(record['source_admission_id'], reader_budget=reader_budget)
    _timestamp(record['created_at'], reader_budget=reader_budget)
    _check(type(record['level']) is str and record['level'] in ('source', 'attempt', 'control', 'effect')
           and type(record['action']) is str and record['action'] in ACTIONS)
    issuer = _model(RepositoryIssuer, record['issuer'], reader_budget=reader_budget)
    _integer(issuer.result_id, 1, nullable=True, reader_budget=reader_budget)
    subject = _closed(record['subject'],
                      'org_slug principal team source_task_id source_session_id instance_id attempt_id control_id task_id session_id', reader_budget=reader_budget)
    _check(subject['org_slug'] == expected_org_slug)
    _agent((_reader_reserve(reader_budget, 320) or {'principal': subject['principal'], 'team': subject['team']}), reader_budget=reader_budget)
    for key in ('source_task_id', 'source_session_id', 'task_id'):
        _token(subject[key], reader_budget=reader_budget)
    for key in ('instance_id', 'attempt_id', 'control_id', 'session_id'):
        _token(subject[key], nullable=True, reader_budget=reader_budget)
    radius = _closed(record['radius'],
                     'repository_id store_owner object_format base paths commands provider_repository pr_number expected_head head_rule', reader_budget=reader_budget)
    _repository(radius['repository_id'], reader_budget=reader_budget)
    _check(type(radius['object_format']) is str and radius['object_format'] in ('sha1', 'sha256'))
    _oid(radius['base'], radius['object_format'], reader_budget=reader_budget)
    _paths(radius['paths'], reader_budget=reader_budget)
    _commands(radius['commands'], reader_budget=reader_budget)
    if radius['store_owner'] is not None:
        _component(radius['store_owner'], reader_budget=reader_budget)
        _check(radius['store_owner'] != 'founder')
    _check(type(radius['head_rule']) is str and radius['head_rule'] in ('own-development-output@1', 'exact-subject@1'))
    _oid(radius['expected_head'], radius['object_format'], nullable=True, reader_budget=reader_budget)
    _check((radius['head_rule'] == 'own-development-output@1') == (radius['expected_head'] is None))
    _integer(radius['pr_number'], 1, nullable=True, reader_budget=reader_budget)
    if radius['provider_repository'] is not None:
        _provider(radius['provider_repository'], reader_budget=reader_budget)
    _check((radius['provider_repository'] is None) == (radius['pr_number'] is None))
    level = record['level']
    action = record['action']
    if level == 'source':
        _check(subject['instance_id'] is None and subject['attempt_id'] is None and subject['control_id'] is None
               and subject['principal'] == issuer.principal and subject['task_id'] == issuer.task_id
               and subject['session_id'] == issuer.session_id
               and subject['source_task_id'] == issuer.task_id and subject['source_session_id'] == issuer.session_id)
        if action == 'start-coding':
            _integer(issuer.result_id, 1, reader_budget=reader_budget)
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
    _, parent = _capture(record['authority'], expected_org_slug, ancestry, reader_budget=reader_budget)
    admission_id = record['authority']['admission_record_id']
    _check(admission_id not in admissions)
    admissions = admissions | _reader_set((_reader_reserve(reader_budget, 80) or (admission_id,)), budget=reader_budget)
    chain = seen | _reader_set((_reader_reserve(reader_budget, 80) or (record['id'],)), budget=reader_budget)
    # Selection is position/level-specific, with no try-one-format fallback.
    if level == 'source' and parent.get('format') == 'workflow-coding-setup-authority@1':
        root = _setup(parent, expected_org_slug, ancestry, chain, admissions, reader_budget=reader_budget)
        _check(record['parent_id'] == root['id']
               and record['source_admission_id'] == record['authority']['admission_record_id'])
        _before(root, record, reader_budget=reader_budget)
    else:
        _check(parent.get('format') == 'workflow-coding-capability@1')
        parent, root = _capability(parent, expected_org_slug, ancestry, chain, admissions, reader_budget=reader_budget)
        _parent_edge(record, parent, reader_budget=reader_budget)
    _root_radius(record, root, reader_budget=reader_budget)
    _manager_in_capture(record['authority'], root['manager_selector'], reader_budget=reader_budget)
    role = ('manager' if level in ('source', 'control', 'effect')
            or action in ('observe-coding-ci', 'merge-coding-approved-head')
            else None if action == 'read-repository-artifact' else 'worker')
    _agent_in_capture(record['authority'], (_reader_reserve(reader_budget, 320) or {'principal': subject['principal'], 'team': subject['team']}), role, reader_budget=reader_budget)
    return (_reader_reserve(reader_budget, 96) or (record, root))


def _path_subset(paths: list[str], maxima: list[str], *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (paths, maxima)), reader_budget)
    _check(_reader_all_true((_reader_any_true((prefix == '.' or path == prefix or path.startswith(prefix + '/')
                   for prefix in maxima), budget=reader_budget) for path in paths), budget=reader_budget))


def _root_radius(record: dict, root: dict, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (record, root)), reader_budget)
    radius, subject, scope = (_reader_reserve(reader_budget, 112) or (record['radius'], record['subject'], root['scope']))
    _check(radius['repository_id'] == root['repository_id']
           and radius['object_format'] in scope['object_formats']
           and record['action'] in root['actions'])
    _path_subset(radius['paths'], scope['path_prefixes'], reader_budget=reader_budget)
    _check(_reader_all_true((command in scope['commands'] for command in radius['commands']), budget=reader_budget))
    if radius['store_owner'] is not None:
        _check(radius['store_owner'] in scope['store_owners'])
    if radius['provider_repository'] is not None:
        _check(scope['provider_rule'] is not None
               and radius['provider_repository'] == scope['provider_rule']['provider_repository'])
    _check(record['issuer']['principal'] == root['manager_selector']['principal'])
    _check(subject['source_task_id'] == record['issuer']['task_id']
           and subject['source_session_id'] == record['issuer']['session_id'])
    if record['level'] == 'source':
        _check((_reader_reserve(reader_budget, 320) or {'principal': subject['principal'], 'team': subject['team']}) == root['manager_selector'])
    elif record['action'] in ('implement-code-change', 'review-code-change', 'verify-code-change'):
        _check(subject['principal'] != root['manager_selector']['principal'])
    # Role membership of an arbitrary mapped recipient is external unless its
    # mapping is supplied. The contained SOURCE manager identity is decidable;
    # a downstream manager actor retains its own parent-bound task/principal.
    level_actions = (_reader_reserve(reader_budget, 576) or {
        'source': ('import-release-coding', 'publish-coding-mapping', 'adopt-local-store',
                   'read-repository-artifact', 'start-coding'),
        'attempt': ('implement-code-change', 'review-code-change', 'verify-code-change',
                    'read-repository-artifact', 'observe-coding-ci', 'merge-coding-approved-head'),
        'control': ('replace-coding-stage',),
        'effect': ('observe-coding-ci', 'merge-coding-approved-head'),
    })
    _check(record['action'] in level_actions[record['level']])
    if record['level'] != 'source':
        _integer(record['issuer']['result_id'], 1, reader_budget=reader_budget)  # genuine allocating source preimage
        _check(subject['task_id'] != subject['source_task_id']
               and (subject['session_id'] is None or subject['session_id'] != subject['source_session_id']))


def _parent_edge(child: dict, parent: dict, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (child, parent)), reader_budget)
    _check(child['parent_id'] == parent['id'] and child['id'] != parent['id']
           and child['source_admission_id'] == parent['source_admission_id']
           and child['issuer'] == parent['issuer'])
    _before(parent, child, reader_budget=reader_budget)
    subject, original = (_reader_reserve(reader_budget, 96) or (child['subject'], parent['subject']))
    for key in ('org_slug', 'source_task_id', 'source_session_id'):
        _check(subject[key] == original[key])
    level, upstream = (_reader_reserve(reader_budget, 96) or (child['level'], parent['level']))
    allowed = (_reader_reserve(reader_budget, 576) or {'source': ('source',), 'attempt': ('source', 'attempt'),
               'control': ('source', 'control'), 'effect': ('attempt', 'effect')})
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
    radius, maximum = (_reader_reserve(reader_budget, 96) or (child['radius'], parent['radius']))
    for key in ('repository_id', 'object_format', 'base'):
        _check(radius[key] == maximum[key])
    _path_subset(radius['paths'], maximum['paths'], reader_budget=reader_budget)
    _check(_reader_all_true((command in maximum['commands'] for command in radius['commands']), budget=reader_budget))
    for key in ('store_owner', 'provider_repository', 'pr_number', 'expected_head'):
        if maximum[key] is not None:
            _check(radius[key] == maximum[key])
        elif level == upstream:
            _check(radius[key] is None)
    if maximum['head_rule'] == 'exact-subject@1':
        _check(radius['head_rule'] == 'exact-subject@1')
    if level == upstream:
        _check(radius['head_rule'] == maximum['head_rule'])


def read_saved_capability(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """Full bounded saved ancestry; queued/null SID is never an actor grant."""
    _org(expected_org_slug, reader_budget=budget)
    _capability(_json(raw, VALUE_LIMIT, reader_budget=budget), expected_org_slug, _Ancestry(), reader_budget=budget)
    return SavedCodingRecord(raw)


def read_saved_source_command(raw: bytes, *, expected_org_slug: str, budget=None) -> SavedCodingRecord:
    """D1 nonallocating selectors only; no supplied capability/proof/actor."""
    _org(expected_org_slug, reader_budget=budget)
    _check(type(raw) is bytes and len(raw) <= 65536)
    command = _closed(_json(raw, COMMAND_LIMIT, reader_budget=budget), 'format operation_key parent_id mapping action work', reader_budget=budget)
    _check(command['format'] == 'workflow-coding-source-command@1')
    _operation(command['operation_key'], reader_budget=budget)
    _token(command['parent_id'], reader_budget=budget)
    _check(command['action'] in ('import-release-coding', 'publish-coding-mapping', 'adopt-local-store'))
    mapping = _closed(command['mapping'], 'id generation digest', reader_budget=budget)
    _token(mapping['id'], reader_budget=budget)
    _integer(mapping['generation'], 1, reader_budget=budget)
    _digest(mapping['digest'], reader_budget=budget)
    work = _closed(command['work'], 'repository_id store_owner object_format base paths commands', reader_budget=budget)
    _repository(work['repository_id'], reader_budget=budget)
    _component(work['store_owner'], reader_budget=budget)
    _check(work['store_owner'] != 'founder' and work['object_format'] in ('sha1', 'sha256'))
    _oid(work['base'], work['object_format'], reader_budget=budget)
    _paths(work['paths'], reader_budget=budget)
    _commands(work['commands'], reader_budget=budget)
    return SavedCodingRecord(raw)
