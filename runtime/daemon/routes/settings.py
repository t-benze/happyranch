"""GET /api/v1/orgs/{slug}/settings — read-only system + org settings.
PUT /api/v1/orgs/{slug}/settings/org — partial-update editable org fields.

Phase 1: read-only System + Org settings surface.
Phase 2: editable Org settings (dreaming, threads, session_timeout_seconds).

Spec: artifacts/TASK-349/settings-gui-design-spec-v2.md
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from fastapi import APIRouter, HTTPException

from runtime.config import settings as global_settings
from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    OrgConfigError,
    load_org_config,
    save_org_config,
)
from runtime.orchestrator.org_validation import (
    OrgConsistencyError,
    validate_team_membership,
)

router = APIRouter(dependencies=[require_token()])

# ----------------------------------------------------------------
# SystemSettingsView — ALLOW-LIST from module-global Settings
# ----------------------------------------------------------------


class SystemSettingEntry(BaseModel):
    """A single allow-listed system setting carrying its value and
    whether a change requires a daemon restart."""
    value: str | int
    restart_required: bool


class SystemSettingsView(BaseModel):
    """Read-only view of selected daemon-wide settings.

    ALLOW-LIST: only the fields listed below are ever serialized.
    Any unlisted Settings field (permission_mode, codex_sandbox_mode,
    daemon_bind_host, daemon_port, etc.) is excluded by construction.

    Each entry is a ``SystemSettingEntry`` so the ``restart_required``
    flag travels as part of the GET /settings contract (no client-side
    hard-coded duplicate).
    """

    claude_cli_path: SystemSettingEntry
    codex_cli_path: SystemSettingEntry
    opencode_cli_path: SystemSettingEntry
    pi_cli_path: SystemSettingEntry
    session_timeout_seconds: SystemSettingEntry
    max_orchestration_steps: SystemSettingEntry
    queue_workers: SystemSettingEntry
    protocol_dir: SystemSettingEntry

    @classmethod
    def from_settings(cls, s) -> "SystemSettingsView":
        """Build from the module-global Settings."""
        def entry(val: Any, restart: bool) -> SystemSettingEntry:
            return SystemSettingEntry(value=val, restart_required=restart)
        return cls(
            claude_cli_path=entry(s.claude_cli_path, True),
            codex_cli_path=entry(s.codex_cli_path, True),
            opencode_cli_path=entry(s.opencode_cli_path, True),
            pi_cli_path=entry(s.pi_cli_path, True),
            session_timeout_seconds=entry(s.session_timeout_seconds, False),
            max_orchestration_steps=entry(s.max_orchestration_steps, True),
            queue_workers=entry(s.queue_workers, True),
            protocol_dir=entry(s.protocol_dir, True),
        )


# ----------------------------------------------------------------
# OrgSettingsView — ALLOW-LIST mapped from OrgConfig
# ----------------------------------------------------------------


class DreamingScheduleView(BaseModel):
    """Read-only dreaming schedule detail."""
    time: str
    timezone: str


class DreamingAgentsView(BaseModel):
    """Read-only dreaming agent scope."""
    mode: str
    include: list[str]
    exclude: list[str]


class DreamingSettingsView(BaseModel):
    """Read-only dreaming configuration."""
    enabled: bool
    schedule: DreamingScheduleView
    catch_up_on_startup: bool
    agents: DreamingAgentsView


class ThreadsSettingsView(BaseModel):
    """Read-only threads configuration — nested view of the FLATTENED
    OrgConfig dataclass fields."""
    enabled: bool
    default_turn_cap: int
    invocation_timeout_seconds: int | None


class OrgSettingsView(BaseModel):
    """Read-only view of selected org-level settings.

    ALLOW-LIST: only session_timeout_seconds, dreaming, and threads.
    feishu_notifications and any other OrgConfig field are excluded by
    construction — they have NO attribute on this model.
    """

    session_timeout_seconds: int | None
    dreaming: DreamingSettingsView
    threads: ThreadsSettingsView


def _org_config_to_view(cfg) -> OrgSettingsView:
    """Pure function: map OrgConfig → OrgSettingsView (allow-list)."""
    return OrgSettingsView(
        session_timeout_seconds=cfg.session_timeout_seconds,
        dreaming=DreamingSettingsView(
            enabled=cfg.dreaming.enabled,
            schedule=DreamingScheduleView(
                time=cfg.dreaming.schedule_time,
                timezone=cfg.dreaming.timezone,
            ),
            catch_up_on_startup=cfg.dreaming.catch_up_on_startup,
            agents=DreamingAgentsView(
                mode=cfg.dreaming.agent_mode,
                include=list(cfg.dreaming.include_agents),
                exclude=list(cfg.dreaming.exclude_agents),
            ),
        ),
        threads=ThreadsSettingsView(
            enabled=cfg.threads_enabled,
            default_turn_cap=cfg.threads_default_turn_cap,
            invocation_timeout_seconds=cfg.threads_invocation_timeout_seconds,
        ),
    )


# ----------------------------------------------------------------
# Response envelope
# ----------------------------------------------------------------


class SettingsResponse(BaseModel):
    system: SystemSettingsView
    org: OrgSettingsView


# ----------------------------------------------------------------
# Route
# ----------------------------------------------------------------


@router.get("/settings", response_model=SettingsResponse)
def get_settings(slug: str, org: OrgDep) -> SettingsResponse:
    """Return read-only system + org settings for the given org."""
    cfg = load_org_config(OrgPaths(root=org.root))
    return SettingsResponse(
        system=SystemSettingsView.from_settings(global_settings),
        org=_org_config_to_view(cfg),
    )


# ----------------------------------------------------------------
# PATCH models — partial-update, extra='forbid'
# ----------------------------------------------------------------


class DreamingSchedulePatch(BaseModel):
    """Optional dreaming schedule override. Every field is optional —
    absent fields leave the current value untouched."""
    model_config = ConfigDict(extra="forbid")

    time: str | None = None
    timezone: str | None = None

    @field_validator("time")
    @classmethod
    def _time_must_be_hhmm(cls, v: str | None) -> str | None:
        import re
        if v is not None and not re.match(r"^[0-2][0-9]:[0-5][0-9]$", v):
            raise ValueError("dreaming.schedule.time must be HH:MM")
        if v is not None and int(v[:2]) > 23:
            raise ValueError("dreaming.schedule.time hour must be 00–23")
        return v

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_valid(cls, v: str | None) -> str | None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        if v is not None:
            try:
                ZoneInfo(v)
            except ZoneInfoNotFoundError:
                raise ValueError(f"unknown timezone {v!r}")
        return v


class DreamingAgentsPatch(BaseModel):
    """Optional dreaming agent scope override."""
    model_config = ConfigDict(extra="forbid")

    mode: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None

    @field_validator("mode")
    @classmethod
    def _mode_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in {"all", "whitelist"}:
            raise ValueError(f"dreaming.agents.mode must be 'all' or 'whitelist', got {v!r}")
        return v


class DreamingPatch(BaseModel):
    """Optional dreaming configuration override."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    schedule: DreamingSchedulePatch | None = None
    catch_up_on_startup: bool | None = None
    agents: DreamingAgentsPatch | None = None


