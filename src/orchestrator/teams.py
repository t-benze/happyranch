"""Team registry: who manages whom, loaded from <runtime>/teams.yaml."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.runtime import RuntimeDir


DEFAULT_LAYOUT: dict[str, dict[str, object]] = {
    "engineering": {
        "manager": "engineering_head",
        "workers": ["product_manager", "dev_agent", "payment_agent", "qa_engineer"],
    },
    "content": {
        "manager": "content_manager",
        "workers": ["content_writer", "content_qa"],
    },
}


@dataclass(frozen=True)
class TeamManager:
    name: str
    team: str
    workers: tuple[str, ...]


class TeamsRegistry:
    def __init__(self, teams: dict[str, TeamManager], runtime: RuntimeDir | None = None) -> None:
        self._teams = dict(teams)
        self._runtime = runtime

    # ---- construction ----

    @classmethod
    def load(cls, runtime: RuntimeDir) -> "TeamsRegistry":
        path = runtime.teams_config_path
        if not path.exists():
            return cls._from_layout(DEFAULT_LAYOUT, runtime)
        raw = yaml.safe_load(path.read_text()) or {}
        layout = raw.get("teams") or {}
        if not layout:
            return cls._from_layout(DEFAULT_LAYOUT, runtime)
        return cls._from_layout(layout, runtime)

    @classmethod
    def _from_layout(cls, layout: dict[str, dict[str, object]], runtime: RuntimeDir | None = None) -> "TeamsRegistry":
        teams: dict[str, TeamManager] = {}
        for team_name, entry in layout.items():
            manager = entry.get("manager")
            workers = tuple(entry.get("workers") or ())
            if not isinstance(manager, str) or not manager:
                raise ValueError(f"team {team_name!r} missing manager")
            teams[team_name] = TeamManager(name=manager, team=team_name, workers=workers)
        return cls(teams, runtime=runtime)

    @classmethod
    def seed_default(cls, runtime: RuntimeDir) -> None:
        """Write the default teams.yaml to *runtime* if it doesn't already exist."""
        if runtime.teams_config_path.exists():
            return
        cls._from_layout(DEFAULT_LAYOUT, runtime).save(runtime)

    # ---- persistence ----

    def save(self, runtime: RuntimeDir | None = None) -> None:
        target = runtime if runtime is not None else self._runtime
        if target is None:
            raise RuntimeError("TeamsRegistry.save requires a RuntimeDir (none supplied and none stored)")
        path = target.teams_config_path
        payload = {"teams": {
            team: {"manager": m.name, "workers": list(m.workers)}
            for team, m in sorted(self._teams.items())
        }}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in same dir, then rename.
        fd, tmp = tempfile.mkstemp(prefix=".teams.", suffix=".yaml", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    # ---- lookups ----

    def teams(self) -> list[str]:
        return sorted(self._teams.keys())

    def manager_for_team(self, team: str) -> TeamManager:
        if team not in self._teams:
            raise KeyError(team)
        return self._teams[team]

    def team_for_agent(self, name: str) -> str | None:
        for team, m in self._teams.items():
            if name in m.workers or name == m.name:
                return team
        return None

    def team_for_manager(self, manager_name: str) -> str | None:
        for team, m in self._teams.items():
            if m.name == manager_name:
                return team
        return None

    def is_team_manager(self, name: str) -> bool:
        return any(m.name == name for m in self._teams.values())

    def all_agents(self) -> list[str]:
        out: list[str] = []
        for m in self._teams.values():
            out.append(m.name)
            out.extend(m.workers)
        return out

    # ---- mutation (auto-persist) ----

    def add_worker(self, team: str, agent: str) -> None:
        if team not in self._teams:
            raise KeyError(team)
        m = self._teams[team]
        if agent in m.workers:
            return
        self._teams[team] = TeamManager(
            name=m.name, team=m.team, workers=tuple([*m.workers, agent]),
        )
        if self._runtime is not None:
            self.save()

    def remove_worker(self, team: str, agent: str) -> None:
        if team not in self._teams:
            raise KeyError(team)
        m = self._teams[team]
        if agent not in m.workers:
            return
        self._teams[team] = TeamManager(
            name=m.name, team=m.team,
            workers=tuple(w for w in m.workers if w != agent),
        )
        if self._runtime is not None:
            self.save()
