"""AgentDef dataclass and frontmatter parsing for <runtime>/org/agents/<name>.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml


_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_REPO_KEY_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)", re.DOTALL)


class AgentParseError(ValueError):
    """Raised when an agent file cannot be parsed or fails validation."""


Role = Literal["worker", "manager"]
Executor = Literal["claude", "codex", "opencode", "pi"]


@dataclass(frozen=True)
class AgentDef:
    name: str
    team: str
    role: Role
    executor: Executor
    allow_rules: tuple[str, ...]
    repos: dict[str, str]
    enrolled_by: str | None
    enrolled_at_task: str | None
    enrolled_at: datetime | None
    system_prompt: str
    description: str | None = None


def _parse_iso(ts: object) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not isinstance(ts, str):
        raise AgentParseError(f"enrolled_at must be a string or datetime, got {type(ts).__name__}")
    try:
        # PyYAML may emit naive datetimes; accept both Z suffix and +00:00.
        normalized = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AgentParseError(f"enrolled_at: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_agent_text(text: str, *, expected_name: str) -> AgentDef:
    """Parse a markdown-with-YAML-frontmatter agent file.

    Raises AgentParseError if structure or validation fails.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise AgentParseError("no leading frontmatter or missing closing fence")

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise AgentParseError(f"malformed YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise AgentParseError("frontmatter must be a mapping")

    body = m.group(2).lstrip("\n")
    if not body.strip():
        raise AgentParseError("empty body")

    for required in ("name", "team", "role", "executor"):
        if required not in fm:
            raise AgentParseError(f"missing frontmatter field: {required}")

    name = fm["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise AgentParseError(f"invalid name: {name!r}")
    if name != expected_name:
        raise AgentParseError(f"name mismatch: file says {name!r}, expected {expected_name!r}")

    team = fm["team"]
    if not isinstance(team, str) or not team:
        raise AgentParseError("team must be a non-empty string")

    role = fm["role"]
    if role not in ("worker", "manager"):
        raise AgentParseError(f"role must be 'worker' or 'manager', got {role!r}")

    executor = fm["executor"]
    if executor not in ("claude", "codex", "opencode", "pi"):
        raise AgentParseError(
            f"executor must be 'claude', 'codex', 'opencode', or 'pi', got {executor!r}"
        )

    raw_rules = fm.get("allow_rules") or []
    if not isinstance(raw_rules, list):
        raise AgentParseError("allow_rules must be a list")
    for r in raw_rules:
        if not isinstance(r, str) or not r:
            raise AgentParseError(f"allow_rules entries must be non-empty strings, got {r!r}")
    allow_rules: tuple[str, ...] = tuple(raw_rules)

    raw_repos = fm.get("repos") or {}
    if not isinstance(raw_repos, dict):
        raise AgentParseError("repos must be a mapping")
    repos: dict[str, str] = {}
    for k, v in raw_repos.items():
        if not isinstance(k, str) or not _REPO_KEY_RE.match(k):
            raise AgentParseError(f"invalid repo key: {k!r}")
        if not isinstance(v, str) or not v:
            raise AgentParseError(f"repo {k!r} url must be a non-empty string")
        repos[k] = v

    enrolled_by = fm.get("enrolled_by")
    if enrolled_by is not None and not isinstance(enrolled_by, str):
        raise AgentParseError("enrolled_by must be a string or null")

    enrolled_at_task = fm.get("enrolled_at_task")
    if enrolled_at_task is not None and not isinstance(enrolled_at_task, str):
        raise AgentParseError("enrolled_at_task must be a string or null")

    enrolled_at = _parse_iso(fm.get("enrolled_at"))

    description = fm.get("description")
    if description is not None and not isinstance(description, str):
        raise AgentParseError("description must be a string or null")

    return AgentDef(
        name=name,
        team=team,
        role=role,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        allow_rules=allow_rules,
        repos=repos,
        enrolled_by=enrolled_by,
        enrolled_at_task=enrolled_at_task,
        enrolled_at=enrolled_at,
        system_prompt=body if body.endswith("\n") else body + "\n",
        description=description,
    )


def render_agent_text(agent: AgentDef) -> str:
    """Inverse of parse_agent_text. Renders frontmatter + body."""
    fm: dict[str, object] = {
        "name": agent.name,
        "team": agent.team,
        "role": agent.role,
        "executor": agent.executor,
        "description": agent.description,
        "allow_rules": list(agent.allow_rules),
        "repos": dict(agent.repos),
        "enrolled_by": agent.enrolled_by,
        "enrolled_at_task": agent.enrolled_at_task,
        "enrolled_at": (
            agent.enrolled_at.isoformat().replace("+00:00", "Z")
            if agent.enrolled_at is not None
            else None
        ),
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    body = agent.system_prompt if agent.system_prompt.endswith("\n") else agent.system_prompt + "\n"
    return f"---\n{fm_text}\n---\n\n{body}"


def parse_agent_file(path: Path) -> AgentDef:
    """Read and parse an agent file. The filename stem is the expected name."""
    return parse_agent_text(path.read_text(), expected_name=path.stem)
