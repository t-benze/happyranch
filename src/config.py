from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPC_",
        env_file=".env",
        extra="ignore",
    )

    # Project root (resolved at import time)
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )

    # Claude Code executor
    claude_cli_path: str = "claude"
    permission_mode: str = "auto"

    # SQLite (relative to project_root)
    db_path: str = "opc.db"

    # Agent workspaces (relative to project_root)
    workspaces_dir: str = "workspaces"

    # Task constraints
    max_revision_rounds: int = 2
    session_timeout_seconds: int = 1800  # 30 minutes

    # Performance tier thresholds
    tier_green_threshold: float = 0.90
    tier_yellow_threshold: float = 0.75

    def get_db_path(self) -> Path:
        return self.project_root / self.db_path

    def get_workspaces_dir(self) -> Path:
        return self.project_root / self.workspaces_dir


settings = Settings()
