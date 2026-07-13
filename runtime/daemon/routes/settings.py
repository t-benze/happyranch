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
    write_org_setting_to_db,
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
            session_timeout_seconds=entry(s.session_timeout_seconds, True),
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
    # None = inherited (omitted in config); resolved to org.timezone ->
    # machine-local -> UTC at dream-scheduling time. The write-side view
    # (DreamingScheduleUpdate) already accepts None.
    timezone: str | None


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


class WorkHoursWindowView(BaseModel):
    """Read-only window leaf of a working-hours schedule layer. Any leaf may be
    ``None`` — it is inherited from a lower-precedence tier."""
    start: str | None
    end: str | None
    timezone: str | None


class WorkHoursLayerView(BaseModel):
    """Read-only RAW per-tier working-hours layer (default / a team / an agent
    override). ``None`` leaves are unset at this tier and inherit downward; the
    client derives per-leaf provenance + the effective schedule from these raw
    tiers (THR-035 §4.3 reconciliation view)."""
    mode: str | None
    window: WorkHoursWindowView
    interval: str | None
    days: list[str] | None
    catch_up_on_startup: bool | None


class WorkHoursAgentsView(BaseModel):
    """Read-only eligibility selector — a single org-level gate (not per-tier)."""
    mode: str
    include: list[str]
    exclude: list[str]


class WorkingHoursSettingsView(BaseModel):
    """Read-only RAW per-tier working-hours configuration.

    ``enabled`` is the single feature-level on/off switch (NOT a per-tier leaf).
    ``agents`` is the single org-level eligibility gate. ``default`` / ``teams``
    / ``overrides`` are the raw tiers the reconciliation UI merges client-side
    to show per-leaf provenance and the effective schedule.
    """
    enabled: bool
    agents: WorkHoursAgentsView
    default: WorkHoursLayerView
    teams: dict[str, WorkHoursLayerView]
    overrides: dict[str, WorkHoursLayerView]


def _layer_to_view(layer) -> WorkHoursLayerView:
    return WorkHoursLayerView(
        mode=layer.mode,
        window=WorkHoursWindowView(
            start=layer.window_start,
            end=layer.window_end,
            timezone=layer.timezone,
        ),
        interval=layer.interval,
        days=list(layer.days) if layer.days is not None else None,
        catch_up_on_startup=layer.catch_up_on_startup,
    )


class OrgSettingsView(BaseModel):
    """Read-only view of selected org-level settings.

    ALLOW-LIST: only session_timeout_seconds, dreaming, threads, and
    working_hours. feishu_notifications and any other OrgConfig field are
    excluded by construction — they have NO attribute on this model.
    """

    session_timeout_seconds: int | None
    dreaming: DreamingSettingsView
    threads: ThreadsSettingsView
    working_hours: WorkingHoursSettingsView


def _org_config_to_view_from_resolved(
    *,
    session_timeout_seconds: int | None,
    dreaming_cfg,
    threads_kwargs: dict,
    wh_cfg,
) -> OrgSettingsView:
    """Build OrgSettingsView from DB-resolved values (THR-095 single-store)."""
    return OrgSettingsView(
        session_timeout_seconds=session_timeout_seconds,
        dreaming=DreamingSettingsView(
            enabled=dreaming_cfg.enabled,
            schedule=DreamingScheduleView(
                time=dreaming_cfg.schedule_time,
                timezone=dreaming_cfg.timezone,
            ),
            catch_up_on_startup=dreaming_cfg.catch_up_on_startup,
            agents=DreamingAgentsView(
                mode=dreaming_cfg.agent_mode,
                include=list(dreaming_cfg.include_agents),
                exclude=list(dreaming_cfg.exclude_agents),
            ),
        ),
        threads=ThreadsSettingsView(
            enabled=threads_kwargs["enabled"],
            default_turn_cap=threads_kwargs["default_turn_cap"],
            invocation_timeout_seconds=threads_kwargs["invocation_timeout_seconds"],
        ),
        working_hours=WorkingHoursSettingsView(
            enabled=wh_cfg.enabled,
            agents=WorkHoursAgentsView(
                mode=wh_cfg.agent_mode,
                include=list(wh_cfg.include_agents),
                exclude=list(wh_cfg.exclude_agents),
            ),
            default=_layer_to_view(wh_cfg.default),
            teams={name: _layer_to_view(layer) for name, layer in wh_cfg.teams.items()},
            overrides={name: _layer_to_view(layer) for name, layer in wh_cfg.overrides.items()},
        ),
    )


