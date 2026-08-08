"""Slice-A direct custom-CLI ingress: durable receipt, never projection or launch."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from runtime.daemon import paths
from runtime.daemon.direct_connect_store import canonical_wrapper_destination
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX, _RUNTIME_ORG

router = APIRouter()
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class UpgradeableChild(BaseModel):
    slot: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    executable: Annotated[str, Field(min_length=1)]
    version_probe_argv: list[Annotated[str, Field(min_length=1)]]

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def exact_path_probe(self) -> "UpgradeableChild":
        executable = Path(self.executable)
        if not executable.is_absolute() or ".." in executable.parts:
            raise ValueError("child executable must be a lexical absolute no-escape path")
        if not self.version_probe_argv or self.version_probe_argv[0] != self.executable:
            raise ValueError("version_probe_argv must begin with the declared child executable")
        return self


class DirectManifestV2(BaseModel):
    manifest_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    wrapper_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    upgradeable_children: Annotated[list[UpgradeableChild], Field(min_length=1)]
    workspace_adapter_id: Literal["claude", "codex", "opencode", "pi"] = Field(
        ...,
        description=(
            "Which first-party workspace-bootstrap convention this CLI's "
            "agent workspaces should use (Claude-style .claude/settings.json "
            "+ CLAUDE.md, or AGENTS.md-style for codex/opencode/pi). Declared "
            "by the connecting wrapper, not chosen by the founder — only the "
            "wrapper author knows which convention their CLI expects."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def unique_children(self) -> "DirectManifestV2":
        slots = [child.slot for child in self.upgradeable_children]
        paths_ = [child.executable for child in self.upgradeable_children]
        if len(slots) != len(set(slots)) or len(paths_) != len(set(paths_)):
            raise ValueError("direct manifest child slots and paths must be unique")
        return self


class DirectConnectRequest(BaseModel):
    metadata: dict[str, str] = Field(default_factory=dict)
    manifest: DirectManifestV2

    model_config = {"extra": "forbid"}

    @field_validator("metadata")
    @classmethod
    def metadata_is_nonsecret(cls, metadata: dict[str, str]) -> dict[str, str]:
        for key, value in metadata.items():
            if key.lower() in {"authorization", "token", "registration_token"} or REGISTRATION_TOKEN_PREFIX in value:
                raise ValueError("direct metadata cannot contain authorization material")
        return metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _no_symlink_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink is forbidden in direct artifact path: {current}")


def _artifact_facts(path: Path, *, expected_path: Path | None = None) -> tuple[str, dict[str, object]]:
    if expected_path is not None and path != expected_path:
        raise ValueError("wrapper is not at the server-issued canonical path")
    _no_symlink_ancestors(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ValueError("direct artifact must be a regular executable")
    if path.resolve() != path:
        raise ValueError("direct artifact path must resolve exactly to itself")
    parent = path.parent.stat()
    return _sha256(path), {
        "owner_uid": info.st_uid,
        "owner_gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "parent_realpath": str(path.parent.resolve()),
        "parent_dev": parent.st_dev,
        "parent_ino": parent.st_ino,
    }


def _reject(detail: str, code: int = status.HTTP_401_UNAUTHORIZED) -> None:
    raise HTTPException(status_code=code, detail=detail)


@router.post("/runtime/custom-cli/connect", status_code=status.HTTP_201_CREATED)
async def connect(request: Request) -> dict[str, str]:
    """Accept one canonical direct manifest and issue only a nonlaunchable receipt."""
    peer = request.client.host if request.client else None
    if peer not in _LOCAL_HOSTS:
        _reject("direct connect is loopback-only", status.HTTP_403_FORBIDDEN)
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if not token.startswith(REGISTRATION_TOKEN_PREFIX) or token == paths.read_token():
        _reject("direct connect requires a registration token")
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        _reject("direct authority unavailable", status.HTTP_500_INTERNAL_SERVER_ERROR)
    authority = authority_store.get_for_token(token)
    if authority is None:
        _reject("invalid direct registration context")
    expected_wrapper = canonical_wrapper_destination(
        getattr(authority_store, "_runtime_root", None), authority.intended_profile_name
    )
    if authority.wrapper_destination != expected_wrapper:
        authority_store.terminalize_known(token, "noncanonical_authority_target")
        daemon.registration_token_store.consume_runtime(token)
        _reject("invalid direct registration context")
    token_store = daemon.registration_token_store
    now = time.time()
    token_record = token_store.validate_runtime(token, now=now)
    if (
        token_record is None or token_record.org != _RUNTIME_ORG
        or token_record.purpose != "adapter"
        or token_record.intended_profile_name != authority.intended_profile_name
        or token_record.consumed or token_record.reserved or token_record.expires_at < now
        or authority.expires_at < now
    ):
        authority_store.terminalize_known(token, "invalid_direct_context", now=now)
        token_store.consume_runtime(token, now=now)
        _reject("invalid direct registration context")
    reserved_record = token_store.reserve_runtime(token, now=now)
    operation_id = authority_store.reserve(token, now=now) if reserved_record is not None else None
    if operation_id is None:
        if reserved_record is not None:
            token_store.commit_runtime(token)
        authority_store.terminalize_known(token, "reservation_refused", now=now)
        _reject("direct registration token is no longer available")
    try:
        raw = await request.json()
        body = DirectConnectRequest.model_validate(raw)
        wrapper_hash, wrapper_facts = _artifact_facts(
            expected_wrapper, expected_path=expected_wrapper
        )
        if wrapper_hash != body.manifest.wrapper_sha256:
            raise ValueError("wrapper hash does not match immutable manifest")
        children: list[dict[str, object]] = []
        for child in body.manifest.upgradeable_children:
            child_path = Path(child.executable)
            child_hash, child_facts = _artifact_facts(child_path)
            if child_path == expected_wrapper:
                raise ValueError("wrapper cannot be an upgradeable child")
            child_facts["version_probe_argv"] = child.version_probe_argv
            children.append({"slot": child.slot, "path": str(child_path), "sha256": child_hash, "facts": child_facts})
        receipt = authority_store.receive(
            token, operation_id, wrapper_sha256=wrapper_hash, wrapper_facts=wrapper_facts,
            children=children, workspace_adapter_id=body.manifest.workspace_adapter_id, now=now,
        )
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        authority_store.terminalize(token, operation_id, "invalid_manifest", now=now)
        token_store.commit_runtime(token)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid direct manifest") from None
    except Exception:
        authority_store.terminalize(token, operation_id, "intake_fault", now=now)
        token_store.commit_runtime(token)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct intake failed") from None
    if not token_store.commit_runtime(token):
        authority_store.compensate_received(token, operation_id, "registration_token_commit_failed", now=now)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct intake receipt unavailable")
    return {"operation_id": receipt.operation_id, "state": receipt.state}
