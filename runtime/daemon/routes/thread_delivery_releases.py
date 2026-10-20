"""Dormant human release adapter; deliberately unimported/unmounted by app.py.

The existing bearer/human policy cannot establish exclusive human presence.
Only the serving owner runs native work and retires its operation after waiter
cancellation. This router has no executor, release/recovery loop or readiness flag.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.thread_delivery_release_service import _ThreadDeliveryReleaseService
from runtime.infrastructure.thread_delivery_release import (
    PreviewRequest, ReleaseCommand, ReleaseUnavailable, RISKS, canonical,
    confirmation_inputs, decode, digest, validate_confirmation,
)

PRIVATE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
REQUEST_LIMIT = 16384
RESPONSE_LIMIT = 32768
CONFLICTS = frozenset({
    "selection", "range", "running_owner", "thread_closed", "participant_missing",
    "unrelated_owner", "deferral_unsafe", "breaker_unsafe", "exchange_unsafe",
    "key_collision", "already_disposed", "stale_preview", "no_obstruction", "unstarted",
    "native_control_changed", "native_observation_changed", "attachment_changed",
    "serving_admission_revoked", "preparation_current_owner", "native_writer_current_owner",
    "native_task_current_owner", "team_move_held", "pause_control_unsettled", "invocation_cas", "slot_cas",
    "host_current_owner", "tracker_current_owner", "task_preparation_current_owner", "marker_cas",
})
# Only these literal categories can cross the adapter. Other native diagnostics
# collapse to unavailable without exposing exception/cause text.
UNAVAILABLE = frozenset({
    "serving_owner_unavailable", "native_submission_uncertain", "native_operation_unavailable",
    "whole_owner_proof_unavailable", "budget_unavailable", "database_busy", "database_owned",
    "move_busy", "serving_leaf_busy", "settings_unrestored", "rollback_unavailable",
    "commit_readback_required", "committed_readback_incomplete", "read_snapshot_unrestored",
    "notification_read_unrestored", "native_unavailable", "output_bound",
    "native_population_unavailable", "native_container_unavailable", "native_identity_unavailable",
    "native_counter_unavailable", "preparation_unavailable", "preparation_identity_unavailable",
    "pause_control_unavailable", "current_native_eligibility_unavailable",
    "controller_busy", "residue_busy", "tracker_busy", "host_identity_unavailable",
    "host_ownership_unavailable", "host_topology_unavailable", "host_unattributed_owner",
    "host_unscoped_owner", "residue_or_measurement_unknown", "residue_unavailable",
    "tracker_context_unavailable", "tracker_identity_unavailable", "tracker_topology_unavailable",
    "task_preparation_unavailable",
})
REASONS = frozenset({"serving_owner_unavailable", "current_native_eligibility_unavailable"})


def _identity(value: Any, *, key: bool = False) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1," + ("128" if key else "256") + "}", value) is None:
        raise HTTPException(422)
    return value


def _digest(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid_projection")


def _fields(value: Any, fields: str, booleans: str = "") -> None:
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("invalid_projection")
    if any(type(value[key]) is not bool for key in booleans.split()):
        raise ValueError("invalid_projection")


def _error(status: int, code: str, committed: bool | None, *, key: str | None = None,
           operation: dict | None = None) -> JSONResponse:
    # The tri-state is exact, never inferred from a message or HTTP status.
    if committed is not None and type(committed) is not bool:
        committed = None
    detail = dict(code=code, committed=committed, operation_key=key,
                  next_action="show_same_key" if key is not None else "read_capability",
                  operation=operation)
    body = {"detail": detail}
    if len(canonical(body)) > RESPONSE_LIMIT:
        detail.update(code="output_bound", operation=None)
        status = 503
    return JSONResponse(body, status_code=status, headers=PRIVATE)


class PrivateReleaseRoute(APIRoute):
    """Includes dependency failures, preserving their status without their text."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def guarded(request: Request) -> Any:
            try:
                response = await original(request)
                response.headers.update(PRIVATE)
                return response
            except HTTPException as exc:
                code = {401: "bearer_refused", 403: "human_policy_refused", 404: "unknown_org",
                        422: "invalid_request", 500: "dependency_unavailable", 503: "serving_owner_unavailable"}.get(
                            exc.status_code, "request_refused")
                return _error(exc.status_code, code, False,
                              key=getattr(request.state, "release_key", None))
            except RequestValidationError:
                return _error(422, "invalid_request", False)
            except Exception:
                # Cancellation is a BaseException: never catch it or retire the
                # service-owned operation. Unexpected outcomes remain unknown.
                return _error(503, "unavailable", None,
                              key=getattr(request.state, "release_key", None))
        return guarded


