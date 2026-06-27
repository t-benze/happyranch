"""Org-level configuration loaded from <runtime>/org/config.yaml.

A small, additive layer between the global Settings defaults and per-agent
overrides. The file is optional — a runtime without it inherits the global
defaults exactly as before.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from runtime.orchestrator._paths import OrgPaths


class OrgConfigError(ValueError):
    """Raised when org/config.yaml is malformed or fails validation."""


@dataclass(frozen=True)
class DreamingConfig:
    enabled: bool = False
    schedule_time: str = "02:00"
    # None means "inherit" — resolved (dreaming.timezone -> org.timezone ->
    # machine-local -> UTC) via ``resolve_dreaming_timezone``. A None here must
    # never reach ``ZoneInfo`` directly.
    timezone: str | None = None
    catch_up_on_startup: bool = True
    agent_mode: str = "all"
    include_agents: list[str] = field(default_factory=list)
    exclude_agents: list[str] = field(default_factory=list)


_WORK_HOURS_MODES = ("windowed", "continuous")
_WORK_HOURS_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_INTERVAL_RE = re.compile(r"^(\d+)([hm])$")
_HHMM_RE = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")
_DAY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class WorkHoursScheduleLayer:
    """A partial working-hours schedule. Any leaf left ``None`` inherits from a
    lower precedence tier during ``WorkingHoursConfig.resolve_for``."""
    mode: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    timezone: str | None = None
    interval: str | None = None
    days: tuple[str, ...] | None = None
    catch_up_on_startup: bool | None = None


@dataclass(frozen=True)
class WorkHoursSchedule:
    """A fully resolved effective schedule for one agent (all required leaves
    present for its ``mode``)."""
    mode: str
    interval: str
    timezone: str
    catch_up_on_startup: bool
    window_start: str | None = None
    window_end: str | None = None
    days: tuple[str, ...] | None = None


@dataclass(frozen=True)
class WorkingHoursConfig:
    enabled: bool = False
    default: WorkHoursScheduleLayer = field(default_factory=WorkHoursScheduleLayer)
    teams: dict[str, WorkHoursScheduleLayer] = field(default_factory=dict)
    overrides: dict[str, WorkHoursScheduleLayer] = field(default_factory=dict)
    agent_mode: str = "all"
    include_agents: list[str] = field(default_factory=list)
    exclude_agents: list[str] = field(default_factory=list)

    def resolve_for(self, agent_name: str, team: str | None) -> WorkHoursSchedule:
        """Overlay the three tiers (default -> teams.<team> -> overrides.<agent>)
        leaf-by-leaf and validate the resolved effective schedule. Raises
        OrgConfigError if the merged schedule is incomplete or incoherent for
        its mode."""
        leaves = (
            "mode", "window_start", "window_end", "timezone",
            "interval", "days", "catch_up_on_startup",
        )
        merged: dict[str, object | None] = {leaf: None for leaf in leaves}
        layers = [self.default]
        if team is not None and team in self.teams:
            layers.append(self.teams[team])
        if agent_name in self.overrides:
            layers.append(self.overrides[agent_name])
        for layer in layers:
            for leaf in leaves:
                value = getattr(layer, leaf)
                if value is not None:
                    merged[leaf] = value

        where = f"working_hours (resolved for agent {agent_name!r})"
        mode = merged["mode"]
        if mode is None:
            raise OrgConfigError(f"{where}: mode is required (after resolution)")
        timezone = merged["timezone"]
        if timezone is None:
            raise OrgConfigError(f"{where}: a timezone is required (after resolution)")
        interval = merged["interval"]
        if interval is None:
            raise OrgConfigError(f"{where}: interval is required (after resolution)")
        catch_up = merged["catch_up_on_startup"]
        catch_up = True if catch_up is None else bool(catch_up)

        if mode == "continuous":
            # window and days are ignored in continuous mode.
            _check_interval_divides_day(interval, where)
            return WorkHoursSchedule(
                mode="continuous", interval=interval, timezone=timezone,
                catch_up_on_startup=catch_up,
            )

        # windowed: window.{start,end} + days required after resolution.
        window_start = merged["window_start"]
        window_end = merged["window_end"]
        days = merged["days"]
        if window_start is None or window_end is None:
            raise OrgConfigError(
                f"{where}: windowed mode requires window.start and window.end "
                f"(after resolution)"
            )
        if days is None:
            raise OrgConfigError(f"{where}: windowed mode requires days (after resolution)")
        _check_window_and_interval(window_start, window_end, interval, where)
        return WorkHoursSchedule(
            mode="windowed", interval=interval, timezone=timezone,
            catch_up_on_startup=catch_up, window_start=window_start,
            window_end=window_end, days=tuple(days),
        )


@dataclass(frozen=True)
class OrgConfig:
    session_timeout_seconds: int | None = None
    # Org-wide local timezone. None (the default) means "inherit machine-local"
    # — resolved via ``resolve_org_timezone``. An explicit value must be a valid
    # IANA name (validated at load).
    timezone: str | None = None
    dreaming: DreamingConfig = field(default_factory=DreamingConfig)
    working_hours: WorkingHoursConfig = field(default_factory=WorkingHoursConfig)
    threads_enabled: bool = True
    threads_default_turn_cap: int = 500
    threads_invocation_timeout_seconds: int | None = None

    @classmethod
    def load_from_text(cls, text: str, path: str = "<text>") -> "OrgConfig":
        """Parse YAML text directly into OrgConfig. Used in tests and CLI helpers."""
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise OrgConfigError(f"malformed YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise OrgConfigError(f"{path}: top-level must be a mapping")
        return _build_org_config(data, path)


def _validate_agent_list(
    value: object, name: str, path: str, *, prefix: str = "dreaming.agents",
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OrgConfigError(f"{path}: {prefix}.{name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise OrgConfigError(f"{path}: {prefix}.{name} entries must be strings")
    return list(value)


def _validate_positive_int(
    value: object, name: str, *, min_v: int, max_v: int, path: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise OrgConfigError(f"{path}: {name} must be an integer, got {value!r}")
    if value < min_v or value > max_v:
        raise OrgConfigError(
            f"{path}: {name} must be in [{min_v}, {max_v}], got {value}"
        )
    return value


def _parse_dreaming(block: dict, path: str) -> DreamingConfig:
    if not isinstance(block, dict):
        raise OrgConfigError(f"{path}: dreaming must be a mapping")

    enabled = block.get("enabled", False)
    if not isinstance(enabled, bool):
        raise OrgConfigError(f"{path}: dreaming.enabled must be a boolean")

    schedule = block.get("schedule", {})
    if schedule is None:
        schedule = {}
    if not isinstance(schedule, dict):
        raise OrgConfigError(f"{path}: dreaming.schedule must be a mapping")
    schedule_time = schedule.get("time", "02:00")
    if not isinstance(schedule_time, str) or not re.match(r"^[0-2][0-9]:[0-5][0-9]$", schedule_time):
        raise OrgConfigError(f"{path}: dreaming.schedule.time must be HH:MM")
    hour = int(schedule_time[:2])
    if hour > 23:
        raise OrgConfigError(f"{path}: dreaming.schedule.time must be HH:MM")
    # Omitted -> None (inherit org.timezone -> machine-local -> UTC at resolve
    # time). An explicit value is validated as a real IANA name.
    timezone = schedule.get("timezone")
    if timezone is not None:
        if not isinstance(timezone, str):
            raise OrgConfigError(f"{path}: dreaming.schedule.timezone must be a string")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise OrgConfigError(f"{path}: unknown dreaming.schedule.timezone {timezone!r}") from exc
    catch_up = schedule.get("catch_up_on_startup", True)
    if not isinstance(catch_up, bool):
        raise OrgConfigError(f"{path}: dreaming.schedule.catch_up_on_startup must be a boolean")

    agents = block.get("agents", {})
    if agents is None:
        agents = {}
    if not isinstance(agents, dict):
        raise OrgConfigError(f"{path}: dreaming.agents must be a mapping")
    mode = agents.get("mode", "all")
    if mode not in {"all", "whitelist"}:
        raise OrgConfigError(f"{path}: dreaming.agents.mode must be one of ['all', 'whitelist']")

    return DreamingConfig(
        enabled=enabled,
        schedule_time=schedule_time,
        timezone=timezone,
        catch_up_on_startup=catch_up,
        agent_mode=mode,
        include_agents=_validate_agent_list(agents.get("include"), "include", path),
        exclude_agents=_validate_agent_list(agents.get("exclude"), "exclude", path),
    )


def _interval_to_seconds(value: str) -> int:
    """Convert an already-format-validated ``Nh``/``Nm`` interval to seconds."""
    m = _INTERVAL_RE.match(value)
    assert m is not None  # callers validate format first
    return int(m.group(1)) * (3600 if m.group(2) == "h" else 60)


def _hhmm_to_seconds(value: str) -> int:
    return int(value[:2]) * 3600 + int(value[3:]) * 60


def _validate_timezone(value: object, label: str, path: str) -> str:
    if not isinstance(value, str):
        raise OrgConfigError(f"{path}: {label} must be a string")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise OrgConfigError(f"{path}: unknown {label} {value!r}") from exc
    return value


def _machine_local_iana() -> str | None:
    """Best-effort IANA zone name for the host, stdlib-only (POSIX).

    Reads the ``/etc/localtime`` symlink and parses its ``zoneinfo/`` suffix —
    how darwin and linux expose the configured zone. Returns None when the link
    is absent/unreadable, unparseable, or names a zone ``ZoneInfo`` can't load.
    """
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    idx = link.rfind(marker)
    if idx == -1:
        return None
    name = link[idx + len(marker):].strip("/")
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def _resolve_timezone(value: str | None) -> tuple[tzinfo, str]:
    """Resolve a timezone string-or-None to an effective ``(tzinfo, display)``.

    Precedence:
      1. explicit IANA name -> ``ZoneInfo(value)``; an invalid value falls
         through gracefully (never crashes);
      2. None -> machine-local: the IANA name from ``/etc/localtime`` when
         derivable, else a fixed offset from ``datetime.now().astimezone()``
         displayed as ``UTC±HH:MM``;
      3. ultimate fallback -> UTC.
    """
    if value is not None:
        try:
            return ZoneInfo(value), value
        except (ZoneInfoNotFoundError, ValueError):
            pass  # graceful fall-through to machine-local
    iana = _machine_local_iana()
    if iana is not None:
        return ZoneInfo(iana), iana
    try:
        local = datetime.now().astimezone()
        offset = local.utcoffset()
        if offset is not None:
            fixed = timezone(offset)
            # timezone.tzname(None) renders "UTC", "UTC+08:00", etc.
            return fixed, fixed.tzname(None)
    except (OSError, ValueError):
        pass
    return timezone.utc, "UTC"


def resolve_timezone_or_local(value: str | None) -> tzinfo:
    """Resolve an explicit-or-None IANA timezone string to an effective tzinfo
    (machine-local then UTC fallback). For call sites that only hold a bare
    timezone string rather than a full ``OrgConfig``."""
    return _resolve_timezone(value)[0]


def resolve_org_timezone(org_config: OrgConfig) -> tzinfo:
    """Effective org timezone as a tzinfo. See ``_resolve_timezone``."""
    return _resolve_timezone(org_config.timezone)[0]


def resolve_org_timezone_display(org_config: OrgConfig) -> tuple[tzinfo, str]:
    """Effective org timezone plus its display name (e.g. ``Asia/Shanghai`` or
    ``UTC+08:00``)."""
    return _resolve_timezone(org_config.timezone)


def resolve_dreaming_timezone(org_config: OrgConfig) -> tzinfo:
    """Effective dreaming timezone as a tzinfo. Precedence: ``dreaming.timezone``
    (explicit) -> ``org.timezone`` -> machine-local -> UTC."""
    effective = org_config.dreaming.timezone
    if effective is None:
        effective = org_config.timezone
    return _resolve_timezone(effective)[0]


def _validate_window_time(value: object, label: str, path: str) -> str:
    if not isinstance(value, str) or not _HHMM_RE.match(value) or int(value[:2]) > 23:
        raise OrgConfigError(f"{path}: {label} must be HH:MM (hour 00-23)")
    return value


def _validate_interval_format(value: object, label: str, path: str) -> str:
    if not isinstance(value, str):
        raise OrgConfigError(f"{path}: {label} must be a string like '2h' or '30m'")
    m = _INTERVAL_RE.match(value)
    if not m or int(m.group(1)) <= 0:
        raise OrgConfigError(f"{path}: {label} must be Nh or Nm and positive, got {value!r}")
    return value


def _validate_days_list(value: object, label: str, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(d, str) for d in value):
        raise OrgConfigError(f"{path}: {label} must be a list of day names")
    bad = [d for d in value if d not in _WORK_HOURS_DAYS]
    if bad:
        raise OrgConfigError(
            f"{path}: {label} has invalid days {bad}; allowed: {list(_WORK_HOURS_DAYS)}"
        )
    return tuple(value)


def _check_interval_divides_day(interval: str, where: str) -> None:
    if _DAY_SECONDS % _interval_to_seconds(interval) != 0:
        raise OrgConfigError(
            f"{where}: continuous interval {interval!r} must evenly divide 24h"
        )


def _check_window_and_interval(start: str, end: str, interval: str, where: str) -> None:
    if start >= end:
        raise OrgConfigError(f"{where}: window.start must be before window.end")
    window_seconds = _hhmm_to_seconds(end) - _hhmm_to_seconds(start)
    if _interval_to_seconds(interval) > window_seconds:
        raise OrgConfigError(
            f"{where}: interval {interval!r} is longer than the window length"
        )


def _validate_layer_coherence(layer: WorkHoursScheduleLayer, label: str, path: str) -> None:
    """Single-layer cross-leaf checks at load time. Cross-tier effective-schedule
    validation additionally runs in ``WorkingHoursConfig.resolve_for``."""
    where = f"{path}: {label}"
    if layer.window_start is not None and layer.window_end is not None:
        if layer.window_start >= layer.window_end:
            raise OrgConfigError(f"{where}.window.start must be before window.end")
    if layer.mode == "continuous" and layer.interval is not None:
        _check_interval_divides_day(layer.interval, where)
    if (
        layer.mode == "windowed"
        and layer.interval is not None
        and layer.window_start is not None
        and layer.window_end is not None
    ):
        _check_window_and_interval(layer.window_start, layer.window_end, layer.interval, where)


def _parse_schedule_layer(block: object, path: str, label: str) -> WorkHoursScheduleLayer:
    if block is None:
        block = {}
    if not isinstance(block, dict):
        raise OrgConfigError(f"{path}: {label} must be a mapping")

    mode = block.get("mode")
    if mode is not None and mode not in _WORK_HOURS_MODES:
        raise OrgConfigError(
            f"{path}: {label}.mode must be one of {list(_WORK_HOURS_MODES)}, got {mode!r}"
        )

    window = block.get("window")
    window_start = window_end = window_tz = None
    if window is not None:
        if not isinstance(window, dict):
            raise OrgConfigError(f"{path}: {label}.window must be a mapping")
        if "start" in window:
            window_start = _validate_window_time(window["start"], f"{label}.window.start", path)
        if "end" in window:
            window_end = _validate_window_time(window["end"], f"{label}.window.end", path)
        if "timezone" in window:
            window_tz = _validate_timezone(window["timezone"], f"{label}.window.timezone", path)

    # window.timezone wins; a bare ``timezone`` leaf is the continuous-mode form.
    timezone = window_tz
    if timezone is None and "timezone" in block:
        timezone = _validate_timezone(block["timezone"], f"{label}.timezone", path)

    interval = block.get("interval")
    if interval is not None:
        interval = _validate_interval_format(interval, f"{label}.interval", path)

    days = block.get("days")
    if days is not None:
        days = _validate_days_list(days, f"{label}.days", path)

    catch_up = block.get("catch_up_on_startup")
    if catch_up is not None and not isinstance(catch_up, bool):
        raise OrgConfigError(f"{path}: {label}.catch_up_on_startup must be a boolean")

    layer = WorkHoursScheduleLayer(
        mode=mode,
        window_start=window_start,
        window_end=window_end,
        timezone=timezone,
        interval=interval,
        days=days,
        catch_up_on_startup=catch_up,
    )
    _validate_layer_coherence(layer, label, path)
    return layer


def _parse_working_hours(block: object, path: str) -> WorkingHoursConfig:
    if not isinstance(block, dict):
        raise OrgConfigError(f"{path}: working_hours must be a mapping")

    enabled = block.get("enabled", False)
    if not isinstance(enabled, bool):
        raise OrgConfigError(f"{path}: working_hours.enabled must be a boolean")

    default = _parse_schedule_layer(block.get("default"), path, "working_hours.default")

    agents = block.get("agents", {})
    if agents is None:
        agents = {}
    if not isinstance(agents, dict):
        raise OrgConfigError(f"{path}: working_hours.agents must be a mapping")
    mode = agents.get("mode", "all")
    if mode not in {"all", "whitelist"}:
        raise OrgConfigError(
            f"{path}: working_hours.agents.mode must be one of ['all', 'whitelist']"
        )

    teams_block = block.get("teams", {})
    if teams_block is None:
        teams_block = {}
    if not isinstance(teams_block, dict):
        raise OrgConfigError(f"{path}: working_hours.teams must be a mapping")
    teams = {
        name: _parse_schedule_layer(value, path, f"working_hours.teams.{name}")
        for name, value in teams_block.items()
    }

    overrides_block = block.get("overrides", {})
    if overrides_block is None:
        overrides_block = {}
    if not isinstance(overrides_block, dict):
        raise OrgConfigError(f"{path}: working_hours.overrides must be a mapping")
    overrides = {
        name: _parse_schedule_layer(value, path, f"working_hours.overrides.{name}")
        for name, value in overrides_block.items()
    }

    return WorkingHoursConfig(
        enabled=enabled,
        default=default,
        teams=teams,
        overrides=overrides,
        agent_mode=mode,
        include_agents=_validate_agent_list(
            agents.get("include"), "include", path, prefix="working_hours.agents"
        ),
        exclude_agents=_validate_agent_list(
            agents.get("exclude"), "exclude", path, prefix="working_hours.agents"
        ),
    )


def _parse_threads(block: dict, path: str) -> dict:
    """Parse the threads: block and return kwargs for OrgConfig."""
    if not isinstance(block, dict):
        raise OrgConfigError(f"{path}: threads must be a mapping")

    kwargs: dict = {}

    if "enabled" in block:
        enabled = block["enabled"]
        if not isinstance(enabled, bool):
            raise OrgConfigError(f"{path}: threads.enabled must be a boolean, got {enabled!r}")
        kwargs["threads_enabled"] = enabled

    if "default_turn_cap" in block:
        cap = block["default_turn_cap"]
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise OrgConfigError(
                f"{path}: threads.default_turn_cap must be a positive int, got {cap!r}"
            )
        kwargs["threads_default_turn_cap"] = cap

    if "invocation_timeout_seconds" in block:
        t = block["invocation_timeout_seconds"]
        if t is not None and (not isinstance(t, int) or isinstance(t, bool) or t <= 0):
            raise OrgConfigError(
                f"{path}: threads.invocation_timeout_seconds must be a positive int or null, "
                f"got {t!r}"
            )
        kwargs["threads_invocation_timeout_seconds"] = t

    return kwargs


def _build_org_config(data: dict, path: str) -> OrgConfig:
    """Build OrgConfig from a parsed YAML dict."""
    timeout = data.get("session_timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise OrgConfigError(
                f"{path}: session_timeout_seconds must be a positive integer, "
                f"got {timeout!r}"
            )

    # Top-level org timezone. None (default) -> machine-local at resolve time.
    org_timezone = data.get("timezone")
    if org_timezone is not None:
        org_timezone = _validate_timezone(org_timezone, "timezone", path)

    # feishu_notifications is tolerated but ignored — Feishu was removed
    # (TASK-302/THR-022). Legacy configs with this key load without error.
    _feishu_block = data.get("feishu_notifications")

    dreaming_block = data.get("dreaming")
    dreaming_cfg = DreamingConfig()
    if dreaming_block is not None:
        dreaming_cfg = _parse_dreaming(dreaming_block, path)

    working_hours_block = data.get("working_hours")
    working_hours_cfg = WorkingHoursConfig()
    if working_hours_block is not None:
        working_hours_cfg = _parse_working_hours(working_hours_block, path)

    threads_block = data.get("threads")
    threads_kwargs: dict = {}
    if threads_block is not None:
        threads_kwargs = _parse_threads(threads_block, path)

    return OrgConfig(
        session_timeout_seconds=timeout,
        timezone=org_timezone,
        dreaming=dreaming_cfg,
        working_hours=working_hours_cfg,
        **threads_kwargs,
    )


def load_org_config(paths: OrgPaths) -> OrgConfig:
    """Load <runtime>/org/config.yaml. Missing file -> empty OrgConfig."""
    path = paths.org_config_path
    if not path.exists():
        return OrgConfig()

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise OrgConfigError(f"malformed YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OrgConfigError(f"{path}: top-level must be a mapping")

    return _build_org_config(data, str(path))


# ------------------------------------------------------------------
# ALLOW-LIST keys that the Settings GUI can mutate via PUT /settings/org.
# Every other top-level key in org/config.yaml is carried through verbatim
# (feishu_notifications, working_hours, unknown future keys, etc.).
# ------------------------------------------------------------------

_ORG_WRITABLE_KEYS = {"dreaming", "threads", "session_timeout_seconds"}


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into *base*.

    Dictionaries are merged recursively — sibling keys in *base* survive
    unless explicitly overridden. All other types (scalars, lists, None)
    are replaced outright by the override value. A ``None`` override clears
    the key so nullable fields (e.g. ``session_timeout_seconds``) can revert
    to default.
    """
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def save_org_config(paths: OrgPaths, patch: dict) -> None:
    """Atomically deep-merge *patch* into org/config.yaml for allow-listed keys.

    Algorithm:
    1. Read the current raw dict from disk. If the file doesn't exist, start
       with an empty dict (``load_org_config`` treats missing as defaults).
    2. Deep-merge **only** the allow-listed keys (``dreaming``, ``threads``,
       ``session_timeout_seconds``) from *patch* into the raw dict. Nested
       dictionaries within those blocks are merged recursively so a partial
       patch (e.g. ``{"dreaming": {"enabled": true}}``) does not drop sibling
       leaves. Every other top-level key is carried through verbatim.
    3. Validate the candidate dict via ``_build_org_config`` (the existing
       authoritative validator). If it raises ``OrgConfigError``, the write
       is aborted and the error is surfaced to the caller.
    4. Atomic write: ``yaml.safe_dump`` to a temp file in the same directory,
       then ``os.replace`` (atomic rename on POSIX).

    This function is purely additive — it calls ``_build_org_config`` and
    ``load_org_config`` read-only and never edits their bodies or signatures.
    """
    config_path = paths.org_config_path

    # 1. Read current raw dict
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise OrgConfigError(f"malformed YAML in {config_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise OrgConfigError(f"{config_path}: top-level must be a mapping")
    else:
        raw = {}

    # 2. Deep-merge only allow-listed keys
    raw = dict(raw)  # shallow copy to avoid mutating the parsed object
    for key in _ORG_WRITABLE_KEYS:
        if key in patch:
            if isinstance(patch[key], dict) and isinstance(raw.get(key), dict):
                raw[key] = _deep_merge(raw[key], patch[key])
            else:
                raw[key] = patch[key]

    # 3. Validate candidate via the authoritative validator
    try:
        _build_org_config(raw, str(config_path))
    except OrgConfigError:
        raise  # re-raise so the route can return 422

    # 4. Atomic write
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".org-config.", suffix=".yaml", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(raw, fh, sort_keys=False)
        os.replace(tmp, config_path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


