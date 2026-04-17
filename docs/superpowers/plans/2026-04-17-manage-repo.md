# manage-repo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let agents add, remove, or update repos in their own `agent.yaml` via a single `opc manage-repo` CLI subcommand backed by a daemon route.

**Architecture:** Single daemon route `POST /agents/{name}/repos` dispatches on an `action` field (add/remove/update). Each operation mutates `agent.yaml` via helpers in `agent_config.py`, then performs the filesystem work (clone/delete), and finishes by calling `ensure_workspace_ready` to regenerate `settings.json` so the PreToolUse git-pull hook stays in sync.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, PyYAML, pytest, httpx (CLI client)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/daemon/agent_config.py` | Modify | Add `add_repo`, `remove_repo`, `update_repo_url` helpers |
| `tests/test_agent_config.py` | Create | Unit tests for the three helpers + error cases |
| `src/daemon/routes/agents.py` | Modify | Add `POST /agents/{name}/repos` route |
| `tests/daemon/test_routes_agents.py` | Modify | Route-level tests for all 3 actions + errors |
| `src/cli.py` | Modify | Add `opc manage-repo` subcommand with `--from-file` support |
| `tests/test_cli.py` | Modify | Parser tests + `cmd_manage_repo` tests |
| `protocol/skills/manage-repo/SKILL.md` | Create | Agent-facing skill with JSON-file + single-line invocation |
| `tests/test_skills.py` | Modify | Add `"manage-repo"` to the parameterized frontmatter check |

---

### Task 1: agent_config helpers

**Files:**
- Modify: `src/daemon/agent_config.py`
- Create: `tests/test_agent_config.py`

- [ ] **Step 1: Write failing tests for `add_repo`**

```python
# tests/test_agent_config.py
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.daemon.agent_config import (
    add_repo,
    load_agent_config,
    remove_repo,
    update_repo_url,
    write_default_agent_config,
)


def test_add_repo_creates_entry(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["web-app"] == "https://github.com/t-benze/web-app.git"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_config.py -v`
Expected: FAIL — `add_repo` not defined

- [ ] **Step 3: Implement `add_repo`**

Add to `src/daemon/agent_config.py`:

```python
def add_repo(workspace: Path, name: str, url: str) -> None:
    """Add a repo entry to agent.yaml. Raises ValueError if name exists."""
    config = load_agent_config(workspace)
    repos = config.setdefault("repos", {})
    if name in repos:
        raise ValueError(f"repo {name!r} already exists")
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))
```

- [ ] **Step 4: Run tests to verify `add_repo` tests pass**

Run: `uv run pytest tests/test_agent_config.py -v -k add_repo`
Expected: 3 tests PASS

- [ ] **Step 5: Write failing tests for `remove_repo`**

Append to `tests/test_agent_config.py`:

```python
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
```

- [ ] **Step 6: Implement `remove_repo`**

Add to `src/daemon/agent_config.py`:

```python
def remove_repo(workspace: Path, name: str) -> None:
    """Remove a repo entry from agent.yaml. Raises KeyError if not found."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    del repos[name]
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))
```

- [ ] **Step 7: Run tests to verify `remove_repo` tests pass**

Run: `uv run pytest tests/test_agent_config.py -v -k remove_repo`
Expected: 2 tests PASS

- [ ] **Step 8: Write failing tests for `update_repo_url`**

Append to `tests/test_agent_config.py`:

```python
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
```

- [ ] **Step 9: Implement `update_repo_url`**

Add to `src/daemon/agent_config.py`:

```python
def update_repo_url(workspace: Path, name: str, url: str) -> None:
    """Change the URL for an existing repo. Raises KeyError if not found."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))
```

- [ ] **Step 10: Run all agent_config tests**

Run: `uv run pytest tests/test_agent_config.py -v`
Expected: 7 tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/daemon/agent_config.py tests/test_agent_config.py
git commit -m "feat(agent_config): add/remove/update repo helpers for agent.yaml"
```

---

### Task 2: Daemon route

**Files:**
- Modify: `src/daemon/routes/agents.py`
- Modify: `tests/daemon/test_routes_agents.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/daemon/test_routes_agents.py`:

```python
import shutil
from unittest.mock import patch


def test_manage_repo_add_creates_entry_and_clones(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "add", "repo_name": "docs", "url": "https://github.com/t-benze/docs.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_ctx.clone_repo.assert_called_once()
    mock_ctx.ensure_workspace_ready.assert_called_once()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://github.com/t-benze/docs.git"


def test_manage_repo_add_duplicate_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs", "url": "https://new.git"},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_manage_repo_remove_deletes_entry_and_dir(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()  # fake git dir

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "remove", "repo_name": "docs"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert not repo_dir.exists()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert "docs" not in cfg.get("repos", {})


def test_manage_repo_remove_nonexistent_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "remove", "repo_name": "ghost"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_repo_update_reclones(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "update", "repo_name": "docs", "url": "https://new.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    mock_ctx.clone_repo.assert_called_once()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://new.git"


def test_manage_repo_add_missing_url_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_manage_repo_unknown_workspace_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/nonexistent/repos",
        json={"action": "add", "repo_name": "x", "url": "https://x.git"},
        headers=auth_headers,
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v -k manage_repo`
Expected: FAIL — route not defined (404 for all)

