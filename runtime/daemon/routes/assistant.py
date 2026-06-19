"""System assistant setup and status routes."""
from __future__ import annotations

import asyncio
import contextlib
import json as _json
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


async def _pump_assistant_output_structured(
    websocket: WebSocket,
    session: AssistantPtySession,
    queue: asyncio.Queue[str | None],
) -> None:
    """Pump PTY output to WS as structured JSON frames.

    Each chunk of PTY output is wrapped in ``{"type":"output","text":"..."}``
    so the structured chat dock can render it as a turn. The session-close
    sentinel also sends a ``{"type":"status","code":"session_closed"}`` frame.
    """
    try:
        while True:
            text = await queue.get()
            if text is None:
                frame = _json.dumps(
                    {"type": "status", "code": "session_closed"}
                )
                await _safe_websocket_send_text(websocket, frame)
                await _safe_websocket_close(
                    websocket,
                    code=status.WS_1000_NORMAL_CLOSURE,
                )
                return
            frame = _json.dumps({"type": "output", "text": text})
            if not await _safe_websocket_send_text(websocket, frame):
                return
    finally:
        session.unsubscribe(queue)


def _try_parse_handshake(text: str) -> dict[str, Any] | None:
    """Parse a JSON handshake frame; return None if it isn't one."""
    try:
        obj = _json.loads(text)
    except (_json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and obj.get("type") == "handshake":
        return obj
    return None


async def _run_structured_session(
    websocket: WebSocket,
    session: AssistantPtySession,
) -> None:
    """Run the WS session chat I/O loop in structured JSON mode.

    The output pump is already running (managed by the caller).  This function
    only handles the handshake ack and the structured ``{"type":"chat",…}``
    input loop.  Resize control strings and raw text (legacy fallback) are
    still accepted.
    """
    # Ack the handshake.
    await websocket.send_text(
        _json.dumps({"type": "status", "code": "ready"})
    )
    try:
        while True:
            text = await websocket.receive_text()
            # Resize controls still work in structured mode (frozen PTY contract).
            resize = _parse_resize_control(text)
            if resize is not None:
                rows, cols = resize
                await session.resize(rows=rows, cols=cols)
                continue
            # Try structured JSON input.
            try:
                msg = _json.loads(text)
            except (_json.JSONDecodeError, ValueError):
                # Fallback: forward as raw text (backward compat).
                await session.write_text(text)
                continue
            if isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "chat":
                    chat_text = msg.get("text", "")
                    if chat_text:
                        await session.write_text(chat_text + "\n")
                elif msg_type == "resize":
                    rows = msg.get("rows", 0)
                    cols = msg.get("cols", 0)
                    if rows > 0 and cols > 0:
                        await session.resize(rows=rows, cols=cols)
                else:
                    # Unknown structured type — forward raw.
                    await session.write_text(text)
            else:
                await session.write_text(text)
    except WebSocketDisconnect:
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

    # Echo back the bearer subprotocol the browser offered (THR-006 Option A)
    # so the handshake completes; the CLI path offers none, so accept(None) —
    # the prior behaviour — is preserved unchanged.
    await websocket.accept(subprotocol=_websocket_bearer_subprotocol(websocket))
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

    # Buffer early PTY output while we await the first client message to
    # detect a JSON handshake. This guarantees structured clients never
    # receive a raw-text frame before the server classifies the handshake.
    queue = session.subscribe()
    output_buffer: list[str] = []
    output_mode: str = "buffering"

    async def _pump_with_buffering() -> None:
        """Unified output pump: buffers in 'buffering' mode;
        emits structured JSON frames in 'json-chat' mode;
        emits raw text in 'raw' (legacy) mode."""
        nonlocal output_mode
        try:
            while True:
                text = await queue.get()
                if text is None:
                    if output_mode == "json-chat":
                        await _safe_websocket_send_text(
                            websocket,
                            _json.dumps(
                                {"type": "status", "code": "session_closed"}
                            ),
                        )
                    await _safe_websocket_close(
                        websocket, code=status.WS_1000_NORMAL_CLOSURE
                    )
                    return
                if output_mode == "buffering":
                    output_buffer.append(text)
                elif output_mode == "json-chat":
                    await _safe_websocket_send_text(
                        websocket,
                        _json.dumps({"type": "output", "text": text}),
                    )
                else:
                    await _safe_websocket_send_text(websocket, text)
        finally:
            session.unsubscribe(queue)

    output_task = asyncio.create_task(_pump_with_buffering())

    # Negotiate mode: read the first client message to detect a JSON handshake.
    # If it matches, flush buffered output as structured frames and switch to
    # structured mode; otherwise flush as raw text and continue in legacy
    # PTY-tunnel mode (xterm terminal).
    try:
        first_text = await websocket.receive_text()
    except WebSocketDisconnect:
        output_task.cancel()
        return
    handshake = _try_parse_handshake(first_text)
    is_structured = handshake is not None and handshake.get("protocol") == "json-chat"

    # Switch mode so the pump routes new output correctly, then flush any
    # early output that accumulated before the handshake was received.
    output_mode = "json-chat" if is_structured else "raw"
    for buffered in output_buffer:
        if is_structured:
            await _safe_websocket_send_text(
                websocket,
                _json.dumps({"type": "output", "text": buffered}),
            )
        else:
            await _safe_websocket_send_text(websocket, buffered)
    output_buffer.clear()

    if is_structured:
        # Hand off to the structured chat I/O loop (ack + chat input).
        # The output pump is already running.
        await _run_structured_session(websocket, session)
        output_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await output_task
        return

    # Legacy PTY-tunnel mode: replay first_text as normal input.
    try:
        resize = _parse_resize_control(first_text)
        if resize is not None:
            rows, cols = resize
            await session.resize(rows=rows, cols=cols)
        else:
            await session.write_text(first_text)
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
