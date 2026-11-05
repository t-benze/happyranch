"""Human receipts, current reviewer staging and selected-recipient reads."""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool, StrictInt, ValidationError, field_validator

from runtime.workflows.activation import (AbstractRole, ActivatedTemplate, ClosedRecord,
    DocumentTaskInput, DocumentThreadInput, SHA256, Token)


from runtime.daemon.startup_recovery import StartupUnavailable
from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_submissions import ERRORS, IDENTITY_KEYS, READ_RESPONSES, TOKEN_SCHEMA, agent_transport, fail, proof_schema, query_values, raise_error, raise_read_error, sqlite_error_types
from runtime.workflows.review_receipts import HumanReviewCommand, ReviewReceiptProjection, ArtifactHumanReviewCommand, ArtifactReviewReceiptProjection, RevisionHumanReviewCommand, RevisionReviewReceiptProjection, parse_human_review, recipient_receipt
from runtime.workflows.templates import WorkflowTemplatePrincipal
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.draft_dispatch import DraftOwnershipError
from runtime.workflows.review_dispatch import ReviewStageCommand, ArtifactReviewStageCommand, RevisionReviewStageCommand, parse_stage

router = APIRouter()


class ReviewActivation(ClosedRecord):
    id: Token
    revision: StrictInt = Field(ge=1, le=1)
    request_digest: SHA256


class ReviewOutput(ClosedRecord):
    primitive: Literal['immutable-document-revision']
    description: str = Field(min_length=1, max_length=2000)


class ReviewInput(ClosedRecord):
    pin: Annotated[DocumentTaskInput | DocumentThreadInput, Field(discriminator='kind')]
    bytes_base64: str


class ReviewRequestScope(ClosedRecord):
    format: Literal['workflow-review-request@1']
    org_slug: str = Field(min_length=1, max_length=40, pattern=r'^[a-z0-9-]+$')
    instance_id: Token
    handoff_id: Token
    submission_id: Token
    submission_digest: SHA256
    revision: StrictInt = Field(ge=1, le=1)
    round_id: Token
    request_id: Token
    role: AbstractRole
    kind: Literal['agent', 'human']
    principal: Token
    team: str | None = Field(max_length=63)
    assignment_generation: StrictInt = Field(ge=1, le=1)
    template: ActivatedTemplate
    activation: ReviewActivation
    authorization: str = Field(min_length=1, max_length=256)
    binding_snapshot_id: str = Field(min_length=1, max_length=256)
    context_id: str = Field(min_length=1, max_length=256)
    output: ReviewOutput
    outcomes: list[Literal['approved', 'changes_requested']] = Field(min_length=1, max_length=2)
    purpose: Literal['Independently review the pinned immutable document; requests remain unsigned.']
    allowed_actions: list[Literal['collect-review']] = Field(min_length=1, max_length=1)
    inputs: list[ReviewInput] = Field(max_length=32)


class ArtifactReviewOutput(ClosedRecord):
    primitive: Literal['immutable-artifact-revision']
    description: str = Field(min_length=1, max_length=2000)
    accepted_kinds: list[Literal['document']] = Field(min_length=1, max_length=1)


class ReviewArtifactIdentity(ClosedRecord):
    org_namespace: Token
    instance_id: Token
    output_slot: Literal['output']


class ReviewDocumentSubject(ClosedRecord):
    content_digest: SHA256


class ReviewArtifact(ClosedRecord):
    identity: ReviewArtifactIdentity
    submission_id: Token
    revision: StrictInt = Field(ge=1, le=1)
    kind: Literal['document']
    bytes_digest: SHA256
    subject: ReviewDocumentSubject


class ArtifactReviewRequestScope(ReviewRequestScope):
    format: Literal['workflow-review-request@2']
    output: ArtifactReviewOutput
    purpose: Literal['Independently review the pinned immutable artifact; requests remain unsigned.']
    artifact: ReviewArtifact
    repository_resolution: None


class RevisionReviewArtifact(ReviewArtifact):
    revision: StrictInt = Field(ge=2, le=2)


