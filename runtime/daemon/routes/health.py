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
from runtime.orchestrator.executor_registry import ExecutorRegistry, get_registry

router = APIRouter()

# ---------------------------------------------------------------------------
# Injectable presence-check seam (MEM-110). Default probes the real PATH;
# tests can override to mock without requiring agent CLIs on CI.
# ---------------------------------------------------------------------------

CheckPresence = Callable[[str], str | None]

# Preserved for test-compatibility with existing imports; no longer
# used in route logic — binary registry is the sole resolution source.
_presence_checker: CheckPresence = shutil.which


def _set_presence_checker(fn: CheckPresence) -> None:
    """Test seam: inject a mock presence checker."""
    global _presence_checker
    _presence_checker = fn


def _get_cli_binary(profile_name: str, settings: Settings) -> str:
    """Return the CLI binary name for a registered profile name.

    Returns the profile's declared command (custom profiles) or the
    corresponding Settings CLI-field value (built-ins) for display/hint
    purposes.  Presence is always determined from the machine-local
    binary registry (``executors.json``), never from this value
    (THR-107 seq155).  Returns the empty string if the profile is
    unregistered.
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
    - **Custom profiles**: same as built-ins — the machine-local binary
      registry (executors.json) is the sole availability gate (THR-107
      seq155). The profile's declared ``command`` no longer gates
      availability.
    """
    hints: dict[str, str] = {
        "claude": "Register Claude Code via the onboarding prompt flow",
        "codex": "Register OpenAI Codex via the onboarding prompt flow",
        "opencode": "Register opencode via the onboarding prompt flow",
        "pi": "Register Pi via the onboarding prompt flow",
    }
    return hints.get(
        profile_name,
        f"Register the '{profile_name}' binary via: "
        f"happyranch executor-binaries register {profile_name} --path <absolute-path>",
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

    - **Custom profiles**: ``present`` = the profile has an entry in the
      machine-local binary registry (``executors.json``) keyed by the
      profile name with a valid stored path.  The profile's declared
      ``command`` is no longer resolved via ``shutil.which`` — binary
      registration is the sole availability gate (THR-107 seq155).

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
            # Custom-adapter profiles: eligibility is determined by the
            # adapter store (APPROVED + hash-verified), NOT by executors.json.
            # The approved adapter executable IS the launch artifact.
            # Generic-cli profiles: require executors.json entry (seq155).
            cmd_adapter = profile.command_adapter_id or ""
            if cmd_adapter.startswith("custom-adapter:"):
                eligibility = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
                adapter_present = eligibility is not None
                adapter_path = eligibility["executable"] if eligibility else None
                results.append(ExecutorPrereq(
                    tool=name,
                    present=adapter_present,
                    path=adapter_path,
                    hint=_hint_for(name),
                ))
            else:
                # Generic-cli custom profile — requires an explicit executors.json entry
                # keyed by the profile name (THR-107 seq155).  Same gate as
                # built-ins; no shutil.which fallback.
                stored = get_binary(name)
                registered = stored is not None and is_binary_valid(stored)
                results.append(ExecutorPrereq(
                    tool=name,
                    present=registered,
                    path=stored if registered else None,
                    hint=_hint_for(name),
                ))
        else:
            # Built-in — presence is determined solely by the machine-local
            # binary registry (executors.json).  Optional Settings CLI
            # metadata does NOT suppress a valid registry pin (THR-107 seq155).
            stored = get_binary(name)
            registered = stored is not None and is_binary_valid(stored)
            results.append(ExecutorPrereq(
                tool=name,
                present=registered,
                path=stored if registered else None,
                hint=_hint_for(name),
            ))
    return PrereqsResponse(prereqs=results)
