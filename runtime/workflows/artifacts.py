"""Pure pinned artifact projections; no storage, access or execution authority.

Callers authenticate the supplied evidence and own error translation. Legacy
presentation stays in the document adapter; code descriptors are representation
only until a separately authorized immutable-object reader is mounted.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _dump as _reader_dump,
    _encode as _reader_encode,
    _json_dumps as _reader_json_dumps,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _reserve as _reader_reserve, _work as _reader_work,
    _sha256 as _reader_sha256, _fromhex as _reader_fromhex,
)

import base64
import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


CONTENT_LIMIT = 1048576
Token = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')]
SHA256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Revision = Annotated[StrictInt, Field(gt=0)]


def _canonical_bytes(value: object, *, budget=None) -> bytes:
    # Exact historical serializer, deliberately independent of the store module.
    _reader_reserve(budget, 0)
    return _reader_encode(_reader_json_dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False, budget=budget), 'utf8', budget=budget)


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class ArtifactIdentity(_FrozenRecord):
    org_namespace: Token
    instance_id: Token
    output_slot: Token


class ArtifactProvenance(_FrozenRecord):
    """References/snapshots of authenticated evidence, never a new attestation."""
    source_task_id: Token | None
    source_session_id: Token | None
    source_result_id: Revision | None
    author_principal: Token
    source_bytes: bytes
    contributor_bytes: tuple[bytes, ...]
    pins_bytes: bytes


class DocumentSubject(_FrozenRecord):
    kind: Literal['document']
    content_bytes: bytes = Field(min_length=1, max_length=CONTENT_LIMIT)
    content_digest: SHA256

    @model_validator(mode='after')
    def integrity(self) -> DocumentSubject:
        if hashlib.sha256(self.content_bytes).hexdigest() != self.content_digest:
            raise ValueError('artifact_document_digest_mismatch')
        self.content_bytes.decode('utf8')  # Verify without normalization/re-encoding.
        return self


class _Repository(_FrozenRecord):
    id: Token  # An opaque server-resolved binding key, never a path or URL.


class _Comparison(_FrozenRecord):
    base: str


class _Navigation(_FrozenRecord):
    branch: str | None = Field(max_length=256)
    pr_url: str | None = Field(max_length=2048)

    @model_validator(mode='after')
    def bounded_text(self) -> _Navigation:
        for value, limit in ((self.branch, 256), (self.pr_url, 2048)):
            if value is not None and (not value.strip() or '\x00' in value
                                      or len(value.encode('utf8')) > limit):
                raise ValueError('artifact_navigation_invalid')
        return self


class CodeChangeDescriptor(_FrozenRecord):
    format: Literal['workflow-code-change@1']
    kind: Literal['code-change']
    repository: _Repository
    object_format: Literal['sha1', 'sha256']
    head: str
    comparison: _Comparison | None
    navigation: _Navigation

    @model_validator(mode='after')
    def full_objects(self) -> CodeChangeDescriptor:
        size = 40 if self.object_format == 'sha1' else 64
        for oid in (self.head,) + (() if self.comparison is None else (self.comparison.base,)):
            if re.fullmatch(r'[0-9a-f]{' + str(size) + r'}', oid) is None:
                raise ValueError('artifact_commit_oid_invalid')
        return self

    @property
    def immutable_key(self) -> tuple[str, str, str, str | None]:
        return (self.repository.id, self.object_format, self.head,
                None if self.comparison is None else self.comparison.base)


def _closed_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('artifact_descriptor_duplicate_key')
        result[key] = value
    return result


def parse_code_change(raw: bytes) -> CodeChangeDescriptor:
    """Parse canonical descriptor bytes only; an absent object may still parse."""
    if type(raw) is not bytes or not 1 <= len(raw) <= CONTENT_LIMIT:
        raise ValueError('artifact_descriptor_bounds')
    descriptor = CodeChangeDescriptor.model_validate(
        json.loads(raw.decode('utf8'), object_pairs_hook=_closed_object))
    if _canonical_bytes(descriptor.model_dump()) != raw:
        raise ValueError('artifact_descriptor_not_canonical')
    return descriptor


class CodeChangeSubject(_FrozenRecord):
    kind: Literal['code-change']
    descriptor_bytes: bytes = Field(min_length=1, max_length=CONTENT_LIMIT)
    descriptor_digest: SHA256

    @model_validator(mode='after')
    def integrity(self) -> CodeChangeSubject:
        if hashlib.sha256(self.descriptor_bytes).hexdigest() != self.descriptor_digest:
            raise ValueError('artifact_descriptor_digest_mismatch')
        parse_code_change(self.descriptor_bytes)
        return self


class ArtifactRevision(_FrozenRecord):
    identity: ArtifactIdentity
    submission_id: Token
    revision: Revision
    subject: Annotated[DocumentSubject | CodeChangeSubject, Field(discriminator='kind')]
    provenance: ArtifactProvenance

    @property
    def review_key(self) -> tuple:
        subject = self.subject
        if isinstance(subject, DocumentSubject):
            key = (subject.content_digest,)
        else:
            key = (*parse_code_change(subject.descriptor_bytes).immutable_key, subject.descriptor_digest)
        return (self.identity.org_namespace, self.identity.instance_id, self.identity.output_slot,
                self.submission_id, self.revision, subject.kind, *key)


def document_revision(*, identity: ArtifactIdentity, submission_id: str, revision: int,
                      content_bytes: bytes, content_digest: str,
                      provenance: ArtifactProvenance, budget=None) -> ArtifactRevision:
    _reader_work((_reader_reserve(budget, 160) or (identity,submission_id,revision,content_bytes,content_digest,provenance)), budget, copies=2)
    return ArtifactRevision(identity=identity, submission_id=submission_id, revision=revision,
        subject=DocumentSubject(kind='document', content_bytes=content_bytes, content_digest=content_digest),
        provenance=provenance)


def code_change_revision(*, identity: ArtifactIdentity, submission_id: str, revision: int,
                         descriptor_bytes: bytes, descriptor_digest: str,
                         provenance: ArtifactProvenance) -> ArtifactRevision:
    """Representation only; neither parsing nor a descriptor digest resolves an object."""
    return ArtifactRevision(identity=identity, submission_id=submission_id, revision=revision,
        subject=CodeChangeSubject(kind='code-change', descriptor_bytes=descriptor_bytes,
                                 descriptor_digest=descriptor_digest), provenance=provenance)


def require_document_subject(artifact: ArtifactRevision, scope: dict, *, budget=None) -> None:
    """Match a closed, already selected legacy scope against stored subject facts."""
    _reader_work((_reader_reserve(budget, 96) or (artifact,scope)), budget, copies=2)
    expected_identity = ArtifactIdentity(org_namespace=scope['org_slug'],
        instance_id=scope['instance_id'], output_slot='output')
    # Construct a strict expected value too: True cannot compare equal to revision 1.
    expected = ArtifactRevision(identity=expected_identity, submission_id=scope['submission_id'],
        revision=scope['revision'], subject=artifact.subject, provenance=artifact.provenance)
    if (not isinstance(artifact.subject, DocumentSubject) or artifact.review_key != expected.review_key
            or artifact.subject.content_digest != scope['submission_digest']):
        raise ValueError('artifact_subject_mismatch')
    if review_family(scope, budget=budget) in (2, 3):
        expected_projection = (_reader_reserve(budget, 832) or dict(identity=_reader_dump(artifact.identity, budget=budget), submission_id=artifact.submission_id,
            revision=artifact.revision, kind='document', bytes_digest=artifact.subject.content_digest,
            subject=(_reader_reserve(budget, 192) or dict(content_digest=artifact.subject.content_digest))))
        if (_canonical_bytes(scope['artifact'], budget=budget) != _canonical_bytes(expected_projection, budget=budget)
                or scope['repository_resolution'] is not None):
            raise ValueError('artifact_subject_mismatch')


def document_from_handoff(row: dict, proof: dict, *, source_result_id: int | None = None,
                          contributors: tuple[dict, ...] = (), budget=None) -> ArtifactRevision:
    """Project caller-authenticated H data; an active author has no invented result."""
    _reader_work((_reader_reserve(budget, 128) or (row,proof,source_result_id,contributors)), budget, copies=2)
    return document_revision(identity=ArtifactIdentity(org_namespace=row['org_slug'],
        instance_id=row['instance_id'], output_slot='output'), submission_id=row['submission_id'],
        revision=row['revision'], content_bytes=row['candidate_bytes'], content_digest=row['candidate_digest'],
        provenance=ArtifactProvenance(source_task_id=row['source_task_id'],
            source_session_id=row['source_session_id'], source_result_id=source_result_id,
            author_principal=row['principal'], source_bytes=_canonical_bytes(proof['source_intent'], budget=budget),
            contributor_bytes=_reader_tuple((_canonical_bytes(item, budget=budget) for item in contributors), budget=budget),
            pins_bytes=_canonical_bytes(proof['pins'], budget=budget)), budget=budget)


def document_from_submission(submission: dict, *, org_namespace: str, pins: dict,
                             source_result_id: int | None = None,
                             contributors: tuple[dict, ...] = (), budget=None) -> ArtifactRevision:
    """Project a stored row; legacy TEXT result is retained, never cast to INTEGER."""
    _reader_work((_reader_reserve(budget, 144) or (submission,org_namespace,pins,source_result_id,contributors)), budget, copies=2)
    source = _reader_dict_pairs(((_reader_reserve(budget, 96) or (key,  submission[key] )) for key in ('source_task_id', 'source_session_id',
                                               'source_result_id', 'author_principal')), budget=budget)
    return document_revision(identity=ArtifactIdentity(org_namespace=org_namespace,
        instance_id=submission['instance_id'], output_slot='output'), submission_id=submission['id'],
        revision=submission['revision'], content_bytes=submission['submission_bytes'],
        content_digest=submission['submission_digest'], provenance=ArtifactProvenance(
            source_task_id=submission['source_task_id'], source_session_id=submission['source_session_id'],
            source_result_id=source_result_id, author_principal=submission['author_principal'],
            source_bytes=_canonical_bytes(source, budget=budget),
            contributor_bytes=_reader_tuple((_canonical_bytes(item, budget=budget) for item in contributors), budget=budget),
            pins_bytes=_canonical_bytes(pins, budget=budget)), budget=budget)


def document_review_scope(*, row: dict, proof: dict, context: dict, contract: dict, role: str,
                          request_id: str, round_id: str, binding: dict, budget=None) -> dict:
    """Historical document compatibility presentation, with byte-exact fields."""
    _reader_work((_reader_reserve(budget, 192) or (row,proof,context,contract,role,request_id,round_id,binding)), budget, copies=2)
    inputs = _reader_list((item for item in context['inputs'] if role in item['pin']['recipients']), budget=budget)
    return (_reader_reserve(budget, 3136) or dict(format='workflow-review-request@1', org_slug=row['org_slug'], instance_id=row['instance_id'],
                handoff_id=row['id'], submission_id=row['submission_id'], submission_digest=row['candidate_digest'],
                revision=1, round_id=round_id, request_id=request_id, role=role,
                kind=contract['role_kinds'][role], principal=binding['principal'], team=binding['team'],
                assignment_generation=1, template=proof['pins']['template'],
                activation=proof['pins']['activation'], authorization=proof['pins']['authorization']['id'],
                binding_snapshot_id=proof['pins']['binding']['id'], context_id=proof['pins']['context']['id'],
                output=contract['output'], outcomes=contract['outcomes'],
                purpose='Independently review the pinned immutable document; requests remain unsigned.',
                allowed_actions=(_reader_reserve(budget, 192) or ['collect-review']), inputs=inputs))


def document_review_brief(scope: dict, candidate: bytes, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 96) or (scope,candidate)), budget, copies=2)
    return ("Workflow document review. Independently assess only the pinned document and your authorized inputs. "
            "Do not delegate, implement code, merge, approve planning input, or sign on behalf of another principal. "
            "Report your findings through your actual task/session completion callback as the final session action. "
            "This request and its callback remain unsigned; genuine review receipts and joins are later units.\n\n"
            + _reader_decode(_canonical_bytes((_reader_reserve(budget, 320) or dict(scope=scope,
                submission_bytes_base64=_reader_decode(_reader_b64encode(candidate, budget=budget), 'ascii', budget=budget))), budget=budget), budget=budget))


def context_contract(context: dict, *, budget=None) -> dict:
    """Only matched explicit original-author context families are supported."""
    _reader_reserve(budget, 0)
    _reader_work(context.get('format'), budget)
    if context.get('format') == 'workflow-document-revision-context@1':
        contract = context['contract']
        if contract['format'] in ('workflow-document-contract@3', 'workflow-artifact-contract@4'):
            return contract
        raise ValueError('artifact_context_mismatch')
    if context.get('format') == 'workflow-initial-draft-context@4':
        contract = context['artifact_contract']
        if (contract['format'] != 'workflow-artifact-contract@4'
                or contract['output']['accepted_kinds'] != (_reader_reserve(budget, 192) or ['document'])
                or context['repository_binding'] is not None):
            raise ValueError('artifact_context_mismatch')
        return contract
    if context.get('format') == 'workflow-initial-draft-context@3':
        contract = context['document_contract']
        if contract['format'] == 'workflow-document-contract@3':
            return contract
    raise ValueError('artifact_context_mismatch')


def review_family(scope: dict, *, budget=None) -> int:
    """No unknown scope can inherit a historical proof interpretation."""
    _reader_reserve(budget, 0)
    _reader_work(scope.get('format'), budget)
    version = (_reader_reserve(budget, 448) or {'workflow-review-request@1': 1, 'workflow-review-request@2': 2, 'workflow-review-request@3': 3}).get(scope.get('format'))
    if version is None:
        raise ValueError('artifact_scope_family_unknown')
    return version


def artifact_review_scope(*, budget=None, **values: object) -> dict:
    _reader_work((_reader_reserve(budget, 80) or (values,)), budget, copies=2)
    row, proof, contract = (_reader_reserve(budget, 112) or (values['row'], values['proof'], values['contract']))
    if (contract['format'] != 'workflow-artifact-contract@4'
            or contract['output']['accepted_kinds'] != (_reader_reserve(budget, 192) or ['document'])
            or proof['format'] != 'workflow-author-handoff@2'
            or proof['repository_resolution'] is not None):
        raise ValueError('artifact_scope_mismatch')
    artifact = document_from_handoff(row, proof, budget=budget)
    # Explicit new presentation; retain the entire legacy serializer unchanged.
    scope = document_review_scope(**values, budget=budget)
    result = dict(scope, format='workflow-review-request@2',
        purpose='Independently review the pinned immutable artifact; requests remain unsigned.',
        repository_resolution=None, artifact=(_reader_reserve(budget, 832) or dict(identity=_reader_dump(artifact.identity, budget=budget),
            submission_id=artifact.submission_id, revision=artifact.revision, kind='document',
            bytes_digest=artifact.subject.content_digest,
            subject=(_reader_reserve(budget, 192) or dict(content_digest=artifact.subject.content_digest)))))
    if len(_canonical_bytes(result, budget=budget)) > CONTENT_LIMIT:
        raise ValueError('artifact_scope_bounds')
    return result


def artifact_review_brief(scope: dict, candidate: bytes, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 96) or (scope,candidate)), budget, copies=2)
    if review_family(scope, budget=budget) != 2:
        raise ValueError('artifact_scope_mismatch')
    return ("Workflow artifact review. Independently assess the pinned artifact and only your authorized inputs. "
            "Do not delegate, implement code, merge, deploy, or sign for another principal. "
            "Use the matched workflow-review-stage-command@2 only when an authorized stage adapter is available. "
            "Stage findings under one stable operation key before the actual final task/session callback; "
            "stage and callback remain unsigned until genuine successful/quiescent receipt closure. "
            "Report an unavailable stage adapter honestly.\n\n"
            + _reader_decode(_canonical_bytes((_reader_reserve(budget, 320) or dict(scope=scope,
                submission_bytes_base64=_reader_decode(_reader_b64encode(candidate, budget=budget), 'ascii', budget=budget))), budget=budget), budget=budget))


def decode_artifact_json(raw: bytes) -> object:
    """Closed new-family transport JSON; canonicalization belongs to the writer."""
    if type(raw) is not bytes or not raw or raw.startswith(b'\xef\xbb\xbf'):
        raise ValueError('artifact_json_invalid')
    def constant(value: str) -> None:
        raise ValueError('artifact_json_invalid')
    return json.loads(raw.decode('utf8'), object_pairs_hook=_closed_object, parse_constant=constant)
