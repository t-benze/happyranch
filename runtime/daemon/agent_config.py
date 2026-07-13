"""Per-agent workspace configuration (agent.yaml).

THR-095 (founder ruling option B): org/agents/<name>.md frontmatter is now
the SINGLE authoritative store for executor / repos / model.  agent.yaml
persistence for those three fields is DEPRECATED — no new writes target
agent.yaml, and the ORG-agent read paths (orchestrator resolvers,
thread_runner, dream_runner, list_agents) all read from AgentDef (.md).

The ``load_agent_config`` reader is kept for the one-shot migration
(``migrate_agent_yaml_to_frontmatter``) and for the ``set_agent_executor``
route's before/after display.  It will be removed in a follow-up cleanup
once existing workspaces have been migrated.

System assistant (runtime/system_assistant.py) writes its own agent.yaml
directly and has no org/agents/<name>.md — it is unaffected by this module.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_EXECUTOR = "claude"


def load_agent_config(workspace: Path) -> dict:
    """Parse workspace/agent.yaml, returning {} if missing.

    Existing configs that omit `executor` still behave as if they selected
    Claude Code.
    """
    path = workspace / "agent.yaml"
    if not path.exists():
        return {}
    config = yaml.safe_load(path.read_text()) or {}
    config.setdefault("repos", {})
    config.setdefault("executor", DEFAULT_EXECUTOR)
    return config


def write_default_agent_config(workspace: Path) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path.

    Kept for backward compatibility with system_assistant and any
    external callers.  Org-agent paths now write to .md frontmatter.
    """
    path = workspace / "agent.yaml"
    if path.exists():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"repos": {}, "executor": DEFAULT_EXECUTOR}, default_flow_style=False),
    )


