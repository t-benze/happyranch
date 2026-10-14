"""Unmounted K2a2a saved coding grammars, never current-use authorization.

These readers do no store, roster, object, provider or network I/O. A parsed
record is no authentic issuer, active grant, admission or valid native/J graph.
Complete captures and issuance deliberately refuse the unresolved nested
serialization documented by ``CodingSavedDependencyError``. There is no
resolver, trusted flag or permissive opaque-authority acceptance path.
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

from runtime.workflows.artifacts import Token
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


class CodingSavedDependencyError(CodingSavedRecordError):
    """The ONE unresolved coding authority/issuance serialization delta.

    B2 specifies capture keys, but not the encoding of profile_evidence. D1
    specifies issuance keys, but not admission or the root issuer encoding.
    Native AuthorityAdmissionCapture, workflow_admission_records and Founder
    provenance exist; none specifies that persisted coding mapping. These paths
    cannot accept arbitrary dictionaries or a guessed reduced serialization.
    """

    def __init__(self) -> None:
        super().__init__('coding_saved_authority_serialization_unavailable')


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


def _capture(value: object, expected_org_slug: str) -> None:
    capture = _closed(value, 'pin admission_record_id snapshot_bytes_base64 profile_evidence grant_bytes_base64 grant_digest')
    pin = _pin(capture['pin'], expected_org_slug)
    _token(capture['admission_record_id'])
    snapshot_raw = _b64(capture['snapshot_bytes_base64'], VALUE_LIMIT)
    snapshot = _json(snapshot_raw, VALUE_LIMIT)
    _check(_hash(snapshot_raw) == pin.snapshot_digest and snapshot.get('org_slug') == expected_org_slug)
    grant = _b64(capture['grant_bytes_base64'], VALUE_LIMIT)
    _digest(capture['grant_digest'])
    _check(_hash(grant) == capture['grant_digest'])
    _json(grant, VALUE_LIMIT)
    # No profile_evidence, grant or snapshot is accepted as an opaque dict.
    # Existing snapshot validation is not a coding grant/profile serializer.
    raise CodingSavedDependencyError()


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
    """Complete envelope/command/pairing, then refuse missing authority grammar."""
    _org(expected_org_slug)
    record = _closed(_json(raw, VALUE_LIMIT),
                     'format command_bytes_base64 command_digest issuer authority adoptions mapping created_at')
    _check(record['format'] == 'workflow-coding-mapping-record@1')
    _, command_value = _command_bytes(record)
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
    _capture(capture, expected_org_slug)
    # _capture always refuses; no digest-only/reduced envelope is returned.
    raise CodingSavedDependencyError()


def _setup(value: object, expected_org_slug: str) -> dict:
    record = _closed(value, 'format id org_slug repository_id manager_selector actions scope source_rule base_rule authority created_at')
    _check(record['format'] == 'workflow-coding-setup-authority@1'
           and record['org_slug'] == expected_org_slug)
    _token(record['id'])
    _setup_scope({key: record[key] for key in (
        'repository_id', 'manager_selector', 'actions', 'scope', 'source_rule', 'base_rule')})
    _timestamp(record['created_at'])
    return record


def read_saved_setup(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    _org(expected_org_slug)
    record = _setup(_json(raw, VALUE_LIMIT), expected_org_slug)
    _capture(record['authority'], expected_org_slug)
    raise CodingSavedDependencyError()


def read_saved_issuance(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Retain issue/withdraw distinction and original bytes; no inactive replay."""
    _org(expected_org_slug)
    record = _closed(_json(raw, VALUE_LIMIT),
                     'format command_bytes_base64 command_digest issuer mode setup admission created_at')
    _check(record['format'] == 'workflow-coding-capability-issuance@1')
    _, command_value = _command_bytes(record)
    command = _setup_command(command_value, expected_org_slug)
    _check(record['mode'] == command['mode'])
    _timestamp(record['created_at'])
    if record['mode'] == 'withdraw':
        _check(record['setup'] is None)
    else:
        setup = _setup(record['setup'], expected_org_slug)
        _check(all(setup[key] == item for key, item in command['scope'].items())
               and setup['created_at'] == record['created_at'])
    # No guess that Founder.provenance, a native row or a handoff capture is
    # the approved coding issuer/admission wire value, even on withdrawal.
    raise CodingSavedDependencyError()


def read_saved_capability(raw: bytes, *, expected_org_slug: str) -> SavedCodingRecord:
    """Source/queued/SID-bound/control/effect grammar; no capability is active."""
    _org(expected_org_slug)
    record = _closed(_json(raw, VALUE_LIMIT),
                     'format id parent_id level issuer subject action radius authority source_admission_id created_at')
    _check(record['format'] == 'workflow-coding-capability@1')
    _token(record['id'])
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
    if action in ('review-code-change', 'verify-code-change', 'observe-coding-ci', 'merge-coding-approved-head',
                  'read-repository-artifact'):
        _check(radius['head_rule'] == 'exact-subject@1')
    # The pending SID is retained as null, never replaced by issuer/source SID.
    # Relational admission, role/action intersections and parent subset checks
    # cannot be established here. The missing capture grammar refuses them all.
    _capture(record['authority'], expected_org_slug)
    raise CodingSavedDependencyError()
