"""Liveness and readiness endpoints."""
from __future__ import annotations

import shutil
from typing import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from runtime.config import Settings, settings as _settings
from runtime.orchestrator.executor_binary_registry import (
    get_binary,
    is_binary_valid,
)
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
    """Return a short registration hint for a known executor.

    - **Built-ins**: the machine-local registry (executor_binary_registry)
      is the source of truth for whether an executor is 'registered' on
      this machine. Registration happens via the onboarding prompt flow
      (copy-paste), not by being on PATH.
    - **Custom profiles**: the profile's declared ``command`` must be on
      the daemon's PATH. Registration happens via the Settings → Executors
      custom-connect flow.
    """
    hints: dict[str, str] = {
        "claude": "Register Claude Code via the onboarding prompt flow",
        "codex": "Register OpenAI Codex via the onboarding prompt flow",
        "opencode": "Register opencode via the onboarding prompt flow",
        "pi": "Register Pi via the onboarding prompt flow",
    }
    return hints.get(
        profile_name,
        f"Verify the '{profile_name}' command is on the daemon's PATH.",
    )


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
    """Return per-executor CLI registration status.

    Enumerates the exact executors the registry knows (built-in +
    org-registered custom profiles).

    - **Built-ins** (claude/codex/opencode/pi): ``present`` = executor has
      an entry in the machine-local binary registry (``executors.json``)
      with a valid stored path.  A built-in counts as 'connected' ONLY
      after the user explicitly registers its binary via the onboarding
      prompt flow — being on PATH is NOT sufficient.

    - **Custom profiles**: ``present`` = the profile's declared ``command``
      resolves to an executable on the daemon's PATH (via ``shutil.which``).
      No ``executors.json`` entry is required for a custom profile to be
      considered present — the profile's own ``command`` field IS the
      executable declaration (issue #490).  ``path`` is the resolved
      absolute path to the executable, or ``None`` when unresolved.

    Honesty fence: invents no badges, metrics, or fake status — just
    registered/not-registered + hint.
    """
    state = request.app.state.daemon
    registry = get_registry()
    names = registry.list_profile_names()
    results: list[ExecutorPrereq] = []
    for name in names:
        profile = registry.get_profile(name)
        if profile is None:
            continue
        if profile.kind == "custom":
            # Custom profile — derive present/path from the profile's
            # declared ``command``, resolved via the same shutil.which
            # semantics used at registration/validation time.
            cmd = profile.command
            resolved = _presence_checker(cmd) if cmd else None
            present = resolved is not None
            results.append(ExecutorPrereq(
                tool=name,
                present=present,
                path=resolved if present else None,
                hint=_hint_for(name),
            ))
        else:
            # Built-in — requires an explicit executors.json entry.
            cli = _get_cli_binary(name, state.settings)
            if not cli:
                continue
            stored = get_binary(name)
            registered = stored is not None and is_binary_valid(stored)
            results.append(ExecutorPrereq(
                tool=name,
                present=registered,
                path=stored if registered else None,
                hint=_hint_for(name),
            ))
    return PrereqsResponse(prereqs=results)
