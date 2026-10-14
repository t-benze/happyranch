"""K1 repository records and unavailable current-use boundary.

Saved readers establish historical integrity only. They perform no filesystem
I/O and confer neither adoption, current disclosure nor execution authority.
The private object implementation is deliberately not called by this mount:
K2b's authenticated adoption/authority producer does not exist yet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from runtime.workflows.artifacts import CodeChangeDescriptor, SHA256, Token

SCOPE_LIMIT = 1048576
PRESENTATION_LIMIT = 262144
COMMIT_LIMIT = 65536
TREE_LIMIT = 262144
BLOB_LIMIT = 65536
OBJECT_LIMIT = 4096
DEPTH_LIMIT = 64
PATH_LIMIT = 512
READ_LIMIT = 8388608
COPY_LIMIT = 67108864
DEADLINE_SECONDS = 5.0

Component = Annotated[str, Field(min_length=1, max_length=128,
    pattern=r'^[A-Za-z0-9_][A-Za-z0-9._-]*$')]
Role = Annotated[str, Field(min_length=1, max_length=63, pattern=r'^[a-z][a-z0-9-]*$')]
RepositoryId = Annotated[str, Field(pattern=r'^workflow-repository:[0-9a-f]{64}$')]


class WorkflowRepositoryError(ValueError):
    """Category-only error; private paths, config and Git stderr stay private."""

    def __init__(self, category: str, *, execution_unknown: bool = False,
                 process_id: int | None = None, private_storage: str | None = None) -> None:
        self.code = 'workflow_repository_' + category
        # Internal ownership facts, never public DTO fields or proof of safety.
        self.execution_unknown = execution_unknown
        self.process_id = process_id
        self.private_storage = private_storage
        super().__init__(self.code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('repository_duplicate_key')
        result[key] = value
    return result


def _saved(raw: bytes, model: type[BaseModel], limit: int) -> BaseModel:
    if type(raw) is not bytes or not 1 <= len(raw) <= limit:
        raise ValueError('repository_record_bounds')
    def constant(value: str) -> None:
        raise ValueError('repository_nonfinite')
    record = model.model_validate(json.loads(raw.decode('utf8'),
        object_pairs_hook=_pairs, parse_constant=constant))
    if _canonical(record.model_dump()) != raw:
        raise ValueError('repository_record_not_canonical')
    return record


def _base64(value: str, limit: int) -> bytes:
    if len(value) > 4 * ((limit + 2) // 3):
        raise ValueError('repository_base64_bounds')
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit or base64.b64encode(raw).decode('ascii') != value:
        raise ValueError('repository_base64_invalid')
    return raw


def _oid(value: str, object_format: str) -> None:
    size = 40 if object_format == 'sha1' else 64
    if re.fullmatch(r'[0-9a-f]{' + str(size) + r'}', value) is None:
        raise ValueError('repository_oid_invalid')


def _object_hash(kind: str, raw: bytes, object_format: str) -> str:
    return hashlib.new(object_format, kind.encode('ascii') + b' ' +
                       str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def _path(value: str) -> bytes:
    raw = value.encode('utf8')
    parts = value.split('/')
    if (not raw or len(raw) > 4096 or len(parts) > DEPTH_LIMIT
            or any(part in ('', '.', '..') or part.casefold() == '.git' for part in parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value):
        raise ValueError('repository_path_invalid')
    return raw


def _text(raw: bytes) -> None:
    text = raw.decode('utf8')
    if any(ord(c) < 32 and c not in '\t\r\n' for c in text) or '\x7f' in text:
        raise ValueError('repository_binary_content')


class _Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class RepositoryAuthority(_Record):
    namespace: str = Field(min_length=1, max_length=256)
    generation: StrictInt = Field(gt=0)
    snapshot_digest: SHA256

    @model_validator(mode='after')
    def text(self) -> RepositoryAuthority:
        if len(self.namespace.encode('utf8')) > 256 or any(ord(c) < 32 for c in self.namespace):
            raise ValueError('repository_authority_invalid')
        return self


class RepositoryRecipient(_Record):
    role: Role
    kind: Literal['agent', 'human']
    principal: Token
    team: Component | None

    @model_validator(mode='after')
    def identity(self) -> RepositoryRecipient:
        if ((self.kind == 'human' and (self.principal != 'founder' or self.team is not None))
                or (self.kind == 'agent' and (self.principal == 'founder' or self.team is None))):
            raise ValueError('repository_recipient_invalid')
        return self


class RepositoryBinding(_Record):
    format: Literal['workflow-repository-binding@1']
    action: Literal['read-repository-artifact']
    org_slug: Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,62}$')]
    repository_id: RepositoryId
    repo_key: Component
    origin_digest: SHA256
    store_owner: Component
    authority: RepositoryAuthority
    recipients: list[RepositoryRecipient] = Field(min_length=1, max_length=4)
    reader: Literal['local-git-objects@1']

    @model_validator(mode='after')
    def identity(self) -> RepositoryBinding:
        expected = 'workflow-repository:' + _digest(_canonical(
            [self.org_slug, self.repo_key, self.origin_digest]))
        if self.repository_id != expected:
            raise ValueError('repository_binding_identity')
        if (len({r.role for r in self.recipients}) != len(self.recipients)
                or len({(r.kind, r.principal) for r in self.recipients}) != len(self.recipients)
                or sum(r.kind == 'human' for r in self.recipients) > 1
                or sum(r.kind == 'agent' and r.principal == self.store_owner
                       for r in self.recipients) != 1):
            raise ValueError('repository_binding_recipients')
        return self


class RepositoryStoreIdentity(_Record):
    repo_device: StrictInt = Field(ge=0)
    repo_inode: StrictInt = Field(ge=0)
    git_device: StrictInt = Field(ge=0)
    git_inode: StrictInt = Field(ge=0)
    objects_device: StrictInt = Field(ge=0)
    objects_inode: StrictInt = Field(ge=0)
    config_digest: SHA256


class RepositoryAdoption(_Record):
    format: Literal['workflow-repository-adoption@1']
    operation_id: Token
    store_identity_digest: SHA256
    adopted_by: Token
    authority: RepositoryAuthority
    policy: Literal['adopted-local-store@1']


class RepositoryIssuer(_Record):
    kind: Literal['agent']
    principal: Token
    task_id: Token
    session_id: Token
    result_id: StrictInt | None = Field(gt=0)

    @model_validator(mode='after')
    def agent(self) -> RepositoryIssuer:
        if self.principal == 'founder':
            raise ValueError('repository_issuer_invalid')
        return self


class RepositoryAdoptionChecks(_Record):
    primary_store: StrictBool
    no_follow: StrictBool
    ordinary_git_directory: StrictBool
    no_alternates: StrictBool
    no_promisor: StrictBool
    no_shallow: StrictBool
    no_replace_or_graft: StrictBool
    no_helpers: StrictBool

    @model_validator(mode='after')
    def complete(self) -> RepositoryAdoptionChecks:
        if any(value is not True for value in self.model_dump().values()):
            raise ValueError('repository_adoption_incomplete')
        return self


class RepositoryAdoptionProof(_Record):
    """Saved server observation grammar; constructing this is not adopting."""
    format: Literal['workflow-repository-adoption-proof@1']
    repository_id: RepositoryId
    operation_id: Token
    issuer: RepositoryIssuer
    authority: RepositoryAuthority
    store_identity: RepositoryStoreIdentity
    configured_origin_digest: SHA256
    observed_origin_digest: SHA256
    object_format: Literal['sha1', 'sha256']
    policy: Literal['adopted-local-store@1']
    checks: RepositoryAdoptionChecks
    observed_at: str = Field(min_length=20, max_length=27)

    @model_validator(mode='after')
    def observation(self) -> RepositoryAdoptionProof:
        if self.configured_origin_digest != self.observed_origin_digest:
            raise ValueError('repository_adoption_origin')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z', self.observed_at) is None:
            raise ValueError('repository_adoption_timestamp')
        datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
        return self


class RepositorySide(_Record):
    mode: Literal['100644', '100755']
    blob_oid: str
    bytes_base64: str = Field(max_length=4 * ((BLOB_LIMIT + 2) // 3))


class RepositoryEntry(_Record):
    path: str = Field(min_length=1, max_length=4096)
    before: RepositorySide | None
    after: RepositorySide | None


class RepositoryPresentation(_Record):
    format: Literal['workflow-repository-presentation@1']
    repository_id: RepositoryId
    object_format: Literal['sha1', 'sha256']
    head: str
    base: str | None
    entries: list[RepositoryEntry] = Field(max_length=PATH_LIMIT)

    @model_validator(mode='after')
    def integrity(self) -> RepositoryPresentation:
        _oid(self.head, self.object_format)
        if self.base is not None:
            _oid(self.base, self.object_format)
        paths = [_path(entry.path) for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError('repository_presentation_order')
        aggregate = 0
        for entry in self.entries:
            if ((entry.before is None and entry.after is None)
                    or (self.base is None and (entry.before is not None or entry.after is None))
                    or (entry.before is not None and entry.before == entry.after)):
                raise ValueError('repository_presentation_change')
            for side in (entry.before, entry.after):
                if side is None:
                    continue
                _oid(side.blob_oid, self.object_format)
                raw = _base64(side.bytes_base64, BLOB_LIMIT)
                _text(raw)
                aggregate += len(raw)
                if _object_hash('blob', raw, self.object_format) != side.blob_oid:
                    raise ValueError('repository_blob_hash')
        # Also disallow file/directory conflicts in either complete manifest side.
        for which in ('before', 'after'):
            selected = {e.path for e in self.entries if getattr(e, which) is not None}
            for path in selected:
                pieces = path.split('/')
                if any('/'.join(pieces[:i]) in selected for i in range(1, len(pieces))):
                    raise ValueError('repository_path_conflict')
        if aggregate > READ_LIMIT or len(_canonical(self.model_dump())) > PRESENTATION_LIMIT:
            raise ValueError('repository_presentation_bounds')
        return self


class RepositoryResolution(_Record):
    binding: RepositoryBinding
    binding_digest: SHA256
    presentation_bytes_base64: str = Field(max_length=4 * ((PRESENTATION_LIMIT + 2) // 3))
    presentation_digest: SHA256

    @model_validator(mode='after')
    def integrity(self) -> RepositoryResolution:
        raw = _base64(self.presentation_bytes_base64, PRESENTATION_LIMIT)
        presentation = read_saved_presentation(raw)
        if (_digest(_canonical(self.binding.model_dump())) != self.binding_digest
                or _digest(raw) != self.presentation_digest
                or presentation.repository_id != self.binding.repository_id
                or len(_canonical(self.model_dump())) > SCOPE_LIMIT):
            raise ValueError('repository_resolution_integrity')
        return self


def read_saved_binding(raw: bytes) -> RepositoryBinding:
    return _saved(raw, RepositoryBinding, SCOPE_LIMIT)


def read_saved_adoption(raw: bytes) -> RepositoryAdoption:
    return _saved(raw, RepositoryAdoption, SCOPE_LIMIT)


def read_saved_adoption_proof(raw: bytes) -> RepositoryAdoptionProof:
    return _saved(raw, RepositoryAdoptionProof, SCOPE_LIMIT)


def read_saved_presentation(raw: bytes) -> RepositoryPresentation:
    return _saved(raw, RepositoryPresentation, PRESENTATION_LIMIT)


def read_saved_resolution(raw: bytes, *, descriptor: CodeChangeDescriptor,
                          binding_bytes: bytes) -> RepositoryResolution:
    resolution = _saved(raw, RepositoryResolution, SCOPE_LIMIT)
    binding = read_saved_binding(binding_bytes)
    presentation = read_saved_presentation(_base64(
        resolution.presentation_bytes_base64, PRESENTATION_LIMIT))
    if (resolution.binding != binding or presentation.repository_id != descriptor.repository.id
            or presentation.object_format != descriptor.object_format
            or presentation.head != descriptor.head
            or presentation.base != (None if descriptor.comparison is None else descriptor.comparison.base)):
        raise ValueError('repository_resolution_subject')
    return resolution


def _match_adoption(binding: RepositoryBinding, adoption: RepositoryAdoption,
                    proof: RepositoryAdoptionProof) -> None:
    """Historical consistency only, never verifies authority or admits access."""
    if (proof.repository_id != binding.repository_id
            or adoption.operation_id != proof.operation_id
            or adoption.adopted_by != proof.issuer.principal
            or adoption.authority != proof.authority
            or adoption.store_identity_digest != _digest(_canonical(proof.store_identity.model_dump()))
            or binding.origin_digest != proof.configured_origin_digest):
        raise ValueError('repository_adoption_identity')


def validate_saved_adoption(*, binding_bytes: bytes, adoption_bytes: bytes,
                            proof_bytes: bytes) -> None:
    _match_adoption(read_saved_binding(binding_bytes), read_saved_adoption(adoption_bytes),
                    read_saved_adoption_proof(proof_bytes))


class WorkflowRepositoryArtifactService:
    """Inert OrgState mount. No switch, injected verifier or raw-reader method.

    K2b must supply a genuine selected admission producer before implementing
    this operation. Its future sequence is authenticated org/principal/session,
    relational selection and current capsule/adoption/authority BEFORE private
    decode/probe; awaited outside-writer resolution; final selected admission
    revalidation BEFORE any disclosure. Saved receipts cannot skip either gate.
    """
    __slots__ = ()

    async def resolve(self, *, session_id: str, selected_id: str) -> RepositoryResolution:
        # Never inspect even these selectors without the genuine producer.
        raise WorkflowRepositoryError('reader_unavailable')
