"""System assistant A-mode (headless, structured-output) WebSocket session.

PR-1 of the THR-056 approach-A dock rebuild.  This route is the new,
ADDITIVE A-mode entry point — ``/assistant/a-mode`` — that streams
normalized ``TurnFrame`` objects over WebSocket in place of raw PTY output.

The existing PTY path at ``/assistant/session`` (``attach_assistant_session``)
is FROZEN — no edits.  A-mode is structured from frame zero with no
dual raw/handshake negotiation.
"""
from __future__ import annotations

import asyncio
import contextlib
import json as _json
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
    Depends,
)
from pydantic import BaseModel, ConfigDict

from runtime.daemon import paths as daemon_paths
from runtime.daemon.headless_assistant import (
    TurnFrame,
    PermissionPosture,
    HeadlessAssistantManager,
    get_adapter,
    run_headless_turn,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import allow_rules_for_agent
from runtime.daemon.auth import require_token
from runtime.daemon.routes.assistant import (
    _websocket_token_is_valid,
    _websocket_bearer_subprotocol,
    _safe_websocket_send_text,
    _safe_websocket_close,
)
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantState,
    classify_assistant_state,
    load_assistant_config,
    system_assistant_paths,
)

router = APIRouter()


class AStatusResponse(BaseModel):
    """Response from GET /assistant/a-mode/status."""
    model_config = ConfigDict(extra="forbid")

    available: bool
    executor: str | None = None
    reason: str | None = None


@router.get("/assistant/a-mode/status", dependencies=[require_token()])
async def get_a_mode_status(request: Request) -> dict[str, Any]:
    """Check whether A-mode is available for the system assistant.

    Returns ``available: true`` when the assistant is configured AND
    an adapter is registered for the selected executor.
    """
    state: DaemonState = request.app.state.daemon
    if state.runtime is None:
        return AStatusResponse(
            available=False,
            reason="no active runtime",
        ).model_dump()

    root = state.runtime.root
    assistant_status = classify_assistant_state(root)
    if assistant_status.state != AssistantState.CONFIGURED:
        return AStatusResponse(
            available=False,
            reason=f"assistant not configured: {assistant_status.state.value}",
        ).model_dump()

    config = load_assistant_config(root)
    if config is None:
        return AStatusResponse(
            available=False,
            reason="assistant config missing",
        ).model_dump()

    adapter = get_adapter(config.selected_executor)
    if adapter is None:
        return AStatusResponse(
            available=False,
            executor=config.selected_executor,
            reason=f"no A-mode adapter for executor '{config.selected_executor}'",
        ).model_dump()

    return AStatusResponse(
        available=True,
        executor=config.selected_executor,
    ).model_dump()