class RevisionReviewRequestScope(ClosedRecord):
    format: Literal['workflow-review-request@3']
    org_slug: str = Field(min_length=1, max_length=40, pattern=r'^[a-z0-9-]+$')
    instance_id: Token
    revision_operation_id: Token
    submission_id: Token
    submission_digest: SHA256
    revision: StrictInt = Field(ge=2, le=2)
    round_id: Token
    request_id: Token
    role: AbstractRole
    kind: Literal['agent', 'human']
    principal: Token
    team: str | None = Field(max_length=63)
    assignment_generation: StrictInt = Field(ge=2)
    template: ActivatedTemplate
    activation: ReviewActivation
    authorization: str = Field(min_length=1, max_length=256)
    binding_snapshot_id: str = Field(min_length=1, max_length=256)
    context_id: str = Field(min_length=1, max_length=256)
    output: ReviewOutput | ArtifactReviewOutput
    outcomes: list[Literal['approved', 'changes_requested']] = Field(min_length=1, max_length=2)
    purpose: Literal['Independently review the pinned immutable artifact; requests remain unsigned.']
    allowed_actions: list[Literal['collect-review']] = Field(min_length=1, max_length=1)
    inputs: list[ReviewInput] = Field(max_length=32)

    artifact: RevisionReviewArtifact
    repository_resolution: None


class ReviewRequestBase(ClosedRecord):
    format: Literal['workflow-review-request-receipt@1']
    request_id: Token
    scope: ReviewRequestScope
    request_scope_digest: SHA256
    reconciliation_required: StrictBool
    submission_bytes_base64: str = Field(min_length=4, max_length=1398104)


class PendingReviewRequest(ReviewRequestBase):
    status: Literal['pending']
    signed: Literal[False]
    pending: Literal[True]
    receipt: None


class SignedReviewRequest(ReviewRequestBase):
    status: Literal['approved', 'changes_requested']
    signed: Literal[True]
    pending: Literal[False]
    receipt: ReviewReceiptProjection


class PendingArtifactReviewRequest(PendingReviewRequest):
    format: Literal['workflow-review-request-receipt@2']
    scope: ArtifactReviewRequestScope


class SignedArtifactReviewRequest(SignedReviewRequest):
    format: Literal['workflow-review-request-receipt@2']
    scope: ArtifactReviewRequestScope
    receipt: ArtifactReviewReceiptProjection


class PriorReview(ClosedRecord):
    request_id: Token
    request_scope_digest: SHA256
    submission_id: Token
    submission_digest: SHA256
    submission_bytes_base64: str = Field(min_length=4, max_length=1398104)
    receipt: ReviewReceiptProjection | ArtifactReviewReceiptProjection


class PendingRevisionReviewRequest(PendingReviewRequest):
    format: Literal['workflow-review-request-receipt@3']
    scope: RevisionReviewRequestScope
    prior_review: PriorReview


class SignedRevisionReviewRequest(SignedReviewRequest):
    format: Literal['workflow-review-request-receipt@3']
    scope: RevisionReviewRequestScope
    receipt: RevisionReviewReceiptProjection
    prior_review: PriorReview


ReviewRequestReceipt = (
    Annotated[PendingReviewRequest | SignedReviewRequest, Field(discriminator='status')]
    | Annotated[PendingArtifactReviewRequest | SignedArtifactReviewRequest, Field(discriminator='status')]
    | Annotated[PendingRevisionReviewRequest | SignedRevisionReviewRequest, Field(discriminator='status')]
)


class HumanReviewReceipt(ClosedRecord):
    format: Literal['workflow-human-review-receipt@1']
    receipt: ReviewReceiptProjection


class ArtifactHumanReviewReceipt(ClosedRecord):
    format: Literal['workflow-human-review-receipt@2']
    receipt: ArtifactReviewReceiptProjection


class RevisionHumanReviewReceipt(ClosedRecord):
    format: Literal['workflow-human-review-receipt@3']
    receipt: RevisionReviewReceiptProjection


HumanReceipt = HumanReviewReceipt | ArtifactHumanReviewReceipt | RevisionHumanReviewReceipt


def human_command_schema() -> dict:
    return {'anyOf': [HumanReviewCommand.model_json_schema(), ArtifactHumanReviewCommand.model_json_schema(), RevisionHumanReviewCommand.model_json_schema()]}


class ReviewErrorDetail(ClosedRecord):
    code: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=256)
    required_action: str | None = Field(default=None, max_length=2000)


class ReviewError(ClosedRecord):
    detail: ReviewErrorDetail


REVIEW_RESPONSES = {status: dict(spec, model=ReviewError) for status, spec in READ_RESPONSES.items()}


def human_principal(org: OrgDep, has_bearer: bool) -> WorkflowTemplatePrincipal:
    _require_human(has_bearer)
    return WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug='',
                                             revalidate=lambda: _require_human(has_bearer))


