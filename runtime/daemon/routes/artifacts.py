"""Bearer-protected, org-owned artifact admission and lifecycle projections."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
import asyncio
from pydantic import BaseModel
from fastapi.exceptions import RequestValidationError
from collections.abc import AsyncIterator, Iterator

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName, MAX_ARTIFACT_BYTES
from runtime.infrastructure.shared_artifact_service import SharedArtifactError, SharedArtifactService
from runtime.orchestrator._paths import OrgPaths

router = APIRouter(dependencies=[require_token()])


def artifact_service(org: OrgState) -> SharedArtifactService:
    def manager(thread_id: str) -> str | None:
        thread = org.db.get_thread(thread_id)
        teams = getattr(org, 'teams', None)
        if not thread or not teams:
            return None
        team = teams.team_for_agent(thread.composed_by)
        if team:
            return teams.executable_manager_for_team(team)
        managers = set()
        for participant in org.db.list_thread_participants(thread_id):
            team = teams.team_for_agent(participant.agent_name)
            if team:
                value = teams.executable_manager_for_team(team)
                if value:
                    managers.add(value)
        return next(iter(managers)) if len(managers) == 1 else None
    service = SharedArtifactService(org.db, ArtifactStore(OrgPaths(org.root).artifacts_dir),
        known_agents={path.stem for path in (org.root/'org'/'agents').glob('*.md')},
        manager_for_thread=manager,download_prefix=f"/api/v1/orgs/{org.slug}/artifacts")
    org.db._shared_artifact_service = service
    return service


def artifact_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SharedArtifactError):
        return HTTPException(status_code=exc.status, detail=exc.detail)
    return HTTPException(status_code=400,detail={'code':'invalid_artifact_name','message':str(exc)})


async def artifact_error_boundary(request: Request) -> AsyncIterator[None]:
    """Map transactional attachment/lifecycle refusals at the HTTP boundary."""
    try:
        yield
    except SharedArtifactError as exc:
        raise artifact_http_error(exc) from exc
    except RequestValidationError as exc:
        if '/artifacts' not in request.url.path:
            raise
        fields = {'.'.join(str(part) for part in error['loc']):error['type'] for error in exc.errors()}
        raise HTTPException(422,detail={'code':'artifact_metadata_invalid','fields':fields}) from exc


router.dependencies.append(Depends(artifact_error_boundary))


@router.post('/artifacts')
async def put_artifact(
    slug: str, org: OrgDep, request: Request, file: UploadFile = File(...), name: str | None = Query(None),
    agent: str = Query(''), task_id: str | None = Query(None), thread_id: str | None = Query(None),
    purpose: str | None = Query(None), retention: str | None = Query(None),
    reason: str | None = Query(None), expires_at: str | None = Query(None),
    operation_id: str | None = Query(None),
) -> dict:
    service = artifact_service(org)
    effective_name = name or file.filename or ''
    try:
        service.store.validate_name(effective_name)
        metadata = service.metadata(agent=agent,task_id=task_id,thread_id=thread_id,purpose=purpose,
            retention=retention,reason=reason,expires_at=expires_at,provisional=request.headers.get("X-HappyRanch-Surface") != "cli")
        content = await file.read(MAX_ARTIFACT_BYTES+1)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise SharedArtifactError(413,'artifact_too_large',max_bytes=MAX_ARTIFACT_BYTES)
        return await asyncio.to_thread(service.put,effective_name,content,metadata,operation_id=operation_id)
    except (SharedArtifactError, InvalidArtifactName) as exc:
        if isinstance(exc,SharedArtifactError) and exc.detail['code'] == 'artifact_quota_exceeded':
            service.audit.log_artifact_quota(action='artifact_quota_refused',agent=agent,
                                             payload=exc.detail,uncommitted=False)
        raise artifact_http_error(exc) from exc


class ArtifactGrantBody(BaseModel):
    # Stable errors are produced at the boundary rather than FastAPI's generic missing-field shape.
    agent: str = ''
    continuing_use: str = ''
    expires_at: str | None = None
    retention: str | None = None
    reason: str | None = None


async def _grant(org: OrgState, name: str, body: ArtifactGrantBody, *, restore: bool) -> dict:
    try:
        if body.expires_at is None:
            raise SharedArtifactError(422,'artifact_metadata_required',missing_fields=['expires_at'])
        return await asyncio.to_thread(artifact_service(org).renew,name,agent=body.agent,continuing_use=body.continuing_use,
            expires_at=body.expires_at,retention=body.retention,reason=body.reason,restore=restore)
    except (SharedArtifactError,InvalidArtifactName) as exc:
        raise artifact_http_error(exc) from exc


# Lifecycle paths precede the catch-all, including nested artifact names.
@router.post('/artifacts/{name:path}/renew')
async def renew_artifact(slug: str,name: str,body: ArtifactGrantBody,org: OrgDep) -> dict:
    return await _grant(org,name,body,restore=False)


@router.post('/artifacts/{name:path}/restore')
async def restore_artifact(slug: str,name: str,body: ArtifactGrantBody,org: OrgDep) -> dict:
    return await _grant(org,name,body,restore=True)


@router.get('/artifacts')
async def list_artifacts(slug: str,org: OrgDep,prefix: str = Query(''),available_only: bool = Query(False)) -> dict:
    service = artifact_service(org)
    values = {info.name:dict(name=info.name,size_bytes=info.size_bytes,modified_at=info.modified_at,
                           state='legacy_unclassified',expires_at=None) for info in service.store.list_artifacts(prefix)}
    with org.db.coherent_read_view() as conn:
        for row in conn.execute('SELECT g.* FROM shared_artifacts a JOIN shared_artifact_generations g ON g.name=a.name AND g.generation=a.current_generation'):
            if row['name'].startswith(prefix):
                projected = service.projection(dict(row))
                if projected['modified_at'] is None:
                    projected['modified_at'] = values.get(row['name'],{}).get('modified_at')
                values[row['name']] = projected
    return {'artifacts':[values[name] for name in sorted(values) if not available_only or values[name]['state'] not in ('recoverable','deleted')]}


@router.get('/artifacts/{name:path}')
async def get_artifact(slug: str,name: str,org: OrgDep,generation: int | None = Query(None,ge=1)) -> StreamingResponse:
    service = artifact_service(org)
    try:
        stream,metadata = await asyncio.to_thread(service.open_download,name,generation)
        from runtime.daemon.content_disposition import attachment_content_disposition
        def chunks() -> Iterator[bytes]:
            with stream:
                yield from iter(lambda:stream.read(64*1024),b'')
        return StreamingResponse(chunks(),background=BackgroundTask(stream.close),media_type='application/octet-stream',headers={
            'Content-Disposition':attachment_content_disposition(name),
            'X-Artifact-Expires-At':metadata.get('expires_at') or '',
            'X-Artifact-Generation':str(metadata.get('generation') or ''),
            'X-Artifact-Protected':str(bool(metadata.get('hold_reason'))).lower(),
        })
    except (SharedArtifactError,InvalidArtifactName) as exc:
        raise artifact_http_error(exc) from exc


@router.delete('/artifacts/{name:path}')
async def delete_artifact(slug: str,name: str,org: OrgDep,agent: str = Query(...)) -> dict:
    service = artifact_service(org)
    try:
        return await asyncio.to_thread(service.retire,name,agent=agent)
    except (SharedArtifactError,InvalidArtifactName) as exc:
        raise artifact_http_error(exc) from exc