router = APIRouter(route_class=PrivateReleaseRoute,
    dependencies=[require_token(), Depends(_require_human)], responses={
        401: {"description": "Existing bearer refused"},
        403: {"description": "Existing human policy refused; shared bearer limit"},
        404: {"description": "Unknown org or complete inventory has no matching key"},
        409: {"description": "Stale selection, key collision or native winner"},
        422: {"description": "Closed original request refused before apply"},
        500: {"description": "Existing authentication dependency unavailable"},
        503: {"description": "Unavailable/incomplete; committed tri-state preserved"},
    })


def body_schema(model: Any) -> dict:
    """Metadata comes from the shipped strict DTO, not a second request model."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value: Any) -> Any:
        if type(value) is dict:
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: expand(item) for key, item in value.items()}
        return [expand(item) for item in value] if type(value) is list else value
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": expand(schema)}}}}


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid_request")
        value[key] = item
    return value


def _constant(_: str) -> Any:
    raise ValueError("invalid_request")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("invalid_request")
    return number


async def _body(request: Request, model: Any = None) -> tuple[str, Any]:
    if request.scope.get("query_string", b""):
        raise HTTPException(422)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > REQUEST_LIMIT or (model is None and chunk):
            raise HTTPException(422)
        raw.extend(chunk)
    if model is None:
        return "", None
    try:
        text = bytes(raw).decode("utf-8")
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant, parse_float=_float)
        if type(value) is not dict:
            raise ValueError("invalid_request")
        # Preserve ORIGINAL JSON types all the way into the native before
        # validators, particularly version true/1.0 and confirmed 1.
        command = decode(model, text)
        integers = [command.selection.invocation_id]
        if model is ReleaseCommand:
            validate_confirmation(command)
            integers += [command.prefix.last_id, command.range.acknowledged, command.range.required,
                         command.range.running_from, command.range.running_through]
        if any(value is not None and (type(value) is not int or not 0 <= value < 2**63) for value in integers):
            raise ValueError("invalid_request")
        request.state.release_key = command.operation_key
        return text, command
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422) from None


def _owner(request: Request, slug: str) -> _ThreadDeliveryReleaseService:
    _identity(slug)
    daemon = getattr(request.app.state, "daemon", None)
    orgs = getattr(daemon, "orgs", None)
    if type(orgs) is not dict:
        raise HTTPException(503)
    if slug not in orgs:
        raise HTTPException(404)
    owner = getattr(daemon, "_thread_release_service", None)
    if (type(owner) is not _ThreadDeliveryReleaseService or owner.state is not daemon
            or owner.app is not request.app):
        raise HTTPException(503)
    return owner


def _capability(value: dict) -> None:
    _fields(value, "version serving_admission_available proof_backed_available external_adapters_available",
            "serving_admission_available proof_backed_available external_adapters_available")
    if type(value["version"]) is not int or value["version"] != 1 or value["proof_backed_available"] is not False:
        raise ValueError("invalid_projection")


def _preview(value: dict, command: PreviewRequest) -> None:
    _fields(value, "inputs preview_digest accepted_risks proof_backed_available serving_admission_available disposition",
            "proof_backed_available serving_admission_available")
    if (value["proof_backed_available"] is not False or value["accepted_risks"] != list(RISKS)
            or value["disposition"] not in {"running_replaced", "historical_only"}):
        raise ValueError("invalid_projection")
    _digest(value["preview_digest"])
    if digest(canonical(value["inputs"])) != value["preview_digest"]:
        raise ValueError("invalid_projection")
    full = decode(ReleaseCommand, canonical({**value["inputs"], "confirmation": {
        "confirmed": True, "preview_digest": value["preview_digest"], "accepted_risks": value["accepted_risks"],
    }}).decode("utf8"))
    validate_confirmation(full)
    if confirmation_inputs(full) != value["inputs"] or any(
            full.model_dump(mode="json")[key] != item for key, item in command.model_dump(mode="json").items()):
        raise ValueError("invalid_projection")


def _operation(value: dict, key: str, command: ReleaseCommand | None) -> None:
    _fields(value, "version operation_key audit_id command_digest committed disposition original_id replacement_id "
            "replacement_token_digest current_delivery_permission current_native_eligibility serving_admission_available "
            "incomplete_reasons notification", "current_delivery_permission current_native_eligibility serving_admission_available")
    if (type(value["version"]) is not int or value["version"] != 1
            or value["committed"] is not True or value["operation_key"] != key):
        raise ValueError("invalid_projection")
    for field in ("audit_id", "original_id"):
        if type(value[field]) is not int or not 0 < value[field] < 2**63:
            raise ValueError("invalid_projection")
    _digest(value["command_digest"])
    if value["disposition"] == "running_replaced":
        replacement = value["replacement_id"]
        if type(replacement) is not int or not 0 < replacement < 2**63 or replacement == value["original_id"]:
            raise ValueError("invalid_projection")
        _digest(value["replacement_token_digest"])
    elif value["disposition"] != "historical_only" or value["replacement_id"] is not None or value["replacement_token_digest"] is not None:
        raise ValueError("invalid_projection")
    reasons = value["incomplete_reasons"]
    if (type(reasons) is not list or len(reasons) > 64
            or any(type(item) is not str or item not in REASONS for item in reasons)
            or len(set(reasons)) != len(reasons)
            or value["notification"] not in {"unknown", "pending_or_skipped", "published_same_token"}):
        raise ValueError("invalid_projection")
    if value["current_native_eligibility"] and (value["replacement_id"] is None
            or not value["current_delivery_permission"] or not value["serving_admission_available"]):
        raise ValueError("invalid_projection")
    if value["notification"] == "published_same_token" and value["replacement_id"] is None:
        raise ValueError("invalid_projection")
    if command is not None and (value["command_digest"] != digest(canonical(command.model_dump(mode="json")))
            or value["original_id"] != command.selection.invocation_id):
        raise ValueError("invalid_projection")


async def _invoke(owner: _ThreadDeliveryReleaseService, slug: str, kind: str, *,
                  raw: str = "", command: Any = None, key: str | None = None) -> JSONResponse:
    try:
        if kind == "capability":
            value = owner.capability(slug)
        elif kind == "preview":
            value = await owner.preview(slug, raw)
        elif kind == "apply":
            value = await owner.apply(slug, raw)
        else:
            value = await owner.show(slug, key)
    except ReleaseUnavailable as exc:
        code = exc.code if exc.code in CONFLICTS | UNAVAILABLE else "unavailable"
        # Key-only show has no caller selection: this category means the
        # strict stored graph is unavailable, not a request selection conflict.
        conflict = code in CONFLICTS and not (kind == "show" and code == "selection")
        return _error(409 if conflict else 503, code, exc.committed, key=key)
    except Exception:
        return _error(503, "unavailable", None, key=key)
    if value is None and kind == "show":
        # Only the owner's completed complete inventory can establish absence
        # at this read; it never proves rollback of an earlier lost response.
        return _error(404, "key_not_found_at_read", None, key=key)
    committed = value.get("committed") if type(value) is dict else None
    try:
        if len(canonical(value)) > RESPONSE_LIMIT:
            raise ValueError("invalid_projection")
        if kind == "capability":
            _capability(value)
        elif kind == "preview":
            _preview(value, command)
        else:
            _operation(value, key, command)
            if value["incomplete_reasons"]:
                return _error(503, "incomplete_readback", True, key=key, operation=value)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return _error(503, "incomplete_output", committed, key=key)
    return JSONResponse(value, headers=PRIVATE)


@router.get("/thread-delivery-releases/capability")
async def capability(slug: str, request: Request) -> JSONResponse:
    await _body(request)
    return await _invoke(_owner(request, slug), slug, "capability")


@router.post("/thread-delivery-releases/preview", openapi_extra=body_schema(PreviewRequest))
async def preview(slug: str, request: Request) -> JSONResponse:
    raw, command = await _body(request, PreviewRequest)
    if command.selection.org_slug != slug:
        raise HTTPException(422)
    return await _invoke(_owner(request, slug), slug, "preview", raw=raw, command=command, key=command.operation_key)


@router.post("/thread-delivery-releases", openapi_extra=body_schema(ReleaseCommand),
             responses={202: {"description": "Reserved genuine accepted work; current owner always awaits retirement"}})
async def apply(slug: str, request: Request) -> JSONResponse:
    raw, command = await _body(request, ReleaseCommand)
    if command.selection.org_slug != slug:
        raise HTTPException(422)
    return await _invoke(_owner(request, slug), slug, "apply", raw=raw, command=command, key=command.operation_key)


@router.get("/thread-delivery-releases/by-key/{operation_key}")
async def show(slug: str, operation_key: str, request: Request) -> JSONResponse:
    await _body(request)
    key = _identity(operation_key, key=True)
    request.state.release_key = key
    return await _invoke(_owner(request, slug), slug, "show", key=key)