- [ ] **Step 3: Implement the route**

Add to `src/daemon/routes/agents.py`, after the existing imports:

```python
import shutil
from enum import StrEnum

from src.daemon.agent_config import add_repo, remove_repo, update_repo_url
```

Add the request model and route:

```python
class RepoAction(StrEnum):
    add = "add"
    remove = "remove"
    update = "update"


class ManageRepoBody(BaseModel):
    action: RepoAction
    repo_name: str
    url: str | None = None


@router.post("/agents/{agent_name}/repos")
async def manage_repo(agent_name: str, body: ManageRepoBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    workspace = state.runtime.workspaces_dir / agent_name
    if not workspace.exists():
        raise HTTPException(status_code=404, detail=f"workspace {agent_name!r} not found")

    if body.action in (RepoAction.add, RepoAction.update) and not body.url:
        raise HTTPException(status_code=422, detail=f"url required for {body.action!r}")

    ctx = ContextBuilder(state.settings)
    prompts = load_all_prompts(state.settings.get_protocol_dir())
    agent_prompt = prompts.get(agent_name, "")

    if body.action == RepoAction.add:
        try:
            add_repo(workspace, body.repo_name, body.url)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)

    elif body.action == RepoAction.remove:
        try:
            remove_repo(workspace, body.repo_name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)

    elif body.action == RepoAction.update:
        try:
            update_repo_url(workspace, body.repo_name, body.url)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready, workspace, agent_name, agent_prompt,
    )
    return {"ok": True}
```

- [ ] **Step 4: Run route tests**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(daemon): POST /agents/{name}/repos for add/remove/update"
```

---

### Task 3: CLI subcommand

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI parser tests**

Append to `tests/test_cli.py`:

```python
def test_manage_repo_parser_add():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "add",
        "--agent", "dev_agent",
        "--repo-name", "docs",
        "--url", "https://github.com/t-benze/docs.git",
    ])
    assert args.command == "manage-repo"
    assert args.action == "add"
    assert args.agent == "dev_agent"
    assert args.repo_name == "docs"
    assert args.url == "https://github.com/t-benze/docs.git"


def test_manage_repo_parser_remove():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "remove",
        "--agent", "dev_agent",
        "--repo-name", "docs",
    ])
    assert args.action == "remove"
    assert args.url is None


def test_manage_repo_parser_from_file():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "--from-file", "/tmp/repo.json",
    ])
    assert args.from_file == "/tmp/repo.json"