def _org_config_to_view(cfg) -> OrgSettingsView:
    """Pure function: map OrgConfig → OrgSettingsView (allow-list).

    DEPRECATED for the 4 web-writable knobs — use _org_config_to_view_from_resolved
    instead (THR-095).  Kept for backward-compat reference."""
    wh = cfg.working_hours
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
        working_hours=WorkingHoursSettingsView(
            enabled=wh.enabled,
            agents=WorkHoursAgentsView(
                mode=wh.agent_mode,
                include=list(wh.include_agents),
                exclude=list(wh.exclude_agents),
            ),
            default=_layer_to_view(wh.default),
            teams={name: _layer_to_view(layer) for name, layer in wh.teams.items()},
            overrides={name: _layer_to_view(layer) for name, layer in wh.overrides.items()},
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
    """Return read-only system + org settings for the given org.

    THR-095: the 4 web-writable knobs are resolved from the DB with config.yaml
    as the fallback default."""
    from runtime.orchestrator.org_config import (
        load_org_config,
        resolve_org_setting_dreaming,
        resolve_org_setting_threads,
        resolve_org_setting_session_timeout,
        resolve_org_setting_working_hours,
    )
    cfg = load_org_config(OrgPaths(root=org.root))
    # Resolve the 4 writable knobs from DB.
    dreaming_cfg = resolve_org_setting_dreaming(org.db, code_default=cfg.dreaming)
    threads_kwargs = resolve_org_setting_threads(org.db, code_default=cfg)
    sto = resolve_org_setting_session_timeout(org.db, code_default=cfg.session_timeout_seconds)
    wh_cfg = resolve_org_setting_working_hours(org.db, code_default=cfg.working_hours)
    return SettingsResponse(
        system=SystemSettingsView.from_settings(global_settings),
        org=_org_config_to_view_from_resolved(
            session_timeout_seconds=sto,
            dreaming_cfg=dreaming_cfg,
            threads_kwargs=threads_kwargs,
            wh_cfg=wh_cfg,
        ),
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


class WorkHoursWindowPatch(BaseModel):
    """Optional window leaf override of a working-hours schedule layer."""
    model_config = ConfigDict(extra="forbid")

    start: str | None = None
    end: str | None = None
    timezone: str | None = None


class WorkHoursLayerPatch(BaseModel):
    """Optional RAW per-tier schedule-layer override. Authoritative validation
    (divides-24h, window completeness, start<end, interval≤window) runs
    server-side in ``_build_org_config`` at save — NOT here. ``extra='forbid'``
    only rejects unknown leaf names."""
    model_config = ConfigDict(extra="forbid")

    mode: str | None = None
    window: WorkHoursWindowPatch | None = None
    interval: str | None = None
    days: list[str] | None = None
    catch_up_on_startup: bool | None = None


class WorkHoursAgentsPatch(BaseModel):
    """Optional eligibility-selector override (single org-level gate)."""
    model_config = ConfigDict(extra="forbid")

    mode: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None

    @field_validator("mode")
    @classmethod
    def _mode_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in {"all", "whitelist"}:
            raise ValueError(
                f"working_hours.agents.mode must be 'all' or 'whitelist', got {v!r}"
            )
        return v


class WorkingHoursPatch(BaseModel):
    """Optional working-hours configuration override.

    ``enabled`` is the single feature-level switch (NOT a per-tier leaf).
    ``agents`` is the single org-level eligibility gate. ``teams`` / ``overrides``
    are keyed by team / agent name; the deep-merge preserves sibling entries.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    agents: WorkHoursAgentsPatch | None = None
    default: WorkHoursLayerPatch | None = None
    teams: dict[str, WorkHoursLayerPatch] | None = None
    overrides: dict[str, WorkHoursLayerPatch] | None = None


class OrgSettingsPatch(BaseModel):
    """Partial update of non-sensitive OrgConfig fields.

    EVERY field is optional — absent keys leave the current on-disk value
    untouched. ``extra='forbid'`` rejects any unknown or sensitive key
    (feishu_notifications, permission_mode, etc.) with a 422 so the GUI client
    gets immediate feedback rather than a silent no-op.
    """
    model_config = ConfigDict(extra="forbid")

    session_timeout_seconds: int | None = None
    dreaming: DreamingPatch | None = None
    threads: ThreadsPatch | None = None
    working_hours: WorkingHoursPatch | None = None

    @field_validator("session_timeout_seconds")
    @classmethod
    def _timeout_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("session_timeout_seconds must be a positive integer")
        return v


def _patch_to_raw_dict(patch: OrgSettingsPatch) -> dict:
    """Convert the Pydantic patch model to a raw dict suitable for
    ``save_org_config`` deep-merge.

    Uses Pydantic v2 ``model_fields_set`` (and its nested-model equivalents)
    to distinguish an EXPLICIT ``null`` sent by the client (which should
    clear the override) from an OMITTED field (which leaves the current
    value untouched).
    """
    result: dict = {}
    fields = patch.model_fields_set or set()

    # session_timeout_seconds: include if in fields_set (even if None)
    if "session_timeout_seconds" in fields:
        result["session_timeout_seconds"] = patch.session_timeout_seconds
    elif patch.session_timeout_seconds is not None:
        result["session_timeout_seconds"] = patch.session_timeout_seconds

    if patch.dreaming is not None:
        dreaming: dict = {}
        d = patch.dreaming
        d_fields = d.model_fields_set or set()
        if d.enabled is not None or "enabled" in d_fields:
            dreaming["enabled"] = d.enabled
        # schedule: build from schedule patch + catch_up_on_startup
        # (catch_up_on_startup lives inside schedule in YAML, but is a peer
        # of schedule in the view/PATCH model for cleaner UI grouping.)
        sched: dict = {}
        if d.schedule is not None:
            s_fields = d.schedule.model_fields_set or set()
            if d.schedule.time is not None or "time" in s_fields:
                sched["time"] = d.schedule.time
            if d.schedule.timezone is not None or "timezone" in s_fields:
                sched["timezone"] = d.schedule.timezone
        if d.catch_up_on_startup is not None or "catch_up_on_startup" in d_fields:
            sched["catch_up_on_startup"] = d.catch_up_on_startup
        if sched:
            dreaming["schedule"] = sched
        if d.agents is not None:
            agents: dict = {}
            a_fields = d.agents.model_fields_set or set()
            if d.agents.mode is not None or "mode" in a_fields:
                agents["mode"] = d.agents.mode
            if d.agents.include is not None or "include" in a_fields:
                agents["include"] = d.agents.include
            if d.agents.exclude is not None or "exclude" in a_fields:
                agents["exclude"] = d.agents.exclude
            if agents:
                dreaming["agents"] = agents
        if dreaming:
            result["dreaming"] = dreaming

    if patch.threads is not None:
        threads: dict = {}
        t = patch.threads
        t_fields = t.model_fields_set or set()
        if t.enabled is not None or "enabled" in t_fields:
            threads["enabled"] = t.enabled
        if t.default_turn_cap is not None or "default_turn_cap" in t_fields:
            threads["default_turn_cap"] = t.default_turn_cap
        if t.invocation_timeout_seconds is not None or "invocation_timeout_seconds" in t_fields:
            threads["invocation_timeout_seconds"] = t.invocation_timeout_seconds
        if threads:
            result["threads"] = threads

    if patch.working_hours is not None:
        result["working_hours"] = _working_hours_patch_to_raw(patch.working_hours)

    return result


def _layer_patch_to_raw(layer: WorkHoursLayerPatch) -> dict:
    """Convert one schedule-layer patch to its raw YAML dict, mirroring the
    on-disk shape ``_parse_schedule_layer`` expects (window nested, timezone
    under window). Uses ``model_fields_set`` so an explicit ``null`` clears a
    leaf (reset-to-inherited) while an omitted leaf is left untouched."""
    raw: dict = {}
    lf = layer.model_fields_set or set()
    if "mode" in lf:
        raw["mode"] = layer.mode
    if layer.window is not None:
        window: dict = {}
        wf = layer.window.model_fields_set or set()
        if "start" in wf:
            window["start"] = layer.window.start
        if "end" in wf:
            window["end"] = layer.window.end
        if "timezone" in wf:
            window["timezone"] = layer.window.timezone
        if window:
            raw["window"] = window
    if "interval" in lf:
        raw["interval"] = layer.interval
    if "days" in lf:
        raw["days"] = layer.days
    if "catch_up_on_startup" in lf:
        raw["catch_up_on_startup"] = layer.catch_up_on_startup
    return raw


def _working_hours_patch_to_raw(w: WorkingHoursPatch) -> dict:
    """Convert the working_hours patch to a raw dict for ``save_org_config``
    deep-merge. teams/overrides are keyed dicts — sending one key deep-merges
    into the existing tier without dropping siblings."""
    wh: dict = {}
    wf = w.model_fields_set or set()
    if "enabled" in wf:
        wh["enabled"] = w.enabled
    if w.agents is not None:
        agents: dict = {}
        af = w.agents.model_fields_set or set()
        if "mode" in af:
            agents["mode"] = w.agents.mode
        if "include" in af:
            agents["include"] = w.agents.include
        if "exclude" in af:
            agents["exclude"] = w.agents.exclude
        if agents:
            wh["agents"] = agents
    if w.default is not None:
        wh["default"] = _layer_patch_to_raw(w.default)
    if w.teams is not None:
        wh["teams"] = {name: _layer_patch_to_raw(layer) for name, layer in w.teams.items()}
    if w.overrides is not None:
        wh["overrides"] = {
            name: _layer_patch_to_raw(layer) for name, layer in w.overrides.items()
        }
    return wh


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


def _validate_working_hours_names(
    wh_block: dict, known_agents: set[str], known_teams: set[str],
) -> list[str]:
    """Pre-flight name validation for a working_hours patch against the live
    roster (mirrors ``_validate_agent_names``). Agent names appear in
    ``agents.include`` / ``agents.exclude`` and as ``overrides.<agent>`` keys;
    team names appear as ``teams.<team>`` keys. Unknown names → 422 before any
    write, so a stale reference can never reach disk."""
    errors: list[str] = []
    agents_block = wh_block.get("agents")
    if isinstance(agents_block, dict):
        for label in ("include", "exclude"):
            names = agents_block.get(label)
            if names:
                unknown = sorted(set(names) - known_agents)
                if unknown:
                    errors.append(
                        f"working_hours.agents.{label} references unknown agent(s): "
                        + ", ".join(unknown)
                    )
    teams_block = wh_block.get("teams")
    if isinstance(teams_block, dict):
        unknown_teams = sorted(set(teams_block) - known_teams)
        if unknown_teams:
            errors.append(
                "working_hours.teams references unknown team(s): "
                + ", ".join(unknown_teams)
            )
    overrides_block = wh_block.get("overrides")
    if isinstance(overrides_block, dict):
        unknown = sorted(set(overrides_block) - known_agents)
        if unknown:
            errors.append(
                "working_hours.overrides references unknown agent(s): "
                + ", ".join(unknown)
            )
    return errors


def _read_raw_working_hours(paths: OrgPaths) -> dict:
    """Read the raw ``working_hours`` block from org/config.yaml (``{}`` if the
    file or key is absent). Used to snapshot before→after for the audit row."""
    import yaml
    path = paths.org_config_path
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("working_hours", {})
    return block if isinstance(block, dict) else {}


@router.put("/settings/org", response_model=SettingsResponse)
def put_org_settings(slug: str, org: OrgDep, patch: OrgSettingsPatch) -> SettingsResponse:
    """Partial-update editable org settings.

    Only allow-listed keys (dreaming, threads, session_timeout_seconds,
    working_hours) are written to the org_settings DB table (THR-095
    single-store). Each section write is transactional with its
    ``config:<section>`` audit row.

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

    # Pre-flight: validate working_hours agent/team names against the live
    # roster so a stale reference is rejected (422) before reaching disk.
    if "working_hours" in patch_raw:
        known_agents = _resolve_agent_names(paths)
        known_teams = set(org.teams.teams()) if getattr(org, "teams", None) else set()
        wh_errors = _validate_working_hours_names(
            patch_raw["working_hours"], known_agents, known_teams,
        )
        if wh_errors:
            raise HTTPException(status_code=422, detail={"errors": wh_errors})

    # THR-095: write to DB (transactional per section: upsert + audit row).
    try:
        write_org_setting_to_db(paths, org.db, patch_raw)
    except OrgConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Return updated snapshot from DB-resolved values.
    return get_settings(slug, org)


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


def _preflight_team_workers(
    paths: OrgPaths,
    teams: object,
    team_name: str,
    add_workers: list[str],
    remove_workers: list[str],
) -> list[str]:
    """Pre-flight validate worker membership targets before mutation.

    Checks:
    1. Every target in add_workers and remove_workers is a known active agent.
    2. No target in add_workers is the team's manager.

    Returns a list of human-readable error messages (empty → valid).
    """
    agent_names: set[str] = set()
    agent_roles: dict[str, str] = {}
    for agent_def in prompt_loader.list_agents(paths):
        agent_names.add(agent_def.name)
        agent_roles[agent_def.name] = agent_def.role

    errors: list[str] = []

    for label, targets in (("add_workers", add_workers), ("remove_workers", remove_workers)):
        for agent in targets:
            if agent not in agent_names:
                errors.append(f"{label}: unknown agent {agent!r}")
            elif label == "add_workers" and agent_roles.get(agent) == "manager":
                # Reject ANY agent whose role is 'manager' outright,
                # independent of which team they manage. Managers manage
                # teams; they are never workers.
                errors.append(
                    f"add_workers: {agent!r} is a manager and cannot be added as a worker"
                )

    return errors


def _post_flight_worker_agent_drift(
    paths: OrgPaths,
    teams: object,
) -> list[str]:
    """Bidirectional consistency check between teams.yaml worker rows
    and agent file team declarations.

    (a) Every worker in teams.yaml has a matching agent file that declares
        the same team.
    (b) Every non-manager agent that declares a team is present in that
        team's worker list.

    This supplements ``validate_team_membership`` (which only checks agent
    files → team names / manager matches). We do NOT modify
    ``validate_team_membership`` — this is additive.
    """
    from runtime.orchestrator.teams import TeamsRegistry

    if not isinstance(teams, TeamsRegistry):
        return []

    # Build mappings: agent_name → declared_team, agent_name → role
    agent_team: dict[str, str] = {}
    agent_role: dict[str, str] = {}
    for agent_def in prompt_loader.list_agents(paths):
        agent_team[agent_def.name] = agent_def.team
        agent_role[agent_def.name] = agent_def.role

    drift: list[str] = []

    # Build worker sets per team
    team_workers: dict[str, set[str]] = {}
    for team_name in teams.teams():
        tm = teams.manager_for_team(team_name)
        team_workers[team_name] = set(tm.workers)

    # (a) worker → agent file
    for team_name, workers in team_workers.items():
        for worker in workers:
            declared_team = agent_team.get(worker)
            if declared_team is None:
                drift.append(
                    f"worker {worker!r} in team {team_name!r} has no agent file"
                )
            elif declared_team != team_name:
                drift.append(
                    f"worker {worker!r} in team {team_name!r} declares team "
                    f"{declared_team!r} in their agent file"
                )

    # (b) non-manager agent declaring a team → must be in that team's worker list
    for agent_name, declared_team in agent_team.items():
        role = agent_role.get(agent_name, "worker")
        if role == "manager":
            continue  # managers are validated by validate_team_membership
        workers = team_workers.get(declared_team, set())
        if agent_name not in workers:
            drift.append(
                f"agent {agent_name!r} declares team {declared_team!r} "
                f"but is not in that team's worker list"
            )

    return drift


@router.put("/settings/teams")
async def put_teams(slug: str, org: OrgDep, patch: TeamsPatch) -> dict:
    """Update worker membership for a single team.

    Pre-flight validates every add/remove target against the active agent
    list and rejects unknown agents or managers added as workers before
    touching teams.yaml.

    Then wraps ``TeamsRegistry.add_worker`` / ``remove_worker`` (which
    auto-persist to teams.yaml), re-runs ``validate_team_membership``
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

    # Pre-flight: reject unknown agents / manager-as-worker before mutation
    preflight_errors = _preflight_team_workers(
        paths, teams, patch.team, patch.add_workers, patch.remove_workers,
    )
    if preflight_errors:
        raise HTTPException(status_code=422, detail={"errors": preflight_errors})

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

        # Supplementary check: worker rows vs agent file declarations
        worker_drift = _post_flight_worker_agent_drift(paths, teams)
        if worker_drift:
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
                detail={"code": "teams_worker_agent_drift", "message": "; ".join(worker_drift)},
            )

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
