"""Selected-recipient reads for retained document-review history."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool, StrictInt

from runtime.workflows.activation import (AbstractRole, ActivatedTemplate, ClosedRecord,
    DocumentTaskInput, DocumentThreadInput, SHA256, Token)


from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_submissions import ERRORS, READ_RESPONSES, TOKEN_SCHEMA, agent_transport, fail, proof_schema, query_values, raise_read_error, sqlite_error_types
from runtime.workflows.review_receipts import ReviewReceiptProjection, ArtifactReviewReceiptProjection, RevisionReviewReceiptProjection
from runtime.workflows.templates import WorkflowTemplatePrincipal

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