def decode_human_command(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate')
            value[key] = item
        return value
    try:
        if not 1 <= len(raw) <= 2097152 or raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError('size')
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs)
        if isinstance(value, dict) and set(value) & (IDENTITY_KEYS | {'proof', 'proof_bytes', 'actor_proof'}):
            fail('body_identity_rejected', 403)
        return parse_human_review(value)
    except (ValueError, UnicodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail={'code': 'workflow_human_review_invalid_request'}) from exc


@router.post('/workflows/reviews/human-receipts', response_model=HumanReceipt,
             responses=REVIEW_RESPONSES,
             openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {
                 'schema': human_command_schema()}}}})
async def sign_human_review(org: OrgDep, request: Request,
                            has_bearer: bool = optional_bearer()) -> dict:
    principal = human_principal(org, has_bearer)
    if request.query_params:
        fail('workflow_human_review_invalid_request', 422)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 2097152:
            fail('workflow_human_review_invalid_request', 422)
        raw.extend(chunk)
    command = decode_human_command(bytes(raw))
    try:
        receipt = await org.workflow_review_receipts.admit_human(principal=principal, command=command)
        projection = recipient_receipt(receipt)
        return dict(format=projection['format'].replace('workflow-review-receipt', 'workflow-human-review-receipt'), receipt=projection)
    except ERRORS as exc:
        if exc.code == 'workflow_human_review_operation_conflict':
            fail(exc.code, 409)
        if exc.code in {'template_version_not_found', 'workflow_activation_not_found',
                        'workflow_activation_invalid_request'}:
            # These retained readers run only after selected-human/current-grant
            # admission; caller command parsing and invisible selection are separate.
            fail('workflow_review_receipt_storage_corrupt', 500)
        raise_error(exc)
    except sqlite_error_types() as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_review_receipt_storage_unavailable'}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_review_receipt_storage_corrupt'}) from exc


@router.get('/workflows/reviews/requests/{request_id}', response_model=ReviewRequestReceipt,
            responses=REVIEW_RESPONSES,
            openapi_extra={'x-query-proof-schema': proof_schema({'session_id': TOKEN_SCHEMA},
                             {'proof_kind': {'type': 'string', 'const': 'human'}})})
async def get_review_request(org: OrgDep, request_id: str, request: Request,
                              session_id: str | None = Query(default=None), proof_kind: str | None = Query(default=None),
                              has_bearer: bool = optional_bearer()) -> dict:
    values = query_values(request, {'session_id', 'proof_kind'})
    if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', request_id) is None:
        fail('workflow_author_handoff_invalid_request', 422)
    principal = None
    if set(values) == {'proof_kind'} and proof_kind == 'human':
        principal = human_principal(org, has_bearer)
        human = True
    elif set(values) == {'session_id'}:
        agent_transport(request, has_bearer)
        human = False
    else:
        fail('workflow_author_handoff_invalid_request', 422)
    try:
        return await org.workflow_submissions.get_request(request_id=request_id, session_id=session_id,
                                                          human=human, principal=principal)
    except ERRORS as exc:
        raise_read_error(exc)
    except sqlite_error_types() as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_author_handoff_storage_unavailable'}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_author_handoff_storage_corrupt'}) from exc


class ReviewStageAck(ClosedRecord):
    format: Literal['workflow-review-stage-ack@1']
    stage_event_id: Token
    request_digest: SHA256
    replayed: StrictBool
    signed: Literal[False]

    @field_validator('signed', mode='before')
    @classmethod
    def unsigned_boolean(cls, value: object) -> object:
        if value is not False:
            raise ValueError('unsigned Boolean required')
        return value


class ArtifactReviewStageAck(ReviewStageAck):
    format: Literal['workflow-review-stage-ack@2']


class RevisionReviewStageAck(ReviewStageAck):
    format: Literal['workflow-review-stage-ack@3']


StageAck = ReviewStageAck | ArtifactReviewStageAck | RevisionReviewStageAck


class ReviewStageErrorDetail(ClosedRecord):
    code: Literal[
        'auth_path_conflict', 'bearer_not_accepted', 'body_identity_rejected',
        'unknown_session', 'cross_org_session', 'session_not_current', 'recovery_purpose_forbidden',
        'workflow_author_handoff_read_not_authorized', 'workflow_author_handoff_action_not_authorized',
        'role_binding_not_authorized', 'workflow_review_request_not_found',
        'workflow_review_stage_operation_conflict', 'workflow_author_handoff_source_not_current',
        'workflow_activation_authority_stale', 'workflow_new_runs_disabled',
        'workflow_author_handoff_schema_required', 'workflow_review_stage_authority_unavailable',
        'workflow_review_stage_invalid_request', 'workflow_review_stage_storage_unavailable',
        'workflow_review_stage_storage_corrupt',
    ]