def test_cmd_manage_repo_posts_to_daemon():
    from src.cli import cmd_manage_repo

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    args = MagicMock(
        from_file=None,
        action="add", agent="dev_agent",
        repo_name="docs", url="https://github.com/t-benze/docs.git",
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "add"
    assert kwargs["json"]["repo_name"] == "docs"
    assert kwargs["json"]["url"] == "https://github.com/t-benze/docs.git"


def test_cmd_manage_repo_from_file(tmp_path):
    import json

    from src.cli import cmd_manage_repo

    payload = {
        "action": "remove",
        "agent": "dev_agent",
        "repo_name": "docs",
    }
    f = tmp_path / "repo.json"
    f.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    args = MagicMock(
        from_file=str(f),
        action=None, agent=None, repo_name=None, url=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "remove"
    assert kwargs["json"]["repo_name"] == "docs"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v -k manage_repo`
Expected: FAIL — `cmd_manage_repo` not defined, parser doesn't have subcommand

- [ ] **Step 3: Implement `cmd_manage_repo` and parser**

Add the handler to `src/cli.py` (after `cmd_learning`):

```python
def _manage_repo_payload_from_file(path: str) -> tuple[str, dict]:
    """Load a manage-repo payload from a JSON file.

    Same pattern as report-completion: single-line `opc` invocation avoids
    Claude Code's permission matcher splitting on newlines.

    Returns ``(agent, body)`` shaped for the daemon's manage-repo endpoint.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    required = ["action", "agent", "repo_name"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"manage-repo file missing keys: {missing}")
    body = {"action": data["action"], "repo_name": data["repo_name"]}
    if data.get("url"):
        body["url"] = data["url"]
    return data["agent"], body


def cmd_manage_repo(args: argparse.Namespace) -> None:
    """Agent callback: add, remove, or update a repo in agent.yaml."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    import json as _json
    if args.from_file:
        try:
            agent, body = _manage_repo_payload_from_file(args.from_file)
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading manage-repo file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        agent = args.agent
        body = {"action": args.action, "repo_name": args.repo_name}
        if args.url:
            body["url"] = args.url

    r = client.post(f"/api/v1/agents/{agent}/repos", json=body)
    if not _ok(r):
        return
    print(f"ok: {args.action or body['action']} {body['repo_name']}")
```

Add the parser entry inside `build_parser()` (after the `init-agent` block):

```python
    # opc manage-repo
    p_repo = sub.add_parser("manage-repo", help="Add, remove, or update a repo in an agent's config")
    p_repo.add_argument("action", nargs="?", default=None, choices=["add", "remove", "update"],
                         help="Action to perform")
    p_repo.add_argument("--agent", default=None, help="Agent name")
    p_repo.add_argument("--repo-name", dest="repo_name", default=None, help="Repository name")
    p_repo.add_argument("--url", default=None, help="Repository URL (required for add/update)")
    p_repo.add_argument("--from-file", dest="from_file", default=None,
                         help="Path to JSON file with action/agent/repo_name/url keys")
    p_repo.set_defaults(func=cmd_manage_repo)
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v -k manage_repo`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): opc manage-repo subcommand with --from-file support"
```

---

### Task 4: Skill document

**Files:**
- Create: `protocol/skills/manage-repo/SKILL.md`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the skill**

Create `protocol/skills/manage-repo/SKILL.md`:

```markdown
---
name: manage-repo
description: Add, remove, or update a repository in your agent.yaml configuration. Write a JSON file and call opc manage-repo --from-file to keep the invocation single-line.
---

# manage-repo

Manage the repositories cloned into your `repos/` directory. You can **add** a new repo, **remove** an existing one, or **update** the URL of an existing repo (which deletes the old clone and re-clones from the new URL).

## Usage

1. **Write a JSON file** to `/tmp/manage-repo-<unique>.json` using the Write tool:

   **Add a repo:**
   ```json
   {
     "action": "add",
     "agent": "<your_agent_name>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/web-app.git"
   }
   ```

   **Remove a repo:**
   ```json
   {
     "action": "remove",
     "agent": "<your_agent_name>",
     "repo_name": "web-app"
   }
   ```

   **Update a repo URL:**
   ```json
   {
     "action": "update",
     "agent": "<your_agent_name>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/new-web-app.git"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   opc manage-repo --from-file /tmp/manage-repo-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. Multi-line bash
   commands are rejected by the `Bash(opc:*)` permission rule (newlines count
   as command separators).

## What happens

- **add**: writes the entry to `agent.yaml`, clones the repo into `repos/<name>/`, and regenerates `.claude/settings.json` so the PreToolUse git-pull hook covers the new repo.
- **remove**: deletes the entry from `agent.yaml`, removes the `repos/<name>/` directory, and regenerates `.claude/settings.json`.
- **update**: updates the URL in `agent.yaml`, deletes the old clone, re-clones from the new URL, and regenerates `.claude/settings.json`.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate repo on add) and `404` (repo not found on remove/update) are not retryable — check your `agent.yaml` and adjust.
```

- [ ] **Step 2: Add `"manage-repo"` to the parameterized frontmatter test**

In `tests/test_skills.py`, update the parametrize decorator:

```python
@pytest.mark.parametrize("skill_name", ["start-task", "make-worktree", "manage-repo"])
```

- [ ] **Step 3: Run skill tests**

Run: `uv run pytest tests/test_skills.py -v`
Expected: All tests PASS (including the new `manage-repo` parametrize case, and the `test_skill_cli_commands_exist` cross-reference check passes because `manage-repo` is now in the CLI parser)

- [ ] **Step 4: Commit**

```bash
git add protocol/skills/manage-repo/SKILL.md tests/test_skills.py
git commit -m "feat(skills): manage-repo skill for agent-driven repo management"
```

---

### Task 5: Full test suite + CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md` (worktree copy)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 2: Update CLAUDE.md**

Add `manage-repo` to the skills listing in the Directory Layout section. Update the `protocol/skills/` entry:

```
|   +-- skills/                        # Claude Code skills copied into every agent workspace
|       |-- start-task/                # Parses injected params, runs role, reports via CLI callback
|       |-- make-worktree/             # Creates an isolated git worktree under .claude/worktrees/
|       +-- manage-repo/              # Agent-driven repo add/remove/update via opc manage-repo
```

Add `opc manage-repo` to the CLI examples:

```
opc manage-repo add --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs.git
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for manage-repo skill"
```