class ThreadsPatch(BaseModel):
    """Optional threads configuration override."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    default_turn_cap: int | None = None
    invocation_timeout_seconds: int | None = None

    @field_validator("default_turn_cap")
    @classmethod
    def _cap_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("threads.default_turn_cap must be positive")
        return v

    @field_validator("invocation_timeout_seconds")
    @classmethod
    def _timeout_must_be_positive_or_none(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("threads.invocation_timeout_seconds must be positive or null")
        return v


class OrgSettingsPatch(BaseModel):
    """Partial update of non-sensitive OrgConfig fields.

    EVERY field is optional — absent keys leave the current on-disk value
    untouched. ``extra='forbid'`` rejects any unknown or sensitive key
    (feishu_notifications, working_hours, permission_mode, etc.) with a
    422 so the GUI client gets immediate feedback rather than a silent
    no-op.
    """
    model_config = ConfigDict(extra="forbid")

    session_timeout_seconds: int | None = None
    dreaming: DreamingPatch | None = None
    threads: ThreadsPatch | None = None

    @field_validator("session_timeout_seconds")
    @classmethod
    def _timeout_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("session_timeout_seconds must be a positive integer")
        return v


def _patch_to_raw_dict(patch: OrgSettingsPatch) -> dict:
    """Convert the Pydantic patch model to a raw dict suitable for
    ``save_org_config`` deep-merge, stripping ``None``-valued leaves
    so that absent fields are not written."""
    result: dict = {}

    if patch.session_timeout_seconds is not None:
        result["session_timeout_seconds"] = patch.session_timeout_seconds

    if patch.dreaming is not None:
        dreaming: dict = {}
        d = patch.dreaming
        if d.enabled is not None:
            dreaming["enabled"] = d.enabled
        # schedule: build from schedule patch + catch_up_on_startup
        # (catch_up_on_startup lives inside schedule in YAML, but is a peer
        # of schedule in the view/PATCH model for cleaner UI grouping.)
        sched: dict = {}
        if d.schedule is not None:
            if d.schedule.time is not None:
                sched["time"] = d.schedule.time
            if d.schedule.timezone is not None:
                sched["timezone"] = d.schedule.timezone
        if d.catch_up_on_startup is not None:
            sched["catch_up_on_startup"] = d.catch_up_on_startup
        if sched:
            dreaming["schedule"] = sched
        if d.agents is not None:
            agents: dict = {}
            if d.agents.mode is not None:
                agents["mode"] = d.agents.mode
            if d.agents.include is not None:
                agents["include"] = d.agents.include
            if d.agents.exclude is not None:
                agents["exclude"] = d.agents.exclude
            if agents:
                dreaming["agents"] = agents
        if dreaming:
            result["dreaming"] = dreaming

    if patch.threads is not None:
        threads: dict = {}
        t = patch.threads
        if t.enabled is not None:
            threads["enabled"] = t.enabled
        if t.default_turn_cap is not None:
            threads["default_turn_cap"] = t.default_turn_cap
        if t.invocation_timeout_seconds is not None:
            threads["invocation_timeout_seconds"] = t.invocation_timeout_seconds
        if threads:
            result["threads"] = threads

    return result


def _resolve_agent_names(paths: OrgPaths) -> set[str]:
    """Return the set of known agent names from workspaces + agent files."""
    names: set[str] = set()
    for agent_def in prompt_loader.list_agents(paths):
        names.add(agent_def.name)
    return names


def _validate_agent_names(
    include: list[str] | None, exclude: list[str] | None, known: set[str],
) -> list[str]:
    """Check that every name in include/exclude is a known agent.
    Returns a list of human-readable error messages, empty if valid."""
    errors: list[str] = []
    for label, names in (("include", include), ("exclude", exclude)):
        if names:
            unknown = sorted(set(names) - known)
            if unknown:
                errors.append(
                    f"dreaming.agents.{label} references unknown agent(s): "
                    + ", ".join(unknown)
                )
    return errors


@router.put("/settings/org", response_model=SettingsResponse)
def put_org_settings(slug: str, org: OrgDep, patch: OrgSettingsPatch) -> SettingsResponse:
    """Partial-update editable org settings.

    Only allow-listed keys (dreaming, threads, session_timeout_seconds)
    are written — ``feishu_notifications``, ``working_hours``, and any
    other unknown key are carried through verbatim.

    Returns the updated settings snapshot so the client can invalidate
    and re-render in a single round-trip.
    """
    paths = OrgPaths(root=org.root)

    # Pre-flight: validate agent names if dreaming agents are being patched
    patch_raw = _patch_to_raw_dict(patch)
    if "dreaming" in patch_raw:
        dreaming_block = patch_raw["dreaming"]
        if "agents" in dreaming_block:
            agents_block = dreaming_block["agents"]
            known = _resolve_agent_names(paths)
            errors = _validate_agent_names(
                agents_block.get("include"), agents_block.get("exclude"), known,
            )
            if errors:
                detail = {"errors": errors}
                raise HTTPException(status_code=422, detail=detail)

    try:
        save_org_config(paths, patch_raw)
    except OrgConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Return updated snapshot
    cfg = load_org_config(paths)
    return SettingsResponse(
        system=SystemSettingsView.from_settings(global_settings),
        org=_org_config_to_view(cfg),
    )


# ----------------------------------------------------------------
# Teams membership editing — Phase 2
# ----------------------------------------------------------------


class TeamsPatch(BaseModel):
    """Worker-membership-only patch. Manager reassignment and team
    create/delete stay on the founder-gated manage-agent path."""
    model_config = ConfigDict(extra="forbid")

    team: str
    add_workers: list[str] = []
    remove_workers: list[str] = []


@router.put("/settings/teams")
async def put_teams(slug: str, org: OrgDep, patch: TeamsPatch) -> dict:
    """Update worker membership for a single team.

    Wraps ``TeamsRegistry.add_worker`` / ``remove_worker`` (which
    auto-persist to teams.yaml), then re-runs ``validate_team_membership``
    to guarantee consistency. On drift → 409 and the change is rolled
    back to the pre-request state.
    """
    paths = OrgPaths(root=org.root)
    teams = org.teams

    # Validate the team exists
    if patch.team not in set(teams.teams()):
        raise HTTPException(
            status_code=404,
            detail=f"team {patch.team!r} not found",
        )

    # Remember original state for rollback
    m = teams.manager_for_team(patch.team)
    original_workers = m.workers

    async with org.teams_lock:
        for agent in patch.add_workers:
            try:
                teams.add_worker(patch.team, agent)
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail=f"team {patch.team!r} not found",
                )
        for agent in patch.remove_workers:
            try:
                teams.remove_worker(patch.team, agent)
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail=f"team {patch.team!r} not found",
                )

        # Re-validate consistency
        try:
            validate_team_membership(paths, teams)
        except OrgConsistencyError as exc:
            # Rollback: restore original workers
            current = teams.manager_for_team(patch.team).workers
            added = set(current) - set(original_workers)
            removed = set(original_workers) - set(current)
            for agent in added:
                teams.remove_worker(patch.team, agent)
            for agent in removed:
                teams.add_worker(patch.team, agent)
            raise HTTPException(
                status_code=409,
                detail={"code": "teams_consistency_drift", "message": str(exc)},
            ) from exc

    # Return updated teams list (mirrors GET /teams shape)
    rows = []
    for tname in teams.teams():
        tm = teams.manager_for_team(tname)
        rows.append({
            "name": tname,
            "manager": tm.name,
            "workers": list(tm.workers),
        })
    return {"teams": rows}
