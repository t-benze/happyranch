from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _daemon_home() -> Path:
    """Resolve the daemon home dir (``~/.happyranch``).

    Honors ``HAPPYRANCH_DAEMON_HOME`` (used by tests to isolate state). Inlined
    from ``src.daemon.paths.daemon_home`` on purpose: ``config`` is foundational
    and must not import a ``daemon`` submodule.
    """
    override = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    return Path(override) if override else Path.home() / ".happyranch"


class Settings(BaseSettings):
    # Operational settings load from (highest precedence first):
    #   1. HAPPYRANCH_-prefixed environment variables
    #   2. <daemon-home>/config.yaml  (keys are field names, e.g. `queue_workers: 6`)
    #   3. the code defaults below
    model_config = SettingsConfigDict(
        env_prefix="HAPPYRANCH_",
        extra="ignore",
    )

    # Project root — where source code and protocol docs live
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )

    # Claude Code executor
    claude_cli_path: str = "claude"
    permission_mode: str = "auto"

    # Codex executor
    codex_cli_path: str = "codex"
    codex_sandbox_mode: str = "workspace-write"

    # opencode executor
    opencode_cli_path: str = "opencode"

    # Pi executor
    pi_cli_path: str = "pi"

    # Protocol docs (relative to project_root)
    protocol_dir: str = "protocol"

    # Task constraints
    session_timeout_seconds: int = 1800  # 30 minutes

    # Orchestration loop
    max_orchestration_steps: int = 50

    # Daemon
    daemon_bind_host: str = "127.0.0.1"
    daemon_port: int = 8765  # 0 = ephemeral (old behaviour)
    # Number of run_step worker slots (daemon-wide, shared across all orgs).
    # Each slot blocks on one agent subprocess for the whole session, so this
    # caps concurrent agent sessions. Must be positive.
    queue_workers: int = Field(default=3, gt=0)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority order (earlier wins): init args > env vars > config.yaml > secrets.
        # Dropping dotenv_settings disables .env loading by design.
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(
                settings_cls, yaml_file=_daemon_home() / "config.yaml"
            ),
            file_secret_settings,
        )

    def get_protocol_dir(self) -> Path:
        return self.project_root / self.protocol_dir


settings = Settings()
