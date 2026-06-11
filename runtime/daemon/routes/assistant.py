"""System assistant setup and status routes."""
from __future__ import annotations

import asyncio
import contextlib
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
from runtime.daemon.assistant_pty import AssistantPtySession
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
_RESIZE_CONTROL_PREFIX = "__HAPPYRANCH_ASSISTANT_RESIZE__"


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


def _websocket_token_is_valid(websocket: WebSocket) -> bool:
    expected = daemon_paths.read_token()
    if expected is None:
        return False
    authorization = websocket.headers.get("authorization")
    if authorization is None:
        return False
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    candidate = authorization[len(prefix):]
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


def _parse_resize_control(text: str) -> tuple[int, int] | None:
    if not text.startswith(_RESIZE_CONTROL_PREFIX):
        return None
    parts = text.split()
    if len(parts) != 3:
        return None
    try:
        rows = int(parts[1])
        cols = int(parts[2])
    except ValueError:
        return None
    if rows <= 0 or cols <= 0:
        return None
    return rows, cols


async def _pump_assistant_output(
    websocket: WebSocket,
    session: AssistantPtySession,
    queue: asyncio.Queue[str | None],
) -> None:
    try:
        while True:
            text = await queue.get()
            if text is None:
                await _safe_websocket_close(
                    websocket,
                    code=status.WS_1000_NORMAL_CLOSURE,
                )
                return
            if not await _safe_websocket_send_text(websocket, text):
                return
    finally:
        session.unsubscribe(queue)


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
                await state.assistant_sessions.close_all()
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
            await state.assistant_sessions.close_all()
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
            await state.assistant_sessions.close_all()
            bootstrap_assistant_workspace(root, executor=config.selected_executor)
            save_assistant_config(root, config)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()


@router.websocket("/assistant/session")
async def attach_assistant_session(websocket: WebSocket) -> None:
    if not _websocket_token_is_valid(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    state_obj = websocket.app.state.daemon
    assert isinstance(state_obj, DaemonState)
    try:
        async with state_obj.assistant_lifecycle_lock:
            if state_obj.runtime is None:
                await websocket.send_text("assistant_init_required: no active runtime")
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

            root = state_obj.runtime.root
            assistant_status = classify_assistant_state(root)
            if assistant_status.state != AssistantState.CONFIGURED:
                await websocket.send_text(
                    _assistant_init_hint(assistant_status.state, assistant_status.detail)
                )
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

            try:
                config = load_assistant_config(root)
            except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
                await websocket.send_text(
                    _assistant_init_hint(AssistantState.STALE_OR_BROKEN, str(exc))
                )
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return
            if config is None:
                await websocket.send_text(
                    _assistant_init_hint(AssistantState.UNINITIALIZED, None)
                )
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

            session = await state_obj.assistant_sessions.get_or_start(
                command=config.selected_command,
                argv=config.selected_argv,
                workspace=system_assistant_paths(root).workspace,
            )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        await websocket.send_text(f"assistant_launch_failed: {exc}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    queue = session.subscribe()
    output_task = asyncio.create_task(_pump_assistant_output(websocket, session, queue))
    try:
        while True:
            text = await websocket.receive_text()
            resize = _parse_resize_control(text)
            if resize is not None:
                rows, cols = resize
                await session.resize(rows=rows, cols=cols)
                continue
            await session.write_text(text)
    except WebSocketDisconnect:
        return
    finally:
        output_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await output_task
