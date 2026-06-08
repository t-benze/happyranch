"""System assistant setup and status routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from runtime.daemon.assistant_pty import (
    InteractiveExecutorSpec,
    ProbeResult,
    ProbeRunner,
    build_executor_specs,
)
from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)

router = APIRouter(dependencies=[require_token()])


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


@router.get("/assistant/status")
async def get_assistant_status(request: Request) -> dict[str, Any]:
    root = _runtime_root(request)
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/probes")
def probe_assistant_executors(request: Request) -> dict[str, Any]:
    _runtime_root(request)
    state: DaemonState = request.app.state.daemon
    runner = ProbeRunner()
    probe_results = [
        _probe_result_to_dict(spec, runner.probe_executor(spec))
        for spec in build_executor_specs(state.settings)
    ]
    return {"probe_results": probe_results}


@router.post("/assistant/configure")
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


@router.post("/assistant/repair")
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
