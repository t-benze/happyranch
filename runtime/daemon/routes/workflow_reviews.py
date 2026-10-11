"""Founder-bearer human receipts and current selected-recipient request reads."""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool, StrictInt, ValidationError

from runtime.workflows.activation import (AbstractRole, ActivatedTemplate, ClosedRecord,
    DocumentTaskInput, DocumentThreadInput, SHA256, Token)


from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_submissions import ERRORS, IDENTITY_KEYS, READ_RESPONSES, TOKEN_SCHEMA, agent_transport, fail, proof_schema, query_values, raise_error, raise_read_error, sqlite_error_types
from runtime.workflows.review_receipts import HumanReviewCommand, ReviewReceiptProjection, parse_human_review, recipient_receipt
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


ReviewRequestReceipt = Annotated[PendingReviewRequest | SignedReviewRequest, Field(discriminator='status')]


class HumanReviewReceipt(ClosedRecord):
    format: Literal['workflow-human-review-receipt@1']
    receipt: ReviewReceiptProjection


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


@router.post('/workflows/reviews/human-receipts', response_model=HumanReviewReceipt,
             responses=REVIEW_RESPONSES,
             openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {
                 'schema': HumanReviewCommand.model_json_schema()}}}})
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
        return dict(format='workflow-human-review-receipt@1', receipt=recipient_receipt(receipt))
    except ERRORS as exc:
        if exc.code == 'workflow_human_review_operation_conflict':
            fail(exc.code, 409)
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
