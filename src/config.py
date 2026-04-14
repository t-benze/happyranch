from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPC_",
        env_file=".env",
        extra="ignore",
    )

    # Project root — where source code and protocol docs live
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )

    # Claude Code executor
    claude_cli_path: str = "claude"
    permission_mode: str = "auto"

    # Protocol docs (relative to project_root)
    protocol_dir: str = "protocol"

    # Task constraints
    session_timeout_seconds: int = 1800  # 30 minutes

    # Orchestration loop
    max_orchestration_steps: int = 10

    # Performance tier thresholds
    tier_green_threshold: float = 0.90
    tier_yellow_threshold: float = 0.75

    # Daemon
    daemon_bind_host: str = "127.0.0.1"

    def get_protocol_dir(self) -> Path:
        return self.project_root / self.protocol_dir


settings = Settings()
