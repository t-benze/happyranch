"""Per-org root view used by the orchestrator + workspace adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrgPaths:
    root: Path

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

    @property
    def org_dir(self) -> Path:
        return self.root / "org"

    @property
    def agents_dir(self) -> Path:
        return self.org_dir / "agents"

    @property
    def pending_agents_dir(self) -> Path:
        return self.agents_dir / "_pending"

    @property
    def teams_config_path(self) -> Path:
        return self.org_dir / "teams.yaml"

    @property
    def org_config_path(self) -> Path:
        return self.org_dir / "config.yaml"

    @property
    def db_path(self) -> Path:
        return self.root / "happyranch.db"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"
