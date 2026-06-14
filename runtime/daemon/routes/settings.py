"""GET /api/v1/orgs/{slug}/settings — read-only system + org settings.

Phase 1: read-only System + Org settings surface. Phase 2 (separate task)
will add PUT /settings/org for editable org fields.

Spec: artifacts/TASK-349/settings-gui-design-spec-v2.md
"""
from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter

from runtime.config import settings as global_settings
from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config

router = APIRouter(dependencies=[require_token()])

# ----------------------------------------------------------------
# SystemSettingsView — ALLOW-LIST from module-global Settings
# ----------------------------------------------------------------


class SystemSettingsView(BaseModel):
    """Read-only view of selected daemon-wide settings.

    ALLOW-LIST: only the fields listed below are ever serialized.
    Any unlisted Settings field (permission_mode, codex_sandbox_mode,
    daemon_bind_host, daemon_port, etc.) is excluded by construction.
    """

    claude_cli_path: str
    codex_cli_path: str
    opencode_cli_path: str
    pi_cli_path: str
    session_timeout_seconds: int
    max_orchestration_steps: int
    queue_workers: int
    protocol_dir: str

    @staticmethod
    def _restart_required_field(field_name: str) -> bool:
        """True when changing this field requires a daemon restart."""
        return field_name != "session_timeout_seconds"

    def restart_required(self, field_name: str) -> bool:
        return self._restart_required_field(field_name)

    @classmethod
    def from_settings(cls, s) -> "SystemSettingsView":
        """Build from the module-global Settings."""
        return cls(
            claude_cli_path=s.claude_cli_path,
            codex_cli_path=s.codex_cli_path,
            opencode_cli_path=s.opencode_cli_path,
            pi_cli_path=s.pi_cli_path,
            session_timeout_seconds=s.session_timeout_seconds,
            max_orchestration_steps=s.max_orchestration_steps,
            queue_workers=s.queue_workers,
            protocol_dir=s.protocol_dir,
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
