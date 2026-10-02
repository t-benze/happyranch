"""U1B workflow-template publish/list/get routes."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.templates import (
    WorkflowTemplateError,
    WorkflowTemplatePrincipal,
    WorkflowTemplateStore,
    WorkflowTemplateVersion,
)


router = APIRouter()
_FORBIDDEN_IDENTITY = frozenset({
    "publisher", "principal", "principal_id", "principal_kind", "proof_kind",
    "namespace", "org", "org_slug", "task", "task_id", "agent", "agent_name",
})
_CONFLICT_CODES = frozenset({
    "template_publish_operation_conflict",
    "template_version_cas_stale",
    "template_content_already_published",
    "template_storage_conflict",
})
_FORBIDDEN_CODES = frozenset({
    "namespace_claim_rejected", "manager_required", "manager_authority_lost",
    "unknown_session", "session_not_current", "cross_org_session",
    "recovery_purpose_forbidden", "body_identity_rejected", "bearer_not_accepted",
    "authentication_required", "auth_path_conflict", "team_not_found",
})


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    operation_key: str
    template_name: str
    expected_current_version: int
    definition: object
    team_slug: str | None = None


def _error(code: str, status_code: int) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code})


def _raise_store_error(exc: WorkflowTemplateError) -> None:
    if exc.code == "template_version_not_found":
        _error(exc.code, 404)
    if exc.code in _CONFLICT_CODES:
        _error(exc.code, 409)
    if exc.code in _FORBIDDEN_CODES:
        _error(exc.code, 403)
    if exc.code in {"template_storage_corrupt", "template_caller_transaction_not_allowed"}:
        _error(exc.code, 500)
    _error(exc.code, 422)


def _serialize(item: WorkflowTemplateVersion) -> dict:
    return {
        "identity_id": item.identity_id,
        "version_id": item.version_id,
        "namespace": item.namespace,
        "template_name": item.template_name,
        "version": item.version,
        "definition_bytes_base64": base64.b64encode(item.definition_bytes).decode("ascii"),
        "definition_json": item.definition_bytes.decode("utf-8"),
        "definition_digest": item.definition_digest,
        "compiler_pin": item.compiler_pin,
        "validator_pin": item.validator_pin,
        "source_pin": item.source_pin,
        "publisher": item.publisher,
        "published_at": item.published_at,
    }


def _parse_body(body_raw: object) -> PublishBody:
    try:
        body = PublishBody.model_validate(body_raw)
    except ValidationError:
        _error("invalid_request", 422)
    return body


def _check_extra_identity(body: PublishBody) -> None:
    extras = set(body.model_extra or {})
    if extras & _FORBIDDEN_IDENTITY:
        _error("body_identity_rejected", 403)
    if extras:
        _error("invalid_request", 422)


@router.post("/workflows/templates/publish", status_code=201)
async def publish_template(
    request: Request,
    org: OrgDep,
    body_raw: object = Body(...),
    session_id: str | None = Query(default=None),
    has_bearer: bool = optional_bearer(),
) -> dict:
    body = _parse_body(body_raw)
    _check_extra_identity(body)
    store = WorkflowTemplateStore(org.db)

    if has_bearer:
        if session_id is not None:
            _error("auth_path_conflict", 403)
        if body.team_slug is None:
            _error("invalid_request", 422)
        async with org.teams_lock:
            team_slug = body.team_slug

            def revalidate() -> None:
                if team_slug not in org.teams.teams():
                    raise WorkflowTemplateError("team_not_found")

            principal = WorkflowTemplatePrincipal.founder(
                org_slug=org.slug,
                team_slug=team_slug,
                revalidate=revalidate,
            )
            try:
                result = store.publish_version(
                    org_slug=org.slug,
                    principal=principal,
                    operation_key=body.operation_key,
                    namespace=f"org/{org.slug}/team/{team_slug}",
                    template_name=body.template_name,
                    definition=body.definition,
                    expected_current_version=body.expected_current_version,
                )
            except WorkflowTemplateError as exc:
                _raise_store_error(exc)
        return _serialize(result)

    if session_id is None:
        _error("authentication_required", 403)
    if "authorization" in request.headers:
        _error("bearer_not_accepted", 403)
    if body.team_slug is not None:
        _error("namespace_claim_rejected", 403)
    context = org.sessions.get_context_by_session(session_id)
    if context is None:
        _error("unknown_session", 403)
    verified_org, task_id, agent = context
    if verified_org != org.slug:
        _error("cross_org_session", 403)
    binding_lease = org.sessions._get_binding_lease(task_id, agent)

    async with org.teams_lock:
        with binding_lease:
            if org.sessions.get_active(task_id, agent) != session_id:
                _error("session_not_current", 403)
            if org.sessions.is_recovery_session(task_id, agent, session_id):
                _error("recovery_purpose_forbidden", 403)
            manager_teams = org.teams.teams_for_manager(agent)
            if len(manager_teams) != 1:
                _error("manager_required", 403)
            team_slug = manager_teams[0]

            def revalidate() -> None:
                if org.sessions.get_active(task_id, agent) != session_id:
                    raise WorkflowTemplateError("session_not_current")
                if org.sessions.get_context_by_session(session_id) != (
                    org.slug, task_id, agent,
                ):
                    raise WorkflowTemplateError("manager_authority_lost")
                if org.teams.teams_for_manager(agent) != (team_slug,):
                    raise WorkflowTemplateError("manager_authority_lost")

            principal = WorkflowTemplatePrincipal.agent(
                org_slug=org.slug,
                agent_name=agent,
                team_slug=team_slug,
                task_id=task_id,
                session_id=session_id,
                revalidate=revalidate,
            )
            try:
                result = store.publish_version(
                    org_slug=org.slug,
                    principal=principal,
                    operation_key=body.operation_key,
                    namespace=f"org/{org.slug}/team/{team_slug}",
                    template_name=body.template_name,
                    definition=body.definition,
                    expected_current_version=body.expected_current_version,
                )
            except WorkflowTemplateError as exc:
                _raise_store_error(exc)
    return _serialize(result)


@router.get("/workflows/templates", dependencies=[Depends(_require_human)])
def list_templates(org: OrgDep, team_slug: str = Query(...)) -> dict:
    try:
        items = WorkflowTemplateStore(org.db).list(
            org_slug=org.slug,
            namespace=f"org/{org.slug}/team/{team_slug}",
        )
    except WorkflowTemplateError as exc:
        _raise_store_error(exc)
    return {"templates": [_serialize(item) for item in items]}


@router.get(
    "/workflows/templates/{team_slug}/{template_name}/{version}",
    dependencies=[Depends(_require_human)],
)
def get_template(
    org: OrgDep, team_slug: str, template_name: str, version: int,
) -> dict:
    try:
        item = WorkflowTemplateStore(org.db).get(
            org_slug=org.slug,
            namespace=f"org/{org.slug}/team/{team_slug}",
            template_name=template_name,
            version=version,
        )
    except WorkflowTemplateError as exc:
        _raise_store_error(exc)
    return _serialize(item)
