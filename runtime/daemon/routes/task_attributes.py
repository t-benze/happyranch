"""Custom attributes: existing authority, org-local reads and atomic audited edits."""
from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager, contextmanager, nullcontext
import json
import sqlite3
from typing import Annotated
import yaml

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from runtime.daemon.auth import _check_optional_token, _check_token, _require_human
from runtime.daemon.routes._org_dep import resolve_org
from runtime.daemon.routes.identities import _BINDING_FIELDS
from runtime.daemon.routes.task_attribute_models import (
    ArchiveDefinition, AttributeErrorResponse, CreateDefinition, DefinitionRead, DefinitionsRead,
    SaveAttributes, TaskAttributesRead, TaskHistoryRead, UpdateDefinition, body_schema,
)
from runtime.infrastructure.db.task_attributes import AttributeStoreError
from runtime.infrastructure.team_move_store import TeamMoveError
from runtime.models import TaskStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentParseError
from runtime.orchestrator.canonical_roster import canonical_roster
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.task_attributes import AttributeValidationError

_RESPONSES = {code: {"model": AttributeErrorResponse} for code in (401, 403, 404, 409, 422, 503)}
_NO_STORE = {"Cache-Control": "no-store"}
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED}
_TASK_STATUSES = {status.value for status in TaskStatus}


class AttributeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def no_store(request):
            try:
                response = await handler(request)
            except HTTPException as exc:
                # The app's SPA handlers discard HTTPException headers. Render
                # attribute errors here so the delivered response is no-store.
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except (TeamMoveError, DeferredTeamMove) as exc:
                response = JSONResponse({"detail": {"code": exc.code}}, status_code=409)
            except RequestValidationError as exc:
                errors = {".".join(str(p) for p in e["loc"]): [e["type"]] for e in exc.errors()}
                response = JSONResponse({"detail": {"code": "attribute_validation_failed", "errors": errors}}, status_code=422)
            response.headers.update(_NO_STORE)
            return response
        return no_store


router = APIRouter(route_class=AttributeRoute, dependencies=[Depends(_check_optional_token)])


def _error(status: int, code: str, **detail):
    raise HTTPException(status, detail={"code": code, **detail}, headers=_NO_STORE)


def _org(slug: str, request: Request):
    # Preserve the native mounted-org capture; do not expose its org inventory.
    with ExitStack() as stack:
        try:
            org = stack.enter_context(contextmanager(resolve_org)(slug, request))
        except HTTPException as exc:
            if exc.status_code == 404:
                _error(404, "not_found")
            if exc.status_code == 409:
                _error(409, exc.detail["code"])
            raise
        yield org


Org = Annotated[object, Depends(_org)]


def _query(request: Request, allowed: set[str]) -> str | None:
    seen = set()
    for key, _ in request.query_params.multi_items():
        if key in seen or key not in allowed:
            _error(422, "attribute_validation_failed", errors={key: ["duplicate_or_unsupported_query"]})
        seen.add(key)
    return request.query_params.get("session_id")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AttributeValidationError({key: ["duplicate_json_member"]})
        result[key] = value
    return result


async def _body(request: Request, model, *, human=False) -> dict:
    try:
        raw = json.loads(await request.body(), object_pairs_hook=_pairs,
                         parse_constant=lambda _: _invalid_json())
    except AttributeValidationError as exc:
        _error(422, "attribute_validation_failed", errors=exc.errors)
    except (ValueError, UnicodeError, RecursionError):
        _error(422, "attribute_validation_failed", errors={"body": ["invalid_json"]})
    if human and isinstance(raw, dict) and raw.keys() & _BINDING_FIELDS:
        _error(403, "human_only")
    try:
        return model.model_validate(raw).model_dump(exclude_unset=True)
    except ValidationError as exc:
        errors = {}
        for item in exc.errors(include_input=False, include_context=False):
            path = ".".join(str(part) for part in item["loc"]) or "body"
            errors.setdefault(path, []).append(item["type"])
        _error(422, "attribute_validation_failed", errors=errors)


def _invalid_json():
    raise ValueError("invalid_json")


def _context(org, session_id):
    if session_id is None:
        return None
    context = org.sessions.get_context_by_session(session_id)
    return context if context is not None and context[0] == org.slug else None


