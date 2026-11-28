"""Metadata-only bearer API. Request values are parsed without input echo."""
from __future__ import annotations

import json
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from runtime.daemon.auth import _check_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.org_secrets.names import SecretError
from runtime.org_secrets.store import store_for

_NO_STORE = {'Cache-Control': 'no-store'}
_MESSAGES = {'reserved_name': 'Name is reserved for runtime, launcher, interpreter, shell, loader or locale controls. Choose an application credential name.',
             'secret_exists': 'Secret already exists. Use Replace value.',
             'key_locked': 'Secrets locked. Log in to unlock, then check key status.'}
_MAX_REQUEST_BYTES = 400000  # 64 KiB UTF-8 value, including JSON escaping


class SecretRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                response = await handler(request)
            except SecretError as exc:
                response = JSONResponse({'detail': {'code': exc.code, 'message': _MESSAGES.get(exc.code, exc.code)}}, status_code=exc.status)
            except HTTPException as exc:
                code = {401: 'unauthorized', 404: 'not_found', 409: 'org_unavailable'}.get(exc.status_code, 'request_refused')
                response = JSONResponse({'detail': {'code': code, 'message': code}}, status_code=exc.status_code)
            except RequestValidationError:
                response = JSONResponse({'detail': {'code': 'invalid_request', 'message': 'invalid_request'}}, status_code=422)
            except Exception:
                # Never log dependency exception text, body, or validation input.
                response = JSONResponse({'detail': {'code': 'store_unavailable', 'message': 'store_unavailable'}}, status_code=503)
            response.headers.update(_NO_STORE)
            return response
        return guarded


router = APIRouter(route_class=SecretRoute, dependencies=[Depends(_check_token)])


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SecretError('invalid_request')
        result[key] = value
    return result


async def _body(request: Request, fields: set[str]) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise SecretError('value_too_large', 413)
    try:
        result = json.loads(raw, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise SecretError('invalid_request') from None
    if not isinstance(result, dict) or result.keys() != fields:
        raise SecretError('invalid_request')
    return result


def _operation(request: Request) -> str:
    values = request.headers.getlist('x-secret-operation')
    if len(values) != 1:
        raise SecretError('invalid_operation_id')
    return values[0]


@router.get('/secrets')
def list_secrets(org: OrgDep) -> dict:
    return store_for(org.root).metadata()


@router.post('/secrets/key-status')
def key_status(org: OrgDep) -> dict:
    store = store_for(org.root)
    state = store.probe()
    return {'key_state': state, 'hold_reason': None if store.ready() else 'org_secrets_locked'}


@router.get('/secrets/operations/{operation_id}')
def operation_result(operation_id: str, org: OrgDep) -> dict:
    return store_for(org.root).operation(operation_id)


@router.post('/secrets', status_code=201)
async def create_secret(request: Request, org: OrgDep) -> dict:
    body = await _body(request, {'name', 'value'})
    return await asyncio.to_thread(store_for(org.root).mutate, 'create', body['name'], body['value'], _operation(request))


@router.put('/secrets/{name}')
async def replace_secret(name: str, request: Request, org: OrgDep) -> dict:
    body = await _body(request, {'value'})
    return await asyncio.to_thread(store_for(org.root).mutate, 'replace', name, body['value'], _operation(request))


@router.delete('/secrets/{name}')
def delete_secret(name: str, request: Request, org: OrgDep) -> dict:
    return store_for(org.root).mutate('delete', name, None, _operation(request))
