"""System assistant setup and status routes."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from runtime.daemon import paths as daemon_paths
from runtime.daemon.assistant_pty import (
    AssistantPtySession,
    InteractiveExecutorSpec,
    ProbeResult,
    ProbeRunner,
    build_executor_specs,
)
from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)

router = APIRouter()


class ConfigureAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_executor: str
    probe_results: list["ProbeResultRow"]


class ProbeResultRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    executor: str
    command: str
    argv: list[str]
    name: str
    prompt_surface: str
    output_excerpt: str
    detail: str
    elapsed_seconds: float
    timed_out: bool
    error: str | None
    returncode: int | None


def _runtime_root(request: Request) -> Path:
    state: DaemonState = request.app.state.daemon
    if state.runtime is None:
        raise HTTPException(status_code=409, detail={"code": "no_active_runtime"})
    return state.runtime.root


def _probe_result_to_dict(
    spec: InteractiveExecutorSpec,
    result: ProbeResult,
) -> dict[str, Any]:
    argv = list(spec.argv)
    return {
        "passed": bool(result.passed),
        "executor": result.executor,
        "command": argv[0] if argv else spec.name,
        "argv": argv,
        "name": spec.name,
        "prompt_surface": spec.prompt_surface,
        "output_excerpt": result.output_excerpt,
        "detail": result.detail,
        "elapsed_seconds": result.elapsed_seconds,
        "timed_out": result.timed_out,
        "error": result.error,
        "returncode": result.returncode,
    }


def _matching_passed_probe(
    selected_executor: str,
    probe_results: list[ProbeResultRow],
) -> ProbeResultRow | None:
    for result in probe_results:
        if result.executor == selected_executor and result.passed is True:
            return result
    return None


def _server_selected_command(
    selected_executor: str,
    specs: list[InteractiveExecutorSpec],
) -> str:
    for spec in specs:
        if spec.name == selected_executor:
            return spec.argv[0] if spec.argv else spec.name
    return selected_executor


def _normalize_probe_results(
    probe_results: list[ProbeResultRow],
    specs: list[InteractiveExecutorSpec],
) -> list[dict[str, Any]]:
    specs_by_name = {spec.name: spec for spec in specs}
    normalized: list[dict[str, Any]] = []
    for result in probe_results:
        spec = specs_by_name.get(result.executor)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_probe_executor", "executor": result.executor},
            )
        if result.passed and (
            result.timed_out
            or result.error is not None
            or (result.returncode is not None and result.returncode != 0)
        ):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_probe_result", "executor": result.executor},
            )
        row = result.model_dump()
        argv = list(spec.argv)
        row.update(
            {
                "command": argv[0] if argv else spec.name,
                "argv": argv,
                "name": spec.name,
                "prompt_surface": spec.prompt_surface,
            }
        )
        normalized.append(row)
    return normalized


def _assistant_error(code: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": str(exc)})


def _websocket_token_is_valid(websocket: WebSocket) -> bool:
    expected = daemon_paths.read_token()
    if expected is None:
        return False
    return websocket.query_params.get("token") == expected


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
                    code=status.WS_1011_INTERNAL_ERROR,
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


@router.post("/assistant/probes", dependencies=[require_token()])
def probe_assistant_executors(request: Request) -> dict[str, Any]:
    _runtime_root(request)
    state: DaemonState = request.app.state.daemon
    runner = ProbeRunner()
    probe_results = [
        _probe_result_to_dict(spec, runner.probe_executor(spec))
        for spec in build_executor_specs(state.settings)
    ]
    return {"probe_results": probe_results}


@router.post("/assistant/configure", dependencies=[require_token()])
async def configure_assistant(
    body: ConfigureAssistantRequest,
    request: Request,
) -> dict[str, Any]:
    root = _runtime_root(request)
    state: DaemonState = request.app.state.daemon
    specs = build_executor_specs(state.settings)
    paths = system_assistant_paths(root)
    try:
        AssistantConfig(
            selected_executor=body.selected_executor,
            selected_command=_server_selected_command(body.selected_executor, specs),
            workspace_path=str(paths.workspace),
            latest_probe_results=[],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_assistant_executor", "message": str(exc)},
        ) from exc

    probe_results = _normalize_probe_results(body.probe_results, specs)
    matching_probe = _matching_passed_probe(body.selected_executor, body.probe_results)
    if matching_probe is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "selected_executor_not_probe_passed"},
        )

    try:
        config = AssistantConfig(
            selected_executor=body.selected_executor,
            selected_command=_server_selected_command(body.selected_executor, specs),
            workspace_path=str(paths.workspace),
            latest_probe_results=probe_results,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_assistant_executor", "message": str(exc)},
        ) from exc

    try:
        bootstrap_assistant_workspace(root, executor=body.selected_executor)
        save_assistant_config(root, config)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/repair", dependencies=[require_token()])
async def repair_assistant(request: Request) -> dict[str, Any]:
    root = _runtime_root(request)
    try:
        config = load_assistant_config(root)
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise _assistant_error("assistant_config_invalid", exc) from exc
    if config is None:
        raise HTTPException(status_code=409, detail={"code": "assistant_not_configured"})

    try:
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
        await websocket.send_text(_assistant_init_hint(AssistantState.UNINITIALIZED, None))
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        return

    try:
        session = await state_obj.assistant_sessions.get_or_start(
            command=config.selected_command,
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
            await session.write_text(text)
    except WebSocketDisconnect:
        return
    finally:
        output_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await output_task