def set_executor(workspace: Path, executor: str | None) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path.

    Org-agent executor is now written to .md frontmatter via AgentDef.
    """
    config = load_agent_config(workspace)
    config["executor"] = executor or DEFAULT_EXECUTOR
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def set_model(workspace: Path, model: str | None) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path.

    Org-agent model is now written to .md frontmatter via AgentDef.
    """
    config = load_agent_config(workspace)
    effective = model if model else None
    if effective:
        config["model"] = effective
    else:
        config.pop("model", None)
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def add_repo(workspace: Path, name: str, url: str) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path.

    Org-agent repos are now written to .md frontmatter via AgentDef.
    """
    config = load_agent_config(workspace)
    repos = config.setdefault("repos", {})
    if name in repos:
        raise ValueError(f"repo {name!r} already exists")
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def remove_repo(workspace: Path, name: str) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    del repos[name]
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def update_repo_url(workspace: Path, name: str, url: str) -> None:
    """DEPRECATED (THR-095). No longer called by any org-agent path."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def migrate_agent_yaml_to_frontmatter(paths) -> dict[str, str]:
    """One-shot idempotent reconcile: copy agent.yaml executor/repos/model
    into org/agents/<name>.md frontmatter for every org agent with a workspace.

    agent.yaml is the DE-FACTO OPERATIVE truth today, so this is the
    parity-preserving direction.  It also REPAIRS known live drift
    (e.g. engineering_manager.md repos:{} while agent.yaml has happyranch).

    NUANCE: when agent.yaml has no model key, CLEAR .md.model — do NOT
    preserve a stale .md.model the live spawn never used.
    Copy executor verbatim (default 'claude' if absent), repos verbatim.

    CONSUMPTION: after copying values into .md (or confirming they already
    match), the agent.yaml is DELETED and a ``.agent_yaml_consumed``
    sentinel file is written in the workspace.  The sentinel is the
    DURABLE one-shot gate — even if agent.yaml is later recreated, the
    next daemon startup skips the agent entirely.  This closes the
    reviewer-proven breach where a stale/edited agent.yaml could re-win
    on subsequent startups.

    FENCE: system_assistant writes its own agent.yaml directly and is NOT
    an org agent — it is always skipped.

    Returns a dict of agent_name -> outcome for logging.

    Safe to run every daemon startup (sentinel makes it a no-op after
    first run).
    """
    import logging
    import os
    import tempfile

    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from runtime.orchestrator.prompt_loader import load_agent

    _logger = logging.getLogger(__name__)
    results: dict[str, str] = {}

    agents_dir = paths.agents_dir
    workspaces_dir = paths.workspaces_dir

    if not workspaces_dir.exists():
        return results

    for workspace_entry in sorted(workspaces_dir.iterdir()):
        if not workspace_entry.is_dir():
            continue
        agent_name = workspace_entry.name
        workspace = workspace_entry

        yaml_path = workspace / "agent.yaml"
        consumed_sentinel = workspace / ".agent_yaml_consumed"

        # Sentinel check: if this workspace was already consumed by a
        # prior migration run, skip even if agent.yaml reappears.  The
        # sentinel is the durable proof that .md is now the single
        # authoritative store.
        if consumed_sentinel.exists():
            results[agent_name] = "skipped (already migrated)"
            continue

        if not yaml_path.exists():
            # Org-agent workspace with no agent.yaml on first post-cutover startup:
            # lock .md as authoritative one-shot by writing the sentinel NOW.
            # Otherwise a later agent.yaml could re-win on a subsequent startup.
            if agent_name != "system_assistant":
                agent_def = load_agent(paths, agent_name)
                if agent_def is not None:
                    consumed_sentinel.write_text("")
                    results[agent_name] = "locked (no agent.yaml, org agent)"
            continue

        try:
            # FENCE system_assistant: it writes its own agent.yaml directly
            # and is NOT an org agent in the migration scope.  Leave its
            # agent.yaml completely untouched.
            if agent_name == "system_assistant":
                results[agent_name] = "skipped (system_assistant)"
                continue

            agent_def = load_agent(paths, agent_name)
            if agent_def is None:
                _logger.warning(
                    "migrate_agent_yaml: agent %s has workspace but no .md — skipping",
                    agent_name,
                )
                results[agent_name] = "skipped (no .md)"
                continue

            cfg = load_agent_config(workspace)
            yaml_executor = cfg.get("executor") or "claude"
            yaml_repos = dict(cfg.get("repos") or {})
            yaml_model = cfg.get("model") or None  # empty string → None (old resolver semantics)

            # Check if migration is needed (idempotency guard)
            executor_changed = agent_def.executor != yaml_executor
            repos_changed = agent_def.repos != yaml_repos
            model_changed = agent_def.model != yaml_model

            if not (executor_changed or repos_changed or model_changed):
                # Already in sync — consume agent.yaml so a later stale/
                # edited agent.yaml can NEVER re-win on a subsequent startup.
                try:
                    yaml_path.unlink()
                except FileNotFoundError:
                    pass
                # Write durable sentinel so even if agent.yaml is recreated
                # the next startup skips this agent entirely.
                consumed_sentinel.write_text("")
                results[agent_name] = "unchanged (consumed)"
                continue

            # Build the reconciled AgentDef
            updated = AgentDef(
                name=agent_def.name,
                team=agent_def.team,
                role=agent_def.role,
                executor=yaml_executor,  # type: ignore[arg-type]
                allow_rules=agent_def.allow_rules,
                repos=yaml_repos,
                enrolled_by=agent_def.enrolled_by,
                enrolled_at_task=agent_def.enrolled_at_task,
                enrolled_at=agent_def.enrolled_at,
                system_prompt=agent_def.system_prompt,
                description=agent_def.description,
                model=yaml_model,  # None when agent.yaml had no model key
            )

            # Atomic write via tempfile + os.replace
            active_path = agents_dir / f"{agent_name}.md"
            fd, tmp = tempfile.mkstemp(
                prefix=f".{agent_name}.", suffix=".md", dir=str(agents_dir),
            )
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(render_agent_text(updated))
                os.replace(tmp, active_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                raise

            changes = []
            if executor_changed:
                changes.append(f"executor: {agent_def.executor} -> {yaml_executor}")
            if repos_changed:
                changes.append("repos updated")
            if model_changed:
                changes.append(f"model: {agent_def.model!r} -> {yaml_model!r}")

            # Consume agent.yaml: delete it AND write a durable sentinel.
            # The sentinel prevents a later stale/edited agent.yaml from
            # regaining authority on a subsequent daemon startup.
            try:
                yaml_path.unlink()
            except FileNotFoundError:
                pass
            consumed_sentinel.write_text("")

            outcome = "migrated (" + "; ".join(changes) + ")"
            results[agent_name] = outcome
            _logger.info("migrate_agent_yaml: %s — %s", agent_name, outcome)

        except Exception as exc:
            _logger.warning("migrate_agent_yaml: %s — error: %s", agent_name, exc)
            results[agent_name] = f"error: {exc}"

    return results
