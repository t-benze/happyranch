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

    # Data directory — where runtime data lives (db, workspaces)
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".opc"
    )

    # Claude Code executor
    claude_cli_path: str = "claude"
    permission_mode: str = "auto"

    # SQLite (relative to data_dir)
    db_path: str = "opc.db"

    # Agent workspaces (relative to data_dir)
    workspaces_dir: str = "workspaces"

    # Protocol docs (relative to project_root)
    protocol_dir: str = "protocol"

    # Git repos for agent workspace clones: {"name": "url", ...}
    # Set via OPC_REPOS='{"my-opc": "https://...", "web-app": "https://..."}'
    repos: dict[str, str] = Field(default_factory=dict)

    # Task constraints
    max_revision_rounds: int = 2
    session_timeout_seconds: int = 1800  # 30 minutes

    # Orchestration loop
    max_orchestration_steps: int = 10

    # Performance tier thresholds
    tier_green_threshold: float = 0.90
    tier_yellow_threshold: float = 0.75

    def get_db_path(self) -> Path:
        return self.data_dir / self.db_path

    def get_workspaces_dir(self) -> Path:
        return self.data_dir / self.workspaces_dir

    def get_protocol_dir(self) -> Path:
        return self.project_root / self.protocol_dir


settings = Settings()
