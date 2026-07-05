"""System assistant setup and status routes."""
from __future__ import annotations

from pathlib import Path
import secrets
import shutil
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from runtime.daemon import paths as daemon_paths
from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    clear_assistant_config,
    load_assistant_config,
    prepare_assistant_registration_workspace,
    save_assistant_config,
    system_assistant_paths,
)

router = APIRouter()
# Browsers cannot set the Authorization header on ``new WebSocket()``, so the
# bearer token may also be offered as a ``Sec-WebSocket-Protocol`` subprotocol
# ``happyranch.bearer.<token>`` (THR-006 Option A).
_BEARER_SUBPROTOCOL_PREFIX = "happyranch.bearer."


class InitAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reconfigure: bool = False


class RegisterAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor: str
    command: str
    argv: list[str] = Field(default_factory=list)


def _runtime_root(request: Request) -> Path:
    state: DaemonState = request.app.state.daemon
    if state.runtime is None:
        raise HTTPException(status_code=409, detail={"code": "no_active_runtime"})
    return state.runtime.root


def _require_current_runtime_root(state: DaemonState, expected_root: Path) -> Path:
    if state.runtime is None:
        raise HTTPException(status_code=409, detail={"code": "no_active_runtime"})
    current_root = state.runtime.root
    if current_root != expected_root:
        raise HTTPException(
            status_code=409,
            detail={"code": "assistant_runtime_changed"},
        )
    return current_root


def _assistant_error(code: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": str(exc)})


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    """The subprotocols the client offered on the WS upgrade.

    Reads the ASGI ``scope["subprotocols"]`` list when present (real servers
    and the Starlette test client populate it from the upgrade header), falling
    back to parsing the raw ``Sec-WebSocket-Protocol`` header otherwise.
    """
    scope = getattr(websocket, "scope", None)
    if isinstance(scope, dict):
        offered = scope.get("subprotocols")
        if offered:
            return list(offered)
    header = websocket.headers.get("sec-websocket-protocol")
    if header:
        return [part.strip() for part in header.split(",") if part.strip()]
    return []


def _websocket_bearer_subprotocol(websocket: WebSocket) -> str | None:
    """The ``happyranch.bearer.<token>`` subprotocol the client offered, if any."""
    for offered in _offered_subprotocols(websocket):
        if offered.startswith(_BEARER_SUBPROTOCOL_PREFIX):
            return offered
    return None


def _websocket_token_is_valid(websocket: WebSocket) -> bool:
    expected = daemon_paths.read_token()
    if expected is None:
        return False
    prefix = "Bearer "
    authorization = websocket.headers.get("authorization")
    if authorization is not None and authorization.startswith(prefix):
        candidate: str | None = authorization[len(prefix):]
    else:
        # Browser path (THR-006 Option A): token via the bearer subprotocol.
        subprotocol = _websocket_bearer_subprotocol(websocket)
        candidate = (
            subprotocol[len(_BEARER_SUBPROTOCOL_PREFIX):]
            if subprotocol is not None
            else None
        )
    if candidate is None:
        return False
    return secrets.compare_digest(candidate, expected)


def _assistant_init_hint(state: AssistantState, detail: str | None) -> str:
    if state == AssistantState.UNINITIALIZED:
        return "assistant_init_required: configure the system assistant before attaching"
    if detail:
        return f"assistant_init_required: repair the system assistant before attaching ({detail})"
    return "assistant_init_required: repair the system assistant before attaching"


async def _safe_websocket_send_text(websocket: WebSocket, text: str) -> bool:
    try:
        await websocket.send_text(text)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return False
    return True


async def _safe_websocket_close(websocket: WebSocket, *, code: int) -> None:
    try:
        await websocket.close(code=code)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return


@router.get("/assistant/status", dependencies=[require_token()])
async def get_assistant_status(request: Request) -> dict[str, Any]:
    root = _runtime_root(request)
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/init", dependencies=[require_token()])
async def init_assistant(
    body: InitAssistantRequest,
    request: Request,
) -> dict[str, Any]:
    root = _runtime_root(request)
    state: DaemonState = request.app.state.daemon
    try:
        async with state.assistant_lifecycle_lock:
            root = _require_current_runtime_root(state, root)
            current = classify_assistant_state(root)
            if current.state == AssistantState.CONFIGURED and not body.reconfigure:
                return current.model_dump()
            if body.reconfigure:
                clear_assistant_config(root)
            prepare_assistant_registration_workspace(root)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/register", dependencies=[require_token()])
async def register_assistant(
    body: RegisterAssistantRequest,
    request: Request,
) -> dict[str, Any]:
    root = _runtime_root(request)
    state: DaemonState = request.app.state.daemon

    executor = body.executor.strip()
    command = body.command.strip()
    argv = [a for a in body.argv if a and a.strip()] or ([command] if command else [])
    if not executor or not command or not argv:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "assistant_registration_invalid",
                "message": "executor, command, and argv must be non-empty",
            },
        )
    if shutil.which(argv[0]) is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "assistant_executable_not_found",
                "executable": argv[0],
            },
        )

    paths = system_assistant_paths(root)
    try:
        config = AssistantConfig(
            selected_executor=executor,
            selected_command=command,
            selected_argv=argv,
            workspace_path=str(paths.workspace),
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "assistant_registration_invalid", "message": str(exc)},
        ) from exc

    try:
        async with state.assistant_lifecycle_lock:
            root = _require_current_runtime_root(state, root)
            paths = system_assistant_paths(root)
            config = AssistantConfig(
                selected_executor=executor,
                selected_command=command,
                selected_argv=argv,
                workspace_path=str(paths.workspace),
            )
            bootstrap_assistant_workspace(root, executor=executor)
            save_assistant_config(root, config)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/repair", dependencies=[require_token()])
async def repair_assistant(request: Request) -> dict[str, Any]:
    try:
        state: DaemonState = request.app.state.daemon
        async with state.assistant_lifecycle_lock:
            root = _runtime_root(request)
            try:
                config = load_assistant_config(root)
            except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
                raise _assistant_error("assistant_config_invalid", exc) from exc
            if config is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "assistant_not_configured"},
                )
            bootstrap_assistant_workspace(root, executor=config.selected_executor)
            save_assistant_config(root, config)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()