def _task_row(org, task_id):
    # Attribute provenance deliberately tolerates unavailable legacy timestamps.
    # Authority consumes only its native columns, not the unrelated full-task
    # timestamp parser. Caller holds the database lock and coherent transaction.
    return org.db._conn.execute(
        "SELECT assigned_agent,team,parent_task_id,status,cancelled_at,current_session_id FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()


def _session_reason(request, org, session_id, context):
    if context is None:
        return "authority_unknown"
    state = request.app.state.daemon
    _, caller_id, agent = context
    if (state.is_idle or state.orgs.get(org.slug) is not org
            or org.sessions.get_context_by_session(session_id) != context
            or org.sessions.get_active(caller_id, agent) != session_id):
        return "session_not_current"
    if org.sessions.is_recovery_session(caller_id, agent, session_id):
        return "recovery_purpose_forbidden"
    caller = _task_row(org, caller_id)
    if caller is None or caller["assigned_agent"] != agent or caller["current_session_id"] != session_id:
        return "session_not_current"
    if caller["status"] not in _TASK_STATUSES:
        return "authority_unknown"
    if caller["status"] in _TERMINAL or caller["cancelled_at"] is not None:
        return "task_not_active"
    return None


def _roster(org, agent):
    """Fresh canonical facts under the existing roster publication interval."""
    try:
        canonical = TeamsRegistry.from_bytes((org.root / "org/teams.yaml").read_bytes(), root=org.root)
        if (canonical.teams() != org.teams.teams()
                or any(canonical.team_row(team) != org.teams.team_row(team) for team in canonical.teams())):
            return None, None
        actor = prompt_loader.load_agent(OrgPaths(org.root), agent)
        if (actor is None or not actor.executor or actor.team != canonical.team_for_agent(agent)
                or agent not in canonical.all_agents()):
            return None, None
        return canonical, actor
    except (OSError, ValueError, KeyError, AgentParseError, yaml.YAMLError):
        return None, None


def _editability(code=None):
    return {"can_edit": code is None, "refusal_reason": None if code is None else {"code": code}}


def _task_editability(request, org, task_id, session_id, context):
    target = _task_row(org, task_id)
    if target is None:
        _error(404, "not_found")
    if session_id is None:
        return _editability()  # task route already required the human bearer
    code = _session_reason(request, org, session_id, context)
    if code:
        return _editability(code)
    agent = context[2]
    teams, actor = _roster(org, agent)
    if teams is None:
        return _editability("authority_unknown")
    if target["assigned_agent"] == agent:
        return _editability()
    registrations = teams.teams_for_manager(agent)
    if actor.role != "manager" or len(registrations) != 1 or registrations[0] != actor.team:
        return _editability("not_task_owner_or_owning_manager")
    if target["team"] not in teams.teams():
        return _editability("authority_unknown")
    if teams.executable_manager_for_team(target["team"]) == agent:
        return _editability()
    if target["parent_task_id"] is not None:
        parent = _task_row(org, target["parent_task_id"])
        if parent is None:
            return _editability("authority_unknown")
        if parent["assigned_agent"] == agent:
            return _editability()
    return _editability("not_task_owner_or_owning_manager")


@asynccontextmanager
async def _access(org, context):
    # Async waits precede all synchronous leases. The existing publisher lock
    # also excludes canonical writers on other threads. No await spans this
    # short binding -> move -> SQLite interval or its final COMMIT.
    authority = org.workflow_authority
    async with authority._async_writer_lock, org.teams_lock, org.db_lock:
        lease = org.sessions.binding_lease(context[1], context[2]) if context else nullcontext()
        move = getattr(org.db, "_team_move_coordinator", None)
        with authority._publisher_lock, canonical_roster(org.root), lease, move.gate if move else nullcontext(), org.db._lock:
            yield


def _definition(value):
    return {**value, "archived": value["archived_at"] is not None}


def _definition_editability(human, current=None):
    archived = current is not None and current["archived_at"] is not None
    allowed = human and not archived
    code = "human_only" if not human else "definition_archived" if archived else None
    return {"can_create": allowed, "can_edit_metadata": allowed, "can_edit_structure": allowed and (
        current is None or current["first_used_at"] is None), "can_archive": allowed,
        "refusal_reason": None if code is None else {"code": code}}


def _definition_read(org, definition_id, *, human, version=None):
    current = org.db.get_task_attribute_definition(definition_id)
    definition = current if version is None else org.db.get_task_attribute_definition(definition_id, version=version)
    return {"definition": _definition(definition), "editability": _definition_editability(human, current)}


def _history(history, names):
    for event in history["events"]:
        event["definition"] = _definition(event["definition"])
        event["actor"] = {"canonical_id": event["actor_id"], "kind": event["actor_kind"],
                          "addressable_name": names.get(event["actor_id"])}
    return history


def _names(org):
    # Optional display metadata only. Unavailable names never affect authority,
    # provenance, history availability, or the immutable Founder actor kind.
    from runtime.identities.registry import current_identity_records
    from runtime.identities.schema import NamingError
    try:
        return {subject.canonical_id: subject.current_label for subject, _ in current_identity_records(org)}
    except (NamingError, OSError, ValueError, sqlite3.Error, yaml.YAMLError):
        return {}


def _task_read(request, org, task_id, session_id, context, names):
    editability = _task_editability(request, org, task_id, session_id, context)
    attributes = org.db.get_task_attributes(task_id)
    for item in attributes:
        item["definition"] = _definition(item["definition"])
        if item["initial"]["state"] == "set":
            item["initial"]["definition"] = _definition(item["initial"]["definition"])
        item["history"] = _history(item["history"], names)
        item["editability"] = _editability("definition_archived") if item["definition"]["archived"] else editability
    return {"task_id": task_id, "editability": editability, "attributes": attributes}


def _storage_error(exc):
    if isinstance(exc, AttributeStoreError) and exc.code in ("definition_not_found", "task_not_found"):
        _error(404, "not_found")
    _error(503, "attribute_storage_unavailable")


async def _discovery(request, org, session_id, *, definition_id=None, version=None, include_archived=False):
    human = session_id is None
    if human:
        _check_token(request.headers.get("authorization"))
    context = _context(org, session_id)
    try:
        async with _access(org, context):
            with org.db._attribute_reader():
                if not human:
                    code = _session_reason(request, org, session_id, context)
                    teams, _ = _roster(org, context[2]) if context else (None, None)
                    if code or teams is None:
                        _error(403, code or "authority_unknown")
                if definition_id is not None:
                    result = _definition_read(org, definition_id, human=human, version=version)
                else:
                    result = {"definitions": [_definition(d) for d in org.db.list_task_attribute_definitions(
                        include_archived=include_archived)], "editability": _definition_editability(human)}
        return JSONResponse(result, headers=_NO_STORE)
    except (ValueError, sqlite3.Error) as exc:
        _storage_error(exc)


@router.get("/task-attributes/definitions", response_model=DefinitionsRead, responses=_RESPONSES)
async def list_definitions(request: Request, org: Org, include_archived: bool = False, session_id: str | None = None):
    _query(request, {"include_archived", "session_id"})
    return await _discovery(request, org, session_id, include_archived=include_archived)


@router.get("/task-attributes/definitions/{definition_id}", response_model=DefinitionRead, responses=_RESPONSES)
async def get_definition(request: Request, org: Org, definition_id: str, version: int | None = None, session_id: str | None = None):
    _query(request, {"version", "session_id"})
    if version is not None and (version < 1 or request.query_params["version"] != str(version)):
        _error(422, "attribute_validation_failed", errors={"version": ["invalid_version"]})
    return await _discovery(request, org, session_id, definition_id=definition_id, version=version)


async def _definition_write(request, org, model, operation, definition_id=None):
    if request.query_params or any(key in request.headers for key in (
            "x-happyranch-task-session", "x-happyranch-session-id")):
        _error(403, "human_only")
    _require_human(_check_optional_token(request.headers.get("authorization")))
    command = await _body(request, model, human=True)
    try:
        async with _access(org, None):
            try:
                with org.db._attribute_writer():
                    before = None if definition_id is None else org.db.get_task_attribute_definition(definition_id)
                    fields = {key: value for key, value in command.items() if key != "expected_version"}
                    if operation == "create":
                        after = org.db._create_task_attribute_definition_uncommitted(**fields)
                    elif operation == "update":
                        after = org.db._update_task_attribute_definition_uncommitted(
                            definition_id, expected_version=command["expected_version"], **fields)
                    else:
                        after = org.db._archive_task_attribute_definition_uncommitted(
                            definition_id, expected_version=command["expected_version"])
                    org.db._audit_task_attribute_definition_uncommitted(operation, before, after)
                    result = _definition_read(org, after["definition_id"], human=True)
            except AttributeValidationError as exc:
                if exc.errors.get("key") == ["key_reserved"]:
                    with org.db._attribute_reader():
                        owner = org.db._attribute_key_owner(command["key"])
                        if owner is not None:
                            _error(409, "key_reserved", current=_definition_read(org, owner["definition_id"], human=True))
                _error(422, "attribute_validation_failed", errors=exc.errors)
            except AttributeStoreError as exc:
                if exc.code in ("definition_version_conflict", "definition_archived", "definition_structure_frozen"):
                    with org.db._attribute_reader():
                        _error(409, exc.code, current=_definition_read(org, definition_id, human=True))
                raise
        return JSONResponse(result, status_code=201 if operation == "create" else 200, headers=_NO_STORE)
    except (ValueError, sqlite3.Error) as exc:
        _storage_error(exc)


@router.post("/task-attributes/definitions", status_code=201, response_model=DefinitionRead,
             responses=_RESPONSES, openapi_extra=body_schema(CreateDefinition))
async def create_definition(request: Request, org: Org):
    return await _definition_write(request, org, CreateDefinition, "create")


@router.patch("/task-attributes/definitions/{definition_id}", response_model=DefinitionRead,
              responses=_RESPONSES, openapi_extra=body_schema(UpdateDefinition))
async def update_definition(request: Request, org: Org, definition_id: str):
    return await _definition_write(request, org, UpdateDefinition, "update", definition_id)


@router.post("/task-attributes/definitions/{definition_id}/archive", response_model=DefinitionRead,
             responses=_RESPONSES, openapi_extra=body_schema(ArchiveDefinition))
async def archive_definition(request: Request, org: Org, definition_id: str):
    return await _definition_write(request, org, ArchiveDefinition, "archive", definition_id)


async def _task_route(request, org, task_id, session_id, *, changes=None, history=False, definition_id=None):
    context = _context(org, session_id)
    try:
        # Name capture performs filesystem reads outside the DB transaction.
        names = _names(org)
        async with _access(org, context):
            if changes is not None:
                try:
                    with org.db._attribute_writer():
                        editability = _task_editability(request, org, task_id, session_id, context)
                        if not editability["can_edit"]:
                            _error(403, editability["refusal_reason"]["code"])
                        org.db._write_task_attribute_changes_uncommitted(task_id, changes,
                            actor_id="founder" if session_id is None else context[2],
                            actor_kind="founder" if session_id is None else "agent")
                        result = _task_read(request, org, task_id, session_id, context, names)
                except AttributeValidationError as exc:
                    _error(422, "attribute_validation_failed", errors=exc.errors)
                except AttributeStoreError as exc:
                    if exc.code in ("definition_version_conflict", "value_revision_conflict", "definition_archived"):
                        with org.db._attribute_reader():
                            current = _task_read(request, org, task_id, session_id, context, names)
                            if not current["editability"]["can_edit"]:
                                _error(403, current["editability"]["refusal_reason"]["code"])
                            detail = {"current": current}
                            if exc.conflicts is not None:
                                detail["conflicts"] = exc.conflicts
                            # Archived unused fields are deliberately absent from
                            # ordinary task reads, but must be recoverable on conflict.
                            if exc.definition_id and all(a["definition"]["definition_id"] != exc.definition_id
                                                         for a in current["attributes"]):
                                field = org.db.get_task_attribute(task_id, exc.definition_id)
                                field["definition"] = _definition(field["definition"])
                                field["history"] = _history(field["history"], names)
                                field["editability"] = _editability("definition_archived")
                                current["attributes"].append(field)
                            _error(409, exc.code, **detail)
                    raise
            else:
                with org.db._attribute_reader():
                    if history:
                        if _task_row(org, task_id) is None:
                            _error(404, "not_found")
                        if definition_id is not None:
                            org.db.get_task_attribute_definition(definition_id)
                        result = {"task_id": task_id, **_history(org.db.get_task_attribute_history(
                            task_id, definition_id=definition_id), names)}
                    else:
                        result = _task_read(request, org, task_id, session_id, context, names)
        return JSONResponse(result, headers=_NO_STORE)
    except (ValueError, sqlite3.Error) as exc:
        _storage_error(exc)


@router.get("/tasks/{task_id}/attributes", response_model=TaskAttributesRead, responses=_RESPONSES,
            dependencies=[Depends(_check_token)])
async def get_attributes(request: Request, org: Org, task_id: str, session_id: str | None = None):
    _query(request, {"session_id"})
    return await _task_route(request, org, task_id, session_id)


@router.put("/tasks/{task_id}/attributes", response_model=TaskAttributesRead, responses=_RESPONSES,
            dependencies=[Depends(_check_token)], openapi_extra=body_schema(SaveAttributes))
async def save_attributes(request: Request, org: Org, task_id: str, session_id: str | None = None):
    _query(request, {"session_id"})
    command = await _body(request, SaveAttributes)
    return await _task_route(request, org, task_id, session_id, changes=command["changes"])


@router.get("/tasks/{task_id}/attributes/history", response_model=TaskHistoryRead, responses=_RESPONSES,
            dependencies=[Depends(_check_token)])
async def get_attribute_history(request: Request, org: Org, task_id: str,
                                definition_id: str | None = None, session_id: str | None = None):
    _query(request, {"session_id", "definition_id"})
    return await _task_route(request, org, task_id, session_id, history=True, definition_id=definition_id)
