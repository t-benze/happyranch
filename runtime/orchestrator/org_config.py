"""Org-level configuration loaded from <runtime>/org/config.yaml.

A small, additive layer between the global Settings defaults and per-agent
overrides. The file is optional — a runtime without it inherits the global
defaults exactly as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from runtime.orchestrator._paths import OrgPaths


class OrgConfigError(ValueError):
    """Raised when org/config.yaml is malformed or fails validation."""


# region → SDK domain literal accepted by lark_oapi.Client.builder().domain(...)
FEISHU_REGIONS = {"feishu", "lark"}


@dataclass(frozen=True)
class FeishuNotificationsConfig:
    provider: str
    region: str
    chat_id: str
    app_id: str
    app_secret: str
    reply_ttl_hours: int = 72
    notify_on_failure: bool = False
    allow_dispatch: bool = False


@dataclass(frozen=True)
class DreamingConfig:
    enabled: bool = False
    schedule_time: str = "02:00"
    timezone: str = "UTC"
    catch_up_on_startup: bool = True
    agent_mode: str = "all"
    include_agents: list[str] = field(default_factory=list)
    exclude_agents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrgConfig:
    session_timeout_seconds: int | None = None
    feishu_notifications: FeishuNotificationsConfig | None = None
    dreaming: DreamingConfig = field(default_factory=DreamingConfig)
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


def _validate_agent_list(value: object, name: str, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OrgConfigError(f"{path}: dreaming.agents.{name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise OrgConfigError(f"{path}: dreaming.agents.{name} entries must be strings")
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
    timezone = schedule.get("timezone", "UTC")
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


def _parse_feishu_notifications(
    block: dict, path: str,
) -> FeishuNotificationsConfig | None:
    if not block.get("enabled", False):
        return None

    provider = block.get("provider")
    if provider != "feishu":
        raise OrgConfigError(
            f"{path}: feishu_notifications.provider must be 'feishu' in v1, "
            f"got {provider!r}"
        )

    region = block.get("region")
    if region not in FEISHU_REGIONS:
        raise OrgConfigError(
            f"{path}: feishu_notifications.region must be one of "
            f"{sorted(FEISHU_REGIONS)}, got {region!r}"
        )

    chat_id = block.get("chat_id")
    if not chat_id or not isinstance(chat_id, str):
        raise OrgConfigError(
            f"{path}: feishu_notifications.chat_id is required when enabled"
        )

    app_id = block.get("app_id")
    if not app_id or not isinstance(app_id, str):
        raise OrgConfigError(
            f"{path}: feishu_notifications.app_id is required when enabled"
        )

    app_secret = block.get("app_secret")
    if not app_secret or not isinstance(app_secret, str):
        raise OrgConfigError(
            f"{path}: feishu_notifications.app_secret is required when enabled"
        )

    ttl = _validate_positive_int(
        block.get("reply_ttl_hours", 72),
        "feishu_notifications.reply_ttl_hours",
        min_v=1, max_v=720, path=path,
    )

    notify_on_failure = block.get("notify_on_failure", False)
    if not isinstance(notify_on_failure, bool):
        raise OrgConfigError(
            f"{path}: feishu_notifications.notify_on_failure must be a boolean, "
            f"got {type(notify_on_failure).__name__}"
        )

    allow_dispatch = block.get("allow_dispatch", False)
    if not isinstance(allow_dispatch, bool):
        raise OrgConfigError(
            f"{path}: feishu_notifications.allow_dispatch must be a boolean, "
            f"got {type(allow_dispatch).__name__}"
        )

    return FeishuNotificationsConfig(
        provider=provider,
        region=region,
        chat_id=chat_id,
        app_id=app_id,
        app_secret=app_secret,
        reply_ttl_hours=ttl,
        notify_on_failure=notify_on_failure,
        allow_dispatch=allow_dispatch,
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

    feishu_block = data.get("feishu_notifications")
    feishu_cfg: FeishuNotificationsConfig | None = None
    if feishu_block is not None:
        if not isinstance(feishu_block, dict):
            raise OrgConfigError(f"{path}: feishu_notifications must be a mapping")
        feishu_cfg = _parse_feishu_notifications(feishu_block, path)

    dreaming_block = data.get("dreaming")
    dreaming_cfg = DreamingConfig()
    if dreaming_block is not None:
        dreaming_cfg = _parse_dreaming(dreaming_block, path)

    threads_block = data.get("threads")
    threads_kwargs: dict = {}
    if threads_block is not None:
        threads_kwargs = _parse_threads(threads_block, path)

    return OrgConfig(
        session_timeout_seconds=timeout,
        feishu_notifications=feishu_cfg,
        dreaming=dreaming_cfg,
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