@router.websocket("/assistant/a-mode")
async def attach_assistant_a_mode(websocket: WebSocket) -> None:
    """A-mode (headless, structured-output) assistant session.

    Protocol:
        * Client connects with the same auth as the PTY session (bearer token
          or subprotocol).
        * Frame 0 from client is a JSON startup message:
          ``{"type":"start","text":"<user prompt>"}``
        * Server responds with ``status{code:"ready"}``, then streams
          ``TurnFrame`` objects for the assistant turn.
        * Subsequent client messages are new prompts for new turns.
        * ``{"type":"close"}`` from client ends the session gracefully.
    """
    if not _websocket_token_is_valid(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept(subprotocol=_websocket_bearer_subprotocol(websocket))
    state_obj: DaemonState = websocket.app.state.daemon
    assert isinstance(state_obj, DaemonState)

    if state_obj.runtime is None:
        await _safe_websocket_send_text(
            websocket,
            TurnFrame.error(message="no active runtime").model_dump_json(),
        )
        await _safe_websocket_close(websocket, code=status.WS_1000_NORMAL_CLOSURE)
        return

    root = state_obj.runtime.root
    assistant_status = classify_assistant_state(root)
    if assistant_status.state != AssistantState.CONFIGURED:
        await _safe_websocket_send_text(
            websocket,
            TurnFrame.error(
                message=f"assistant not configured: {assistant_status.state.value}"
            ).model_dump_json(),
        )
        await _safe_websocket_close(websocket, code=status.WS_1000_NORMAL_CLOSURE)
        return

    config = load_assistant_config(root)
    if config is None:
        await _safe_websocket_send_text(
            websocket,
            TurnFrame.error(message="assistant config missing").model_dump_json(),
        )
        await _safe_websocket_close(websocket, code=status.WS_1000_NORMAL_CLOSURE)
        return

    adapter = get_adapter(config.selected_executor)
    if adapter is None:
        await _safe_websocket_send_text(
            websocket,
            TurnFrame.error(
                message=f"a-mode-unavailable: no adapter for executor '{config.selected_executor}'. Use full session."
            ).model_dump_json(),
        )
        await _safe_websocket_close(websocket, code=status.WS_1000_NORMAL_CLOSURE)
        return

    workspace = system_assistant_paths(root).workspace
    headless_manager: HeadlessAssistantManager = state_obj.headless_assistant
    conversation = await headless_manager.get_conversation(workspace=workspace)

    # Replay the persisted structured conversation log (design §4).
    if conversation.turns:
        serialised = _serialise_turns(conversation.turns)
        await _safe_websocket_send_text(
            websocket,
            TurnFrame.history(turns=serialised).model_dump_json(),
        )

    # Send initial ready status.
    await _safe_websocket_send_text(
        websocket,
        TurnFrame.status(code="ready").model_dump_json(),
    )

    async def _send_frame(frame: TurnFrame) -> None:
        await _safe_websocket_send_text(websocket, frame.model_dump_json())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _json.loads(raw)
            except (_json.JSONDecodeError, ValueError):
                await _safe_websocket_send_text(
                    websocket,
                    TurnFrame.error(message="invalid JSON").model_dump_json(),
                )
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")
            if msg_type == "close":
                await _safe_websocket_send_text(
                    websocket,
                    TurnFrame.status(code="session_closed").model_dump_json(),
                )
                await _safe_websocket_close(
                    websocket, code=status.WS_1000_NORMAL_CLOSURE,
                )
                return

            if msg_type == "start":
                prompt = msg.get("text", "")
                if not prompt or not prompt.strip():
                    await _safe_websocket_send_text(
                        websocket,
                        TurnFrame.error(message="prompt must be non-empty").model_dump_json(),
                    )
                    continue

                # Build the permission posture from the org-agent allow_rules
                # machinery — exactly as ClaudeExecutor does (executors.py:580-596).
                # This mirrors the founder-ruled KB posture for claude.
                #
                # Agent identity: read from the assistant workspace's agent.yaml,
                # which the bootstrap writes as `name: system_assistant`
                # (runtime/system_assistant.py:640).  This is the single source
                # of truth — we must NOT pass workspace.name ('workspace') which
                # would resolve a non-existent org/agents/workspace.md.
                import yaml as _yaml
                agent_yaml_path = workspace / "agent.yaml"
                agent_name = "system_assistant"  # fallback
                if agent_yaml_path.exists():
                    try:
                        agent_cfg = _yaml.safe_load(agent_yaml_path.read_text())
                        if isinstance(agent_cfg, dict):
                            an = agent_cfg.get("name")
                            if isinstance(an, str) and an.strip():
                                agent_name = an.strip()
                    except Exception:
                        pass  # fall back to default
                paths = OrgPaths(root=root)
                allowed_tools = " ".join(
                    allow_rules_for_agent(paths, agent_name, cli=True)
                )
                posture = PermissionPosture(
                    claude_allowed_tools=allowed_tools,
                    claude_permission_mode=state_obj.settings.permission_mode,
                )

                await run_headless_turn(
                    manager=headless_manager,
                    adapter=adapter,
                    workspace=workspace,
                    prompt=prompt,
                    conversation=conversation,
                    permission_posture=posture,
                    frame_sender=_send_frame,
                )
                continue

            # Unknown message type — ignore.
            await _safe_websocket_send_text(
                websocket,
                TurnFrame.error(message=f"unknown message type: {msg_type}").model_dump_json(),
            )

    except WebSocketDisconnect:
        # Dock closed — buffer in-flight turn (finish-in-background, design §4).
        await headless_manager.close_workspace(workspace)
        return
    finally:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialise_turns(turns: list[Any]) -> list[dict[str, Any]]:
    """Serialise a list of _TurnRecord objects into dicts suitable for a
    history frame (TurnFrame.history).  Uses model_dump(exclude_none=True) on
    each TurnFrame so the shape matches the save() format."""
    out: list[dict[str, Any]] = []
    for t in turns:
        out.append({
            "id": t.id,
            "prompt": t.prompt,
            "frames": [f.model_dump(exclude_none=True) for f in t.frames],
            "started_at": t.started_at,
            "finished_at": t.finished_at,
            "session_id": t.session_id,
        })
    return out
