"""Liveness and readiness endpoints."""
from __future__ import annotations

import shutil
from typing import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from runtime.config import Settings, settings as _settings
from runtime.orchestrator.executor_registry import get_registry

router = APIRouter()

# ---------------------------------------------------------------------------
# Injectable presence-check seam (MEM-110). Default probes the real PATH;
# tests can override to mock without requiring agent CLIs on CI.
# ---------------------------------------------------------------------------

CheckPresence = Callable[[str], str | None]

_presence_checker: CheckPresence = shutil.which


def _set_presence_checker(fn: CheckPresence) -> None:
    """Test seam: inject a mock presence checker."""
    global _presence_checker
    _presence_checker = fn


def _get_cli_binary(profile_name: str, settings: Settings) -> str:
    """Return the CLI binary name for a registered profile name.

    Built-in profiles resolve from Settings; custom profiles carry their
    own ``command`` field. Returns the empty string if the profile is
    unregistered (shouldn't happen — the route enumerates from the registry).
    """
    registry = get_registry()
    profile = registry.get_profile(profile_name)
    if profile is None:
        return ""
    if profile.kind == "builtin":
        # Map profile name → Settings CLI path.
        # These are the four built-ins registered in ExecutorRegistry.
        builtin_map: dict[str, str] = {
            "claude": settings.claude_cli_path,
            "codex": settings.codex_cli_path,
            "opencode": settings.opencode_cli_path,
            "pi": settings.pi_cli_path,
        }
        return builtin_map.get(profile_name, "")
    # Custom profile — use its declared command.
    return profile.command or ""


def _hint_for(profile_name: str) -> str:
    """Return a short install/fix hint for a known executor."""
    hints: dict[str, str] = {
        "claude": "Install Claude Code: npm install -g @anthropic-ai/claude-code",
        "codex": "Install OpenAI Codex: see https://github.com/openai/codex",
        "opencode": "Install opencode: see https://github.com/opencode-ai/opencode",
        "pi": "Install Pi: npm install -g @earendil-works/pi-coding-agent",
    }
    return hints.get(profile_name, f"Install the '{profile_name}' CLI and ensure it is on PATH.")


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class ExecutorPrereq(BaseModel):
    tool: str
    present: bool
    path: str | None
    hint: str


class PrereqsResponse(BaseModel):
    prereqs: list[ExecutorPrereq]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
def health(request: Request) -> dict:
    state = request.app.state.daemon
    return {
        "status": "ok",
        "active_runtime": str(state.runtime.root) if state.runtime else None,
    }


@router.get("/health/prereqs", response_model=PrereqsResponse)
def health_prereqs(request: Request) -> PrereqsResponse:
    """Return per-executor CLI readiness.

    Enumerates the exact executors the registry knows (built-in +
    org-registered custom profiles).  ``present`` = CLI binary found on
    PATH; ``path`` = resolved absolute path or None; ``hint`` = short
    install/fix instruction.

    Honesty fence: invents no badges, metrics, or fake status — just
    present/absent + hint.
    """
    registry = get_registry()
    names = registry.list_profile_names()
    results: list[ExecutorPrereq] = []
    for name in names:
        cli = _get_cli_binary(name, _settings)
        if not cli:
            continue
        resolved = _presence_checker(cli)
        results.append(ExecutorPrereq(
            tool=name,
            present=resolved is not None,
            path=resolved,
            hint=_hint_for(name),
        ))
    return PrereqsResponse(prereqs=results)
