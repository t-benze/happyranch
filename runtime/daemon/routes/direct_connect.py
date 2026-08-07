"""Scoped, daemon-authoritative custom-CLI direct-connect intake.

This route intentionally has its own router: it accepts an ``hrreg_`` token,
not the master bearer, and is mounted before the master-gated adapter router.
It only records a durable, nonlaunchable pre-projection operation.  YAML,
registry, executor construction, and subprocess launch remain out of scope.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from runtime.daemon.direct_connect_store import (
    DirectConnectAuthorityStore,
    _validate_no_symlink_regular_executable,
)
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX

router = APIRouter()
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class DirectDependency(BaseModel):
    """A later-observable child at an explicit same-path identity."""

    slot: str = Field(..., min_length=1)
    executable: str = Field(..., min_length=1)
    version_probe_argv: list[str] = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class DirectConnectRequest(BaseModel):
    """Metadata only: no caller-selected wrapper or authority fields."""

    version: str = Field(..., min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    dependency_manifest_version: StrictInt = Field(..., ge=2, le=2)
    dependencies: list[DirectDependency] = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class DirectConnectResponse(BaseModel):
    adapter_id: str
    state: str
    wrapper_sha256: str


def _raw_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing registration token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith(REGISTRATION_TOKEN_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not a registration token")
    return token


def _terminalize_known(store: DirectConnectAuthorityStore, token: str, reason: str) -> None:
    authority = store.get_for_token(token)
    if authority is not None:
        store.terminalize(authority.token_fingerprint, reason)


def _validate_manifest(dependencies: list[DirectDependency], wrapper_path: str) -> list[dict]:
    """Validate children without selecting or modifying any executable."""
    seen_slots: set[str] = set()
    seen_paths: set[str] = set()
    normalized: list[dict] = []
    for dependency in dependencies:
        if dependency.slot in seen_slots:
            raise ValueError("duplicate dependency slot")
        if dependency.executable in seen_paths or dependency.executable == wrapper_path:
            raise ValueError("duplicate or wrapper dependency executable")
        path = os.fspath(dependency.executable)
        canonical, sha256 = _validate_no_symlink_regular_executable(Path(path))
        if dependency.version_probe_argv[0] != path:
            raise ValueError("version_probe_argv must begin with the exact child executable")
        if any(not isinstance(arg, str) or not arg for arg in dependency.version_probe_argv):
            raise ValueError("version_probe_argv must contain fixed non-empty literals")
        seen_slots.add(dependency.slot)
        seen_paths.add(path)
        normalized.append({
            "slot": dependency.slot,
            "executable": str(canonical),
            "sha256": sha256,
            "version_probe_argv": dependency.version_probe_argv,
            "class": "upgradeable_child",
        })
    return normalized


@router.post(
    "/runtime/custom-cli/connect",
    response_model=DirectConnectResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["version", "dependency_manifest_version", "dependencies"],
                        "properties": {
                            "version": {"type": "string"},
                            "capabilities": {"type": "array", "items": {"type": "string"}},
                            "dependency_manifest_version": {"type": "integer", "const": 2},
                            "dependencies": {"type": "array", "minItems": 1},
                        },
                    }
                }
            },
        }
    },
)
async def connect_direct_custom_cli(request: Request) -> DirectConnectResponse:
    """Commit one validated direct operation, never a legacy adapter entry."""
    peer = request.client.host if request.client else None
    if peer not in _LOCAL_HOSTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "not_localhost", "peer": peer})
    token = _raw_token(request)
    state = request.app.state.daemon
    token_store = state.registration_token_store
    direct_store: DirectConnectAuthorityStore | None = state.direct_connect_authority_store
    if direct_store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="direct authority store unavailable")
    authority = direct_store.get_for_token(token)
    # Unknown/foreign requests retain ordinary invalid-context behavior.
    record = token_store.validate_runtime(token)
    if authority is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid direct registration token")
    if record is None or record.purpose != "adapter" or authority.expires_at <= time.time():
        _terminalize_known(direct_store, token, "expired_or_invalid_authority")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired direct registration token")
    if not token_store.is_challenge_complete_runtime(token):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="direct conformance challenge is incomplete")

    raw_body = await request.body()
    try:
        decoded = json.loads(raw_body)
        body = DirectConnectRequest.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError):
        _terminalize_known(direct_store, token, "malformed_body")
        reserved = token_store.reserve_runtime(token)
        if reserved is not None:
            token_store.commit_runtime(token)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid direct-connect request") from None

    try:
        dependencies = _validate_manifest(body.dependencies, str(authority.wrapper_destination))
        metadata = {"version": body.version.strip(), "capabilities": body.capabilities, "wrapper_class": "immutable_wrapper"}
        if not metadata["version"]:
            raise ValueError("version is required")
        operation = direct_store.commit_pre_projection(
            authority=authority, metadata=metadata, dependencies=dependencies,
        )
    except (ValueError, OSError) as exc:
        _terminalize_known(direct_store, token, str(exc))
        reserved = token_store.reserve_runtime(token)
        if reserved is not None:
            token_store.commit_runtime(token)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None

    # An exact already-committed replay reaches this branch without a second
    # durable event.  Its token was consumed by the first successful commit.
    if operation.state == "pre_projection" and token_store.validate_runtime(token) is not None:
        reserved = token_store.reserve_runtime(token)
        if reserved is None or not token_store.commit_runtime(token):
            _terminalize_known(direct_store, token, "token_commit_failed")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="direct token reservation lost")
    return DirectConnectResponse(
        adapter_id=operation.adapter_id, state=operation.state, wrapper_sha256=operation.wrapper_sha256,
    )
