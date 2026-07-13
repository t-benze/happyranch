from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.daemon.agent_config import (
    add_repo,
    load_agent_config,
    remove_repo,
    set_executor,
    set_model,
    update_repo_url,
    write_default_agent_config,
)


def test_add_repo_creates_entry(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["web-app"] == "https://github.com/t-benze/web-app.git"
    assert cfg["executor"] == "claude"


def test_add_repo_duplicate_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    with pytest.raises(ValueError, match="already exists"):
        add_repo(tmp_path, "web-app", "https://other.git")


def test_add_repo_initializes_repos_if_missing(tmp_path: Path) -> None:
    """agent.yaml exists but has no repos key."""
    (tmp_path / "agent.yaml").write_text(yaml.dump({"other": "val"}))
    add_repo(tmp_path, "docs", "https://github.com/t-benze/docs.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["docs"] == "https://github.com/t-benze/docs.git"
    assert cfg["executor"] == "claude"


def test_remove_repo_deletes_entry(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    remove_repo(tmp_path, "web-app")
    cfg = load_agent_config(tmp_path)
    assert "web-app" not in cfg.get("repos", {})


def test_remove_repo_nonexistent_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    with pytest.raises(KeyError, match="web-app"):
        remove_repo(tmp_path, "web-app")


def test_update_repo_url_changes_url(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://old.git")
    update_repo_url(tmp_path, "web-app", "https://new.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["web-app"] == "https://new.git"


def test_update_repo_url_nonexistent_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    with pytest.raises(KeyError, match="web-app"):
        update_repo_url(tmp_path, "web-app", "https://new.git")


def test_load_agent_config_defaults_executor_when_missing(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text(yaml.dump({"repos": {}}))
    cfg = load_agent_config(tmp_path)
    assert cfg["executor"] == "claude"


def test_set_executor_updates_agent_yaml(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    set_executor(tmp_path, "codex")
    cfg = load_agent_config(tmp_path)
    assert cfg["executor"] == "codex"


# ---- model ----

def test_set_model_writes_to_agent_yaml(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    set_model(tmp_path, "gpt-5")
    cfg = load_agent_config(tmp_path)
    assert cfg["model"] == "gpt-5"


def test_set_model_none_clears_key(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    set_model(tmp_path, "gpt-5")
    set_model(tmp_path, None)
    cfg = load_agent_config(tmp_path)
    assert "model" not in cfg


def test_set_model_empty_string_clears_key(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    set_model(tmp_path, "gpt-5")
    set_model(tmp_path, "")
    cfg = load_agent_config(tmp_path)
    assert "model" not in cfg


def test_load_agent_config_no_model_key_when_absent(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    cfg = load_agent_config(tmp_path)
    assert "model" not in cfg
    assert "executor" in cfg  # still injects default executor


# ---- migrate_agent_yaml_to_frontmatter ----


def _write_agent_md(paths, agent_name: str, **overrides) -> None:
    """Write a minimal AgentDef to the agents_dir."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    defaults = dict(
        name=agent_name, team="engineering", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=None, system_prompt="system prompt", description="desc",
        model=None,
    )
    defaults.update(overrides)
    agent = AgentDef(**defaults)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{agent_name}.md").write_text(render_agent_text(agent))


def _write_agent_yaml(workspace: Path, **kwargs) -> None:
    """Write an agent.yaml to the workspace dir."""
    import yaml as _yaml
    workspace.mkdir(parents=True, exist_ok=True)
    data = dict(kwargs)
    (workspace / "agent.yaml").write_text(_yaml.dump(data, default_flow_style=False))


def test_migrate_copies_executor_repos_model_from_yaml_to_md(
    tmp_path: Path,
) -> None:
    """(a) When .md lacks executor/repos/model, migration copies them from
    agent.yaml into .md frontmatter."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    # .md has default executor=claude, repos={}, model=None
    _write_agent_md(paths, "dev_agent")
    # agent.yaml has codex, repos, and a model
    _write_agent_yaml(workspace, executor="codex", model="gpt-5",
                      repos={"happyranch": "https://github.com/t-benze/happyranch.git"})

    results = migrate_agent_yaml_to_frontmatter(paths)
    assert results["dev_agent"].startswith("migrated")

    # After migration, .md reflects agent.yaml values
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.executor == "codex"
    assert agent_def.model == "gpt-5"
    assert agent_def.repos == {"happyranch": "https://github.com/t-benze/happyranch.git"}


def test_migrate_repairs_engineering_manager_repos_drift(
    tmp_path: Path,
) -> None:
    """(b) engineering_manager drift-repair: .md has repos:{} while
    agent.yaml has happyranch -> after migration .md declares happyranch."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "engineering_manager"

    # .md has repos={} (the known drift state)
    _write_agent_md(paths, "engineering_manager", executor="claude",
                    repos={}, model="claude-sonnet-4-5")
    # agent.yaml has the real repos AND the same model (to avoid model-clearing)
    _write_agent_yaml(workspace, executor="claude", model="claude-sonnet-4-5",
                      repos={"happyranch": "https://github.com/t-benze/happyranch.git"})

    results = migrate_agent_yaml_to_frontmatter(paths)
    assert results["engineering_manager"].startswith("migrated")
    assert "repos updated" in results["engineering_manager"]

    # After migration, .md repos now carries happyranch from agent.yaml
    agent_def = load_agent(paths, "engineering_manager")
    assert agent_def is not None
    assert agent_def.repos == {"happyranch": "https://github.com/t-benze/happyranch.git"}
    # executor and model unchanged (already matched)
    assert agent_def.executor == "claude"
    assert agent_def.model == "claude-sonnet-4-5"


def test_migrate_idempotent_second_run_noop(
    tmp_path: Path,
) -> None:
    """(c) One-shot consumption: after migration deletes agent.yaml
    and writes the .agent_yaml_consumed sentinel, a second run skips
    with "already migrated" — .md stays unchanged and authoritative."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    _write_agent_md(paths, "dev_agent")
    _write_agent_yaml(workspace, executor="codex", model="gpt-5",
                      repos={"docs": "https://github.com/t-benze/docs.git"})

    # First run: migrates AND deletes agent.yaml
    results1 = migrate_agent_yaml_to_frontmatter(paths)
    assert results1["dev_agent"].startswith("migrated")
    assert not (workspace / "agent.yaml").exists()  # consumed

    # Second run: sentinel exists → agent skipped with "already migrated"
    results2 = migrate_agent_yaml_to_frontmatter(paths)
    assert results2["dev_agent"] == "skipped (already migrated)"


def test_migrate_empty_model_normalizes_to_none(
    tmp_path: Path,
) -> None:
    """FIX 2: agent.yaml with model:'' -> frontmatter model unset (None).
    The old live resolver treated empty string as unset, and AgentDef
    parsing rejects empty models. After migration, the agent still loads."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    # .md starts with model=None (no model)
    _write_agent_md(paths, "dev_agent", model=None)
    # agent.yaml has model: "" (empty string — legacy state)
    _write_agent_yaml(workspace, executor="claude", repos={}, model="")

    results = migrate_agent_yaml_to_frontmatter(paths)
    # model unchanged: .md was None, agent.yaml '' → None (same)
    assert "consumed" in results["dev_agent"]

    # Verify agent still loads after migration
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.model is None

    # Now test the case where .md has a stale model but agent.yaml has ''
    # -> migration should clear .md.model to None.
    #
    # First remove the sentinel so the migration runs again, then rewrite
    # both .md and agent.yaml to simulate a first-run scenario.
    (workspace / ".agent_yaml_consumed").unlink()
    _write_agent_md(paths, "dev_agent", model="old-model")
    _write_agent_yaml(workspace, executor="claude", repos={}, model="")
    results2 = migrate_agent_yaml_to_frontmatter(paths)
    assert "migrated" in results2["dev_agent"]
    assert "model" in results2["dev_agent"]

    agent_def2 = load_agent(paths, "dev_agent")
    assert agent_def2 is not None
    assert agent_def2.model is None


def test_migrate_divergent_agent_yaml_is_ignored_after_cutover(
    tmp_path: Path,
) -> None:
    """RED-proof (reviewer gap closed): after migration cutover CONSUMED
    agent.yaml and wrote the .agent_yaml_consumed sentinel, a later
    stale/edited agent.yaml CANNOT re-win on a subsequent daemon startup.

    Steps:
      1. Run migrate once — .md now holds the values, agent.yaml is
         deleted, sentinel is written.
      2. Rewrite agent.yaml with DIVERGENT executor/repos/model.
      3. RE-RUN migrate_agent_yaml_to_frontmatter (the startup path —
         NOT just load_agent; this is the gap).
      4. Assert .md/AgentDef is unchanged (divergent agent.yaml ignored
         because sentinel exists).
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    # Step 1: .md and agent.yaml start in sync
    _write_agent_md(paths, "dev_agent", executor="codex", model="gpt-5",
                    repos={"docs": "https://github.com/t-benze/docs.git"})
    _write_agent_yaml(workspace, executor="codex", model="gpt-5",
                      repos={"docs": "https://github.com/t-benze/docs.git"})

    # Migration confirms they're in sync AND deletes agent.yaml + writes sentinel
    results = migrate_agent_yaml_to_frontmatter(paths)
    assert "consumed" in results["dev_agent"]
    assert not (workspace / "agent.yaml").exists(), "agent.yaml must be deleted"
    assert (workspace / ".agent_yaml_consumed").exists(), "sentinel must be written"

    # Verify .md is intact
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.executor == "codex"
    assert agent_def.repos == {"docs": "https://github.com/t-benze/docs.git"}
    assert agent_def.model == "gpt-5"

    # Step 2: stealth edit — someone writes a divergent agent.yaml
    _write_agent_yaml(workspace, executor="pi", model="claude-sonnet-4-5",
                      repos={"evil": "https://evil.git"})

    # Step 3: RE-RUN migration (the startup path — the reviewer's gap:
    # prior test only called load_agent and missed the startup path).
    results2 = migrate_agent_yaml_to_frontmatter(paths)
    # Sentinel exists → migration skips entirely, even though agent.yaml
    # has been recreated with divergent values.
    assert "skipped (already migrated)" in results2.get("dev_agent", "")

    # Step 4: .md/AgentDef remains authoritative
    agent_def2 = load_agent(paths, "dev_agent")
    assert agent_def2 is not None
    assert agent_def2.executor == "codex", ".md executor must still be codex"
    assert agent_def2.repos == {"docs": "https://github.com/t-benze/docs.git"}, \
        ".md repos must still be docs"
    assert agent_def2.model == "gpt-5", ".md model must still be gpt-5"

    # Confirm agent.yaml divergent values exist but were ignored
    assert (workspace / "agent.yaml").exists(), (
        "agent.yaml should still exist (divergent values ignored, not deleted)"
    )


def test_migrate_skips_workspace_without_yaml(
    tmp_path: Path,
) -> None:
    """Workspace dir exists but no agent.yaml -> skipped (not in results)."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "no_yaml_agent"
    workspace.mkdir(parents=True)
    # no agent.yaml written

    results = migrate_agent_yaml_to_frontmatter(paths)
    assert "no_yaml_agent" not in results


def test_migrate_skips_workspace_without_md(
    tmp_path: Path,
) -> None:
    """Workspace has agent.yaml but no .md -> skipped with reason."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "orphan_agent"
    _write_agent_yaml(workspace, executor="claude")
    # no .md written

    results = migrate_agent_yaml_to_frontmatter(paths)
    assert results["orphan_agent"] == "skipped (no .md)"


# ── THR-095 REVISE round 3: no-yaml entry states ──────────────────────


def test_migrate_no_yaml_org_agent_locks_md_with_sentinel(
    tmp_path: Path,
) -> None:
    """RED regression test (reviewer gap): org agent .md present
    (executor=codex/repos=docs/model=gpt-5), workspace with NO agent.yaml
    -> first migrate writes sentinel, locking .md as authoritative one-shot.
    A later DIVERGENT agent.yaml must NEVER re-win."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    # .md is the authoritative store: codex / docs / gpt-5
    _write_agent_md(paths, "dev_agent", executor="codex", model="gpt-5",
                    repos={"docs": "https://github.com/t-benze/docs.git"})
    # Workspace dir exists but NO agent.yaml
    workspace.mkdir(parents=True)

    # First migration: agent.yaml absent, but this IS an org agent
    results1 = migrate_agent_yaml_to_frontmatter(paths)
    assert results1["dev_agent"] == "locked (no agent.yaml, org agent)"

    # Sentinel must exist after first migration
    sentinel = workspace / ".agent_yaml_consumed"
    assert sentinel.exists(), (
        "Sentinel must be written even though agent.yaml was absent — "
        "the .md is now the single authoritative store and must be locked"
    )

    # .md must be unchanged
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.executor == "codex"
    assert agent_def.model == "gpt-5"
    assert agent_def.repos == {"docs": "https://github.com/t-benze/docs.git"}

    # ── Stealth edit: someone creates a DIVERGENT agent.yaml ──
    _write_agent_yaml(workspace, executor="pi", model="claude-sonnet-4-5",
                      repos={"evil": "https://evil.git"})

    # RE-RUN migration via the STARTUP path (the reviewer's exact gap)
    results2 = migrate_agent_yaml_to_frontmatter(paths)
    assert results2["dev_agent"] == "skipped (already migrated)"

    # .md is STILL authoritative — divergent agent.yaml must NEVER win
    agent_def2 = load_agent(paths, "dev_agent")
    assert agent_def2 is not None
    assert agent_def2.executor == "codex", ".md executor must still be codex"
    assert agent_def2.model == "gpt-5", ".md model must still be gpt-5"
    assert agent_def2.repos == {"docs": "https://github.com/t-benze/docs.git"}, \
        ".md repos must still be docs"

    # agent.yaml divergent values exist but were ignored
    assert (workspace / "agent.yaml").exists(), (
        "agent.yaml should still exist (divergent values ignored, not deleted)"
    )


def test_migrate_no_yaml_non_org_and_system_assistant_untouched(
    tmp_path: Path,
) -> None:
    """No-agent.yaml entry states (d):
    - system_assistant workspace with no agent.yaml → untouched, no sentinel
    - non-org workspace (no .md) with no agent.yaml → untouched, no sentinel
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)

    # ── system_assistant: workspace exists, no agent.yaml ──
    sa_ws = tmp_path / "workspaces" / "system_assistant"
    sa_ws.mkdir(parents=True)
    # system_assistant does NOT have an org-agent .md

    results = migrate_agent_yaml_to_frontmatter(paths)
    # Must not appear in results (no sentinel written, no action taken)
    assert "system_assistant" not in results
    sentinel = sa_ws / ".agent_yaml_consumed"
    assert not sentinel.exists(), "system_assistant with no yaml must NOT get a sentinel"

    # ── non-org workspace: directory exists but no .md and no agent.yaml ──
    other_ws = tmp_path / "workspaces" / "some_stranger"
    other_ws.mkdir(parents=True)
    # Re-run — stranger must not appear in results and must have no sentinel
    results = migrate_agent_yaml_to_frontmatter(paths)
    assert "some_stranger" not in results
    sentinel2 = other_ws / ".agent_yaml_consumed"
    assert not sentinel2.exists(), "non-org workspace with no yaml must NOT get a sentinel"


def test_migrate_preserves_allow_rules_and_other_metadata(
    tmp_path: Path,
) -> None:
    """Migration must NOT alter allow_rules, system_prompt, description,
    enrolled_by, enrolled_at_task, or role — only executor/repos/model."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter

    paths = OrgPaths(root=tmp_path)
    workspace = tmp_path / "workspaces" / "dev_agent"

    _write_agent_md(paths, "dev_agent",
                    executor="claude",
                    allow_rules=("Bash(git:*)", "Bash(npm:*)"),
                    system_prompt="You are a dev agent.",
                    description="Writes code.",
                    role="worker",
                    team="engineering",
                    enrolled_by="engineering_head",
                    enrolled_at_task="TASK-100",
                    model=None,
                    repos={})
    _write_agent_yaml(workspace, executor="codex",
                      repos={"docs": "https://github.com/t-benze/docs.git"})

    results = migrate_agent_yaml_to_frontmatter(paths)
    assert results["dev_agent"].startswith("migrated")

    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    # Changed fields
    assert agent_def.executor == "codex"
    assert agent_def.repos == {"docs": "https://github.com/t-benze/docs.git"}
    # Preserved fields (system_prompt may have trailing newline from render_agent_text)
    assert agent_def.allow_rules == ("Bash(git:*)", "Bash(npm:*)")
    assert agent_def.system_prompt.rstrip("\n") == "You are a dev agent."
    assert agent_def.description == "Writes code."
    assert agent_def.role == "worker"
    assert agent_def.team == "engineering"
    assert agent_def.enrolled_by == "engineering_head"
    assert agent_def.enrolled_at_task == "TASK-100"
    assert agent_def.model is None
