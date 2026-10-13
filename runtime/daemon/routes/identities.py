"""Unpublished naming API slice; permanent IDs remain every actor/owner key.

Reads never reconcile/install naming. Operator rename reuses the existing bearer
trust surface, which cannot cryptographically identify a human holding it.
"""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.identities.registry import (
    Subject, classify, current_identity_records, rename_owner,
)
from runtime.identities.schema import NamingError
from runtime.models import ThreadStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths

router = APIRouter(dependencies=[require_token()])

# Existing task/session and thread invocation models use these identity keys.
# Like cutover/activation raw guards, inspect presence BEFORE lossy decoding.
# No shared auth helper or its other consumers are changed by this guard.
_BINDING_FIELDS = frozenset({
    'task', 'task_id', 'session', 'session_id', 'agent', 'agent_id', 'agent_name',
    'composer', 'speaker', 'thread', 'thread_id', 'invocation', 'invocation_id',
    'invocation_token', 'token', 'actor', 'actor_id', 'principal', 'principal_id',
    'principal_kind', 'org', 'org_slug',
})
_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', re.ASCII)
_ID = re.compile(r'[a-z0-9_]{1,64}', re.ASCII)


class IdentityView(BaseModel):
    canonical_id: str
    kind: Literal['agent', 'founder']
    lifecycle: Literal['active', 'pending', 'terminated', 'absent', 'founder']
    addressable_name: str | None
    name_revision: int | None
    canonical_definition_revision: str | None
    naming_status: Literal['ready', 'unavailable']


class IdentityList(BaseModel):
    identities: list[IdentityView]


class RenameBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    addressable_name: StrictStr = Field(min_length=1, max_length=64,
                                      pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    expected_name_revision: StrictInt = Field(gt=0)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    addresses: list[StrictStr] = Field(min_length=1, max_length=128)
    context: Literal['thread_recipient', 'task_owner', 'lookup'] = 'lookup'
    thread_id: StrictStr | None = Field(default=None, min_length=1, max_length=128)


class Resolution(BaseModel):
    address: str
    status: Literal['resolved', 'former_name', 'unknown_identity',
                    'invalid_identity_address', 'ineligible_identity', 'naming_unavailable']
    identity: IdentityView | None
    eligible: bool


class ResolveResponse(BaseModel):
    resolutions: list[Resolution]


def _view(subject: Subject, revision: str | None = None) -> IdentityView:
    return IdentityView(canonical_id=subject.canonical_id, kind=subject.kind,
                        lifecycle=subject.lifecycle, addressable_name=subject.current_label,
                        name_revision=subject.revision, canonical_definition_revision=revision,
                        naming_status='ready' if subject.revision is not None else 'unavailable')


def _definition_revision(org, subject: Subject) -> str | None:
    if subject.kind == 'founder' or subject.lifecycle == 'absent':
        return None
    paths = OrgPaths(org.root)
    if subject.lifecycle == 'active':
        return prompt_loader.agent_revision(paths, subject.canonical_id)
    import hashlib
    directory = paths.pending_agents_dir if subject.lifecycle == 'pending' else paths.agents_dir / '_terminated'
    try:
        return hashlib.sha256((directory / f'{subject.canonical_id}.md').read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


@router.get('/identities', response_model=IdentityList)
def list_identities(org: OrgDep) -> IdentityList:
    return IdentityList(identities=[_view(subject, revision)
                                   for subject, revision in current_identity_records(org)])


@router.post('/identities/resolve', response_model=ResolveResponse)
def resolve_identities(body: ResolveBody, org: OrgDep) -> ResolveResponse:
    thread = None
    if body.thread_id is not None:
        if body.context != 'thread_recipient':
            raise HTTPException(status_code=422, detail={'code': 'invalid_identity_context'})
        thread = org.db.get_thread(body.thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail={'code': 'unknown_thread'})
    results = []
    for address in body.addresses:
        # Raw names/IDs only. No Unicode, trimming, spaces or bracket syntax.
        if not address.isascii() or (_LABEL.fullmatch(address) is None and
                                     _ID.fullmatch(address.lower()) is None):
            results.append(Resolution(address=address, status='invalid_identity_address',
                                      identity=None, eligible=False))
            continue
        try:
            subject = classify(org, address)
        except NamingError:
            results.append(Resolution(address=address, status='naming_unavailable',
                                      identity=None, eligible=False))
            continue
        if subject is None:
            # Leading underscore is an actual same-owner ID exception only.
            status = 'unknown_identity' if _LABEL.fullmatch(address) else 'invalid_identity_address'
            results.append(Resolution(address=address, status=status, identity=None, eligible=False))
            continue
        if subject.classification == 'former':
            # Diagnostic only: current name is exposed, never forwarded.
            results.append(Resolution(address=address, status='former_name',
                                      identity=_view(subject), eligible=False))
            continue
        eligible = body.context == 'lookup'
        if body.context == 'task_owner':
            eligible = (subject.kind == 'agent' and subject.lifecycle == 'active'
                        and subject.canonical_id in org.teams.all_agents()
                        and prompt_loader.load_agent(OrgPaths(org.root), subject.canonical_id) is not None)
        elif body.context == 'thread_recipient':
            # Founder is a human inbox target, never an agent participant.
            eligible = (thread is None or thread.status is ThreadStatus.OPEN) and (subject.kind == 'founder' or (
                subject.lifecycle == 'active'
                and prompt_loader.load_agent(OrgPaths(org.root), subject.canonical_id) is not None
                and (org.root / 'workspaces' / subject.canonical_id).exists()
                and (body.thread_id is None or org.db.is_thread_participant(body.thread_id, subject.canonical_id))))
        results.append(Resolution(address=address,
                                  status='resolved' if eligible else 'ineligible_identity',
                                  identity=_view(subject, _definition_revision(org, subject)), eligible=eligible))
    # This read is neither authorization nor reservation. Later action owners
    # must revalidate current canonical lifecycle/context at their own boundary.
    return ResolveResponse(resolutions=results)


def _rename_body(raw: object, request: Request) -> RenameBody:
    if (set(request.query_params) & _BINDING_FIELDS or
            isinstance(raw, dict) and set(raw) & _BINDING_FIELDS):
        raise HTTPException(status_code=403, detail={'code': 'identity_operator_binding_rejected'})
    try:
        body = RenameBody.model_validate(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail={'code': 'invalid_identity_request'}) from None
    # Fullmatch also rejects a trailing newline accepted by some `$` patterns.
    if _LABEL.fullmatch(body.addressable_name) is None:
        raise HTTPException(status_code=422, detail={'code': 'invalid_identity_request'})
    return body


async def _rename(org, kind: str, canonical_id: str, body: RenameBody) -> IdentityView:
    try:
        subject = await rename_owner(org, kind=kind, canonical_id=canonical_id,
                                     label=body.addressable_name,
                                     expected_revision=body.expected_name_revision)
        return _view(subject, _definition_revision(org, subject))
    except NamingError as exc:
        code = exc.code
        status = (404 if code == 'identity_owner_absent' else
                  409 if code in {'stale_identity_revision', 'identity_name_unavailable'} else
                  422 if code.startswith('invalid_identity_') else 503)
        raise HTTPException(status_code=status, detail={'code': code}) from None
    except Exception:
        # Includes ambiguous postcommit failure. Read back before attempting
        # another CAS; no invented rollback/success or raw DB/path diagnostics.
        raise HTTPException(status_code=503, detail={'code': 'naming_unavailable'}) from None


_RENAME_OPENAPI = {'requestBody': {'required': True, 'content': {
    'application/json': {'schema': RenameBody.model_json_schema()},
}}}


@router.put('/agents/{agent_id}/addressable-name', response_model=IdentityView,
            dependencies=[Depends(_require_human)], openapi_extra=_RENAME_OPENAPI)
async def rename_agent_identity(agent_id: str, org: OrgDep, request: Request,
                                raw: object = Body(...)) -> IdentityView:
    body = _rename_body(raw, request)
    if _ID.fullmatch(agent_id) is None or agent_id == 'founder':
        raise HTTPException(status_code=404, detail={'code': 'identity_owner_absent'})
    return await _rename(org, 'agent', agent_id, body)


@router.put('/founder/addressable-name', response_model=IdentityView,
            dependencies=[Depends(_require_human)], openapi_extra=_RENAME_OPENAPI)
async def rename_founder_identity(org: OrgDep, request: Request,
                                  raw: object = Body(...)) -> IdentityView:
    return await _rename(org, 'founder', 'founder', _rename_body(raw, request))