class ReviewStageSchemaErrorDetail(ClosedRecord):
    code: Literal['workflow_author_handoff_schema_required']
    owner: str = Field(min_length=1, max_length=256)
    required_action: str = Field(min_length=1, max_length=2000)


class ReviewStageError(ClosedRecord):
    detail: ReviewStageErrorDetail | ReviewStageSchemaErrorDetail


STAGE_RESPONSES = {status: dict(spec, model=ReviewStageError) for status, spec in READ_RESPONSES.items()}
STAGE_RESPONSES[422]['description'] = 'Closed query, bounded strict JSON, findings, scope or outcome refused'


def stage_command_schema() -> dict:
    return {'anyOf': [ReviewStageCommand.model_json_schema(), ArtifactReviewStageCommand.model_json_schema(), RevisionReviewStageCommand.model_json_schema()]}


def decode_stage_command(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate')
            value[key] = item
        return value

    def nonfinite(value: str) -> None:
        raise ValueError('nonfinite')

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite')
        return number

    try:
        if not 1 <= len(raw) <= 2097152 or raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError('size')
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
                           parse_constant=nonfinite, parse_float=finite_float)
        if isinstance(value, dict) and set(value) & (IDENTITY_KEYS | {'proof', 'proof_bytes', 'actor_proof', 'provenance'}):
            fail('body_identity_rejected', 403)
        return parse_stage(value)
    except (ValueError, UnicodeError, ValidationError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail={'code': 'workflow_review_stage_invalid_request'}) from exc


def raise_stage_error(exc: Exception) -> None:
    code = exc.code
    if code in {'unknown_session', 'cross_org_session', 'session_not_current', 'recovery_purpose_forbidden',
                'workflow_author_handoff_read_not_authorized', 'workflow_author_handoff_action_not_authorized',
                'role_binding_not_authorized'}:
        fail(code, 403)
    if code == 'workflow_review_request_not_found':
        fail(code, 404)
    if code == 'workflow_review_stage_invalid_request':
        fail(code, 422)
    if code in {'workflow_review_stage_operation_conflict', 'workflow_author_handoff_source_not_current',
                'workflow_activation_authority_stale', 'workflow_new_runs_disabled',
                'workflow_author_handoff_schema_required'}:
        detail = {'code': code}
        if code == 'workflow_author_handoff_schema_required' and getattr(exc, 'owner', None):
            detail.update(owner=exc.owner, required_action=exc.required_action)
        raise HTTPException(status_code=409, detail=detail) from exc
    if isinstance(exc, (WorkflowAuthorityError, ProfileCoordinatorError)):
        fail('workflow_review_stage_authority_unavailable', 409)
    # A retained template/activation/closure error is an internal failure after
    # selected admission, never caller422 or a private existence distinction.
    fail('workflow_review_stage_storage_corrupt', 500)


@router.post('/workflows/reviews/stages', response_model=StageAck,
             responses=STAGE_RESPONSES,
             openapi_extra={'x-query-proof-schema': proof_schema({'session_id': TOKEN_SCHEMA}),
                            'parameters': [{'name': 'session_id', 'in': 'query', 'required': True,
                                            'schema': TOKEN_SCHEMA}],
                            'requestBody': {'required': True, 'content': {'application/json': {
                                'schema': stage_command_schema()}}}})
async def stage_agent_review(org: OrgDep, request: Request,
                             has_bearer: bool = optional_bearer()) -> dict:
    agent_transport(request, has_bearer)
    pairs = list(request.query_params.multi_items())
    if (len(pairs) != 1 or pairs[0][0] != 'session_id'
            or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', pairs[0][1]) is None):
        fail('workflow_review_stage_invalid_request', 422)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 2097152:
            fail('workflow_review_stage_invalid_request', 422)
        raw.extend(chunk)
    command = decode_stage_command(bytes(raw))
    try:
        acknowledgement = await org.workflow_reviews.stage_review(session_id=pairs[0][1], command=command)
        model = {'workflow-review-stage-command@1': ReviewStageAck,
                 'workflow-review-stage-command@2': ArtifactReviewStageAck,
                 'workflow-review-stage-command@3': RevisionReviewStageAck}[command['format']]
        model.model_validate(acknowledgement)
        return acknowledgement
    except ERRORS as exc:
        raise_stage_error(exc)
    except sqlite_error_types() as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_review_stage_storage_unavailable'}) from exc
    except (DraftOwnershipError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_review_stage_storage_corrupt'}) from exc
    except StartupUnavailable:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_review_stage_storage_corrupt'}) from exc
