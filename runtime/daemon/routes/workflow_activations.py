"""Founder-only initial activation and authenticated immutable receipt reads."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from runtime.daemon.auth import _require_human
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.activation import (
    ActivationReceipt, ActivationRequest, DocumentActivationReceipt, DocumentActivationRequest,
    WorkflowActivationError,
)
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplateError, WorkflowTemplatePrincipal


router = APIRouter(dependencies=[Depends(_require_human)])
Receipt = ActivationReceipt | DocumentActivationReceipt


def _request_schema() -> dict[str, Any]:
    return {"anyOf": [_expand_request_schema(model, index)
                      for index, model in enumerate((ActivationRequest, DocumentActivationRequest))]}


def _expand_request_schema(model: type[ActivationRequest], branch: int) -> dict[str, Any]:
    schema = model.model_json_schema(by_alias=True)
    definitions = schema.pop("$defs", {})

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    expanded = expand(schema)
    if model is DocumentActivationRequest:
        # Pydantic emits key regexes as patternProperties, which alone allows
        # unmatched keys. Keep the key bounds and explicitly constrain every
        # key, with the same closed value schemas as runtime validation.
        for name in ("bindings", "eligible_replacements"):
            role_map = expanded["properties"][name]
            pattern, value_schema = next(iter(role_map.pop("patternProperties").items()))
            role_map["propertyNames"]["pattern"] = pattern
            role_map["additionalProperties"] = value_schema
    # Definitions are inlined at this served request-body location. Mapping
    # strings are JSON pointers too: they must identify those same variants,
    # rather than the removed Pydantic $defs at the document root.
    items = expanded["properties"]["inputs"]["items"]
    pointer = ("#/paths/~1api~1v1~1orgs~1{slug}~1workflows~1activations/post/"
               f"requestBody/content/application~1json/schema/anyOf/{branch}/properties/inputs/items/oneOf/")
    items["discriminator"]["mapping"] = {
        variant["properties"]["kind"]["const"]: f"{pointer}{index}"
        for index, variant in enumerate(items["oneOf"])
    }
    return expanded


def _principal(org: Any) -> WorkflowTemplatePrincipal:
    return WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug="",
                                              revalidate=lambda: None)


def _raise(exc: WorkflowActivationError | WorkflowAuthorityError | WorkflowTemplateError | ProfileCoordinatorError) -> None:
    code = exc.code
    status = (500 if code.endswith("storage_corrupt") else 404 if code.endswith("not_found")
              else 403 if code == "role_binding_not_authorized" else 409 if code in {
                  "workflow_activation_operation_conflict", "workflow_activation_cas_stale",
                  "workflow_activation_authority_stale", "workflow_new_runs_disabled",
                  "workflow_activation_author_pending", "authority_pointer_not_ready",
              } or code.startswith(("profile_", "publication_", "authority_")) else 422)
    detail = {"code": code}
    if isinstance(exc, WorkflowActivationError) and exc.owner:
        detail.update(owner=exc.owner, required_action=exc.required_action)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.post("/workflows/activations", status_code=201, response_model=Receipt,
             responses={200: {"model": Receipt, "description": "Exact original actor/org/key/request replay"},
                        403: {"description": "Founder or role binding authority refused"},
                        409: {"description": "Operation conflict, stale CAS/authority, or fenced admission"},
                        422: {"description": "Malformed request or unavailable/bounded input with owner/remedy"},
                        500: {"description": "Stored activation closure corrupt; no repair"}},
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": _request_schema()}}}})
async def activate_workflow(org: OrgDep, response: Response, http_request: Request, body_raw: object = Body(...)) -> dict:
    if isinstance(body_raw, dict) and set(body_raw) & {
        "actor", "principal", "principal_id", "provenance", "task", "task_id", "session_id", "result_id", "org_slug",
    }:
        raise HTTPException(status_code=403, detail={"code": "body_identity_rejected"})
    try:
        receipt = await org.workflow_activations.activate(principal=_principal(org), request=body_raw)
    except (WorkflowActivationError, WorkflowAuthorityError, WorkflowTemplateError, ProfileCoordinatorError) as exc:
        _raise(exc)
    response.status_code = 200 if receipt["replayed"] else 201
    if not receipt["replayed"]:
        from runtime.daemon.runner import enqueue_task
        # Durable intent is the work source. A lost notification is discovered
        # at startup and periodic sweeps; response-loss replay never enqueues.
        try:
            enqueue_task(http_request.app.state.daemon, org.slug, receipt["root_task_id"])
        except Exception:
            import logging
            logging.getLogger(__name__).exception("workflow draft notification unavailable")
    return receipt


@router.get("/workflows/activations", response_model=list[Receipt],
            responses={403: {"description": "Founder authority refused"},
                       500: {"description": "Stored activation closure corrupt"}})
async def list_workflow_activations(org: OrgDep) -> list[dict]:
    try:
        return org.workflow_activations.list(principal=_principal(org))
    except WorkflowActivationError as exc:
        _raise(exc)


@router.get("/workflows/activations/{activation_id}", response_model=Receipt,
            responses={403: {"description": "Founder authority refused"},
                       404: {"description": "No authenticated same-org activation receipt"},
                       500: {"description": "Stored activation closure corrupt"}})
async def get_workflow_activation(org: OrgDep, activation_id: str) -> dict:
    try:
        return org.workflow_activations.get(principal=_principal(org), activation_id=activation_id)
    except WorkflowActivationError as exc:
        _raise(exc)
