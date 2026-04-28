"""Org-level configuration loaded from <runtime>/org/config.yaml.

A small, additive layer between the global Settings defaults and per-agent
overrides. The file is optional — a runtime without it inherits the global
defaults exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from src.runtime import RuntimeDir


class OrgConfigError(ValueError):
    """Raised when org/config.yaml is malformed or fails validation."""


@dataclass(frozen=True)
class OrgConfig:
    session_timeout_seconds: int | None = None


def load_org_config(runtime: RuntimeDir) -> OrgConfig:
    """Load <runtime>/org/config.yaml. Missing file -> empty OrgConfig.

    Validates types and ranges; does not fall back to defaults — callers
    layer this on top of Settings.
    """
    path = runtime.org_config_path
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
                f"{path}: session_timeout_seconds must be a positive integer, got {timeout!r}"
            )

    return OrgConfig(session_timeout_seconds=timeout)
