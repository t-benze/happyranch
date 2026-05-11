"""Org-level configuration loaded from <runtime>/org/config.yaml.

A small, additive layer between the global Settings defaults and per-agent
overrides. The file is optional — a runtime without it inherits the global
defaults exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from src.orchestrator._paths import OrgPaths


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


@dataclass(frozen=True)
class OrgConfig:
    session_timeout_seconds: int | None = None
    feishu_notifications: FeishuNotificationsConfig | None = None


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

    return FeishuNotificationsConfig(
        provider=provider,
        region=region,
        chat_id=chat_id,
        app_id=app_id,
        app_secret=app_secret,
        reply_ttl_hours=ttl,
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
            raise OrgConfigError(
                f"{path}: feishu_notifications must be a mapping"
            )
        feishu_cfg = _parse_feishu_notifications(feishu_block, str(path))

    return OrgConfig(
        session_timeout_seconds=timeout,
        feishu_notifications=feishu_cfg,
    )


