"""Team registry: who manages whom, loaded from <root>/org/teams.yaml."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TeamManager:
    name: str | None
    team: str
    workers: tuple[str, ...]
    kind: str = "agent"
    principal: str | None = None
    tagged: bool = False

    def __post_init__(self) -> None:
        principal = self.principal if self.principal is not None else self.name
        if self.kind not in {"agent", "human"} or not isinstance(principal, str) or not principal.strip():
            raise ValueError("invalid team manager principal")
        if self.kind == "human" and (principal != "founder" or self.name is not None):
            raise ValueError("human team manager must be founder, with no executable name")
        if self.kind == "agent" and self.name != principal:
            raise ValueError("agent team manager name must match principal")
        object.__setattr__(self, "principal", principal)

    def manager_value(self, *, typed: bool = False) -> str | dict[str, str]:
        if typed or self.tagged or self.kind == "human":
            return {"kind": self.kind, "principal": self.principal}
        return self.name


class TeamsRegistry:
    def __init__(self, teams: dict[str, TeamManager], root: Path | None = None, *, metadata: dict | None = None) -> None:
        self._teams = dict(teams)
        self._root = root
        self._metadata = dict(metadata or {})
        for pointer in ("default_team", "task_default_team"):
            value = self._metadata.get(pointer)
            if pointer in self._metadata and (not isinstance(value, str) or value not in self._teams):
                raise ValueError(f"invalid {pointer}: {value!r}")

    @property
    def default_team(self) -> str:
        return self._metadata.get("default_team", "engineering")

    @property
    def task_default_team(self) -> str:
        return self._metadata.get("task_default_team", "engineering")

    # ---- construction ----

    @classmethod
    def load(cls, root: Path) -> "TeamsRegistry":
        """Load from <root>/org/teams.yaml. ``root`` is an org root (or, in
        legacy single-org runtimes, the runtime root)."""
        path = root / "org" / "teams.yaml"
        if not path.exists():
            return cls({}, root=root)
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError("teams.yaml must be a mapping")
        layout = raw.get("teams") or {}
        return cls._from_layout(layout, root, metadata={key: value for key, value in raw.items() if key != "teams"})

    @classmethod
    def _from_layout(cls, layout: dict[str, dict[str, object]], root: Path | None = None, *, metadata: dict | None = None) -> "TeamsRegistry":
        if not isinstance(layout, dict):
            raise ValueError("teams must be a mapping")
        teams: dict[str, TeamManager] = {}
        for team_name, entry in layout.items():
            if not isinstance(team_name, str) or not team_name.strip() or not isinstance(entry, dict):
                raise ValueError("invalid team entry")
            manager = entry.get("manager")
            workers = entry.get("workers", [])
            if workers is None:
                workers = []
            if not isinstance(workers, list) or any(not isinstance(w, str) or not w.strip() for w in workers):
                raise ValueError(f"team {team_name!r} invalid workers")
            if isinstance(manager, str):
                kind, principal, tagged = "agent", manager, False
            elif isinstance(manager, dict) and set(manager) == {"kind", "principal"}:
                kind, principal, tagged = manager["kind"], manager["principal"], True
            else:
                raise ValueError(f"team {team_name!r} invalid manager")
            teams[team_name] = TeamManager(
                name=principal if kind == "agent" else None, team=team_name,
                workers=tuple(workers), kind=kind, principal=principal, tagged=tagged,
            )
        return cls(teams, root=root, metadata=metadata)

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
            team: {"manager": m.manager_value(), "workers": list(m.workers)}
            for team, m in sorted(self._teams.items())
        }}
        payload = {**self._metadata, **payload}
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

    def executable_manager_for_team(self, team: str) -> str | None:
        return self.manager_for_team(team).name

    def team_row(self, team: str) -> dict:
        manager = self.manager_for_team(team)
        row = {"name": team, "manager": manager.name, "workers": list(manager.workers)}
        if manager.kind == "human":
            row.update(manager_kind="human", human_manager=manager.principal,
                       is_default=team == self.default_team)
        return row

    def team_for_agent(self, name: str) -> str | None:
        for team, m in self._teams.items():
            if name in m.workers or name == m.name:
                return team
        return None

    def team_for_manager(self, manager_name: str) -> str | None:
        for team, m in self._teams.items():
            if m.kind == "agent" and m.name == manager_name:
                return team
        return None

    def teams_for_manager(self, manager_name: str) -> tuple[str, ...]:
        """Return every exact registration for *manager_name* in stable order.

        Authority callers must require cardinality one; the older first-match
        helper remains for non-authority compatibility consumers.
        """
        return tuple(sorted(team for team, manager in self._teams.items()
                            if manager.kind == "agent" and manager.name == manager_name))

    def is_team_manager(self, name: str) -> bool:
        return any(m.kind == "agent" and m.name == name for m in self._teams.values())

    def all_agents(self) -> list[str]:
        out: list[str] = []
        for m in self._teams.values():
            if m.name is not None:
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
        self._teams[team] = replace(m, workers=(*m.workers, agent))
        if self._root is not None:
            self.save()

    def remove_worker(self, team: str, agent: str) -> None:
        if team not in self._teams:
            raise KeyError(team)
        m = self._teams[team]
        if agent not in m.workers:
            return
        self._teams[team] = replace(m, workers=tuple(w for w in m.workers if w != agent))
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
