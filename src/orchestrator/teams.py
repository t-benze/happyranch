"""Team registry: who manages whom, loaded from <root>/org/teams.yaml."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TeamManager:
    name: str
    team: str
    workers: tuple[str, ...]


class TeamsRegistry:
    def __init__(self, teams: dict[str, TeamManager], root: Path | None = None) -> None:
        self._teams = dict(teams)
        self._root = root

    # ---- construction ----

    @classmethod
    def load(cls, root: Path) -> "TeamsRegistry":
        """Load from <root>/org/teams.yaml. ``root`` is an org root (or, in
        legacy single-org runtimes, the runtime root)."""
        path = root / "org" / "teams.yaml"
        if not path.exists():
            return cls({}, root=root)
        raw = yaml.safe_load(path.read_text()) or {}
        layout = raw.get("teams") or {}
        return cls._from_layout(layout, root)

    @classmethod
    def _from_layout(cls, layout: dict[str, dict[str, object]], root: Path | None = None) -> "TeamsRegistry":
        teams: dict[str, TeamManager] = {}
        for team_name, entry in layout.items():
            manager = entry.get("manager")
            workers = tuple(entry.get("workers") or ())
            if not isinstance(manager, str) or not manager:
                raise ValueError(f"team {team_name!r} missing manager")
            teams[team_name] = TeamManager(name=manager, team=team_name, workers=workers)
        return cls(teams, root=root)

    @classmethod
    def seed_empty(cls, root: Path) -> None:
        """Write an empty ``teams: {}`` block under *root* if it doesn't exist."""
        path = root / "org" / "teams.yaml"
        if path.exists():
            return
        cls({}, root=root).save()

    # ---- persistence ----

    def save(self, root: Path | None = None) -> None:
        target = root if root is not None else self._root
        if target is None:
            raise RuntimeError("TeamsRegistry.save requires a root path (none supplied and none stored)")
        path = target / "org" / "teams.yaml"
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
        if self._root is not None:
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
        if self._root is not None:
            self.save()

    def remove_team(self, name: str) -> None:
        """Remove a team entirely.

        Auto-persists when ``self._root`` is set. No-op if the team does
        not exist (mirrors ``remove_worker``'s tolerance of missing
        targets). Used by ``founder_create_agent`` to roll back a freshly
        created team if the subsequent agent file write fails.
        """
        if name not in self._teams:
            return
        del self._teams[name]
        if self._root is not None:
            self.save()

    def add_team(self, name: str, manager: str) -> None:
        """Register a new team with the given manager and empty workers.

        Auto-persists to teams.yaml when ``self._root`` is set, matching
        ``add_worker`` / ``remove_worker`` semantics.

        Raises ValueError if a team with this name already exists.
        """
        if name in self._teams:
            raise ValueError(f"team {name!r} already exists")
        self._teams[name] = TeamManager(name=manager, team=name, workers=())
        if self._root is not None:
            self.save()
